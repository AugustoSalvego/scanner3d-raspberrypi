import cv2
import os
from datetime import datetime

from scanner_core.config import CAMERA_INDEX
from scanner_core.config import OUTPUT_FOLDER
from scanner_core.state import scanner_state
from scanner_core.logger import add_log
from scanner_core.session import get_current_session
from scanner_core.session import add_capture_to_session

camera = None


def initialize():
    global camera

    if camera is None:
        camera = cv2.VideoCapture(CAMERA_INDEX)

        if not camera.isOpened():
            add_log("Camera failed to open")
            camera = None


def get_frame():
    initialize()

    if camera is None:
        return None

    success, frame = camera.read()

    if success:
        return frame

    return None


def capture():
    frame = get_frame()

    if frame is None:
        add_log("Capture failed: no camera frame")
        return None

    session = get_current_session()

    filename = datetime.now().strftime(
        "capture_%Y%m%d_%H%M%S.jpg"
    )

    if session is not None:
        output_folder = session["captures_path"]
    else:
        output_folder = OUTPUT_FOLDER

    os.makedirs(
        output_folder,
        exist_ok=True
    )

    filepath = os.path.join(
        output_folder,
        filename
    )

    success = cv2.imwrite(
        filepath,
        frame
    )

    if not success:
        add_log("Capture failed: image not saved")
        return None

    scanner_state["frames_captured"] += 1
    scanner_state["last_capture"] = filename

    add_capture_to_session(filename)

    return filename


def encode_frame():
    frame = get_frame()

    if frame is None:
        return None

    success, buffer = cv2.imencode(
        ".jpg",
        frame
    )

    if not success:
        return None

    return buffer.tobytes()


def release():
    global camera

    if camera is not None:
        camera.release()
        camera = None