"""Scan control and scan session management."""

from __future__ import annotations

from flask import Blueprint

from scanner_core.errors import SessionError
from scanner_core.pipeline import scan_controller
from scanner_core.session import session_manager
from web_interface.api_response import api_error, api_success, error_from_exception

blueprint = Blueprint("scan", __name__)


@blueprint.post("/api/scan/start")
def start():
    try:
        session_id = scan_controller.start_scan_async()
    except Exception as error:  # noqa: BLE001 - translated for the client
        return error_from_exception(error)

    return api_success("Scan started.", {"session_id": session_id}, 201)


@blueprint.post("/api/scan/stop")
def stop():
    stopped = scan_controller.request_stop()

    if not stopped:
        return api_error("No scan is running.", 409)

    return api_success("Stop requested; the scan will end at the next checkpoint.")


@blueprint.get("/api/scan/sessions")
def list_sessions():
    return api_success("Scan sessions.", {"sessions": session_manager.list_sessions()})


@blueprint.get("/api/scan/sessions/<session_id>")
def get_session(session_id: str):
    try:
        session = session_manager.load(session_id)
    except SessionError as error:
        return error_from_exception(error)

    metadata = session.metadata()

    captures = [
        {
            **capture,
            "url": f"/captures/{session_id}/{capture['filename']}",
        }
        for capture in metadata.get("captures", [])
    ]

    point_clouds = [
        {
            **cloud,
            "download_url": f"/download/point-cloud/{session_id}/{cloud['filename']}",
        }
        for cloud in metadata.get("point_clouds", [])
    ]

    return api_success(
        "Scan session.",
        {
            "session": {
                **metadata,
                "captures": captures,
                "point_clouds": point_clouds,
            }
        },
    )


@blueprint.delete("/api/scan/sessions/<session_id>")
def delete_session(session_id: str):
    try:
        session_manager.delete(session_id)
    except SessionError as error:
        return error_from_exception(error)

    return api_success("Scan session deleted.", {"session_id": session_id})


@blueprint.post("/api/scan/sessions/<session_id>/reconstruct")
def reconstruct(session_id: str):
    """Re-run reconstruction on stored captures, without moving the hardware."""

    try:
        outcome = scan_controller.reconstruct_existing_session(session_id)
    except Exception as error:  # noqa: BLE001 - translated for the client
        return error_from_exception(error)

    return api_success(
        outcome.message,
        {
            "session_id": outcome.session_id,
            "point_count": outcome.point_count,
            "point_cloud": (
                None if outcome.point_cloud_path is None else outcome.point_cloud_path.name
            ),
            "statistics": outcome.statistics or {},
        },
        201,
    )
