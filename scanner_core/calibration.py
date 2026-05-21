import json
import os

from scanner_core.logger import add_log


CALIBRATION_FOLDER = "outputs/calibration"

CAMERA_CALIBRATION_FILE = "camera_calibration.json"
LASER_CALIBRATION_FILE = "laser_calibration.json"
TURNTABLE_CALIBRATION_FILE = "turntable_calibration.json"
SCALE_CALIBRATION_FILE = "scale_calibration.json"


def ensure_calibration_folder():
    os.makedirs(
        CALIBRATION_FOLDER,
        exist_ok=True
    )


def get_calibration_file_path(filename):
    ensure_calibration_folder()

    return os.path.join(
        CALIBRATION_FOLDER,
        filename
    )


def save_calibration_data(filename, data):
    filepath = get_calibration_file_path(filename)

    with open(filepath, "w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            indent=4
        )

    add_log(
        f"Calibration data saved: {filename}"
    )

    return filepath


def load_calibration_data(filename):
    filepath = get_calibration_file_path(filename)

    if not os.path.exists(filepath):
        return None

    with open(filepath, "r", encoding="utf-8") as file:
        return json.load(file)


def calibration_file_exists(filename):
    filepath = get_calibration_file_path(filename)

    return os.path.exists(filepath)


def is_calibrated(filename):
    data = load_calibration_data(filename)

    if data is None:
        return False

    return data.get("calibrated", False)


def get_calibration_status():
    return {
        "camera_calibrated": is_calibrated(
            CAMERA_CALIBRATION_FILE
        ),
        "laser_calibrated": is_calibrated(
            LASER_CALIBRATION_FILE
        ),
        "turntable_calibrated": is_calibrated(
            TURNTABLE_CALIBRATION_FILE
        ),
        "scale_calibrated": is_calibrated(
            SCALE_CALIBRATION_FILE
        )
    }


def create_default_calibration_files():
    ensure_calibration_folder()

    if not calibration_file_exists(CAMERA_CALIBRATION_FILE):
        save_calibration_data(
            CAMERA_CALIBRATION_FILE,
            {
                "calibrated": False,
                "camera_matrix": None,
                "distortion_coefficients": None,
                "notes": "Camera calibration not performed yet."
            }
        )

    if not calibration_file_exists(LASER_CALIBRATION_FILE):
        save_calibration_data(
            LASER_CALIBRATION_FILE,
            {
                "calibrated": False,
                "laser_plane": None,
                "notes": "Laser plane calibration not performed yet."
            }
        )

    if not calibration_file_exists(TURNTABLE_CALIBRATION_FILE):
        save_calibration_data(
            TURNTABLE_CALIBRATION_FILE,
            {
                "calibrated": False,
                "steps_per_rotation": None,
                "angle_per_step": None,
                "turntable_center": None,
                "notes": "Turntable calibration not performed yet."
            }
        )

    if not calibration_file_exists(SCALE_CALIBRATION_FILE):
        save_calibration_data(
            SCALE_CALIBRATION_FILE,
            {
                "calibrated": False,
                "scale_factor": None,
                "unit": "mm",
                "notes": "Scale calibration not performed yet."
            }
        )

    add_log(
        "Default calibration files checked"
    )