"""Calibrated laser triangulation and turntable de-rotation.

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


def stripe_pixels(frame, camera_geometry=None, radial_limit_mm=90.0):
    """Locate actual laser-line centers, even on cyan-lit/low-red surfaces.

    A saturated-red HSV mask can reject real laser measurements if green and
    blue illumination remain strong. Instead, locate narrow red-channel peaks,
    group them into continuous traces, and, when calibrated geometry is given,
    prefer traces geometrically close to the turntable axis.

    The selection does not rely on any fixed image-coordinate object region.
    It is nevertheless a heuristic: calibration and a visible laser line are
    still required for reliable scanning.
    """
    height, width = frame.shape[:2]
    red = frame[:, :, 2].astype(np.int16)
    threshold = max(25.0, min(60.0, float(np.percentile(red, 99.5)) * 0.7))
    background = max(10.0, threshold - 10.0)
    candidates = []

    for y in range(2, height - 2):
        row = red[y]
        xs = np.flatnonzero(row > threshold)
        if len(xs) < 2:
            continue
        groups = np.split(xs, np.flatnonzero(np.diff(xs) > 2) + 1)
        for group in groups:
            if len(group) < 2 or not 1 <= group[-1] - group[0] <= max(35, width // 25):
                continue
            strength = float(np.maximum(row[group] - background, 0).sum())
            if strength < max(50.0, threshold * 1.5):
                continue
            weights = np.maximum(
                row[group].astype(float) - background, 1.0
            ) ** 1.2
            candidates.append((
                float(np.average(group, weights=weights)), float(y)
            ))

    pixels = np.asarray(candidates, dtype=np.float64).reshape(-1, 2)
    if not len(pixels):
        return pixels

    radii = None
    if camera_geometry is not None:
        K, dist, normal, offset, origin, direction = camera_geometry
        undistorted = cv2.undistortPoints(
            pixels.reshape(-1, 1, 2), K, dist
        ).reshape(-1, 2)
        rays = np.column_stack((undistorted, np.ones(len(undistorted))))
        denominator = rays @ normal
        good = np.abs(denominator) > 1e-7
        depths = np.zeros(len(pixels), dtype=np.float64)
        depths[good] = -offset / denominator[good]
        good &= (depths > 10.0) & (depths < 2500.0)
        points = rays * depths[:, None]
        relative = points - origin
        axial = relative @ direction
        radial_vectors = relative - axial[:, None] * direction
        radii = np.linalg.norm(radial_vectors, axis=1)
        good &= (
            (radii <= radial_limit_mm) &
            (axial >= -150.0) & (axial <= 150.0)
        )
        pixels, radii = pixels[good], radii[good]
        if not len(pixels):
            return pixels

    # Trace neighboring subpixel peaks, keeping actual measured positions.
    # This rejects isolated red reflections without inventing missing points.
    tracks = []
    recent = []
    for index, (x, y) in enumerate(pixels):
        near = [
            (prev, track_id) for prev, track_id in recent
            if (0 < y - pixels[prev, 1] <= 3.0 and
                abs(x - pixels[prev, 0]) <= 16.0)
        ]
        if near:
            _, track_id = min(
                near, key=lambda item: abs(pixels[item[0], 0] - x)
            )
            tracks[track_id].append(index)
        else:
            track_id = len(tracks)
            tracks.append([index])
        recent = [
            (prev, tid) for prev, tid in recent
            if y - pixels[prev, 1] <= 3.0
        ]
        recent.append((index, track_id))

    min_length = max(6, height // 45)
    eligible = [
        np.asarray(track, dtype=np.int64)
        for track in tracks if len(track) >= min_length
    ]
    if not eligible:
        return np.empty((0, 2), dtype=np.float64)
    if radii is None:
        return pixels[max(eligible, key=len)]

    scored = [
        (len(track) / (float(np.median(radii[track])) + 10.0) ** 1.3, track)
        for track in eligible
    ]
    max_score = max(score for score, _ in scored)
    selected = [
        track for score, track in scored if score >= 0.25 * max_score
    ]
    return pixels[np.sort(np.concatenate(selected))]


def exclude_flat_support(pixels, points_camera, origin, direction):
    """Remove a visible flat platform trace without a hardcoded pixel ROI.

    The end of a vertical laser trace may continue across the turntable as a
    nearly constant axial height while its radius grows. Detect that shape;
    otherwise leave the input unchanged instead of guessing a base height.
    """
    if len(pixels) < 36 or len(pixels) != len(points_camera):
        return points_camera
    relative = points_camera - origin
    axial = relative @ direction
    radial = np.linalg.norm(
        relative - axial[:, None] * direction, axis=1
    )
    ordered = np.argsort(pixels[:, 1])
    size = max(12, len(ordered) // 15)
    tail, front = ordered[-size:], ordered[:size]
    plane = float(np.median(axial[tail]))
    first_height = float(np.median(axial[front]))
    if not (
        float(np.std(axial[tail])) < 0.6 and
        float(np.ptp(radial[tail])) >= 5.0 and
        abs(plane - first_height) >= 4.0
    ):
        return points_camera
    if first_height < plane:
        return points_camera[axial < plane - 1.5]
    return points_camera[axial > plane + 1.5]


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


def reconstruct_latest(progress_callback=None, radial_limit_mm=90.0, session_id=None):
    """Reconstruct one completed physical session (or the most recent one)."""
    camera, axis, laser = load_calibration()
    if session_id is None:
        folder, meta = find_recent_real_session()
    else:
        # Session identifiers originate from local session creation. Reject
        # unexpected path elements even when called from another Python module.
        if (not isinstance(session_id, str) or
                not session_id.startswith("scan_") or
                not session_id.replace("_", "").replace("-", "").isalnum()):
            raise ReconstructionError("Invalid scan session identifier.")
        folder = SCANS_DIR / session_id
        meta = _load_json(folder / "metadata.json")
        positions = meta.get("capture_positions") or []
        if (meta.get("status") != "finished" or len(positions) < 8 or
                not all(p.get("physical") is True for p in positions)):
            raise ReconstructionError(
                "Requested session is not a completed physical scan with enough photos."
            )
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
        pixels = stripe_pixels(
            frame, camera_geometry=(K, dist, normal, offset, origin, direction),
            radial_limit_mm=radial_limit_mm,
        )
        points_camera = project_laser_points(pixels, K, dist, normal, offset)
        points_camera = exclude_flat_support(
            pixels, points_camera, origin, direction
        )
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
    bins = np.rint(cloud / 0.4).astype(np.int64)
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
