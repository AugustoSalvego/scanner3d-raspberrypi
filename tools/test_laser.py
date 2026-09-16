"""Check laser power control.

    python -m tools.test_laser            # report how the laser is wired
    python -m tools.test_laser --blink 3  # only when a GPIO pin is configured

The reference build powers the laser externally and cannot switch it, so this
tool reports that plainly instead of pretending a pin exists.  Configure
``laser.control = "gpio"`` and ``laser.gpio_pin`` first if you wired a switching
transistor - and never drive a laser module straight from a GPIO pin.
"""

from __future__ import annotations

import argparse
import time

from scanner_core.config import LaserControlMode
from scanner_core.errors import LaserError
from scanner_core.hardware.laser import ExternalLaser, GpioLaser
from tools._common import add_common_arguments, bootstrap, fail, heading, show


def main() -> int:
    parser = argparse.ArgumentParser(description="Test the laser control.")

    parser.add_argument(
        "--blink", type=int, default=0, help="Switch the laser on/off this many times."
    )

    add_common_arguments(parser)

    arguments = parser.parse_args()
    config = bootstrap(arguments.verbose)

    heading("Laser configuration")
    show("control", config.laser.control.value)
    show("gpio pin", config.laser.gpio_pin if config.laser.gpio_pin is not None else "not wired")
    show("switch delay", f"{config.laser.switch_delay} s")

    if config.laser.control is not LaserControlMode.GPIO:
        laser = ExternalLaser(config.laser)
        laser.open()

        heading("Result")
        show("controllable", "no")

        print(
            "\n  This laser is powered externally, so the software cannot switch it.\n"
            "  That is a valid configuration - it just means laser-off frames and\n"
            "  differential detection are unavailable.\n\n"
            "  To enable them, wire the laser through a transistor or relay driven by\n"
            "  a GPIO pin, then set laser.control to 'gpio' and laser.gpio_pin in the\n"
            "  configuration. See docs/wiring.md."
        )

        laser.close()

        return 0

    laser = GpioLaser(config.laser)

    try:
        laser.open()
    except LaserError as error:
        return fail(str(error))

    try:
        heading("Switching")

        show("controllable", "yes")

        cycles = max(1, arguments.blink) if arguments.blink else 1

        for index in range(cycles):
            laser.set_enabled(True)
            show(f"cycle {index + 1}", "ON")

            time.sleep(0.4)

            laser.set_enabled(False)
            show(f"cycle {index + 1}", "OFF")

            time.sleep(0.4)

        print("\nLaser test finished.")

        return 0
    except KeyboardInterrupt:
        print("\nInterrupted.")

        return 130
    finally:
        try:
            laser.set_enabled(False)
        except LaserError:
            pass

        laser.close()

        print("Laser pin released.")


if __name__ == "__main__":
    raise SystemExit(main())
