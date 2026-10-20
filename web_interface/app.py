from datetime import datetime
from flask import Flask
from flask import Response
from flask import jsonify
from flask import render_template
from flask import request
from flask import send_from_directory
from werkzeug.utils import secure_filename

import glob
import os
from pathlib import Path
import threading
import time

from scanner_core.camera import capture
from scanner_core.camera import encode_frame
from scanner_core.camera import get_frame
from scanner_core.camera import release
from scanner_core.config import OUTPUT_FOLDER
from scanner_core.config import POINT_CLOUD_FILE
from scanner_core.config import POINT_CLOUD_FOLDER
from scanner_core.logger import add_log
from scanner_core.logger import get_logs
from scanner_core.pipeline import execute_scan
from scanner_core.pipeline import stop
from scanner_core.point_cloud import generate
from scanner_core.runtime_settings import get_settings
from scanner_core.runtime_settings import set_simulation_mode
from scanner_core.runtime_settings import toggle_simulation_mode
from scanner_core.runtime_settings import update_settings
from scanner_core.state import scanner_state
from scanner_core.status import get_status
from scanner_core.session import (
    list_scan_sessions, set_reconstruction_status, rename_scan_session,
    delete_scan_session, get_session_ply,
)
from web_interface.api_response import api_success
from web_interface.api_response import api_error
from scanner_core.calibration import get_calibration_status
from scanner_core.calibration import create_default_calibration_files
from scanner_core.calibration_workflow import (
    calibration_status as detailed_calibration_status,
    start_capture as start_calibration_capture,
    start_compute as start_calibration_compute,
    stop_calibration as request_calibration_stop,
    reset_photos as reset_calibration_photos,
)
from scanner_core.hardware_gate import hardware_gate
from scanner_core.calibration_workflow import RUN_DIR as CALIB_RUN_DIR
from flask import send_file
from scanner_core.calibration_geometry import CalibrationFailure, find_board
from scanner_core.real_reconstruction import reconstruct_latest, ReconstructionError
import cv2
from scanner_core.viewer import get_viewer_status
from scanner_core.laser_detection import analyze_laser_frame
from scanner_core.laser_detection import encode_laser_mask
from scanner_core.laser_detection import encode_laser_overlay
from scanner_core.laser_detection import get_laser_background_status
from scanner_core.laser_detection import save_laser_background

app = Flask(__name__)

os.makedirs(
    OUTPUT_FOLDER,
    exist_ok=True
)

os.makedirs(
    POINT_CLOUD_FOLDER,
    exist_ok=True
)


def generate_frames():
    while True:
        frame = encode_frame()

        if frame is None:
            time.sleep(0.05)
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + frame
            + b"\r\n"
        )


def get_safe_ply_path(filename):
    safe_filename = secure_filename(filename)

    if safe_filename != filename:
        return None

    if not safe_filename.endswith(".ply"):
        return None

    folder = os.path.abspath(POINT_CLOUD_FOLDER)

    filepath = os.path.abspath(
        os.path.join(
            folder,
            safe_filename
        )
    )

    if not filepath.startswith(folder):
        return None

    return filepath


def generate_laser_mask_frames():
    while True:
        frame = get_frame()

        if frame is None:
            time.sleep(0.05)
            continue

        encoded_frame = encode_laser_mask(frame)

        if encoded_frame is None:
            time.sleep(0.05)
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + encoded_frame
            + b"\r\n"
        )


def generate_laser_overlay_frames():
    while True:
        frame = get_frame()

        if frame is None:
            time.sleep(0.05)
            continue

        encoded_frame = encode_laser_overlay(frame)

        if encoded_frame is None:
            time.sleep(0.05)
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"
            + encoded_frame
            + b"\r\n"
        )


@app.route("/laser-mask-video")
def laser_mask_video():
    return Response(
        generate_laser_mask_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.route("/laser-overlay-video")
def laser_overlay_video():
    return Response(
        generate_laser_overlay_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.route("/calibrate-laser-background", methods=["POST"])
def calibrate_laser_background():
    frame = get_frame()

    if frame is None:
        return api_error(
            message="No camera frame available",
            status_code=500
        )

    data = save_laser_background(frame)

    add_log("Laser background calibration saved")

    return api_success(
        message="Laser background calibrated successfully",
        data=data
    )


@app.route("/laser-background-status")
def laser_background_status():
    return api_success(
        message="Laser background status loaded",
        data=get_laser_background_status()
    )


@app.route("/laser-diagnostic")
def laser_diagnostic():
    frame = get_frame()

    if frame is None:
        return api_error(
            message="No camera frame available",
            status_code=500
        )

    return api_success(
        message="Laser diagnostic generated",
        data=analyze_laser_frame(frame)
    )


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/video")
def video():
    return Response(
        generate_frames(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.route("/capture", methods=["POST"])
def capture_image():
    filename = capture()

    if filename is None:
        add_log("Capture failed")

        return api_error(
            message="Image capture failed",
            status_code=500
        )

    add_log(f"Image captured: {filename}")

    return api_success(
        message="Image captured successfully",
        data={
            "file": filename
        },
        file=filename
    )


@app.route("/captures/<filename>")
def get_capture(filename):
    return send_from_directory(
        OUTPUT_FOLDER,
        filename
    )


@app.route("/clear-captures", methods=["POST"])
def clear_captures():
    files = glob.glob(
        os.path.join(OUTPUT_FOLDER, "*.jpg")
    )

    for file in files:
        os.remove(file)

    scanner_state["last_capture"] = None

    add_log(f"Deleted captures: {len(files)}")

    return api_success(
        message="Captures cleared successfully",
        data={
            "deleted": len(files)
        },
        deleted=len(files)
    )


@app.route("/reset-camera", methods=["POST"])
def reset_camera():
    release()

    add_log("Camera reset requested")

    return api_success(
        message="Camera reset requested"
    )


# Prevent two HTTP requests from starting two capture threads simultaneously.
_scan_start_lock = threading.Lock()


def _run_scan_exclusive():
    """Keep the hardware gate until capture AND reconstruction have finished."""
    try:
        outcome = execute_scan()
        if (outcome and outcome["status"] == "finished" and
                not outcome["simulation"] and outcome["session"]):
            session_id = outcome["session"]
            with _reconstruction_state_lock:
                _reconstruction_state.update({
                    "running": True, "done": 0, "total": 0,
                    "error": None, "result": None,
                    "message": "Photographs captured; reconstructing automatically...",
                })
            _reconstruction_worker(session_id=session_id, release_gate=False)
    finally:
        hardware_gate.release()
        _scan_start_lock.release()


@app.route("/start-scan", methods=["POST"])
def start_scan():
    if not _scan_start_lock.acquire(blocking=False):
        return api_error(message="Scan is already running", status_code=409)
    if not hardware_gate.acquire(blocking=False):
        _scan_start_lock.release()
        return api_error(message="Hardware busy: calibration or another operation is active.", status_code=409)

    try:
        thread = threading.Thread(target=_run_scan_exclusive, daemon=True)
        thread.start()
    except Exception:
        hardware_gate.release()
        _scan_start_lock.release()
        raise

    return api_success(message="Scan requested; acquisition will start shortly.")


@app.route("/stop-scan", methods=["POST"])
def stop_scan():
    stop()

    return api_success(
        message="Stop scan requested"
    )


@app.route("/generate-ply", methods=["POST"])
def generate_ply():
    try:
        filepath = generate()

        scanner_state["last_scan_time"] = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        return api_success(
            message="PLY generated successfully",
            data={
                "file": filepath
            },
            file=filepath
        )

    except Exception as error:
        scanner_state["last_error"] = str(error)

        add_log(f"PLY generation failed: {error}")

        return api_error(
            message="PLY generation failed",
            status_code=500,
            data={
                "error": str(error)
            }
        )


@app.route("/logs")
def logs():
    logs_data = get_logs()

    return api_success(
        message="Logs loaded successfully",
        data={
            "logs": logs_data
        },
        logs=logs_data
    )


@app.route("/status")
def status():
    status_data = get_status()

    return api_success(
        message="Scanner status loaded successfully",
        data={
            "status": status_data
        },
        scanner_status=status_data
    )


@app.route("/settings", methods=["GET"])
def settings_get():
    return api_success(
        message="Settings loaded successfully",
        data={
            "settings": get_settings()
        },
        settings=get_settings()
    )


@app.route("/settings", methods=["POST"])
def settings_post():
    data = request.get_json(silent=True) or {}

    settings, errors = update_settings(data)

    if errors:
        return api_error(
            message="Settings update failed",
            status_code=400,
            data={
                "errors": errors,
                "settings": settings
            },
            errors=errors,
            settings=settings
        )

    add_log("Settings updated")

    return api_success(
        message="Settings updated successfully",
        data={
            "settings": settings
        },
        settings=settings
    )


@app.route("/simulation-mode", methods=["GET"])
def simulation_mode_get():
    settings = get_settings()

    return api_success(
        message="Simulation mode loaded successfully",
        data={
            "simulation_mode": settings["simulation_mode"]
        },
        simulation_mode=settings["simulation_mode"]
    )


@app.route("/simulation-mode", methods=["POST"])
def simulation_mode_post():
    data = request.get_json(silent=True) or {}

    if "enabled" in data:
        settings = set_simulation_mode(data["enabled"])
    else:
        settings = toggle_simulation_mode()

    scanner_state["simulation_mode"] = settings["simulation_mode"]

    status_text = "ON" if settings["simulation_mode"] else "OFF"

    add_log(f"Simulation mode changed to {status_text}")

    return api_success(
        message=f"Simulation Mode: {status_text}.",
        data={
            "simulation_mode": settings["simulation_mode"]
        },
        simulation_mode=settings["simulation_mode"]
    )

@app.route("/point-clouds")
def list_point_clouds():
    folder = os.path.abspath(POINT_CLOUD_FOLDER)

    os.makedirs(
        folder,
        exist_ok=True
    )

    files = []

    for filename in os.listdir(folder):
        if filename.endswith(".ply"):
            filepath = os.path.join(
                folder,
                filename
            )

            size_kb = os.path.getsize(filepath) / 1024

            files.append({
                "name": filename,
                "size_kb": round(size_kb, 2),
                "modified_at": datetime.fromtimestamp(
                    os.path.getmtime(filepath)
                ).strftime("%Y-%m-%d %H:%M:%S")
            })

    files.sort(
        key=lambda item: item["modified_at"],
        reverse=True
    )

    return api_success(
        message="Point cloud files loaded successfully",
        data={
            "files": files
        },
        files=files
    )


@app.route("/download-ply")
def download_ply():
    last_point_cloud = scanner_state.get("last_point_cloud")

    if last_point_cloud is not None:
        filepath = os.path.abspath(last_point_cloud)
        folder = os.path.dirname(filepath)
        filename = os.path.basename(filepath)

        if os.path.exists(filepath):
            return send_from_directory(
                folder,
                filename,
                as_attachment=True
            )

    folder = os.path.abspath(POINT_CLOUD_FOLDER)

    filepath = os.path.join(
        folder,
        POINT_CLOUD_FILE
    )

    if not os.path.exists(filepath):
        add_log("Download failed: PLY file not found")

        return api_error(
            message="PLY file not found. Generate PLY first.",
            status_code=404,
            data={
                "expected_path": filepath
            },
            expected_path=filepath
        )

    return send_from_directory(
        folder,
        POINT_CLOUD_FILE,
        as_attachment=True
    )

@app.route("/download-ply/<filename>")
def download_ply_file(filename):
    filepath = get_safe_ply_path(filename)

    if filepath is None:
        return api_error(
            message="Invalid PLY filename",
            status_code=400,
            data={
                "file": filename
            }
        )

    if not os.path.exists(filepath):
        return api_error(
            message="PLY file not found",
            status_code=404,
            data={
                "file": filename
            }
        )

    folder = os.path.dirname(filepath)
    safe_filename = os.path.basename(filepath)

    return send_from_directory(
        folder,
        safe_filename,
        as_attachment=True
    )

@app.route("/delete-ply/<filename>", methods=["POST"])
def delete_ply_file(filename):
    filepath = get_safe_ply_path(filename)

    if filepath is None:
        return api_error(
            message="Invalid PLY filename",
            status_code=400,
            data={
                "file": filename
            }
        )

    if not os.path.exists(filepath):
        return api_error(
            message="PLY file not found",
            status_code=404,
            data={
                "file": filename
            }
        )

    safe_filename = os.path.basename(filepath)

    os.remove(filepath)

    add_log(f"Deleted PLY: {safe_filename}")

    return api_success(
        message="PLY deleted successfully",
        data={
            "file": safe_filename
        }
    )

@app.route("/health")
def health():
    return api_success(
        message="Scanner service is online",
        data={
            "status": "ok",
            "scanner": "online"
        },
        status="ok",
        scanner="online"
    )


@app.route("/api-info")
def api_info():
    settings = get_settings()

    return api_success(
        message="API information loaded successfully",
        data={
            "name": "Scanner 3D API",
            "version": scanner_state.get("version", "0.1"),
            "simulation_mode": settings["simulation_mode"],
            "routes": [
                "/",
                "/video",
                "/capture",
                "/clear-captures",
                "/reset-camera",
                "/start-scan",
                "/stop-scan",
                "/generate-ply",
                "/download-ply",
                "/point-clouds",
                "/download-ply/<filename>",
                "/delete-ply/<filename>",
                "/settings",
                "/simulation-mode",
                "/logs",
                "/status",
                "/health",
                "/api-info",
                "/scan-sessions",
                "/calibration/status",
                "/viewer/status"
            ]
        }
    )

@app.route("/scan-sessions")
def scan_sessions():
    sessions = list_scan_sessions()

    return api_success(
        message="Scan sessions loaded successfully",
        data={
            "sessions": sessions
        },
        sessions=sessions
    )

@app.route("/api/sessions", methods=["GET"])
def session_collection():
    items = []
    for meta in list_scan_sessions():
        entry = dict(meta)
        ident = entry.get("session_id")
        entry["ply_available"] = False
        if ident:
            try:
                get_session_ply(ident)
                entry["ply_available"] = True
            except (ValueError, OSError):
                pass
        items.append(entry)
    return api_success("Sessões disponíveis.", data={"sessions": items})


@app.route("/api/sessions/<session_id>/ply", methods=["GET"])
def session_ply(session_id):
    try:
        file = get_session_ply(session_id)
    except ValueError as error:
        return api_error(str(error), status_code=400)
    except (OSError, FileNotFoundError):
        return api_error("Arquivo PLY indisponível.", status_code=404)
    return send_from_directory(
        str(file.parent), file.name,
        as_attachment=request.args.get("download") == "1",
        mimetype="application/octet-stream"
    )


@app.route("/api/sessions/<session_id>/rename", methods=["POST"])
def rename_session(session_id):
    if hardware_gate.locked():
        return api_error("Aguarde a conclusão da operação atual.", status_code=409)
    payload = request.get_json(silent=True) or {}
    try:
        metadata = rename_scan_session(session_id, payload.get("name"))
        return api_success("Escaneamento renomeado.", data={"session": metadata})
    except ValueError as error:
        return api_error(str(error), status_code=400)
    except FileNotFoundError:
        return api_error("Escaneamento não encontrado.", status_code=404)
    except (OSError, TypeError) as error:
        return api_error(str(error), status_code=500)


@app.route("/api/sessions/<session_id>", methods=["DELETE"])
def delete_session(session_id):
    if hardware_gate.locked():
        return api_error("Aguarde a conclusão da operação atual.", status_code=409)
    try:
        delete_scan_session(session_id)
        return api_success("Escaneamento excluído.")
    except ValueError as error:
        return api_error(str(error), status_code=400)
    except FileNotFoundError:
        return api_error("Escaneamento não encontrado.", status_code=404)
    except OSError as error:
        return api_error(str(error), status_code=500)


@app.route("/calibration/status")
def calibration_status():
    status = get_calibration_status()

    return api_success(
        message="Calibration status loaded successfully",
        data={
            "calibration": status
        },
        calibration=status
    )

@app.route("/api/calibration/workflow/check-board", methods=["POST"])
def calibration_check_board():
    """No motor movement: verify all 54 corners are visible before a full sweep."""
    if not hardware_gate.acquire(blocking=False):
        return api_error("Scanner busy: wait for the motor to stop.", status_code=409)
    try:
        frame = get_frame()
        if frame is None:
            return api_error("Camera unavailable. Check USB connection.", status_code=503)
        corners = find_board(frame)
        found = corners is not None and len(corners) == 54
        if found:
            marked = frame.copy()
            cv2.drawChessboardCorners(marked, (9, 6), corners, True)
            diag = CALIB_RUN_DIR / "diagnostics"
            diag.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(diag / "preflight.jpg"), marked)
        return api_success(
            "54 chessboard corners detected." if found else
            "Board not recognized. Improve white light and show the entire 9x6 pattern.",
            data={"board_detected": found, "corners_found": 54 if found else 0,
                  "image_size": [frame.shape[1], frame.shape[0]],
                  "preview_url": "/api/calibration/workflow/preflight-preview" if found else None},
        )
    except Exception as error:
        return api_error(f"Checkerboard test failed: {error}", status_code=422)
    finally:
        hardware_gate.release()


@app.route("/api/calibration/workflow/preflight-preview")
def calibration_check_board_preview():
    path = CALIB_RUN_DIR / "diagnostics" / "preflight.jpg"
    if not path.is_file():
        return api_error("No checkerboard test image saved.", status_code=404)
    return send_file(path, mimetype="image/jpeg", conditional=False)


@app.route("/api/calibration/workflow", methods=["GET"])
def calibration_workflow_status():
    try:
        return api_success("Current calibration progress.", data=detailed_calibration_status())
    except CalibrationFailure as error:
        return api_error(str(error), status_code=422)


@app.route("/api/calibration/workflow/capture", methods=["POST"])
def calibration_workflow_capture():
    payload = request.get_json(silent=True) or {}
    try:
        start_calibration_capture(
            str(payload.get("kind", "")),
            int(payload.get("placement", 0)),
            payload.get("square_size_mm", 10.0),
            payload.get("right_direction", 1),
        )
        return api_success("Calibration photo capture started.")
    except (CalibrationFailure, TypeError, ValueError) as error:
        return api_error(str(error), status_code=409)


@app.route("/api/calibration/workflow/compute", methods=["POST"])
def calibration_workflow_compute():
    payload = request.get_json(silent=True) or {}
    try:
        start_calibration_compute(str(payload.get("kind", "")))
        return api_success("Calibration computation started.")
    except CalibrationFailure as error:
        return api_error(str(error), status_code=409)


@app.route("/api/calibration/workflow/stop", methods=["POST"])
def calibration_workflow_stop():
    if not request_calibration_stop():
        return api_error("No calibration operation is running.", status_code=409)
    return api_success("Calibration stop requested.")


@app.route("/api/calibration/workflow/diagnostics/<stage>")
def calibration_checkerboard_diagnostic(stage):
    if stage not in {f"{kind}{i}" for kind in ("camera", "laser") for i in (1, 2, 3)}:
        return api_error("Unknown calibration stage.", status_code=404)
    path = CALIB_RUN_DIR / "diagnostics" / f"{stage}.jpg"
    if not path.is_file():
        return api_error("No recognized checkerboard image for this stage yet.", status_code=404)
    return send_file(path, mimetype="image/jpeg", conditional=False)


@app.route("/api/calibration/workflow/reset", methods=["POST"])
def calibration_workflow_reset():
    try:
        reset_calibration_photos()
        return api_success("Calibration photo dataset reset; saved profiles kept.")
    except CalibrationFailure as error:
        return api_error(str(error), status_code=409)


# Physical laser reconstruction is deliberately separate from demo PLY generation.
_reconstruction_state_lock = threading.Lock()
_reconstruction_state = {
    "running": False, "done": 0, "total": 0,
    "message": "No real reconstruction requested yet.", "error": None, "result": None,
}


def _reconstruction_progress(done, total):
    with _reconstruction_state_lock:
        _reconstruction_state.update({
            "done": done, "total": total,
            "message": f"Triangulating laser photos: {done}/{total}",
        })


def _reconstruction_worker(session_id=None, release_gate=True):
    """Use the same real algorithm for automatic and manual reconstruction."""
    try:
        if session_id:
            set_reconstruction_status(session_id, "running")
        result = reconstruct_latest(
            progress_callback=_reconstruction_progress,
            session_id=session_id,
        )
        set_reconstruction_status(
            result["session"], "finished", filename=result["file"]
        )
        with _reconstruction_state_lock:
            _reconstruction_state["result"] = result
            _reconstruction_state["message"] = (
                f"PLY generated: {result['points']} points from "
                f"{result['usable_frames']} physical images."
            )
        scanner_state["point_cloud_generated"] = True
        scanner_state["last_point_cloud"] = os.path.join(
            POINT_CLOUD_FOLDER, result["file"]
        )
        add_log(f"PLY generated: {result['file']}")
    except Exception as error:
        scanner_state["last_error"] = str(error)
        if session_id:
            try:
                set_reconstruction_status(session_id, "error", error=error)
            except Exception as state_error:
                add_log(f"Could not save reconstruction error status: {state_error}")
        with _reconstruction_state_lock:
            _reconstruction_state["error"] = str(error)
            _reconstruction_state["message"] = f"Reconstruction failed: {error}"
        add_log(f"Reconstruction failed: {error}")
    finally:
        with _reconstruction_state_lock:
            _reconstruction_state["running"] = False
        if release_gate:
            hardware_gate.release()


@app.route("/api/reconstruction/start", methods=["POST"])
def reconstruct_real_scan():
    if not hardware_gate.acquire(blocking=False):
        return api_error("Wait for scan/calibration to finish.", status_code=409)
    try:
        # Reject invalid/missing inputs before launching the expensive job.
        from scanner_core.real_reconstruction import load_calibration, find_recent_real_session
        load_calibration()
        selected_folder, _ = find_recent_real_session()
        with _reconstruction_state_lock:
            _reconstruction_state.update({
                "running": True, "done": 0, "total": 0, "error": None,
                "result": None, "message": "Loading real physical captures...",
            })
        thread = threading.Thread(
            target=_reconstruction_worker,
            kwargs={"session_id": selected_folder.name}, daemon=True
        )
        thread.start()
        return api_success("Real reconstruction started.")
    except ReconstructionError as error:
        hardware_gate.release()
        return api_error(str(error), status_code=422)
    except Exception:
        hardware_gate.release()
        raise


@app.route("/api/reconstruction/status")
def reconstruction_status():
    with _reconstruction_state_lock:
        return api_success("Real reconstruction progress.", data=_reconstruction_state.copy())


@app.route("/viewer/status")
def viewer_status():
    status = get_viewer_status()

    return api_success(
        message="Viewer status loaded successfully",
        data={
            "viewer": status
        },
        viewer=status
    )

if __name__ == "__main__":
    create_default_calibration_files()

    app.run(
        debug=False,
        use_reloader=False,
        host="0.0.0.0",
        port=5000
    )