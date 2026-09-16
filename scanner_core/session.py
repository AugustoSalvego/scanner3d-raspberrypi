import json
import os
from datetime import datetime

from scanner_core.config import SCANS_FOLDER
from scanner_core.logger import add_log


current_session = None


def create_scan_session():
    global current_session

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_id = f"scan_{timestamp}"

    session_path = os.path.join(
        SCANS_FOLDER,
        session_id
    )

    captures_path = os.path.join(
        session_path,
        "captures"
    )

    point_clouds_path = os.path.join(
        session_path,
        "point_clouds"
    )

    os.makedirs(captures_path, exist_ok=True)
    os.makedirs(point_clouds_path, exist_ok=True)

    metadata = {
        "session_id": session_id,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": None,
        "captures": [],
        "point_clouds": [],
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

    with open(metadata_path, "w", encoding="utf-8") as file:
        json.dump(
            metadata,
            file,
            indent=4
        )


def add_capture_to_session(filename):
    if current_session is None:
        return

    metadata = current_session["metadata"]

    metadata["captures"].append(filename)

    save_metadata(
        current_session["session_path"],
        metadata
    )


def add_point_cloud_to_session(filename):
    if current_session is None:
        return

    metadata = current_session["metadata"]

    metadata["point_clouds"].append(filename)

    save_metadata(
        current_session["session_path"],
        metadata
    )


def finish_current_session():
    global current_session

    if current_session is None:
        return

    metadata = current_session["metadata"]

    metadata["status"] = "finished"
    metadata["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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

        with open(metadata_path, "r", encoding="utf-8") as file:
            metadata = json.load(file)

        sessions.append(metadata)

    sessions.sort(
        key=lambda item: item["created_at"],
        reverse=True
    )

    return sessions