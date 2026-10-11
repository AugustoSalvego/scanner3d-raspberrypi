"""Camera intrinsic calibration from checkerboard images.

Workflow implemented here:

1. read every image of the calibration set;
2. locate the inner corners with ``findChessboardCornersSB`` when the installed
   OpenCV provides it (it is markedly more robust on blurry webcam frames) and
   fall back to ``findChessboardCorners`` + ``cornerSubPix`` otherwise;
3. run ``calibrateCamera`` against the metric object points built from the
   square size, which is what ties the whole system to millimetres;
4. compute the per-view reprojection error so a single bad photograph can be
   spotted and removed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import cv2
import numpy as np

from scanner_core.calibration.store import (
    CALIBRATION_SCHEMA_VERSION,
    CameraCalibration,
)
from scanner_core.errors import CalibrationError

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff")


@dataclass(frozen=True)
class CheckerboardSpec:
    """Description of the physical calibration target.

    ``columns`` and ``rows`` count *inner* corners, not squares: a board with
    10x7 squares has 9x6 inner corners.
    """

    columns: int = 9
    rows: int = 6
    square_size_mm: float = 25.0

    def validate(self) -> "CheckerboardSpec":
        if self.columns < 3 or self.rows < 3:
            raise CalibrationError(
                "The checkerboard must have at least 3x3 inner corners."
            )

        if self.columns == self.rows:
            raise CalibrationError(
                "Use a checkerboard with a different number of rows and columns; "
                "a square board makes the corner order ambiguous."
            )

        if self.square_size_mm <= 0:
            raise CalibrationError("square_size_mm must be positive.")

        return self

    @property
    def size(self) -> tuple[int, int]:
        return (self.columns, self.rows)

    def object_points(self) -> np.ndarray:
        """Metric coordinates of the inner corners, ``Z = 0``."""

        grid = np.zeros((self.rows * self.columns, 3), dtype=np.float32)
        grid[:, :2] = np.mgrid[0 : self.columns, 0 : self.rows].T.reshape(-1, 2)

        return grid * float(self.square_size_mm)

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": self.columns,
            "rows": self.rows,
            "square_size_mm": self.square_size_mm,
        }


@dataclass
class CornerDetection:
    """Corners found in one calibration image."""

    path: Path
    corners: np.ndarray
    image_size: tuple[int, int]


def list_calibration_images(directory: Path) -> list[Path]:
    """Return every readable image in ``directory``, sorted by name."""

    if not directory.exists():
        raise CalibrationError(f"Calibration image folder not found: {directory}")

    images = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )

    if not images:
        raise CalibrationError(f"No calibration images found in {directory}.")

    return images


def normalize_corner_order(corners: np.ndarray) -> np.ndarray:
    """Make corner 0 the image-top-left end of the grid.

    A board with different row and column counts can still be reported in two
    orders, 180 degrees apart, and OpenCV's two detectors disagree: on the same
    upright board ``findChessboardCornersSB`` starts at the bottom-right corner
    and ``findChessboardCorners`` at the top-left.  The intrinsics do not care,
    but anything that follows corner 0 across images (turntable tracking, board
    poses) jumps to the opposite corner whenever the detector used changes.

    The board never turns upside down on the turntable, so "corner 0 is the end
    of the grid nearest the image top-left" is a stable physical choice.
    """

    corners = np.asarray(corners, dtype=np.float32).reshape(-1, 1, 2)

    first = corners[0, 0]
    last = corners[-1, 0]

    if float(first[0] + first[1]) > float(last[0] + last[1]):
        corners = corners[::-1].copy()

    return corners


def find_checkerboard(
    image: np.ndarray,
    spec: CheckerboardSpec,
) -> np.ndarray | None:
    """Locate the inner corners of the checkerboard, refined to sub-pixel.

    Returns ``None`` when the board is not visible in the image.  The corners
    always come back in the order described by :func:`normalize_corner_order`,
    whichever detector found them.
    """

    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image

    # ``SB`` (sector based) is far more tolerant of blur and uneven lighting.
    finder_sb = getattr(cv2, "findChessboardCornersSB", None)

    if finder_sb is not None:
        flags = cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY
        found, corners = finder_sb(gray, spec.size, flags=flags)

        if found:
            return normalize_corner_order(corners)

    found, corners = cv2.findChessboardCorners(
        gray,
        spec.size,
        flags=cv2.CALIB_CB_ADAPTIVE_THRESH
        | cv2.CALIB_CB_NORMALIZE_IMAGE
        | cv2.CALIB_CB_FAST_CHECK,
    )

    if not found:
        return None

    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)

    # The refinement window must stay inside one square.  A fixed 23x23 px
    # window swallowed neighbouring corners whenever the board was small or
    # seen at a steep angle, pulling corners up to 10 px off.
    grid = corners.reshape(spec.rows, spec.columns, 2)
    spacing = min(
        float(np.min(np.linalg.norm(np.diff(grid, axis=1), axis=2))),
        float(np.min(np.linalg.norm(np.diff(grid, axis=0), axis=2))),
    )
    half = int(np.clip(round(spacing * 0.4), 2, 11))

    return normalize_corner_order(
        cv2.cornerSubPix(gray, corners, (half, half), (-1, -1), criteria)
    )


def detect_corners_in_images(
    images: Iterable[Path],
    spec: CheckerboardSpec,
) -> tuple[list[CornerDetection], list[Path]]:
    """Run corner detection over a set of files.

    Returns ``(detections, failures)`` so the operator learns exactly which
    photographs need to be retaken.
    """

    spec.validate()

    detections: list[CornerDetection] = []
    failures: list[Path] = []

    for path in images:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)

        if image is None:
            failures.append(path)
            continue

        corners = find_checkerboard(image, spec)

        if corners is None:
            failures.append(path)
            continue

        height, width = image.shape[:2]

        detections.append(
            CornerDetection(path=path, corners=corners, image_size=(width, height))
        )

    return detections, failures


def calibrate_camera_from_detections(
    detections: Sequence[CornerDetection],
    spec: CheckerboardSpec,
    *,
    source: str = "checkerboard",
    notes: str = "",
) -> CameraCalibration:
    """Run ``cv2.calibrateCamera`` and build a :class:`CameraCalibration`."""

    if len(detections) < 5:
        raise CalibrationError(
            f"Camera calibration needs at least 5 usable views, got {len(detections)}. "
            "Photograph the board from several distances and tilts."
        )

    sizes = {detection.image_size for detection in detections}

    if len(sizes) != 1:
        raise CalibrationError(
            "All calibration images must have the same resolution; found "
            + ", ".join(f"{width}x{height}" for width, height in sorted(sizes))
        )

    image_size = detections[0].image_size

    object_points = [spec.object_points() for _ in detections]
    image_points = [detection.corners for detection in detections]

    rms, camera_matrix, distortion, rotations, translations = cv2.calibrateCamera(
        object_points,
        image_points,
        image_size,
        None,
        None,
    )

    per_view_errors: list[float] = []

    for index, detection in enumerate(detections):
        projected, _ = cv2.projectPoints(
            object_points[index],
            rotations[index],
            translations[index],
            camera_matrix,
            distortion,
        )

        error = cv2.norm(detection.corners, projected, cv2.NORM_L2) / len(projected)
        per_view_errors.append(float(error))

    profile = CameraCalibration(
        schema_version=CALIBRATION_SCHEMA_VERSION,
        source=source,
        notes=notes,
        camera_matrix=np.asarray(camera_matrix, dtype=np.float64),
        distortion_coefficients=np.asarray(distortion, dtype=np.float64).reshape(-1),
        image_size=image_size,
        rms_reprojection_error=float(rms),
        image_count=len(detections),
        checkerboard=spec.to_dict(),
        per_view_errors=per_view_errors,
    )

    profile.validate()

    return profile


def calibrate_camera_from_folder(
    directory: Path,
    spec: CheckerboardSpec,
    *,
    notes: str = "",
) -> tuple[CameraCalibration, list[Path]]:
    """Calibrate from every checkerboard image in ``directory``.

    Returns the profile together with the list of images where the board could
    not be found.
    """

    images = list_calibration_images(directory)
    detections, failures = detect_corners_in_images(images, spec)

    profile = calibrate_camera_from_detections(
        detections,
        spec,
        notes=notes or f"Calibrated from {directory.name} ({len(images)} images).",
    )

    return profile, failures


def estimate_board_pose(
    corners: np.ndarray,
    spec: CheckerboardSpec,
    calibration: CameraCalibration,
) -> tuple[np.ndarray, np.ndarray]:
    """Pose of the checkerboard in the camera frame.

    Returns ``(rotation_matrix, translation)`` such that
    ``X_camera = R @ X_board + t`` with ``t`` in millimetres.
    """

    if not calibration.valid:
        raise CalibrationError("A valid camera calibration is required to estimate poses.")

    success, rotation_vector, translation = cv2.solvePnP(
        spec.object_points(),
        np.asarray(corners, dtype=np.float32).reshape(-1, 1, 2),
        np.asarray(calibration.camera_matrix, dtype=np.float64),
        np.asarray(calibration.distortion_coefficients, dtype=np.float64).reshape(1, -1),
        flags=cv2.SOLVEPNP_ITERATIVE,
    )

    if not success:
        raise CalibrationError("solvePnP failed to recover the checkerboard pose.")

    rotation_matrix, _ = cv2.Rodrigues(rotation_vector)

    return np.asarray(rotation_matrix, dtype=np.float64), np.asarray(
        translation, dtype=np.float64
    ).reshape(3)


def undistort_image(image: np.ndarray, calibration: CameraCalibration) -> np.ndarray:
    """Remove lens distortion from a frame using a validated calibration."""

    if not calibration.valid:
        raise CalibrationError("Cannot undistort with an invalid camera calibration.")

    return cv2.undistort(
        image,
        np.asarray(calibration.camera_matrix, dtype=np.float64),
        np.asarray(calibration.distortion_coefficients, dtype=np.float64).reshape(1, -1),
    )
