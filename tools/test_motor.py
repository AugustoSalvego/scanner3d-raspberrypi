"""Drive the turntable motor a small, safe amount.

    python -m tools.test_motor                 # 64 steps forward, then back
    python -m tools.test_motor --steps 512
    python -m tools.test_motor --direction -1
    python -m tools.test_motor --revolution    # one full turn, to measure the gearing

Start small.  The first run should move a visible but harmless amount so the
wiring and the direction can be checked before anything is mounted on the
platform.  The coils are always de-energised in a ``finally`` block, including
on Ctrl-C.
"""

from __future__ import annotations

import argparse
import time

from scanner_core.config import MotorConfig
from scanner_core.errors import MotorError
from scanner_core.hardware.motor import HalfStepMotor28BYJ48, SimulatedMotor
from tools._common import add_common_arguments, bootstrap, fail, heading, show


def main() -> int:
    parser = argparse.ArgumentParser(description="Test the turntable stepper motor.")

    parser.add_argument("--steps", type=int, default=64, help="Half-steps to move.")
    parser.add_argument("--direction", type=int, choices=(1, -1), default=1)
    parser.add_argument("--delay", type=float, default=None, help="Seconds per half-step.")
    parser.add_argument(
        "--revolution",
        action="store_true",
        help="Move exactly steps_per_revolution, to check the real gear ratio.",
    )
    parser.add_argument(
        "--no-return", action="store_true", help="Do not move back to the start."
    )
    parser.add_argument(
        "--simulated",
        action="store_true",
        help="Exercise the simulated driver instead of GPIO.",
    )

    add_common_arguments(parser)

    arguments = parser.parse_args()
    config = bootstrap(arguments.verbose)

    motor_config = MotorConfig(**config.motor.__dict__)
    motor_config.direction = arguments.direction

    if arguments.delay is not None:
        motor_config.step_delay = arguments.delay

    motor_config.validate()

    steps = (
        motor_config.steps_per_revolution if arguments.revolution else max(1, arguments.steps)
    )

    heading("Motor configuration")
    show("driver", "simulated" if arguments.simulated else "28BYJ-48 / ULN2003")
    show("pins (BCM)", list(motor_config.pins))
    show("steps per revolution", motor_config.steps_per_revolution)
    show("step delay", f"{motor_config.step_delay} s")
    show("direction", motor_config.direction)
    show("movement", f"{steps} half-steps")
    show(
        "expected angle",
        f"{steps / motor_config.steps_per_revolution * 360.0:.2f} deg",
    )

    motor = (
        SimulatedMotor(motor_config, simulate_timing=False)
        if arguments.simulated
        else HalfStepMotor28BYJ48(motor_config)
    )

    try:
        motor.open()
    except MotorError as error:
        return fail(str(error))

    try:
        heading("Moving")

        started = time.perf_counter()
        motor.move_relative(steps)
        elapsed = time.perf_counter() - started

        show("position", motor.position)
        show("angle", f"{motor.angle_for_position():.2f} deg")
        show("elapsed", f"{elapsed:.2f} s")

        if arguments.revolution:
            print(
                "\n  Mark the platform before and after. If it did not land back on the\n"
                "  mark, steps_per_revolution is wrong for this unit - correct it in the\n"
                "  configuration, or run 'python -m tools.calibrate turntable' to measure it."
            )

        if not arguments.no_return:
            print("\n  Returning to the starting position...")

            motor.move_to(0)

            show("position", motor.position)

        print("\nMotor test finished.")

        return 0
    except KeyboardInterrupt:
        print("\nInterrupted.")

        return 130
    except MotorError as error:
        return fail(str(error))
    finally:
        # Always de-energise: a 28BYJ-48 left energised heats up for nothing.
        motor.release()
        motor.close()

        print("Motor coils released.")


if __name__ == "__main__":
    raise SystemExit(main())
