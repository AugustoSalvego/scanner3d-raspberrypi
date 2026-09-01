import cv2
import numpy as np


DEFAULT_HSV_RANGES = (
    (np.array([0, 60, 60]), np.array([15, 255, 255])),
    (np.array([165, 60, 60]), np.array([180, 255, 255])),
)


def create_laser_mask(frame):
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
    }
