"""Run a scan from the command line.

    python -m tools.run_scan                          # use the stored configuration
    python -m tools.run_scan --mode simulation --captures 24
    python -m tools.run_scan --mode physical --captures 120 --settle 1.2

Useful over SSH on the Raspberry Pi, where starting the web server just to press
one button is awkward.  Ctrl-C cancels cleanly: the coils are released, the
laser is switched off if it can be, and the session is closed as ``cancelled``
rather than being left half open.
"""

from __future__ import annotations

import argparse
import signal

from scanner_core.config import config_store
from scanner_core.errors import ScannerError
from scanner_core.pipeline import scan_controller
from scanner_core.session import SessionStatus
from tools._common import add_common_arguments, bootstrap, fail, heading, show


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a scan.")

    parser.add_argument("--mode", choices=("simulation", "physical"), default=None)
    parser.add_argument("--captures", type=int, default=None)
    parser.add_argument("--settle", type=float, default=None, help="Settle delay in seconds.")
    parser.add_argument(
        "--no-reconstruct",
        action="store_true",
        help="Only acquire; reconstruct later with tools.reconstruct.",
    )

    add_common_arguments(parser)

    arguments = parser.parse_args()
    bootstrap(arguments.verbose)

    patch: dict = {}

    if arguments.mode:
        patch["mode"] = arguments.mode

    scan: dict = {}

    if arguments.captures is not None:
        scan["capture_count"] = arguments.captures

    if arguments.settle is not None:
        scan["settle_delay"] = arguments.settle

    if arguments.no_reconstruct:
        scan["auto_reconstruct"] = False

    if scan:
        patch["scan"] = scan

    try:
        config = config_store.update(patch, persist=False) if patch else config_store.get()
    except ScannerError as error:
        return fail(str(error))

    heading("Scan")
    show("mode", config.mode.value)
    show("captures", config.scan.capture_count)
    show("steps per revolution", config.motor.steps_per_revolution)
    show("settle delay", f"{config.scan.settle_delay} s")
    show("auto reconstruct", config.scan.auto_reconstruct)

    def handle_interrupt(_signum, _frame):
        print("\nStop requested; finishing the current step...")

        scan_controller.request_stop()

    signal.signal(signal.SIGINT, handle_interrupt)

    print()

    try:
        outcome = scan_controller.run_scan()
    except ScannerError as error:
        return fail(str(error))
    finally:
        scan_controller.release_hardware()

    heading("Result")
    show("session", outcome.session_id)
    show("status", outcome.status.value)
    show("message", outcome.message)

    if outcome.point_cloud_path is not None:
        show("point cloud", outcome.point_cloud_path)
        show("points", outcome.point_count)

    statistics = outcome.statistics or {}

    if statistics:
        heading("Reconstruction statistics")

        for key in (
            "frames_total",
            "frames_accepted",
            "frames_rejected",
            "laser_pixels",
            "accepted_points",
            "rejected_near_parallel",
            "rejected_behind_camera",
            "rejected_out_of_depth",
            "rejected_out_of_radius",
            "rejected_out_of_height",
            "points_before_filter",
            "points_after_filter",
            "reconstruction_seconds",
        ):
            if key in statistics:
                show(key, statistics[key])

    return 0 if outcome.status is SessionStatus.COMPLETED else 1


if __name__ == "__main__":
    raise SystemExit(main())
