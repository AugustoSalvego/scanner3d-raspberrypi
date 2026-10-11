"""Automatic calibration with the checkerboard standing on the turntable.

The operator places the board on the platform three times:

1. upright, printed side facing the camera;
2. leaning back, like a picture frame;
3. leaning forward.

For each placement the turntable makes one revolution, stopping ``stops`` times.
Every stop is photographed and stored together with the motor position.  The
laser stays on the whole time: the reference build cannot switch it by
software, and the board is still found with the laser line crossing it.

Everything is computed afterwards from the stored photographs - capture once,
compute as often as needed (for example after tuning the laser detector):

* lens intrinsics from every view of the board;
* the turntable axis from the circle the board centre describes during each
  placement - three independent estimates that must agree with each other;
* the laser plane from the laser line crossing the board in every view.

The three results are saved together or not at all.  A run that cannot produce
a consistent set never overwrites the calibration that is already on disk.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from scanner_core.calibration.camera import (
    CheckerboardSpec,
    CornerDetection,
    calibrate_camera_from_detections,
    estimate_board_pose,
    find_checkerboard,
)
from scanner_core.calibration.laser import LaserPoseSample, fit_laser_plane
from scanner_core.calibration.store import CalibrationStore
from scanner_core.calibration.turntable import (
    ANGLE_SCALE_TOLERANCE,
    AxisObservation,
    TurntableEstimate,
    build_turntable_profile,
    estimate_turntable_axis,
)
from scanner_core.errors import CalibrationError, CameraError, ScanCancelled
from scanner_core.geometry import (
    camera_to_turntable,
    fit_plane,
    intersect_rays_with_plane,
    normalize,
    pixels_to_camera_rays,
    plane_from_pose,
)
from scanner_core.laser_detection import LaserLineDetector
from scanner_core.scan_plan import plan_capture_positions

RUN_SCHEMA_VERSION = 1
AUTO_DIRNAME = "auto"
RUN_FILENAME = "run.json"
SOURCE_AUTO = "auto_turntable"

#: Stops per revolution.  Measured on the synthetic rig: 36 stops (every 10
#: degrees) gave a lower lens error than 24 for a few extra minutes per step.
DEFAULT_STOPS = 36

STEPS: dict[int, str] = {
    1: "Board standing upright, printed side facing the camera.",
    2: "Board leaning back 15-25 degrees, like a picture frame.",
    3: "Board leaning forward 15-25 degrees.",
}

#: Lens model.  The board only covers part of the image, and a free k3 fits
#: that region and then diverges by hundreds of pixels towards the edges.
LENS_FLAGS = cv2.CALIB_FIX_K3

MIN_LENS_VIEWS = 8
MIN_AXIS_VIEWS_PER_STEP = 4
MIN_LASER_POINTS_PER_VIEW = 15
#: A view whose reprojection error exceeds this many times the median (and 1 px)
#: is dropped from the lens solution and the rest is solved again.
VIEW_OUTLIER_FACTOR = 3.0

#: The three placements must agree on the axis within these limits, otherwise
#: the platform wobbles under the board or the board slid during a revolution.
MAX_AXIS_DISAGREEMENT_DEG = 2.0
MAX_AXIS_SPREAD_MM = 1.5

#: The laser points of each step must lie on the same plane within this many
#: millimetres. A larger spread means the laser holder turned or slid between
#: the steps, and a plane fitted through all of them describes no real laser.
MAX_LASER_SHIFT_MM = 1.0
#: Fewer laser photographs than this in a step make its shift too noisy to judge.
MIN_LASER_VIEWS_FOR_SHIFT = 3
#: The laser points must spread across the plane, not only along one line, for
#: its direction to be known: second-largest over largest spread.
MIN_PLANE_SPREAD_RATIO = 0.05
#: The laser line in the closing photograph may sit at most this many pixels
#: from where it was in the first one. Detection repeats to a few tenths of a
#: pixel on an unchanged scene.
MAX_LASER_DRIFT_PX = 2.0
#: Rows of laser line both photographs must share for the comparison.
MIN_DRIFT_ROWS = 20

#: Learning the empty scan volume (see ``_learn_scan_volume``).
#: A scan line is "static" - the laser on the box, not on anything turning -
#: when at least this share of a turn's photographs puts the line there.
STATIC_SHARE = 0.6
STATIC_TOLERANCE_PX = 1.5
#: Static points nearer the axis than this are not taken for the box (the
#: centre pin, say); a scan volume smaller than this is not offered.
MIN_SCAN_RADIUS_MM = 30.0
#: Keep this far inside the nearest part of the box.
SCAN_RADIUS_MARGIN_MM = 5.0
#: The platform shows as a band of laser points this many times denser, per
#: millimetre of height, than the board around it.
PLATFORM_PEAK_FACTOR = 3.0
PLATFORM_BAND_FACTOR = 2.0
#: Keep this far above the top of the platform band.
FLOOR_MARGIN_MM = 1.0

JPEG_QUALITY = 95

#: Sub-folder of ``outputs/diagnostics`` holding the per-step overlay images,
#: so the existing ``/diagnostics/<run>/<file>`` route serves them.
DIAGNOSTICS_RUN = "auto_calibration"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ----------------------------------------------------------------------
# Run storage
# ----------------------------------------------------------------------
def run_directory(calibration_directory: Path) -> Path:
    """Where the automatic calibration keeps its photographs."""

    return Path(calibration_directory) / AUTO_DIRNAME


def load_run(directory: Path) -> dict[str, Any] | None:
    path = Path(directory) / RUN_FILENAME

    if not path.exists():
        return None

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CalibrationError(
            "The automatic calibration record is unreadable. Start over."
        ) from error


def save_run(directory: Path, run: dict[str, Any]) -> None:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    target = directory / RUN_FILENAME
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(run, indent=1), encoding="utf-8")

    os.replace(temporary, target)


def reset_run(directory: Path) -> None:
    """Delete every photograph and record of the current run."""

    directory = Path(directory)

    if directory.exists():
        shutil.rmtree(directory)


def new_run(spec: CheckerboardSpec, steps_per_revolution: int, stops: int) -> dict[str, Any]:
    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "created_at": _now(),
        "board": spec.to_dict(),
        "steps_per_revolution": int(steps_per_revolution),
        "stops": int(stops),
        "image_size": None,
        "steps": {},
        "result": None,
    }


def summarize_run(run: dict[str, Any] | None) -> dict[str, Any]:
    """What the dashboard shows about the stored run."""

    steps: dict[str, Any] = {}

    for number, description in STEPS.items():
        record = (run or {}).get("steps", {}).get(str(number))

        if record is None:
            steps[str(number)] = {"status": "pending", "description": description}
            continue

        frames = record.get("frames", [])

        steps[str(number)] = {
            "status": record.get("status"),
            "description": description,
            "photos": len(frames),
            "board_found": sum(1 for frame in frames if frame.get("corners")),
            "started_at": record.get("started_at"),
            "finished_at": record.get("finished_at"),
            "error": record.get("error"),
        }

    complete = [key for key, value in steps.items() if value["status"] == "complete"]

    return {
        "exists": run is not None,
        "board": (run or {}).get("board"),
        "image_size": (run or {}).get("image_size"),
        "stops": (run or {}).get("stops", DEFAULT_STOPS),
        "steps": steps,
        "steps_complete": len(complete),
        "ready_to_compute": len(complete) == len(STEPS),
        "result": (run or {}).get("result"),
    }


# ----------------------------------------------------------------------
# Capture
# ----------------------------------------------------------------------
def capture_step(
    step: int,
    *,
    camera: Any,
    motor: Any,
    spec: CheckerboardSpec,
    directory: Path,
    steps_per_revolution: int,
    stops: int = DEFAULT_STOPS,
    settle_delay: float = 0.8,
    flush_frames: int | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Turn the platform once and photograph every stop.

    Re-running a step replaces that step's photographs; the other steps are
    kept.  The record is written after every photograph, so an interrupted
    step is visible as such instead of looking complete.
    """

    if step not in STEPS:
        raise CalibrationError(f"Unknown calibration step {step}; use 1, 2 or 3.")

    spec.validate()

    directory = Path(directory)
    run = load_run(directory) or new_run(spec, steps_per_revolution, stops)

    if run["board"] != spec.to_dict():
        raise CalibrationError(
            "The checkerboard values differ from the ones used in the earlier steps. "
            "Fix them, or start the automatic calibration over."
        )

    if int(run["steps_per_revolution"]) != int(steps_per_revolution):
        raise CalibrationError(
            "motor.steps_per_revolution changed since the earlier steps. "
            "Start the automatic calibration over."
        )

    stops = int(run.get("stops", stops))
    plan = plan_capture_positions(stops, steps_per_revolution)

    step_dir = directory / f"step{step}"

    if step_dir.exists():
        shutil.rmtree(step_dir)

    step_dir.mkdir(parents=True)

    record: dict[str, Any] = {
        "status": "running",
        "started_at": _now(),
        "finished_at": None,
        "frames": [],
        "error": None,
    }
    run["steps"][str(step)] = record
    run["result"] = None
    save_run(directory, run)

    def cancelled() -> bool:
        return should_cancel is not None and should_cancel()

    def wait(seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)

        while time.monotonic() < deadline:
            if cancelled():
                raise ScanCancelled("Calibration cancelled.")

            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    found = 0

    try:
        if not camera.is_open:
            camera.open()

        motor.open()
        motor.reset_position()

        for entry in plan:
            if cancelled():
                raise ScanCancelled("Calibration cancelled.")

            motor.move_to(entry.target_step, should_cancel=should_cancel)
            wait(settle_delay)

            frame = camera.capture(flush_frames=flush_frames)

            if frame is None:
                raise CameraError(f"The camera returned no frame at stop {entry.index + 1}.")

            size = [int(frame.shape[1]), int(frame.shape[0])]

            if run["image_size"] is None:
                run["image_size"] = size
            elif run["image_size"] != size:
                raise CalibrationError(
                    f"The camera now delivers {size[0]}x{size[1]} but the earlier steps "
                    f"were {run['image_size'][0]}x{run['image_size'][1]}. Start over."
                )

            relative = f"step{step}/frame_{entry.index:03d}.jpg"

            if not cv2.imwrite(
                str(directory / relative), frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
            ):
                raise CalibrationError(f"Could not write {relative}.")

            corners = find_checkerboard(frame, spec)

            if corners is not None:
                found += 1

            position = int(motor.position)

            record["frames"].append(
                {
                    "index": entry.index,
                    "file": relative,
                    "target_step": entry.target_step,
                    "motor_position": position,
                    "angle_deg": round(position / steps_per_revolution * 360.0, 6),
                    "corners": None
                    if corners is None
                    else np.round(corners.reshape(-1, 2), 3).tolist(),
                }
            )
            save_run(directory, run)

            if progress is not None:
                progress(
                    {
                        "step": step,
                        "done": entry.index + 1,
                        "total": len(plan),
                        "board_found": found,
                    }
                )

        # Finish the revolution so the board faces the camera again, and
        # photograph it there. It is the same view as the first photograph, so
        # a laser line that is not where it was means something moved during
        # the turn - the one check that needs no calibration at all.
        if not cancelled():
            motor.move_to(steps_per_revolution, should_cancel=should_cancel)
            wait(settle_delay)

            frame = camera.capture(flush_frames=flush_frames)
            relative = f"step{step}/closing.jpg"

            if frame is not None and cv2.imwrite(
                str(directory / relative), frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
            ):
                record["closing"] = {"file": relative, "motor_position": int(motor.position)}

        record["status"] = "complete"

        return record

    except ScanCancelled:
        record["status"] = "cancelled"
        raise

    except Exception as error:
        record["status"] = "failed"
        record["error"] = str(error)
        raise

    finally:
        record["finished_at"] = _now()

        try:
            save_run(directory, run)
        finally:
            try:
                motor.release()
            except Exception:  # noqa: BLE001 - best effort
                pass


# ----------------------------------------------------------------------
# Computation
# ----------------------------------------------------------------------
@dataclass
class _View:
    step: int
    frame: dict[str, Any]
    corners: np.ndarray
    rotation: np.ndarray | None = None
    translation: np.ndarray | None = None


@dataclass
class AutoCalibrationReport:
    """Everything the operator needs to judge an automatic calibration."""

    saved: bool = False
    camera: dict[str, Any] | None = None
    turntable: dict[str, Any] | None = None
    laser: dict[str, Any] | None = None
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    computed_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "saved": self.saved,
            "camera": self.camera,
            "turntable": self.turntable,
            "laser": self.laser,
            "issues": list(self.issues),
            "warnings": list(self.warnings),
            "diagnostics": list(self.diagnostics),
            "computed_at": self.computed_at,
        }


def _views(run: dict[str, Any]) -> list[_View]:
    views: list[_View] = []

    for number in STEPS:
        record = run.get("steps", {}).get(str(number))

        if not record or record.get("status") != "complete":
            continue

        for frame in record.get("frames", []):
            if frame.get("corners"):
                corners = np.asarray(frame["corners"], dtype=np.float32).reshape(-1, 1, 2)
                views.append(_View(step=number, frame=frame, corners=corners))

    return views


def _solve_lens(views: list[_View], spec: CheckerboardSpec, image_size: tuple[int, int]):
    def solve(selected: list[_View]):
        detections = [
            CornerDetection(path=Path(view.frame["file"]), corners=view.corners, image_size=image_size)
            for view in selected
        ]

        return calibrate_camera_from_detections(
            detections,
            spec,
            source=SOURCE_AUTO,
            notes=f"Automatic calibration from {len(selected)} turntable views.",
            flags=LENS_FLAGS,
        )

    camera = solve(views)

    errors = np.asarray(camera.per_view_errors, dtype=np.float64)
    limit = max(1.0, VIEW_OUTLIER_FACTOR * float(np.median(errors)))
    keep = [view for view, error in zip(views, errors) if error <= limit]
    dropped = len(views) - len(keep)

    if dropped and len(keep) >= MIN_LENS_VIEWS:
        camera = solve(keep)

        return camera, keep, dropped

    return camera, views, 0


def _combine_axes(
    estimates: dict[int, TurntableEstimate],
    steps_per_revolution: int,
) -> tuple[TurntableEstimate, float, float]:
    directions = np.vstack([estimate.axis_direction for estimate in estimates.values()])
    direction = normalize(directions.mean(axis=0))

    disagreement = max(
        float(np.degrees(np.arccos(np.clip(row @ direction, -1.0, 1.0)))) for row in directions
    )

    origins = np.vstack([estimate.axis_origin for estimate in estimates.values()])
    origin = origins.mean(axis=0)

    offsets = origins - origin
    off_axis = offsets - np.outer(offsets @ direction, direction)
    spread = float(np.max(np.linalg.norm(off_axis, axis=1)))

    senses = {estimate.rotation_direction for estimate in estimates.values()}

    if len(senses) != 1:
        raise CalibrationError(
            "The placements disagree on which way the platform turns. Check that the "
            "board stayed fixed to the platform during each revolution."
        )

    scale = float(np.mean([estimate.angle_scale for estimate in estimates.values()]))
    suggested = (
        int(round(steps_per_revolution / scale)) if abs(scale - 1.0) > ANGLE_SCALE_TOLERANCE else None
    )

    warnings: list[str] = []

    for estimate in estimates.values():
        for warning in estimate.warnings:
            if warning not in warnings:
                warnings.append(warning)

    combined = TurntableEstimate(
        axis_origin=origin,
        axis_direction=direction,
        rotation_direction=senses.pop(),
        radius_mm=float(np.mean([estimate.radius_mm for estimate in estimates.values()])),
        rms_mm=float(max(estimate.rms_mm for estimate in estimates.values())),
        observation_count=sum(estimate.observation_count for estimate in estimates.values()),
        angle_scale=scale,
        suggested_steps_per_revolution=suggested,
        warnings=warnings,
    )

    return combined, disagreement, spread


def _board_polygon(view: _View, spec: CheckerboardSpec, camera) -> np.ndarray:
    """Outline of the printed pattern in the image, as an int32 polygon.

    The inner corners stop one square short of the pattern edge; projecting the
    pattern's outer rectangle uses the whole printed area.
    """

    square = spec.square_size_mm
    outline = np.array(
        [
            [-square, -square, 0.0],
            [spec.columns * square, -square, 0.0],
            [spec.columns * square, spec.rows * square, 0.0],
            [-square, spec.rows * square, 0.0],
        ]
    )
    rvec, _ = cv2.Rodrigues(view.rotation)
    projected, _ = cv2.projectPoints(
        outline,
        rvec,
        view.translation,
        np.asarray(camera.camera_matrix, dtype=np.float64),
        np.asarray(camera.distortion_coefficients, dtype=np.float64).reshape(1, -1),
    )

    return np.round(projected.reshape(-1, 2)).astype(np.int32)


def _laser_points_on_board(
    image: np.ndarray,
    view: _View,
    spec: CheckerboardSpec,
    camera,
    detector: LaserLineDetector,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Laser pixels on the board, back-projected onto the board plane.

    Everything outside the printed pattern is blacked out first.  The detector
    keeps the strongest laser response of each image row, and the line often
    continues on the wall behind the board in the same rows; without the mask
    it could pick the wall.

    Returns ``(points_camera, pixels_used, laser_pixels_detected)``.
    """

    polygon = _board_polygon(view, spec, camera)

    mask = np.zeros(image.shape[:2], dtype=np.uint8)
    cv2.fillConvexPoly(mask, polygon, 255)

    masked = cv2.bitwise_and(image, image, mask=mask)
    detection = detector.detect(masked)

    if detection.point_count == 0:
        return np.zeros((0, 3)), np.zeros((0, 2)), 0

    columns = np.clip(np.round(detection.pixels[:, 0]).astype(int), 0, mask.shape[1] - 1)
    rows = np.clip(np.round(detection.pixels[:, 1]).astype(int), 0, mask.shape[0] - 1)

    # Stay a few pixels clear of the pattern edge, where the blur of the mask
    # border and the paper edge can bias the centroid.
    inner = cv2.erode(mask, np.ones((5, 5), np.uint8))
    pixels = detection.pixels[inner[rows, columns] > 0]

    if len(pixels) == 0:
        return np.zeros((0, 3)), pixels, detection.point_count

    rays = pixels_to_camera_rays(
        pixels, camera.camera_matrix, camera.distortion_coefficients, undistort=True
    )
    points, valid = intersect_rays_with_plane(
        rays,
        plane_from_pose(view.rotation, view.translation),
        min_incidence=0.02,
        min_depth_mm=1.0,
        max_depth_mm=100_000.0,
    )

    return points[valid], pixels[valid], detection.point_count


def _learn_scan_volume(
    directory: Path,
    run: dict[str, Any],
    steps: list[int],
    camera: Any,
    laser: Any,
    turntable: Any,
    detector: LaserLineDetector,
) -> dict[str, Any]:
    """Where the platform and the box are, from the calibration photographs.

    Every photograph of a turn is triangulated with the new calibration. The
    laser on the box does not move while the platform turns: scan lines where
    most photographs agree are the box, and the nearest of them bounds the scan
    radius. The platform is the lowest dense band of points: the laser lies on
    it in every photograph, at the same height. Heights are returned relative
    to the turntable origin (the board centre's height).

    Returns ``platform_mm`` (height of the platform), ``floor_mm`` (lowest
    height that is not the platform, relative to the platform), ``box_mm``
    (distance from the axis to the nearest part of the box) and ``radius_mm``
    (the scan radius) - each ``None`` when it could not be found.
    """

    origin = np.asarray(turntable.axis_origin, dtype=np.float64).reshape(3)
    direction = np.asarray(turntable.axis_direction, dtype=np.float64).reshape(3)
    along, across = (1, 0) if detector.config.scan_axis == "rows" else (0, 1)

    def to_turntable(pixels: np.ndarray) -> np.ndarray:
        if len(pixels) == 0:
            return np.zeros((0, 3))

        rays = pixels_to_camera_rays(
            pixels, camera.camera_matrix, camera.distortion_coefficients, undistort=True
        )
        points, valid = intersect_rays_with_plane(
            rays, laser.plane, min_incidence=0.02, min_depth_mm=1.0, max_depth_mm=100_000.0
        )

        return camera_to_turntable(points[valid], origin, direction)

    every: list[np.ndarray] = []
    static: list[np.ndarray] = []

    for number in steps:
        frames = run["steps"][str(number)]["frames"]
        lines: list[dict[int, float]] = []

        for frame in frames:
            image = cv2.imread(str(directory / frame["file"]), cv2.IMREAD_COLOR)
            detection = detector.detect(image) if image is not None else None

            if detection is None or not detection.accepted:
                lines.append({})
                continue

            every.append(to_turntable(detection.pixels))
            lines.append(
                {int(round(point[along])): float(point[across]) for point in detection.pixels}
            )

        needed = STATIC_SHARE * len(frames)
        found: list[tuple[float, float]] = []

        for key in sorted(set().union(*lines)) if lines else []:
            seen = np.array([line[key] for line in lines if key in line])

            if len(seen) < needed:
                continue

            close = np.abs(seen - np.median(seen)) <= STATIC_TOLERANCE_PX

            if np.count_nonzero(close) >= needed:
                position = float(np.median(seen[close]))
                found.append((position, key) if along == 1 else (key, position))

        if found:
            static.append(to_turntable(np.array(found, dtype=np.float64)))

    result: dict[str, Any] = {"platform_mm": None, "floor_mm": None, "box_mm": None, "radius_mm": None}

    # --- the platform: a dense band of points below the board centre
    if every:
        height = np.vstack(every)[:, 2]
        values = height[height < 0.0]

        if len(values) >= 50:
            edges = np.arange(np.floor(values.min()), 1.0, 1.0)

            if len(edges) >= 3:
                counts, edges = np.histogram(values, bins=edges)
                typical = float(np.median(counts[counts > 0]))
                peak = int(np.argmax(counts))

                if counts[peak] >= PLATFORM_PEAK_FACTOR * max(typical, 1.0):
                    low = high = peak

                    while low > 0 and counts[low - 1] > PLATFORM_BAND_FACTOR * typical:
                        low -= 1

                    while high + 1 < len(counts) and counts[high + 1] > PLATFORM_BAND_FACTOR * typical:
                        high += 1

                    band = values[(values >= edges[low]) & (values <= edges[high + 1])]
                    platform = float(np.median(band))

                    result["platform_mm"] = platform
                    result["floor_mm"] = float(edges[high + 1]) + FLOOR_MARGIN_MM - platform

    # --- the box: the nearest static laser points above the platform (a flat
    # platform is static too, and is not the box)
    if static:
        box = np.vstack(static)
        floor = (
            0.0
            if result["platform_mm"] is None
            else result["platform_mm"] + result["floor_mm"] + SCAN_RADIUS_MARGIN_MM
        )
        box = box[box[:, 2] > floor]
        distance = np.hypot(box[:, 0], box[:, 1])
        distance = distance[distance >= MIN_SCAN_RADIUS_MM]

        if len(distance) >= MIN_DRIFT_ROWS:
            nearest = float(np.percentile(distance, 5))
            result["box_mm"] = nearest

            if nearest - SCAN_RADIUS_MARGIN_MM >= MIN_SCAN_RADIUS_MM:
                result["radius_mm"] = nearest - SCAN_RADIUS_MARGIN_MM

    return result


def _laser_shift_by_step(samples: list[LaserPoseSample], steps: list[int]) -> dict[int, float]:
    """How far, in mm along the laser normal, each step's line sits from the first step's.

    The points are fitted with parallel planes, one per step, sharing a normal:
    each step is centred on its own centroid and the normal is the direction of
    least spread of all the centred points together. A shift between steps is
    then the gap between their planes. (A single plane through every point
    would tilt to absorb part of a shift and under-report it.) With a steady
    laser the steps agree within a few tenths of a mm; a laser that turned or
    slid in its holder between steps shows up as millimetres. Steps with too
    few photographs are left out rather than reported as noise.
    """

    grouped: dict[int, list[np.ndarray]] = {}

    for sample, step in zip(samples, steps):
        grouped.setdefault(step, []).append(np.asarray(sample.points_camera, dtype=np.float64))

    clouds = {
        step: np.vstack(parts)
        for step, parts in sorted(grouped.items())
        if len(parts) >= MIN_LASER_VIEWS_FOR_SHIFT
    }

    if not clouds:
        return {}

    normal = _shared_normal(clouds)

    if normal is None:
        return {}

    # A first pass may be pulled by stray points (reflections, the board edge):
    # drop the points far from their step's plane and fit once more.
    trimmed: dict[int, np.ndarray] = {}

    for step, points in clouds.items():
        distance = points @ normal
        residual = np.abs(distance - np.median(distance))
        limit = max(0.5, 3.0 * 1.4826 * float(np.median(residual)))
        kept = points[residual <= limit]
        trimmed[step] = kept if len(kept) >= 3 else points

    normal = _shared_normal(trimmed)

    if normal is None:
        return {}

    # The normal's sign is arbitrary; point it to the camera's right so a
    # positive shift means the line moved right in the image, for every run.
    if normal[0] < 0:
        normal = -normal

    level = {step: float(np.median(points @ normal)) for step, points in trimmed.items()}
    reference = level[min(level)]

    return {step: value - reference for step, value in level.items()}


def _laser_drift_px(
    directory: Path, record: dict[str, Any], detector: LaserLineDetector
) -> float | None:
    """How far the laser line moved during a turn, in pixels.

    Compares the first photograph with the closing one, taken after the full
    turn at the same platform angle: the same scene, so the line must fall on
    the same pixels. Row by row (column by column for a horizontal line), the
    median offset is returned. ``None`` when the step has no closing photograph
    (runs captured before it existed) or the line is not found in both.
    """

    closing = record.get("closing")
    first = next((frame for frame in record.get("frames", []) if frame.get("index") == 0), None)

    if not closing or first is None:
        return None

    lines = []

    for name in (first["file"], closing["file"]):
        image = cv2.imread(str(directory / name), cv2.IMREAD_COLOR)

        if image is None:
            return None

        detection = detector.detect(image)

        if not detection.accepted:
            return None

        # The detector reports one point per scan line: (u, row) for a
        # vertical line, (column, v) for a horizontal one.
        along, across = (1, 0) if detector.config.scan_axis == "rows" else (0, 1)
        lines.append(
            {int(round(point[along])): float(point[across]) for point in detection.pixels}
        )

    shared = sorted(set(lines[0]) & set(lines[1]))

    if len(shared) < MIN_DRIFT_ROWS:
        return None

    return float(np.median([lines[1][key] - lines[0][key] for key in shared]))


def _shared_normal(clouds: dict[int, np.ndarray]) -> np.ndarray | None:
    centred = np.vstack([points - points.mean(axis=0) for points in clouds.values()])

    if len(centred) < 3:
        return None

    try:
        _, singular, vt = np.linalg.svd(centred, full_matrices=False)
    except np.linalg.LinAlgError:
        return None

    # Points along (nearly) one line have no defined normal: the plane could
    # turn about that line.
    if singular[1] <= MIN_PLANE_SPREAD_RATIO * float(singular[0]):
        return None

    return vt[-1]


def _write_overlay(
    target: Path, image: np.ndarray, view: _View, spec: CheckerboardSpec, pixels: np.ndarray
) -> bool:
    overlay = image.copy()

    cv2.drawChessboardCorners(overlay, spec.size, view.corners, True)

    for u, v in pixels:
        cv2.circle(overlay, (int(round(u)), int(round(v))), 2, (0, 255, 0), -1)

    target.parent.mkdir(parents=True, exist_ok=True)

    return bool(cv2.imwrite(str(target), overlay))


def compute_auto_calibration(
    directory: Path,
    detector: LaserLineDetector,
    *,
    store: CalibrationStore | None = None,
    diagnostics_dir: Path | None = None,
) -> AutoCalibrationReport:
    """Compute lens, turntable axis and laser plane from the stored run.

    Saves the three profiles to ``store`` only when all three are valid.
    """

    directory = Path(directory)
    run = load_run(directory)
    report = AutoCalibrationReport()

    if run is None or not run.get("steps"):
        raise CalibrationError("Nothing has been captured yet. Run the calibration steps first.")

    spec = CheckerboardSpec(**run["board"]).validate()
    steps_per_revolution = int(run["steps_per_revolution"])
    image_size = tuple(run["image_size"] or (0, 0))

    complete = [
        number for number in STEPS if run["steps"].get(str(number), {}).get("status") == "complete"
    ]

    # A step that was never started is simply not part of this run (the
    # one-turn calibration only uses step 1); one that was started and did not
    # finish is worth a warning.
    unfinished = [str(number) for number in STEPS if str(number) in run["steps"] and number not in complete]

    if unfinished:
        report.warnings.append(
            f"Step(s) {', '.join(unfinished)} did not finish; computing from step(s) "
            f"{', '.join(map(str, complete)) or 'none'} only."
        )

    views = _views(run)

    # --- lens -------------------------------------------------------------
    if len(views) < MIN_LENS_VIEWS:
        report.issues.append(
            f"The board was found in only {len(views)} photographs; at least "
            f"{MIN_LENS_VIEWS} are needed. Check that the board faces the camera at "
            "the start of each step and is fully in view."
        )

        return _finish(directory, run, report)

    camera, views, dropped = _solve_lens(views, spec, image_size)  # type: ignore[arg-type]

    report.camera = {
        "views": len(views),
        "views_dropped": dropped,
        "rms_px": round(float(camera.rms_reprojection_error), 4),
        "quality": camera.quality,
        "fx": round(float(camera.camera_matrix[0][0]), 2),
        "fy": round(float(camera.camera_matrix[1][1]), 2),
        "cx": round(float(camera.camera_matrix[0][2]), 2),
        "cy": round(float(camera.camera_matrix[1][2]), 2),
        "image_size": list(image_size),
    }

    if not camera.valid:
        report.issues.extend(f"Lens: {issue}" for issue in camera.issues)

        return _finish(directory, run, report)

    for view in views:
        view.rotation, view.translation = estimate_board_pose(view.corners, spec, camera)

    # --- turntable axis ---------------------------------------------------
    centre = spec.object_points().mean(axis=0).astype(np.float64)
    estimates: dict[int, TurntableEstimate] = {}
    per_step: list[dict[str, Any]] = []

    for number in complete:
        observations = [
            AxisObservation(
                commanded_angle_deg=float(view.frame["angle_deg"]),
                point_camera=view.rotation @ centre + view.translation,
                source=view.frame["file"],
            )
            for view in views
            if view.step == number
        ]

        if len(observations) < MIN_AXIS_VIEWS_PER_STEP:
            report.warnings.append(
                f"Step {number}: only {len(observations)} usable photographs, so it was "
                "left out of the turntable axis."
            )
            continue

        try:
            estimate = estimate_turntable_axis(observations, steps_per_revolution)
        except CalibrationError as error:
            report.warnings.append(f"Step {number}: {error}")
            continue

        estimates[number] = estimate
        per_step.append(
            {
                "step": number,
                "views": len(observations),
                "rms_mm": round(estimate.rms_mm, 4),
                "radius_mm": round(estimate.radius_mm, 2),
            }
        )

    turntable_profile = None

    if not estimates:
        report.issues.append("Turntable: no step had enough photographs to fit the axis.")
    else:
        try:
            combined, disagreement, spread = _combine_axes(estimates, steps_per_revolution)
        except CalibrationError as error:
            report.issues.append(f"Turntable: {error}")
            combined = None

        if combined is not None:
            report.turntable = {
                "per_step": per_step,
                "axis_disagreement_deg": round(disagreement, 3),
                "axis_spread_mm": round(spread, 3),
                "rms_mm": round(combined.rms_mm, 4),
                "rotation_direction": combined.rotation_direction,
                "angle_scale": round(combined.angle_scale, 5),
                "suggested_steps_per_revolution": combined.suggested_steps_per_revolution,
            }

            report.warnings.extend(combined.warnings)

            if len(estimates) > 1 and (
                disagreement > MAX_AXIS_DISAGREEMENT_DEG or spread > MAX_AXIS_SPREAD_MM
            ):
                report.issues.append(
                    f"Turntable: the placements disagree about the axis ({disagreement:.2f} deg, "
                    f"{spread:.2f} mm apart). The platform probably wobbles under the board, or "
                    "the board moved during a revolution."
                )
            else:
                turntable_profile = build_turntable_profile(
                    combined,
                    steps_per_revolution,
                    source=SOURCE_AUTO,
                    notes=f"Automatic calibration, {len(estimates)} placement(s).",
                )
                report.turntable["quality"] = turntable_profile.quality

                if not turntable_profile.valid:
                    report.issues.extend(f"Turntable: {issue}" for issue in turntable_profile.issues)
                    turntable_profile = None

    # --- laser plane ------------------------------------------------------
    samples: list[LaserPoseSample] = []
    sample_steps: list[int] = []
    best_view: dict[int, tuple[int, _View, np.ndarray, np.ndarray]] = {}
    laser_pixels_total = 0

    for view in views:
        image = cv2.imread(str(directory / view.frame["file"]), cv2.IMREAD_COLOR)

        if image is None:
            report.warnings.append(f"{view.frame['file']} could not be read.")
            continue

        points, pixels, total = _laser_points_on_board(image, view, spec, camera, detector)
        laser_pixels_total += total

        current = best_view.get(view.step)

        if current is None or len(pixels) > current[0]:
            best_view[view.step] = (len(pixels), view, image, pixels)

        if len(points) >= MIN_LASER_POINTS_PER_VIEW:
            samples.append(
                LaserPoseSample(
                    path=Path(view.frame["file"]),
                    points_camera=points,
                    board_distance_mm=float(np.linalg.norm(view.translation)),
                    laser_pixel_count=int(len(points)),
                    rejected_pixel_count=int(total - len(points)),
                )
            )
            sample_steps.append(view.step)

    laser_profile = None
    outliers = 0
    shifts = _laser_shift_by_step(samples, sample_steps)
    drift: dict[int, float] = {}

    for number in complete:
        value = _laser_drift_px(directory, run["steps"][str(number)], detector)

        if value is not None:
            drift[number] = value

    if len(samples) >= 2:
        everything = np.vstack([sample.points_camera for sample in samples])
        first = fit_plane(everything)
        residual = np.abs(first.plane.signed_distance(everything))
        mad = float(np.median(np.abs(residual - np.median(residual))))
        limit = max(0.75, 3.0 * 1.4826 * mad + float(np.median(residual)))

        kept: list[LaserPoseSample] = []

        for sample in samples:
            mask = np.abs(first.plane.signed_distance(sample.points_camera)) <= limit
            outliers += int(np.count_nonzero(~mask))

            if np.count_nonzero(mask) >= MIN_LASER_POINTS_PER_VIEW:
                sample.points_camera = sample.points_camera[mask]
                sample.laser_pixel_count = int(np.count_nonzero(mask))
                kept.append(sample)

        samples = kept

    report.laser = {
        "views_with_board": len(views),
        "views_with_laser": len(samples),
        "laser_pixels_detected": int(laser_pixels_total),
        "points": int(sum(len(sample.points_camera) for sample in samples)),
        "outliers_removed": outliers,
        "shift_by_step_mm": {str(step): round(value, 2) for step, value in shifts.items()},
        "drift_during_turn_px": {str(step): round(value, 2) for step, value in drift.items()},
    }
    moved = len(shifts) > 1 and (max(shifts.values()) - min(shifts.values())) > MAX_LASER_SHIFT_MM
    drifting = {step: value for step, value in drift.items() if abs(value) > MAX_LASER_DRIFT_PX}

    if moved:
        listing = ", ".join(f"step {step}: {value:+.1f} mm" for step, value in shifts.items())
        report.issues.append(
            "Laser: the laser moved between the steps "
            f"({listing}, measured against step {min(shifts)}). The laser plane only "
            "exists if the laser stays still: fix the laser holder so it cannot turn or "
            "slide, do not touch the box, then calibrate again."
        )

    for step, value in drifting.items():
        report.issues.append(
            f"Laser: the laser moved during the turn of step {step}: in the last "
            "photograph, taken where the first one was, the line is "
            f"{value:+.1f} px away from where it started. The laser or the board moved. "
            "Fix the laser holder so it cannot turn or slide, check that the board "
            "stands firmly, do not touch the box, then calibrate again."
        )

    moved = moved or bool(drifting)

    if len(samples) < 2:
        report.issues.append(
            f"Laser: the line was found on the board in only {len(samples)} of "
            f"{len(views)} photographs. Check the overlay images; if the line is "
            "visible but not marked, the laser detector thresholds need adjusting. "
            "The photographs are kept, so 'Compute' can be run again after that."
        )
    else:
        try:
            laser_profile = fit_laser_plane(
                samples,
                camera,
                source=SOURCE_AUTO,
                notes=f"Automatic calibration from {len(samples)} turntable views.",
            )
        except CalibrationError as error:
            report.issues.append(f"Laser: {error}")

        if laser_profile is not None:
            report.laser.update(
                {
                    "rms_mm": round(float(laser_profile.rms_mm), 4),
                    "planarity_ratio": round(float(laser_profile.planarity_ratio), 4),
                    "quality": laser_profile.quality,
                }
            )

            if not laser_profile.valid:
                report.issues.extend(f"Laser: {issue}" for issue in laser_profile.issues)
                laser_profile = None

        if moved:
            laser_profile = None

    # --- diagnostics ------------------------------------------------------
    if diagnostics_dir is not None:
        for number, (_, view, image, pixels) in sorted(best_view.items()):
            name = f"step{number}.jpg"

            if _write_overlay(Path(diagnostics_dir) / name, image, view, spec, pixels):
                report.diagnostics.append(name)

    # --- scan volume: the platform and the box, from the same photographs ---
    if laser_profile is not None and turntable_profile is not None:
        volume = _learn_scan_volume(
            directory, run, complete, camera, laser_profile, turntable_profile, detector
        )

        if volume["platform_mm"] is not None:
            # Heights are measured from the platform from now on.
            direction = np.asarray(turntable_profile.axis_direction, dtype=np.float64).reshape(3)
            turntable_profile.axis_origin = (
                np.asarray(turntable_profile.axis_origin, dtype=np.float64).reshape(3)
                + volume["platform_mm"] * direction
            )
            turntable_profile.scan_floor_mm = round(volume["floor_mm"], 2)
        else:
            report.warnings.append(
                "The platform was not found in the photographs, so it will not be "
                "removed from scans; heights are measured from the board centre."
            )

        if volume["radius_mm"] is not None:
            turntable_profile.scan_radius_mm = round(volume["radius_mm"], 1)
        else:
            report.warnings.append(
                "The box around the platform was not found in the photographs, so "
                "the configured scan radius applies."
            )

        report.turntable["scan_volume"] = {
            "platform_found": volume["platform_mm"] is not None,
            "board_centre_above_platform_mm": None
            if volume["platform_mm"] is None
            else round(-volume["platform_mm"], 1),
            "floor_mm": turntable_profile.scan_floor_mm,
            "box_mm": None if volume["box_mm"] is None else round(volume["box_mm"], 1),
            "radius_mm": turntable_profile.scan_radius_mm,
        }

    # --- save, all or nothing ----------------------------------------------
    if store is not None and laser_profile is not None and turntable_profile is not None:
        store.save_camera(camera)
        store.save_turntable(turntable_profile)
        store.save_laser(laser_profile)
        report.saved = True
    elif store is not None:
        report.warnings.append(
            "Nothing was saved: lens, turntable and laser are saved together, and at "
            "least one of them did not pass. The previous calibration is untouched."
        )

    return _finish(directory, run, report)


def _finish(directory: Path, run: dict[str, Any], report: AutoCalibrationReport) -> AutoCalibrationReport:
    run["result"] = report.to_dict()
    save_run(directory, run)

    return report
