"""Shared helpers for serving generated files safely.

Every filename that arrives over HTTP is resolved through
:func:`scanner_core.paths.resolve_within`, which rejects separators, ``..``,
absolute paths, drive letters and anything that resolves outside its allowed
root.  Extension checks are applied on top, so ``/captures/<name>`` can only
ever serve an image and the point cloud routes can only ever serve ``.ply``.

The old code only guarded the PLY routes and served ``/captures/<filename>``
straight to ``send_from_directory``, which was a real traversal hole.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from scanner_core import paths
from scanner_core.errors import UnsafePathError

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
POINT_CLOUD_SUFFIX = ".ply"


def safe_point_cloud_path(filename: str, *, session_id: str | None = None) -> Path:
    """Resolve a ``.ply`` inside the shared folder or inside one session."""

    if session_id is None:
        return paths.resolve_within(paths.POINT_CLOUDS_DIR, filename, suffix=POINT_CLOUD_SUFFIX)

    session_root = paths.resolve_within(paths.SCANS_DIR, session_id)

    return paths.resolve_within(
        session_root / "point_clouds", filename, suffix=POINT_CLOUD_SUFFIX
    )


def safe_capture_path(filename: str, *, session_id: str | None = None) -> Path:
    """Resolve a capture image inside the shared folder or inside one session."""

    if session_id is None:
        resolved = paths.resolve_within(paths.CAPTURES_DIR, filename)
    else:
        session_root = paths.resolve_within(paths.SCANS_DIR, session_id)
        resolved = paths.resolve_within(session_root / "captures", filename)

    if resolved.suffix.lower() not in IMAGE_SUFFIXES:
        raise UnsafePathError(f"{filename!r} is not an image this scanner serves.")

    return resolved


def safe_diagnostic_path(*components: str) -> Path:
    """Resolve a laser diagnostics image."""

    resolved = paths.resolve_within(paths.DIAGNOSTICS_DIR, *components)

    if resolved.suffix.lower() not in IMAGE_SUFFIXES:
        raise UnsafePathError(f"{components[-1]!r} is not an image this scanner serves.")

    return resolved


def describe_file(path: Path, *, session_id: str | None = None) -> dict[str, Any]:
    """Metadata row for a file listing."""

    stat = path.stat()

    return {
        "name": path.name,
        "session_id": session_id,
        "size_kb": round(stat.st_size / 1024, 2),
        "modified_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    }


def list_point_clouds() -> list[dict[str, Any]]:
    """Every generated point cloud, from the shared folder and every session.

    Session output stays organised in its own folder on disk; this listing is
    what unifies the two places for the dashboard, tagging each row with the
    session it belongs to (or ``None`` for a manual generation).
    """

    entries: list[dict[str, Any]] = []

    if paths.POINT_CLOUDS_DIR.exists():
        for path in paths.POINT_CLOUDS_DIR.iterdir():
            if path.is_file() and path.suffix.lower() == POINT_CLOUD_SUFFIX:
                entries.append(describe_file(path))

    if paths.SCANS_DIR.exists():
        for session_dir in paths.SCANS_DIR.iterdir():
            clouds = session_dir / "point_clouds"

            if not clouds.is_dir():
                continue

            for path in clouds.iterdir():
                if path.is_file() and path.suffix.lower() == POINT_CLOUD_SUFFIX:
                    entries.append(describe_file(path, session_id=session_dir.name))

    entries.sort(key=lambda item: item["modified_at"], reverse=True)

    return entries
