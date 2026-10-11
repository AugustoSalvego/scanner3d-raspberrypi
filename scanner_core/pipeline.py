"""Acquisition loop: photos plus commanded motor positions (no fake geometry).

The physical scan acquires real photos and stores angle metadata. It refuses to
claim that the legacy synthetic PLY represents the scanned physical object.
"""
from __future__ import annotations

import time
from datetime import datetime

from scanner_core.camera import capture
from scanner_core.config import MOTOR_HALF_STEPS_PER_REV, MOTOR_PHASE_DELAY
from scanner_core.logger import add_log
from scanner_core.motor import MotorInterrupted, TurntableMotor, rotate_step
from scanner_core.point_cloud import generate
from scanner_core.runtime_settings import get_settings
from scanner_core.session import (
    add_capture_position, create_scan_session, finish_current_session,
)
from scanner_core.state import scanner_state


def _wait_and_check(seconds):
    """Settling delay, interruptible with Stop Scan."""
    deadline = time.monotonic() + max(0, float(seconds))
    while time.monotonic() < deadline:
        if not scanner_state["running"]:
            raise MotorInterrupted("Stop requested while platform was settling.")
        time.sleep(min(0.05, deadline - time.monotonic()))


def _physical_capture(settings):
    captures = int(settings["scan_steps"])
    if not 4 <= captures <= 720:
        raise ValueError("For physical scans, scan_steps must be from 4 to 720.")

    # The current physical orientation becomes angular zero. Without an encoder,
    # these are commanded, open-loop positions, not measured physical angles.
    with TurntableMotor(phase_delay=MOTOR_PHASE_DELAY) as motor:
        for i in range(captures):
            if not scanner_state["running"]:
                raise MotorInterrupted("Stop Scan requested.")
            target = round(i * MOTOR_HALF_STEPS_PER_REV / captures)
            motor.move(
                target - motor.position,
                cancelled=lambda: not scanner_state["running"],
            )
            _wait_and_check(settings["step_delay"])
            filename = capture(step_number=i + 1)
            if filename is None:
                raise RuntimeError("Camera capture failed; stopping the scan.")
            angle_deg = motor.position * 360.0 / MOTOR_HALF_STEPS_PER_REV
            add_capture_position(filename, motor.position, angle_deg, physical=True)
            add_log(f"Real photo {i + 1}/{captures}: {filename}, {angle_deg:.3f} deg")
            if i + 1 < captures:
                _wait_and_check(settings["capture_delay"])

        # Complete a full turn only if the scan was not stopped.
        motor.move(
            MOTOR_HALF_STEPS_PER_REV - motor.position,
            cancelled=lambda: not scanner_state["running"],
        )

    add_log(f"Physical capture complete: {captures} photographs / one full turn.")
    add_log("Real point-cloud generation is pending camera, axis and laser calibration.")


def _simulated_capture(settings):
    count = int(settings["scan_steps"])
    if not 1 <= count <= 720:
        raise ValueError("scan_steps must be between 1 and 720.")
    for i in range(count):
        if not scanner_state["running"]:
            raise MotorInterrupted("Stop Scan requested.")
        rotate_step()
        _wait_and_check(settings["step_delay"])
        filename = capture(step_number=i + 1)
        if filename is None:
            raise RuntimeError("Camera capture failed.")
        add_capture_position(filename, i, i * 360.0 / count, physical=False)
        if i + 1 < count:
            _wait_and_check(settings["capture_delay"])
    generate()
    add_log("DEMO PLY generated; it does not represent the photographed object.")


def execute_scan():
    if scanner_state["running"]:
        add_log("Scan already running")
        return

    settings = get_settings()
    scanner_state.update({
        "running": True,
        "point_cloud_generated": False,
        "frames_captured": 0,
        "motor_position": 0,
        "last_capture": None,
        "last_point_cloud": None,
        "last_error": None,
        "current_session": None,
    })
    outcome = "error"
    session_started = False

    try:
        session = create_scan_session()
        session_started = True
        scanner_state["current_session"] = session["session_id"]
        scanner_state["last_session"] = session["session_id"]

        simulation = settings["simulation_mode"]
        add_log(f"Acquisition started in {'SIMULATION' if simulation else 'PHYSICAL'} mode")
        if simulation:
            _simulated_capture(settings)
        else:
            _physical_capture(settings)
        scanner_state["last_scan_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        outcome = "finished"
        add_log("Acquisition finished")

    except MotorInterrupted as error:
        outcome = "stopped"
        add_log(f"Acquisition stopped: {error}")

    except Exception as error:
        scanner_state["last_error"] = str(error)
        add_log(f"Acquisition failed: {error}")

    finally:
        try:
            if session_started:
                finish_current_session(status=outcome)
        finally:
            scanner_state["running"] = False
            scanner_state["current_session"] = None


def stop():
    if scanner_state["running"]:
        scanner_state["running"] = False
        add_log("Stop requested")
    else:
        add_log("Scanner is not running")
