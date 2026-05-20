import math
import os
from datetime import datetime

from scanner_core.config import POINT_CLOUD_FILE
from scanner_core.config import POINT_CLOUD_FOLDER
from scanner_core.logger import add_log
from scanner_core.state import scanner_state
from scanner_core.session import get_current_session
from scanner_core.session import add_point_cloud_to_session


def generate_point_cloud_filename(session=None):
    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S_%f"
    )[:-3]

    if session is not None:
        session_id = session["session_id"]
        return f"point_cloud_{session_id}_{timestamp}.ply"

    return f"point_cloud_manual_{timestamp}.ply"


def generate():
    add_log("Generating point cloud")

    session = get_current_session()

    if session is not None:
        output_folder = session["point_clouds_path"]
        filename = generate_point_cloud_filename(session)
    else:
        output_folder = POINT_CLOUD_FOLDER
        filename = generate_point_cloud_filename()

    os.makedirs(
        output_folder,
        exist_ok=True
    )

    filepath = os.path.join(
        output_folder,
        filename
    )

    points = []

    for i in range(120):
        angle = math.radians(i * 3)

        x = math.cos(angle)
        y = math.sin(angle)
        z = i * 0.01

        points.append((x, y, z))

    with open(filepath, "w", encoding="utf-8") as file:
        file.write("ply\n")
        file.write("format ascii 1.0\n")
        file.write(f"element vertex {len(points)}\n")
        file.write("property float x\n")
        file.write("property float y\n")
        file.write("property float z\n")
        file.write("end_header\n")

        for x, y, z in points:
            file.write(f"{x} {y} {z}\n")

    scanner_state["point_cloud_generated"] = True
    scanner_state["last_point_cloud"] = filepath

    add_point_cloud_to_session(filename)

    add_log(f"PLY generated: {filepath}")

    return filepath