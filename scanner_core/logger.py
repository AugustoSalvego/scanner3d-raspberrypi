from datetime import datetime

from scanner_core.config import MAX_LOG_LINES


logs = []


def add_log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"

    logs.insert(0, line)

    if len(logs) > MAX_LOG_LINES:
        logs.pop()


def get_logs():
    return logs.copy()