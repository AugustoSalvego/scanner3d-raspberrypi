"""Hardware adapters for the scanner.

``base``
    The interfaces the scan pipeline talks to.
``camera`` / ``motor`` / ``laser``
    Real and synthetic implementations.
``factory``
    Builds the right set for the configured operating mode.
"""

from scanner_core.hardware.base import CameraDevice, LaserController, MotorDriver
from scanner_core.hardware.camera import OpenCVCamera, SyntheticCamera, encode_jpeg
from scanner_core.hardware.factory import HardwareSet, build_hardware
from scanner_core.hardware.laser import ExternalLaser, GpioLaser, SimulatedLaser
from scanner_core.hardware.motor import (
    HALF_STEP_SEQUENCE,
    HalfStepMotor28BYJ48,
    SimulatedMotor,
)

__all__ = [
    "HALF_STEP_SEQUENCE",
    "CameraDevice",
    "ExternalLaser",
    "GpioLaser",
    "HalfStepMotor28BYJ48",
    "HardwareSet",
    "LaserController",
    "MotorDriver",
    "OpenCVCamera",
    "SimulatedLaser",
    "SimulatedMotor",
    "SyntheticCamera",
    "build_hardware",
    "encode_jpeg",
]
