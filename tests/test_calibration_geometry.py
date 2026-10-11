"""Synthetic math regression tests; no Raspberry Pi or GPIO required.

These are deterministic geometry checks, NOT physical scanner validation.
Run with: python -m pytest tests/test_calibration_geometry.py -q
"""
import math

import cv2
import numpy as np
import pytest

from scanner_core.calibration_geometry import (
    CalibrationFailure, compute_axis, compute_camera,
    compute_laser_plane, object_points
)


SQUARE = 10.0
K = np.array([[930., 0., 640.], [0., 930., 360.], [0., 0., 1.]])
DIST = np.zeros(5)
PROFILE = {
    "camera_matrix": K.tolist(),
    "distortion_coefficients": DIST.tolist(),
    "image_size": [1280, 720],
}
LOCAL_CENTER = np.array([40., 25., 0.])
PIVOT = np.array([0., 0., 560.])


def rot_x(degrees):
    a = math.radians(degrees)
    return np.array([[1., 0., 0.], [0., math.cos(a), -math.sin(a)],
                     [0., math.sin(a), math.cos(a)]])


def rot_y(degrees):
    a = math.radians(degrees)
    return np.array([[math.cos(a), 0., math.sin(a)], [0., 1., 0.],
                     [-math.sin(a), 0., math.cos(a)]])


def make_pose(angle, tilt):
    R = rot_y(angle) @ rot_x(tilt)
    center = PIVOT + rot_y(angle) @ np.array([22., 0., 0.])
    t = center - R @ LOCAL_CENTER
    return R, t


def project_board(angle, tilt):
    R, t = make_pose(angle, tilt)
    rvec, _ = cv2.Rodrigues(R)
    uv, _ = cv2.projectPoints(object_points(SQUARE), rvec, t, K, DIST)
    return uv.astype(np.float32), R, t


def test_requires_multiple_checkerboard_views():
    with pytest.raises(CalibrationFailure, match="need at least"):
        compute_camera([], SQUARE, (1280, 720))


def test_rotation_axis_from_three_independent_tilts():
    records = {}
    for index, tilt in enumerate((0, 20, -20), start=1):
        records[str(index)] = []
        for angle in range(0, 121, 10):
            corners, _, _ = project_board(angle, tilt)
            records[str(index)].append((angle, corners))
    result = compute_axis(records, SQUARE, PROFILE)
    axis = np.asarray(result["axis_direction_camera"])
    # Sign depends on positive direction of commanded motor rotation.
    assert abs(float(axis @ np.array([0., 1., 0.]))) > 0.99
    center = np.asarray(result["axis_origin_camera_mm"])
    assert np.linalg.norm(np.cross(center - PIVOT, axis)) < 1.5
    assert result["axis_offset_disagreement_mm"] < 1.5


def test_camera_calibration_solves_numerical_intrinsics():
    records = []
    for tilt in (0, 20, -20):
        for angle in range(0, 91, 15):
            corners, _, _ = project_board(angle, tilt)
            records.append((angle, corners))
    result = compute_camera(records, SQUARE, (1280, 720))
    assert result["calibrated"]
    assert result["usable_images"] >= 12
    assert result["rms_reprojection_px"] < 2.5


def test_laser_plane_reconstructed_from_bright_red_stripes():
    # Synthetic laser plane in camera coordinates is X = 8 mm.
    observations = []
    for tilt in (-15, 0, 15):
        for angle in (0, 10, 20, 30, 40, 50):
            corners, R, t = project_board(angle, tilt)
            frame = np.zeros((720, 1280, 3), dtype=np.uint8)
            rvec, _ = cv2.Rodrigues(R)
            laser_pts = []
            for local_y in np.linspace(0, 50, 120):
                if abs(R[0, 0]) < .1:
                    continue
                local_x = (8.0 - t[0] - R[0, 1] * local_y) / R[0, 0]
                if not 3 < local_x < 77:
                    continue
                laser_pts.append([local_x, local_y, 0])
            if len(laser_pts) < 20:
                continue
            uv, _ = cv2.projectPoints(np.array(laser_pts, np.float64), rvec, t, K, DIST)
            for pixel in uv.reshape(-1, 2):
                x, y = np.rint(pixel).astype(int)
                if 3 <= x < 1277 and 3 <= y < 717:
                    cv2.circle(frame, (x, y), 2, (0, 0, 255), -1)
            observations.append((frame, corners, angle))
    assert len(observations) >= 4
    result = compute_laser_plane(observations, SQUARE, PROFILE)
    n = np.array(result["plane_normal_camera"])
    assert abs(n[0]) > 0.98
    # Calibration is observed near the board at camera Z=560 mm. Extrapolating
    # the fitted plane to Z=0 magnifies subpixel line-centre measurement bias.
    predicted_x_at_board = -(result["plane_offset_mm"] + n[2] * 560.0) / n[0]
    assert abs(predicted_x_at_board - 8.0) < 1.0


def test_single_upright_orbit_estimates_rotation_axis():
    """Functional-first method: no forward/backward checkerboard tilts."""
    observed = []
    for angle in range(0, 121, 10):
        corners, _, _ = project_board(angle, 0)
        observed.append((angle, corners))
    axis = compute_axis({"1": observed}, SQUARE, PROFILE)
    assert axis["calibrated"] is True
    assert axis["single_orientation"] is True
    assert axis["limited_validation"] is True
    assert len(axis["placements"]) == 1
    direction = np.array(axis["axis_direction_camera"])
    assert abs(float(direction @ np.array([0., 1., 0.]))) > .99
    center = np.array(axis["axis_origin_camera_mm"])
    assert np.linalg.norm(np.cross(center - PIVOT, direction)) < 1.5


def test_single_upright_orbit_rejects_too_small_visible_arc():
    observed = []
    for angle in (0, 10, 20, 30, 40, 45, 46, 47):
        corners, _, _ = project_board(angle, 0)
        observed.append((angle, corners))
    with pytest.raises(CalibrationFailure, match="50 degrees"):
        compute_axis({"1": observed}, SQUARE, PROFILE)
