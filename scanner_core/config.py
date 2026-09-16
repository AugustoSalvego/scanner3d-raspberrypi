"""Typed scanner configuration.

The configuration is split by concern so that a value has exactly one home:

``ScannerConfig``
    Top level container holding the operating mode and every sub-section.
``CameraConfig`` / ``MotorConfig`` / ``LaserConfig``
    Hardware description: devices, pins, timings.
``ScanConfig``
    Acquisition parameters the operator changes between scans.
``DetectorConfig`` / ``ReconstructionConfig`` / ``FilterConfig``
    Vision and geometry tuning.
``SimulationConfig``
    The synthetic rig used by simulation mode.

Values are validated on load and on every update, so an invalid configuration
fails loudly instead of producing a silently wrong point cloud.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from scanner_core import paths
from scanner_core.errors import ConfigurationError

APP_VERSION = "1.0.0"
CONFIG_SCHEMA_VERSION = 2


class ScannerMode(str, Enum):
    """How the scanner talks (or does not talk) to physical devices."""

    #: Synthetic camera and motor.  No webcam, no GPIO, runs anywhere.
    SIMULATION = "simulation"
    #: No acquisition at all; only reconstruction of already captured sessions.
    OFFLINE = "offline"
    #: Real webcam, real 28BYJ-48 stepper, optional real laser control.
    PHYSICAL = "physical"

    @classmethod
    def parse(cls, value: Any) -> "ScannerMode":
        if isinstance(value, cls):
            return value

        try:
            return cls(str(value).strip().lower())
        except ValueError as error:
            valid = ", ".join(mode.value for mode in cls)
            raise ConfigurationError(
                f"Unknown scanner mode {value!r}. Valid modes: {valid}."
            ) from error


class LaserControlMode(str, Enum):
    """How the laser is powered."""

    #: Laser is permanently powered from an external supply.
    ALWAYS_ON = "always_on"
    #: Laser is switched through a GPIO pin (usually via a transistor/relay).
    GPIO = "gpio"

    @classmethod
    def parse(cls, value: Any) -> "LaserControlMode":
        if isinstance(value, cls):
            return value

        try:
            return cls(str(value).strip().lower())
        except ValueError as error:
            valid = ", ".join(mode.value for mode in cls)
            raise ConfigurationError(
                f"Unknown laser control mode {value!r}. Valid modes: {valid}."
            ) from error


def _require_number(value: Any, name: str, *, minimum: float | None = None,
                    maximum: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ConfigurationError(f"{name} must be a number.") from error

    if number != number:  # NaN
        raise ConfigurationError(f"{name} must be a finite number.")

    if minimum is not None and number < minimum:
        raise ConfigurationError(f"{name} must be >= {minimum}.")

    if maximum is not None and number > maximum:
        raise ConfigurationError(f"{name} must be <= {maximum}.")

    return number


def _require_int(value: Any, name: str, *, minimum: int | None = None,
                 maximum: int | None = None) -> int:
    if isinstance(value, bool):
        raise ConfigurationError(f"{name} must be an integer.")

    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise ConfigurationError(f"{name} must be an integer.") from error

    if minimum is not None and number < minimum:
        raise ConfigurationError(f"{name} must be >= {minimum}.")

    if maximum is not None and number > maximum:
        raise ConfigurationError(f"{name} must be <= {maximum}.")

    return number


@dataclass
class CameraConfig:
    """USB webcam description.

    Defaults match the Fifine K420 settings that were validated in
    ``tools/scan_test.py``: 1280x720 MJPG.
    """

    device: int | str = 0
    width: int = 1280
    height: int = 720
    fps: int = 30
    fourcc: str | None = "MJPG"
    #: Frames read and thrown away before a scan capture, to drop stale
    #: buffered frames after the platform moved.
    flush_frames: int = 8
    #: Frames read once right after opening the device, to let auto-exposure
    #: settle before the first real capture.
    warmup_frames: int = 5
    read_retries: int = 3
    reopen_on_failure: bool = True
    stream_quality: int = 75
    #: Optional driver controls (``exposure``, ``gain``, ``brightness``...).
    #: Applied best-effort: every value is written and then read back.
    controls: dict[str, float] = field(default_factory=dict)

    def validate(self) -> None:
        if not isinstance(self.device, (int, str)):
            raise ConfigurationError("camera.device must be an index or a path.")

        self.width = _require_int(self.width, "camera.width", minimum=64, maximum=8192)
        self.height = _require_int(self.height, "camera.height", minimum=64, maximum=8192)
        self.fps = _require_int(self.fps, "camera.fps", minimum=1, maximum=240)
        self.flush_frames = _require_int(
            self.flush_frames, "camera.flush_frames", minimum=0, maximum=200
        )
        self.warmup_frames = _require_int(
            self.warmup_frames, "camera.warmup_frames", minimum=0, maximum=200
        )
        self.read_retries = _require_int(
            self.read_retries, "camera.read_retries", minimum=1, maximum=20
        )
        self.stream_quality = _require_int(
            self.stream_quality, "camera.stream_quality", minimum=10, maximum=100
        )

        if self.fourcc is not None:
            fourcc = str(self.fourcc).strip().upper()

            if len(fourcc) != 4:
                raise ConfigurationError("camera.fourcc must be exactly 4 characters.")

            self.fourcc = fourcc

        if not isinstance(self.controls, dict):
            raise ConfigurationError("camera.controls must be an object.")

        self.controls = {
            str(key): _require_number(value, f"camera.controls.{key}")
            for key, value in self.controls.items()
        }


@dataclass
class MotorConfig:
    """28BYJ-48 stepper driven through a ULN2003 board.

    ``steps_per_revolution`` is expressed in *half-steps of the output shaft*.
    4096 is the usual figure for a 28BYJ-48 but the real gear ratio varies
    between units, so this must be measured on the physical platform - see
    ``docs/wiring.md``.
    """

    pins: tuple[int, int, int, int] = (17, 27, 22, 23)
    steps_per_revolution: int = 4096
    step_delay: float = 0.002
    direction: int = 1
    release_after_move: bool = True
    #: Extra pause after the coils are released, before reading the camera.
    settle_after_release: float = 0.1

    def validate(self) -> None:
        pins = tuple(self.pins)

        if len(pins) != 4:
            raise ConfigurationError("motor.pins must contain exactly 4 BCM pins.")

        validated: list[int] = []

        for index, pin in enumerate(pins):
            validated.append(_require_int(pin, f"motor.pins[{index}]", minimum=0, maximum=27))

        if len(set(validated)) != 4:
            raise ConfigurationError("motor.pins must not repeat a GPIO number.")

        self.pins = (validated[0], validated[1], validated[2], validated[3])

        self.steps_per_revolution = _require_int(
            self.steps_per_revolution,
            "motor.steps_per_revolution",
            minimum=8,
            maximum=1_000_000,
        )
        self.step_delay = _require_number(
            self.step_delay, "motor.step_delay", minimum=0.0005, maximum=1.0
        )
        self.settle_after_release = _require_number(
            self.settle_after_release,
            "motor.settle_after_release",
            minimum=0.0,
            maximum=10.0,
        )

        direction = _require_int(self.direction, "motor.direction")

        if direction not in (1, -1):
            raise ConfigurationError("motor.direction must be 1 or -1.")

        self.direction = direction
        self.release_after_move = bool(self.release_after_move)


@dataclass
class LaserConfig:
    """Laser power / control description.

    The reference build powers the laser from an external supply and never
    switches it, which is why ``always_on`` is the default and ``gpio_pin`` is
    ``None``.  A GPIO pin is only used when the operator explicitly wires one
    and sets it here - the software never assumes a pin.
    """

    control: LaserControlMode = LaserControlMode.ALWAYS_ON
    gpio_pin: int | None = None
    active_high: bool = True
    #: Time to wait after switching the laser before trusting the image.
    switch_delay: float = 0.25

    def validate(self) -> None:
        self.control = LaserControlMode.parse(self.control)

        self.switch_delay = _require_number(
            self.switch_delay, "laser.switch_delay", minimum=0.0, maximum=10.0
        )
        self.active_high = bool(self.active_high)

        if self.control is LaserControlMode.GPIO:
            if self.gpio_pin is None:
                raise ConfigurationError(
                    "laser.gpio_pin is required when laser.control is 'gpio'. "
                    "Set the pin you physically wired; the software never guesses one."
                )

            self.gpio_pin = _require_int(
                self.gpio_pin, "laser.gpio_pin", minimum=0, maximum=27
            )
        elif self.gpio_pin is not None:
            self.gpio_pin = _require_int(
                self.gpio_pin, "laser.gpio_pin", minimum=0, maximum=27
            )

    @property
    def is_software_controlled(self) -> bool:
        return self.control is LaserControlMode.GPIO and self.gpio_pin is not None


@dataclass
class ScanConfig:
    """Acquisition parameters for one scan."""

    capture_count: int = 60
    #: Seconds to wait after the platform stops before flushing the camera.
    settle_delay: float = 0.8
    #: Extra seconds after a capture, mostly useful for slow USB buses.
    capture_delay: float = 0.05
    save_raw_frames: bool = True
    #: Capture a laser-off frame next to every laser-on frame.  Requires
    #: ``laser.control == 'gpio'``; hugely improves detection on bright scenes.
    capture_laser_off_frames: bool = False
    auto_reconstruct: bool = True

    def validate(self) -> None:
        self.capture_count = _require_int(
            self.capture_count, "scan.capture_count", minimum=2, maximum=5000
        )
        self.settle_delay = _require_number(
            self.settle_delay, "scan.settle_delay", minimum=0.0, maximum=60.0
        )
        self.capture_delay = _require_number(
            self.capture_delay, "scan.capture_delay", minimum=0.0, maximum=60.0
        )
        self.save_raw_frames = bool(self.save_raw_frames)
        self.capture_laser_off_frames = bool(self.capture_laser_off_frames)
        self.auto_reconstruct = bool(self.auto_reconstruct)


@dataclass
class DetectorConfig:
    """Red laser line detector tuning.

    The default HSV bands come from ``tools/detect_red.py`` and cover both ends
    of the red hue wrap-around (0-10 and 170-180 in OpenCV's 0-179 hue scale).
    """

    method: str = "hsv"
    #: ``rows`` finds one sub-pixel column per image row (vertical laser line).
    #: ``columns`` does the transpose (horizontal laser line).
    scan_axis: str = "rows"
    hue_low_min: int = 0
    hue_low_max: int = 10
    hue_high_min: int = 170
    hue_high_max: int = 179
    saturation_min: int = 80
    value_min: int = 80
    blur_kernel: int = 3
    morphology_kernel: int = 3
    min_component_area: int = 40
    #: Rows whose laser run is thinner/thicker than this are discarded.
    min_line_thickness: int = 1
    max_line_thickness: int = 60
    min_confidence: float = 0.12
    #: A detection with fewer accepted rows than this is rejected outright.
    min_points: int = 20
    #: Region of interest as fractions of the image: (x, y, width, height).
    roi: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
    #: Threshold on the laser-on minus laser-off difference image.
    difference_threshold: int = 25

    def validate(self) -> None:
        method = str(self.method).strip().lower()

        if method not in {"hsv", "difference"}:
            raise ConfigurationError("detector.method must be 'hsv' or 'difference'.")

        self.method = method

        scan_axis = str(self.scan_axis).strip().lower()

        if scan_axis not in {"rows", "columns"}:
            raise ConfigurationError("detector.scan_axis must be 'rows' or 'columns'.")

        self.scan_axis = scan_axis

        self.hue_low_min = _require_int(self.hue_low_min, "detector.hue_low_min", minimum=0, maximum=179)
        self.hue_low_max = _require_int(self.hue_low_max, "detector.hue_low_max", minimum=0, maximum=179)
        self.hue_high_min = _require_int(self.hue_high_min, "detector.hue_high_min", minimum=0, maximum=179)
        self.hue_high_max = _require_int(self.hue_high_max, "detector.hue_high_max", minimum=0, maximum=179)

        if self.hue_low_min > self.hue_low_max:
            raise ConfigurationError("detector.hue_low_min must be <= detector.hue_low_max.")

        if self.hue_high_min > self.hue_high_max:
            raise ConfigurationError("detector.hue_high_min must be <= detector.hue_high_max.")

        self.saturation_min = _require_int(
            self.saturation_min, "detector.saturation_min", minimum=0, maximum=255
        )
        self.value_min = _require_int(
            self.value_min, "detector.value_min", minimum=0, maximum=255
        )
        self.difference_threshold = _require_int(
            self.difference_threshold, "detector.difference_threshold", minimum=1, maximum=255
        )

        self.blur_kernel = _require_int(
            self.blur_kernel, "detector.blur_kernel", minimum=0, maximum=31
        )

        if self.blur_kernel and self.blur_kernel % 2 == 0:
            raise ConfigurationError("detector.blur_kernel must be 0 or an odd number.")

        self.morphology_kernel = _require_int(
            self.morphology_kernel, "detector.morphology_kernel", minimum=0, maximum=31
        )
        self.min_component_area = _require_int(
            self.min_component_area, "detector.min_component_area", minimum=0, maximum=1_000_000
        )
        self.min_line_thickness = _require_int(
            self.min_line_thickness, "detector.min_line_thickness", minimum=1, maximum=500
        )
        self.max_line_thickness = _require_int(
            self.max_line_thickness, "detector.max_line_thickness", minimum=1, maximum=2000
        )

        if self.min_line_thickness > self.max_line_thickness:
            raise ConfigurationError(
                "detector.min_line_thickness must be <= detector.max_line_thickness."
            )

        self.min_confidence = _require_number(
            self.min_confidence, "detector.min_confidence", minimum=0.0, maximum=1.0
        )
        self.min_points = _require_int(
            self.min_points, "detector.min_points", minimum=1, maximum=100_000
        )

        roi = tuple(self.roi)

        if len(roi) != 4:
            raise ConfigurationError("detector.roi must have 4 values: x, y, width, height.")

        x, y, width, height = (
            _require_number(roi[0], "detector.roi.x", minimum=0.0, maximum=1.0),
            _require_number(roi[1], "detector.roi.y", minimum=0.0, maximum=1.0),
            _require_number(roi[2], "detector.roi.width", minimum=0.01, maximum=1.0),
            _require_number(roi[3], "detector.roi.height", minimum=0.01, maximum=1.0),
        )

        if x + width > 1.0001 or y + height > 1.0001:
            raise ConfigurationError("detector.roi must stay inside the image.")

        self.roi = (x, y, width, height)


@dataclass
class ReconstructionConfig:
    """Ray/plane triangulation limits, all distances in millimetres."""

    min_depth_mm: float = 40.0
    max_depth_mm: float = 1500.0
    #: Reject rays that hit the laser plane at a grazing angle: the
    #: intersection becomes numerically meaningless.
    min_incidence: float = 0.02
    #: Points further than this from the rotation axis are discarded.
    max_radius_mm: float = 250.0
    min_height_mm: float = -50.0
    max_height_mm: float = 500.0
    undistort: bool = True

    def validate(self) -> None:
        self.min_depth_mm = _require_number(
            self.min_depth_mm, "reconstruction.min_depth_mm", minimum=0.1
        )
        self.max_depth_mm = _require_number(
            self.max_depth_mm, "reconstruction.max_depth_mm", minimum=1.0
        )

        if self.min_depth_mm >= self.max_depth_mm:
            raise ConfigurationError(
                "reconstruction.min_depth_mm must be < reconstruction.max_depth_mm."
            )

        self.min_incidence = _require_number(
            self.min_incidence, "reconstruction.min_incidence", minimum=1e-6, maximum=1.0
        )
        self.max_radius_mm = _require_number(
            self.max_radius_mm, "reconstruction.max_radius_mm", minimum=1.0
        )
        self.min_height_mm = _require_number(self.min_height_mm, "reconstruction.min_height_mm")
        self.max_height_mm = _require_number(self.max_height_mm, "reconstruction.max_height_mm")

        if self.min_height_mm >= self.max_height_mm:
            raise ConfigurationError(
                "reconstruction.min_height_mm must be < reconstruction.max_height_mm."
            )

        self.undistort = bool(self.undistort)


@dataclass
class FilterConfig:
    """Point cloud clean-up applied after all captures are triangulated."""

    #: Voxel edge length in millimetres.  0 disables down-sampling.
    voxel_size_mm: float = 0.6
    min_confidence: float = 0.15
    statistical_outlier_enabled: bool = True
    statistical_neighbours: int = 12
    statistical_std_ratio: float = 2.0
    radius_outlier_enabled: bool = False
    radius_outlier_radius_mm: float = 3.0
    radius_outlier_min_neighbours: int = 4
    #: Guard rail for the Raspberry Pi: skip O(n*k) filters above this size.
    max_points_for_outlier_filters: int = 400_000

    def validate(self) -> None:
        self.voxel_size_mm = _require_number(
            self.voxel_size_mm, "filtering.voxel_size_mm", minimum=0.0, maximum=100.0
        )
        self.min_confidence = _require_number(
            self.min_confidence, "filtering.min_confidence", minimum=0.0, maximum=1.0
        )
        self.statistical_outlier_enabled = bool(self.statistical_outlier_enabled)
        self.statistical_neighbours = _require_int(
            self.statistical_neighbours, "filtering.statistical_neighbours", minimum=2, maximum=200
        )
        self.statistical_std_ratio = _require_number(
            self.statistical_std_ratio, "filtering.statistical_std_ratio", minimum=0.1, maximum=10.0
        )
        self.radius_outlier_enabled = bool(self.radius_outlier_enabled)
        self.radius_outlier_radius_mm = _require_number(
            self.radius_outlier_radius_mm,
            "filtering.radius_outlier_radius_mm",
            minimum=0.01,
            maximum=1000.0,
        )
        self.radius_outlier_min_neighbours = _require_int(
            self.radius_outlier_min_neighbours,
            "filtering.radius_outlier_min_neighbours",
            minimum=1,
            maximum=200,
        )
        self.max_points_for_outlier_filters = _require_int(
            self.max_points_for_outlier_filters,
            "filtering.max_points_for_outlier_filters",
            minimum=1000,
            maximum=50_000_000,
        )


@dataclass
class SimulationConfig:
    """The virtual scanner rig used by simulation mode.

    These numbers describe a *synthetic* scanner, not the physical one.  They
    are never written to the calibration store and physical scans refuse to use
    them, so a simulated run can never be mistaken for a calibrated one.
    """

    image_width: int = 640
    image_height: int = 480
    focal_length_px: float = 700.0
    #: Object placed on the virtual turntable.
    shape: str = "cylinder"
    shape_radius_mm: float = 40.0
    shape_height_mm: float = 80.0
    #: Distance from the virtual camera to the rotation axis.
    camera_distance_mm: float = 320.0
    camera_height_mm: float = 60.0
    #: Angle between the laser plane and the camera optical axis, in degrees.
    laser_angle_deg: float = 30.0
    laser_line_thickness_px: float = 2.2
    noise_level: float = 6.0
    seed: int = 20260916

    def validate(self) -> None:
        self.image_width = _require_int(
            self.image_width, "simulation.image_width", minimum=64, maximum=4096
        )
        self.image_height = _require_int(
            self.image_height, "simulation.image_height", minimum=64, maximum=4096
        )
        self.focal_length_px = _require_number(
            self.focal_length_px, "simulation.focal_length_px", minimum=10.0
        )

        shape = str(self.shape).strip().lower()

        if shape not in {"cylinder", "sphere", "cube"}:
            raise ConfigurationError(
                "simulation.shape must be 'cylinder', 'sphere' or 'cube'."
            )

        self.shape = shape

        self.shape_radius_mm = _require_number(
            self.shape_radius_mm, "simulation.shape_radius_mm", minimum=1.0, maximum=1000.0
        )
        self.shape_height_mm = _require_number(
            self.shape_height_mm, "simulation.shape_height_mm", minimum=1.0, maximum=2000.0
        )
        self.camera_distance_mm = _require_number(
            self.camera_distance_mm, "simulation.camera_distance_mm", minimum=20.0
        )
        self.camera_height_mm = _require_number(
            self.camera_height_mm, "simulation.camera_height_mm"
        )
        self.laser_angle_deg = _require_number(
            self.laser_angle_deg, "simulation.laser_angle_deg", minimum=5.0, maximum=80.0
        )
        self.laser_line_thickness_px = _require_number(
            self.laser_line_thickness_px,
            "simulation.laser_line_thickness_px",
            minimum=0.5,
            maximum=20.0,
        )
        self.noise_level = _require_number(
            self.noise_level, "simulation.noise_level", minimum=0.0, maximum=80.0
        )
        self.seed = _require_int(self.seed, "simulation.seed", minimum=0)


@dataclass
class ScannerConfig:
    """Root configuration object."""

    mode: ScannerMode = ScannerMode.SIMULATION
    schema_version: int = CONFIG_SCHEMA_VERSION
    camera: CameraConfig = field(default_factory=CameraConfig)
    motor: MotorConfig = field(default_factory=MotorConfig)
    laser: LaserConfig = field(default_factory=LaserConfig)
    scan: ScanConfig = field(default_factory=ScanConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    reconstruction: ReconstructionConfig = field(default_factory=ReconstructionConfig)
    filtering: FilterConfig = field(default_factory=FilterConfig)
    simulation: SimulationConfig = field(default_factory=SimulationConfig)

    def validate(self) -> "ScannerConfig":
        self.mode = ScannerMode.parse(self.mode)
        self.schema_version = CONFIG_SCHEMA_VERSION

        for section in (
            self.camera,
            self.motor,
            self.laser,
            self.scan,
            self.detector,
            self.reconstruction,
            self.filtering,
            self.simulation,
        ):
            section.validate()

        if self.scan.capture_laser_off_frames and not self.laser.is_software_controlled:
            raise ConfigurationError(
                "scan.capture_laser_off_frames requires laser.control = 'gpio' "
                "with a configured laser.gpio_pin."
            )

        return self

    def to_dict(self) -> dict[str, Any]:
        return _to_plain(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ScannerConfig":
        config = cls()

        if not data:
            return config.validate()

        if "mode" in data:
            config.mode = ScannerMode.parse(data["mode"])

        sections = {
            "camera": config.camera,
            "motor": config.motor,
            "laser": config.laser,
            "scan": config.scan,
            "detector": config.detector,
            "reconstruction": config.reconstruction,
            "filtering": config.filtering,
            "simulation": config.simulation,
        }

        for name, section in sections.items():
            payload = data.get(name)

            if payload is None:
                continue

            if not isinstance(payload, dict):
                raise ConfigurationError(f"Configuration section '{name}' must be an object.")

            _apply_section(section, payload, name)

        return config.validate()

    def copy(self) -> "ScannerConfig":
        return ScannerConfig.from_dict(self.to_dict())


def _apply_section(section: Any, payload: dict[str, Any], name: str) -> None:
    known = {f.name for f in fields(section)}

    for key, value in payload.items():
        if key not in known:
            raise ConfigurationError(f"Unknown setting '{name}.{key}'.")

        current = getattr(section, key)

        if isinstance(current, tuple) and isinstance(value, list):
            value = tuple(value)

        setattr(section, key, value)


def _to_plain(value: Any) -> Any:
    """Convert dataclasses, enums and tuples into JSON friendly values."""

    if is_dataclass(value) and not isinstance(value, type):
        return {key: _to_plain(item) for key, item in asdict(value).items()}

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, dict):
        return {str(key): _to_plain(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [_to_plain(item) for item in value]

    if isinstance(value, Path):
        return str(value)

    return value


class ConfigStore:
    """Thread-safe holder and JSON persistence for :class:`ScannerConfig`."""

    def __init__(self, path: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._path = path
        self._config = ScannerConfig().validate()
        self._loaded = False

    @property
    def path(self) -> Path:
        return self._path if self._path is not None else paths.CONFIG_FILE

    def load(self) -> ScannerConfig:
        """Load the configuration from disk, falling back to defaults."""

        with self._lock:
            path = self.path

            if path.exists():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    self._config = ScannerConfig.from_dict(raw)
                except (OSError, json.JSONDecodeError, ConfigurationError):
                    # A broken file must not stop the scanner from booting; the
                    # caller logs the problem and the defaults are used.
                    self._config = ScannerConfig().validate()
                    raise
                finally:
                    self._loaded = True
            else:
                self._config = ScannerConfig().validate()
                self._loaded = True

            return self._config.copy()

    def save(self) -> Path:
        with self._lock:
            path = self.path
            path.parent.mkdir(parents=True, exist_ok=True)

            payload = self._config.to_dict()
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

            return path

    def get(self) -> ScannerConfig:
        """Return an isolated copy so callers cannot mutate shared state."""

        with self._lock:
            if not self._loaded:
                try:
                    self.load()
                except Exception:  # noqa: BLE001 - defaults already restored
                    self._loaded = True

            return self._config.copy()

    def replace(self, config: ScannerConfig) -> ScannerConfig:
        with self._lock:
            self._config = config.validate()
            self._loaded = True

            return self._config.copy()

    def update(self, patch: dict[str, Any], *, persist: bool = True) -> ScannerConfig:
        """Merge ``patch`` into the current configuration.

        The merge happens on a copy; the live configuration is only replaced
        once the whole patch validates, so a rejected update leaves the running
        scanner untouched.
        """

        with self._lock:
            merged = _deep_merge(self.get().to_dict(), patch)
            config = ScannerConfig.from_dict(merged)

            self._config = config
            self._loaded = True

            if persist:
                self.save()

            return self._config.copy()

    def set_mode(self, mode: Any, *, persist: bool = True) -> ScannerConfig:
        return self.update({"mode": ScannerMode.parse(mode).value}, persist=persist)


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(patch, dict):
        raise ConfigurationError("Configuration patch must be an object.")

    result = dict(base)

    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value

    return result


#: Process-wide configuration store used by the web application and the CLIs.
config_store = ConfigStore()


def get_config() -> ScannerConfig:
    return config_store.get()
