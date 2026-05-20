import time

from scanner_core.camera import capture
from scanner_core.config import CAPTURE_DELAY
from scanner_core.config import SCAN_STEPS
from scanner_core.config import STEP_DELAY
from scanner_core.logger import add_log
from scanner_core.motor import rotate_step
from scanner_core.point_cloud import generate
from scanner_core.state import scanner_state


def execute_scan():
    if scanner_state["running"]:
        add_log("Scan already running")
        return

    scanner_state["running"] = True
    scanner_state["point_cloud_generated"] = False
    scanner_state["frames_captured"] = 0
    scanner_state["last_capture"] = None

    add_log("Scan started")

    for _ in range(SCAN_STEPS):
        if not scanner_state["running"]:
            add_log("Scan interrupted")
            scanner_state["running"] = False
            return

        rotate_step()
        time.sleep(STEP_DELAY)

        filename = capture()

        if filename:
            add_log(f"Captured {filename}")
        else:
            add_log("Capture failed during scan")

        time.sleep(CAPTURE_DELAY)

    generate()

    add_log("Scan finished")

    scanner_state["running"] = False


def stop():
    if scanner_state["running"]:
        scanner_state["running"] = False
        add_log("Stop requested")
    else:
        add_log("Scanner is not running")