"""Exception hierarchy for the Scanner3D core.

Every failure raised by :mod:`scanner_core` derives from :class:`ScannerError`
so the web layer can translate domain failures into HTTP status codes without
leaking tracebacks to the browser.
"""

from __future__ import annotations


class ScannerError(Exception):
    """Base class for every scanner specific failure."""

    #: Suggested HTTP status code when this error escapes to the API layer.
    status_code: int = 500


class ConfigurationError(ScannerError):
    """Raised when configuration values are missing or inconsistent."""

    status_code = 422


class CalibrationError(ScannerError):
    """Raised when a calibration is missing, invalid or degenerate."""

    status_code = 422


class HardwareError(ScannerError):
    """Raised when a physical device cannot be initialised or driven."""

    status_code = 503


class CameraError(HardwareError):
    """Raised when the camera cannot deliver frames."""


class MotorError(HardwareError):
    """Raised when the stepper motor cannot be driven."""


class LaserError(HardwareError):
    """Raised when the laser control device cannot be driven."""


class DetectionError(ScannerError):
    """Raised when the laser line cannot be detected reliably."""

    status_code = 422


class ReconstructionError(ScannerError):
    """Raised when triangulation cannot produce a usable point cloud."""

    status_code = 422


class SessionError(ScannerError):
    """Raised for invalid scan session access or transitions."""

    status_code = 404


class ScanCancelled(ScannerError):
    """Raised internally when the operator stops a running scan."""

    status_code = 409


class ScanAlreadyRunning(ScannerError):
    """Raised when a second scan is requested while one is active."""

    status_code = 409


class UnsafePathError(ScannerError):
    """Raised when a user supplied path escapes its allowed root."""

    status_code = 400
