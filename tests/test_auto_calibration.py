"""Automatic calibration with the board standing on the turntable.

A fake camera renders the synthetic board (``_board_scene.py``) at whatever
angle the simulated motor has reached, so the capture loop, the stored run and
the computation are all exercised exactly as on the scanner - and the result
can be compared with the true geometry of the virtual rig.
"""

from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from _board_scene import BoardTurntableScene
from scanner_core.calibration.auto import (
    capture_step,
    compute_auto_calibration,
    load_run,
    reset_run,
    summarize_run,
)
from scanner_core.calibration.camera import CheckerboardSpec
from scanner_core.calibration.store import CalibrationStore
from scanner_core.config import DetectorConfig, MotorConfig
from scanner_core.errors import CalibrationError, ScanCancelled
from scanner_core.geometry import intersect_rays_with_plane, pixels_to_camera_rays
from scanner_core.hardware.motor import SimulatedMotor

SPEC = CheckerboardSpec(columns=9, rows=6, square_size_mm=10.0)
STEPS_PER_REVOLUTION = 4096
STOPS = 24
TILTS = {1: 0.0, 2: 18.0, 3: -18.0}


class FakeTurntableCamera:
    """Renders the synthetic board at the angle the motor has reached."""

    def __init__(self, scene: BoardTurntableScene, motor: SimulatedMotor) -> None:
        self.scene = scene
        self.motor = motor
        self.tilt = 0.0
        self.laser = True
        self._open = False

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def read(self):
        angle = self.motor.position / STEPS_PER_REVOLUTION * 360.0

        return self.scene.render(angle, self.tilt, laser=self.laser)

    def capture(self, *, flush_frames=None):
        return self.read()

    @property
    def resolution(self):
        return self.scene.width, self.scene.height

    def describe(self):
        return {}


def _rig(scene: BoardTurntableScene):
    motor = SimulatedMotor(MotorConfig(steps_per_revolution=STEPS_PER_REVOLUTION))

    return FakeTurntableCamera(scene, motor), motor


def _capture_all(directory, camera, motor, steps=(1, 2, 3)):
    for step in steps:
        camera.tilt = TILTS[step]

        capture_step(
            step,
            camera=camera,
            motor=motor,
            spec=SPEC,
            directory=directory,
            steps_per_revolution=STEPS_PER_REVOLUTION,
            stops=STOPS,
            settle_delay=0.0,
        )


def _detector():
    config = DetectorConfig(min_points=10)
    config.validate()

    from scanner_core.laser_detection import LaserLineDetector

    return LaserLineDetector(config)


def _triangulation_error_mm(scene: BoardTurntableScene, store: CalibrationStore) -> np.ndarray:
    """Error of laser points triangulated with the recovered calibration.

    True points on the laser sheet, inside the volume an object occupies, are
    projected with the true camera and then triangulated back with the
    calibration under test.  This is the error a scan inherits.
    """

    bundle = store.load_bundle()
    normal, offset = scene.laser_plane_camera
    axis_origin = scene.axis_origin_camera
    up = scene.axis_direction_camera

    # Two in-plane directions: the axis itself and the horizontal one.
    across = np.cross(normal, up)
    across /= np.linalg.norm(across)

    points = np.array(
        [
            axis_origin + radius * across + height * up
            for radius in np.linspace(-40.0, 40.0, 9)
            for height in np.linspace(0.0, 70.0, 8)
        ]
    )

    pixels, _ = cv2.projectPoints(
        points, np.zeros(3), np.zeros(3), scene.camera_matrix, scene.dist
    )

    rays = pixels_to_camera_rays(
        pixels.reshape(-1, 2),
        bundle.camera.camera_matrix,
        bundle.camera.distortion_coefficients,
        undistort=True,
    )
    recovered, valid = intersect_rays_with_plane(rays, bundle.laser.plane, min_incidence=0.0)

    assert np.all(valid)

    return np.linalg.norm(recovered - points, axis=1)


@pytest.fixture
def scene():
    return BoardTurntableScene()


class TestCapture:
    def test_a_step_photographs_every_stop_and_records_the_angles(self, tmp_path, scene):
        camera, motor = _rig(scene)

        _capture_all(tmp_path, camera, motor, steps=(1,))

        run = load_run(tmp_path)
        frames = run["steps"]["1"]["frames"]

        assert run["steps"]["1"]["status"] == "complete"
        assert len(frames) == STOPS
        # Angles of the integer motor targets actually reached (4096 steps do
        # not divide by 24), not the ideal 15-degree fractions.
        assert [frame["angle_deg"] for frame in frames] == pytest.approx(
            [(index * STEPS_PER_REVOLUTION // STOPS) * 360.0 / STEPS_PER_REVOLUTION for index in range(STOPS)],
            abs=1e-4,
        )
        assert all((tmp_path / frame["file"]).exists() for frame in frames)

        # The board faces the camera for roughly a third of the revolution.
        found = sum(1 for frame in frames if frame["corners"])

        assert 6 <= found <= 12

        # The platform finishes the revolution, back where the operator put it.
        assert motor.position == STEPS_PER_REVOLUTION

    def test_rerunning_a_step_replaces_only_that_step(self, tmp_path, scene):
        camera, motor = _rig(scene)

        _capture_all(tmp_path, camera, motor, steps=(1, 2))
        first_step_two = load_run(tmp_path)["steps"]["2"]["started_at"]

        camera.tilt = 0.0
        capture_step(
            1,
            camera=camera,
            motor=motor,
            spec=SPEC,
            directory=tmp_path,
            steps_per_revolution=STEPS_PER_REVOLUTION,
            stops=STOPS,
            settle_delay=0.0,
        )

        run = load_run(tmp_path)

        assert len(run["steps"]["1"]["frames"]) == STOPS
        assert run["steps"]["2"]["started_at"] == first_step_two
        assert len(list((tmp_path / "step1").iterdir())) == STOPS

    def test_a_different_board_is_refused_mid_run(self, tmp_path, scene):
        camera, motor = _rig(scene)

        _capture_all(tmp_path, camera, motor, steps=(1,))

        with pytest.raises(CalibrationError, match="checkerboard"):
            capture_step(
                2,
                camera=camera,
                motor=motor,
                spec=CheckerboardSpec(columns=9, rows=6, square_size_mm=25.0),
                directory=tmp_path,
                steps_per_revolution=STEPS_PER_REVOLUTION,
                stops=STOPS,
                settle_delay=0.0,
            )

    def test_cancelling_marks_the_step_cancelled(self, tmp_path, scene):
        camera, motor = _rig(scene)
        calls = {"n": 0}

        def stop_after_a_few():
            calls["n"] += 1

            return calls["n"] > 40

        with pytest.raises(ScanCancelled):
            capture_step(
                1,
                camera=camera,
                motor=motor,
                spec=SPEC,
                directory=tmp_path,
                steps_per_revolution=STEPS_PER_REVOLUTION,
                stops=STOPS,
                settle_delay=0.0,
                should_cancel=stop_after_a_few,
            )

        run = load_run(tmp_path)

        assert run["steps"]["1"]["status"] == "cancelled"
        assert summarize_run(run)["ready_to_compute"] is False

    def test_reset_removes_the_run(self, tmp_path, scene):
        camera, motor = _rig(scene)

        _capture_all(tmp_path, camera, motor, steps=(1,))
        reset_run(tmp_path)

        assert load_run(tmp_path) is None
        assert summarize_run(None)["steps"]["1"]["status"] == "pending"


class TestComputation:
    @pytest.mark.parametrize("sense", [1, -1])
    def test_recovers_the_rig_geometry(self, tmp_path, sense):
        scene = BoardTurntableScene(rotation_sense=sense)
        camera, motor = _rig(scene)
        store = CalibrationStore(directory=tmp_path / "calibration")

        _capture_all(tmp_path / "run", camera, motor)

        report = compute_auto_calibration(
            tmp_path / "run", _detector(), store=store, diagnostics_dir=tmp_path / "diag"
        )

        assert report.issues == []
        assert report.saved is True

        bundle = store.load_bundle()

        assert bundle.is_ready()

        # Lens.
        assert bundle.camera.camera_matrix[0][0] == pytest.approx(scene.focal_px, rel=0.015)
        assert bundle.camera.camera_matrix[1][1] == pytest.approx(scene.focal_px, rel=0.015)

        # Turntable axis: direction, position and sense of rotation.
        truth_up = scene.axis_direction_camera
        direction = np.asarray(bundle.turntable.axis_direction)
        offset = np.asarray(bundle.turntable.axis_origin) - scene.axis_origin_camera
        off_axis = offset - (offset @ truth_up) * truth_up

        assert np.degrees(np.arccos(np.clip(direction @ truth_up, -1, 1))) < 0.5
        assert np.linalg.norm(off_axis) < 1.0
        assert bundle.turntable.rotation_direction == sense

        # Laser plane, judged by what a scan inherits from it.  Measured: about
        # 0.48 mm mean at this 640x480 test resolution, where one pixel of
        # principal-point error is ~0.3 mm; about 0.31 mm mean / 0.41 mm max at
        # the real 1280x720 with 36 stops.
        errors = _triangulation_error_mm(scene, store)

        assert float(np.mean(errors)) < 0.6
        assert float(np.max(errors)) < 0.9

        # One overlay per step, for the operator to look at.
        assert sorted(report.diagnostics) == ["step1.jpg", "step2.jpg", "step3.jpg"]
        assert all((tmp_path / "diag" / name).exists() for name in report.diagnostics)

        # The report is kept with the run, for the dashboard.
        assert load_run(tmp_path / "run")["result"]["saved"] is True

    def test_without_a_visible_laser_nothing_is_saved(self, tmp_path, scene):
        camera, motor = _rig(scene)
        camera.laser = False
        store = CalibrationStore(directory=tmp_path / "calibration")

        _capture_all(tmp_path / "run", camera, motor)

        report = compute_auto_calibration(tmp_path / "run", _detector(), store=store)

        assert report.saved is False
        assert any(issue.startswith("Laser") for issue in report.issues)

        # Lens and axis were still computed and reported...
        assert report.camera["views"] >= 20
        assert report.turntable["rms_mm"] < 0.5

        # ...but nothing reached the store: the three are saved together.
        bundle = store.load_bundle()

        assert bundle.camera is None and bundle.laser is None and bundle.turntable is None

    def test_a_wobbling_platform_is_reported(self, tmp_path, scene):
        camera, motor = _rig(scene)
        store = CalibrationStore(directory=tmp_path / "calibration")

        # The axis tilts by 4 degrees between placements, as a platform does when
        # it sags under an off-centre board.
        for step, axis_tilt in ((1, 0.0), (2, 4.0), (3, -4.0)):
            camera.scene = BoardTurntableScene(axis_tilt_deg=axis_tilt)
            _capture_all(tmp_path / "run", camera, motor, steps=(step,))

        report = compute_auto_calibration(tmp_path / "run", _detector(), store=store)

        assert report.saved is False
        assert any("disagree about the axis" in issue for issue in report.issues)

    def test_nothing_captured(self, tmp_path):
        with pytest.raises(CalibrationError, match="Nothing has been captured"):
            compute_auto_calibration(tmp_path / "run", _detector())

    def test_the_run_record_is_plain_json(self, tmp_path, scene):
        camera, motor = _rig(scene)

        _capture_all(tmp_path, camera, motor, steps=(1,))

        json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))


class TestThroughTheController:
    """The same path the dashboard button takes: background steps, then compute."""

    def _controller(self, tmp_path, scene, mode="physical"):
        import time  # noqa: F401 - used by _wait

        from scanner_core.calibration.store import CalibrationBundle
        from scanner_core.config import ConfigStore, LaserConfig, ScannerMode
        from scanner_core.hardware.factory import HardwareSet
        from scanner_core.hardware.laser import ExternalLaser
        from scanner_core.pipeline import ScanController
        from scanner_core.session import SessionManager
        from scanner_core.state import StateStore

        config = ConfigStore(path=tmp_path / "config.json")
        config.update(
            {"mode": mode, "scan": {"settle_delay": 0.0}, "detector": {"min_points": 10}},
            persist=False,
        )

        controller = ScanController(
            configuration=config,
            state=StateStore(),
            sessions=SessionManager(root=tmp_path / "scans"),
            calibration=CalibrationStore(directory=tmp_path / "calibration"),
        )
        controller.auto_calibration_stops = STOPS

        camera, motor = _rig(scene)
        hardware = HardwareSet(
            camera=camera,
            motor=motor,
            laser=ExternalLaser(LaserConfig()),
            calibration=CalibrationBundle(),
            mode=ScannerMode.PHYSICAL,
        )

        # Stand-in for the real webcam and GPIO motor.
        controller._ensure_hardware = lambda _config: hardware

        return controller, camera

    @staticmethod
    def _wait(controller, timeout=180.0):
        import time

        deadline = time.monotonic() + timeout

        while controller.auto_calibration_status()["progress"]["running"]:
            assert time.monotonic() < deadline, "calibration step did not finish"
            time.sleep(0.05)

    def test_three_steps_then_compute(self, tmp_path, scene):
        controller, camera = self._controller(tmp_path, scene)

        for step in (1, 2, 3):
            camera.tilt = TILTS[step]

            controller.start_auto_calibration_step(step, SPEC)
            self._wait(controller)

            status = controller.auto_calibration_status()

            assert status["progress"]["error"] is None
            assert status["run"]["steps"][str(step)]["status"] == "complete"
            assert status["run"]["steps"][str(step)]["board_found"] >= 6

        assert not controller.is_running
        assert controller.auto_calibration_status()["run"]["ready_to_compute"] is True

        report = controller.compute_auto_calibration()

        assert report.saved is True
        assert controller._calibration.load_bundle().is_ready()

        # A physical scan now passes the calibration check it used to fail.
        session = controller._prepare_scan(controller._config_store.get())

        assert session.metadata()["calibration"]["camera"]["source"] == "auto_turntable"

    def test_one_thing_at_a_time_and_stop_works(self, tmp_path, scene):
        from scanner_core.errors import ScanAlreadyRunning

        controller, _ = self._controller(tmp_path, scene)

        controller.start_auto_calibration_step(1, SPEC)

        try:
            with pytest.raises(ScanAlreadyRunning):
                controller.start_auto_calibration_step(2, SPEC)

            with pytest.raises(ScanAlreadyRunning):
                controller.compute_auto_calibration()
        finally:
            assert controller.request_stop() is True
            self._wait(controller)

        status = controller.auto_calibration_status()

        assert status["progress"]["message"] == "Step 1 cancelled."
        assert status["run"]["steps"]["1"]["status"] == "cancelled"
        assert not controller.is_running

    def test_refused_outside_physical_mode(self, tmp_path, scene):
        from scanner_core.errors import ConfigurationError

        controller, _ = self._controller(tmp_path, scene, mode="simulation")

        with pytest.raises(ConfigurationError, match="physical"):
            controller.start_auto_calibration_step(1, SPEC)

        # The refusal must not leave the scanner locked.
        assert not controller.is_running
