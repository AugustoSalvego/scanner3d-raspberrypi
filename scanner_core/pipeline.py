import time
from datetime import datetime

from scanner_core.camera import capture
from scanner_core.logger import add_log
from scanner_core.motor import rotate_step
from scanner_core.point_cloud import generate
from scanner_core.runtime_settings import get_settings
from scanner_core.state import scanner_state


def execute_scan():
    if scanner_state["running"]:
        add_log("Scan already running")
        return

    settings = get_settings()

    scanner_state["running"] = True
    scanner_state["point_cloud_generated"] = False
    scanner_state["frames_captured"] = 0
    scanner_state["last_capture"] = None
    scanner_state["last_error"] = None

    add_log("Scan started")
    add_log(
        "Scan settings: "
        f"{settings['scan_steps']} steps, "
        f"{settings['step_delay']}s step delay, "
        f"{settings['capture_delay']}s capture delay"
    )

    if settings["simulation_mode"]:
        add_log("Simulation mode is ON")

    try:
        for step in range(settings["scan_steps"]):
            if not scanner_state["running"]:
                add_log("Scan interrupted")
                return

            rotate_step()

            scanner_state["motor_position"] = step + 1

            time.sleep(settings["step_delay"])

            filename = capture()

            if filename:
                scanner_state["frames_captured"] += 1
                scanner_state["last_capture"] = filename

                add_log(f"Captured {filename}")
            else:
                add_log("Capture failed during scan")

            current_settings = get_settings()

            time.sleep(current_settings["capture_delay"])

        generate()

        scanner_state["last_scan"] = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        add_log("Scan finished")

    except Exception as error:
        scanner_state["last_error"] = str(error)

        add_log(f"Scan error: {error}")

    finally:
        scanner_state["running"] = False


def stop():
    if scanner_state["running"]:
        scanner_state["running"] = False
        add_log("Stop requested")
    else:
        add_log("Scanner is not running")