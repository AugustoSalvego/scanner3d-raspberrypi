"""Measured checkerboard geometry for the physical turntable laser scanner.

Coordinate frame is the fixed camera frame, units are millimetres.
Algorithms use OpenCV / NumPy rather than an external scanner implementation.
"""
from __future__ import annotations

import math
import cv2
import numpy as np

BOARD_COLUMNS = 9
BOARD_ROWS = 6
MIN_CORNER_VIEWS = 12


class CalibrationFailure(RuntimeError):
    """Calibration could not be computed reliably from the actual images."""


def object_points(square_mm):
    if not 0 < float(square_mm) <= 100:
        raise CalibrationFailure("Measured chessboard square size must be in (0, 100] mm.")
    grid = np.zeros((BOARD_ROWS * BOARD_COLUMNS, 3), np.float32)
    grid[:, :2] = np.mgrid[0:BOARD_COLUMNS, 0:BOARD_ROWS].T.reshape(-1, 2)
    grid *= float(square_mm)
    return grid


def find_board(frame):
    """Return ordered 54 internal corners with subpixel accuracy, or None."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    pattern = (BOARD_COLUMNS, BOARD_ROWS)
    corners = None
    if hasattr(cv2, "findChessboardCornersSB"):
        try:
            ok, corners = cv2.findChessboardCornersSB(
                gray, pattern, flags=cv2.CALIB_CB_NORMALIZE_IMAGE
            )
            if not ok:
                corners = None
        except cv2.error:
            corners = None
    if corners is None:
        ok, corners = cv2.findChessboardCorners(
            gray, pattern, flags=cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
        )
        if not ok:
            return None
        cv2.cornerSubPix(
            gray, corners, (5, 5), (-1, -1),
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 40, 0.001)
        )
    return np.asarray(corners, dtype=np.float32).reshape(-1, 1, 2)


def board_area_pixels(corners):
    """Projected convex hull area; reject near edge-on/degenerate board poses."""
    if corners is None:
        return 0.0
    points = np.asarray(corners, np.float32).reshape(-1, 2)
    if len(points) != BOARD_ROWS * BOARD_COLUMNS:
        return 0.0
    return float(cv2.contourArea(cv2.convexHull(points)))


def solve_board_pose(corners, square_mm, camera):
    if board_area_pixels(corners) < 200.0:
        raise CalibrationFailure("Checkerboard is too edge-on for reliable pose estimation.")
    obj = object_points(square_mm)
    K = np.asarray(camera["camera_matrix"], np.float64)
    dist = np.asarray(camera["distortion_coefficients"], np.float64)
    try:
        success, rvec, tvec = cv2.solvePnP(
            obj, np.ascontiguousarray(corners, dtype=np.float32),
            K, dist, flags=cv2.SOLVEPNP_ITERATIVE
        )
    except cv2.error as error:
        raise CalibrationFailure(f"OpenCV checkerboard pose failed: {error}") from error
    if not success or tvec[2, 0] <= 0:
        raise CalibrationFailure("Unable to estimate a visible checkerboard pose.")
    R, _ = cv2.Rodrigues(rvec)
    local_center = np.array([4.0 * square_mm, 2.5 * square_mm, 0.0])
    center = R @ local_center + tvec.reshape(3)
    normal = R[:, 2]
    plane_offset = -float(np.dot(normal, tvec.reshape(3)))
    return center, normal, plane_offset, R, tvec.reshape(3)


def compute_camera(observations, square_mm, image_size):
    """Observations: (angle_deg, corners), all from one fixed camera."""
    valid = [(angle, pts) for angle, pts in observations
             if pts is not None and board_area_pixels(pts) >= 200.0]
    if len(valid) < MIN_CORNER_VIEWS:
        raise CalibrationFailure(
            f"Found chessboard in {len(valid)} images; need at least {MIN_CORNER_VIEWS}. "
            "Move the board into the camera view and use diffuse white light."
        )
    obj = object_points(square_mm)
    objects = [obj.copy() for _ in valid]
    image_pts = [np.asarray(pts, np.float32).reshape(-1, 1, 2) for _, pts in valid]
    try:
        rms, K, dist, rvecs, tvecs = cv2.calibrateCamera(
            objects, image_pts, tuple(image_size), None, None,
            flags=cv2.CALIB_FIX_K3
        )
    except cv2.error as error:
        raise CalibrationFailure(f"OpenCV camera calibration failed: {error}") from error
    if not np.isfinite(rms) or rms > 2.5 or not np.isfinite(K).all():
        raise CalibrationFailure(
            f"Camera reprojection RMS is {rms:.3f} px; maximum is 2.5 px. "
            "Check sharpness, rigid board, and varied board inclinations."
        )
    if K[0, 0] <= 0 or K[1, 1] <= 0:
        raise CalibrationFailure("Estimated camera focal lengths are not valid.")
    per_view = []
    for object_pts, image, rvec, tvec in zip(objects, image_pts, rvecs, tvecs):
        reproj, _ = cv2.projectPoints(object_pts, rvec, tvec, K, dist)
        per_view.append(float(np.sqrt(np.mean(np.sum((reproj - image) ** 2, axis=2)))))
    return {
        "calibrated": True,
        "board_inner_corners": [BOARD_COLUMNS, BOARD_ROWS],
        "square_size_mm": float(square_mm),
        "image_size": list(map(int, image_size)),
        "camera_matrix": K.tolist(),
        "distortion_coefficients": dist.ravel().tolist(),
        "rms_reprojection_px": float(rms),
        "max_view_error_px": max(per_view),
        "usable_images": len(valid),
    }


def compute_axis(by_placement, square_mm, camera):
    """Fit C + U*cos(theta) + V*sin(theta) to board centres at motor angles.

    C is on the axis; cross(U,V) determines the axis direction. Three tilted
    placements give independent axis estimates and expose mechanical wobble.
    """
    axes = []
    origins = []
    reports = []
    for key in ("1", "2", "3"):
        samples = by_placement.get(key, [])
        if len(samples) < 6:
            raise CalibrationFailure(
                f"Board placement {key}: only {len(samples)} recognized views; need 6."
            )
        theta = []
        centers = []
        for angle, corners in samples:
            if board_area_pixels(corners) < 200.0:
                continue
            try:
                center, _, _, _, _ = solve_board_pose(corners, square_mm, camera)
            except CalibrationFailure:
                continue
            theta.append(math.radians(float(angle)))
            centers.append(center)
        if len(theta) < 6:
            raise CalibrationFailure(f"Not enough checkerboard poses for placement {key}.")
        # A narrow angular arc makes the fitted rotation axis unreliable.
        if np.ptp(np.unwrap(theta)) < math.radians(40):
            raise CalibrationFailure(
                f"Placement {key} covers under 40 degrees of visible rotation."
            )
        design = np.column_stack([np.ones(len(theta)), np.cos(theta), np.sin(theta)])
        fit, _, _, _ = np.linalg.lstsq(design, np.asarray(centers), rcond=None)
        center, U, V = fit
        radius = (np.linalg.norm(U) + np.linalg.norm(V)) / 2.0
        cross = np.cross(U, V)
        normal_size = np.linalg.norm(cross)
        if radius < 4.0 or normal_size < 1e-6:
            raise CalibrationFailure(
                f"Placement {key}: board motion is too small to determine the rotation axis."
            )
        axis = cross / normal_size
        residual = np.asarray(centers) - design @ fit
        rms = float(np.sqrt(np.mean(np.sum(residual * residual, axis=1))))
        if rms > 3.0:
            raise CalibrationFailure(
                f"Placement {key}: observed rotation does not fit a circle (RMS {rms:.2f} mm). "
                "Check platform wobble and lost motor steps."
            )
        axes.append(axis)
        origins.append(center)
        reports.append({"placement": int(key), "views": len(theta),
                        "orbit_radius_mm": float(radius), "fit_rms_mm": rms})
    direction = np.mean(axes, axis=0)
    direction /= np.linalg.norm(direction)
    spread = [float(math.degrees(math.acos(np.clip(np.dot(a, direction), -1, 1))))
              for a in axes]
    if max(spread) > 3.0:
        raise CalibrationFailure(
            f"The three positions disagree about the axis by {max(spread):.2f} degrees. "
            "Secure the platform and prevent board movement while rotating."
        )
    origin = np.mean(origins, axis=0)
    # Perpendicular distance of each fitted orbit centre to the common axis.
    center_spread = [
        float(np.linalg.norm(np.cross(c - origin, direction))) for c in origins
    ]
    if max(center_spread) > 3.0:
        raise CalibrationFailure(
            f"Turntable axis positions differ by {max(center_spread):.2f} mm. "
            "The turntable may be tilting under the checkerboard."
        )
    return {
        "calibrated": True,
        "axis_origin_camera_mm": origin.tolist(),
        "axis_direction_camera": direction.tolist(),
        "axis_angle_disagreement_deg": max(spread),
        "axis_offset_disagreement_mm": max(center_spread),
        "placements": reports,
    }


def find_laser_pixels(frame, corners):
    """Detect red stripe *inside* the checkerboard, never the red surroundings."""
    h, w = frame.shape[:2]
    region = np.zeros((h, w), dtype=np.uint8)
    polygon = cv2.convexHull(np.rint(corners.reshape(-1, 2)).astype(np.int32))
    cv2.fillConvexPoly(region, polygon, 255)
    region = cv2.erode(region, np.ones((3, 3), np.uint8))
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    low = cv2.inRange(hsv, (0, 85, 90), (13, 255, 255))
    high = cv2.inRange(hsv, (165, 85, 90), (179, 255, 255))
    candidate = cv2.bitwise_and(cv2.bitwise_or(low, high), region)
    result = []
    red = frame[:, :, 2].astype(np.float32)
    for y in range(0, h, 2):
        xs = np.flatnonzero(candidate[y] > 0)
        if xs.size == 0:
            continue
        # Prefer strongest red signal; use weighted subpixel centre near its peak.
        selected = int(xs[np.argmax(red[y, xs])])
        nearby = xs[np.abs(xs - selected) <= 3]
        if nearby.size == 0:
            continue
        weights = np.maximum(1.0, red[y, nearby] - 70.0)
        x = float(np.average(nearby, weights=weights))
        result.append((x, float(y)))
    return np.asarray(result, dtype=np.float64).reshape(-1, 2)


def compute_laser_plane(observations, square_mm, camera):
    """Fit the 3D light plane using laser pixels crossing known checkerboard poses."""
    K = np.asarray(camera["camera_matrix"], dtype=np.float64)
    dist = np.asarray(camera["distortion_coefficients"], dtype=np.float64)
    all_points = []
    accepted_frames = 0
    for frame, corners, _angle in observations:
        if corners is None:
            continue
        pixels = find_laser_pixels(frame, corners)
        if len(pixels) < 8:
            continue
        try:
            _, normal, board_offset, _, _ = solve_board_pose(corners, square_mm, camera)
        except CalibrationFailure:
            continue
        xy = cv2.undistortPoints(
            pixels.reshape(-1, 1, 2), K, dist
        ).reshape(-1, 2)
        rays = np.column_stack([xy, np.ones(len(xy))])
        denom = rays @ normal
        ok = np.abs(denom) > 1e-7
        depth = np.zeros(len(xy))
        depth[ok] = -board_offset / denom[ok]
        ok &= (depth > 0) & (depth < 4000)
        world = rays[ok] * depth[ok, None]
        if len(world) >= 8:
            all_points.append(world)
            accepted_frames += 1
    if accepted_frames < 4 or sum(len(p) for p in all_points) < 100:
        raise CalibrationFailure(
            "Laser line recognized on too few checkerboard images. "
            "Use the red laser with moderate exposure and no green lighting."
        )
    xyz = np.concatenate(all_points)
    # Fit a plane, reject outliers, then refit.
    centroid = xyz.mean(axis=0)
    _, singular, basis = np.linalg.svd(xyz - centroid, full_matrices=False)
    normal = basis[-1]
    distances = (xyz - centroid) @ normal
    cutoff = max(1.0, 3.0 * float(np.median(np.abs(distances))))
    inliers = xyz[np.abs(distances) <= cutoff]
    if len(inliers) < 100:
        raise CalibrationFailure("Laser plane has insufficient consistent 3D points.")
    centroid = inliers.mean(axis=0)
    _, singular, basis = np.linalg.svd(inliers - centroid, full_matrices=False)
    normal = basis[-1]
    offset = -float(np.dot(normal, centroid))
    errors = inliers @ normal + offset
    rms = float(np.sqrt(np.mean(errors ** 2)))
    if not np.isfinite(rms) or rms > 2.0:
        raise CalibrationFailure(
            f"Laser plane RMS is {rms:.2f} mm (limit 2 mm). "
            "Do not move the laser between captures; reduce glare."
        )
    if singular[-2] < 3.0:
        raise CalibrationFailure("Laser points lack enough 3D variation to fit a plane.")
    return {
        "calibrated": True,
        "plane_normal_camera": normal.tolist(),
        "plane_offset_mm": offset,
        "rms_plane_mm": rms,
        "laser_points_used": int(len(inliers)),
        "frames_used": int(accepted_frames),
    }
