"""Camera devices.

``OpenCVCamera``
    A real USB webcam.  Everything that touches ``cv2.VideoCapture`` happens
    under one re-entrant lock, so the MJPEG preview and the scan thread cannot
    call into the driver at the same time - and, more importantly, the preview
    cannot steal the frame that the scanner just flushed the buffer for.

``SyntheticCamera``
    Renders the simulation rig.  Opens no device and needs no webcam.

Settings are never assumed to have been applied: every ``set()`` is followed by
a ``get()`` and the readback is reported, because USB webcams routinely accept a
call and then ignore it.  The old ``tools/reset_camera_settings.py`` did the
former without the latter, which is exactly how the project ended up with a
camera nobody could trust.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import cv2
import numpy as np

from scanner_core import logger
from scanner_core.config import CameraConfig
from scanner_core.errors import CameraError
from scanner_core.hardware.base import CameraDevice
from scanner_core.simulation import SimulationRig

#: Driver properties that can be requested through ``camera.controls``.
CONTROL_PROPERTIES: dict[str, int] = {
    "brightness": cv2.CAP_PROP_BRIGHTNESS,
    "contrast": cv2.CAP_PROP_CONTRAST,
    "saturation": cv2.CAP_PROP_SATURATION,
    "sharpness": cv2.CAP_PROP_SHARPNESS,
    "gain": cv2.CAP_PROP_GAIN,
    "exposure": cv2.CAP_PROP_EXPOSURE,
    "auto_exposure": cv2.CAP_PROP_AUTO_EXPOSURE,
    "focus": cv2.CAP_PROP_FOCUS,
    "autofocus": cv2.CAP_PROP_AUTOFOCUS,
    "white_balance": cv2.CAP_PROP_WB_TEMPERATURE,
    "auto_white_balance": cv2.CAP_PROP_AUTO_WB,
}

#: How close a readback must be to the request before it counts as accepted.
CONTROL_TOLERANCE = 1e-3


class OpenCVCamera(CameraDevice):
    """A USB webcam accessed through OpenCV."""

    def __init__(self, config: CameraConfig) -> None:
        config.validate()

        self.config = config
        self._lock = threading.RLock()
        self._capture: cv2.VideoCapture | None = None
        self._actual: dict[str, Any] = {}
        self._control_report: dict[str, Any] = {}
        self._consecutive_failures = 0

    # ------------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._capture is not None and self._capture.isOpened()

    def open(self) -> None:
        with self._lock:
            if self.is_open:
                return

            device = self.config.device

            capture = cv2.VideoCapture(device)

            if not capture.isOpened():
                capture.release()

                raise CameraError(
                    f"Camera {device!r} could not be opened. Check that it is "
                    "connected and not in use by another program."
                )

            self._capture = capture

            self._apply_format()
            self._apply_controls()
            self._warm_up()
            self._read_back_format()

            self._consecutive_failures = 0

            width, height = self.resolution

            logger.info(
                f"Camera {device!r} open at {width}x{height} "
                f"(requested {self.config.width}x{self.config.height})."
            )

    def close(self) -> None:
        with self._lock:
            if self._capture is not None:
                try:
                    self._capture.release()
                except Exception:  # noqa: BLE001 - close must never raise
                    pass

            self._capture = None

    # ------------------------------------------------------------------
    def _apply_format(self) -> None:
        capture = self._capture

        if capture is None:
            return

        # FOURCC must be set before the resolution: on many UVC cameras the
        # available resolutions depend on the pixel format.  MJPG is what lets
        # a Fifine K420 reach 1280x720 at a usable frame rate over USB 2.
        if self.config.fourcc:
            capture.set(
                cv2.CAP_PROP_FOURCC,
                cv2.VideoWriter_fourcc(*self.config.fourcc),
            )

        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
        capture.set(cv2.CAP_PROP_FPS, self.config.fps)

    def _read_back_format(self) -> None:
        capture = self._capture

        if capture is None:
            return

        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS))

        fourcc_value = int(capture.get(cv2.CAP_PROP_FOURCC))
        fourcc = (
            "".join(chr((fourcc_value >> (8 * index)) & 0xFF) for index in range(4))
            if fourcc_value
            else ""
        )

        self._actual = {
            "width": width,
            "height": height,
            "fps": fps,
            "fourcc": fourcc.strip("\x00").strip(),
        }

        if (width, height) != (self.config.width, self.config.height):
            logger.warning(
                f"Camera delivered {width}x{height} instead of the requested "
                f"{self.config.width}x{self.config.height}. Reconstruction uses the "
                "real resolution, so recalibrate if this is not what you calibrated at."
            )

    def _apply_controls(self) -> None:
        """Write every requested control and read it back.

        Scales differ wildly between drivers, so a mismatch is reported rather
        than retried: the operator decides what to do about it.
        """

        capture = self._capture

        if capture is None or not self.config.controls:
            self._control_report = {}
            return

        report: dict[str, Any] = {}

        for name, value in self.config.controls.items():
            prop = CONTROL_PROPERTIES.get(name)

            if prop is None:
                report[name] = {"requested": value, "accepted": False, "reason": "unknown control"}
                continue

            capture.set(prop, float(value))

            # Some drivers need a moment before the readback is meaningful.
            time.sleep(0.05)

            actual = float(capture.get(prop))
            accepted = abs(actual - float(value)) <= max(
                CONTROL_TOLERANCE, abs(float(value)) * 0.02
            )

            report[name] = {
                "requested": float(value),
                "actual": actual,
                "accepted": accepted,
            }

            if not accepted:
                logger.warning(
                    f"Camera control '{name}': requested {value}, driver reports {actual}."
                )

        self._control_report = report

    def _warm_up(self) -> None:
        capture = self._capture

        if capture is None:
            return

        for _ in range(self.config.warmup_frames):
            capture.read()

    # ------------------------------------------------------------------
    def read(self) -> np.ndarray | None:
        with self._lock:
            if self._capture is None:
                try:
                    self.open()
                except CameraError:
                    return None

            capture = self._capture

            if capture is None:
                return None

            for _ in range(self.config.read_retries):
                success, frame = capture.read()

                if success and frame is not None and frame.size:
                    self._consecutive_failures = 0

                    return frame

            self._consecutive_failures += 1

            if self.config.reopen_on_failure and self._consecutive_failures >= 2:
                logger.warning("Camera stopped delivering frames; reopening the device.")

                self.close()
                self._consecutive_failures = 0

                try:
                    self.open()
                except CameraError as error:
                    logger.error(f"Camera could not be reopened: {error}")

                    return None

                # Read from the freshly opened handle, not the released one.
                if self._capture is not None:
                    success, frame = self._capture.read()

                    if success and frame is not None and frame.size:
                        return frame

            return None

    def capture(self, *, flush_frames: int | None = None) -> np.ndarray | None:
        """Flush stale frames and return a fresh one, atomically.

        Holding the lock across the flush is the whole point: a USB webcam keeps
        several frames buffered, so after the platform moves the first frames
        still show the previous position.
        """

        count = self.config.flush_frames if flush_frames is None else int(flush_frames)

        with self._lock:
            if self._capture is None:
                self.open()

            capture = self._capture

            if capture is None:
                return None

            for _ in range(max(0, count)):
                capture.grab()

            return self.read()

    @property
    def resolution(self) -> tuple[int, int]:
        width = int(self._actual.get("width") or self.config.width)
        height = int(self._actual.get("height") or self.config.height)

        return width, height

    def describe(self) -> dict[str, Any]:
        return {
            "backend": "opencv",
            "device": self.config.device,
            "open": self.is_open,
            "requested": {
                "width": self.config.width,
                "height": self.config.height,
                "fps": self.config.fps,
                "fourcc": self.config.fourcc,
            },
            "actual": dict(self._actual),
            "flush_frames": self.config.flush_frames,
            "controls": dict(self._control_report),
        }


class SyntheticCamera(CameraDevice):
    """Renders the simulation rig instead of opening a webcam."""

    def __init__(self, rig: SimulationRig) -> None:
        self.rig = rig
        self._open = False
        self._lock = threading.RLock()

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def read(self) -> np.ndarray | None:
        if not self._open:
            return None

        with self._lock:
            return self.rig.render_current()

    def capture(self, *, flush_frames: int | None = None) -> np.ndarray | None:
        # A synthetic camera has no buffer to flush.
        return self.read()

    @property
    def resolution(self) -> tuple[int, int]:
        config = self.rig.scene.config

        return config.image_width, config.image_height

    def describe(self) -> dict[str, Any]:
        width, height = self.resolution

        return {
            "backend": "synthetic",
            "device": "simulation",
            "open": self.is_open,
            "requested": {"width": width, "height": height, "fps": 0, "fourcc": None},
            "actual": {"width": width, "height": height, "fps": 0, "fourcc": "SYNTH"},
            "flush_frames": 0,
            "controls": {},
            "scene": self.rig.scene.describe(),
        }


def encode_jpeg(frame: np.ndarray, quality: int = 75) -> bytes | None:
    """Encode a frame as JPEG for the MJPEG stream."""

    if frame is None or frame.size == 0:
        return None

    success, buffer = cv2.imencode(
        ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
    )

    if not success:
        return None

    return buffer.tobytes()
