"""Project paths are independent of the shell's working directory."""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = Path(os.environ.get("SCANNER_OUTPUT_ROOT", PROJECT_ROOT / "outputs")).resolve()
APP_VERSION = "0.3.0"
SCAN_STEPS = 36
STEP_DELAY = 0.1
CAPTURE_DELAY = 0.05
CAMERA_INDEX = 0
OUTPUT_FOLDER = str(OUTPUT_ROOT / "captures")
POINT_CLOUD_FOLDER = str(OUTPUT_ROOT / "point_clouds")
SCANS_FOLDER = str(OUTPUT_ROOT / "scans")
POINT_CLOUD_FILE = "scan_result.ply"
LASER_THRESHOLD = 180
MAX_LOG_LINES = 100
SIMULATION_MODE_DEFAULT = True
