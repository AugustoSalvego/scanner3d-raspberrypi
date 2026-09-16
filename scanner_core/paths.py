"""Filesystem layout and safe path resolution.

All generated data lives under a single project ``outputs`` root so the
repository only ever stores source code and documentation.  Every path that is
built from user or HTTP input must go through :func:`resolve_within` so a
crafted name such as ``../../etc/passwd`` can never escape its directory.
"""

from __future__ import annotations

import os
from pathlib import Path

from scanner_core.errors import UnsafePathError

#: Repository root (the directory that contains ``scanner_core``).
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _root_from_env(variable: str, default: Path) -> Path:
    value = os.environ.get(variable)

    if not value:
        return default

    return Path(value).expanduser().resolve()


#: Root for every generated artefact.  Overridable so tests never touch the
#: developer's real output tree.
OUTPUTS_ROOT = _root_from_env("SCANNER_OUTPUTS", PROJECT_ROOT / "outputs")

CAPTURES_DIR = OUTPUTS_ROOT / "captures"
POINT_CLOUDS_DIR = OUTPUTS_ROOT / "point_clouds"
SCANS_DIR = OUTPUTS_ROOT / "scans"
CALIBRATION_DIR = OUTPUTS_ROOT / "calibration"
CALIBRATION_IMAGES_DIR = CALIBRATION_DIR / "images"
DIAGNOSTICS_DIR = OUTPUTS_ROOT / "diagnostics"
LOGS_DIR = OUTPUTS_ROOT / "logs"
CONFIG_DIR = OUTPUTS_ROOT / "config"

CONFIG_FILE = CONFIG_DIR / "scanner_config.json"
LOG_FILE = LOGS_DIR / "scanner.log"

#: Directories created eagerly at application start-up.
MANAGED_DIRECTORIES = (
    OUTPUTS_ROOT,
    CAPTURES_DIR,
    POINT_CLOUDS_DIR,
    SCANS_DIR,
    CALIBRATION_DIR,
    CALIBRATION_IMAGES_DIR,
    DIAGNOSTICS_DIR,
    LOGS_DIR,
    CONFIG_DIR,
)


def ensure_directories() -> None:
    """Create every managed output directory if it does not exist yet."""

    for directory in MANAGED_DIRECTORIES:
        directory.mkdir(parents=True, exist_ok=True)


def refresh_roots() -> None:
    """Re-read ``SCANNER_OUTPUTS`` and rebuild every derived path.

    Only needed by the test-suite, which points the output tree at a temporary
    directory after the module has already been imported.
    """

    global OUTPUTS_ROOT, CAPTURES_DIR, POINT_CLOUDS_DIR, SCANS_DIR
    global CALIBRATION_DIR, CALIBRATION_IMAGES_DIR, DIAGNOSTICS_DIR
    global LOGS_DIR, CONFIG_DIR, CONFIG_FILE, LOG_FILE, MANAGED_DIRECTORIES

    OUTPUTS_ROOT = _root_from_env("SCANNER_OUTPUTS", PROJECT_ROOT / "outputs")

    CAPTURES_DIR = OUTPUTS_ROOT / "captures"
    POINT_CLOUDS_DIR = OUTPUTS_ROOT / "point_clouds"
    SCANS_DIR = OUTPUTS_ROOT / "scans"
    CALIBRATION_DIR = OUTPUTS_ROOT / "calibration"
    CALIBRATION_IMAGES_DIR = CALIBRATION_DIR / "images"
    DIAGNOSTICS_DIR = OUTPUTS_ROOT / "diagnostics"
    LOGS_DIR = OUTPUTS_ROOT / "logs"
    CONFIG_DIR = OUTPUTS_ROOT / "config"

    CONFIG_FILE = CONFIG_DIR / "scanner_config.json"
    LOG_FILE = LOGS_DIR / "scanner.log"

    MANAGED_DIRECTORIES = (
        OUTPUTS_ROOT,
        CAPTURES_DIR,
        POINT_CLOUDS_DIR,
        SCANS_DIR,
        CALIBRATION_DIR,
        CALIBRATION_IMAGES_DIR,
        DIAGNOSTICS_DIR,
        LOGS_DIR,
        CONFIG_DIR,
    )


def is_safe_component(name: str) -> bool:
    """Return ``True`` when ``name`` is a single, harmless path component.

    Rejects empty strings, separators, drive letters, ``.``/``..`` and NUL
    bytes.  This is the first gate before any filesystem access.
    """

    if not name or len(name) > 255:
        return False

    if name in {".", ".."}:
        return False

    if "\x00" in name:
        return False

    if "/" in name or "\\" in name:
        return False

    # ``C:file`` style relative-drive paths and NTFS alternate data streams.
    if ":" in name:
        return False

    return True


def resolve_within(root: Path, *components: str, suffix: str | None = None) -> Path:
    """Resolve ``components`` under ``root``, refusing anything that escapes it.

    Args:
        root: Directory the result must stay inside.
        components: Individual path components supplied by the caller.
        suffix: Optional required file extension, compared case-insensitively.

    Raises:
        UnsafePathError: If a component is unsafe or the resolved path would
            land outside ``root``.
    """

    if not components:
        raise UnsafePathError("No path component supplied.")

    for component in components:
        if not is_safe_component(component):
            raise UnsafePathError(f"Invalid path component: {component!r}")

    if suffix is not None:
        if not components[-1].lower().endswith(suffix.lower()):
            raise UnsafePathError(f"Expected a {suffix} file, got {components[-1]!r}")

    root_resolved = root.resolve()
    candidate = root_resolved.joinpath(*components)

    # ``strict=False`` keeps this usable for files that do not exist yet.
    resolved = candidate.resolve(strict=False)

    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise UnsafePathError("Resolved path escapes its allowed root.")

    return resolved


def relative_to_outputs(path: Path) -> str:
    """Return ``path`` relative to the outputs root using forward slashes."""

    try:
        return path.resolve().relative_to(OUTPUTS_ROOT.resolve()).as_posix()
    except ValueError:
        return path.name
