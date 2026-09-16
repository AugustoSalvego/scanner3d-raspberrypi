"""Turntable motor drivers.

``SimulatedMotor``
    Tracks a position and (in simulation mode) tells the synthetic rig which
    angle to render.  Touches no hardware whatsoever.

``HalfStepMotor28BYJ48``
    Drives a real 28BYJ-48 through a ULN2003 board with ``gpiozero``, using the
    8-phase half-step sequence that was validated in ``tools/stepper_test.py``.

Both share the same absolute-position bookkeeping, so the scan pipeline can
command ``move_to(target)`` and never accumulate rounding error.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from scanner_core import logger
from scanner_core.config import MotorConfig
from scanner_core.errors import MotorError, ScanCancelled
from scanner_core.hardware.base import MotorDriver

#: 8-phase half-step sequence for a ULN2003 driven 28BYJ-48.  Half stepping
#: doubles the resolution and is noticeably smoother than full stepping, which
#: matters because the platform must settle quickly between captures.
HALF_STEP_SEQUENCE: tuple[tuple[int, int, int, int], ...] = (
    (1, 0, 0, 0),
    (1, 1, 0, 0),
    (0, 1, 0, 0),
    (0, 1, 1, 0),
    (0, 0, 1, 0),
    (0, 0, 1, 1),
    (0, 0, 0, 1),
    (1, 0, 0, 1),
)


class _BaseMotor(MotorDriver):
    """Shared position bookkeeping and cancellation handling."""

    def __init__(self, config: MotorConfig) -> None:
        config.validate()

        self.config = config
        self._position = 0
        self._lock = threading.RLock()
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def position(self) -> int:
        with self._lock:
            return self._position

    def reset_position(self) -> None:
        with self._lock:
            self._position = 0

    def angle_for_position(self, position: int | None = None) -> float:
        """Platform angle implied by an absolute motor position."""

        value = self.position if position is None else position

        return (value / self.config.steps_per_revolution) * 360.0

    def move_to(self, position: int, *, should_cancel: Callable[[], bool] | None = None) -> int:
        with self._lock:
            delta = int(position) - self._position

        return self.move_relative(delta, should_cancel=should_cancel)

    def move_relative(
        self, steps: int, *, should_cancel: Callable[[], bool] | None = None
    ) -> int:
        steps = int(steps)

        if steps == 0:
            return self.position

        if not self._open:
            raise MotorError("Motor is not open.")

        direction = 1 if steps > 0 else -1
        magnitude = abs(steps)

        self._drive(magnitude, direction, should_cancel)

        return self.position

    def _drive(
        self,
        magnitude: int,
        direction: int,
        should_cancel: Callable[[], bool] | None,
    ) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "angle_deg": round(self.angle_for_position(), 4),
            "steps_per_revolution": self.config.steps_per_revolution,
            "step_delay": self.config.step_delay,
            "direction": self.config.direction,
            "open": self.is_open,
        }


class SimulatedMotor(_BaseMotor):
    """A motor that exists only in software.

    ``on_position_changed`` lets the simulation rig follow the platform so the
    synthetic camera renders the correct angle.  Movement is not slept through
    at full speed: a scan of thousands of steps would otherwise take as long as
    the real thing for no benefit.
    """

    def __init__(
        self,
        config: MotorConfig,
        *,
        on_position_changed: Callable[[int, float], None] | None = None,
        simulate_timing: bool = False,
    ) -> None:
        super().__init__(config)

        self._on_position_changed = on_position_changed
        self._simulate_timing = simulate_timing
        self._released = True

    def open(self) -> None:
        self._open = True
        self._released = False

        logger.debug("Simulated motor ready (no GPIO is used in simulation mode).")

    def close(self) -> None:
        self._open = False
        self._released = True

    def release(self) -> None:
        self._released = True

    def _drive(
        self,
        magnitude: int,
        direction: int,
        should_cancel: Callable[[], bool] | None,
    ) -> None:
        effective = direction * self.config.direction

        # Check cancellation on a coarse grid; a simulated move is instant.
        chunk = max(1, magnitude // 20)
        moved = 0

        while moved < magnitude:
            if should_cancel is not None and should_cancel():
                raise ScanCancelled("Motor movement cancelled.")

            step = min(chunk, magnitude - moved)
            moved += step

            with self._lock:
                self._position += effective * step

            if self._simulate_timing:
                time.sleep(self.config.step_delay * step)

        self._released = False

        if self._on_position_changed is not None:
            self._on_position_changed(self.position, self.angle_for_position())

    def describe(self) -> dict[str, Any]:
        data = super().describe()
        data.update({"driver": "simulated", "coils_released": self._released})

        return data


class HalfStepMotor28BYJ48(_BaseMotor):
    """Real 28BYJ-48 stepper on a ULN2003 board, driven with ``gpiozero``.

    GPIO objects are created in :meth:`open`, never at import time, so this
    module can be imported on a development laptop without a Pi.
    """

    def __init__(self, config: MotorConfig) -> None:
        super().__init__(config)

        self._pins: list[Any] = []
        self._sequence_index = 0
        self._released = True

    def open(self) -> None:
        if self._open:
            return

        try:
            from gpiozero import OutputDevice
        except ImportError as error:  # pragma: no cover - depends on the host
            raise MotorError(
                "gpiozero is not installed, so the physical motor cannot be driven. "
                "Install it on the Raspberry Pi with 'sudo apt install python3-gpiozero'."
            ) from error

        try:
            self._pins = [OutputDevice(pin) for pin in self.config.pins]
        except Exception as error:  # noqa: BLE001 - gpiozero raises many types
            self._free_pins()

            raise MotorError(
                f"Could not claim GPIO pins {list(self.config.pins)}: {error}"
            ) from error

        self._open = True
        self._sequence_index = 0

        self.release()

        logger.info(
            f"28BYJ-48 motor ready on BCM pins {list(self.config.pins)} "
            f"({self.config.steps_per_revolution} half-steps per revolution)."
        )

    def close(self) -> None:
        try:
            if self._pins:
                self.release()
        except Exception:  # noqa: BLE001 - close must never raise
            pass

        self._free_pins()
        self._open = False

    def _free_pins(self) -> None:
        for pin in self._pins:
            try:
                pin.close()
            except Exception:  # noqa: BLE001 - best effort clean-up
                pass

        self._pins = []

    def release(self) -> None:
        """De-energise every coil.

        Leaving a coil energised makes a 28BYJ-48 draw current and warm up for
        no reason, and the holding torque is not needed once the platform has
        stopped.
        """

        with self._lock:
            for pin in self._pins:
                try:
                    pin.off()
                except Exception:  # noqa: BLE001 - best effort
                    pass

            self._released = True

    def _apply_phase(self, phase: tuple[int, int, int, int]) -> None:
        for pin, value in zip(self._pins, phase):
            if value:
                pin.on()
            else:
                pin.off()

    def _drive(
        self,
        magnitude: int,
        direction: int,
        should_cancel: Callable[[], bool] | None,
    ) -> None:
        if not self._pins:
            raise MotorError("Motor pins are not claimed; call open() first.")

        effective = direction * self.config.direction
        delay = self.config.step_delay
        sequence_length = len(HALF_STEP_SEQUENCE)

        try:
            for index in range(magnitude):
                # Checking every step keeps Stop Scan responsive without
                # measurably slowing a 2 ms per step motor.
                if should_cancel is not None and should_cancel():
                    raise ScanCancelled("Motor movement cancelled.")

                self._sequence_index = (self._sequence_index + effective) % sequence_length

                self._apply_phase(HALF_STEP_SEQUENCE[self._sequence_index])

                with self._lock:
                    self._position += effective

                self._released = False

                time.sleep(delay)
        except ScanCancelled:
            self.release()
            raise
        except Exception as error:  # noqa: BLE001 - surface as a domain error
            self.release()

            raise MotorError(f"Motor movement failed: {error}") from error
        finally:
            if self.config.release_after_move:
                self.release()

                if self.config.settle_after_release > 0:
                    time.sleep(self.config.settle_after_release)

    def describe(self) -> dict[str, Any]:
        data = super().describe()
        data.update(
            {
                "driver": "28byj48_uln2003",
                "pins": list(self.config.pins),
                "coils_released": self._released,
            }
        )

        return data
