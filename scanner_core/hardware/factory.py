"""Builds the hardware set that matches the configured operating mode.

This is the only place that decides "real device or synthetic one", which keeps
the mode check out of the scan pipeline entirely.  In particular, nothing in the
simulation path can reach GPIO or ``cv2.VideoCapture``: the objects that could
do so are never constructed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scanner_core.calibration.store import CalibrationBundle, calibration_store
from scanner_core.config import LaserControlMode, ScannerConfig, ScannerMode
from scanner_core.errors import ConfigurationError
from scanner_core.hardware.base import CameraDevice, LaserController, MotorDriver
from scanner_core.hardware.camera import OpenCVCamera, SyntheticCamera
from scanner_core.hardware.laser import ExternalLaser, GpioLaser, SimulatedLaser
from scanner_core.hardware.motor import HalfStepMotor28BYJ48, SimulatedMotor
from scanner_core.simulation import SimulationRig


@dataclass
class HardwareSet:
    """The devices a scan needs, plus the calibration it must use."""

    camera: CameraDevice
    motor: MotorDriver
    laser: LaserController
    calibration: CalibrationBundle
    mode: ScannerMode
    rig: SimulationRig | None = None

    @property
    def is_synthetic(self) -> bool:
        return self.rig is not None

    def open(self) -> None:
        """Open every device, rolling back if one of them fails."""

        opened: list[Any] = []

        try:
            for device in (self.camera, self.motor, self.laser):
                device.open()
                opened.append(device)
        except Exception:
            for device in reversed(opened):
                try:
                    device.close()
                except Exception:  # noqa: BLE001 - best effort roll-back
                    pass

            raise

    def close(self, *, keep_camera: bool = False) -> None:
        """Release the devices.  Never raises.

        ``keep_camera`` leaves the webcam open so the live preview survives the
        end of a scan; the motor coils and the laser are always released.
        """

        try:
            self.motor.release()
        except Exception:  # noqa: BLE001 - best effort
            pass

        for device in (self.motor, self.laser):
            try:
                device.close()
            except Exception:  # noqa: BLE001 - best effort
                pass

        if not keep_camera:
            try:
                self.camera.close()
            except Exception:  # noqa: BLE001 - best effort
                pass

    def describe(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "camera": self.camera.describe(),
            "motor": self.motor.describe(),
            "laser": self.laser.describe(),
        }


def build_hardware(
    config: ScannerConfig,
    *,
    camera: CameraDevice | None = None,
    store: Any = None,
) -> HardwareSet:
    """Create the devices and load the calibration for ``config.mode``.

    Args:
        config: The validated scanner configuration.
        camera: An already-open camera to reuse, so the live preview is not
            torn down when a scan starts.  Ignored in simulation mode.
        store: Calibration store override, used by the tests.
    """

    mode = config.mode

    if mode is ScannerMode.OFFLINE:
        raise ConfigurationError(
            "Offline mode does not drive hardware. Switch to simulation or "
            "physical mode to acquire new captures."
        )

    if mode is ScannerMode.SIMULATION:
        rig = SimulationRig(config.simulation)

        motor = SimulatedMotor(
            config.motor,
            on_position_changed=lambda _position, angle: rig.set_angle(angle),
        )

        return HardwareSet(
            camera=SyntheticCamera(rig),
            motor=motor,
            laser=SimulatedLaser(config.laser, rig),
            calibration=rig.scene.build_calibration_bundle(
                steps_per_revolution=config.motor.steps_per_revolution
            ),
            mode=mode,
            rig=rig,
        )

    calibration_source = store if store is not None else calibration_store

    laser: LaserController

    if config.laser.control is LaserControlMode.GPIO:
        laser = GpioLaser(config.laser)
    else:
        laser = ExternalLaser(config.laser)

    return HardwareSet(
        camera=camera if camera is not None else OpenCVCamera(config.camera),
        motor=HalfStepMotor28BYJ48(config.motor),
        laser=laser,
        calibration=calibration_source.load_bundle(),
        mode=mode,
    )
