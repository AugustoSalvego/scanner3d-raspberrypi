"""Rebuild a point cloud from a stored scan session, without moving anything.

    python -m tools.reconstruct --list
    python -m tools.reconstruct scan_20260916_142233_a91f0c
    python -m tools.reconstruct scan_... --voxel 0.3 --min-confidence 0.25

This is the loop that makes tuning practical: capture once, then iterate on the
detector thresholds or a fresh calibration and rebuild the cloud as many times
as needed.

The platform angles come from the session metadata, never from the number of
images.  A scan that was cancelled after 40 of 120 captures reconstructs as the
110 degrees it really covered, not as a full revolution squeezed into 40 frames.
"""

from __future__ import annotations

import argparse

from scanner_core.config import config_store
from scanner_core.errors import ScannerError
from scanner_core.pipeline import scan_controller
from scanner_core.session import session_manager
from tools._common import add_common_arguments, bootstrap, fail, heading, show


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconstruct a stored scan session.")

    parser.add_argument("session", nargs="?", help="Session id to reconstruct.")
    parser.add_argument("--list", action="store_true", help="List sessions and exit.")
    parser.add_argument("--voxel", type=float, default=None, help="Voxel size in mm.")
    parser.add_argument("--min-confidence", type=float, default=None)
    parser.add_argument("--saturation-min", type=int, default=None)
    parser.add_argument("--value-min", type=int, default=None)

    add_common_arguments(parser)

    arguments = parser.parse_args()
    bootstrap(arguments.verbose)

    if arguments.list or not arguments.session:
        sessions = session_manager.list_sessions(limit=30)

        if not sessions:
            print("No scan sessions found.")

            return 0

        heading("Scan sessions")

        for session in sessions:
            show(
                str(session["session_id"]),
                f"{session['status']:<10} {session['mode']:<11} "
                f"{session['capture_count']:>4} captures  "
                f"{session.get('created_at', '')}",
            )

        if not arguments.session:
            print("\nPass a session id to reconstruct it.")

        return 0

    patch: dict = {}

    filtering: dict = {}
    detector: dict = {}

    if arguments.voxel is not None:
        filtering["voxel_size_mm"] = arguments.voxel

    if arguments.min_confidence is not None:
        detector["min_confidence"] = arguments.min_confidence

    if arguments.saturation_min is not None:
        detector["saturation_min"] = arguments.saturation_min

    if arguments.value_min is not None:
        detector["value_min"] = arguments.value_min

    if filtering:
        patch["filtering"] = filtering

    if detector:
        patch["detector"] = detector

    try:
        config = config_store.update(patch, persist=False) if patch else config_store.get()
    except ScannerError as error:
        return fail(str(error))

    heading("Reconstruction settings")
    show("session", arguments.session)
    show("voxel size", f"{config.filtering.voxel_size_mm} mm")
    show("min confidence", config.detector.min_confidence)
    show("saturation min", config.detector.saturation_min)
    show("value min", config.detector.value_min)

    print()

    try:
        outcome = scan_controller.reconstruct_existing_session(
            arguments.session, config=config
        )
    except ScannerError as error:
        return fail(str(error))

    heading("Result")
    show("point cloud", outcome.point_cloud_path)
    show("points", outcome.point_count)

    statistics = outcome.statistics or {}

    if statistics:
        heading("Statistics")

        for key, value in statistics.items():
            if isinstance(value, (int, float, str, bool)):
                show(key, value)

        cloud = statistics.get("cloud") or {}

        if cloud.get("bounds_mm"):
            show("size (mm)", cloud.get("size_mm"))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
