"""Single, thread-safe source of truth for live scanner state.

Only *runtime* facts live here.  The operating mode, scan parameters and
hardware description belong to :mod:`scanner_core.config`; the calibration
belongs to :mod:`scanner_core.calibration`.  Keeping them apart is what stops
the old ``simulation_mode`` duplication from coming back.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ScanPhase(str, Enum):
    """Coarse lifecycle of the acquisition/reconstruction pipeline."""

    IDLE = "idle"
    PREPARING = "preparing"
    SCANNING = "scanning"
    RECONSTRUCTING = "reconstructing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class ScannerState:
    """Live values displayed by the dashboard."""

    phase: ScanPhase = ScanPhase.IDLE
    running: bool = False

    session_id: str | None = None
    capture_index: int = 0
    capture_total: int = 0
    angle_deg: float | None = None
    motor_position: int = 0
    motor_target: int | None = None
    frames_captured: int = 0

    started_at: str | None = None
    finished_at: str | None = None
    started_monotonic: float | None = None
    elapsed_seconds: float = 0.0

    last_capture: str | None = None
    last_point_cloud: str | None = None
    last_point_cloud_session: str | None = None
    last_point_count: int | None = None
    last_scan_finished_at: str | None = None
    last_session_id: str | None = None
    last_error: str | None = None

    statistics: dict[str, Any] = field(default_factory=dict)

    @property
    def progress(self) -> float:
        if self.capture_total <= 0:
            return 0.0

        return round(min(1.0, self.capture_index / self.capture_total) * 100.0, 1)

    def to_dict(self) -> dict[str, Any]:
        elapsed = self.elapsed_seconds

        if self.running and self.started_monotonic is not None:
            elapsed = time.monotonic() - self.started_monotonic

        return {
            "phase": self.phase.value,
            "running": self.running,
            "session_id": self.session_id,
            "capture_index": self.capture_index,
            "capture_total": self.capture_total,
            "progress_percent": self.progress,
            "angle_deg": self.angle_deg,
            "motor_position": self.motor_position,
            "motor_target": self.motor_target,
            "frames_captured": self.frames_captured,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": round(elapsed, 2),
            "last_capture": self.last_capture,
            "last_point_cloud": self.last_point_cloud,
            "last_point_cloud_session": self.last_point_cloud_session,
            "last_point_count": self.last_point_count,
            "last_scan_finished_at": self.last_scan_finished_at,
            "last_session_id": self.last_session_id,
            "last_error": self.last_error,
            "statistics": dict(self.statistics),
        }


class StateStore:
    """Guards :class:`ScannerState` with a re-entrant lock."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = ScannerState()

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._state.to_dict()

    def get(self) -> ScannerState:
        with self._lock:
            return self._state

    def update(self, **values: Any) -> None:
        with self._lock:
            for key, value in values.items():
                if not hasattr(self._state, key):
                    raise AttributeError(f"Unknown scanner state field: {key}")

                setattr(self._state, key, value)

    def is_running(self) -> bool:
        with self._lock:
            return self._state.running

    def begin_scan(self, session_id: str, capture_total: int) -> None:
        """Atomically mark a scan as started.

        Callers must already hold the scan mutex; this only resets the values
        the dashboard shows.
        """

        with self._lock:
            now = datetime.now()

            self._state.phase = ScanPhase.PREPARING
            self._state.running = True
            self._state.session_id = session_id
            self._state.last_session_id = session_id
            self._state.capture_index = 0
            self._state.capture_total = capture_total
            self._state.angle_deg = None
            self._state.motor_position = 0
            self._state.motor_target = None
            self._state.frames_captured = 0
            self._state.started_at = now.strftime("%Y-%m-%d %H:%M:%S")
            self._state.finished_at = None
            self._state.started_monotonic = time.monotonic()
            self._state.elapsed_seconds = 0.0
            self._state.last_error = None
            self._state.statistics = {}

    def end_scan(self, phase: ScanPhase, *, error: str | None = None) -> None:
        with self._lock:
            now = datetime.now()

            if self._state.started_monotonic is not None:
                self._state.elapsed_seconds = time.monotonic() - self._state.started_monotonic

            self._state.phase = phase
            self._state.running = False
            self._state.session_id = None
            self._state.motor_target = None
            self._state.started_monotonic = None
            self._state.finished_at = now.strftime("%Y-%m-%d %H:%M:%S")
            self._state.last_scan_finished_at = self._state.finished_at

            if error is not None:
                self._state.last_error = error

    def record_capture(
        self,
        *,
        capture_index: int,
        filename: str,
        angle_deg: float,
        motor_position: int,
    ) -> None:
        with self._lock:
            self._state.capture_index = capture_index
            self._state.frames_captured += 1
            self._state.last_capture = filename
            self._state.angle_deg = angle_deg
            self._state.motor_position = motor_position

    def record_point_cloud(
        self,
        *,
        path: str,
        session_id: str | None,
        point_count: int,
        statistics: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._state.last_point_cloud = path
            self._state.last_point_cloud_session = session_id
            self._state.last_point_count = point_count

            if statistics is not None:
                self._state.statistics = dict(statistics)

    def clear_error(self) -> None:
        with self._lock:
            self._state.last_error = None

            if self._state.phase is ScanPhase.ERROR:
                self._state.phase = ScanPhase.IDLE

    def reset(self) -> None:
        with self._lock:
            self._state = ScannerState()


#: Process-wide state used by the web application.
state_store = StateStore()
