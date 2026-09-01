import json
import os
from datetime import datetime

import cv2
import numpy as np


CALIBRATION_FOLDER = "outputs/calibration"
LASER_BACKGROUND_MASK_FILE = "laser_background_mask.png"
LASER_BACKGROUND_METADATA_FILE = "laser_background_metadata.json"

DEFAULT_HSV_RANGES = (
    (np.array([0, 60, 60]), np.array([15, 255, 255])),
    (np.array([165, 60, 60]), np.array([180, 255, 255])),
)

# ROI tuned for the current Raspberry scanner camera view.
# Coordinates are based on 640x480 frames.
ROI_X_MIN = 90
ROI_X_MAX = 540
ROI_Y_MIN = 130
ROI_Y_MAX = 455

BACKGROUND_SUBTRACTION_DILATE_SIZE = 7


def ensure_calibration_folder():
    os.makedirs(
        CALIBRATION_FOLDER,
        exist_ok=True
    )


def get_background_mask_path():
    ensure_calibration_folder()

    return os.path.join(
        CALIBRATION_FOLDER,
        LASER_BACKGROUND_MASK_FILE
    )


def get_background_metadata_path():
    ensure_calibration_folder()

    return os.path.join(
        CALIBRATION_FOLDER,
        LASER_BACKGROUND_METADATA_FILE
    )


def laser_background_exists():
    return os.path.exists(
        get_background_mask_path()
    )


def apply_roi(mask):
    height, width = mask.shape

    roi_mask = np.zeros_like(mask)

    x_min = max(0, min(ROI_X_MIN, width))
    x_max = max(0, min(ROI_X_MAX, width))
    y_min = max(0, min(ROI_Y_MIN, height))
    y_max = max(0, min(ROI_Y_MAX, height))

    roi_mask[y_min:y_max, x_min:x_max] = mask[y_min:y_max, x_min:x_max]

    return roi_mask


def create_raw_laser_mask(frame):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    mask = None

    for lower_bound, upper_bound in DEFAULT_HSV_RANGES:
        current_mask = cv2.inRange(
            hsv,
            lower_bound,
            upper_bound
        )

        if mask is None:
            mask = current_mask
        else:
            mask = cv2.bitwise_or(mask, current_mask)

    mask = apply_roi(mask)

    kernel = np.ones((3, 3), np.uint8)

    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_OPEN,
        kernel
    )

    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        kernel
    )

    return mask


def subtract_background(mask):
    if not laser_background_exists():
        return mask

    background = cv2.imread(
        get_background_mask_path(),
        cv2.IMREAD_GRAYSCALE
    )

    if background is None:
        return mask

    if background.shape != mask.shape:
        return mask

    kernel = np.ones(
        (
            BACKGROUND_SUBTRACTION_DILATE_SIZE,
            BACKGROUND_SUBTRACTION_DILATE_SIZE
        ),
        np.uint8
    )

    dilated_background = cv2.dilate(
        background,
        kernel,
        iterations=1
    )

    cleaned_mask = mask.copy()
    cleaned_mask[dilated_background > 0] = 0

    return cleaned_mask


def create_laser_mask(frame, subtract_calibrated_background=True):
    mask = create_raw_laser_mask(frame)

    if subtract_calibrated_background:
        mask = subtract_background(mask)

    return mask


def create_laser_overlay(frame):
    mask = create_laser_mask(frame)

    red_layer = np.zeros_like(frame)
    red_layer[:, :, 2] = mask

    overlay = cv2.addWeighted(
        frame,
        0.75,
        red_layer,
        0.85,
        0
    )

    cv2.rectangle(
        overlay,
        (ROI_X_MIN, ROI_Y_MIN),
        (ROI_X_MAX, ROI_Y_MAX),
        (0, 255, 255),
        2
    )

    return overlay


def encode_laser_mask(frame):
    mask = create_laser_mask(frame)

    success, buffer = cv2.imencode(
        ".jpg",
        mask
    )

    if not success:
        return None

    return buffer.tobytes()


def encode_laser_overlay(frame):
    overlay = create_laser_overlay(frame)

    success, buffer = cv2.imencode(
        ".jpg",
        overlay
    )

    if not success:
        return None

    return buffer.tobytes()


def save_laser_background(frame):
    raw_mask = create_raw_laser_mask(frame)

    ensure_calibration_folder()

    mask_path = get_background_mask_path()
    metadata_path = get_background_metadata_path()

    cv2.imwrite(
        mask_path,
        raw_mask
    )

    height, width = raw_mask.shape
    laser_pixels = int(
        np.count_nonzero(raw_mask)
    )

    metadata = {
        "calibrated": True,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "width": width,
        "height": height,
        "laser_pixels": laser_pixels,
        "laser_coverage_percent": round(
            (laser_pixels / float(width * height)) * 100,
            4
        ),
        "roi": {
            "x_min": ROI_X_MIN,
            "x_max": ROI_X_MAX,
            "y_min": ROI_Y_MIN,
            "y_max": ROI_Y_MAX
        },
        "mask_file": mask_path
    }

    with open(metadata_path, "w", encoding="utf-8") as file:
        json.dump(
            metadata,
            file,
            indent=4
        )

    return metadata


def get_laser_background_status():
    if not laser_background_exists():
        return {
            "calibrated": False
        }

    metadata_path = get_background_metadata_path()

    if not os.path.exists(metadata_path):
        return {
            "calibrated": True,
            "metadata": None
        }

    with open(metadata_path, "r", encoding="utf-8") as file:
        return json.load(file)


def analyze_laser_frame(frame):
    mask = create_laser_mask(frame)

    laser_pixels = int(
        np.count_nonzero(mask)
    )

    height, width = mask.shape
    total_pixels = width * height

    return {
        "width": width,
        "height": height,
        "laser_pixels": laser_pixels,
        "laser_coverage_percent": round(
            (laser_pixels / float(total_pixels)) * 100,
            4
        ),
        "background_calibrated": laser_background_exists(),
        "roi": {
            "x_min": ROI_X_MIN,
            "x_max": ROI_X_MAX,
            "y_min": ROI_Y_MIN,
            "y_max": ROI_Y_MAX
        }
    }
