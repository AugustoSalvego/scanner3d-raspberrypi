"""Turntable calibration: where the rotation axis is and how it turns.

Reconstruction cannot assume the axis sits in the middle of the image, so the
axis has to be established explicitly.  Two routes are supported.

``manual``
    The operator measures the geometry and types the numbers in.  Documented
    step by step in ``docs/calibration.md``; useful as a starting point and as a
    fallback when no calibration target is available.

``target_tracking``
    A checkerboard is placed on the platform and photographed at several known
    angles.  ``solvePnP`` gives the board origin in camera coordinates for each
    angle; as the platform turns, that origin traces a circle whose axis is the
    rotation axis.  Fitting plane + circle recovers both the direction and a
    point on the axis.

The tracking route also *measures* two things that are otherwise guesswork: the
rotation direction, and whether ``steps_per_revolution`` is correct.  If the
platform only turned 340 degrees while the software commanded 360, the fitted
angle scale is 0.944 and the tool reports the corrected step count instead of
letting every later scan be silently sheared.
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
)
from scanner_core.calibration.store import (
    CALIBRATION_SCHEMA_VERSION,
    CameraCalibration,
    TurntableCalibration,
)
from scanner_core.errors import CalibrationError
from scanner_core.geometry import build_axis_frame, fit_rotation_axis, normalize

#: The fitted angle scale must stay this close to 1.0, otherwise the configured
#: steps_per_revolution disagrees with the platform's real gear ratio.
ANGLE_SCALE_TOLERANCE = 0.05


@dataclass
class AxisObservation:
    """One tracked position of a fixed point on the rotating platform."""

    commanded_angle_deg: float
    point_camera: np.ndarray
    source: str = ""


@dataclass
class TurntableEstimate:
    """Result of fitting the rotation axis, before it becomes a profile."""

    axis_origin: np.ndarray
    axis_direction: np.ndarray
    rotation_direction: int
    radius_mm: float
    rms_mm: float
    observation_count: int
    angle_scale: float
    suggested_steps_per_revolution: int | None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis_origin": [float(value) for value in self.axis_origin],
            "axis_direction": [float(value) for value in self.axis_direction],
            "rotation_direction": self.rotation_direction,
            "radius_mm": round(self.radius_mm, 3),
            "rms_mm": round(self.rms_mm, 4),
            "observation_count": self.observation_count,
            "angle_scale": round(self.angle_scale, 5),
            "suggested_steps_per_revolution": self.suggested_steps_per_revolution,
            "warnings": list(self.warnings),
        }


def estimate_turntable_axis(
    observations: Sequence[AxisObservation],
    configured_steps_per_revolution: int,
) -> TurntableEstimate:
    """Fit the rotation axis from a point tracked across platform angles."""

    if len(observations) < 4:
        raise CalibrationError(
            "At least 4 observations are required to fit the rotation axis; "
            "6 to 12 spread over a full revolution works well."
        )

    points = np.vstack([np.asarray(obs.point_camera, dtype=np.float64).reshape(3) for obs in observations])
    commanded = np.array([float(obs.commanded_angle_deg) for obs in observations], dtype=np.float64)

    fit = fit_rotation_axis(points)

    warnings: list[str] = []

    if fit.radius_mm < 5.0:
        warnings.append(
            f"The tracked point is only {fit.radius_mm:.1f} mm from the axis. "
            "Place the target further from the centre for a better fit."
        )

    # Measure the realised rotation by looking at the polar angle of each
    # observation inside the fitted axis frame.
    frame = build_axis_frame(fit.direction)
    local = (points - fit.origin) @ frame.T

    measured = np.degrees(np.arctan2(local[:, 1], local[:, 0]))

    order = np.argsort(commanded)
    commanded_sorted = commanded[order]
    measured_sorted = np.unwrap(np.radians(measured[order]))
    measured_sorted = np.degrees(measured_sorted)

    # Least squares slope of measured versus commanded angle.
    design = np.column_stack([commanded_sorted, np.ones(len(commanded_sorted))])
    solution, *_ = np.linalg.lstsq(design, measured_sorted, rcond=None)
    slope = float(solution[0])

    if abs(slope) < 1e-6:
        raise CalibrationError(
            "The tracked point did not move with the platform. Check that the "
            "target is fixed to the turntable and that the motor really turned."
        )

    rotation_direction = 1 if slope > 0 else -1
    angle_scale = abs(slope)

    suggested: int | None = None

    if abs(angle_scale - 1.0) > ANGLE_SCALE_TOLERANCE:
        suggested = int(round(configured_steps_per_revolution / angle_scale))

        warnings.append(
            f"The platform turned {angle_scale:.3f} times the commanded angle. "
            f"steps_per_revolution is probably {suggested} instead of "
            f"{configured_steps_per_revolution}."
        )

    return TurntableEstimate(
        axis_origin=fit.origin,
        axis_direction=normalize(fit.direction),
        rotation_direction=rotation_direction,
        radius_mm=fit.radius_mm,
        rms_mm=fit.rms_mm,
        observation_count=len(observations),
        angle_scale=angle_scale,
        suggested_steps_per_revolution=suggested,
        warnings=warnings,
    )


def track_target_across_images(
    entries: Sequence[tuple[Path, float]],
    spec: CheckerboardSpec,
    camera: CameraCalibration,
) -> tuple[list[AxisObservation], list[tuple[Path, str]]]:
    """Locate the checkerboard origin in each ``(image, angle)`` pair."""

    if not camera.valid:
        raise CalibrationError("A valid camera calibration is required to track the target.")

    spec.validate()

    observations: list[AxisObservation] = []
    failures: list[tuple[Path, str]] = []

    for path, angle in entries:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)

        if image is None:
            failures.append((path, "Image could not be read."))
            continue

        corners = find_checkerboard(image, spec)

        if corners is None:
            failures.append((path, "Checkerboard not found."))
            continue

        try:
            _, translation = estimate_board_pose(corners, spec, camera)
        except CalibrationError as error:
            failures.append((path, str(error)))
            continue

        observations.append(
            AxisObservation(
                commanded_angle_deg=float(angle),
                point_camera=translation,
                source=path.name,
            )
        )

    return observations, failures


def build_turntable_profile(
    estimate: TurntableEstimate,
    steps_per_revolution: int,
    *,
    reference_angle_deg: float = 0.0,
    source: str = "target_tracking",
    notes: str = "",
) -> TurntableCalibration:
    """Turn an axis estimate into a persistable calibration profile."""

    profile = TurntableCalibration(
        schema_version=CALIBRATION_SCHEMA_VERSION,
        source=source,
        notes=notes or "; ".join(estimate.warnings),
        axis_origin=estimate.axis_origin,
        axis_direction=estimate.axis_direction,
        rotation_direction=estimate.rotation_direction,
        steps_per_revolution=int(steps_per_revolution),
        reference_angle_deg=float(reference_angle_deg),
        radius_mm=estimate.radius_mm,
        rms_mm=estimate.rms_mm,
        observation_count=estimate.observation_count,
        method="target_tracking",
    )

    profile.validate()

    return profile


def build_manual_turntable_profile(
    axis_origin: Sequence[float],
    axis_direction: Sequence[float],
    steps_per_revolution: int,
    *,
    rotation_direction: int = 1,
    reference_angle_deg: float = 0.0,
    notes: str = "",
) -> TurntableCalibration:
    """Build a profile from values the operator measured by hand.

    The direction is normalised for the caller, but nothing else is invented:
    an origin of ``(0, 0, 0)`` or a missing step count is reported as invalid.
    """

    direction = np.asarray(axis_direction, dtype=np.float64).reshape(-1)

    if direction.size == 3 and float(np.linalg.norm(direction)) > 1e-9:
        direction = normalize(direction)

    profile = TurntableCalibration(
        schema_version=CALIBRATION_SCHEMA_VERSION,
        source="manual",
        notes=notes or "Measured by hand; see docs/calibration.md.",
        axis_origin=np.asarray(axis_origin, dtype=np.float64).reshape(-1),
        axis_direction=direction,
        rotation_direction=int(rotation_direction),
        steps_per_revolution=int(steps_per_revolution),
        reference_angle_deg=float(reference_angle_deg),
        method="manual",
    )

    profile.validate()

    return profile
