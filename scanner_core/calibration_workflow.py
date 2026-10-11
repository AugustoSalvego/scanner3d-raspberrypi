"""Asynchronous, physically driven calibration capture and computation.

Three checkerboard poses (upright, tilted back, tilted forward) are used.
1. With the laser OFF: three motorized camera/axis sweeps.
2. With the laser ON: at least one sweep (three recommended) for laser plane.
Each run stores the photos, corner detections, step positions, and a report.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from scanner_core import camera as camera_device
from scanner_core.calibration_geometry import (
    BOARD_COLUMNS, BOARD_ROWS, CalibrationFailure,
    compute_axis, compute_camera, compute_laser_plane, find_board,
)
from scanner_core.config import MOTOR_HALF_STEPS_PER_REV, MOTOR_PHASE_DELAY
from scanner_core.hardware_gate import hardware_gate
from scanner_core.logger import add_log
from scanner_core.motor import MotorInterrupted, TurntableMotor

OUTPUT = Path("outputs/calibration")
RUN_DIR = OUTPUT / "chatgpt_version"
RUN_FILE = RUN_DIR / "run.json"
CAMERA_FILE = OUTPUT / "camera_calibration.json"
AXIS_FILE = OUTPUT / "turntable_calibration.json"
LASER_FILE = OUTPUT / "laser_calibration.json"
STOPS = 36
PHOTOS_AT_LEAST = 6

_state_lock = threading.RLock()
_stop = threading.Event()
_current = {
    "running": False, "operation": None, "placement": None,
    "done": 0, "total": 0, "board_found": 0,
    "message": "Ready", "error": None,
}


def _save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load_json(path, default=None):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as error:
        raise CalibrationFailure(f"Cannot read calibration data in {path}: {error}") from error


def _set_state(**kwargs):
    with _state_lock:
        _current.update(kwargs)


def _valid_square(value):
    try:
        square = float(value)
    except (TypeError, ValueError) as error:
        raise CalibrationFailure("Enter the measured square size in millimetres.") from error
    if not np.isfinite(square) or square < 1 or square > 100:
        raise CalibrationFailure("Measured square size must be between 1 and 100 mm.")
    return square


def calibration_status():
    with _state_lock:
        state = _current.copy()
    run = _load_json(RUN_FILE, {}) or {}
    completed = {}
    for name, record in (run.get("stages") or {}).items():
        completed[name] = {
            "status": record.get("status"),
            "images": len(record.get("frames", [])),
            "board_found": sum(1 for f in record.get("frames", []) if f.get("corners")),
            "error": record.get("error"),
            "preview_url": (
                f"/api/calibration/workflow/diagnostics/{name}"
                if (RUN_DIR / "diagnostics" / f"{name}.jpg").is_file() else None
            ),
        }
    profiles = {}
    for kind, path in (("camera", CAMERA_FILE), ("turntable", AXIS_FILE),
                       ("laser", LASER_FILE)):
        data = _load_json(path, {}) or {}
        profiles[kind] = {"calibrated": bool(data.get("calibrated")), "profile": data}
    return {
        "run": state,
        "square_size_mm": run.get("square_size_mm"),
        "board": [BOARD_COLUMNS, BOARD_ROWS],
        "stops_per_revolution": STOPS,
        "stages": completed,
        "preflight_preview_url": (
            "/api/calibration/workflow/preflight-preview"
            if (RUN_DIR / "diagnostics" / "preflight.jpg").is_file() else None
        ),
        "profiles": profiles,
    }


def stop_calibration():
    with _state_lock:
        if not _current["running"]:
            return False
    _stop.set()
    _set_state(message="Stop requested; releasing motor at the next half-step.")
    return True


def start_capture(kind, placement, square_mm):
    if kind not in ("camera", "laser") or placement not in (1, 2, 3):
        raise CalibrationFailure("Choose camera/laser and placement 1, 2, or 3.")
    square = _valid_square(square_mm)
    if not hardware_gate.acquire(blocking=False):
        raise CalibrationFailure("Scanner busy: finish the current scan or calibration first.")
    try:
        run = _load_json(RUN_FILE, {}) or {}
        if run and abs(float(run.get("square_size_mm", square)) - square) > 0.0001:
            raise CalibrationFailure(
                "Square size changed since previous captures. Use Reset calibration photos "
                "to start a new dataset, or enter the original measured size."
            )
        if kind == "laser":
            camera = _load_json(CAMERA_FILE, {}) or {}
            axis = _load_json(AXIS_FILE, {}) or {}
            if not (camera.get("calibrated") and axis.get("calibrated")):
                raise CalibrationFailure("Complete camera and rotation-axis calibration first.")
            if abs(float(camera.get("square_size_mm", 0)) - square) > 0.0001:
                raise CalibrationFailure("Laser and camera calibrations must use the same square size.")
        _stop.clear()
        _set_state(running=True, operation=f"capture_{kind}", placement=placement,
                   done=0, total=STOPS, board_found=0, error=None,
                   message=f"Capturing {kind} position {placement}...")
        thread = threading.Thread(
            target=_capture_worker, args=(kind, placement, square),
            name=f"calibration-{kind}-{placement}", daemon=True
        )
        thread.start()
    except Exception:
        hardware_gate.release()
        raise


def _capture_worker(kind, placement, square):
    key = f"{kind}{placement}"
    record = {"status": "running", "frames": [], "error": None}
    run = None
    try:
        run = _load_json(RUN_FILE, {}) or {
            "schema_version": 1,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "square_size_mm": square,
            "steps_per_revolution": MOTOR_HALF_STEPS_PER_REV,
            "board": [BOARD_COLUMNS, BOARD_ROWS],
            "stages": {},
        }
        if int(run.get("steps_per_revolution", MOTOR_HALF_STEPS_PER_REV)) != MOTOR_HALF_STEPS_PER_REV:
            raise CalibrationFailure("Motor steps/revolution changed. Reset the photo dataset.")
        run.setdefault("stages", {})[key] = record
        folder = RUN_DIR / key
        folder.mkdir(parents=True, exist_ok=True)
        for previous in folder.glob("frame_*.jpg"):
            previous.unlink()
        diag = RUN_DIR / "diagnostics"
        diag.mkdir(parents=True, exist_ok=True)
        overlay_file = diag / f"{key}.jpg"
        if overlay_file.exists():
            overlay_file.unlink()
        _save_json(RUN_FILE, run)

        # No simulated motor allowed during calibration. All captures have
        # commanded step positions saved, including the last segment to 360 deg.
        with TurntableMotor(phase_delay=MOTOR_PHASE_DELAY) as motor:
            for i in range(STOPS):
                if _stop.is_set():
                    raise MotorInterrupted("Calibration stopped by user.")
                target = round(i * MOTOR_HALF_STEPS_PER_REV / STOPS)
                motor.move(target - motor.position, cancelled=_stop.is_set)
                deadline = time.monotonic() + 0.45
                while time.monotonic() < deadline:
                    if _stop.is_set():
                        raise MotorInterrupted("Calibration stopped by user.")
                    time.sleep(0.03)
                # Avoid buffered webcam frames from the previous motor position.
                for _ in range(3):
                    camera_device.get_frame()
                frame = camera_device.get_frame()
                if frame is None:
                    raise CalibrationFailure("The USB webcam returned no frame.")
                file = folder / f"frame_{i:03d}.jpg"
                if not cv2.imwrite(str(file), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95]):
                    raise CalibrationFailure(f"Could not save {file}.")
                corners = find_board(frame)
                if corners is not None:
                    overlay = frame.copy()
                    cv2.drawChessboardCorners(
                        overlay, (BOARD_COLUMNS, BOARD_ROWS), corners, True
                    )
                    cv2.imwrite(str(overlay_file), overlay)
                record["frames"].append({
                    "filename": file.relative_to(RUN_DIR).as_posix(),
                    "angle_deg": motor.position * 360.0 / MOTOR_HALF_STEPS_PER_REV,
                    "half_steps": motor.position,
                    "image_size": [frame.shape[1], frame.shape[0]],
                    "corners": None if corners is None else np.round(
                        corners.reshape(-1, 2), 4
                    ).tolist(),
                })
                record["status"] = "running"
                _save_json(RUN_FILE, run)
                _set_state(done=i + 1, board_found=sum(
                    bool(item["corners"]) for item in record["frames"]
                ), message=f"Captured {i+1}/{STOPS} photos ({key}).")
            if not _stop.is_set():
                motor.move(MOTOR_HALF_STEPS_PER_REV - motor.position, cancelled=_stop.is_set)
        record["status"] = "complete"
        _set_state(message=f"{key}: complete. Board detected in "
                   f"{sum(bool(f['corners']) for f in record['frames'])}/{STOPS} photos.")
    except MotorInterrupted as error:
        record["status"] = "stopped"
        record["error"] = str(error)
        _set_state(message=str(error), error=str(error))
    except Exception as error:
        record["status"] = "error"
        record["error"] = str(error)
        _set_state(message=f"Calibration capture error: {error}", error=str(error))
        add_log(f"Calibration capture failed: {error}")
    finally:
        if run is not None:
            _save_json(RUN_FILE, run)
        _set_state(running=False)
        hardware_gate.release()


def _read_observations(record):
    result = []
    for item in record.get("frames", []):
        corners = item.get("corners")
        points = None if corners is None else np.asarray(corners, np.float32).reshape(-1, 1, 2)
        result.append((float(item["angle_deg"]), points))
    return result


def start_compute(kind):
    if kind not in ("camera", "laser"):
        raise CalibrationFailure("Choose camera or laser computation.")
    if not hardware_gate.acquire(blocking=False):
        raise CalibrationFailure("Scanner busy; wait before computing calibration.")
    try:
        _set_state(running=True, operation=f"compute_{kind}", placement=None,
                   done=0, total=1, board_found=0, error=None,
                   message=f"Computing {kind} calibration from saved photos...")
        thread = threading.Thread(
            target=_compute_worker, args=(kind,), daemon=True
        )
        thread.start()
    except Exception:
        hardware_gate.release()
        raise


def _compute_worker(kind):
    try:
        run = _load_json(RUN_FILE, {}) or {}
        square = float(run.get("square_size_mm", 0))
        stages = run.get("stages") or {}
        if kind == "camera":
            by_placement = {}
            all_obs = []
            expected_size = None
            for i in (1, 2, 3):
                record = stages.get(f"camera{i}") or {}
                if record.get("status") != "complete":
                    raise CalibrationFailure(f"Camera position {i} is incomplete.")
                observations = _read_observations(record)
                by_placement[str(i)] = [(a, pts) for a, pts in observations if pts is not None]
                all_obs.extend(observations)
                sizes = {tuple(f["image_size"]) for f in record["frames"]}
                if len(sizes) != 1:
                    raise CalibrationFailure("Camera image resolution changed mid-calibration.")
                size = sizes.pop()
                if expected_size and size != expected_size:
                    raise CalibrationFailure("Resolution differs across the three positions.")
                expected_size = size
            camera = compute_camera(all_obs, square, expected_size)
            axis = compute_axis(by_placement, square, camera)
            axis["square_size_mm"] = square
            axis["steps_per_revolution"] = int(run["steps_per_revolution"])
            # A new camera/axis profile changes the reference geometry.
            # Never leave an old laser calibration marked READY after this.
            _save_json(CAMERA_FILE, camera)
            _save_json(AXIS_FILE, axis)
            previous_laser = _load_json(LASER_FILE, {}) or {}
            if previous_laser.get("calibrated"):
                previous_laser["calibrated"] = False
                previous_laser["invalidated_reason"] = (
                    "Camera/axis calibration changed; recalibrate the laser plane."
                )
                _save_json(LASER_FILE, previous_laser)
            _set_state(done=1, message=(
                f"Camera calibrated (RMS {camera['rms_reprojection_px']:.2f} px); "
                f"axis calibrated. Laser calibration is next."
            ))
            add_log("Camera and axis calibration saved.")
        else:
            camera = _load_json(CAMERA_FILE, {}) or {}
            axis = _load_json(AXIS_FILE, {}) or {}
            if not (camera.get("calibrated") and axis.get("calibrated")):
                raise CalibrationFailure("Calibrate camera and turntable before laser.")
            photos = []
            complete_stages = 0
            for i in (1, 2, 3):
                record = stages.get(f"laser{i}") or {}
                if record.get("status") != "complete":
                    continue
                complete_stages += 1
                for entry in record.get("frames", []):
                    if entry.get("corners") is None:
                        continue
                    file = RUN_DIR / entry["filename"]
                    # The filenames are generated by this workflow, not HTTP input.
                    frame = cv2.imread(str(file), cv2.IMREAD_COLOR)
                    if frame is None:
                        raise CalibrationFailure(f"Missing laser image: {file}.")
                    if [frame.shape[1], frame.shape[0]] != camera["image_size"]:
                        raise CalibrationFailure("Laser photos use a different camera resolution.")
                    corners = np.asarray(entry["corners"], np.float32).reshape(-1, 1, 2)
                    photos.append((frame, corners, entry["angle_deg"]))
            if not complete_stages:
                raise CalibrationFailure("Capture at least one laser sweep first.")
            laser = compute_laser_plane(photos, square, camera)
            laser["square_size_mm"] = square
            _save_json(LASER_FILE, laser)
            _set_state(done=1, message=(
                f"Laser calibrated with {laser['frames_used']} photos; "
                f"plane RMS {laser['rms_plane_mm']:.2f} mm."
            ))
            add_log("Laser plane calibration saved.")
    except Exception as error:
        _set_state(error=str(error), message=f"Calibration rejected: {error}")
        add_log(f"Calibration rejected: {error}")
    finally:
        _set_state(running=False)
        hardware_gate.release()


def reset_photos():
    if not hardware_gate.acquire(blocking=False):
        raise CalibrationFailure("Cannot reset while scanning or calibrating.")
    try:
        # Preserve saved calibration profiles; remove only working photo dataset.
        # Files are not automatically deleted on calibration failure.
        if RUN_DIR.exists():
            import shutil
            shutil.rmtree(RUN_DIR)
        _set_state(running=False, done=0, total=0, error=None,
                   message="Calibration photographs reset. Saved profiles remain on disk.")
    finally:
        hardware_gate.release()
