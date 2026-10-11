"""One process-wide lock protecting the motor during calibration and scanning."""
from threading import Lock

hardware_gate = Lock()
