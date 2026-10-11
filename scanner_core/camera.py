import cv2
import os
from datetime import datetime
import threading

from scanner_core.config import CAMERA_INDEX
from scanner_core.config import OUTPUT_FOLDER
from scanner_core.state import scanner_state
from scanner_core.logger import add_log
from scanner_core.session import get_current_session
from scanner_core.session import add_capture_to_session


camera = None
_camera_lock = threading.RLock()


def initialize():
    global camera
    with _camera_lock:
        if camera is None:
            candidate = cv2.VideoCapture(CAMERA_INDEX)
            if not candidate.isOpened():
                add_log("Camera failed to open")
                candidate.release()
                return
            # Stable MJPEG resolution across calibration and scanning.
            candidate.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            candidate.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            candidate.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            candidate.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            camera = candidate
            add_log("USB camera opened (requested 1280x720 MJPEG).")


def get_frame():
    with _camera_lock:
        initialize()
        if camera is None:
            return None
        success, frame = camera.read()
        return frame if success else None


def generate_capture_filename(step_number=None):
    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S_%f"
    )[:-3]

    if step_number is not None:
        return f"capture_step_{step_number:03d}_{timestamp}.jpg"

    return f"capture_manual_{timestamp}.jpg"


def capture(step_number=None):
    frame = get_frame()

    if frame is None:
        add_log("Capture failed: no camera frame")
        return None

    session = get_current_session()

    filename = generate_capture_filename(
        step_number=step_number
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
    with _camera_lock:
        if camera is not None:
            camera.release()
            camera = None