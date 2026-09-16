"""Red laser stripe extraction. Pixel coordinates are (u, v); no gaps are filled."""
from dataclasses import dataclass
from pathlib import Path
import json
import numbers

import cv2
import numpy as np


DEFAULT_DETECTOR_SETTINGS = {
    "roi": None, "hue_low_max": 10, "hue_high_min": 170,
    "min_saturation": 70, "min_value": 70, "min_red_excess": 25,
    "min_laser_delta": 20, "min_width": 1, "max_width": 15,
    "saturation_level": 250, "max_saturated_fraction": 0.6,
    "ambiguity_ratio": 1.3, "max_jump_px": 8.0, "max_gap_rows": 3,
    "min_segment_rows": 6, "min_confidence": 0.15,
}


def validate_detector_settings(settings=None):
    supplied = {} if settings is None else settings
    if not isinstance(supplied, dict):
        raise ValueError("Detector settings must be an object")
    unknown = set(supplied) - set(DEFAULT_DETECTOR_SETTINGS)
    if unknown:
        raise ValueError("Unknown detector settings: " + ", ".join(sorted(unknown)))
    values = dict(DEFAULT_DETECTOR_SETTINGS, **supplied)
    integer_ranges = {
        "hue_low_max": (0, 89), "hue_high_min": (90, 179),
        "min_saturation": (0, 255), "min_value": (0, 255),
        "min_red_excess": (0, 255), "min_laser_delta": (0, 255),
        "min_width": (1, 1000), "max_width": (1, 1000),
        "saturation_level": (1, 255), "max_gap_rows": (0, 1000),
        "min_segment_rows": (1, 10000),
    }
    for key, (low, high) in integer_ranges.items():
        value = values[key]
        if isinstance(value, bool) or not isinstance(value, numbers.Integral) or not low <= value <= high:
            raise ValueError(f"{key} must be an integer in [{low}, {high}]")
    for key, low, high in (
        ("max_saturated_fraction", 0, 1), ("ambiguity_ratio", 1.001, 100),
        ("max_jump_px", 0.01, 10000), ("min_confidence", 0, 1),
    ):
        value = values[key]
        if isinstance(value, bool) or not isinstance(value, numbers.Real) or not np.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{key} must be finite and in [{low}, {high}]")
    if values["min_width"] > values["max_width"]:
        raise ValueError("min_width must not exceed max_width")
    roi = values["roi"]
    if roi is not None:
        if not isinstance(roi, (list, tuple)) or len(roi) != 4:
            raise ValueError("roi must be [x, y, width, height]")
        if any(isinstance(v, bool) or not isinstance(v, numbers.Integral) for v in roi):
            raise ValueError("roi entries must be integers")
        if min(roi[:2]) < 0 or min(roi[2:]) < 1:
            raise ValueError("roi origin must be nonnegative and size positive")
        values["roi"] = list(roi)
    return values


@dataclass
class LaserDetection:
    points: np.ndarray
    confidence: np.ndarray
    mask: np.ndarray
    overlay: np.ndarray
    stats: dict

    @property
    def pixels(self):
        return self.points


def _image(image, name):
    if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3 or not image.size:
        raise ValueError(f"{name} must be a nonempty uint8 BGR image")


def detect_laser(image, settings=None, laser_off=None):
    """Select at most one observed stripe centre per row.

    Narrow red components must pass intensity, width, saturation, ambiguity,
    and continuity checks. A laser-off frame must use the same fixed pose,
    exposure, resolution, and lighting. It is supplied manually; this module
    does not assume electronic control of the laser.
    """
    _image(image, "image")
    params = validate_detector_settings(settings)
    height, width = image.shape[:2]
    roi = params["roi"] or [0, 0, width, height]
    x0, y0, rw, rh = roi
    if x0 + rw > width or y0 + rh > height:
        raise ValueError("Detector ROI extends beyond the image")
    if laser_off is not None:
        _image(laser_off, "laser_off")
        if laser_off.shape != image.shape:
            raise ValueError("Laser-on and laser-off images must have identical dimensions")
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)
    red = image[:, :, 2].astype(np.float32)
    excess = red - np.maximum(image[:, :, 0], image[:, :, 1]).astype(np.float32)
    selected = (((hue <= params["hue_low_max"]) | (hue >= params["hue_high_min"]))
                & (saturation >= params["min_saturation"])
                & (value >= params["min_value"])
                & (excess >= params["min_red_excess"]))
    weights = np.maximum(excess, 0)
    if laser_off is not None:
        delta = red - laser_off[:, :, 2].astype(np.float32)
        selected &= delta >= params["min_laser_delta"]
        weights = np.minimum(weights, np.maximum(delta, 0))
    roi_mask = np.zeros((height, width), dtype=bool)
    roi_mask[y0:y0 + rh, x0:x0 + rw] = True
    selected &= roi_mask
    stats = {
        "rows_in_roi": rh, "rows_accepted": 0, "candidate_pixels": int(selected.sum()),
        "paired_laser_off": laser_off is not None, "roi": list(roi),
        "rows_rejected": {"absent": 0, "no_valid_candidate": 0, "ambiguous": 0,
                          "discontinuous": 0, "short_segment": 0},
        "candidates_rejected": {"width": 0, "saturation": 0, "confidence": 0},
    }
    observations = []
    last = None
    for y in range(y0, y0 + rh):
        xs = np.flatnonzero(selected[y])
        if not len(xs):
            stats["rows_rejected"]["absent"] += 1
            continue
        groups = np.split(xs, np.flatnonzero(np.diff(xs) > 1) + 1)
        candidates = []
        for group in groups:
            stripe_width = len(group)
            if not params["min_width"] <= stripe_width <= params["max_width"]:
                stats["candidates_rejected"]["width"] += 1
                continue
            clipped = float(np.mean(red[y, group] >= params["saturation_level"]))
            if clipped > params["max_saturated_fraction"]:
                stats["candidates_rejected"]["saturation"] += 1
                continue
            intensities = weights[y, group]
            total = float(intensities.sum())
            if total <= 0:
                continue
            centre = float(np.dot(group, intensities) / total)
            confidence = float(np.clip(float(intensities.max()) / 150.0, 0, 1) * (1 - clipped))
            if confidence < params["min_confidence"]:
                stats["candidates_rejected"]["confidence"] += 1
                continue
            candidates.append((total, centre, confidence))
        if not candidates:
            stats["rows_rejected"]["no_valid_candidate"] += 1
            continue
        candidates.sort(reverse=True)
        best = candidates[0]
        if len(candidates) > 1 and best[0] < params["ambiguity_ratio"] * candidates[1][0]:
            stats["rows_rejected"]["ambiguous"] += 1
            continue
        centre = best[1]
        if last is not None:
            dy = y - last[1]
            if dy <= params["max_gap_rows"] + 1 and abs(centre - last[0]) > params["max_jump_px"] * dy:
                stats["rows_rejected"]["discontinuous"] += 1
                continue
        observations.append((centre, y, best[2]))
        last = (centre, y)
    # Remove short isolated runs, without inventing data in missing rows.
    accepted = []
    segment = []
    for observation in observations:
        if segment and (observation[1] - segment[-1][1] > params["max_gap_rows"] + 1
                        or abs(observation[0] - segment[-1][0]) > params["max_jump_px"] * (observation[1] - segment[-1][1])):
            if len(segment) >= params["min_segment_rows"]:
                accepted.extend(segment)
            else:
                stats["rows_rejected"]["short_segment"] += len(segment)
            segment = []
        segment.append(observation)
    if len(segment) >= params["min_segment_rows"]:
        accepted.extend(segment)
    else:
        stats["rows_rejected"]["short_segment"] += len(segment)
    arr = np.asarray(accepted, dtype=np.float64).reshape(-1, 3)
    points = arr[:, :2].copy()
    confidence = arr[:, 2].copy()
    stats["rows_accepted"] = len(points)
    stats["mean_confidence"] = float(confidence.mean()) if len(confidence) else 0.0
    overlay = image.copy()
    for u, v in points:
        cv2.circle(overlay, (int(round(u)), int(v)), 1, (0, 255, 0), -1)
    return LaserDetection(points, confidence, selected.astype(np.uint8) * 255, overlay, stats)


def save_diagnostics(result, folder, stem):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if Path(stem).name != stem or not stem:
        raise ValueError("Diagnostic stem must be a filename without directories")
    paths = {
        "mask": folder / f"{stem}_mask.png",
        "overlay": folder / f"{stem}_overlay.png",
        "statistics": folder / f"{stem}_statistics.json",
    }
    for name, image in (("mask", result.mask), ("overlay", result.overlay)):
        if not cv2.imwrite(str(paths[name]), image):
            raise OSError(f"Could not write {paths[name]}")
    paths["statistics"].write_text(json.dumps(result.stats, indent=2, allow_nan=False), encoding="utf-8")
    return {key: str(path.resolve()) for key, path in paths.items()}
