from scanner_core.logger import add_log
from scanner_core.state import scanner_state


def rotate_step():
    scanner_state["motor_position"] += 1

    add_log(
        f"Motor step -> {scanner_state['motor_position']}"
    )


def reset():
    scanner_state["motor_position"] = 0

    add_log("Motor reset")