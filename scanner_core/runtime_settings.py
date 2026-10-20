"""Validated, persistent scan settings.

Angular spacing is a user-facing target. The actual commanded positions are
integer half-steps distributed evenly around one full revolution.
"""
import json
import math
import os
import tempfile
import threading
from pathlib import Path

from scanner_core.config import CAPTURE_DELAY, SCAN_STEPS, SIMULATION_MODE_DEFAULT, STEP_DELAY

SETTINGS_FILE = Path("outputs/settings.json")
_settings_lock = threading.Lock()
_DEFAULTS = {
    "angle_deg": 360.0 / SCAN_STEPS,
    "scan_steps": SCAN_STEPS,
    "step_delay": STEP_DELAY,
    "capture_delay": CAPTURE_DELAY,
    "simulation_mode": SIMULATION_MODE_DEFAULT,
}


def _validate_angle(value):
    try:
        value = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError("Informe um intervalo angular numérico válido.") from None
    if not math.isfinite(value) or not 0.5 <= value <= 90.0:
        raise ValueError("Intervalo angular permitido: 0,5° a 90°.")
    return value


def _load_settings():
    result = _DEFAULTS.copy()
    try:
        values = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            return result
        if "angle_deg" in values:
            angle = _validate_angle(values["angle_deg"])
        elif "scan_steps" in values:
            steps = int(values["scan_steps"])
            angle = _validate_angle(360.0 / steps)
        else:
            angle = result["angle_deg"]
        result["angle_deg"] = angle
        result["scan_steps"] = max(4, math.ceil(360.0 / angle - 1e-9))
        for key in ("step_delay", "capture_delay"):
            v = float(values.get(key, result[key]))
            if math.isfinite(v) and 0 <= v <= 30:
                result[key] = v
    except (OSError, ValueError, TypeError, ZeroDivisionError):
        # Keep the scanner usable if an old or damaged settings file exists.
        pass
    return result


_runtime_settings = _load_settings()


def _persist(settings):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(
        prefix=".settings_", suffix=".tmp", dir=SETTINGS_FILE.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({
                k: v for k, v in settings.items() if k != "simulation_mode"
            }, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(filename, SETTINGS_FILE)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def get_settings():
    with _settings_lock:
        return _runtime_settings.copy()


def update_settings(data):
    if not isinstance(data, dict):
        return get_settings(), ["As configurações devem ser um objeto JSON."]
    errors = []
    with _settings_lock:
        candidate = _runtime_settings.copy()
        if "angle_deg" in data or "scan_steps" in data:
            try:
                if "angle_deg" in data:
                    angle = _validate_angle(data["angle_deg"])
                else:
                    count = int(data["scan_steps"])
                    if not 4 <= count <= 720:
                        raise ValueError("Quantidade de capturas deve estar entre 4 e 720.")
                    angle = 360.0 / count
                candidate["angle_deg"] = angle
                candidate["scan_steps"] = max(
                    4, math.ceil(360.0 / angle - 1e-9)
                )
            except (TypeError, ValueError) as e:
                errors.append(str(e))
        for key in ("step_delay", "capture_delay"):
            if key in data:
                try:
                    value = float(data[key])
                    if not math.isfinite(value) or not 0 <= value <= 30:
                        raise ValueError("Informe um tempo entre 0 e 30 segundos.")
                    candidate[key] = value
                except (TypeError, ValueError) as e:
                    errors.append(f"{key}: {e}")
        if errors:
            return _runtime_settings.copy(), errors
        try:
            _persist(candidate)
        except OSError as error:
            return _runtime_settings.copy(), [f"Não foi possível salvar: {error}"]
        _runtime_settings.update(candidate)
        return _runtime_settings.copy(), []


def set_simulation_mode(enabled):
    with _settings_lock:
        _runtime_settings["simulation_mode"] = bool(enabled)
        return _runtime_settings.copy()


def toggle_simulation_mode():
    with _settings_lock:
        _runtime_settings["simulation_mode"] = not _runtime_settings["simulation_mode"]
        return _runtime_settings.copy()
