"""Point cloud listing, download, deletion and viewer data feed."""

from __future__ import annotations

import struct
from pathlib import Path

from flask import Blueprint, Response, request, send_file

from scanner_core import logger, paths
from scanner_core.errors import UnsafePathError
from scanner_core.point_cloud import read_ply
from scanner_core.state import state_store
from web_interface.api_response import api_error, api_success, error_from_exception
from web_interface.files import list_point_clouds, safe_point_cloud_path

blueprint = Blueprint("clouds", __name__)

#: Above this the browser gets a decimated cloud.  A Raspberry Pi serving a
#: multi-million point file to a phone helps nobody.
DEFAULT_VIEWER_LIMIT = 300_000
MAX_VIEWER_LIMIT = 2_000_000


@blueprint.get("/api/point-clouds")
def index():
    return api_success("Point clouds.", {"files": list_point_clouds()})


def _resolve(filename: str, session_id: str | None):
    return safe_point_cloud_path(filename, session_id=session_id)


@blueprint.get("/download/point-cloud")
def download_latest():
    latest = state_store.snapshot().get("last_point_cloud")

    if not latest:
        return api_error(
            "No point cloud has been generated yet. Run a scan first.", 404
        )

    path = Path(latest).resolve()

    # The path comes from our own state, but keeping the check makes the rule
    # "nothing outside outputs/ is ever served" hold for every download route.
    outputs_root = paths.OUTPUTS_ROOT.resolve()

    if outputs_root not in path.parents:
        return api_error("The last point cloud is outside the outputs folder.", 400)

    if not path.exists():
        return api_error("The last point cloud file is no longer on disk.", 404)

    return send_file(path, as_attachment=True, download_name=path.name)


@blueprint.get("/download/point-cloud/<filename>")
def download(filename: str):
    try:
        path = _resolve(filename, None)
    except UnsafePathError as error:
        return error_from_exception(error)

    if not path.exists():
        return api_error("Point cloud not found.", 404)

    return send_file(path, as_attachment=True, download_name=path.name)


@blueprint.get("/download/point-cloud/<session_id>/<filename>")
def download_from_session(session_id: str, filename: str):
    try:
        path = _resolve(filename, session_id)
    except UnsafePathError as error:
        return error_from_exception(error)

    if not path.exists():
        return api_error("Point cloud not found.", 404)

    return send_file(path, as_attachment=True, download_name=path.name)


@blueprint.delete("/api/point-clouds/<filename>")
def delete(filename: str):
    return _delete(filename, None)


@blueprint.delete("/api/point-clouds/<session_id>/<filename>")
def delete_from_session(session_id: str, filename: str):
    return _delete(filename, session_id)


def _delete(filename: str, session_id: str | None):
    try:
        path = _resolve(filename, session_id)
    except UnsafePathError as error:
        return error_from_exception(error)

    if not path.exists():
        return api_error("Point cloud not found.", 404)

    try:
        path.unlink()
    except OSError as error:
        logger.error(f"Could not delete {path.name}: {error}")

        return api_error("The point cloud could not be deleted.", 500)

    logger.info(f"Deleted point cloud {path.name}.")

    return api_success("Point cloud deleted.", {"file": path.name})


@blueprint.get("/api/point-clouds/data")
def viewer_data():
    """Binary feed for the browser viewer.

    Layout, little-endian::

        uint32  point_count
        uint32  flags            (bit 0: RGB present)
        float32 xyz[point_count * 3]
        uint8   rgb[point_count * 3]   (only when bit 0 is set)

    Sending typed arrays instead of JSON keeps a 300k point cloud around 3.6 MB
    instead of roughly 20 MB of text that the browser then has to parse.
    """

    filename = request.args.get("file", "")
    session_id = request.args.get("session") or None

    if not filename:
        return api_error("A 'file' parameter is required.", 400)

    try:
        limit = int(request.args.get("max_points", DEFAULT_VIEWER_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_VIEWER_LIMIT

    limit = max(1000, min(limit, MAX_VIEWER_LIMIT))

    try:
        path = _resolve(filename, session_id)
    except UnsafePathError as error:
        return error_from_exception(error)

    if not path.exists():
        return api_error("Point cloud not found.", 404)

    try:
        cloud = read_ply(path)
    except Exception as error:  # noqa: BLE001 - translated for the client
        return error_from_exception(error)

    if cloud.is_empty:
        return api_error("This point cloud has no points.", 422)

    points = cloud.points
    colors = cloud.colors

    total = len(points)

    if total > limit:
        # Uniform stride keeps the shape of the object instead of cropping it.
        stride = (total // limit) + 1
        points = points[::stride]

        if colors is not None:
            colors = colors[::stride]

    count = len(points)
    flags = 1 if colors is not None else 0

    payload = bytearray()
    payload += struct.pack("<II", count, flags)
    payload += points.astype("<f4").tobytes()

    if colors is not None:
        payload += colors.astype("u1").tobytes()

    response = Response(bytes(payload), mimetype="application/octet-stream")
    response.headers["X-Point-Count"] = str(count)
    response.headers["X-Point-Count-Total"] = str(total)
    response.headers["Cache-Control"] = "no-store"

    return response
