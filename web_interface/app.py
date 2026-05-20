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


if __name__ == "__main__":
    app.run(
        debug=True,
        host="0.0.0.0",
        port=5000
    )