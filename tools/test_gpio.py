"""Blink the four motor pins one at a time, to verify the wiring.

    python -m tools.test_gpio
    python -m tools.test_gpio --pins 17,27,22,23 --cycles 3

Watch the four LEDs on the ULN2003 board: they should light in the order the
pins are listed.  If they light out of order, the jumper wires are crossed - fix
that here, before trying to drive the motor.

Unlike the old version this terminates on its own and always switches every pin
off, instead of looping forever and leaving the last coil energised.
"""

from __future__ import annotations

import argparse
import time

from tools._common import add_common_arguments, bootstrap, fail, heading, show


def main() -> int:
    parser = argparse.ArgumentParser(description="Blink the motor GPIO pins.")

    parser.add_argument("--pins", default=None, help="Comma separated BCM pins.")
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument("--delay", type=float, default=0.4)

    add_common_arguments(parser)

    arguments = parser.parse_args()
    config = bootstrap(arguments.verbose)

    pins = (
        [int(value) for value in arguments.pins.split(",")]
        if arguments.pins
        else list(config.motor.pins)
    )

    heading("GPIO test")
    show("pins (BCM)", pins)
    show("cycles", arguments.cycles)

    try:
        from gpiozero import OutputDevice
    except ImportError:
        return fail(
            "gpiozero is not installed. On the Raspberry Pi: "
            "sudo apt install python3-gpiozero"
        )

    devices = []

    try:
        devices = [OutputDevice(pin) for pin in pins]
    except Exception as error:  # noqa: BLE001 - gpiozero raises many types
        for device in devices:
            device.close()

        return fail(f"Could not claim the pins: {error}")

    try:
        print()

        for cycle in range(max(1, arguments.cycles)):
            for index, device in enumerate(devices):
                device.on()
                print(f"  cycle {cycle + 1}: BCM {pins[index]} ON")

                time.sleep(arguments.delay)

                device.off()

        print("\nGPIO test finished.")

        return 0
    except KeyboardInterrupt:
        print("\nInterrupted.")

        return 130
    finally:
        for device in devices:
            try:
                device.off()
                device.close()
            except Exception:  # noqa: BLE001 - best effort clean-up
                pass

        print("All pins switched off and released.")


if __name__ == "__main__":
    raise SystemExit(main())
