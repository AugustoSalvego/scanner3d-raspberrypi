"""Calibration command line.

    python -m tools.calibrate camera --folder camera --columns 9 --rows 6 --square 25
    python -m tools.calibrate laser  --folder laser
    python -m tools.calibrate turntable --folder turntable --angles 0,30,60,90,...
    python -m tools.calibrate turntable --manual --origin 0,60,320 --direction 0,-1,0
    python -m tools.calibrate status

Photographs go in ``outputs/calibration/images/<folder>/``.  Nothing is saved
unless the computed calibration passes validation, so a bad capture set fails
loudly instead of leaving behind a file that claims to be calibrated.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from scanner_core import paths
from scanner_core.calibration import (
    CheckerboardSpec,
    build_manual_turntable_profile,
    build_turntable_profile,
    calibrate_camera_from_folder,
    calibrate_laser_plane_from_folder,
    calibration_store,
    estimate_turntable_axis,
    list_calibration_images,
    track_target_across_images,
)
from scanner_core.errors import CalibrationError, ScannerError
from scanner_core.laser_detection import LaserLineDetector
from tools._common import add_common_arguments, bootstrap, fail, heading, show


def _spec(arguments: argparse.Namespace) -> CheckerboardSpec:
    return CheckerboardSpec(
        columns=arguments.columns,
        rows=arguments.rows,
        square_size_mm=arguments.square,
    ).validate()


def _folder(name: str) -> Path:
    return paths.resolve_within(paths.CALIBRATION_IMAGES_DIR, name)


def run_camera(arguments: argparse.Namespace) -> int:
    directory = _folder(arguments.folder)
    spec = _spec(arguments)

    heading("Camera calibration")
    show("images folder", directory)
    show("inner corners", f"{spec.columns} x {spec.rows}")
    show("square size", f"{spec.square_size_mm} mm")

    profile, failures = calibrate_camera_from_folder(directory, spec)

    heading("Result")
    show("views used", profile.image_count)
    show("views without a board", len(failures))
    show("RMS reprojection error", f"{profile.rms_reprojection_error:.4f} px")
    show("quality", profile.quality)

    matrix = profile.camera_matrix

    show("fx, fy", f"{matrix[0][0]:.2f}, {matrix[1][1]:.2f}")
    show("cx, cy", f"{matrix[0][2]:.2f}, {matrix[1][2]:.2f}")
    show("image size", f"{profile.image_size[0]}x{profile.image_size[1]}")

    if failures:
        heading("Images where the board was not found")

        for path in failures:
            show("", path.name)

    if not profile.valid:
        heading("Not saved")

        for issue in profile.issues:
            show("", issue)

        return fail("The calibration did not pass validation.")

    path = calibration_store.save_camera(profile)

    print(f"\nSaved to {path}")

    if profile.quality != "good":
        print(
            "  The calibration is usable but not great. Capturing more views, at more\n"
            "  varied distances and tilts, will lower the reprojection error."
        )

    return 0


def run_laser(arguments: argparse.Namespace) -> int:
    camera = calibration_store.load_camera()

    if camera is None or not camera.valid:
        return fail(
            "A valid camera calibration is required first. Run "
            "'python -m tools.calibrate camera'."
        )

    directory = _folder(arguments.folder)
    spec = _spec(arguments)

    heading("Laser plane calibration")
    show("images folder", directory)
    show("camera calibration", f"{camera.source}, RMS {camera.rms_reprojection_error:.4f} px")

    from scanner_core.config import config_store

    detector = LaserLineDetector(config_store.get().detector)

    report = calibrate_laser_plane_from_folder(directory, spec, camera, detector)

    heading("Per image")

    for sample in report.samples:
        show(
            sample.path.name,
            f"{sample.laser_pixel_count} points at {sample.board_distance_mm:.1f} mm",
        )

    for path, reason in report.failures:
        show(path.name, f"SKIPPED - {reason}")

    if report.profile is None:
        return fail(
            "Not enough usable poses. At least 2 board poses at clearly different "
            "distances and tilts are required."
        )

    profile = report.profile

    heading("Result")
    show("poses used", profile.pose_count)
    show("points used", profile.point_count)
    show("plane normal", [round(value, 6) for value in profile.plane_normal])
    show("plane offset", f"{profile.plane_offset:.4f} mm")
    show("fit RMS", f"{profile.rms_mm:.4f} mm")
    show("max residual", f"{profile.max_residual_mm:.4f} mm")
    show("planarity ratio", f"{profile.planarity_ratio:.4f}")
    show("quality", profile.quality)

    if not profile.valid:
        heading("Not saved")

        for issue in profile.issues:
            show("", issue)

        return fail("The laser plane did not pass validation.")

    path = calibration_store.save_laser(profile)

    print(f"\nSaved to {path}")

    return 0


def run_turntable(arguments: argparse.Namespace) -> int:
    from scanner_core.config import config_store

    config = config_store.get()

    if arguments.manual:
        heading("Manual turntable calibration")

        origin = [float(value) for value in arguments.origin.split(",")]
        direction = [float(value) for value in arguments.direction.split(",")]

        profile = build_manual_turntable_profile(
            origin,
            direction,
            arguments.steps_per_revolution or config.motor.steps_per_revolution,
            rotation_direction=arguments.rotation_direction,
        )

        show("axis origin", origin)
        show("axis direction", direction)
        show("steps per revolution", profile.steps_per_revolution)
        show("rotation direction", profile.rotation_direction)

        if not profile.valid:
            heading("Not saved")

            for issue in profile.issues:
                show("", issue)

            return fail("These values did not pass validation.")

        path = calibration_store.save_turntable(profile)

        print(f"\nSaved to {path}")

        return 0

    camera = calibration_store.load_camera()

    if camera is None or not camera.valid:
        return fail(
            "A valid camera calibration is required to track the target. Run "
            "'python -m tools.calibrate camera'."
        )

    directory = _folder(arguments.folder)
    images = list_calibration_images(directory)

    if arguments.angles:
        angles = [float(value) for value in arguments.angles.split(",")]
    else:
        # Assume the photographs are evenly spread over one revolution, in
        # filename order, which is how tools.run_scan names them.
        angles = [index * 360.0 / len(images) for index in range(len(images))]

    if len(angles) != len(images):
        return fail(
            f"{len(images)} images but {len(angles)} angles. Supply one angle per image."
        )

    heading("Turntable axis calibration")
    show("images folder", directory)
    show("images", len(images))

    observations, failures = track_target_across_images(
        list(zip(images, angles)), _spec(arguments), camera
    )

    for path, reason in failures:
        show(path.name, f"SKIPPED - {reason}")

    if len(observations) < 4:
        return fail(
            f"Only {len(observations)} usable observations; at least 4 are required."
        )

    estimate = estimate_turntable_axis(
        observations, arguments.steps_per_revolution or config.motor.steps_per_revolution
    )

    heading("Result")
    show("axis origin", [round(value, 3) for value in estimate.axis_origin])
    show("axis direction", [round(value, 6) for value in estimate.axis_direction])
    show("rotation direction", estimate.rotation_direction)
    show("target radius", f"{estimate.radius_mm:.2f} mm")
    show("fit RMS", f"{estimate.rms_mm:.4f} mm")
    show("angle scale", f"{estimate.angle_scale:.5f}")

    if estimate.suggested_steps_per_revolution:
        show("suggested steps/revolution", estimate.suggested_steps_per_revolution)

    for warning in estimate.warnings:
        show("warning", warning)

    steps = (
        estimate.suggested_steps_per_revolution
        if (arguments.accept_measured_steps and estimate.suggested_steps_per_revolution)
        else (arguments.steps_per_revolution or config.motor.steps_per_revolution)
    )

    profile = build_turntable_profile(estimate, steps)

    if not profile.valid:
        heading("Not saved")

        for issue in profile.issues:
            show("", issue)

        return fail("The turntable calibration did not pass validation.")

    path = calibration_store.save_turntable(profile)

    print(f"\nSaved to {path}")

    if estimate.suggested_steps_per_revolution and not arguments.accept_measured_steps:
        print(
            "  Re-run with --accept-measured-steps to store the measured step count,\n"
            "  and set motor.steps_per_revolution to the same value."
        )

    return 0


def run_status(_: argparse.Namespace) -> int:
    bundle = calibration_store.load_bundle()
    status = bundle.to_status()

    for name in ("camera", "laser", "turntable"):
        profile = status[name]

        heading(name.capitalize())
        show("present", profile.get("present"))
        show("valid", profile.get("valid"))
        show("source", profile.get("source", "-"))
        show("created", profile.get("created_at", "-"))

        for issue in profile.get("issues", []):
            show("issue", issue)

    heading("Overall")
    show("ready for physical scan", status["ready_for_physical_scan"])

    for issue in status["blocking_issues"]:
        show("blocker", issue)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Scanner calibration tools.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    def board_arguments(target: argparse.ArgumentParser) -> None:
        target.add_argument("--columns", type=int, default=9, help="Inner corners across.")
        target.add_argument("--rows", type=int, default=6, help="Inner corners down.")
        target.add_argument("--square", type=float, default=25.0, help="Square size in mm.")

    camera_parser = subparsers.add_parser("camera", help="Camera intrinsics.")
    camera_parser.add_argument("--folder", default="camera")
    board_arguments(camera_parser)
    add_common_arguments(camera_parser)
    camera_parser.set_defaults(handler=run_camera)

    laser_parser = subparsers.add_parser("laser", help="Laser plane.")
    laser_parser.add_argument("--folder", default="laser")
    board_arguments(laser_parser)
    add_common_arguments(laser_parser)
    laser_parser.set_defaults(handler=run_laser)

    turntable_parser = subparsers.add_parser("turntable", help="Rotation axis.")
    turntable_parser.add_argument("--folder", default="turntable")
    turntable_parser.add_argument("--angles", default=None, help="Comma separated degrees.")
    turntable_parser.add_argument("--steps-per-revolution", type=int, default=None)
    turntable_parser.add_argument("--rotation-direction", type=int, choices=(1, -1), default=1)
    turntable_parser.add_argument(
        "--accept-measured-steps",
        action="store_true",
        help="Store the measured steps per revolution instead of the configured one.",
    )
    turntable_parser.add_argument("--manual", action="store_true")
    turntable_parser.add_argument("--origin", default="0,0,0", help="x,y,z in mm.")
    turntable_parser.add_argument("--direction", default="0,-1,0", help="x,y,z.")
    board_arguments(turntable_parser)
    add_common_arguments(turntable_parser)
    turntable_parser.set_defaults(handler=run_turntable)

    status_parser = subparsers.add_parser("status", help="Show calibration status.")
    add_common_arguments(status_parser)
    status_parser.set_defaults(handler=run_status)

    arguments = parser.parse_args()

    bootstrap(getattr(arguments, "verbose", False))

    try:
        return arguments.handler(arguments)
    except (CalibrationError, ScannerError) as error:
        return fail(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
