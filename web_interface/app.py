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
import threading
import time

from scanner_core.camera import capture
from scanner_core.camera import encode_frame
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
from scanner_core.session import list_scan_sessions
from web_interface.api_response import api_success
from web_interface.api_response import api_error
from scanner_core.calibration import get_calibration_status
from scanner_core.calibration import create_default_calibration_files
from scanner_core.viewer import get_viewer_status

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


@app.route("/start-scan", methods=["POST"])
def start_scan():
    if scanner_state.get("running"):
        return api_error(
            message="Scan is already running",
            status_code=409
        )

    thread = threading.Thread(
        target=execute_scan,
        daemon=True
    )

    thread.start()

    return api_success(
        message="Scan started successfully"
    )


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

    return jsonify(status_data)


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


@app.route("/simulation-mode", methods=["POST"])
def simulation_mode():
    data = request.get_json(silent=True) or {}

    if "enabled" in data:
        settings = set_simulation_mode(data["enabled"])
    else:
        settings = toggle_simulation_mode()

    scanner_state["simulation_mode"] = settings["simulation_mode"]

    status_text = "ON" if settings["simulation_mode"] else "OFF"

    add_log(f"Simulation mode changed to {status_text}")

    return jsonify({
        "success": True,
        "message": f"Simulation Mode: {status_text}.",
        "simulation_mode": settings["simulation_mode"]
    })

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
            "version": scanner_state.get("scanner_version", "0.1"),
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
                "/scan-sessions"
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
        debug=True,
        host="0.0.0.0",
        port=5000
    )