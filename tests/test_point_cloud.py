"""PLY input/output and point cloud filtering."""

from __future__ import annotations

import numpy as np
import pytest

from scanner_core.config import FilterConfig
from scanner_core.errors import ReconstructionError
from scanner_core.point_cloud import (
    PointCloud,
    filter_point_cloud,
    radius_outlier_removal,
    read_ply,
    remove_non_finite,
    statistical_outlier_removal,
    voxel_downsample,
    write_ply,
)


@pytest.fixture
def cloud():
    rng = np.random.default_rng(42)

    count = 2000

    return PointCloud(
        points=rng.normal(0, 20, (count, 3)),
        colors=rng.integers(0, 256, (count, 3)).astype(np.uint8),
        confidence=rng.uniform(0, 1, count),
    )


class TestPointCloud:
    def test_rejects_mismatched_colours(self):
        with pytest.raises(ValueError):
            PointCloud(points=np.zeros((5, 3)), colors=np.zeros((3, 3), dtype=np.uint8))

    def test_rejects_mismatched_confidence(self):
        with pytest.raises(ValueError):
            PointCloud(points=np.zeros((5, 3)), confidence=np.zeros(3))

    def test_select_keeps_every_channel_aligned(self, cloud):
        mask = cloud.confidence > 0.5
        selected = cloud.select(mask)

        assert len(selected) == int(np.count_nonzero(mask))
        assert len(selected.colors) == len(selected)
        assert len(selected.confidence) == len(selected)
        assert (selected.confidence > 0.5).all()

    def test_concatenate(self):
        a = PointCloud(points=np.zeros((3, 3)), confidence=np.ones(3))
        b = PointCloud(points=np.ones((4, 3)), confidence=np.ones(4))

        combined = PointCloud.concatenate([a, b])

        assert len(combined) == 7
        assert combined.confidence is not None

    def test_summary_reports_size(self):
        points = np.array([[0, 0, 0], [10, 20, 30]], dtype=float)

        summary = PointCloud(points=points).summary()

        assert summary["point_count"] == 2
        assert summary["size_mm"] == [10.0, 20.0, 30.0]

    def test_empty_cloud_has_no_bounds(self):
        assert PointCloud(points=np.zeros((0, 3))).bounds() is None


class TestPlyRoundTrip:
    @pytest.mark.parametrize("binary", [True, False])
    def test_round_trip_preserves_everything(self, tmp_path, cloud, binary):
        path = write_ply(tmp_path / "cloud.ply", cloud, binary=binary)
        restored = read_ply(path)

        assert len(restored) == len(cloud)
        # PLY stores coordinates as float32.
        assert np.allclose(restored.points, cloud.points, atol=1e-3)
        assert (restored.colors == cloud.colors).all()
        assert np.allclose(restored.confidence, cloud.confidence, atol=1e-3)

    def test_header_declares_the_real_vertex_count(self, tmp_path, cloud):
        path = write_ply(tmp_path / "cloud.ply", cloud, binary=False)

        header = path.read_text(encoding="ascii").splitlines()

        assert header[0] == "ply"
        assert f"element vertex {len(cloud)}" in header

        body = [line for line in header if line and not line[0].isalpha()]

        assert len(body) == len(cloud)

    def test_points_only_cloud(self, tmp_path):
        cloud = PointCloud(points=np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]))

        restored = read_ply(write_ply(tmp_path / "plain.ply", cloud))

        assert restored.colors is None
        assert restored.confidence is None
        assert np.allclose(restored.points, cloud.points)

    def test_comments_survive(self, tmp_path, cloud):
        path = write_ply(tmp_path / "c.ply", cloud, comments=["session abc", "mode physical"])

        restored = read_ply(path)

        assert any("session abc" in comment for comment in restored.metadata["comments"])

    def test_refuses_to_write_an_empty_cloud(self, tmp_path):
        """An empty PLY looks like a successful scan in a file listing."""

        with pytest.raises(ReconstructionError):
            write_ply(tmp_path / "empty.ply", PointCloud(points=np.zeros((0, 3))))

    def test_missing_file(self, tmp_path):
        with pytest.raises(ReconstructionError):
            read_ply(tmp_path / "nope.ply")

    def test_rejects_a_non_ply_file(self, tmp_path):
        path = tmp_path / "bad.ply"
        path.write_text("not a ply at all", encoding="ascii")

        with pytest.raises(ReconstructionError):
            read_ply(path)

    def test_rejects_a_truncated_file(self, tmp_path, cloud):
        path = write_ply(tmp_path / "trunc.ply", cloud, binary=True)

        data = path.read_bytes()
        path.write_bytes(data[: len(data) // 2])

        with pytest.raises(ReconstructionError):
            read_ply(path)

    def test_reads_a_foreign_ascii_ply(self, tmp_path):
        """A minimal file such as another tool would produce."""

        path = tmp_path / "foreign.ply"
        path.write_text(
            "ply\n"
            "format ascii 1.0\n"
            "element vertex 3\n"
            "property float x\n"
            "property float y\n"
            "property float z\n"
            "end_header\n"
            "1 2 3\n"
            "4 5 6\n"
            "7 8 9\n",
            encoding="ascii",
        )

        cloud = read_ply(path)

        assert len(cloud) == 3
        assert np.allclose(cloud.points[2], [7, 8, 9])


class TestFilters:
    def test_remove_non_finite(self):
        points = np.array([[0, 0, 0], [np.nan, 1, 1], [1, 1, 1], [np.inf, 0, 0]])

        assert len(remove_non_finite(PointCloud(points=points))) == 2

    def test_voxel_downsample_reduces_and_averages(self):
        """Two tight clusters 10 mm apart collapse to one point each.

        The clusters are placed at voxel *centres* (2.5 = half of the 5 mm
        edge). A cluster sitting on a voxel corner would legitimately split
        across up to eight cells, which says nothing about the filter.
        """

        rng = np.random.default_rng(1)

        centre_a = np.array([2.5, 2.5, 2.5])
        centre_b = np.array([12.5, 2.5, 2.5])

        cluster_a = rng.normal(0, 0.1, (500, 3)) + centre_a
        cluster_b = rng.normal(0, 0.1, (500, 3)) + centre_b

        cloud = PointCloud(points=np.vstack([cluster_a, cluster_b]))
        result = voxel_downsample(cloud, 5.0)

        assert len(result) == 2

        # Each surviving point is the centroid of its cluster.
        ordered = result.points[np.argsort(result.points[:, 0])]

        assert np.allclose(ordered[0], centre_a, atol=0.05)
        assert np.allclose(ordered[1], centre_b, atol=0.05)

    def test_voxel_downsample_preserves_geometry(self):
        angles = np.linspace(0, 2 * np.pi, 4000, endpoint=False)
        points = np.column_stack(
            [30 * np.cos(angles), 30 * np.sin(angles), np.zeros(len(angles))]
        )

        result = voxel_downsample(PointCloud(points=points), 1.0)

        radii = np.hypot(result.points[:, 0], result.points[:, 1])

        assert len(result) < len(points)
        assert np.allclose(radii, 30.0, atol=0.6)

    def test_voxel_downsample_averages_colours_and_confidence(self):
        cloud = PointCloud(
            points=np.zeros((4, 3)),
            colors=np.array([[0, 0, 0], [100, 100, 100], [200, 200, 200], [0, 0, 0]], np.uint8),
            confidence=np.array([0.0, 1.0, 0.5, 0.5]),
        )

        result = voxel_downsample(cloud, 1.0)

        assert len(result) == 1
        assert np.isclose(result.confidence[0], 0.5)
        assert result.colors[0][0] == 75

    def test_voxel_size_zero_is_a_no_op(self, cloud):
        assert len(voxel_downsample(cloud, 0.0)) == len(cloud)

    def test_radius_filter_removes_an_isolated_point(self):
        rng = np.random.default_rng(2)

        blob = rng.normal(0, 1, (400, 3))
        outlier = np.array([[80.0, 80.0, 80.0]])

        cloud = PointCloud(points=np.vstack([blob, outlier]))

        result, removed, note = radius_outlier_removal(cloud, 3.0, 4)

        assert note == "ok"
        assert removed >= 1
        assert not np.any(np.all(np.isclose(result.points, outlier), axis=1))

    def test_radius_filter_reports_when_it_cannot_run(self):
        """It must never silently claim to have filtered."""

        cloud = PointCloud(points=np.array([[0.0, 0.0, 0.0], [1e12, 1e12, 1e12]]))

        _, removed, note = radius_outlier_removal(cloud, 0.001, 3)

        assert removed == 0
        assert note.startswith("skipped")

    def test_statistical_filter_removes_outliers_or_says_it_skipped(self):
        rng = np.random.default_rng(3)

        blob = rng.normal(0, 1, (600, 3))
        outliers = rng.normal(0, 1, (5, 3)) + 40

        cloud = PointCloud(points=np.vstack([blob, outliers]))

        result, removed, note = statistical_outlier_removal(cloud, 12, 2.0)

        if note == "ok":
            assert removed >= 5
            assert len(result) < len(cloud)
        else:
            assert note.startswith("skipped")

    def test_filter_chain_reports_each_stage(self, cloud):
        config = FilterConfig()
        config.voxel_size_mm = 2.0
        config.min_confidence = 0.4
        config.radius_outlier_enabled = True
        config.validate()

        result, statistics = filter_point_cloud(cloud, config)

        for key in (
            "points_before_filter",
            "removed_non_finite",
            "removed_low_confidence",
            "removed_by_voxel_downsample",
            "removed_by_radius_filter",
            "removed_by_statistical_filter",
            "points_after_filter",
            "filter_seconds",
        ):
            assert key in statistics

        assert statistics["points_before_filter"] == len(cloud)
        assert statistics["points_after_filter"] == len(result)

    def test_confidence_filter_actually_filters(self, cloud):
        config = FilterConfig()
        config.voxel_size_mm = 0.0
        config.min_confidence = 0.7
        config.statistical_outlier_enabled = False
        config.validate()

        result, _ = filter_point_cloud(cloud, config)

        assert result.confidence.min() >= 0.7

    def test_expensive_filters_are_skipped_on_huge_clouds(self):
        rng = np.random.default_rng(4)

        cloud = PointCloud(points=rng.normal(0, 30, (5000, 3)))

        config = FilterConfig()
        config.voxel_size_mm = 0.0
        config.statistical_outlier_enabled = True
        config.radius_outlier_enabled = True
        config.max_points_for_outlier_filters = 1000
        config.validate()

        _, statistics = filter_point_cloud(cloud, config)

        assert statistics["removed_by_statistical_filter"] == 0
        assert "max_points_for_outlier_filters" in statistics["statistical_filter_note"]
