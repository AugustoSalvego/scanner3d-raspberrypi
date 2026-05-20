from flask import Flask
from flask import Response
from flask import jsonify
from flask import render_template
from flask import send_from_directory

import glob
import os
import threading
import time

from scanner_core.camera import capture
from scanner_core.camera import encode_frame
from scanner_core.camera import release
from scanner_core.config import OUTPUT_FOLDER
from scanner_core.logger import add_log
from scanner_core.logger import get_logs
from scanner_core.pipeline import execute_scan
from scanner_core.pipeline import stop
from scanner_core.point_cloud import generate
from scanner_core.status import get_status
from scanner_core.config import POINT_CLOUD_FOLDER
from scanner_core.config import POINT_CLOUD_FILE

app = Flask(__name__)

os.makedirs(
    OUTPUT_FOLDER,
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
        return jsonify({
            "success": False
        })

    add_log(f"Image captured: {filename}")

    return jsonify({
        "success": True,
        "file": filename
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

    add_log(f"Deleted captures: {len(files)}")

    return jsonify({
        "success": True,
        "deleted": len(files)
    })


@app.route("/reset-camera", methods=["POST"])
def reset_camera():
    release()

    add_log("Camera reset")

    return jsonify({
        "success": True
    })


@app.route("/start-scan", methods=["POST"])
def start_scan():
    thread = threading.Thread(
        target=execute_scan,
        daemon=True
    )

    thread.start()

    return jsonify({
        "success": True
    })


@app.route("/stop-scan", methods=["POST"])
def stop_scan():
    stop()

    return jsonify({
        "success": True
    })


@app.route("/generate-ply", methods=["POST"])
def generate_ply():
    generate()

    return jsonify({
        "success": True
    })


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
            files.append(filename)

    files.sort(reverse=True)

    return jsonify({
        "files": files
    })

@app.route("/download-ply/<filename>")
def download_ply_file(filename):
    folder = os.path.abspath(POINT_CLOUD_FOLDER)

    filepath = os.path.join(
        folder,
        filename
    )

    if not os.path.exists(filepath):
        return jsonify({
            "success": False,
            "error": "PLY file not found"
        }), 404

    return send_from_directory(
        folder,
        filename,
        as_attachment=True
    )

if __name__ == "__main__":
    app.run(
        debug=True,
        host="0.0.0.0",
        port=5000
    )