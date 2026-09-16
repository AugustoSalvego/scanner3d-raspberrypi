"""Logging for the scanner.

Two sinks are wired to the same ``scanner3d`` logger:

* a :class:`~logging.handlers.RotatingFileHandler` writing to
  ``outputs/logs/scanner.log`` so a failed physical scan can be inspected after
  the fact;
* an in-memory ring buffer that feeds the dashboard's *System Logs* panel.

The ring buffer keeps structured records (level, timestamp, message) rather
than pre-formatted strings, so the browser can colour them by severity.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any, Iterable

from scanner_core import paths

LOGGER_NAME = "scanner3d"
MAX_LOG_RECORDS = 400

_logger = logging.getLogger(LOGGER_NAME)
_configured = False
_configure_lock = threading.Lock()


class _RingBufferHandler(logging.Handler):
    """Keep the most recent records in memory for the web dashboard."""

    def __init__(self, capacity: int = MAX_LOG_RECORDS) -> None:
        super().__init__()

        self._records: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - logging must never raise
            message = "<unformattable log record>"

        entry = {
            "time": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
            "timestamp": datetime.fromtimestamp(record.created).isoformat(timespec="seconds"),
            "level": record.levelname,
            "message": message,
        }

        with self._lock:
            self._records.append(entry)

    def snapshot(self, limit: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            records = list(self._records)

        records.reverse()

        if limit is not None:
            records = records[:limit]

        return records

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


_ring_handler = _RingBufferHandler()


def configure_logging(level: int | str = logging.INFO, *, to_file: bool = True) -> logging.Logger:
    """Attach the ring buffer and (optionally) the rotating file handler.

    Safe to call more than once: handlers are only installed on the first call.
    """

    global _configured

    with _configure_lock:
        if isinstance(level, str):
            level = logging.getLevelName(level.strip().upper())

            if not isinstance(level, int):
                level = logging.INFO

        _logger.setLevel(level)
        _logger.propagate = False

        if _ring_handler not in _logger.handlers:
            _ring_handler.setLevel(logging.DEBUG)
            _logger.addHandler(_ring_handler)

        if to_file and not any(
            isinstance(handler, RotatingFileHandler) for handler in _logger.handlers
        ):
            try:
                paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)

                file_handler = RotatingFileHandler(
                    paths.LOG_FILE,
                    maxBytes=1_000_000,
                    backupCount=3,
                    encoding="utf-8",
                )
                file_handler.setLevel(logging.DEBUG)
                file_handler.setFormatter(
                    logging.Formatter(
                        "%(asctime)s %(levelname)-8s [%(threadName)s] %(message)s"
                    )
                )

                _logger.addHandler(file_handler)
            except OSError:
                # A read-only output tree must not prevent the scanner from
                # running; the dashboard ring buffer still works.
                pass

        _configured = True

        return _logger


def get_logger() -> logging.Logger:
    if not _configured:
        configure_logging()

    return _logger


def log(message: str, level: int = logging.INFO, **kwargs: Any) -> None:
    get_logger().log(level, message, **kwargs)


def debug(message: str) -> None:
    get_logger().debug(message)


def info(message: str) -> None:
    get_logger().info(message)


def warning(message: str) -> None:
    get_logger().warning(message)


def error(message: str, *, exc_info: bool = False) -> None:
    get_logger().error(message, exc_info=exc_info)


def add_log(message: str) -> None:
    """Backwards compatible alias used across the code base."""

    info(message)


def get_logs(limit: int | None = None) -> list[dict[str, Any]]:
    """Return recent log records, newest first."""

    if not _configured:
        configure_logging()

    return _ring_handler.snapshot(limit)


def get_log_lines(limit: int | None = None) -> Iterable[str]:
    """Return recent logs as ``[HH:MM:SS] LEVEL message`` strings."""

    return [
        f"[{record['time']}] {record['level']} {record['message']}"
        for record in get_logs(limit)
    ]


def clear_logs() -> None:
    _ring_handler.clear()
