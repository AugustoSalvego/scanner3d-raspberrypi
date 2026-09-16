"""Laser line detection diagnostics.

    python -m tools.detect_laser --image outputs/captures/capture_0001.jpg
    python -m tools.detect_laser --live
    python -m tools.detect_laser --image frame.jpg --saturation-min 60 --value-min 50

Writes the original frame, the binary mask, the laser response and an overlay
with the detected centreline to ``outputs/diagnostics/<run>/``.  Seeing those
four images side by side is the fastest way to tell whether a bad scan is a
threshold problem or a physical alignment problem.

This replaces the old ``tools/detect_red.py``, which hard-coded one filename and
only wrote a mask.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import cv2

from scanner_core import paths
from scanner_core.config import DetectorConfig
from scanner_core.laser_detection import LaserLineDetector, detection_to_diagnostics
from tools._common import add_common_arguments, bootstrap, fail, heading, show


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose the laser line detector.")

    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", help="Analyse an existing image file.")
    source.add_argument(
        "--live", action="store_true", help="Grab one frame from the configured camera."
    )

    parser.add_argument("--laser-off", help="Optional laser-off frame for differencing.")
    parser.add_argument("--method", choices=("hsv", "difference"), default=None)
    parser.add_argument("--saturation-min", type=int, default=None)
    parser.add_argument("--value-min", type=int, default=None)
    parser.add_argument("--min-confidence", type=float, default=None)
    parser.add_argument("--scan-axis", choices=("rows", "columns"), default=None)

    add_common_arguments(parser)

    arguments = parser.parse_args()
    config = bootstrap(arguments.verbose)

    detector_config = DetectorConfig(**config.detector.__dict__)

    overrides = {
        "method": arguments.method,
        "saturation_min": arguments.saturation_min,
        "value_min": arguments.value_min,
        "min_confidence": arguments.min_confidence,
        "scan_axis": arguments.scan_axis,
    }

    for name, value in overrides.items():
        if value is not None:
            setattr(detector_config, name, value)

    detector_config.validate()

    if arguments.image:
        path = Path(arguments.image)
        frame = cv2.imread(str(path), cv2.IMREAD_COLOR)

        if frame is None:
            return fail(f"Image could not be read: {path}")

        label = path.name
    else:
        from scanner_core.pipeline import scan_controller

        frame = scan_controller.preview_frame()

        if frame is None:
            return fail("The camera did not return a frame.")

        label = "live frame"

    laser_off = None

    if arguments.laser_off:
        laser_off = cv2.imread(arguments.laser_off, cv2.IMREAD_COLOR)

        if laser_off is None:
            return fail(f"Laser-off image could not be read: {arguments.laser_off}")

    detector = LaserLineDetector(detector_config)
    detection = detector.detect(frame, laser_off, keep_debug_images=True)

    heading(f"Detection on {label}")
    show("method", detection.statistics.get("method"))
    show("accepted", "yes" if detection.accepted else "NO")
    show("reason", detection.reason)
    show("mask pixels", detection.statistics.get("mask_pixels"))
    show("scan lines with a centroid", detection.statistics.get("raw_line_count"))
    show("points kept", detection.point_count)

    if detection.point_count:
        show("mean confidence", f"{float(detection.confidence.mean()):.3f}")
        show("mean thickness", f"{float(detection.thickness.mean()):.2f} px")
        show(
            "u range",
            f"{detection.pixels[:, 0].min():.1f} .. {detection.pixels[:, 0].max():.1f}",
        )
        show(
            "v range",
            f"{detection.pixels[:, 1].min():.1f} .. {detection.pixels[:, 1].max():.1f}",
        )

    run = datetime.now().strftime("%Y%m%d_%H%M%S")
    directory = paths.DIAGNOSTICS_DIR / run
    directory.mkdir(parents=True, exist_ok=True)

    heading("Diagnostic images")

    for name, image in detection_to_diagnostics(detector, frame, detection).items():
        target = directory / f"{name}.jpg"

        if cv2.imwrite(str(target), image):
            show(name, target)

    if not detection.accepted:
        print(
            "\n  The laser was not detected. Common causes, in order of likelihood:\n"
            "    - the laser is not lit, or not crossing the object;\n"
            "    - the frame is over-exposed, so the stripe is white rather than red\n"
            "      (lower the camera exposure, or lower detector.saturation_min);\n"
            "    - the ambient light is too strong (darken the room);\n"
            "    - detector.min_confidence or min_points is too strict."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
