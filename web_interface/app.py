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
from scanner_core.config import APP_VERSION
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

        return jsonify({
            "success": False,
            "message": "Image capture failed."
        }), 500

    scanner_state["frames_captured"] += 1
    scanner_state["last_capture"] = filename

    add_log(f"Image captured: {filename}")

    return jsonify({
        "success": True,
        "file": filename,
        "message": "Image captured successfully."
    })


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

    return jsonify({
        "success": True,
        "deleted": len(files),
        "message": "Captures cleared."
    })


@app.route("/reset-camera", methods=["POST"])
def reset_camera():
    release()

    add_log("Camera reset requested")

    return jsonify({
        "success": True,
        "message": "Camera reset requested."
    })


@app.route("/start-scan", methods=["POST"])
def start_scan():
    if scanner_state["running"]:
        return jsonify({
            "success": False,
            "message": "Scan is already running."
        }), 409

    thread = threading.Thread(
        target=execute_scan,
        daemon=True
    )

    thread.start()

    return jsonify({
        "success": True,
        "message": "Scan started."
    })


@app.route("/stop-scan", methods=["POST"])
def stop_scan():
    stop()

    return jsonify({
        "success": True,
        "message": "Stop requested."
    })


@app.route("/generate-ply", methods=["POST"])
def generate_ply():
    try:
        generate()

        scanner_state["last_scan"] = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        return jsonify({
            "success": True,
            "message": "PLY generated successfully."
        })

    except Exception as error:
        scanner_state["last_error"] = str(error)

        add_log(f"PLY generation failed: {error}")

        return jsonify({
            "success": False,
            "message": "PLY generation failed.",
            "error": str(error)
        }), 500


@app.route("/logs")
def logs():
    return jsonify({
        "logs": get_logs()
    })


@app.route("/status")
def status():
    return jsonify(
        get_status()
    )


@app.route("/settings", methods=["GET"])
def settings_get():
    return jsonify(
        get_settings()
    )


@app.route("/settings", methods=["POST"])
def settings_post():
    data = request.get_json(silent=True) or {}

    settings, errors = update_settings(data)

    if errors:
        return jsonify({
            "success": False,
            "message": "Settings update failed.",
            "errors": errors,
            "settings": settings
        }), 400

    add_log("Settings updated")

    return jsonify({
        "success": True,
        "message": "Settings updated.",
        "settings": settings
    })


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


@app.route("/download-ply")
def download_ply():
    folder = os.path.abspath(POINT_CLOUD_FOLDER)

    filepath = os.path.join(
        folder,
        POINT_CLOUD_FILE
    )

    if not os.path.exists(filepath):
        add_log("Download failed: PLY file not found")

        return jsonify({
            "success": False,
            "error": "PLY file not found. Generate PLY first.",
            "expected_path": filepath
        }), 404

    return send_from_directory(
        folder,
        POINT_CLOUD_FILE,
        as_attachment=True
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

    return jsonify({
        "files": files
    })


@app.route("/download-ply/<filename>")
def download_ply_file(filename):
    filepath = get_safe_ply_path(filename)

    if filepath is None or not os.path.exists(filepath):
        return jsonify({
            "success": False,
            "error": "PLY file not found"
        }), 404

    folder = os.path.abspath(POINT_CLOUD_FOLDER)

    return send_from_directory(
        folder,
        filename,
        as_attachment=True
    )


@app.route("/delete-ply/<filename>", methods=["POST"])
def delete_ply_file(filename):
    filepath = get_safe_ply_path(filename)

    if filepath is None or not os.path.exists(filepath):
        return jsonify({
            "success": False,
            "message": "PLY file not found."
        }), 404

    os.remove(filepath)

    if scanner_state["last_point_cloud"] == filepath:
        scanner_state["last_point_cloud"] = None
        scanner_state["point_cloud_generated"] = False

    add_log(f"PLY deleted: {filename}")

    return jsonify({
        "success": True,
        "message": "PLY deleted successfully.",
        "deleted": filename
    })


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "scanner": "online"
    })


@app.route("/api-info")
def api_info():
    return jsonify({
        "name": "Scanner 3D API",
        "version": APP_VERSION,
        "simulation_mode": get_settings()["simulation_mode"],
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
            "/api-info"
        ]
    })


if __name__ == "__main__":
    app.run(
        debug=True,
        host="0.0.0.0",
        port=5000
    )