"""Uniform JSON envelope for every API route.

Every response - success or failure - has exactly three keys::

    {"success": true, "message": "...", "data": {...}}

The duplicated top-level compatibility fields the old API carried (``files``,
``logs``, ``settings``, ``scanner_status``...) are gone: the dashboard reads
``data`` and nothing else, so there is only one shape to keep working.
"""

from __future__ import annotations

from typing import Any

from flask import jsonify
from werkzeug.wrappers import Response

from scanner_core import logger
from scanner_core.errors import ScannerError


def api_success(
    message: str = "Success",
    data: dict[str, Any] | None = None,
    status_code: int = 200,
) -> tuple[Response, int]:
    return jsonify({"success": True, "message": message, "data": data or {}}), status_code


def api_error(
    message: str = "Error",
    status_code: int = 400,
    data: dict[str, Any] | None = None,
) -> tuple[Response, int]:
    return jsonify({"success": False, "message": message, "data": data or {}}), status_code


def error_from_exception(error: Exception) -> tuple[Response, int]:
    """Translate a domain exception into a safe response.

    Domain errors carry a message written for the operator and a sensible
    status code.  Anything else is logged in full and reported generically:
    a traceback in the browser helps an attacker far more than it helps the
    person using the scanner.
    """

    if isinstance(error, ScannerError):
        logger.warning(f"{type(error).__name__}: {error}")

        return api_error(str(error), error.status_code)

    logger.error(f"Unhandled error: {error}", exc_info=True)

    return api_error(
        "The scanner hit an unexpected internal error. Check the system logs for details.",
        500,
    )
