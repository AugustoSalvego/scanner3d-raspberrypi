"""Flask application factory.

Run it with::

    python -m web_interface.app

Host, port and debug come from the environment so the defaults stay safe:

``SCANNER_HOST``   default ``0.0.0.0`` (reachable from the LAN, which is the
                   point of running it on a Raspberry Pi)
``SCANNER_PORT``   default ``5000``
``SCANNER_DEBUG``  default ``0``.  Debug mode exposes an interactive console to
                   anyone who can reach the port, so it is never on by default -
                   the old code shipped ``debug=True`` bound to ``0.0.0.0``.
``SCANNER_LOG_LEVEL`` default ``INFO``
"""

from __future__ import annotations

import os

from flask import Flask
from werkzeug.exceptions import HTTPException

from scanner_core import logger, paths
from scanner_core.config import APP_VERSION, config_store
from scanner_core.errors import ConfigurationError
from scanner_core.session import session_manager
from web_interface.api_response import api_error, error_from_exception
from web_interface.routes import BLUEPRINTS


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)

    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "on"}


def create_app() -> Flask:
    """Build the Flask application with everything wired up."""

    paths.refresh_roots()
    paths.ensure_directories()

    logger.configure_logging(os.environ.get("SCANNER_LOG_LEVEL", "INFO"))

    try:
        config_store.load()
    except (OSError, ValueError, ConfigurationError) as error:
        logger.warning(
            f"Stored configuration could not be loaded ({error}); using defaults."
        )

    app = Flask(__name__, static_folder="static", static_url_path="/static")

    app.config["JSON_SORT_KEYS"] = False
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    for blueprint in BLUEPRINTS:
        app.register_blueprint(blueprint)

    @app.errorhandler(HTTPException)
    def handle_http_exception(error: HTTPException):
        return api_error(error.description or error.name, error.code or 500)

    @app.errorhandler(Exception)
    def handle_exception(error: Exception):
        return error_from_exception(error)

    # A session left "running" by a crash or a power cut is not running now.
    session_manager.recover_interrupted_sessions()

    config = config_store.get()

    logger.info(
        f"Scanner3D {APP_VERSION} ready in '{config.mode.value}' mode "
        f"(outputs: {paths.OUTPUTS_ROOT})."
    )

    return app


app = create_app()


def main() -> None:
    host = os.environ.get("SCANNER_HOST", "0.0.0.0")
    port = int(os.environ.get("SCANNER_PORT", "5000"))
    debug = _env_flag("SCANNER_DEBUG", False)

    if debug:
        logger.warning(
            "SCANNER_DEBUG is on: the interactive debugger is reachable by anyone "
            "who can open this port. Never use it on a shared network."
        )

    app.run(host=host, port=port, debug=debug, threaded=True)


if __name__ == "__main__":
    main()
