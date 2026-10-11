APP_VERSION = "0.2.0"

SCAN_STEPS = 36
STEP_DELAY = 0.5
CAPTURE_DELAY = 0.5

CAMERA_INDEX = 0

OUTPUT_FOLDER = "outputs/captures"

POINT_CLOUD_FOLDER = "outputs/point_clouds"
POINT_CLOUD_FILE = "scan_result.ply"

LASER_THRESHOLD = 180

MAX_LOG_LINES = 100

SIMULATION_MODE_DEFAULT = True

SCANS_FOLDER = "outputs/scans"
# Open-loop motor calibration: VERIFY true output-shaft and turntable turns.
# 4096 is an initial estimate for a 28BYJ-48 in half-step mode, not an encoder reading.
MOTOR_HALF_STEPS_PER_REV = 4096
MOTOR_PHASE_DELAY = 0.003
