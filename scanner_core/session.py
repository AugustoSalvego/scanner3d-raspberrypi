"""Scan session storage.

Every acquisition gets its own folder holding the raw frames, the point clouds
produced from them and a ``metadata.json`` describing exactly how the data was
taken.  That metadata is what makes a scan reproducible: it records the settings
snapshot, the calibration that was in force, and - crucially - the real platform
angle of every single capture.  Reconstruction reads those angles rather than
assuming the images are evenly spread over a revolution, so a scan that was
cancelled half way through still reconstructs correctly.

Sessions end in one of five states and the pipeline is responsible for setting
the right one.  The old code marked every session ``finished`` from a ``finally``
block, which meant a crashed or cancelled scan was indistinguishable from a good
one.

Metadata is written atomically (temporary file plus ``os.replace``) so an
interrupted write cannot leave a half-serialised file behind.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from scanner_core import logger, paths
from scanner_core.errors import SessionError, UnsafePathError

SESSION_SCHEMA_VERSION = 2
METADATA_FILENAME = "metadata.json"
CAPTURES_DIRNAME = "captures"
POINT_CLOUDS_DIRNAME = "point_clouds"


class SessionStatus(str, Enum):
    """Lifecycle of a scan session."""

    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"

    @property
    def is_terminal(self) -> bool:
        return self in {SessionStatus.COMPLETED, SessionStatus.CANCELLED, SessionStatus.ERROR}


@dataclass
class CaptureRecord:
    """One frame stored in a session."""

    index: int
    filename: str
    angle_deg: float
    target_step: int
    motor_position: int
    timestamp: str
    laser_state: str = "unknown"
    laser_off_filename: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PointCloudRecord:
    """One point cloud produced from a session."""

    filename: str
    point_count: int
    created_at: str
    source: str = "reconstruction"
    statistics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


#: Bytes of randomness appended to a session id.  Four bytes give 2^32 values
#: per second, so even a burst of scans has a negligible collision chance - and
#: SessionManager.create still checks the directory before using it.
SESSION_ID_RANDOM_BYTES = 4


def generate_session_id() -> str:
    """Build a session id that cannot collide with a neighbouring scan.

    Second resolution alone is not enough: two scans started back to back - or a
    scan started right after a cancelled one - would land on the same name and
    silently share a folder.  A random suffix removes that risk; the folder
    existence check in :meth:`SessionManager.create` is what makes it a
    guarantee rather than a probability.
    """

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    return f"scan_{stamp}_{secrets.token_hex(SESSION_ID_RANDOM_BYTES)}"


class ScanSession:
    """A single scan folder and its metadata."""

    def __init__(self, path: Path, metadata: dict[str, Any]) -> None:
        self.path = path
        self._metadata = metadata
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    @property
    def session_id(self) -> str:
        return str(self._metadata["session_id"])

    @property
    def captures_path(self) -> Path:
        return self.path / CAPTURES_DIRNAME

    @property
    def point_clouds_path(self) -> Path:
        return self.path / POINT_CLOUDS_DIRNAME

    @property
    def metadata_path(self) -> Path:
        return self.path / METADATA_FILENAME

    @property
    def status(self) -> SessionStatus:
        try:
            return SessionStatus(self._metadata.get("status", SessionStatus.CREATED.value))
        except ValueError:
            return SessionStatus.ERROR

    def metadata(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._metadata))

    # ------------------------------------------------------------------
    def save(self) -> None:
        """Write ``metadata.json`` atomically."""

        with self._lock:
            self.path.mkdir(parents=True, exist_ok=True)

            temporary = self.metadata_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(self._metadata, indent=2), encoding="utf-8")

            os.replace(temporary, self.metadata_path)

    def mark_started(self) -> None:
        with self._lock:
            self._metadata["status"] = SessionStatus.RUNNING.value
            self._metadata["started_at"] = datetime.now().isoformat(timespec="seconds")

        self.save()

    def finish(
        self,
        status: SessionStatus,
        *,
        error: str | None = None,
    ) -> None:
        """Close the session in a specific state."""

        with self._lock:
            self._metadata["status"] = status.value
            self._metadata["finished_at"] = datetime.now().isoformat(timespec="seconds")

            if error:
                self._metadata.setdefault("errors", []).append(
                    {"at": datetime.now().isoformat(timespec="seconds"), "message": error}
                )

        self.save()

        logger.info(f"Scan session {self.session_id} finished as '{status.value}'.")

    def add_error(self, message: str) -> None:
        with self._lock:
            self._metadata.setdefault("errors", []).append(
                {"at": datetime.now().isoformat(timespec="seconds"), "message": message}
            )

        self.save()

    def add_capture(self, record: CaptureRecord) -> None:
        with self._lock:
            self._metadata.setdefault("captures", []).append(record.to_dict())
            self._metadata["capture_count"] = len(self._metadata["captures"])

        self.save()

    def add_point_cloud(self, record: PointCloudRecord) -> None:
        with self._lock:
            self._metadata.setdefault("point_clouds", []).append(record.to_dict())

        self.save()

    def set_reconstruction_statistics(self, statistics: dict[str, Any]) -> None:
        with self._lock:
            self._metadata["reconstruction"] = statistics

        self.save()

    def set_plan(self, plan: list[dict[str, Any]]) -> None:
        with self._lock:
            self._metadata["plan"] = plan

        self.save()

    # ------------------------------------------------------------------
    def capture_files(self) -> list[Path]:
        if not self.captures_path.exists():
            return []

        return sorted(path for path in self.captures_path.iterdir() if path.is_file())

    def point_cloud_files(self) -> list[Path]:
        if not self.point_clouds_path.exists():
            return []

        return sorted(
            path
            for path in self.point_clouds_path.iterdir()
            if path.is_file() and path.suffix.lower() == ".ply"
        )

    def summary(self) -> dict[str, Any]:
        """Compact description used by the sessions list in the dashboard."""

        metadata = self.metadata()

        point_clouds = metadata.get("point_clouds") or []

        return {
            "session_id": self.session_id,
            "schema_version": metadata.get("schema_version"),
            "status": metadata.get("status"),
            "mode": metadata.get("mode"),
            "created_at": metadata.get("created_at"),
            "started_at": metadata.get("started_at"),
            "finished_at": metadata.get("finished_at"),
            "capture_count": len(metadata.get("captures") or []),
            "point_cloud_count": len(point_clouds),
            "last_point_cloud": point_clouds[-1]["filename"] if point_clouds else None,
            "point_count": (
                point_clouds[-1].get("point_count") if point_clouds else None
            ),
            "error_count": len(metadata.get("errors") or []),
        }


class SessionManager:
    """Creates, lists, loads and deletes scan sessions."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root
        self._lock = threading.RLock()

    @property
    def root(self) -> Path:
        return self._root if self._root is not None else paths.SCANS_DIR

    # ------------------------------------------------------------------
    def create(
        self,
        *,
        mode: str,
        settings: dict[str, Any] | None = None,
        calibration: dict[str, Any] | None = None,
        notes: str = "",
    ) -> ScanSession:
        """Start a new session folder with a fresh metadata document."""

        with self._lock:
            session_id = generate_session_id()
            path = self.root / session_id

            # Practically impossible with the random suffix, but cheap to check.
            while path.exists():
                session_id = generate_session_id()
                path = self.root / session_id

            (path / CAPTURES_DIRNAME).mkdir(parents=True, exist_ok=True)
            (path / POINT_CLOUDS_DIRNAME).mkdir(parents=True, exist_ok=True)

            metadata: dict[str, Any] = {
                "schema_version": SESSION_SCHEMA_VERSION,
                "session_id": session_id,
                "mode": mode,
                "status": SessionStatus.CREATED.value,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "started_at": None,
                "finished_at": None,
                "notes": notes,
                "settings": settings or {},
                "calibration": calibration or {},
                "plan": [],
                "captures": [],
                "capture_count": 0,
                "point_clouds": [],
                "errors": [],
                "reconstruction": {},
            }

            session = ScanSession(path=path, metadata=metadata)
            session.save()

            logger.info(f"Scan session created: {session_id} (mode: {mode}).")

            return session

    # ------------------------------------------------------------------
    def session_path(self, session_id: str) -> Path:
        """Resolve a session folder, refusing anything outside the scans root."""

        try:
            return paths.resolve_within(self.root, session_id)
        except UnsafePathError as error:
            raise SessionError(f"Invalid session id: {session_id!r}") from error

    def load(self, session_id: str) -> ScanSession:
        path = self.session_path(session_id)
        metadata_path = path / METADATA_FILENAME

        if not metadata_path.exists():
            raise SessionError(f"Scan session not found: {session_id}")

        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SessionError(f"Session metadata is unreadable: {session_id}") from error

        return ScanSession(path=path, metadata=metadata)

    def list_sessions(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Newest session first.  Unreadable folders are skipped, not fatal."""

        root = self.root

        if not root.exists():
            return []

        summaries: list[dict[str, Any]] = []

        for entry in root.iterdir():
            if not entry.is_dir():
                continue

            metadata_path = entry / METADATA_FILENAME

            if not metadata_path.exists():
                continue

            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.warning(f"Skipping session with unreadable metadata: {entry.name}")
                continue

            summaries.append(ScanSession(path=entry, metadata=metadata).summary())

        summaries.sort(key=lambda item: item.get("created_at") or "", reverse=True)

        if limit is not None:
            summaries = summaries[:limit]

        return summaries

    def delete(self, session_id: str) -> None:
        path = self.session_path(session_id)

        if not path.exists():
            raise SessionError(f"Scan session not found: {session_id}")

        shutil.rmtree(path)

        logger.info(f"Scan session deleted: {session_id}")

    def recover_interrupted_sessions(self) -> int:
        """Mark sessions left ``running`` by a crash or power cut as errored.

        Called at start-up: a session that is still ``running`` when the process
        starts cannot possibly be running, and leaving it that way would make
        the dashboard lie.
        """

        recovered = 0

        for summary in self.list_sessions():
            if summary.get("status") not in {
                SessionStatus.RUNNING.value,
                SessionStatus.CREATED.value,
            }:
                continue

            try:
                session = self.load(str(summary["session_id"]))
            except SessionError:
                continue

            session.finish(
                SessionStatus.ERROR,
                error="Session was still marked as running when the scanner restarted.",
            )

            recovered += 1

        if recovered:
            logger.warning(f"Recovered {recovered} interrupted scan session(s) as 'error'.")

        return recovered


#: Process-wide session manager.
session_manager = SessionManager()
