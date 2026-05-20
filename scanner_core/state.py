from scanner_core.config import APP_VERSION
from scanner_core.config import SIMULATION_MODE_DEFAULT


scanner_state = {
    "version": APP_VERSION,
    "running": False,
    "motor_position": 0,
    "frames_captured": 0,
    "last_capture": None,
    "point_cloud_generated": False,
    "last_point_cloud": None,
    "last_scan": None,
    "last_error": None,
    "simulation_mode": SIMULATION_MODE_DEFAULT,
    "current_session": None,
    "last_session": None,
}