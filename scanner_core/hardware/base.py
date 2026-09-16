"""Hardware interfaces.

The scan pipeline only ever talks to these three abstractions, which is what
lets exactly the same code drive a real Raspberry Pi rig and a synthetic one.

Two rules hold for every implementation:

* importing a module must never touch GPIO, open a camera or move a motor -
  construction is explicit and ``open()``/``close()`` are the only lifecycle
  hooks;
* ``close()`` must be safe to call twice and must never raise, so it can be used
  from a ``finally`` block during an error or a cancellation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np


class CameraDevice(ABC):
    """Anything that can deliver BGR frames."""

    @abstractmethod
    def open(self) -> None:
        """Make the device ready.  Idempotent."""

    @abstractmethod
    def close(self) -> None:
        """Release the device.  Idempotent and must not raise."""

    @property
    @abstractmethod
    def is_open(self) -> bool: ...

    @abstractmethod
    def read(self) -> np.ndarray | None:
        """Return the next frame, or ``None`` when the device failed."""

    @abstractmethod
    def capture(self, *, flush_frames: int | None = None) -> np.ndarray | None:
        """Discard stale buffered frames, then return a fresh one.

        This must be atomic with respect to other readers: the live preview
        must not be able to steal the frame that was just flushed for.
        """

    @property
    @abstractmethod
    def resolution(self) -> tuple[int, int]:
        """Actual ``(width, height)`` the device is delivering."""

    @abstractmethod
    def describe(self) -> dict[str, Any]:
        """Requested versus actual settings, for the health endpoint."""


class MotorDriver(ABC):
    """A stepper driving the turntable."""

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None:
        """Release the coils and free the pins.  Idempotent, never raises."""

    @property
    @abstractmethod
    def is_open(self) -> bool: ...

    @property
    @abstractmethod
    def position(self) -> int:
        """Current absolute position in motor steps since ``reset``."""

    @abstractmethod
    def move_relative(self, steps: int, *, should_cancel: Any = None) -> int:
        """Move ``steps`` (signed) and return the new absolute position."""

    @abstractmethod
    def move_to(self, position: int, *, should_cancel: Any = None) -> int:
        """Move to an absolute ``position`` and return it."""

    @abstractmethod
    def release(self) -> None:
        """De-energise the coils so the motor stops heating and drawing current."""

    @abstractmethod
    def reset_position(self) -> None:
        """Declare the current physical position to be step zero."""

    @abstractmethod
    def describe(self) -> dict[str, Any]: ...


class LaserController(ABC):
    """Laser power control.

    The reference build powers the laser from an external supply and cannot
    switch it; that case is represented by a controller that reports
    ``controllable == False`` rather than by pretending a GPIO pin exists.
    """

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @property
    @abstractmethod
    def controllable(self) -> bool:
        """``True`` only when the software can really switch the laser."""

    @property
    @abstractmethod
    def is_on(self) -> bool: ...

    @abstractmethod
    def set_enabled(self, enabled: bool) -> bool:
        """Switch the laser.  Returns the state actually reached."""

    @abstractmethod
    def describe(self) -> dict[str, Any]: ...
