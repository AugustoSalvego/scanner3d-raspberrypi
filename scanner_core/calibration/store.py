"""Calibration data models, validation and persistence.

Three independent calibrations are needed before a physical scan can produce
metric 3D points:

``CameraCalibration``
    Intrinsic matrix and lens distortion, from a checkerboard.  Because the
    checkerboard square size is given in millimetres, this is also what fixes
    the *scale* of the whole system - there is no separate "scale factor" to
    guess afterwards.

``LaserPlaneCalibration``
    The plane of light in camera coordinates, ``n . X + d = 0``.

``TurntableCalibration``
    Where the rotation axis is, which way it turns, and how many motor steps
    make one revolution.

A profile is only ``valid`` when its *content* passes the checks in
``validate()``.  A file that merely exists, or that carries ``"calibrated":
true`` with empty numbers, is reported as invalid - a scan must never run on
invented geometry.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from scanner_core import paths
from scanner_core.errors import CalibrationError
from scanner_core.geometry import Plane

CALIBRATION_SCHEMA_VERSION = 2

CAMERA_FILE = "camera_calibration.json"
LASER_FILE = "laser_calibration.json"
TURNTABLE_FILE = "turntable_calibration.json"

#: A calibration produced by the synthetic simulation rig.  Perfectly valid for
#: simulation and for tests, never acceptable for a physical scan.
SOURCE_SYNTHETIC = "synthetic"

#: Quality gates.  Above these thresholds the calibration is kept but reported
#: as poor, so the dashboard can warn instead of silently producing a bad scan.
MAX_ACCEPTABLE_CAMERA_RMS_PX = 3.0
GOOD_CAMERA_RMS_PX = 1.0
MAX_ACCEPTABLE_LASER_RMS_MM = 2.0
GOOD_LASER_RMS_MM = 0.5
MIN_LASER_PLANARITY_RATIO = 0.02
MIN_LASER_POINTS = 200
MIN_LASER_POSES = 2


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _as_array(value: Any, shape: tuple[int, ...], name: str) -> np.ndarray:
    try:
        array = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise CalibrationError(f"{name} is not numeric.") from error

    if array.size != int(np.prod(shape)):
        raise CalibrationError(f"{name} must have {int(np.prod(shape))} values.")

    return array.reshape(shape)


@dataclass
class CalibrationProfile:
    """Common behaviour for the three calibration profiles."""

    schema_version: int = CALIBRATION_SCHEMA_VERSION
    created_at: str = field(default_factory=_now)
    source: str = "unknown"
    notes: str = ""
    issues: list[str] = field(default_factory=list)

    @property
    def is_synthetic(self) -> bool:
        return self.source == SOURCE_SYNTHETIC

    @property
    def valid(self) -> bool:
        return not self.issues

    def validate(self) -> list[str]:  # pragma: no cover - overridden
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:  # pragma: no cover - overridden
        raise NotImplementedError

    def _base_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "source": self.source,
            "notes": self.notes,
            "calibrated": self.valid,
            "issues": list(self.issues),
        }


@dataclass
class CameraCalibration(CalibrationProfile):
    """Pinhole intrinsics plus lens distortion."""

    camera_matrix: np.ndarray = field(default_factory=lambda: np.zeros((3, 3)))
    distortion_coefficients: np.ndarray = field(default_factory=lambda: np.zeros(5))
    image_size: tuple[int, int] = (0, 0)
    rms_reprojection_error: float = float("inf")
    image_count: int = 0
    checkerboard: dict[str, Any] = field(default_factory=dict)
    per_view_errors: list[float] = field(default_factory=list)

    def validate(self) -> list[str]:
        issues: list[str] = []

        matrix = np.asarray(self.camera_matrix, dtype=np.float64)

        if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
            issues.append("camera_matrix must be a finite 3x3 matrix.")
        else:
            fx, fy = matrix[0, 0], matrix[1, 1]
            cx, cy = matrix[0, 2], matrix[1, 2]

            if fx <= 0 or fy <= 0:
                issues.append("Focal lengths must be positive.")

            width, height = self.image_size

            if width <= 0 or height <= 0:
                issues.append("image_size must be positive.")
            elif not (0 < cx < width and 0 < cy < height):
                issues.append("Principal point falls outside the image.")

            if fx > 0 and fy > 0 and not 0.5 <= fx / fy <= 2.0:
                issues.append("Focal lengths differ by more than 2x; the calibration is suspect.")

        distortion = np.asarray(self.distortion_coefficients, dtype=np.float64).reshape(-1)

        if distortion.size not in (4, 5, 8, 12, 14) or not np.all(np.isfinite(distortion)):
            issues.append("distortion_coefficients must be a finite OpenCV distortion vector.")

        if not np.isfinite(self.rms_reprojection_error):
            issues.append("rms_reprojection_error is missing.")
        elif self.rms_reprojection_error > MAX_ACCEPTABLE_CAMERA_RMS_PX:
            issues.append(
                f"Reprojection error {self.rms_reprojection_error:.3f} px exceeds the "
                f"{MAX_ACCEPTABLE_CAMERA_RMS_PX} px limit; recapture the checkerboard set."
            )

        # A synthetic rig has exact intrinsics by construction, so the
        # "enough checkerboard views" rule simply does not apply to it.
        if not self.is_synthetic and self.image_count < 5:
            issues.append("At least 5 checkerboard views are required for a stable calibration.")

        self.issues = issues

        return issues

    @property
    def quality(self) -> str:
        if not self.valid:
            return "invalid"

        return "good" if self.rms_reprojection_error <= GOOD_CAMERA_RMS_PX else "acceptable"

    def to_dict(self) -> dict[str, Any]:
        data = self._base_dict()
        data.update(
            {
                "camera_matrix": np.asarray(self.camera_matrix, dtype=float).tolist(),
                "distortion_coefficients": np.asarray(
                    self.distortion_coefficients, dtype=float
                ).reshape(-1).tolist(),
                "image_size": [int(self.image_size[0]), int(self.image_size[1])],
                "rms_reprojection_error": float(self.rms_reprojection_error),
                "image_count": int(self.image_count),
                "checkerboard": dict(self.checkerboard),
                "per_view_errors": [float(value) for value in self.per_view_errors],
                "quality": self.quality,
            }
        )

        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CameraCalibration":
        profile = cls(
            schema_version=int(data.get("schema_version", 1)),
            created_at=str(data.get("created_at", "")),
            source=str(data.get("source", "unknown")),
            notes=str(data.get("notes", "")),
            camera_matrix=_as_array(data.get("camera_matrix", np.zeros((3, 3))), (3, 3), "camera_matrix"),
            distortion_coefficients=np.asarray(
                data.get("distortion_coefficients", np.zeros(5)), dtype=np.float64
            ).reshape(-1),
            image_size=tuple(int(value) for value in data.get("image_size", (0, 0)))[:2],  # type: ignore[assignment]
            rms_reprojection_error=float(data.get("rms_reprojection_error", float("inf"))),
            image_count=int(data.get("image_count", 0)),
            checkerboard=dict(data.get("checkerboard", {})),
            per_view_errors=[float(value) for value in data.get("per_view_errors", [])],
        )

        profile.validate()

        return profile

    def undistort_maps_available(self) -> bool:
        return self.valid and np.any(np.abs(self.distortion_coefficients) > 1e-9)


@dataclass
class LaserPlaneCalibration(CalibrationProfile):
    """The laser sheet expressed in camera coordinates."""

    plane_normal: np.ndarray = field(default_factory=lambda: np.zeros(3))
    plane_offset: float = 0.0
    point_count: int = 0
    pose_count: int = 0
    rms_mm: float = float("inf")
    max_residual_mm: float = float("inf")
    planarity_ratio: float = 0.0
    camera_reference: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> list[str]:
        issues: list[str] = []

        normal = np.asarray(self.plane_normal, dtype=np.float64).reshape(-1)

        if normal.size != 3 or not np.all(np.isfinite(normal)):
            issues.append("plane_normal must be 3 finite numbers.")
        elif abs(float(np.linalg.norm(normal)) - 1.0) > 1e-3:
            issues.append("plane_normal must be a unit vector.")

        if not np.isfinite(self.plane_offset) or abs(self.plane_offset) < 1e-9:
            issues.append("plane_offset is missing or places the plane through the camera centre.")

        # The synthetic plane is exact, so the sampling requirements that guard
        # a real, noisy fit do not apply to it.
        if not self.is_synthetic:
            if self.point_count < MIN_LASER_POINTS:
                issues.append(
                    f"Only {self.point_count} laser points were used; "
                    f"at least {MIN_LASER_POINTS} are required."
                )

            if self.pose_count < MIN_LASER_POSES:
                issues.append(
                    f"The laser plane needs at least {MIN_LASER_POSES} board poses, "
                    f"got {self.pose_count}."
                )

        if not np.isfinite(self.rms_mm):
            issues.append("rms_mm is missing.")
        elif self.rms_mm > MAX_ACCEPTABLE_LASER_RMS_MM:
            issues.append(
                f"Plane fit RMS {self.rms_mm:.3f} mm exceeds the "
                f"{MAX_ACCEPTABLE_LASER_RMS_MM} mm limit."
            )

        if self.planarity_ratio < MIN_LASER_PLANARITY_RATIO:
            issues.append(
                "The calibration points are almost collinear, so the plane is not "
                "determined. Capture the laser on board poses at clearly different "
                "distances and tilts."
            )

        if not self.camera_reference:
            issues.append("The laser plane must reference the camera calibration it was fitted with.")

        self.issues = issues

        return issues

    @property
    def quality(self) -> str:
        if not self.valid:
            return "invalid"

        return "good" if self.rms_mm <= GOOD_LASER_RMS_MM else "acceptable"

    @property
    def plane(self) -> Plane:
        if not self.valid:
            raise CalibrationError("Laser plane calibration is not valid.")

        return Plane(normal=np.asarray(self.plane_normal, dtype=np.float64), offset=self.plane_offset)

    def to_dict(self) -> dict[str, Any]:
        data = self._base_dict()
        data.update(
            {
                "plane_normal": np.asarray(self.plane_normal, dtype=float).reshape(-1).tolist(),
                "plane_offset": float(self.plane_offset),
                "point_count": int(self.point_count),
                "pose_count": int(self.pose_count),
                "rms_mm": float(self.rms_mm),
                "max_residual_mm": float(self.max_residual_mm),
                "planarity_ratio": float(self.planarity_ratio),
                "camera_reference": dict(self.camera_reference),
                "quality": self.quality,
            }
        )

        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LaserPlaneCalibration":
        profile = cls(
            schema_version=int(data.get("schema_version", 1)),
            created_at=str(data.get("created_at", "")),
            source=str(data.get("source", "unknown")),
            notes=str(data.get("notes", "")),
            plane_normal=np.asarray(data.get("plane_normal", np.zeros(3)), dtype=np.float64).reshape(-1),
            plane_offset=float(data.get("plane_offset", 0.0)),
            point_count=int(data.get("point_count", 0)),
            pose_count=int(data.get("pose_count", 0)),
            rms_mm=float(data.get("rms_mm", float("inf"))),
            max_residual_mm=float(data.get("max_residual_mm", float("inf"))),
            planarity_ratio=float(data.get("planarity_ratio", 0.0)),
            camera_reference=dict(data.get("camera_reference", {})),
        )

        profile.validate()

        return profile


@dataclass
class TurntableCalibration(CalibrationProfile):
    """Rotation axis, direction and motor resolution."""

    axis_origin: np.ndarray = field(default_factory=lambda: np.zeros(3))
    axis_direction: np.ndarray = field(default_factory=lambda: np.zeros(3))
    rotation_direction: int = 1
    steps_per_revolution: int = 0
    reference_angle_deg: float = 0.0
    radius_mm: float = 0.0
    rms_mm: float = float("inf")
    observation_count: int = 0
    method: str = "manual"

    def validate(self) -> list[str]:
        issues: list[str] = []

        origin = np.asarray(self.axis_origin, dtype=np.float64).reshape(-1)
        direction = np.asarray(self.axis_direction, dtype=np.float64).reshape(-1)

        if origin.size != 3 or not np.all(np.isfinite(origin)):
            issues.append("axis_origin must be 3 finite numbers.")
        elif float(np.linalg.norm(origin)) < 1e-6:
            issues.append(
                "axis_origin sits at the camera centre, which cannot be right; "
                "it must be measured in the camera frame, in millimetres."
            )

        if direction.size != 3 or not np.all(np.isfinite(direction)):
            issues.append("axis_direction must be 3 finite numbers.")
        elif abs(float(np.linalg.norm(direction)) - 1.0) > 1e-3:
            issues.append("axis_direction must be a unit vector.")

        if self.rotation_direction not in (1, -1):
            issues.append("rotation_direction must be +1 or -1.")

        if self.steps_per_revolution < 8:
            issues.append(
                "steps_per_revolution must be measured on the physical platform "
                "(the 28BYJ-48 gear ratio varies between units)."
            )

        if self.method not in {"manual", "target_tracking", SOURCE_SYNTHETIC}:
            issues.append(f"Unknown turntable calibration method: {self.method}.")

        if self.method == "target_tracking":
            if self.observation_count < 3:
                issues.append("Axis tracking needs at least 3 observations.")

            if not np.isfinite(self.rms_mm) or self.rms_mm > 2.0:
                issues.append("Axis fit RMS is missing or above 2 mm.")

        self.issues = issues

        return issues

    @property
    def quality(self) -> str:
        if not self.valid:
            return "invalid"

        if self.method == "manual":
            return "manual"

        return "good" if self.rms_mm <= 0.5 else "acceptable"

    def to_dict(self) -> dict[str, Any]:
        data = self._base_dict()
        data.update(
            {
                "axis_origin": np.asarray(self.axis_origin, dtype=float).reshape(-1).tolist(),
                "axis_direction": np.asarray(self.axis_direction, dtype=float).reshape(-1).tolist(),
                "rotation_direction": int(self.rotation_direction),
                "steps_per_revolution": int(self.steps_per_revolution),
                "reference_angle_deg": float(self.reference_angle_deg),
                "radius_mm": float(self.radius_mm),
                "rms_mm": float(self.rms_mm),
                "observation_count": int(self.observation_count),
                "method": self.method,
                "quality": self.quality,
            }
        )

        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TurntableCalibration":
        profile = cls(
            schema_version=int(data.get("schema_version", 1)),
            created_at=str(data.get("created_at", "")),
            source=str(data.get("source", "unknown")),
            notes=str(data.get("notes", "")),
            axis_origin=np.asarray(data.get("axis_origin", np.zeros(3)), dtype=np.float64).reshape(-1),
            axis_direction=np.asarray(
                data.get("axis_direction", np.zeros(3)), dtype=np.float64
            ).reshape(-1),
            rotation_direction=int(data.get("rotation_direction", 1)),
            steps_per_revolution=int(data.get("steps_per_revolution", 0)),
            reference_angle_deg=float(data.get("reference_angle_deg", 0.0)),
            radius_mm=float(data.get("radius_mm", 0.0)),
            rms_mm=float(data.get("rms_mm", float("inf"))),
            observation_count=int(data.get("observation_count", 0)),
            method=str(data.get("method", "manual")),
        )

        profile.validate()

        return profile


@dataclass
class CalibrationBundle:
    """The three calibrations together, plus a readiness verdict."""

    camera: CameraCalibration | None = None
    laser: LaserPlaneCalibration | None = None
    turntable: TurntableCalibration | None = None

    @property
    def has_synthetic_profile(self) -> bool:
        return any(
            profile is not None and profile.is_synthetic
            for profile in (self.camera, self.laser, self.turntable)
        )

    def blocking_issues(self, *, allow_synthetic: bool = False) -> list[str]:
        """Reasons a metric scan cannot run right now."""

        problems: list[str] = []

        for name, profile in (
            ("Camera", self.camera),
            ("Laser plane", self.laser),
            ("Turntable", self.turntable),
        ):
            if profile is None:
                problems.append(f"{name} calibration is missing.")
                continue

            if not profile.valid:
                problems.extend(f"{name}: {issue}" for issue in profile.issues)

            if profile.is_synthetic and not allow_synthetic:
                problems.append(
                    f"{name} calibration came from the simulation rig and cannot be "
                    "used for a physical scan."
                )

        return problems

    def is_ready(self, *, allow_synthetic: bool = False) -> bool:
        return not self.blocking_issues(allow_synthetic=allow_synthetic)

    def require_ready(self, *, allow_synthetic: bool = False) -> None:
        problems = self.blocking_issues(allow_synthetic=allow_synthetic)

        if problems:
            raise CalibrationError(
                "Calibration is not ready for reconstruction:\n- " + "\n- ".join(problems)
            )

    def to_status(self) -> dict[str, Any]:
        def describe(profile: CalibrationProfile | None) -> dict[str, Any]:
            if profile is None:
                return {"present": False, "valid": False, "issues": ["Not calibrated yet."]}

            payload = profile.to_dict()
            payload["present"] = True
            payload["valid"] = profile.valid

            return payload

        blocking = self.blocking_issues()

        return {
            "camera": describe(self.camera),
            "laser": describe(self.laser),
            "turntable": describe(self.turntable),
            "ready_for_physical_scan": not blocking,
            "blocking_issues": blocking,
            "uses_synthetic_profile": self.has_synthetic_profile,
        }


class CalibrationStore:
    """Reads and writes the three calibration files under ``outputs/calibration``."""

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory
        self._lock = threading.RLock()

    @property
    def directory(self) -> Path:
        return self._directory if self._directory is not None else paths.CALIBRATION_DIR

    def _path(self, filename: str) -> Path:
        return self.directory / filename

    def _load(self, filename: str, loader: Any) -> Any | None:
        path = self._path(filename)

        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        try:
            return loader(data)
        except (CalibrationError, TypeError, ValueError, KeyError):
            return None

    def _save(self, filename: str, profile: CalibrationProfile) -> Path:
        profile.validate()

        path = self._path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(profile.to_dict(), indent=2), encoding="utf-8")

        return path

    def load_camera(self) -> CameraCalibration | None:
        with self._lock:
            return self._load(CAMERA_FILE, CameraCalibration.from_dict)

    def load_laser(self) -> LaserPlaneCalibration | None:
        with self._lock:
            return self._load(LASER_FILE, LaserPlaneCalibration.from_dict)

    def load_turntable(self) -> TurntableCalibration | None:
        with self._lock:
            return self._load(TURNTABLE_FILE, TurntableCalibration.from_dict)

    def load_bundle(self) -> CalibrationBundle:
        with self._lock:
            return CalibrationBundle(
                camera=self.load_camera(),
                laser=self.load_laser(),
                turntable=self.load_turntable(),
            )

    def save_camera(self, profile: CameraCalibration) -> Path:
        with self._lock:
            return self._save(CAMERA_FILE, profile)

    def save_laser(self, profile: LaserPlaneCalibration) -> Path:
        with self._lock:
            return self._save(LASER_FILE, profile)

    def save_turntable(self, profile: TurntableCalibration) -> Path:
        with self._lock:
            return self._save(TURNTABLE_FILE, profile)


#: Process-wide calibration store.
calibration_store = CalibrationStore()
