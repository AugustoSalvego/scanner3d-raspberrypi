"""Durable scan records and narrowly scoped output paths."""
import copy
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from scanner_core.config import OUTPUT_ROOT

def timestamp():
    return datetime.now(timezone.utc).isoformat()

def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

def confined_path(root, relative, must_exist=True):
    root = Path(root).resolve()
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("Invalid relative path")
    candidate = (root / relative).resolve()
    if candidate == root or root not in candidate.parents:
        raise ValueError("File path leaves the authorized output folder")
    if must_exist and not candidate.is_file():
        raise FileNotFoundError(relative)
    return candidate

class OutputLock:
    """OS lock released automatically on process exit; shared by CLI and web."""
    def __init__(self, root):
        self.path = Path(root) / ".scanner.lock"
        self.stream = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        stream.seek(0)
        if not stream.read(1):
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            stream.close()
            raise RuntimeError("Another scanner process owns this output folder") from error
        self.stream = stream

    def release(self):
        if self.stream is not None:
            stream, self.stream = self.stream, None
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()

class SessionStore:
    def __init__(self, root=OUTPUT_ROOT):
        self.root = Path(root).resolve()
        self.scans = self.root / "scans"
        self.lock = threading.RLock()
        self.current_session = None

    def create(self, settings, calibration, source, angle_source):
        with self.lock:
            if self.current_session:
                raise RuntimeError("An acquisition session is already active")
            session_id = "scan_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f") + "_" + uuid.uuid4().hex[:8]
            folder = self.folder(session_id)
            (folder / "captures").mkdir(parents=True, exist_ok=False)
            (folder / "point_clouds").mkdir()
            record = {
                "schema_version": 1, "session_id": session_id, "created_at": timestamp(),
                "finished_at": None, "status": "acquiring", "source": source,
                "angle_source": angle_source, "captures": [], "point_clouds": [],
                "settings": copy.deepcopy(settings), "calibration": copy.deepcopy(calibration),
                "error": None,
            }
            atomic_json(folder / "settings.json", settings)
            atomic_json(folder / "calibration.json", calibration)
            atomic_json(folder / "metadata.json", record)
            self.current_session = record
            return record

    def folder(self, session_id):
        if not isinstance(session_id, str) or not re.fullmatch(r"scan_[A-Za-z0-9_-]+", session_id):
            raise ValueError("Invalid session ID")
        folder = (self.scans / session_id).resolve()
        if self.scans.resolve() not in folder.parents or self.root not in folder.parents:
            raise ValueError("Session leaves authorized output folder")
        return folder

    def save(self, record):
        with self.lock:
            atomic_json(self.folder(record["session_id"]) / "metadata.json", record)

    def finish(self, record, status, error=None):
        if status not in ("completed", "cancelled", "error"):
            raise ValueError("Invalid terminal status")
        with self.lock:
            record.update(status=status, error=error, finished_at=timestamp())
            try:
                self.save(record)
            finally:
                if self.current_session is record:
                    self.current_session = None

    def load(self, session_id):
        with self.lock:
            return json.loads((self.folder(session_id) / "metadata.json").read_text(encoding="utf-8"))

    def list(self):
        with self.lock:
            result = []
            if not self.scans.exists():
                return result
            for folder in self.scans.iterdir():
                try:
                    item = self.load(folder.name)
                    if item["status"] in ("acquiring", "processing", "cancelling") and (not self.current_session or item["session_id"] != self.current_session["session_id"]):
                        item["status"] = "interrupted"
                        item["error"] = "No task in this server owns this unfinished record; inspect before reuse"
                    result.append(item)
                except (ValueError, KeyError, OSError):
                    continue
            return sorted(result, key=lambda x: x["created_at"], reverse=True)

_default_store = SessionStore()

def get_current_session():
    with _default_store.lock:
        return copy.deepcopy(_default_store.current_session)

def get_current_session_id():
    session = get_current_session()
    return session["session_id"] if session else None

def list_scan_sessions():
    return _default_store.list()
