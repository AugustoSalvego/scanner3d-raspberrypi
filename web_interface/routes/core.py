"""Dashboard page, health, status and logs."""

from __future__ import annotations

from flask import Blueprint, render_template, request

from scanner_core import logger
from scanner_core.config import APP_VERSION, config_store
from scanner_core.status import build_status, viewer_status
from web_interface.api_response import api_success

blueprint = Blueprint("core", __name__)


@blueprint.get("/")
def dashboard():
    return render_template("index.html", version=APP_VERSION)


@blueprint.get("/api/health")
def health():
    """Component level health, not just "Flask answered"."""

    status = build_status()
    service = status["service"]

    return api_success(
        "Scanner health report.",
        {
            "service": service,
            "mode": status["mode"],
            "camera": {
                "configured": status["camera"].get("configured"),
                "available": status["camera"].get("available"),
            },
            "motor": {
                "configured": status["motor"].get("configured"),
                "available": status["motor"].get("available"),
            },
            "laser": {
                "configured": status["laser"].get("configured"),
                "controllable": status["laser"].get("controllable"),
            },
            "calibration": {
                "camera": status["calibration"]["camera"].get("valid", False),
                "laser": status["calibration"]["laser"].get("valid", False),
                "turntable": status["calibration"]["turntable"].get("valid", False),
                "ready_for_physical_scan": status["calibration"]["ready_for_physical_scan"],
            },
            "scan": {
                "running": status["scan"]["running"],
                "phase": status["scan"]["phase"],
            },
        },
        200 if service["overall"] == "ok" else 200,
    )


@blueprint.get("/api/status")
def status():
    return api_success("Scanner status.", build_status())


@blueprint.get("/api/logs")
def logs():
    try:
        limit = int(request.args.get("limit", 120))
    except (TypeError, ValueError):
        limit = 120

    limit = max(1, min(limit, 400))

    return api_success("System logs.", {"logs": logger.get_logs(limit)})


@blueprint.get("/api/viewer/status")
def viewer():
    return api_success("Viewer capabilities.", {"viewer": viewer_status()})


@blueprint.get("/api/info")
def info():
    config = config_store.get()

    return api_success(
        "Scanner3D API.",
        {
            "name": "Scanner3D API",
            "version": APP_VERSION,
            "mode": config.mode.value,
            "routes": {
                "page": ["GET /", "GET /video"],
                "status": [
                    "GET /api/health",
                    "GET /api/status",
                    "GET /api/logs",
                    "GET /api/info",
                    "GET /api/viewer/status",
                ],
                "camera": [
                    "POST /api/camera/capture",
                    "POST /api/camera/reset",
                    "GET /api/camera/captures",
                    "POST /api/camera/captures/clear",
                    "GET /captures/<filename>",
                    "GET /captures/<session_id>/<filename>",
                ],
                "scan": [
                    "POST /api/scan/start",
                    "POST /api/scan/stop",
                    "GET /api/scan/sessions",
                    "GET /api/scan/sessions/<session_id>",
                    "DELETE /api/scan/sessions/<session_id>",
                    "POST /api/scan/sessions/<session_id>/reconstruct",
                ],
                "point_clouds": [
                    "GET /api/point-clouds",
                    "GET /api/point-clouds/data",
                    "GET /download/point-cloud",
                    "GET /download/point-cloud/<filename>",
                    "GET /download/point-cloud/<session_id>/<filename>",
                    "DELETE /api/point-clouds/<filename>",
                    "DELETE /api/point-clouds/<session_id>/<filename>",
                ],
                "settings": [
                    "GET /api/settings",
                    "PATCH /api/settings",
                    "GET /api/settings/mode",
                    "POST /api/settings/mode",
                ],
                "calibration": [
                    "GET /api/calibration",
                    "POST /api/calibration/camera",
                    "POST /api/calibration/laser",
                    "POST /api/calibration/turntable/manual",
                    "POST /api/calibration/laser/diagnose",
                    "GET /diagnostics/<run>/<filename>",
                ],
            },
        },
    )
