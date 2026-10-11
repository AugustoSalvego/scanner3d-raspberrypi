"""The real 28BYJ-48 driver, exercised with a fake ``gpiozero``.

No Raspberry Pi is needed: a stand-in ``gpiozero.OutputDevice`` records every
coil state, so the tests see exactly what would be sent to the ULN2003 board.
"""

from __future__ import annotations

import sys
import types

import pytest

from scanner_core.config import MotorConfig
from scanner_core.hardware.motor import HALF_STEP_SEQUENCE, HalfStepMotor28BYJ48
from scanner_core.scan_plan import plan_capture_positions


class _FakeOutputDevice:
    def __init__(self, pin, **_kwargs):
        self.pin = pin
        self.value = 0

    def on(self):
        self.value = 1

    def off(self):
        self.value = 0

    def close(self):
        pass


@pytest.fixture
def motor_factory(monkeypatch):
    fake = types.ModuleType("gpiozero")
    fake.OutputDevice = _FakeOutputDevice
    monkeypatch.setitem(sys.modules, "gpiozero", fake)

    created = []

    def build(direction: int) -> HalfStepMotor28BYJ48:
        config = MotorConfig(direction=direction, step_delay=0.0005, settle_after_release=0.0)
        config.validate()

        motor = HalfStepMotor28BYJ48(config)

        phases: list[tuple[int, ...]] = []
        original = motor._apply_phase

        def recording(phase):
            phases.append(tuple(phase))
            original(phase)

        motor._apply_phase = recording  # type: ignore[method-assign]
        motor.phases = phases  # type: ignore[attr-defined]
        motor.open()
        created.append(motor)

        return motor

    yield build

    for motor in created:
        motor.close()


class TestHalfStepDriver:
    @pytest.mark.parametrize("direction", [1, -1])
    def test_scan_plan_moves_one_slice_at_a_time(self, motor_factory, direction):
        """The bug this guards: with direction -1 each move doubled (68, 204, 476...)."""

        motor = motor_factory(direction)

        previous = 0

        for entry in plan_capture_positions(60, 4096)[:8]:
            motor.phases.clear()
            motor.move_to(entry.target_step)

            assert motor.position == entry.target_step
            assert len(motor.phases) == entry.target_step - previous

            previous = entry.target_step

    def test_inverted_wiring_runs_the_coil_sequence_backwards(self, motor_factory):
        forward = motor_factory(1)
        backward = motor_factory(-1)

        forward.move_relative(8)
        backward.move_relative(8)

        assert forward.phases != backward.phases
        assert set(forward.phases) == set(HALF_STEP_SEQUENCE)
        # Reversed order of the same 8-phase cycle.
        assert backward.phases[0] == HALF_STEP_SEQUENCE[-1]
        assert forward.phases[0] == HALF_STEP_SEQUENCE[1]

    @pytest.mark.parametrize("direction", [1, -1])
    def test_returning_home_retraces_the_same_number_of_steps(self, motor_factory, direction):
        motor = motor_factory(direction)

        motor.move_to(512)
        motor.phases.clear()
        motor.move_to(0)

        assert motor.position == 0
        assert len(motor.phases) == 512
