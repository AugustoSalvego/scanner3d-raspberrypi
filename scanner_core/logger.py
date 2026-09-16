from collections import deque
from datetime import datetime
from threading import Lock
from scanner_core.config import MAX_LOG_LINES

_logs = deque(maxlen=MAX_LOG_LINES)
_lock = Lock()

def add_log(message):
    with _lock:
        _logs.appendleft(f"[{datetime.now():%H:%M:%S}] {message}")

def get_logs():
    with _lock:
        return list(_logs)
