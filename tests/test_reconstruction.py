"""End-to-end reconstruction tests against analytic ground truth.

The synthetic rig renders a laser stripe on a solid whose exact surface is
known.  Those frames go through the real detector, the real triangulator and
the real filters, and the resulting points are compared with the analytic
surface.  A regression anywhere in that chain shows up here as a residual in
millimetres.
"""

from __future__ import annotations

import numpy as np
import pytest

from scanner_core.config import (
    DetectorConfig,
    FilterConfig,
    ReconstructionConfig,
    SimulationConfig,
)
from scanner_core.errors import CalibrationError, ReconstructionError
from scanner_core.laser_detection import LaserLineDetector
from scanner_core.reconstruction import CaptureInput, Reconstructor
from scanner_core.scan_plan import plan_capture_positions
from scanner_core.simulation import SyntheticScene


def build_reconstructor(scene, *, voxel=0.0, filters=False):
    detector_config = DetectorConfig()
    detector_config.min_points = 10
    detector_config.validate()

    filter_config = FilterConfig()
    filter_config.voxel_size_mm = voxel
    filter_config.statistical_outlier_enabled = filters
    filter_config.radius_outlier_enabled = filters
    filter_config.validate()

    return Reconstructor(
        scene.build_calibration_bundle(),
        LaserLineDetector(detector_config),
        ReconstructionConfig(),
        filter_config,
        allow_synthetic_calibration=True,
    )


def capture_scene(scene, count=16):
    captures = []

    for entry in plan_capture_positions(count, 4096):
        frame = scene.render(entry.angle_deg)

        captures.append(
            CaptureInput(index=entry.index, angle_deg=entry.angle_deg, image=frame.image)
        )

    return captures


class TestGroundTruthAccuracy:
    @pytest.mark.parametrize(
        ("shape", "tolerance_mm"),
        [("cylinder", 0.35), ("sphere", 0.6), ("cube", 0.7)],
    )
    def test_reconstruction_lands_on_the_real_surface(self, shape, tolerance_mm):
        config = SimulationConfig(shape=shape)
        config.validate()

        scene = SyntheticScene(config)
        reconstructor = build_reconstructor(scene)

        result = reconstructor.reconstruct(capture_scene(scene))

        assert result.point_count > 500

        residuals = scene.surface_residual(result.cloud.points)

        assert float(np.mean(residuals)) < tolerance_mm
        assert float(np.percentile(residuals, 99)) < tolerance_mm * 2

    def test_cylinder_radius_and_height_are_metric(self):
        """The recovered object must have the dimensions it was built with."""

        config = SimulationConfig(shape="cylinder", shape_radius_mm=40.0, shape_height_mm=80.0)
        config.validate()

        scene = SyntheticScene(config)
        result = build_reconstructor(scene).reconstruct(capture_scene(scene, 24))

        points = result.cloud.points
        radii = np.hypot(points[:, 0], points[:, 1])

        assert np.isclose(float(np.median(radii)), 40.0, atol=0.3)

        height = float(points[:, 2].max() - points[:, 2].min())

        # The stripe cannot reach the very top and bottom edge pixels exactly.
        assert 76.0 < height < 80.5

    def test_every_frame_contributes(self):
        config = SimulationConfig()
        config.validate()

        scene = SyntheticScene(config)
        result = build_reconstructor(scene).reconstruct(capture_scene(scene, 12))

        assert result.statistics["frames_total"] == 12
        assert result.statistics["frames_accepted"] == 12
        assert result.statistics["frames_rejected"] == 0

    def test_points_spread_around_the_full_revolution(self):
        """Captures must land at different angles, not pile up at one."""

        config = SimulationConfig()
        config.validate()

        scene = SyntheticScene(config)
        result = build_reconstructor(scene).reconstruct(capture_scene(scene, 24))

        points = result.cloud.points
        angles = np.degrees(np.arctan2(points[:, 1], points[:, 0])) % 360.0

        occupied = np.unique((angles // 30).astype(int))

        # All twelve 30 degree sectors must contain points.
        assert len(occupied) == 12

    def test_statistics_account_for_every_pixel(self):
        config = SimulationConfig()
        config.validate()

        scene = SyntheticScene(config)
        result = build_reconstructor(scene).reconstruct(capture_scene(scene, 8))

        statistics = result.statistics

        accounted = (
            statistics["accepted_points"]
            + statistics["rejected_near_parallel"]
            + statistics["rejected_behind_camera"]
            + statistics["rejected_out_of_depth"]
            + statistics["rejected_out_of_radius"]
            + statistics["rejected_out_of_height"]
        )

        assert accounted == statistics["laser_pixels"]


class TestFilteringInPipeline:
    def test_voxel_downsampling_reduces_the_cloud(self, scene):
        dense = build_reconstructor(scene).reconstruct(capture_scene(scene, 16))
        sparse = build_reconstructor(scene, voxel=2.0).reconstruct(capture_scene(scene, 16))

        assert sparse.point_count < dense.point_count
        assert sparse.point_count > 100

    def test_filters_do_not_destroy_accuracy(self, scene):
        result = build_reconstructor(scene, voxel=0.5, filters=True).reconstruct(
            capture_scene(scene, 16)
        )

        residuals = scene.surface_residual(result.cloud.points)

        assert float(np.mean(residuals)) < 0.5

    def test_statistical_filter_reports_whether_it_ran(self, scene):
        result = build_reconstructor(scene, filters=True).reconstruct(
            capture_scene(scene, 8)
        )

        note = result.statistics.get("statistical_filter_note")

        # Either it ran, or it said plainly that it did not.
        assert note in {"ok", "skipped: not enough points"} or note.startswith("skipped")


class TestFailureBehaviour:
    def test_blank_frames_raise_instead_of_inventing_a_cloud(self, scene):
        """A physical failure must never fall back to a fake point cloud."""

        reconstructor = build_reconstructor(scene)

        blank = np.zeros(
            (scene.config.image_height, scene.config.image_width, 3), dtype=np.uint8
        )

        captures = [
            CaptureInput(index=index, angle_deg=index * 45.0, image=blank)
            for index in range(8)
        ]

        with pytest.raises(ReconstructionError):
            reconstructor.reconstruct(captures)

    def test_synthetic_calibration_is_refused_for_a_physical_run(self, scene):
        with pytest.raises(CalibrationError):
            Reconstructor(
                scene.build_calibration_bundle(),
                LaserLineDetector(DetectorConfig()),
                allow_synthetic_calibration=False,
            )

    def test_wrong_resolution_is_reported_not_silently_wrong(self, scene):
        reconstructor = build_reconstructor(scene)

        mismatched = np.zeros((120, 160, 3), dtype=np.uint8)

        result = reconstructor.reconstruct_frame(mismatched, 0.0)

        assert not result.accepted
        assert "calibrated at" in result.reason

    def test_cancellation_stops_early(self, scene):
        reconstructor = build_reconstructor(scene)
        captures = capture_scene(scene, 16)

        seen = {"count": 0}

        def should_cancel():
            seen["count"] += 1

            return seen["count"] > 6

        result = reconstructor.reconstruct(captures, should_cancel=should_cancel)

        assert result.statistics["cancelled"] is True
        assert result.statistics["frames_total"] < 16


class TestAnglesDriveGeometry:
    def test_wrong_angles_destroy_the_reconstruction(self):
        """Proof that the recorded angles are actually used.

        A cube is deliberate: a cylinder is rotationally symmetric, so
        de-rotating it by the wrong angle still lands on its surface and the
        residual cannot tell the difference. With a cube, feeding every frame
        the same angle scatters the points badly - which is exactly what would
        happen if the pipeline ignored the recorded angles and assumed the
        images were evenly spaced over a revolution.
        """

        config = SimulationConfig(shape="cube")
        config.validate()

        scene = SyntheticScene(config)
        reconstructor = build_reconstructor(scene)

        correct = reconstructor.reconstruct(capture_scene(scene, 16))

        frames = capture_scene(scene, 16)

        for capture in frames:
            capture.angle_deg = 0.0

        wrong = reconstructor.reconstruct(frames)

        correct_residual = float(np.mean(scene.surface_residual(correct.cloud.points)))
        wrong_residual = float(np.mean(scene.surface_residual(wrong.cloud.points)))

        assert correct_residual < 0.7
        assert wrong_residual > correct_residual * 5

    def test_a_rotationally_symmetric_object_survives_any_angle(self):
        """The counterpart: a cylinder is symmetric, so the residual is blind.

        Recorded here so the test above is not mistaken for a general claim
        that any angle error is detectable from the surface residual alone.
        """

        config = SimulationConfig(shape="cylinder")
        config.validate()

        scene = SyntheticScene(config)
        reconstructor = build_reconstructor(scene)

        frames = capture_scene(scene, 12)

        for capture in frames:
            capture.angle_deg = 0.0

        result = reconstructor.reconstruct(frames)
        residuals = scene.surface_residual(result.cloud.points)

        assert float(np.mean(residuals)) < 0.35
