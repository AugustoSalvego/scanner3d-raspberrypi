import os
import math

from scanner_core.config import POINT_CLOUD_FOLDER
from scanner_core.config import POINT_CLOUD_FILE

from scanner_core.logger import add_log
from scanner_core.state import scanner_state


def generate():
    add_log("Generating point cloud")

    os.makedirs(
        POINT_CLOUD_FOLDER,
        exist_ok=True
    )

    filepath = os.path.join(
        POINT_CLOUD_FOLDER,
        POINT_CLOUD_FILE
    )

    points = []

    for i in range(120):
        angle = math.radians(i * 3)

        x = math.cos(angle)
        y = math.sin(angle)
        z = i * 0.01

        points.append(
            (x, y, z)
        )

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

    add_log(f"PLY generated: {filepath}")