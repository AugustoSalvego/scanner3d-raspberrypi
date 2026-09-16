"""Laser plane calibration.

The laser sheet is recovered from images of a checkerboard with the laser line
falling across it:

1. ``solvePnP`` on the detected corners gives the *board* plane in camera
   coordinates for that photograph;
2. every detected laser pixel is back-projected into a camera ray;
3. each ray is intersected with the known board plane, producing a 3D point
   that provably lies on the laser sheet;
4. points from several board poses are pooled and a plane is fitted by SVD.

Using at least two clearly different board poses is essential.  With a single
pose every sample lies on one straight line, the fit is under-determined, and
any normal perpendicular to that line would score a perfect residual.  The
``planarity_ratio`` returned by the fit detects exactly that situation and the
calibration is rejected instead of being silently wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np

from scanner_core.calibration.camera import (
    CheckerboardSpec,
    estimate_board_pose,
    find_checkerboard,
    list_calibration_images,
)
from scanner_core.calibration.store import (
    CALIBRATION_SCHEMA_VERSION,
    MIN_LASER_PLANARITY_RATIO,
    CameraCalibration,
    LaserPlaneCalibration,
)
from scanner_core.errors import CalibrationError
from scanner_core.geometry import (
    fit_plane,
    intersect_rays_with_plane,
    pixels_to_camera_rays,
    plane_from_pose,
)
from scanner_core.laser_detection import LaserLineDetector


@dataclass
class LaserPoseSample:
    """Laser points recovered from one checkerboard photograph."""

    path: Path
    points_camera: np.ndarray
    board_distance_mm: float
    laser_pixel_count: int
    rejected_pixel_count: int


@dataclass
class LaserCalibrationReport:
    """Everything the operator needs to judge a laser calibration attempt."""

    profile: LaserPlaneCalibration | None
    samples: list[LaserPoseSample] = field(default_factory=list)
    failures: list[tuple[Path, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": None if self.profile is None else self.profile.to_dict(),
            "samples": [
                {
                    "image": sample.path.name,
                    "board_distance_mm": round(sample.board_distance_mm, 2),
                    "laser_points": sample.laser_pixel_count,
                    "rejected_points": sample.rejected_pixel_count,
                }
                for sample in self.samples
            ],
            "failures": [{"image": path.name, "reason": reason} for path, reason in self.failures],
        }


def collect_laser_points_on_board(
    image: np.ndarray,
    spec: CheckerboardSpec,
    camera: CameraCalibration,
    detector: LaserLineDetector,
    *,
    board_margin_mm: float = 5.0,
) -> tuple[np.ndarray, float, int]:
    """Back-project the laser line onto the checkerboard plane of one image.

    Only laser pixels that land *inside* the printed board area are kept, so a
    stripe continuing onto the table behind the target cannot corrupt the fit.

    Returns:
        ``(points_camera, board_distance_mm, rejected_count)``.
    """

    corners = find_checkerboard(image, spec)

    if corners is None:
        raise CalibrationError("Checkerboard not found in the image.")

    rotation, translation = estimate_board_pose(corners, spec, camera)
    board_plane = plane_from_pose(rotation, translation)

    detection = detector.detect(image)

    if not detection.accepted:
        raise CalibrationError(f"Laser line not detected: {detection.reason}")

    rays = pixels_to_camera_rays(
        detection.pixels,
        camera.camera_matrix,
        camera.distortion_coefficients,
        undistort=True,
    )

    points, valid = intersect_rays_with_plane(
        rays,
        board_plane,
        min_incidence=0.02,
        min_depth_mm=1.0,
        max_depth_mm=100_000.0,
    )

    points = points[valid]
    rejected = int(np.count_nonzero(~valid))

    if len(points) == 0:
        raise CalibrationError("No laser ray intersected the checkerboard plane.")

    # Express the points in board coordinates to clip them to the printed area.
    board_points = (points - translation) @ rotation

    width_mm = (spec.columns - 1) * spec.square_size_mm
    height_mm = (spec.rows - 1) * spec.square_size_mm

    inside = (
        (board_points[:, 0] >= -board_margin_mm)
        & (board_points[:, 0] <= width_mm + board_margin_mm)
        & (board_points[:, 1] >= -board_margin_mm)
        & (board_points[:, 1] <= height_mm + board_margin_mm)
    )

    rejected += int(np.count_nonzero(~inside))
    points = points[inside]

    if len(points) == 0:
        raise CalibrationError(
            "Every laser point fell outside the checkerboard area. Aim the laser "
            "across the printed pattern."
        )

    return points, float(np.linalg.norm(translation)), rejected


def fit_laser_plane(
    samples: Sequence[LaserPoseSample],
    camera: CameraCalibration,
    *,
    source: str = "checkerboard",
    notes: str = "",
) -> LaserPlaneCalibration:
    """Fit the laser plane to points pooled from several board poses."""

    if len(samples) < 2:
        raise CalibrationError(
            "At least 2 checkerboard poses are required. With a single pose the "
            "laser points are collinear and the plane cannot be determined."
        )

    all_points = np.vstack([sample.points_camera for sample in samples])

    if len(all_points) < 3:
        raise CalibrationError("Not enough laser points to fit a plane.")

    result = fit_plane(all_points)

    if result.planarity_ratio < MIN_LASER_PLANARITY_RATIO:
        raise CalibrationError(
            "The pooled laser points are almost collinear "
            f"(planarity ratio {result.planarity_ratio:.4f}). Move the calibration "
            "board to clearly different distances and tilts and capture again."
        )

    plane = result.plane

    # Keep the normal pointing towards the camera so the sign of the offset is
    # predictable when a human reads the JSON.
    if plane.offset > 0:
        plane = type(plane)(normal=-plane.normal, offset=-plane.offset)

    profile = LaserPlaneCalibration(
        schema_version=CALIBRATION_SCHEMA_VERSION,
        source=source,
        notes=notes,
        plane_normal=plane.normal,
        plane_offset=plane.offset,
        point_count=result.point_count,
        pose_count=len(samples),
        rms_mm=result.rms_mm,
        max_residual_mm=result.max_abs_residual_mm,
        planarity_ratio=result.planarity_ratio,
        camera_reference={
            "created_at": camera.created_at,
            "source": camera.source,
            "image_size": list(camera.image_size),
            "rms_reprojection_error": camera.rms_reprojection_error,
        },
    )

    profile.validate()

    return profile


def calibrate_laser_plane_from_folder(
    directory: Path,
    spec: CheckerboardSpec,
    camera: CameraCalibration,
    detector: LaserLineDetector,
    *,
    notes: str = "",
) -> LaserCalibrationReport:
    """Calibrate the laser plane from every image in ``directory``.

    Each image must show the checkerboard *and* the laser line crossing it.
    """

    if not camera.valid:
        raise CalibrationError(
            "Camera calibration must be valid before the laser plane can be fitted."
        )

    spec.validate()

    images = list_calibration_images(directory)

    samples: list[LaserPoseSample] = []
    failures: list[tuple[Path, str]] = []

    for path in images:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)

        if image is None:
            failures.append((path, "Image could not be read."))
            continue

        try:
            points, distance, rejected = collect_laser_points_on_board(
                image, spec, camera, detector
            )
        except CalibrationError as error:
            failures.append((path, str(error)))
            continue

        samples.append(
            LaserPoseSample(
                path=path,
                points_camera=points,
                board_distance_mm=distance,
                laser_pixel_count=len(points),
                rejected_pixel_count=rejected,
            )
        )

    if len(samples) < 2:
        return LaserCalibrationReport(profile=None, samples=samples, failures=failures)

    profile = fit_laser_plane(
        samples,
        camera,
        notes=notes or f"Fitted from {len(samples)} board poses in {directory.name}.",
    )

    return LaserCalibrationReport(profile=profile, samples=samples, failures=failures)
