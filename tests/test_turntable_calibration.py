"""Turntable axis estimation and checkerboard corner order.

Both are exercised on rendered images of a 9x6 board standing on a turntable
(see ``_board_scene.py``), so the recovered geometry can be compared with the
exact truth.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from _board_scene import BoardTurntableScene
from scanner_core.calibration.camera import (
    CheckerboardSpec,
    estimate_board_pose,
    find_checkerboard,
    normalize_corner_order,
)
from scanner_core.calibration.store import SOURCE_SYNTHETIC, CameraCalibration
from scanner_core.calibration.turntable import AxisObservation, estimate_turntable_axis
from scanner_core.geometry import orient_axis_upward, rotation_matrix_about_axis

SPEC = CheckerboardSpec(columns=9, rows=6, square_size_mm=10.0)


def _exact_camera(scene: BoardTurntableScene) -> CameraCalibration:
    profile = CameraCalibration(
        source=SOURCE_SYNTHETIC,
        camera_matrix=scene.camera_matrix.copy(),
        distortion_coefficients=scene.dist.copy(),
        image_size=(scene.width, scene.height),
        rms_reprojection_error=0.0,
    )
    profile.validate()

    return profile


@pytest.fixture
def scene():
    return BoardTurntableScene()


class TestCornerOrder:
    @pytest.mark.parametrize("angle", [-45.0, -20.0, 0.0, 20.0, 45.0])
    @pytest.mark.parametrize("tilt", [0.0, 18.0, -18.0])
    def test_both_detectors_agree_with_the_board_numbering(self, scene, monkeypatch, angle, tilt):
        image = scene.render(angle, tilt)
        truth = scene.true_corners(angle, tilt)

        sector_based = find_checkerboard(image, SPEC)

        monkeypatch.setattr(cv2, "findChessboardCornersSB", None)
        classic = find_checkerboard(image, SPEC)

        assert sector_based is not None and classic is not None
        # Before the fix the SB detector often started at the opposite corner.
        assert np.abs(sector_based.reshape(-1, 2) - truth).max() < 1.5
        assert np.abs(classic.reshape(-1, 2) - truth).max() < 1.5

    def test_normalization_is_idempotent_and_order_independent(self):
        grid = np.array([[x, y] for y in range(6) for x in range(9)], dtype=np.float32) * 20 + 100

        forward = normalize_corner_order(grid)
        backward = normalize_corner_order(grid[::-1])

        assert np.array_equal(forward, backward)
        assert np.array_equal(normalize_corner_order(forward), forward)
        assert tuple(forward[0, 0]) == (100.0, 100.0)


class TestAxisOrientation:
    @pytest.mark.parametrize("sense", [1, -1])
    def test_the_axis_always_points_up(self, sense):
        """The fit used to return the axis pointing down about half the time."""

        up = orient_axis_upward(np.array([0.0, -np.cos(0.2), -np.sin(0.2)]))
        rng = np.random.default_rng(3)

        for _ in range(60):
            tilt = rotation_matrix_about_axis(np.array([1.0, 0.0, 0.0]), np.radians(rng.uniform(-6, 6)))
            axis = tilt @ up
            origin = np.array([0.0, 60.0, 180.0])
            start = tilt @ np.array([35.0, 0.0, 0.0])

            observations = [
                AxisObservation(
                    commanded_angle_deg=angle,
                    point_camera=origin
                    + rotation_matrix_about_axis(axis, np.radians(angle) * sense) @ start
                    + rng.normal(0.0, 0.15, 3),
                )
                for angle in range(-60, 61, 15)
            ]

            estimate = estimate_turntable_axis(observations, 4096)

            assert float(estimate.axis_direction @ axis) > 0.995
            assert estimate.rotation_direction == sense

    @pytest.mark.parametrize("sense", [1, -1])
    def test_an_arc_straddling_zero_keeps_the_right_rotation_sense(self, sense):
        """A board is only seen for part of a revolution, usually across 0/360."""

        axis = orient_axis_upward(np.array([0.0, -1.0, -0.2]))
        origin = np.array([0.0, 60.0, 180.0])
        start = np.array([35.0, 0.0, 0.0])

        observations = [
            AxisObservation(
                commanded_angle_deg=angle,
                point_camera=origin + rotation_matrix_about_axis(axis, np.radians(angle) * sense) @ start,
            )
            for angle in (300, 315, 330, 345, 0, 15, 30, 45, 60)
        ]

        estimate = estimate_turntable_axis(observations, 4096)

        assert estimate.rotation_direction == sense
        assert estimate.angle_scale == pytest.approx(1.0, abs=1e-6)
        assert estimate.suggested_steps_per_revolution is None

    def test_orientation_keeps_an_upward_axis_and_flips_a_downward_one(self):
        up = np.array([0.0, -0.98, -0.2])

        assert float(orient_axis_upward(up) @ up) > 0
        assert float(orient_axis_upward(-up) @ up) > 0


class TestTrackingOnRenderedImages:
    @pytest.mark.parametrize("sense", [1, -1])
    def test_axis_recovered_even_when_the_detector_changes(self, scene, monkeypatch, sense):
        scene.rotation_sense = sense
        camera = _exact_camera(scene)

        observations = []
        sb = cv2.findChessboardCornersSB

        for index, angle in enumerate(range(-60, 61, 15)):
            image = scene.render(float(angle))

            # Alternate detectors, as happens on a real rig when SB fails on a
            # slightly blurred frame and the classic finder takes over.
            monkeypatch.setattr(cv2, "findChessboardCornersSB", sb if index % 2 == 0 else None)

            corners = find_checkerboard(image, SPEC)

            assert corners is not None, f"board not found at {angle} deg"

            _, translation = estimate_board_pose(corners, SPEC, camera)
            observations.append(AxisObservation(commanded_angle_deg=angle, point_camera=translation))

        estimate = estimate_turntable_axis(observations, 4096)

        truth_direction = scene.axis_direction_camera
        truth_origin = scene.axis_origin_camera

        offset = estimate.axis_origin - truth_origin
        off_axis = offset - (offset @ truth_direction) * truth_direction

        assert float(estimate.axis_direction @ truth_direction) > 0.999
        assert np.linalg.norm(off_axis) < 1.0
        assert estimate.rotation_direction == sense
        assert estimate.rms_mm < 0.5


class TestScanVolumeStorage:
    def _profile(self, **extra):
        from scanner_core.calibration.store import TurntableCalibration

        return TurntableCalibration(
            axis_origin=np.array([0.0, 10.0, 180.0]),
            axis_direction=np.array([0.0, -1.0, 0.0]),
            steps_per_revolution=4096,
            rms_mm=0.1,
            observation_count=10,
            method="target_tracking",
            **extra,
        )

    def test_round_trip(self):
        from scanner_core.calibration.store import TurntableCalibration

        profile = self._profile(scan_floor_mm=3.9, scan_radius_mm=82.2)
        loaded = TurntableCalibration.from_dict(profile.to_dict())

        assert loaded.valid
        assert loaded.scan_floor_mm == pytest.approx(3.9)
        assert loaded.scan_radius_mm == pytest.approx(82.2)

    def test_files_saved_before_it_existed_still_load(self):
        from scanner_core.calibration.store import TurntableCalibration

        data = self._profile().to_dict()
        del data["scan_floor_mm"], data["scan_radius_mm"]

        loaded = TurntableCalibration.from_dict(data)

        assert loaded.valid
        assert loaded.scan_floor_mm is None and loaded.scan_radius_mm is None

    def test_a_nonsense_radius_is_refused(self):
        assert self._profile(scan_radius_mm=-5.0).validate() != []
