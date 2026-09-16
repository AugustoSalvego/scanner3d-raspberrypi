"""Check the camera and report what the driver actually accepted.

    python -m tools.test_camera
    python -m tools.test_camera --device 0 --width 1280 --height 720 --fourcc MJPG

Never uses ``cv2.imshow``: the Raspberry Pi is usually headless, and a window
that cannot open would hang the test.  The frame is written to
``outputs/diagnostics/`` instead.
"""

from __future__ import annotations

import argparse
from datetime import datetime

import cv2

from scanner_core import paths
from scanner_core.config import CameraConfig
from scanner_core.errors import CameraError
from scanner_core.hardware.camera import OpenCVCamera
from tools._common import add_common_arguments, bootstrap, fail, heading, show


def main() -> int:
    parser = argparse.ArgumentParser(description="Test the scanner camera.")

    parser.add_argument("--device", default=None, help="Camera index or device path.")
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("--fps", type=int, default=None)
    parser.add_argument("--fourcc", default=None, help="For example MJPG or YUYV.")
    parser.add_argument(
        "--frames", type=int, default=5, help="How many frames to grab for the timing test."
    )

    add_common_arguments(parser)

    arguments = parser.parse_args()
    config = bootstrap(arguments.verbose)

    camera_config = CameraConfig(**config.camera.__dict__)

    if arguments.device is not None:
        camera_config.device = (
            int(arguments.device) if arguments.device.isdigit() else arguments.device
        )

    for name in ("width", "height", "fps", "fourcc"):
        value = getattr(arguments, name)

        if value is not None:
            setattr(camera_config, name, value)

    camera_config.validate()

    heading("Requested")
    show("device", camera_config.device)
    show("resolution", f"{camera_config.width}x{camera_config.height}")
    show("fps", camera_config.fps)
    show("fourcc", camera_config.fourcc)

    camera = OpenCVCamera(camera_config)

    try:
        camera.open()
    except CameraError as error:
        return fail(str(error))

    try:
        description = camera.describe()
        actual = description["actual"]

        heading("Reported by the driver")
        show("resolution", f"{actual.get('width')}x{actual.get('height')}")
        show("fps", actual.get("fps"))
        show("fourcc", actual.get("fourcc"))
        show("backend", description["backend"])

        if (actual.get("width"), actual.get("height")) != (
            camera_config.width,
            camera_config.height,
        ):
            print(
                "\n  NOTE: the camera did not accept the requested resolution. "
                "Calibrate at the resolution it really delivers."
            )

        if description["controls"]:
            heading("Controls (requested -> readback)")

            for name, report in description["controls"].items():
                status = "accepted" if report.get("accepted") else "IGNORED"
                show(name, f"{report.get('requested')} -> {report.get('actual')}  [{status}]")

        heading("Capture")

        import time

        started = time.perf_counter()
        frames = 0

        for _ in range(max(1, arguments.frames)):
            if camera.read() is not None:
                frames += 1

        elapsed = time.perf_counter() - started

        show("frames read", f"{frames}/{arguments.frames}")
        show("measured rate", f"{frames / elapsed:.1f} fps" if elapsed > 0 else "-")

        frame = camera.capture()

        if frame is None:
            return fail("The camera did not deliver a frame.")

        paths.DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)

        target = paths.DIAGNOSTICS_DIR / (
            f"camera_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        )

        cv2.imwrite(str(target), frame)

        show("frame shape", f"{frame.shape[1]}x{frame.shape[0]}")
        show("saved to", target)

        print("\nCamera test finished.")

        return 0
    finally:
        camera.close()


if __name__ == "__main__":
    raise SystemExit(main())
