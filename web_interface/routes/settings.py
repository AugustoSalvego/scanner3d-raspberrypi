"""Configuration and operating mode."""

from __future__ import annotations

from flask import Blueprint, request

from scanner_core import logger
from scanner_core.config import ScannerMode, config_store
from scanner_core.pipeline import scan_controller
from web_interface.api_response import api_error, api_success, error_from_exception

blueprint = Blueprint("settings", __name__)


@blueprint.get("/api/settings")
def read_settings():
    return api_success("Scanner configuration.", {"settings": config_store.get().to_dict()})


@blueprint.patch("/api/settings")
def update_settings():
    """Merge a partial configuration patch.

    The patch is validated against a copy first, so a rejected value leaves the
    running scanner exactly as it was.
    """

    if scan_controller.is_running:
        return api_error(
            "Settings cannot be changed while a scan is running.", 409
        )

    patch = request.get_json(silent=True)

    if not isinstance(patch, dict):
        return api_error("Send a JSON object with the settings to change.", 400)

    previous_mode = config_store.get().mode

    try:
        updated = config_store.update(patch)
    except Exception as error:  # noqa: BLE001 - translated for the client
        return error_from_exception(error)

    if updated.mode is not previous_mode:
        scan_controller.release_hardware()

    logger.info(f"Configuration updated: {sorted(patch)}")

    return api_success("Configuration updated.", {"settings": updated.to_dict()})


@blueprint.get("/api/settings/mode")
def read_mode():
    config = config_store.get()

    return api_success(
        "Operating mode.",
        {
            "mode": config.mode.value,
            "available": [mode.value for mode in ScannerMode],
        },
    )


@blueprint.post("/api/settings/mode")
def set_mode():
    if scan_controller.is_running:
        return api_error("The mode cannot be changed while a scan is running.", 409)

    payload = request.get_json(silent=True) or {}
    requested = payload.get("mode")

    if requested is None:
        return api_error("Send {\"mode\": \"simulation\" | \"offline\" | \"physical\"}.", 400)

    try:
        updated = config_store.set_mode(requested)
    except Exception as error:  # noqa: BLE001 - translated for the client
        return error_from_exception(error)

    # Devices belong to a mode; drop them so the next request rebuilds the
    # right ones instead of leaving a webcam open in simulation mode.
    scan_controller.release_hardware()

    logger.info(f"Operating mode set to '{updated.mode.value}'.")

    return api_success(f"Mode set to {updated.mode.value}.", {"mode": updated.mode.value})
