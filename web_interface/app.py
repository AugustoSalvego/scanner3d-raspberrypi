from flask import Flask, render_template, Response, request, jsonify
import cv2
import os
from datetime import datetime
from flask import send_from_directory
import glob
import time

app = Flask(__name__)

camera = cv2.VideoCapture(0)

camera_settings = {
    "brightness": 50,
    "contrast": 50,
    "saturation": 50
}

os.makedirs("captures", exist_ok=True)


def apply_camera_settings():

    camera.set(
        cv2.CAP_PROP_BRIGHTNESS,
        camera_settings["brightness"]
    )

    camera.set(
        cv2.CAP_PROP_CONTRAST,
        camera_settings["contrast"]
    )

    camera.set(
        cv2.CAP_PROP_SATURATION,
        camera_settings["saturation"]
    )


def generate_frames():

    while True:

        success, frame = camera.read()

        if not success:
            continue

        _, buffer = cv2.imencode(
            ".jpg",
            frame
        )

        frame = buffer.tobytes()

        yield (

            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n"

            + frame +

            b"\r\n"

        )


@app.route("/")
def home():

    return render_template(
        "index.html",
        settings=camera_settings
    )


@app.route("/video")
def video():

    return Response(

        generate_frames(),

        mimetype=
        "multipart/x-mixed-replace; boundary=frame"

    )


@app.route(
    "/api/camera-settings",
    methods=["POST"]
)
def update_camera_settings():

    data = request.json

    for key in camera_settings:

        if key in data:

            camera_settings[key] = int(
                data[key]
            )

    apply_camera_settings()

    return jsonify({

        "success": True,

        "settings":
        camera_settings

    })


@app.route(
    "/capture",
    methods=["POST"]
)
def capture():

    success, frame = camera.read()

    if not success:

        return jsonify({

            "success": False

        })

    filename = datetime.now().strftime(

        "capture_%Y%m%d_%H%M%S.jpg"

    )

    filepath = os.path.join(

        "captures",
        filename

    )

    cv2.imwrite(
        filepath,
        frame
    )

    return jsonify({

        "success": True,

        "file": filename

    })

@app.route("/captures/<filename>")
def get_capture(filename):

    return send_from_directory(
        "captures",
        filename
    )

@app.route("/clear-captures", methods=["POST"])
def clear_captures():
    files = glob.glob("captures/*.jpg")

    for file in files:
        os.remove(file)

    return jsonify({
        "success": True,
        "deleted": len(files)
    })

@app.route("/reset-camera", methods=["POST"])
def reset_camera():
    global camera
    global camera_settings

    try:
        camera.release()
        time.sleep(1)
    except Exception:
        pass

    camera = cv2.VideoCapture(0, cv2.CAP_DSHOW)

    camera_settings = {
        "brightness": 50,
        "contrast": 50,
        "saturation": 50
    }

    apply_camera_settings()

    return jsonify({
        "success": True,
        "message": "Camera restarted with default settings"
    })

scanner_status = {
    "running": False
}

system_logs = []


def add_log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    system_logs.insert(0, f"[{timestamp}] {message}")

    if len(system_logs) > 20:
        system_logs.pop()


@app.route("/start-scan", methods=["POST"])
def start_scan():
    scanner_status["running"] = True
    add_log("Scan started")

    return jsonify({
        "success": True,
        "running": True
    })


@app.route("/stop-scan", methods=["POST"])
def stop_scan():
    scanner_status["running"] = False
    add_log("Scan stopped")

    return jsonify({
        "success": True,
        "running": False
    })


@app.route("/generate-ply", methods=["POST"])
def generate_ply():
    add_log("PLY generation simulated")

    return jsonify({
        "success": True,
        "message": "PLY generation simulated"
    })


@app.route("/logs")
def get_logs():
    return jsonify({
        "logs": system_logs,
        "running": scanner_status["running"]
    })

if __name__ == "__main__":

    apply_camera_settings()

    app.run(

        debug=True,

        host="0.0.0.0",

        port=5000

    )