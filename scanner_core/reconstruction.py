"""Turn laser images plus platform angles into a metric point cloud.

For every capture:

1. the laser stripe is detected with sub-pixel accuracy;
2. each stripe pixel is back-projected into a camera ray (lens distortion
   removed using the camera calibration);
3. the ray is intersected with the calibrated laser plane, giving a 3D point in
   camera coordinates;
4. the point is expressed in turntable coordinates and de-rotated by the angle
   the platform held during that capture, so every capture lands in one common
   object frame.

Nothing here invents data.  If the calibration is missing or invalid the whole
operation fails; if a frame yields no usable laser line it is counted as a
rejected frame and the reconstruction continues with the rest.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from scanner_core.calibration.store import CalibrationBundle
from scanner_core.config import FilterConfig, ReconstructionConfig
from scanner_core.errors import ReconstructionError
from scanner_core.geometry import (
    camera_to_turntable,
    derotate_about_z,
    intersect_rays_with_plane_detailed,
    pixels_to_camera_rays,
)
from scanner_core.laser_detection import LaserLineDetector
from scanner_core.point_cloud import PointCloud, filter_point_cloud


@dataclass
class CaptureInput:
    """One frame to reconstruct, with the platform angle it was taken at."""

    index: int
    angle_deg: float
    image: np.ndarray | None = None
    image_path: Path | None = None
    laser_off_image: np.ndarray | None = None
    laser_off_path: Path | None = None

    def load(self) -> tuple[np.ndarray, np.ndarray | None]:
        """Return ``(laser_on, laser_off)`` frames, reading from disk if needed."""

        image = self.image

        if image is None:
            if self.image_path is None:
                raise ReconstructionError(
                    f"Capture {self.index} has neither an image nor a path."
                )

            image = cv2.imread(str(self.image_path), cv2.IMREAD_COLOR)

            if image is None:
                raise ReconstructionError(
                    f"Capture image could not be read: {self.image_path.name}"
                )

        laser_off = self.laser_off_image

        if laser_off is None and self.laser_off_path is not None:
            laser_off = cv2.imread(str(self.laser_off_path), cv2.IMREAD_COLOR)

        return image, laser_off


@dataclass
class FrameResult:
    """Points recovered from a single capture."""

    index: int
    angle_deg: float
    points: np.ndarray
    confidence: np.ndarray
    colors: np.ndarray | None
    accepted: bool
    reason: str
    counts: dict[str, int] = field(default_factory=dict)


@dataclass
class ReconstructionResult:
    """Full outcome of a reconstruction run."""

    cloud: PointCloud
    statistics: dict[str, Any]
    frames: list[FrameResult] = field(default_factory=list)

    @property
    def point_count(self) -> int:
        return len(self.cloud)


class Reconstructor:
    """Stateless-ish triangulation engine built from a validated calibration."""

    def __init__(
        self,
        calibration: CalibrationBundle,
        detector: LaserLineDetector,
        reconstruction_config: ReconstructionConfig | None = None,
        filter_config: FilterConfig | None = None,
        *,
        allow_synthetic_calibration: bool = False,
    ) -> None:
        calibration.require_ready(allow_synthetic=allow_synthetic_calibration)

        self.calibration = calibration
        self.detector = detector
        self.config = reconstruction_config or ReconstructionConfig()
        self.config.validate()
        self.filter_config = filter_config or FilterConfig()
        self.filter_config.validate()

        # These three are guaranteed present and valid by require_ready().
        self._camera = calibration.camera
        self._laser_plane = calibration.laser.plane
        self._turntable = calibration.turntable

        self._camera_matrix = np.asarray(self._camera.camera_matrix, dtype=np.float64)
        self._distortion = np.asarray(
            self._camera.distortion_coefficients, dtype=np.float64
        ).reshape(-1)
        self._axis_origin = np.asarray(self._turntable.axis_origin, dtype=np.float64).reshape(3)
        self._axis_direction = np.asarray(
            self._turntable.axis_direction, dtype=np.float64
        ).reshape(3)

    # ------------------------------------------------------------------
    def reconstruct_frame(
        self,
        image: np.ndarray,
        angle_deg: float,
        *,
        index: int = 0,
        laser_off_image: np.ndarray | None = None,
        sample_colors: bool = True,
    ) -> FrameResult:
        """Triangulate one capture into object-frame points."""

        counts: dict[str, int] = {}

        expected = tuple(self._camera.image_size)
        actual = (image.shape[1], image.shape[0])

        if expected != actual:
            return FrameResult(
                index=index,
                angle_deg=angle_deg,
                points=np.zeros((0, 3)),
                confidence=np.zeros(0),
                colors=None,
                accepted=False,
                reason=(
                    f"Frame is {actual[0]}x{actual[1]} but the camera was calibrated at "
                    f"{expected[0]}x{expected[1]}. Recalibrate or restore the scan resolution."
                ),
                counts=counts,
            )

        detection = self.detector.detect(image, laser_off_image)

        counts["laser_pixels"] = detection.point_count

        if not detection.accepted:
            return FrameResult(
                index=index,
                angle_deg=angle_deg,
                points=np.zeros((0, 3)),
                confidence=np.zeros(0),
                colors=None,
                accepted=False,
                reason=detection.reason,
                counts=counts,
            )

        rays = pixels_to_camera_rays(
            detection.pixels,
            self._camera_matrix,
            self._distortion,
            undistort=self.config.undistort,
        )

        intersection = intersect_rays_with_plane_detailed(
            rays,
            self._laser_plane,
            min_incidence=self.config.min_incidence,
            min_depth_mm=self.config.min_depth_mm,
            max_depth_mm=self.config.max_depth_mm,
        )

        counts.update(intersection.counts())

        valid = intersection.valid

        if not np.any(valid):
            return FrameResult(
                index=index,
                angle_deg=angle_deg,
                points=np.zeros((0, 3)),
                confidence=np.zeros(0),
                colors=None,
                accepted=False,
                reason="No camera ray produced a usable intersection with the laser plane.",
                counts=counts,
            )

        points_camera = intersection.points[valid]
        confidence = detection.confidence[valid]
        pixels = detection.pixels[valid]

        local = camera_to_turntable(points_camera, self._axis_origin, self._axis_direction)

        radius = np.hypot(local[:, 0], local[:, 1])
        within_radius = radius <= self.config.max_radius_mm

        within_height = (local[:, 2] >= self.config.min_height_mm) & (
            local[:, 2] <= self.config.max_height_mm
        )

        keep = within_radius & within_height

        counts["rejected_out_of_radius"] = int(np.count_nonzero(~within_radius))
        counts["rejected_out_of_height"] = int(
            np.count_nonzero(within_radius & ~within_height)
        )

        if not np.any(keep):
            return FrameResult(
                index=index,
                angle_deg=angle_deg,
                points=np.zeros((0, 3)),
                confidence=np.zeros(0),
                colors=None,
                accepted=False,
                reason="Every triangulated point fell outside the scan volume.",
                counts=counts,
            )

        local = local[keep]
        confidence = confidence[keep]
        pixels = pixels[keep]

        angle_rad = float(np.radians(angle_deg))

        if self._turntable.rotation_direction < 0:
            angle_rad = -angle_rad

        points_object = derotate_about_z(local, angle_rad)

        colors = None

        if sample_colors:
            colors = self._sample_colors(image, pixels)

        counts["accepted_points"] = int(len(points_object))

        return FrameResult(
            index=index,
            angle_deg=angle_deg,
            points=points_object,
            confidence=confidence,
            colors=colors,
            accepted=True,
            reason="ok",
            counts=counts,
        )

    @staticmethod
    def _sample_colors(image: np.ndarray, pixels: np.ndarray) -> np.ndarray:
        """Pick the frame colour under each laser pixel (BGR -> RGB)."""

        height, width = image.shape[:2]

        columns = np.clip(np.round(pixels[:, 0]).astype(int), 0, width - 1)
        rows = np.clip(np.round(pixels[:, 1]).astype(int), 0, height - 1)

        bgr = image[rows, columns]

        return bgr[:, ::-1].astype(np.uint8)

    # ------------------------------------------------------------------
    def reconstruct(
        self,
        captures: Iterable[CaptureInput],
        *,
        progress: Any = None,
        should_cancel: Any = None,
        sample_colors: bool = True,
    ) -> ReconstructionResult:
        """Reconstruct a whole scan.

        Args:
            captures: Frames with their platform angles.
            progress: Optional ``callable(index, total_so_far)`` for the UI.
            should_cancel: Optional ``callable() -> bool`` checked between
                frames so a long reconstruction can be interrupted.
            sample_colors: Sample the frame colour for every point.
        """

        started = time.perf_counter()

        frames: list[FrameResult] = []

        point_blocks: list[np.ndarray] = []
        confidence_blocks: list[np.ndarray] = []
        color_blocks: list[np.ndarray] = []

        totals = {
            "frames_total": 0,
            "frames_accepted": 0,
            "frames_rejected": 0,
            "laser_pixels": 0,
            "accepted_points": 0,
            "rejected_near_parallel": 0,
            "rejected_behind_camera": 0,
            "rejected_out_of_depth": 0,
            "rejected_out_of_radius": 0,
            "rejected_out_of_height": 0,
        }

        cancelled = False

        for capture in captures:
            if should_cancel is not None and should_cancel():
                cancelled = True
                break

            totals["frames_total"] += 1

            image, laser_off = capture.load()

            result = self.reconstruct_frame(
                image,
                capture.angle_deg,
                index=capture.index,
                laser_off_image=laser_off,
                sample_colors=sample_colors,
            )

            frames.append(result)

            for key, value in result.counts.items():
                if key in totals:
                    totals[key] += value

            if result.accepted:
                totals["frames_accepted"] += 1

                point_blocks.append(result.points)
                confidence_blocks.append(result.confidence)

                if result.colors is not None:
                    color_blocks.append(result.colors)
            else:
                totals["frames_rejected"] += 1

            if progress is not None:
                progress(capture.index, totals["accepted_points"])

        if not point_blocks:
            raise ReconstructionError(
                "No frame produced a usable laser line. Check the laser alignment, "
                "the detector thresholds and the calibration, then scan again."
            )

        points = np.vstack(point_blocks)
        confidence = np.concatenate(confidence_blocks)

        colors = (
            np.vstack(color_blocks)
            if color_blocks and len(color_blocks) == len(point_blocks)
            else None
        )

        raw_cloud = PointCloud(points=points, colors=colors, confidence=confidence)

        filtered, filter_statistics = filter_point_cloud(raw_cloud, self.filter_config)

        if filtered.is_empty:
            raise ReconstructionError(
                "Filtering removed every point. Loosen filtering.min_confidence or "
                "filtering.voxel_size_mm and reconstruct again."
            )

        statistics: dict[str, Any] = {
            **totals,
            **filter_statistics,
            "cancelled": cancelled,
            "reconstruction_seconds": round(time.perf_counter() - started, 3),
            "cloud": filtered.summary(),
            "calibration": {
                "camera_source": self._camera.source,
                "camera_rms_px": self._camera.rms_reprojection_error,
                "laser_source": self.calibration.laser.source,
                "laser_rms_mm": self.calibration.laser.rms_mm,
                "turntable_method": self._turntable.method,
                "steps_per_revolution": self._turntable.steps_per_revolution,
            },
        }

        return ReconstructionResult(cloud=filtered, statistics=statistics, frames=frames)


def captures_from_session_metadata(
    session_path: Path,
    metadata: dict[str, Any],
) -> Iterator[CaptureInput]:
    """Build reconstruction inputs from a stored scan session.

    The angles come from the recorded metadata, never from the number of
    images: a scan that was cancelled half way through must not be stretched
    into a full revolution.
    """

    captures = metadata.get("captures") or []

    if not captures:
        raise ReconstructionError("This session has no recorded captures.")

    captures_dir = session_path / "captures"

    for entry in captures:
        if not isinstance(entry, dict):
            raise ReconstructionError(
                "This session was recorded by an older version without capture "
                "angles, so it cannot be reconstructed. Run a new scan."
            )

        filename = entry.get("filename")
        angle = entry.get("angle_deg")

        if filename is None or angle is None:
            raise ReconstructionError(
                "A capture entry is missing its filename or angle; the session "
                "metadata is incomplete."
            )

        laser_off_name = entry.get("laser_off_filename")

        yield CaptureInput(
            index=int(entry.get("index", 0)),
            angle_deg=float(angle),
            image_path=captures_dir / str(filename),
            laser_off_path=(
                captures_dir / str(laser_off_name) if laser_off_name else None
            ),
        )
