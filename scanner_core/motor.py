"""28BYJ-48 motor driver (BCM GPIO) for the physical scanner.

One motor step in this module is ONE half-step phase, not a full eight-phase cycle.
The true turntable steps/revolution must be measured before metric reconstruction.
"""
from __future__ import annotations

import time

from scanner_core.logger import add_log
from scanner_core.state import scanner_state

BCM_PINS = (17, 27, 22, 23)
HALF_STEP_SEQUENCE = (
    (1, 0, 0, 0), (1, 1, 0, 0), (0, 1, 0, 0), (0, 1, 1, 0),
    (0, 0, 1, 0), (0, 0, 1, 1), (0, 0, 0, 1), (1, 0, 0, 1),
)


class MotorInterrupted(Exception):
    """A user requested stop during turntable movement."""


class TurntableMotor:
    def __init__(self, pins=BCM_PINS, phase_delay=0.003):
        if len(pins) != 4 or len(set(pins)) != 4:
            raise ValueError("Four distinct BCM GPIO pins are required.")
        if not 0.001 <= float(phase_delay) <= 0.05:
            raise ValueError("phase_delay must be between 0.001 and 0.05 seconds.")
        self.pin_numbers = tuple(pins)
        self.phase_delay = float(phase_delay)
        self.pins = []
        self.phase = 0
        self.position = 0

    def __enter__(self):
        try:
            from gpiozero import OutputDevice
        except ImportError as error:
            raise RuntimeError("gpiozero is required for real motor control.") from error
        try:
            self.pins = [OutputDevice(pin, initial_value=False) for pin in self.pin_numbers]
        except Exception:
            self.close()
            raise
        add_log(f"Physical 28BYJ-48 motor enabled on BCM {self.pin_numbers}")
        return self

    def move(self, half_steps, cancelled=None):
        steps = int(half_steps)
        direction = 1 if steps >= 0 else -1
        for _ in range(abs(steps)):
            if cancelled is not None and cancelled():
                raise MotorInterrupted("Motor movement interrupted by Stop Scan.")
            self.phase = (self.phase + direction) % len(HALF_STEP_SEQUENCE)
            for pin, enabled in zip(self.pins, HALF_STEP_SEQUENCE[self.phase]):
                if enabled:
                    pin.on()
                else:
                    pin.off()
            self.position += direction
            scanner_state["motor_position"] = self.position
            time.sleep(self.phase_delay)
        self.release()

    def release(self):
        for pin in self.pins:
            pin.off()

    def close(self):
        try:
            self.release()
        finally:
            for pin in self.pins:
                pin.close()
            self.pins = []

    def __exit__(self, exc_type, exc, tb):
        self.close()


def rotate_step():
    """Legacy simulation-only helper; never energizes the real motor."""
    scanner_state["motor_position"] += 1


def reset():
    scanner_state["motor_position"] = 0
    add_log("Motor position reset (software only; no homing sensor)")
