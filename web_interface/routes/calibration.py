"""Calibration status, calibration runs and laser diagnostics."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime

import cv2
from flask import Blueprint, request, send_file

from scanner_core import logger, paths
from scanner_core.calibration import (
    CheckerboardSpec,
    build_manual_turntable_profile,
    calibrate_camera_from_folder,
    calibrate_laser_plane_from_folder,
    calibration_store,
)
from scanner_core.calibration.auto import DIAGNOSTICS_RUN as AUTO_CALIBRATION_DIAGNOSTICS
from scanner_core.config import config_store
from scanner_core.errors import CalibrationError, UnsafePathError
from scanner_core.laser_detection import LaserLineDetector, detection_to_diagnostics
from scanner_core.pipeline import scan_controller
from web_interface.api_response import api_error, api_success, error_from_exception
from web_interface.files import safe_diagnostic_path

blueprint = Blueprint("calibration", __name__)


def _spec_from_payload(payload: dict) -> CheckerboardSpec:
    board = payload.get("checkerboard") or {}

    return CheckerboardSpec(
        columns=int(board.get("columns", 9)),
        rows=int(board.get("rows", 6)),
        square_size_mm=float(board.get("square_size_mm", 25.0)),
    ).validate()


@blueprint.get("/api/calibration")
def status():
    return api_success(
        "Calibration status.",
        {"calibration": calibration_store.load_bundle().to_status()},
    )


@blueprint.post("/api/calibration/camera")
def calibrate_camera():
    """Run the checkerboard intrinsics calibration on a folder of images."""

    if scan_controller.is_running:
        return api_error("Cannot calibrate while a scan is running.", 409)

    payload = request.get_json(silent=True) or {}

    folder_name = str(payload.get("folder", "camera"))

    try:
        directory = paths.resolve_within(paths.CALIBRATION_IMAGES_DIR, folder_name)
        spec = _spec_from_payload(payload)
    except (UnsafePathError, CalibrationError, TypeError, ValueError) as error:
        return error_from_exception(
            error if isinstance(error, Exception) else CalibrationError(str(error))
        )

    try:
        profile, failures = calibrate_camera_from_folder(directory, spec)
    except Exception as error:  # noqa: BLE001 - translated for the client
        return error_from_exception(error)

    if not profile.valid:
        return api_error(
            "The calibration was computed but did not pass validation; it was not saved.",
            422,
            {
                "issues": profile.issues,
                "result": profile.to_dict(),
                "images_without_board": [path.name for path in failures],
            },
        )

    path = calibration_store.save_camera(profile)

    logger.info(
        f"Camera calibration saved ({profile.image_count} views, "
        f"RMS {profile.rms_reprojection_error:.4f} px)."
    )

    return api_success(
        "Camera calibration completed.",
        {
            "file": path.name,
            "calibration": profile.to_dict(),
            "images_without_board": [item.name for item in failures],
        },
        201,
    )


@blueprint.post("/api/calibration/laser")
def calibrate_laser():
    """Fit the laser plane from checkerboard images with the laser visible."""

    if scan_controller.is_running:
        return api_error("Cannot calibrate while a scan is running.", 409)

    payload = request.get_json(silent=True) or {}

    camera = calibration_store.load_camera()

    if camera is None or not camera.valid:
        return api_error(
            "A valid camera calibration is required before the laser plane can be fitted.",
            422,
        )

    folder_name = str(payload.get("folder", "laser"))

    try:
        directory = paths.resolve_within(paths.CALIBRATION_IMAGES_DIR, folder_name)
        spec = _spec_from_payload(payload)
    except (UnsafePathError, CalibrationError, TypeError, ValueError) as error:
        return error_from_exception(error)

    detector = LaserLineDetector(config_store.get().detector)

    try:
        report = calibrate_laser_plane_from_folder(directory, spec, camera, detector)
    except Exception as error:  # noqa: BLE001 - translated for the client
        return error_from_exception(error)

    if report.profile is None or not report.profile.valid:
        return api_error(
            "The laser plane could not be fitted from these images; nothing was saved.",
            422,
            report.to_dict(),
        )

    path = calibration_store.save_laser(report.profile)

    logger.info(
        f"Laser plane calibration saved ({report.profile.point_count} points, "
        f"RMS {report.profile.rms_mm:.4f} mm)."
    )

    return api_success(
        "Laser plane calibration completed.",
        {"file": path.name, **report.to_dict()},
        201,
    )


@blueprint.post("/api/calibration/turntable/manual")
def calibrate_turntable_manual():
    """Store turntable geometry the operator measured by hand."""

    if scan_controller.is_running:
        return api_error("Cannot calibrate while a scan is running.", 409)

    payload = request.get_json(silent=True) or {}

    required = ("axis_origin", "axis_direction", "steps_per_revolution")
    missing = [key for key in required if key not in payload]

    if missing:
        return api_error(f"Missing required fields: {', '.join(missing)}.", 400)

    try:
        profile = build_manual_turntable_profile(
            payload["axis_origin"],
            payload["axis_direction"],
            int(payload["steps_per_revolution"]),
            rotation_direction=int(payload.get("rotation_direction", 1)),
            reference_angle_deg=float(payload.get("reference_angle_deg", 0.0)),
            notes=str(payload.get("notes", "")),
        )
    except Exception as error:  # noqa: BLE001 - translated for the client
        return error_from_exception(error)

    if not profile.valid:
        return api_error(
            "These turntable values did not pass validation; nothing was saved.",
            422,
            {"issues": profile.issues},
        )

    path = calibration_store.save_turntable(profile)

    logger.info("Manual turntable calibration saved.")

    return api_success(
        "Turntable calibration saved.",
        {"file": path.name, "calibration": profile.to_dict()},
        201,
    )


# ----------------------------------------------------------------------
# Automatic calibration (board standing on the turntable)
# ----------------------------------------------------------------------
@blueprint.get("/api/calibration/auto")
def auto_status():
    status = scan_controller.auto_calibration_status()

    result = (status["run"] or {}).get("result") or {}
    status["diagnostics_urls"] = [
        f"/diagnostics/{AUTO_CALIBRATION_DIAGNOSTICS}/{name}"
        for name in result.get("diagnostics", [])
    ]

    return api_success("Automatic calibration.", status)


@blueprint.post("/api/calibration/auto/step/<int:step>")
def auto_step(step: int):
    """Turn the platform once and photograph the board for one placement."""

    payload = request.get_json(silent=True) or {}

    try:
        spec = _spec_from_payload(payload)
    except (TypeError, ValueError) as error:
        return error_from_exception(CalibrationError(f"Invalid checkerboard values: {error}"))
    except CalibrationError as error:
        return error_from_exception(error)

    try:
        scan_controller.start_auto_calibration_step(step, spec)
    except Exception as error:  # noqa: BLE001 - translated for the client
        return error_from_exception(error)

    return api_success(f"Calibration step {step} started.", scan_controller.auto_calibration_status(), 202)


@blueprint.post("/api/calibration/auto/stop")
def auto_stop():
    if not scan_controller.request_stop():
        return api_error("Nothing is running.", 409)

    return api_success("Stop requested; the step ends at the next stop of the platform.")


@blueprint.post("/api/calibration/auto/compute")
def auto_compute():
    """Compute lens, turntable and laser plane from the stored photographs."""

    try:
        report = scan_controller.compute_auto_calibration()
    except Exception as error:  # noqa: BLE001 - translated for the client
        return error_from_exception(error)

    payload = report.to_dict()
    payload["diagnostics_urls"] = [
        f"/diagnostics/{AUTO_CALIBRATION_DIAGNOSTICS}/{name}" for name in report.diagnostics
    ]

    if not report.saved:
        return api_error(
            "The calibration did not pass; nothing was saved. See the report.", 422, payload
        )

    return api_success("Calibration saved.", payload, 201)


@blueprint.get("/api/calibration/auto/download")
def auto_download():
    """Every photograph, the run record and the overlays, as one zip.

    So the operator can hand the real images to whoever tunes the detector,
    instead of describing them.
    """

    directory = scan_controller.auto_calibration_directory

    if not (directory / "run.json").exists():
        return api_error("Nothing has been captured yet.", 404)

    buffer = io.BytesIO()

    # JPEGs are already compressed; storing them is as small and much faster
    # on a Raspberry Pi.
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file() and not path.is_symlink() and path.suffix.lower() in {".jpg", ".json"}:
                archive.write(path, arcname=path.relative_to(directory).as_posix())

        diagnostics = paths.DIAGNOSTICS_DIR / AUTO_CALIBRATION_DIAGNOSTICS

        if diagnostics.is_dir():
            for path in sorted(diagnostics.glob("*.jpg")):
                archive.write(path, arcname=f"diagnostics/{path.name}")

        if paths.CONFIG_FILE.is_file():
            archive.write(paths.CONFIG_FILE, arcname="scanner_config.json")

    buffer.seek(0)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    return send_file(
        buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"calibration_photos_{stamp}.zip",
    )


@blueprint.post("/api/calibration/auto/reset")
def auto_reset():
    try:
        scan_controller.reset_auto_calibration()
    except Exception as error:  # noqa: BLE001 - translated for the client
        return error_from_exception(error)

    return api_success("Automatic calibration photographs removed.")


@blueprint.post("/api/calibration/laser/diagnose")
def diagnose_laser():
    """Capture one frame and write the detector's intermediate images.

    This is the tool for physically aiming the laser: the operator sees the
    mask, the response and the detected centreline and can tell immediately
    whether the thresholds or the alignment are the problem.
    """

    config = config_store.get()

    try:
        frame = scan_controller.preview_frame()
    except Exception as error:  # noqa: BLE001
        return error_from_exception(error)

    if frame is None:
        return api_error("The camera did not return a frame to analyse.", 503)

    detector = LaserLineDetector(config.detector)
    detection = detector.detect(frame, keep_debug_images=True)

    run = datetime.now().strftime("%Y%m%d_%H%M%S")
    directory = paths.DIAGNOSTICS_DIR / run
    directory.mkdir(parents=True, exist_ok=True)

    images = detection_to_diagnostics(detector, frame, detection)

    written: dict[str, str] = {}

    for name, image in images.items():
        filename = f"{name}.jpg"

        if cv2.imwrite(str(directory / filename), image):
            written[name] = f"/diagnostics/{run}/{filename}"

    logger.info(
        f"Laser diagnostics '{run}': {detection.point_count} points, "
        f"accepted={detection.accepted}."
    )

    return api_success(
        "Laser diagnostics captured.",
        {
            "run": run,
            "images": written,
            "detection": detection.to_dict(),
        },
        201,
    )


@blueprint.get("/diagnostics/<run>/<filename>")
def serve_diagnostic(run: str, filename: str):
    try:
        path = safe_diagnostic_path(run, filename)
    except UnsafePathError as error:
        return error_from_exception(error)

    if not path.exists():
        return api_error("Diagnostic image not found.", 404)

    return send_file(path, mimetype="image/jpeg", conditional=True)
