"""Ground-truth tests for the triangulation maths.

These build an ideal camera and an ideal laser plane, project known 3D points
into pixels, and check that back-projecting those pixels recovers the original
points.  If any of these fail, every scan the machine produces is wrong.
"""

from __future__ import annotations

import numpy as np
import pytest

from scanner_core.geometry import (
    Plane,
    build_axis_frame,
    camera_to_turntable,
    derotate_about_z,
    fit_circle_2d,
    fit_plane,
    fit_rotation_axis,
    intersect_rays_with_plane,
    intersect_rays_with_plane_detailed,
    normalize,
    pixels_to_camera_rays,
    plane_from_pose,
    project_points,
    rotation_matrix_about_axis,
    transform_to_object_frame,
    turntable_to_camera,
)

CAMERA_MATRIX = np.array(
    [[800.0, 0.0, 320.0], [0.0, 800.0, 240.0], [0.0, 0.0, 1.0]], dtype=np.float64
)


class TestPlane:
    def test_normal_is_normalised(self):
        plane = Plane(normal=np.array([0.0, 0.0, 5.0]), offset=-500.0)

        assert np.isclose(np.linalg.norm(plane.normal), 1.0)
        # Scaling the normal must scale the offset the same way.
        assert np.isclose(plane.offset, -100.0)

    def test_zero_normal_is_rejected(self):
        with pytest.raises(ValueError):
            Plane(normal=np.zeros(3), offset=1.0)

    def test_signed_distance(self):
        plane = Plane(normal=np.array([0.0, 0.0, 1.0]), offset=-100.0)

        distances = plane.signed_distance(np.array([[0, 0, 100], [0, 0, 130], [0, 0, 60]]))

        assert np.allclose(distances, [0.0, 30.0, -40.0])

    def test_round_trip_through_dict(self):
        plane = Plane.from_point_and_normal(np.array([1.0, 2.0, 3.0]), np.array([1.0, 1.0, 1.0]))
        restored = Plane.from_dict(plane.to_dict())

        assert np.allclose(restored.normal, plane.normal)
        assert np.isclose(restored.offset, plane.offset)


class TestRaysAndProjection:
    def test_pixel_to_ray_round_trip(self):
        """A pixel to a ray and back must land on the same pixel."""

        pixels = np.array([[320.0, 240.0], [100.5, 400.25], [600.0, 12.0]])

        rays = pixels_to_camera_rays(pixels, CAMERA_MATRIX, None)

        assert np.allclose(np.linalg.norm(rays, axis=1), 1.0)

        # Push each ray out to an arbitrary depth and project it back.
        points = rays * 250.0
        reprojected = project_points(points, CAMERA_MATRIX)

        assert np.allclose(reprojected, pixels, atol=1e-9)

    def test_principal_point_maps_to_optical_axis(self):
        ray = pixels_to_camera_rays(np.array([[320.0, 240.0]]), CAMERA_MATRIX, None)[0]

        assert np.allclose(ray, [0.0, 0.0, 1.0])

    def test_empty_input(self):
        rays = pixels_to_camera_rays(np.zeros((0, 2)), CAMERA_MATRIX, None)

        assert rays.shape == (0, 3)


class TestRayPlaneIntersection:
    def test_recovers_known_points(self):
        """Project points that lie on a plane, then triangulate them back."""

        plane = Plane.from_point_and_normal(
            np.array([0.0, 0.0, 300.0]), np.array([0.7, 0.0, 0.7])
        )

        rng = np.random.default_rng(7)

        # Build points that lie exactly on the plane.
        basis_u = normalize(np.cross(plane.normal, [0.0, 1.0, 0.0]))
        basis_v = np.cross(plane.normal, basis_u)
        centre = -plane.offset * plane.normal

        truth = (
            centre
            + rng.uniform(-60, 60, (200, 1)) * basis_u
            + rng.uniform(-60, 60, (200, 1)) * basis_v
        )
        truth = truth[truth[:, 2] > 50]

        pixels = project_points(truth, CAMERA_MATRIX)
        rays = pixels_to_camera_rays(pixels, CAMERA_MATRIX, None)

        points, valid = intersect_rays_with_plane(
            rays, plane, min_depth_mm=10, max_depth_mm=2000
        )

        assert valid.all()
        assert np.allclose(points, truth, atol=1e-6)

    def test_rejects_rays_parallel_to_the_plane(self):
        plane = Plane(normal=np.array([0.0, 0.0, 1.0]), offset=-300.0)

        # A ray travelling along +X never meets a plane whose normal is +Z.
        parallel = np.array([[1.0, 0.0, 0.0]])

        result = intersect_rays_with_plane_detailed(parallel, plane)

        assert not result.valid[0]
        assert result.near_parallel[0]
        assert np.isnan(result.points[0]).all()

    def test_rejects_intersections_behind_the_camera(self):
        # Plane sits behind the camera: n.X + d = 0 with d positive.
        plane = Plane(normal=np.array([0.0, 0.0, 1.0]), offset=300.0)

        result = intersect_rays_with_plane_detailed(np.array([[0.0, 0.0, 1.0]]), plane)

        assert not result.valid[0]
        assert result.behind_camera[0]

    def test_rejects_out_of_depth(self):
        plane = Plane(normal=np.array([0.0, 0.0, 1.0]), offset=-300.0)

        result = intersect_rays_with_plane_detailed(
            np.array([[0.0, 0.0, 1.0]]), plane, min_depth_mm=10, max_depth_mm=100
        )

        assert not result.valid[0]
        assert result.out_of_depth[0]

    def test_rejection_masks_are_mutually_exclusive(self):
        plane = Plane.from_point_and_normal(
            np.array([0.0, 0.0, 300.0]), np.array([0.3, 0.1, 0.9])
        )

        rng = np.random.default_rng(3)
        rays = normalize(rng.normal(0, 1, (500, 3)))

        result = intersect_rays_with_plane_detailed(
            rays, plane, min_depth_mm=50, max_depth_mm=600
        )

        counts = result.counts()

        assert sum(counts.values()) == len(rays)


class TestPlaneFitting:
    def test_recovers_a_known_plane(self):
        plane = Plane.from_point_and_normal(
            np.array([10.0, -5.0, 280.0]), np.array([0.5, 0.2, 0.84])
        )

        rng = np.random.default_rng(11)

        basis_u = normalize(np.cross(plane.normal, [1.0, 0.0, 0.0]))
        basis_v = np.cross(plane.normal, basis_u)
        centre = -plane.offset * plane.normal

        points = (
            centre
            + rng.uniform(-70, 70, (400, 1)) * basis_u
            + rng.uniform(-70, 70, (400, 1)) * basis_v
        )

        result = fit_plane(points)

        assert result.rms_mm < 1e-9
        assert result.planarity_ratio > 0.3
        assert abs(float(result.plane.normal @ plane.normal)) > 1 - 1e-9

    def test_detects_a_degenerate_collinear_fit(self):
        """Collinear points do not determine a plane; the ratio must reveal it."""

        direction = normalize(np.array([1.0, 2.0, 3.0]))
        points = np.array([0.0, 0.0, 300.0]) + np.linspace(-50, 50, 60)[:, None] * direction

        result = fit_plane(points)

        assert result.planarity_ratio < 1e-6

    def test_needs_three_points(self):
        with pytest.raises(ValueError):
            fit_plane(np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 1.0]]))

    def test_ignores_non_finite_rows(self):
        points = np.array(
            [[0, 0, 100], [10, 0, 100], [0, 10, 100], [np.nan, 0, 0]], dtype=float
        )

        result = fit_plane(points)

        assert result.point_count == 3

    def test_plane_from_pose(self):
        """A board plane must pass through its own origin."""

        rotation = rotation_matrix_about_axis(np.array([0.0, 1.0, 0.0]), np.radians(25))
        translation = np.array([12.0, -4.0, 310.0])

        plane = plane_from_pose(rotation, translation)

        assert abs(float(plane.signed_distance(translation[None, :])[0])) < 1e-9

        # A point on the board (Z_board = 0) must also lie on the plane.
        board_point = rotation @ np.array([40.0, 25.0, 0.0]) + translation

        assert abs(float(plane.signed_distance(board_point[None, :])[0])) < 1e-9


class TestTurntable:
    def test_axis_frame_is_orthonormal_and_right_handed(self):
        frame = build_axis_frame(np.array([0.2, -1.0, 0.1]))

        assert np.allclose(frame @ frame.T, np.eye(3), atol=1e-12)
        assert np.isclose(np.linalg.det(frame), 1.0)
        # The third row is the supplied axis.
        assert np.allclose(frame[2], normalize(np.array([0.2, -1.0, 0.1])))

    def test_camera_turntable_round_trip(self):
        origin = np.array([3.0, 55.0, 310.0])
        direction = normalize(np.array([0.02, -1.0, 0.03]))

        rng = np.random.default_rng(5)
        points = rng.uniform(-80, 80, (150, 3)) + np.array([0, 40, 300])

        local = camera_to_turntable(points, origin, direction)
        restored = turntable_to_camera(local, origin, direction)

        assert np.allclose(restored, points, atol=1e-9)

    def test_axis_maps_to_local_z(self):
        origin = np.array([0.0, 60.0, 320.0])
        direction = np.array([0.0, -1.0, 0.0])

        on_axis = origin + 45.0 * direction
        local = camera_to_turntable(on_axis[None, :], origin, direction)[0]

        assert np.allclose(local[:2], 0.0, atol=1e-12)
        assert np.isclose(local[2], 45.0)

    def test_derotation_undoes_the_platform_rotation(self):
        """A point measured at angle theta must land back where it started."""

        origin = np.array([0.0, 60.0, 320.0])
        direction = np.array([0.0, -1.0, 0.0])

        object_point = np.array([0.0, 20.0, 280.0])
        object_local = camera_to_turntable(object_point[None, :], origin, direction)

        for angle in (0.0, 37.5, 90.0, 180.0, 275.0, 359.0):
            # Where the platform carries the point at this angle.
            rotated_local = derotate_about_z(object_local, -np.radians(angle))
            rotated_camera = turntable_to_camera(rotated_local, origin, direction)

            recovered = transform_to_object_frame(
                rotated_camera, angle, origin, direction, rotation_direction=1
            )

            assert np.allclose(recovered, object_local, atol=1e-9)

    def test_rotation_direction_flips_the_sign(self):
        origin = np.array([0.0, 60.0, 320.0])
        direction = np.array([0.0, -1.0, 0.0])
        point = np.array([[10.0, 30.0, 300.0]])

        forward = transform_to_object_frame(point, 40.0, origin, direction, rotation_direction=1)
        backward = transform_to_object_frame(
            point, 40.0, origin, direction, rotation_direction=-1
        )

        assert not np.allclose(forward, backward)

        # Reversing the direction is the same as negating the angle.
        negated = transform_to_object_frame(
            point, -40.0, origin, direction, rotation_direction=1
        )

        assert np.allclose(backward, negated, atol=1e-12)

    def test_rodrigues_matches_a_known_rotation(self):
        matrix = rotation_matrix_about_axis(np.array([0.0, 0.0, 1.0]), np.radians(90))

        assert np.allclose(matrix @ np.array([1.0, 0.0, 0.0]), [0.0, 1.0, 0.0], atol=1e-12)
        assert np.allclose(matrix @ matrix.T, np.eye(3), atol=1e-12)


class TestAxisFitting:
    def test_circle_fit(self):
        angles = np.linspace(0, 2 * np.pi, 24, endpoint=False)
        points = np.column_stack(
            [7.0 + 33.0 * np.cos(angles), -2.0 + 33.0 * np.sin(angles)]
        )

        centre, radius, rms = fit_circle_2d(points)

        assert np.allclose(centre, [7.0, -2.0], atol=1e-9)
        assert np.isclose(radius, 33.0)
        assert rms < 1e-9

    def test_collinear_circle_fit_is_rejected(self):
        points = np.column_stack([np.linspace(0, 100, 20), np.zeros(20)])

        with pytest.raises(ValueError):
            fit_circle_2d(points)

    def test_recovers_a_known_rotation_axis(self):
        """This is exactly what the turntable calibration does."""

        origin = np.array([4.0, 58.0, 315.0])
        direction = normalize(np.array([0.03, -1.0, 0.02]))

        start = origin + 42.0 * normalize(np.cross(direction, [0.0, 0.0, 1.0]))

        observations = []

        for angle in range(0, 360, 24):
            local = camera_to_turntable(start[None, :], origin, direction)
            rotated = derotate_about_z(local, -np.radians(angle))
            observations.append(turntable_to_camera(rotated, origin, direction)[0])

        result = fit_rotation_axis(np.array(observations))

        assert result.rms_mm < 1e-6
        assert np.isclose(result.radius_mm, 42.0, atol=1e-6)
        assert abs(float(result.direction @ direction)) > 1 - 1e-9

        # The recovered origin must lie on the true axis, though not
        # necessarily at the same point along it.
        offset = result.origin - origin
        perpendicular = offset - direction * float(offset @ direction)

        assert np.linalg.norm(perpendicular) < 1e-6

    def test_needs_enough_observations(self):
        with pytest.raises(ValueError):
            fit_rotation_axis(np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 1.0]]))
