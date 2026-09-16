"""Metric camera/laser/turntable calibration and strict bundle validation.

Camera coordinates: +x right, +y down, +z forward; distances in millimetres.
Laser equation: normal.dot(point) + offset = 0. Angles follow the right-hand
rule around turntable.axis_direction; reconstruction applies their inverse.
"""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

import cv2
import numpy as np

CALIBRATION_FOLDER = "outputs/calibration"
DEFAULT_CALIBRATION_PATH = "outputs/calibration/calibration.json"
CAMERA_CALIBRATION_FILE = "camera_calibration.json"
LASER_CALIBRATION_FILE = "laser_calibration.json"
TURNTABLE_CALIBRATION_FILE = "turntable_calibration.json"
SCALE_CALIBRATION_FILE = "scale_calibration.json"


def _array(value, shape, name):
    def has_bool(item):
        return isinstance(item, (bool, np.bool_)) or (isinstance(item, (list, tuple)) and any(has_bool(x) for x in item))
    if has_bool(value):
        raise ValueError(f"{name} must contain numbers, not booleans")
    try:
        result = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid numeric {name}") from exc
    if result.shape != shape or not np.isfinite(result).all():
        raise ValueError(f"{name} must have finite shape {shape}")
    return result


def _number(value, name, positive=False):
    if isinstance(value, (bool, str)) or not isinstance(value, (int, float, np.number)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    return float(value)


def _finite_json(value):
    if isinstance(value, dict):
        for item in value.values():
            _finite_json(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _finite_json(item)
    elif isinstance(value, (float, np.floating)) and not math.isfinite(value):
        raise ValueError("Calibration contains NaN or infinity")


def camera_fingerprint(camera):
    geometry = {key: camera[key] for key in ("matrix", "distortion", "resolution")}
    return hashlib.sha256(json.dumps(geometry, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_calibration(data, require_complete=True, expected_resolution=None, allow_synthetic=True):
    """Return an independent normalized bundle; reject flags-only/invalid geometry.

    Partial bundles are for calibration tools only. Reconstruction requires all
    three sections. Optional camera fingerprints reject accidentally mixed files.
    """
    if not isinstance(data, dict):
        raise ValueError("Calibration must be an object")
    result = copy.deepcopy(data)
    _finite_json(result)
    if type(result.get("schema_version")) is not int or result["schema_version"] != 1:
        raise ValueError("Unsupported calibration schema_version (expected 1)")
    if result.get("source") not in ("physical", "synthetic"):
        raise ValueError("Calibration source must be physical or synthetic")
    if not allow_synthetic and result["source"] != "physical":
        raise ValueError("Physical images require physical calibration")
    if result.get("units") != "mm":
        raise ValueError("Calibration units must be mm")
    required = ("camera", "laser", "turntable") if require_complete else ("camera",)
    for section in required:
        if not isinstance(result.get(section), dict):
            raise ValueError(f"Missing calibration section: {section}")
    camera = result["camera"]
    matrix = _array(camera.get("matrix"), (3, 3), "camera.matrix")
    resolution = camera.get("resolution")
    if not isinstance(resolution, (list, tuple)) or len(resolution) != 2 or any(type(v) is not int or v <= 0 for v in resolution):
        raise ValueError("camera.resolution must contain two positive integers")
    if matrix[0, 0] <= 0 or matrix[1, 1] <= 0 or not np.allclose(matrix[2], [0, 0, 1]) or abs(matrix[0, 1]) > 1e-10 or abs(matrix[1, 0]) > 1e-10:
        raise ValueError("camera.matrix must be a standard OpenCV intrinsic matrix with positive focal lengths")
    if not (0 <= matrix[0, 2] < resolution[0] and 0 <= matrix[1, 2] < resolution[1]):
        raise ValueError("Camera principal point lies outside calibrated resolution")
    distortion = camera.get("distortion")
    if not isinstance(distortion, (list, tuple)) or len(distortion) not in (4, 5, 8, 12, 14):
        raise ValueError("camera.distortion must contain 4, 5, 8, 12 or 14 coefficients")
    camera["matrix"] = matrix.tolist()
    camera["distortion"] = _array(distortion, (len(distortion),), "camera.distortion").tolist()
    camera["resolution"] = list(resolution)
    if expected_resolution is not None and tuple(expected_resolution) != tuple(resolution):
        raise ValueError(f"Image resolution {expected_resolution} differs from calibration {resolution}")
    if "rms_reprojection_px" in camera and _number(camera["rms_reprojection_px"], "camera.rms_reprojection_px") < 0:
        raise ValueError("Reprojection error cannot be negative")
    fingerprint = camera_fingerprint(camera)
    for name in ("laser", "turntable"):
        if name not in result:
            continue
        if not isinstance(result[name], dict):
            raise ValueError(f"{name} must be an object")
        if result[name].get("camera_fingerprint", fingerprint) != fingerprint:
            raise ValueError(f"{name} calibration belongs to a different camera calibration")
    if "laser" in result:
        laser = result["laser"]
        normal = _array(laser.get("normal"), (3,), "laser.normal")
        length = float(np.linalg.norm(normal))
        if length < 1e-12:
            raise ValueError("Laser normal cannot be zero")
        offset = _number(laser.get("offset"), "laser.offset") / length
        if abs(offset) < 1e-9:
            raise ValueError("Laser plane passes through the camera centre; triangulation is degenerate")
        laser["normal"] = (normal / length).tolist()
        laser["offset"] = offset
    if "turntable" in result:
        table = result["turntable"]
        table["axis_point"] = _array(table.get("axis_point"), (3,), "turntable.axis_point").tolist()
        direction = _array(table.get("axis_direction"), (3,), "turntable.axis_direction")
        length = float(np.linalg.norm(direction))
        if length < 1e-12:
            raise ValueError("Turntable direction cannot be zero")
        table["axis_direction"] = (direction / length).tolist()
        table["angle_offset_deg"] = _number(table.get("angle_offset_deg", 0), "turntable.angle_offset_deg")
    return result


def load_calibration(path=DEFAULT_CALIBRATION_PATH, **validation_options):
    with Path(path).open(encoding="utf-8") as stream:
        return validate_calibration(json.load(stream), **validation_options)


def save_calibration(data, path=DEFAULT_CALIBRATION_PATH, require_complete=False):
    validated = validate_calibration(data, require_complete=require_complete)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".calibration-", suffix=".json", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(validated, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(destination)


def get_calibration_status(path=DEFAULT_CALIBRATION_PATH):
    status = dict(camera_calibrated=False, laser_calibrated=False, turntable_calibrated=False,
                  scale_calibrated=False, complete=False, path=str(path), error=None)
    try:
        bundle = load_calibration(path, require_complete=False, allow_synthetic=False)
        status["camera_calibrated"] = True
        status["laser_calibrated"] = "laser" in bundle
        status["turntable_calibrated"] = "turntable" in bundle
        status["scale_calibrated"] = bundle["units"] == "mm"
        status["complete"] = status["laser_calibrated"] and status["turntable_calibrated"]
    except (OSError, ValueError, TypeError) as exc:
        status["error"] = str(exc)
    return status


def create_default_calibration_files():
    """Compatibility shim: never manufacture successful or placeholder calibration."""
    Path(CALIBRATION_FOLDER).mkdir(parents=True, exist_ok=True)


def checkerboard_points(columns, rows, square_mm):
    if type(columns) is not int or type(rows) is not int or min(columns, rows) < 3:
        raise ValueError("Checkerboard needs at least 3 x 3 inner corners")
    size = _number(square_mm, "square_mm", positive=True)
    points = np.zeros((columns * rows, 3), np.float32)
    points[:, :2] = np.mgrid[0:columns, 0:rows].T.reshape(-1, 2) * size
    return points


def find_checkerboard(image, columns, rows):
    if image is None or image.ndim not in (2, 3):
        raise ValueError("Missing/invalid checkerboard image")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    found, corners = cv2.findChessboardCorners(gray, (columns, rows), cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE)
    if not found:
        raise ValueError("Checkerboard inner corners were not detected")
    corners = cv2.cornerSubPix(gray, corners, (5, 5), (-1, -1), (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 40, 0.001))
    return corners.reshape(-1, 2)


def calibrate_camera_observations(image_points, resolution, columns, rows, square_mm, min_views=8, max_rms_px=1.0):
    """Calibrate measured checkerboard corners; require location and tilt diversity."""
    if type(min_views) is not int or min_views < 5 or len(image_points) < min_views:
        raise ValueError(f"At least {max(5, min_views)} checkerboard observations are required")
    if len(resolution) != 2 or any(type(x) is not int or x <= 0 for x in resolution):
        raise ValueError("Resolution requires positive integer width and height")
    max_rms_px = _number(max_rms_px, "max_rms_px", positive=True)
    target = checkerboard_points(columns, rows, square_mm)
    observations = [_array(points, (len(target), 2), "checkerboard corners").astype(np.float32) for points in image_points]
    for points in observations:
        if np.any(points < 0) or np.any(points[:, 0] >= resolution[0]) or np.any(points[:, 1] >= resolution[1]):
            raise ValueError("Checkerboard corners lie outside image")
    centres = np.asarray([points.mean(axis=0) for points in observations]) / np.asarray(resolution)
    centre_span = np.ptp(centres, axis=0)
    if np.max(centre_span) < 0.10:
        raise ValueError("Insufficient checkerboard position diversity; move target across the image")
    unique = np.unique(np.round(centres, 2), axis=0)
    if len(unique) < 5:
        raise ValueError("At least five distinct checkerboard positions are required")
    rms, matrix, distortion, rvecs, tvecs = cv2.calibrateCamera([target] * len(observations), observations, tuple(resolution), None, None)
    errors, normals = [], []
    for corners, rvec, tvec in zip(observations, rvecs, tvecs):
        projected, _ = cv2.projectPoints(target, rvec, tvec, matrix, distortion)
        errors.append(float(np.sqrt(np.mean(np.sum((projected.reshape(-1, 2) - corners) ** 2, axis=1)))))
        normals.append(cv2.Rodrigues(rvec)[0][:, 2])
        if np.min((cv2.Rodrigues(rvec)[0] @ target.T + tvec).T[:, 2]) <= 0:
            raise ValueError("Calibration target was estimated behind camera")
    dots = np.clip(np.asarray(normals) @ np.asarray(normals).T, -1, 1)
    tilt_span = float(np.degrees(np.arccos(dots)).max())
    if tilt_span < 10:
        raise ValueError("Insufficient target tilt diversity; tilt target around horizontal and vertical axes")
    if not math.isfinite(rms) or rms > max_rms_px or max(errors) > 2 * max_rms_px:
        raise ValueError(f"Excessive reprojection error: RMS {rms:.4f}px, worst view {max(errors):.4f}px")
    camera = {"matrix": matrix.tolist(), "distortion": distortion.ravel().tolist(), "resolution": list(resolution),
              "rms_reprojection_px": float(rms), "per_view_rms_px": errors, "observation_count": len(observations),
              "target": {"columns": columns, "rows": rows, "square_mm": float(square_mm)},
              "diversity": {"centre_span_fraction": centre_span.tolist(), "normal_span_deg": tilt_span},
              "method": "opencv_checkerboard_calibrateCamera"}
    return validate_calibration({"schema_version": 1, "source": "physical", "units": "mm", "camera": camera}, require_complete=False)["camera"]


def solve_board_pose(corners, camera, columns, rows, square_mm, max_rms_px=1.0):
    target = checkerboard_points(columns, rows, square_mm)
    corners = _array(corners, (len(target), 2), "checkerboard corners")
    matrix, distortion = np.asarray(camera["matrix"]), np.asarray(camera["distortion"])
    ok, rvec, tvec = cv2.solvePnP(target, corners, matrix, distortion, flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok:
        raise ValueError("Checkerboard pose estimation failed")
    rotation = cv2.Rodrigues(rvec)[0]
    if np.min((rotation @ target.T + tvec).T[:, 2]) <= 0:
        raise ValueError("Checkerboard pose lies behind camera")
    projected, _ = cv2.projectPoints(target, rvec, tvec, matrix, distortion)
    rms = float(np.sqrt(np.mean(np.sum((projected.reshape(-1, 2) - corners) ** 2, axis=1))))
    if not math.isfinite(rms) or rms > max_rms_px:
        raise ValueError(f"Target pose reprojection error {rms:.4f}px exceeds {max_rms_px}px")
    return rotation, tvec.ravel(), rms


def laser_points_on_board(pixels, camera, rotation, translation, columns, rows, square_mm):
    pixels = np.asarray(pixels, dtype=np.float64)
    if pixels.ndim != 2 or pixels.shape[1] != 2 or not np.isfinite(pixels).all():
        raise ValueError("Laser pixels must be finite Nx2")
    if len(pixels) == 0:
        return np.empty((0, 3))
    corrected = cv2.undistortPoints(pixels.reshape(-1, 1, 2), np.asarray(camera["matrix"]), np.asarray(camera["distortion"])).reshape(-1, 2)
    rays = np.column_stack((corrected, np.ones(len(corrected))))
    normal = rotation[:, 2]
    denominator = rays @ normal
    valid = np.abs(denominator) > 1e-8
    distances = np.zeros(len(rays))
    distances[valid] = float(normal @ translation) / denominator[valid]
    points = rays * distances[:, None]
    local = (points - translation) @ rotation
    valid &= (distances > 0) & np.isfinite(points).all(axis=1)
    valid &= (local[:, 0] >= 0) & (local[:, 0] <= (columns - 1) * square_mm)
    valid &= (local[:, 1] >= 0) & (local[:, 1] <= (rows - 1) * square_mm)
    return points[valid]


def fit_laser_plane(observations, max_rms_mm=0.5, min_spread_mm=5.0):
    """Fit a plane to stripes from >=3 target poses, rejecting collinear support."""
    if len(observations) < 3:
        raise ValueError("Laser plane requires at least three target positions")
    max_rms_mm = _number(max_rms_mm, "max_rms_mm", positive=True)
    min_spread_mm = _number(min_spread_mm, "min_spread_mm", positive=True)
    groups = []
    for points in observations:
        points = np.asarray(points, dtype=np.float64)
        if points.ndim != 2 or points.shape[1] != 3 or len(points) < 10 or not np.isfinite(points).all():
            raise ValueError("Each laser observation requires at least ten finite 3D points")
        groups.append(points)
    centres = np.asarray([points.mean(axis=0) for points in groups])
    if max(np.linalg.norm(a - b) for a in centres for b in centres) < min_spread_mm:
        raise ValueError("Target observations lack positional diversity")
    points = np.vstack(groups)
    centre = points.mean(axis=0)
    _, singular, basis = np.linalg.svd(points - centre, full_matrices=False)
    spreads = singular / np.sqrt(len(points))
    if spreads[1] < min_spread_mm or spreads[1] / spreads[0] < 0.02:
        raise ValueError("Laser samples are collinear or have insufficient plane support")
    normal = basis[-1]
    offset = -float(normal @ centre)
    if offset > 0:
        normal, offset = -normal, -offset
    residuals = points @ normal + offset
    rms = float(np.sqrt(np.mean(residuals ** 2)))
    if rms > max_rms_mm or np.max(np.abs(residuals)) > 4 * max_rms_mm:
        raise ValueError(f"Laser plane residual too high: RMS {rms:.4f}mm")
    if abs(offset) < 1e-9:
        raise ValueError("Laser plane passes through camera centre")
    return {"normal": normal.tolist(), "offset": offset, "rms_residual_mm": rms,
            "max_residual_mm": float(np.abs(residuals).max()), "point_count": len(points),
            "observation_count": len(groups), "spread_mm": spreads.tolist(),
            "per_observation_rms_mm": [float(np.sqrt(np.mean((p @ normal + offset) ** 2))) for p in groups],
            "method": "checkerboard_ray_intersections_svd"}


def fit_turntable_axis(points, angles_deg, max_rms_mm=1.0):
    """Fit a rotating tracked 3D point using its explicitly supplied angles.

    p(theta)=centre+u*cos(theta)+v*sin(theta); the sign of u cross v
    defines the axis compatible with the supplied signed angles.
    """
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 6 or not np.isfinite(points).all():
        raise ValueError("Axis fit requires at least six finite measured 3D points")
    angles = _array(angles_deg, (len(points),), "angles_deg")
    max_rms_mm = _number(max_rms_mm, "max_rms_mm", positive=True)
    wrapped = np.sort(np.unique(np.mod(angles, 360)))
    if len(wrapped) < 6:
        raise ValueError("Axis fit needs at least six distinct angles")
    coverage = 360 - np.max(np.diff(np.r_[wrapped, wrapped[0] + 360]))
    if coverage < 120:
        raise ValueError("Axis observations must cover at least 120 degrees")
    theta = np.deg2rad(angles)
    design = np.column_stack((np.ones(len(theta)), np.cos(theta), np.sin(theta)))
    if np.linalg.cond(design) > 20:
        raise ValueError("Axis angle distribution is ill conditioned")
    coefficients = np.linalg.lstsq(design, points, rcond=None)[0]
    centre, u, v = coefficients
    ru, rv = np.linalg.norm(u), np.linalg.norm(v)
    if min(ru, rv) < 1 or abs(ru - rv) / max(ru, rv) > 0.05 or abs(u @ v) / (ru * rv) > 0.05:
        raise ValueError("Tracked point does not describe a circle at supplied angles")
    direction = np.cross(u, v)
    direction /= np.linalg.norm(direction)
    first = u / ru
    second = np.cross(direction, first)
    radius = (ru + rv) / 2
    predicted = centre + radius * (np.cos(theta)[:, None] * first + np.sin(theta)[:, None] * second)
    residuals = np.linalg.norm(points - predicted, axis=1)
    rms = float(np.sqrt(np.mean(residuals ** 2)))
    if rms > max_rms_mm or residuals.max() > 3 * max_rms_mm:
        raise ValueError(f"Axis fit residual too high: RMS {rms:.4f}mm")
    return {"axis_point": centre.tolist(), "axis_direction": direction.tolist(), "angle_offset_deg": 0.0,
            "radius_mm": float(radius), "rms_residual_mm": rms, "max_residual_mm": float(residuals.max()),
            "angle_coverage_deg": float(coverage), "observation_count": len(points),
            "method": "tracked_point_with_explicit_angles", "angle_convention": "right_hand_about_axis_direction"}
