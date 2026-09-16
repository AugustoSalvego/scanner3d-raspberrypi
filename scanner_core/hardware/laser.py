"""Laser power control.

The reference scanner powers its red line laser from an external supply and has
no switching hardware at all.  That is represented honestly by
:class:`ExternalLaser`, which reports ``controllable == False``; the software
never invents a GPIO pin for it.

If the operator does wire a switching transistor or relay, they set
``laser.control = "gpio"`` and ``laser.gpio_pin`` in the configuration and
:class:`GpioLaser` takes over, unlocking laser-on/laser-off differential
detection.

Never drive a laser module directly from a GPIO pin: a Raspberry Pi pin is
rated for a few milliamps and most laser modules need far more.  See
``docs/wiring.md``.
"""

from __future__ import annotations

import time
from typing import Any

from scanner_core import logger
from scanner_core.config import LaserConfig
from scanner_core.errors import LaserError
from scanner_core.hardware.base import LaserController


class ExternalLaser(LaserController):
    """A laser that is always powered and cannot be switched by software."""

    def __init__(self, config: LaserConfig) -> None:
        self.config = config
        self._open = False

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def controllable(self) -> bool:
        return False

    @property
    def is_on(self) -> bool:
        # The laser is externally powered, so it is assumed lit whenever the
        # rig is powered.  This is an assumption, not a measurement, and it is
        # reported as such in describe().
        return True

    def set_enabled(self, enabled: bool) -> bool:
        if not enabled:
            logger.warning(
                "Laser switching was requested but this laser is externally powered "
                "and cannot be switched. Laser-off frames are unavailable."
            )

        return True

    def describe(self) -> dict[str, Any]:
        return {
            "control": "always_on",
            "controllable": False,
            "state": "assumed on (externally powered)",
            "gpio_pin": None,
        }


class GpioLaser(LaserController):
    """A laser switched through a GPIO pin, via a transistor or relay."""

    def __init__(self, config: LaserConfig) -> None:
        if config.gpio_pin is None:
            raise LaserError("GpioLaser requires laser.gpio_pin to be configured.")

        self.config = config
        self._device: Any = None
        self._state = False

    def open(self) -> None:
        if self._device is not None:
            return

        try:
            from gpiozero import OutputDevice
        except ImportError as error:  # pragma: no cover - depends on the host
            raise LaserError(
                "gpiozero is not installed, so the laser pin cannot be driven."
            ) from error

        try:
            self._device = OutputDevice(
                self.config.gpio_pin,
                active_high=self.config.active_high,
                initial_value=False,
            )
        except Exception as error:  # noqa: BLE001 - gpiozero raises many types
            raise LaserError(
                f"Could not claim laser GPIO pin {self.config.gpio_pin}: {error}"
            ) from error

        self._state = False

        logger.info(f"Laser control ready on BCM pin {self.config.gpio_pin}.")

    def close(self) -> None:
        if self._device is None:
            return

        try:
            self._device.off()
            self._device.close()
        except Exception:  # noqa: BLE001 - close must never raise
            pass

        self._device = None
        self._state = False

    @property
    def controllable(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self._state

    def set_enabled(self, enabled: bool) -> bool:
        if self._device is None:
            raise LaserError("Laser control is not open.")

        if enabled:
            self._device.on()
        else:
            self._device.off()

        self._state = bool(enabled)

        if self.config.switch_delay > 0:
            time.sleep(self.config.switch_delay)

        return self._state

    def describe(self) -> dict[str, Any]:
        return {
            "control": "gpio",
            "controllable": True,
            "state": "on" if self._state else "off",
            "gpio_pin": self.config.gpio_pin,
            "active_high": self.config.active_high,
        }


class SimulatedLaser(LaserController):
    """Switches the laser inside the synthetic rig."""

    def __init__(self, config: LaserConfig, rig: Any) -> None:
        self.config = config
        self.rig = rig
        self._open = False

    def open(self) -> None:
        self._open = True
        self.rig.set_laser(True)

    def close(self) -> None:
        self._open = False

    @property
    def controllable(self) -> bool:
        # The simulated rig can always switch its laser, which is what makes
        # differential detection testable without hardware.
        return True

    @property
    def is_on(self) -> bool:
        return bool(self.rig.laser_on)

    def set_enabled(self, enabled: bool) -> bool:
        self.rig.set_laser(enabled)

        return self.is_on

    def describe(self) -> dict[str, Any]:
        return {
            "control": "simulated",
            "controllable": True,
            "state": "on" if self.is_on else "off",
            "gpio_pin": None,
        }
