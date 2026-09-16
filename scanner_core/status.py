from scanner_core.runtime_settings import get_settings
from scanner_core.state import scanner_state


def get_status_badge(state):
    if state.get("last_error"):
        return "ERROR"

    if state.get("running"):
        return "SCANNING"

    if state.get("point_cloud_generated"):
        return "COMPLETED"

    return "IDLE"


def get_status():
    status = scanner_state.copy()
    settings = get_settings()

    status["scan_steps"] = settings["scan_steps"]
    status["step_delay"] = settings["step_delay"]
    status["capture_delay"] = settings["capture_delay"]
    status["simulation_mode"] = settings["simulation_mode"]
    status["status_badge"] = get_status_badge(status)

    return status