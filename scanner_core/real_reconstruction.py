"""First real reconstruction: laser triangulation + turntable de-rotation.

This module NEVER synthesizes example geometry. It reads real camera captures,
calibration files and commanded motor angles to generate an ASCII PLY point cloud.
All 3D coordinates are in millimetres. This is an experimental open-loop scanner.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import numpy as np


class ReconstructionError(RuntimeError):
    pass


CALIB_DIR = Path("outputs/calibration")
SCANS_DIR = Path("outputs/scans")
POINT_DIR = Path("outputs/point_clouds")


def _load_json(path):
    if not path.is_file():
        raise ReconstructionError(f"Missing required file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReconstructionError(f"Could not read {path}: {error}") from error


def load_calibration():
    camera = _load_json(CALIB_DIR / "camera_calibration.json")
    axis = _load_json(CALIB_DIR / "turntable_calibration.json")
    laser = _load_json(CALIB_DIR / "laser_calibration.json")
    for name, obj in (("camera", camera), ("rotation axis", axis), ("laser plane", laser)):
        if not obj.get("calibrated"):
            raise ReconstructionError(f"{name} has not been calibrated.")
    if abs(float(camera.get("square_size_mm", -1)) -
           float(laser.get("square_size_mm", -2))) > 0.001:
        raise ReconstructionError("Camera and laser square sizes differ; recalibrate.")
    return camera, axis, laser


def find_recent_real_session():
    """Return latest completed REAL scan; never use demo scan photographs."""
    if not SCANS_DIR.is_dir():
        raise ReconstructionError("No scan sessions exist yet.")
    for directory in sorted(SCANS_DIR.glob("scan_*"), reverse=True):
        try:
            meta = _load_json(directory / "metadata.json")
        except ReconstructionError:
            continue
        positions = meta.get("capture_positions") or []
        if (meta.get("status") == "finished" and
            len(positions) >= 8 and
            all(position.get("physical") is True for position in positions)):
            return directory, meta
    raise ReconstructionError(
        "No finished PHYSICAL scan with at least 8 photos and angle metadata."
    )


def stripe_pixels(frame):
    """Find center of a saturated red line for each alternate image row.

    Only suitable for a pale, non-red, non-reflective target. Red backgrounds
    may be misidentified; geometric limits filter those after triangulation.
    """
    h, w = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    b, g, r = cv2.split(frame)
    red = r.astype(np.int16)
    stronger = (red > g.astype(np.int16) + 35) & (red > b.astype(np.int16) + 35)
    hue = ((hsv[:, :, 0] <= 13) | (hsv[:, :, 0] >= 165))
    candidate = hue & (hsv[:, :, 1] >= 85) & (hsv[:, :, 2] >= 100) & stronger
    pts = []
    # Subsample for performance on the Raspberry Pi 3.
    for y in range(max(2, h // 30), h - max(2, h // 30), 2):
        xs = np.flatnonzero(candidate[y])
        if len(xs) < 2:
            continue
        # A true laser appears as a narrow, bright connected stripe.
        cuts = np.where(np.diff(xs) > 1)[0] + 1
        groups = np.split(xs, cuts)
        groups = [part for part in groups if 2 <= len(part) <= 22]
        if not groups:
            continue
        group = max(groups, key=lambda a: float(np.max(red[y, a])))
        weights = np.maximum(red[y, group].astype(float) - 90.0, 1.0)
        x = float(np.average(group, weights=weights))
        pts.append((x, float(y)))
    return np.asarray(pts, dtype=np.float64).reshape(-1, 2)


def project_laser_points(pixels, K, dist, plane_normal, plane_offset):
    """Intersect each undistorted camera ray with the calibrated laser plane."""
    if not len(pixels):
        return np.empty((0, 3), dtype=np.float64)
    xy = cv2.undistortPoints(pixels.reshape(-1, 1, 2), K, dist).reshape(-1, 2)
    rays = np.column_stack([xy, np.ones(len(xy))])
    denominator = rays @ plane_normal
    valid = np.abs(denominator) > 1e-7
    depths = np.zeros(len(rays), dtype=np.float64)
    depths[valid] = -plane_offset / denominator[valid]
    valid &= (depths > 10.0) & (depths < 2500.0)
    return rays[valid] * depths[valid, None]


def rotate_back_and_filter(points, degrees, origin, direction, radial_limit_mm=90.0):
    """Convert camera XYZ to common fixed-object frame, rejecting wall points."""
    if not len(points):
        return points
    centered = points - origin
    axis_height = centered @ direction
    radial = centered - axis_height[:, None] * direction
    radii = np.linalg.norm(radial, axis=1)
    # Axis origin sits close to the center of the checkerboard, not at base Z=0.
    within = ((radii <= radial_limit_mm) & (radii >= 0.0) &
              (axis_height >= -150.0) & (axis_height <= 150.0))
    centered = centered[within]
    if not len(centered):
        return centered
    rvec = direction * math.radians(-float(degrees))
    rotation, _ = cv2.Rodrigues(rvec)
    return centered @ rotation.T


def reconstruct_latest(progress_callback=None, radial_limit_mm=90.0):
    camera, axis, laser = load_calibration()
    folder, meta = find_recent_real_session()
    K = np.asarray(camera["camera_matrix"], dtype=np.float64)
    dist = np.asarray(camera["distortion_coefficients"], dtype=np.float64)
    origin = np.asarray(axis["axis_origin_camera_mm"], dtype=np.float64)
    direction = np.asarray(axis["axis_direction_camera"], dtype=np.float64)
    normal = np.asarray(laser["plane_normal_camera"], dtype=np.float64)
    offset = float(laser["plane_offset_mm"])
    if K.shape != (3, 3) or any(x.shape != (3,) for x in (origin, direction, normal)):
        raise ReconstructionError("Calibration geometry is malformed.")
    for v in (K, dist, origin, direction, normal, offset):
        if not np.all(np.isfinite(v)):
            raise ReconstructionError("Calibration contains non-finite numbers.")
    if abs(np.linalg.norm(direction) - 1) > 0.1:
        raise ReconstructionError("Turntable axis direction is invalid.")
    direction /= np.linalg.norm(direction)
    if abs(np.linalg.norm(normal) - 1) > 0.1:
        raise ReconstructionError("Laser plane normal is invalid.")
    if not 20.0 <= float(radial_limit_mm) <= 200.0:
        raise ReconstructionError("Object radial limit must be 20–200 mm.")

    # Use a right-handed coordinate frame where output Z matches the axis.
    x_basis = np.array([1.0, 0.0, 0.0])
    x_basis -= (x_basis @ direction) * direction
    if np.linalg.norm(x_basis) < 0.1:
        x_basis = np.array([0.0, 0.0, 1.0])
        x_basis -= (x_basis @ direction) * direction
    x_basis /= np.linalg.norm(x_basis)
    y_basis = np.cross(direction, x_basis)

    positions = meta["capture_positions"]
    all_points = []
    valid_frames = 0
    for i, position in enumerate(positions):
        filename = Path(str(position["filename"])).name
        frame = cv2.imread(str(folder / "captures" / filename), cv2.IMREAD_COLOR)
        if frame is None:
            raise ReconstructionError(f"Missing scan photo: {filename}")
        if [frame.shape[1], frame.shape[0]] != camera["image_size"]:
            raise ReconstructionError(
                f"Camera resolution changed at {filename}; calibration no longer applies."
            )
        pixels = stripe_pixels(frame)
        points_camera = project_laser_points(pixels, K, dist, normal, offset)
        points_local = rotate_back_and_filter(
            points_camera, float(position["angle_deg"]), origin,
            direction, radial_limit_mm=radial_limit_mm,
        )
        if len(points_local) >= 5:
            all_points.append(points_local)
            valid_frames += 1
        if progress_callback:
            progress_callback(i + 1, len(positions))

    if valid_frames < 6 or sum(len(p) for p in all_points) < 120:
        raise ReconstructionError(
            f"Only {valid_frames}/{len(positions)} photos yielded usable laser points. "
            "Check red line visibility, calibrations and the non-red matte object."
        )
    cloud = np.concatenate(all_points)
    # Use a lightweight voxel filter to reduce duplicate/noisy points.
    bins = np.rint(cloud / 0.6).astype(np.int64)
    _, indices = np.unique(bins, axis=0, return_index=True)
    cloud = cloud[np.sort(indices)]
    # Express in consistent output axes (Z = rotation axis).
    xyz = np.column_stack([cloud @ x_basis, cloud @ y_basis, cloud @ direction])
    if len(xyz) < 120:
        raise ReconstructionError("Too few independent 3D points remained after filtering.")
    if np.ptp(xyz, axis=0).max() > 400:
        raise ReconstructionError("Reconstructed size is implausible; review calibration.")

    POINT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = POINT_DIR / f"real_{folder.name}.ply"
    tmp_path = out_path.with_suffix(".ply.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        f.write("ply\nformat ascii 1.0\n")
        f.write("comment REAL scanner capture, camera/laser triangulation, mm\n")
        f.write(f"element vertex {len(xyz)}\n")
        f.write("property float x\nproperty float y\nproperty float z\nend_header\n")
        for x, y, z in xyz:
            f.write(f"{x:.4f} {y:.4f} {z:.4f}\n")
    tmp_path.replace(out_path)
    return {
        "file": out_path.name,
        "session": folder.name,
        "points": int(len(xyz)),
        "usable_frames": int(valid_frames),
        "total_frames": int(len(positions)),
        "bounding_box_mm": np.ptp(xyz, axis=0).round(2).tolist(),
        "limited_calibration": bool(
            camera.get("limited_validation") or axis.get("limited_validation") or
            laser.get("limited_validation")),
    }
