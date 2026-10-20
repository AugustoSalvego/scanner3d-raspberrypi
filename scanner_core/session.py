import json
import os
import tempfile
import uuid
from datetime import datetime

from scanner_core.config import SCANS_FOLDER
from scanner_core.logger import add_log


current_session = None


def create_scan_session():
    global current_session

    # The clock is useful for humans, but never sufficient for uniqueness.
    # mkdir is exclusive and protects sessions even after an incorrect clock reset.
    os.makedirs(SCANS_FOLDER, exist_ok=True)
    for _ in range(10):
        now = datetime.now().astimezone()
        session_id = (
            f"scan_{now:%Y%m%d_%H%M%S_%f}_{uuid.uuid4().hex[:8]}"
        )
        session_path = os.path.join(SCANS_FOLDER, session_id)
        try:
            os.mkdir(session_path)
        except FileExistsError:
            continue
        break
    else:
        raise RuntimeError("Unable to allocate a unique scan session.")

    captures_path = os.path.join(
        session_path,
        "captures"
    )

    point_clouds_path = os.path.join(
        session_path,
        "point_clouds"
    )

    os.mkdir(captures_path)
    os.mkdir(point_clouds_path)

    metadata = {
        "session_id": session_id,
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "created_at_iso": now.isoformat(timespec="seconds"),
        "display_name": None,
        "finished_at": None,
        "captures": [],
        "point_clouds": [],
        "capture_positions": [],
        "status": "running"
    }

    save_metadata(session_path, metadata)

    current_session = {
        "session_id": session_id,
        "session_path": session_path,
        "captures_path": captures_path,
        "point_clouds_path": point_clouds_path,
        "metadata": metadata
    }

    add_log(f"Scan session created: {session_id}")

    return current_session


def get_current_session():
    return current_session


def get_current_session_id():
    if current_session is None:
        return None

    return current_session["session_id"]


def save_metadata(session_path, metadata):
    metadata_path = os.path.join(
        session_path,
        "metadata.json"
    )

    # Replace the complete document atomically: a restart must not corrupt it.
    fd, temporary_path = tempfile.mkstemp(
        prefix=".metadata_", suffix=".tmp", dir=session_path
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(metadata, file, indent=4, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, metadata_path)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def add_capture_to_session(filename):
    if current_session is None:
        return

    metadata = current_session["metadata"]

    metadata["captures"].append(filename)

    save_metadata(
        current_session["session_path"],
        metadata
    )


def add_capture_position(filename, half_steps, angle_deg, physical):
    """Store real, commanded motor position alongside each saved photograph.

    This is open-loop position (no encoder); verify the motor does not skip steps.
    """
    if current_session is None:
        return
    current_session["metadata"].setdefault("capture_positions", []).append({
        "filename": filename,
        "half_steps": int(half_steps),
        "angle_deg": round(float(angle_deg), 6),
        "physical": bool(physical),
    })
    save_metadata(current_session["session_path"], current_session["metadata"])


def add_point_cloud_to_session(filename):
    if current_session is None:
        return

    metadata = current_session["metadata"]

    metadata["point_clouds"].append(filename)

    save_metadata(
        current_session["session_path"],
        metadata
    )


def finish_current_session(status="finished"):
    global current_session

    if current_session is None:
        return

    metadata = current_session["metadata"]

    metadata["status"] = status
    finished = datetime.now().astimezone()
    metadata["finished_at"] = finished.strftime("%Y-%m-%d %H:%M:%S")
    metadata["finished_at_iso"] = finished.isoformat(timespec="seconds")

    save_metadata(
        current_session["session_path"],
        metadata
    )

    add_log(
        f"Scan session finished: {current_session['session_id']}"
    )


def list_scan_sessions():
    os.makedirs(SCANS_FOLDER, exist_ok=True)

    sessions = []

    for session_id in os.listdir(SCANS_FOLDER):
        session_path = os.path.join(
            SCANS_FOLDER,
            session_id
        )

        if not os.path.isdir(session_path):
            continue

        metadata_path = os.path.join(
            session_path,
            "metadata.json"
        )

        if not os.path.exists(metadata_path):
            continue

        try:
            with open(metadata_path, "r", encoding="utf-8") as file:
                metadata = json.load(file)
        except (OSError, ValueError) as error:
            add_log(f"Skipping unreadable session {session_id}: {error}")
            continue
        if not isinstance(metadata, dict):
            continue
        sessions.append(metadata)

    sessions.sort(
        key=lambda item: item.get("created_at", ""),
        reverse=True
    )

    return sessions