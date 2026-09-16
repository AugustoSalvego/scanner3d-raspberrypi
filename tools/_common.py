"""Shared helpers for the command line tools.

Every tool is meant to be run as a module from the project root::

    python -m tools.test_camera

so that ``scanner_core`` imports work without any PYTHONPATH juggling.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from scanner_core import logger, paths
from scanner_core.config import ScannerConfig, config_store
from scanner_core.errors import ScannerError


def bootstrap(verbose: bool = False) -> ScannerConfig:
    """Prepare the output tree, logging and configuration for a CLI run."""

    paths.refresh_roots()
    paths.ensure_directories()

    logger.configure_logging("DEBUG" if verbose else "INFO")

    handler = _console_handler()

    if handler is not None:
        logger.get_logger().addHandler(handler)

    try:
        return config_store.load()
    except (OSError, ValueError, ScannerError) as error:
        print(f"Warning: stored configuration could not be loaded ({error}); using defaults.")

        return config_store.get()


def _console_handler():
    """Mirror the scanner log to stdout, once."""

    import logging

    existing = [
        handler
        for handler in logger.get_logger().handlers
        if isinstance(handler, logging.StreamHandler)
        and getattr(handler, "_scanner_console", False)
    ]

    if existing:
        return None

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(levelname)-8s %(message)s"))
    handler._scanner_console = True  # type: ignore[attr-defined]

    return handler


def add_common_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Print debug level logging."
    )

    return parser


def heading(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def show(label: str, value: Any) -> None:
    print(f"  {label:<32} {value}")


def fail(message: str) -> int:
    print(f"\nERROR: {message}")

    return 1
