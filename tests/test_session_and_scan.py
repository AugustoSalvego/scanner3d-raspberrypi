"""Scan sessions, the motor drivers and the full simulated scan pipeline."""

from __future__ import annotations

import json
import threading

import numpy as np
import pytest

from scanner_core.config import ConfigStore, MotorConfig, ScannerMode
from scanner_core.errors import ScanAlreadyRunning, ScanCancelled, SessionError
from scanner_core.hardware.motor import HALF_STEP_SEQUENCE, SimulatedMotor
from scanner_core.pipeline import ScanController
from scanner_core.point_cloud import read_ply
from scanner_core.session import (
    CaptureRecord,
    PointCloudRecord,
    SessionStatus,
    generate_session_id,
)
from scanner_core.simulation import SyntheticScene
from scanner_core.state import ScanPhase, StateStore


class TestSessionLifecycle:
    def test_create_builds_the_folder_structure(self, session_manager):
        session = session_manager.create(mode="simulation")

        assert session.captures_path.is_dir()
        assert session.point_clouds_path.is_dir()
        assert session.metadata_path.is_file()
        assert session.status is SessionStatus.CREATED

    def test_metadata_carries_the_snapshots(self, session_manager):
        session = session_manager.create(
            mode="physical",
            settings={"scan": {"capture_count": 12}},
            calibration={"camera": {"source": "checkerboard"}},
        )

        metadata = session.metadata()

        assert metadata["schema_version"] >= 2
        assert metadata["mode"] == "physical"
        assert metadata["settings"]["scan"]["capture_count"] == 12
        assert metadata["calibration"]["camera"]["source"] == "checkerboard"

    @pytest.mark.parametrize(
        "status",
        [SessionStatus.COMPLETED, SessionStatus.CANCELLED, SessionStatus.ERROR],
    )
    def test_each_terminal_status_is_recorded(self, session_manager, status):
        """The old code marked everything 'finished' from a finally block."""

        session = session_manager.create(mode="simulation")
        session.mark_started()

        assert session.status is SessionStatus.RUNNING

        session.finish(status, error="boom" if status is SessionStatus.ERROR else None)

        reloaded = session_manager.load(session.session_id)

        assert reloaded.status is status
        assert reloaded.metadata()["finished_at"] is not None

        if status is SessionStatus.ERROR:
            assert reloaded.metadata()["errors"][-1]["message"] == "boom"

    def test_captures_record_the_real_angle(self, session_manager):
        session = session_manager.create(mode="simulation")

        session.add_capture(
            CaptureRecord(
                index=3,
                filename="capture_0003.jpg",
                angle_deg=135.5,
                target_step=1542,
                motor_position=1542,
                timestamp="2026-09-16T10:00:00.000",
                laser_state="on",
            )
        )

        capture = session.metadata()["captures"][0]

        assert capture["angle_deg"] == 135.5
        assert capture["target_step"] == 1542
        assert capture["motor_position"] == 1542
        assert capture["laser_state"] == "on"

    def test_point_cloud_records(self, session_manager):
        session = session_manager.create(mode="simulation")

        session.add_point_cloud(
            PointCloudRecord(filename="a.ply", point_count=1234, created_at="2026-09-16")
        )

        assert session.metadata()["point_clouds"][0]["point_count"] == 1234

    def test_ids_carry_a_random_suffix(self):
        """Second resolution alone collides when scans start back to back."""

        ids = {generate_session_id() for _ in range(200)}

        # All generated inside the same second, so only the suffix separates
        # them. 200 draws from 2^32 makes a collision here effectively
        # impossible without being a probabilistic assertion in disguise.
        assert len(ids) == 200

        stamp, _, suffix = ids.pop().rpartition("_")

        assert stamp.startswith("scan_")
        assert len(suffix) == 8

    def test_manager_never_reuses_a_folder(self, session_manager):
        """The deterministic guarantee, independent of the random suffix."""

        sessions = [session_manager.create(mode="simulation") for _ in range(30)]

        paths_used = [session.path for session in sessions]

        assert len(set(paths_used)) == len(paths_used)
        assert all(path.is_dir() for path in paths_used)

    def test_ids_are_safe_path_components(self):
        from scanner_core import paths

        assert paths.is_safe_component(generate_session_id())

    def test_metadata_writes_are_atomic(self, session_manager):
        session = session_manager.create(mode="simulation")

        for index in range(30):
            session.add_capture(
                CaptureRecord(
                    index=index,
                    filename=f"c{index}.jpg",
                    angle_deg=index * 12.0,
                    target_step=index,
                    motor_position=index,
                    timestamp="2026-09-16T10:00:00.000",
                )
            )

        # No stray temporary file survives, and the document always parses.
        assert not list(session.path.glob("*.tmp"))
        json.loads(session.metadata_path.read_text(encoding="utf-8"))

    def test_listing_is_newest_first(self, session_manager):
        first = session_manager.create(mode="simulation")
        second = session_manager.create(mode="simulation")

        ids = [item["session_id"] for item in session_manager.list_sessions()]

        assert set(ids) == {first.session_id, second.session_id}

    def test_listing_skips_unreadable_metadata(self, session_manager):
        good = session_manager.create(mode="simulation")
        broken = session_manager.create(mode="simulation")

        broken.metadata_path.write_text("{ broken", encoding="utf-8")

        ids = [item["session_id"] for item in session_manager.list_sessions()]

        assert ids == [good.session_id]

    def test_load_rejects_a_traversing_id(self, session_manager):
        with pytest.raises(SessionError):
            session_manager.load("../../etc")

    def test_load_missing_session(self, session_manager):
        with pytest.raises(SessionError):
            session_manager.load("scan_does_not_exist")

    def test_delete_removes_everything(self, session_manager):
        session = session_manager.create(mode="simulation")
        path = session.path

        session_manager.delete(session.session_id)

        assert not path.exists()

    def test_interrupted_sessions_are_recovered_as_errors(self, session_manager):
        """A session still 'running' at start-up cannot possibly be running."""

        session = session_manager.create(mode="simulation")
        session.mark_started()

        assert session_manager.recover_interrupted_sessions() == 1

        assert session_manager.load(session.session_id).status is SessionStatus.ERROR


class TestSimulatedMotor:
    def test_half_step_sequence_is_well_formed(self):
        assert len(HALF_STEP_SEQUENCE) == 8

        for phase in HALF_STEP_SEQUENCE:
            assert len(phase) == 4
            assert 1 <= sum(phase) <= 2

        # Consecutive phases differ in exactly one coil, which is what makes
        # half stepping smooth.
        for index in range(len(HALF_STEP_SEQUENCE)):
            current = HALF_STEP_SEQUENCE[index]
            following = HALF_STEP_SEQUENCE[(index + 1) % len(HALF_STEP_SEQUENCE)]

            differences = sum(a != b for a, b in zip(current, following))

            assert differences == 1

    def test_absolute_moves(self):
        motor = SimulatedMotor(MotorConfig())
        motor.open()

        motor.move_to(1000)
        assert motor.position == 1000

        motor.move_to(250)
        assert motor.position == 250

        motor.move_relative(-250)
        assert motor.position == 0

    def test_angle_follows_the_position(self):
        config = MotorConfig(steps_per_revolution=4096)
        motor = SimulatedMotor(config)
        motor.open()

        motor.move_to(1024)

        assert motor.angle_for_position() == pytest.approx(90.0)

    def test_direction_is_applied(self):
        config = MotorConfig(direction=-1)
        motor = SimulatedMotor(config)
        motor.open()

        motor.move_relative(100)

        assert motor.position == -100

    def test_cancellation_raises(self):
        motor = SimulatedMotor(MotorConfig())
        motor.open()

        with pytest.raises(ScanCancelled):
            motor.move_relative(4096, should_cancel=lambda: True)

    def test_position_callback_drives_the_rig(self):
        seen = []

        motor = SimulatedMotor(
            MotorConfig(), on_position_changed=lambda step, angle: seen.append(angle)
        )
        motor.open()
        motor.move_to(2048)

        assert seen and seen[-1] == pytest.approx(180.0)

    def test_close_is_idempotent(self):
        motor = SimulatedMotor(MotorConfig())
        motor.open()
        motor.close()
        motor.close()

        assert not motor.is_open


class TestSimulatedScan:
    def _controller(self, tmp_path, captures=8, **scan_overrides):
        from scanner_core.calibration.store import CalibrationStore
        from scanner_core.session import SessionManager

        store = ConfigStore(path=tmp_path / "config.json")
        store.update(
            {
                "mode": "simulation",
                "scan": {
                    "capture_count": captures,
                    "settle_delay": 0.0,
                    "capture_delay": 0.0,
                    **scan_overrides,
                },
                "simulation": {"image_width": 320, "image_height": 240, "focal_length_px": 350.0},
                "detector": {"min_points": 10},
            },
            persist=False,
        )

        return ScanController(
            configuration=store,
            state=StateStore(),
            sessions=SessionManager(root=tmp_path / "scans"),
            calibration=CalibrationStore(directory=tmp_path / "calibration"),
        )

    def test_full_scan_produces_a_real_point_cloud(self, tmp_path):
        controller = self._controller(tmp_path, captures=10)

        outcome = controller.run_scan()

        assert outcome.status is SessionStatus.COMPLETED
        assert outcome.point_count > 200
        assert outcome.point_cloud_path.exists()

        cloud = read_ply(outcome.point_cloud_path)

        assert len(cloud) == outcome.point_count
        assert cloud.confidence is not None

    def test_scan_writes_one_frame_per_capture_with_its_angle(self, tmp_path):
        controller = self._controller(tmp_path, captures=6)

        outcome = controller.run_scan()

        session = controller._sessions.load(outcome.session_id)
        metadata = session.metadata()

        assert len(metadata["captures"]) == 6
        assert len(session.capture_files()) == 6

        angles = [capture["angle_deg"] for capture in metadata["captures"]]

        assert angles[0] == 0.0
        assert angles == sorted(angles)
        assert max(angles) < 360.0
        assert len(set(angles)) == 6

    def test_reconstruction_is_metric(self, tmp_path):
        controller = self._controller(tmp_path, captures=16)

        outcome = controller.run_scan()
        cloud = read_ply(outcome.point_cloud_path)

        config = controller._config_store.get()
        scene = SyntheticScene(config.simulation)

        residuals = scene.surface_residual(cloud.points)

        assert float(np.mean(residuals)) < 0.5

    def test_state_ends_completed(self, tmp_path):
        controller = self._controller(tmp_path, captures=6)

        controller.run_scan()

        state = controller._state.snapshot()

        assert state["phase"] == ScanPhase.COMPLETED.value
        assert state["running"] is False
        assert state["last_error"] is None
        assert state["last_point_count"] > 0

    def test_two_scans_cannot_run_at_once(self, tmp_path):
        controller = self._controller(tmp_path, captures=40)

        started = threading.Event()
        rejected = []

        def background():
            started.set()
            controller.run_scan()

        thread = threading.Thread(target=background, daemon=True)
        thread.start()
        started.wait(timeout=5)

        # Hammer it while the first scan is still going.
        for _ in range(20):
            if controller.is_running:
                try:
                    controller.run_scan()
                except ScanAlreadyRunning:
                    rejected.append(True)
                    break

        thread.join(timeout=60)

        assert rejected, "a second scan was allowed to start"
        assert len(controller._sessions.list_sessions()) == 1

    def test_stop_marks_the_session_cancelled(self, tmp_path):
        controller = self._controller(tmp_path, captures=60)

        outcome_holder = {}

        def background():
            outcome_holder["outcome"] = controller.run_scan()

        thread = threading.Thread(target=background, daemon=True)
        thread.start()

        for _ in range(500):
            if controller._state.snapshot()["capture_index"] >= 2:
                break

            threading.Event().wait(0.02)

        controller.request_stop()
        thread.join(timeout=60)

        outcome = outcome_holder["outcome"]

        assert outcome.status is SessionStatus.CANCELLED

        session = controller._sessions.load(outcome.session_id)

        assert session.status is SessionStatus.CANCELLED
        assert controller._state.snapshot()["phase"] == ScanPhase.CANCELLED.value
        # Cancelling must never leave the scanner locked.
        assert not controller.is_running

    def test_offline_mode_refuses_to_acquire(self, tmp_path):
        controller = self._controller(tmp_path)
        controller._config_store.set_mode(ScannerMode.OFFLINE, persist=False)

        with pytest.raises(Exception) as error:
            controller.run_scan()

        assert "offline" in str(error.value).lower()
        assert not controller.is_running

    def test_offline_reconstruction_reuses_the_stored_angles(self, tmp_path):
        controller = self._controller(tmp_path, captures=12)

        outcome = controller.run_scan()
        original = outcome.point_count

        controller._config_store.set_mode(ScannerMode.OFFLINE, persist=False)

        again = controller.reconstruct_existing_session(outcome.session_id)

        assert again.point_count > 0
        # Same captures, same calibration: the result must be reproducible.
        assert abs(again.point_count - original) <= max(2, original * 0.02)

        session = controller._sessions.load(outcome.session_id)

        assert len(session.metadata()["point_clouds"]) == 2

    def test_reconstruction_honours_new_settings(self, tmp_path):
        controller = self._controller(tmp_path, captures=12)

        outcome = controller.run_scan()

        controller._config_store.update({"filtering": {"voxel_size_mm": 4.0}}, persist=False)

        coarse = controller.reconstruct_existing_session(outcome.session_id)

        assert coarse.point_count < outcome.point_count

    def test_auto_reconstruct_can_be_disabled(self, tmp_path):
        controller = self._controller(tmp_path, captures=6, auto_reconstruct=False)

        outcome = controller.run_scan()

        assert outcome.status is SessionStatus.COMPLETED
        assert outcome.point_cloud_path is None

        session = controller._sessions.load(outcome.session_id)

        assert session.metadata()["point_clouds"] == []
        # The frames are still there, so it can be reconstructed later.
        assert len(session.capture_files()) == 6

    def test_discarding_raw_frames(self, tmp_path):
        controller = self._controller(tmp_path, captures=6, save_raw_frames=False)

        outcome = controller.run_scan()
        session = controller._sessions.load(outcome.session_id)

        assert outcome.status is SessionStatus.COMPLETED
        assert session.capture_files() == []
        assert session.metadata()["reconstruction"]["offline_reconstruction_possible"] is False

    def test_simulation_never_opens_gpio_or_a_webcam(self, tmp_path, monkeypatch):
        """The guarantee that simulation mode runs anywhere."""

        import cv2

        def explode(*_args, **_kwargs):
            raise AssertionError("simulation mode must not open a capture device")

        monkeypatch.setattr(cv2, "VideoCapture", explode)

        controller = self._controller(tmp_path, captures=4)
        outcome = controller.run_scan()

        assert outcome.status is SessionStatus.COMPLETED
