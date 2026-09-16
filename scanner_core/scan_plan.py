"""Planning of the motor positions visited during one scan.

Two mistakes are easy to make here and both ruin a reconstruction:

* accumulating rounding error by moving ``steps_per_revolution / N`` steps
  ``N`` times, which leaves the platform short of (or past) a full turn;
* capturing at both 0 deg and 360 deg, which duplicates one slice of the object.

Both are avoided by computing *absolute* targets with integer arithmetic and by
only visiting indices ``0 .. N-1``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CapturePlanEntry:
    """One planned stop of the turntable."""

    index: int
    #: Absolute motor position, in motor steps from the scan origin.
    target_step: int
    #: Platform angle derived from ``target_step``, in degrees.
    angle_deg: float
    #: Steps to move from the previous stop (0 for the first one).
    delta_steps: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "target_step": self.target_step,
            "angle_deg": self.angle_deg,
            "delta_steps": self.delta_steps,
        }


def plan_capture_positions(
    capture_count: int,
    steps_per_revolution: int,
) -> list[CapturePlanEntry]:
    """Spread ``capture_count`` captures over exactly one revolution.

    Target ``i`` is ``floor(i * steps_per_revolution / capture_count)``, so the
    error against the ideal angle never exceeds one motor step and never
    accumulates.  The angle stored for every capture is the one implied by the
    integer target actually commanded, not the ideal fraction, so the metadata
    describes where the platform really was.

    Raises:
        ValueError: If the arguments are not positive, or if there are more
            captures than motor steps in a revolution.
    """

    if capture_count < 2:
        raise ValueError("capture_count must be at least 2.")

    if steps_per_revolution < 1:
        raise ValueError("steps_per_revolution must be positive.")

    if capture_count > steps_per_revolution:
        raise ValueError(
            "capture_count cannot exceed steps_per_revolution "
            f"({capture_count} > {steps_per_revolution}): the motor cannot "
            "resolve that many distinct positions."
        )

    entries: list[CapturePlanEntry] = []
    previous_target = 0

    for index in range(capture_count):
        target = (index * steps_per_revolution) // capture_count
        angle = target / steps_per_revolution * 360.0

        entries.append(
            CapturePlanEntry(
                index=index,
                target_step=target,
                angle_deg=angle,
                delta_steps=target - previous_target,
            )
        )

        previous_target = target

    return entries


def closing_steps(plan: list[CapturePlanEntry], steps_per_revolution: int) -> int:
    """Steps still needed to complete the revolution after the last capture.

    Used to return the platform to its starting orientation once the scan ends.
    """

    if not plan:
        return 0

    return steps_per_revolution - plan[-1].target_step
