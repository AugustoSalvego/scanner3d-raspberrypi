import cv2
import os
from datetime import datetime

from scanner_core.config import CAMERA_INDEX
from scanner_core.config import OUTPUT_FOLDER
from scanner_core.state import scanner_state
from scanner_core.logger import add_log


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

    os.makedirs(
        OUTPUT_FOLDER,
        exist_ok=True
    )

    filename = datetime.now().strftime(
        "capture_%Y%m%d_%H%M%S.jpg"
    )

    filepath = os.path.join(
        OUTPUT_FOLDER,
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