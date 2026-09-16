"""Composite scanner status.

``/health`` used to answer "online" purely because Flask was answering, which
told the operator nothing.  The status built here separates the concerns that
can fail independently:

* ``service`` - the web process itself;
* ``mode`` - simulation, offline or physical;
* ``camera`` / ``motor`` / ``laser`` - device availability;
* ``calibration`` - whether the geometry needed for a metric scan is present
  *and valid*;
* ``scan`` - what the pipeline is doing right now.

The overall verdict is ``degraded`` whenever something the current mode needs is
missing, so a red badge on the dashboard means something real.
"""

from __future__ import annotations

from typing import Any

from scanner_core.calibration.store import CalibrationStore, calibration_store
from scanner_core.config import APP_VERSION, ConfigStore, ScannerMode, config_store
from scanner_core.pipeline import ScanController, scan_controller
from scanner_core.state import ScanPhase, StateStore, state_store


def build_status(
    *,
    configuration: ConfigStore | None = None,
    state: StateStore | None = None,
    calibration: CalibrationStore | None = None,
    controller: ScanController | None = None,
) -> dict[str, Any]:
    """Assemble the full status document."""

    configuration = configuration or config_store
    state = state or state_store
    calibration = calibration or calibration_store
    controller = controller or scan_controller

    config = configuration.get()
    scan_state = state.snapshot()

    hardware = controller.hardware_status()

    if config.mode is ScannerMode.SIMULATION:
        # Simulation carries its own exact geometry; the stored calibration is
        # irrelevant to it and must not be reported as a blocker.
        calibration_status: dict[str, Any] = {
            "camera": {"present": True, "valid": True, "source": "synthetic", "issues": []},
            "laser": {"present": True, "valid": True, "source": "synthetic", "issues": []},
            "turntable": {"present": True, "valid": True, "source": "synthetic", "issues": []},
            "ready_for_physical_scan": False,
            "blocking_issues": [],
            "uses_synthetic_profile": True,
            "note": "Simulation mode uses the exact geometry of the virtual rig.",
        }
        calibration_ready = True
    else:
        calibration_status = calibration.load_bundle().to_status()
        calibration_ready = bool(calibration_status["ready_for_physical_scan"])

    can_scan, scan_blockers = _scan_readiness(
        config.mode, calibration_ready, calibration_status, controller
    )

    phase = scan_state["phase"]

    problems: list[str] = []

    if config.mode is ScannerMode.PHYSICAL and not calibration_ready:
        problems.append("Calibration is incomplete for physical scanning.")

    if phase == ScanPhase.ERROR.value and scan_state.get("last_error"):
        problems.append(str(scan_state["last_error"]))

    return {
        "service": {
            "status": "online",
            "version": APP_VERSION,
            "overall": "degraded" if problems else "ok",
            "problems": problems,
        },
        "mode": config.mode.value,
        "camera": hardware["camera"],
        "motor": hardware["motor"],
        "laser": hardware["laser"],
        "calibration": calibration_status,
        "scan": {
            **scan_state,
            "can_start": can_scan,
            "blocking_issues": scan_blockers,
            "capture_count_setting": config.scan.capture_count,
            "steps_per_revolution": config.motor.steps_per_revolution,
        },
        "badge": _badge(phase, problems),
    }


def _scan_readiness(
    mode: ScannerMode,
    calibration_ready: bool,
    calibration_status: dict[str, Any],
    controller: ScanController,
) -> tuple[bool, list[str]]:
    blockers: list[str] = []

    if mode is ScannerMode.OFFLINE:
        blockers.append(
            "Offline mode only reconstructs existing sessions. Switch to simulation "
            "or physical mode to acquire a new scan."
        )

    if mode is ScannerMode.PHYSICAL and not calibration_ready:
        blockers.extend(calibration_status.get("blocking_issues", []))

    if controller.is_running:
        blockers.append("A scan is already running.")

    return not blockers, blockers


def _badge(phase: str, problems: list[str]) -> str:
    """Short label for the dashboard header."""

    mapping = {
        ScanPhase.IDLE.value: "IDLE",
        ScanPhase.PREPARING.value: "PREPARING",
        ScanPhase.SCANNING.value: "SCANNING",
        ScanPhase.RECONSTRUCTING.value: "RECONSTRUCTING",
        ScanPhase.COMPLETED.value: "COMPLETED",
        ScanPhase.CANCELLED.value: "CANCELLED",
        ScanPhase.ERROR.value: "ERROR",
    }

    badge = mapping.get(phase, "IDLE")

    if badge in {"IDLE", "COMPLETED"} and problems:
        return "NEEDS SETUP"

    return badge


def viewer_status() -> dict[str, Any]:
    """Capabilities of the built-in browser point cloud viewer."""

    return {
        "available": True,
        "renderer": "webgl",
        "bundled": True,
        "requires_internet": False,
        "supported_formats": ["ply"],
        "features": [
            "point_cloud_rendering",
            "orbit_rotate",
            "zoom",
            "pan",
            "reset_camera",
            "point_size",
            "axes_and_grid",
            "session_selection",
            "colour_by_height_or_rgb",
        ],
    }
