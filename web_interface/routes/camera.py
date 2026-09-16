"""Live stream, manual captures and capture management."""

from __future__ import annotations

import time

from flask import Blueprint, Response, send_file

from scanner_core import logger, paths
from scanner_core.config import config_store
from scanner_core.hardware.camera import encode_jpeg
from scanner_core.pipeline import scan_controller
from web_interface.api_response import api_error, api_success, error_from_exception
from web_interface.files import describe_file, safe_capture_path

blueprint = Blueprint("camera", __name__)

#: Cap the preview frame rate so the stream cannot starve a running scan of CPU
#: on a Raspberry Pi 3.
STREAM_INTERVAL_SECONDS = 1 / 15


def _frame_generator():
    quality = config_store.get().camera.stream_quality

    while True:
        started = time.monotonic()

        frame = scan_controller.preview_frame()
        payload = encode_jpeg(frame, quality) if frame is not None else None

        if payload is None:
            time.sleep(0.25)
            continue

        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + payload + b"\r\n"

        elapsed = time.monotonic() - started
        remaining = STREAM_INTERVAL_SECONDS - elapsed

        if remaining > 0:
            time.sleep(remaining)


@blueprint.get("/video")
def video():
    return Response(
        _frame_generator(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@blueprint.post("/api/camera/capture")
def capture():
    try:
        path = scan_controller.capture_still()
    except Exception as error:  # noqa: BLE001 - translated for the client
        return error_from_exception(error)

    return api_success(
        "Image captured.",
        {"file": describe_file(path), "url": f"/captures/{path.name}"},
        201,
    )


@blueprint.post("/api/camera/reset")
def reset():
    try:
        scan_controller.release_hardware()
    except Exception as error:  # noqa: BLE001
        return error_from_exception(error)

    logger.info("Camera and hardware handles released by operator request.")

    return api_success("Camera released; it will reopen on the next frame.")


@blueprint.get("/api/camera/captures")
def list_captures():
    if not paths.CAPTURES_DIR.exists():
        return api_success("Captures.", {"captures": []})

    entries = [
        {**describe_file(path), "url": f"/captures/{path.name}"}
        for path in paths.CAPTURES_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
    ]

    entries.sort(key=lambda item: item["modified_at"], reverse=True)

    return api_success("Captures.", {"captures": entries})


@blueprint.post("/api/camera/captures/clear")
def clear_captures():
    if not paths.CAPTURES_DIR.exists():
        return api_success("Captures cleared.", {"deleted": 0})

    deleted = 0

    for path in paths.CAPTURES_DIR.iterdir():
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
            try:
                path.unlink()
                deleted += 1
            except OSError as error:
                logger.warning(f"Could not delete {path.name}: {error}")

    logger.info(f"Cleared {deleted} manual capture(s).")

    return api_success("Captures cleared.", {"deleted": deleted})


@blueprint.get("/captures/<path:filename>")
def serve_capture(filename: str):
    """Serve a manual capture, or a session capture as ``<session>/<file>``."""

    parts = filename.split("/")

    try:
        if len(parts) == 1:
            path = safe_capture_path(parts[0])
        elif len(parts) == 2:
            path = safe_capture_path(parts[1], session_id=parts[0])
        else:
            return api_error("Invalid capture path.", 400)
    except Exception as error:  # noqa: BLE001
        return error_from_exception(error)

    if not path.exists():
        return api_error("Capture not found.", 404)

    return send_file(path, mimetype="image/jpeg", conditional=True)
