import threading

from scanner_core.config import CAPTURE_DELAY
from scanner_core.config import SCAN_STEPS
from scanner_core.config import SIMULATION_MODE_DEFAULT
from scanner_core.config import STEP_DELAY


_settings_lock = threading.Lock()

_runtime_settings = {
    "scan_steps": SCAN_STEPS,
    "step_delay": STEP_DELAY,
    "capture_delay": CAPTURE_DELAY,
    "simulation_mode": SIMULATION_MODE_DEFAULT,
}


def get_settings():
    with _settings_lock:
        return _runtime_settings.copy()


def update_settings(data):
    errors = []

    with _settings_lock:
        if "scan_steps" in data:
            try:
                scan_steps = int(data["scan_steps"])

                if scan_steps <= 0:
                    errors.append("Scan Steps must be greater than 0.")
                else:
                    _runtime_settings["scan_steps"] = scan_steps

            except (TypeError, ValueError):
                errors.append("Scan Steps must be a valid integer.")

        if "step_delay" in data:
            try:
                step_delay = float(data["step_delay"])

                if step_delay < 0:
                    errors.append("Step Delay cannot be negative.")
                else:
                    _runtime_settings["step_delay"] = step_delay

            except (TypeError, ValueError):
                errors.append("Step Delay must be a valid number.")

        if "capture_delay" in data:
            try:
                capture_delay = float(data["capture_delay"])

                if capture_delay < 0:
                    errors.append("Capture Delay cannot be negative.")
                else:
                    _runtime_settings["capture_delay"] = capture_delay

            except (TypeError, ValueError):
                errors.append("Capture Delay must be a valid number.")

        return _runtime_settings.copy(), errors


def set_simulation_mode(enabled):
    with _settings_lock:
        _runtime_settings["simulation_mode"] = bool(enabled)

        return _runtime_settings.copy()


def toggle_simulation_mode():
    with _settings_lock:
        _runtime_settings["simulation_mode"] = not _runtime_settings["simulation_mode"]

        return _runtime_settings.copy()
