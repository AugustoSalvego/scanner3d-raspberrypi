"""Strict all-or-nothing runtime settings. Each scan takes its own snapshot."""
import copy
import math
import threading
from pathlib import Path
from scanner_core.config import OUTPUT_ROOT

_settings_lock = threading.RLock()
DEFAULT_SETTINGS = {
    "mode": "simulation", "simulation_mode": True, "scan_steps": 36,
    "step_delay": 0.1, "capture_delay": 0.05, "motor_step_delay": 0.002,
    "steps_per_revolution": 4096, "direction": 1, "motor_pins": [17, 27, 22, 23],
    "camera": {"device": 0, "width": 640, "height": 480, "fps": 30,
               "flush_frames": 5, "controls": {}},
    "calibration_path": str(OUTPUT_ROOT / "calibration" / "calibration.json"),
    "detector": {}, "filtering": {}, "reconstruction": {},
}
_runtime_settings = copy.deepcopy(DEFAULT_SETTINGS)

def number(value, name, minimum, maximum, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be finite and between {minimum} and {maximum}")
    if integer and not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value

def validate_settings(data):
    if not isinstance(data, dict):
        raise ValueError("Settings must be a JSON object")
    unknown = set(data) - set(DEFAULT_SETTINGS)
    if unknown:
        raise ValueError(f"Unknown settings: {sorted(unknown)}")
    result = copy.deepcopy(DEFAULT_SETTINGS)
    result.update(copy.deepcopy(data))
    if type(result["simulation_mode"]) is not bool:
        raise ValueError("simulation_mode must be a JSON boolean")
    if result["mode"] not in ("simulation", "physical", "offline"):
        raise ValueError("mode must be simulation, physical or offline")
    if result["simulation_mode"] != (result["mode"] == "simulation"):
        raise ValueError("mode and simulation_mode disagree")
    for name, low, high, integer in (
        ("scan_steps", 1, 10000, True), ("steps_per_revolution", 8, 1000000, True),
        ("step_delay", 0, 60, False), ("capture_delay", 0, 60, False),
        ("motor_step_delay", 0.0005, 0.1, False),
    ):
        number(result[name], name, low, high, integer)
    if type(result["direction"]) is not int or result["direction"] not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    if result["scan_steps"] > result["steps_per_revolution"]:
        raise ValueError("scan_steps cannot exceed distinct motor positions per revolution")
    pins = result["motor_pins"]
    if not isinstance(pins, list) or len(pins) != 4 or any(type(p) is not int or not 0 <= p <= 27 for p in pins) or len(set(pins)) != 4:
        raise ValueError("motor_pins must contain four distinct BCM integers between 0 and 27")
    camera = result["camera"]
    if not isinstance(camera, dict):
        raise ValueError("camera must be an object")
    camera_defaults = DEFAULT_SETTINGS["camera"]
    if set(camera) - set(camera_defaults):
        raise ValueError("Unknown camera settings")
    camera = {**copy.deepcopy(camera_defaults), **camera}
    device = camera["device"]
    if not ((type(device) is int and 0 <= device <= 20) or (isinstance(device, str) and device.startswith("/dev/video") and device[10:].isdigit())):
        raise ValueError("camera.device must be an index or /dev/videoN")
    number(camera["width"], "camera.width", 64, 4096, True)
    number(camera["height"], "camera.height", 64, 3072, True)
    number(camera["fps"], "camera.fps", 1, 120)
    number(camera["flush_frames"], "camera.flush_frames", 1, 120, True)
    if not isinstance(camera["controls"], dict):
        raise ValueError("camera.controls must be an object")
    for key, value in camera["controls"].items():
        if key not in ("exposure", "focus", "auto_exposure", "autofocus", "brightness", "contrast", "gain"):
            raise ValueError(f"Unknown camera control: {key}")
        number(value, f"camera.controls.{key}", -100000, 100000)
    result["camera"] = camera
    if not isinstance(result["calibration_path"], str) or not result["calibration_path"].strip():
        raise ValueError("calibration_path must be a nonempty string")
    from scanner_core.laser import validate_detector_settings
    from scanner_core.point_cloud import validate_filter_settings, validate_reconstruction_settings
    result["detector"] = validate_detector_settings(result["detector"])
    result["filtering"] = validate_filter_settings(result["filtering"])
    result["reconstruction"] = validate_reconstruction_settings(result["reconstruction"])
    return result

def get_settings():
    with _settings_lock:
        return copy.deepcopy(_runtime_settings)

def update_settings(data):
    with _settings_lock:
        try:
            if not isinstance(data, dict):
                raise ValueError("Settings must be a JSON object")
            candidate = copy.deepcopy(_runtime_settings)
            candidate.update(copy.deepcopy(data))
            if "mode" in data and "simulation_mode" not in data:
                candidate["simulation_mode"] = data["mode"] == "simulation"
            if "simulation_mode" in data and "mode" not in data:
                if type(data["simulation_mode"]) is not bool:
                    raise ValueError("simulation_mode must be a JSON boolean")
                candidate["mode"] = "simulation" if data["simulation_mode"] else "physical"
            candidate = validate_settings(candidate)
        except (ValueError, TypeError, KeyError) as error:
            return get_settings(), [str(error)]
        _runtime_settings.clear()
        _runtime_settings.update(candidate)
        return get_settings(), []

def set_simulation_mode(enabled):
    result, errors = update_settings({"simulation_mode": enabled})
    if errors:
        raise ValueError("; ".join(errors))
    return result

def toggle_simulation_mode():
    with _settings_lock:
        return set_simulation_mode(not _runtime_settings["simulation_mode"])
