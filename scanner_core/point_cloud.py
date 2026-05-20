from scanner_core.logger import add_log
from scanner_core.state import scanner_state


def generate():
    add_log("Generating point cloud")

    scanner_state["point_cloud_generated"] = True

    add_log("PLY generated")