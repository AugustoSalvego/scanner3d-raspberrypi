"""Geometric primitives for laser triangulation.

Conventions used everywhere in this project:

Camera frame
    Right handed, origin at the optical centre, ``+X`` right, ``+Y`` down,
    ``+Z`` along the optical axis (OpenCV convention).  Distances in
    millimetres.

Plane
    ``n . X + d = 0`` with ``n`` a unit normal.  Stored as
    ``(normal, offset)``.

Turntable frame
    ``+Z`` along the rotation axis, origin on the axis.  A point measured while
    the platform sat at angle ``theta`` is rotated by ``-theta`` about the local
    ``Z`` to land in the object's own, angle independent frame.

Every routine is vectorised with NumPy: a 1280x720 frame yields thousands of
laser pixels per capture and the Raspberry Pi 3 cannot afford Python loops.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

EPSILON = 1e-12


@dataclass(frozen=True)
class Plane:
    """A plane ``n . X + d = 0`` with a unit normal."""

    normal: np.ndarray
    offset: float

    def __post_init__(self) -> None:
        normal = np.asarray(self.normal, dtype=np.float64).reshape(3)
        norm = float(np.linalg.norm(normal))

        if norm < EPSILON:
            raise ValueError("Plane normal must be a non-zero vector.")

        object.__setattr__(self, "normal", normal / norm)
        object.__setattr__(self, "offset", float(self.offset) / norm)

    def signed_distance(self, points: np.ndarray) -> np.ndarray:
        """Signed distance from each point to the plane, in millimetres."""

        points = np.atleast_2d(np.asarray(points, dtype=np.float64))

        return points @ self.normal + self.offset

    def to_dict(self) -> dict[str, Any]:
        return {
            "normal": [float(value) for value in self.normal],
            "offset": float(self.offset),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Plane":
        return cls(normal=np.asarray(data["normal"], dtype=np.float64), offset=float(data["offset"]))

    @classmethod
    def from_point_and_normal(cls, point: np.ndarray, normal: np.ndarray) -> "Plane":
        point = np.asarray(point, dtype=np.float64).reshape(3)
        normal = np.asarray(normal, dtype=np.float64).reshape(3)
        normal = normal / max(float(np.linalg.norm(normal)), EPSILON)

        return cls(normal=normal, offset=float(-normal @ point))


@dataclass
class PlaneFitResult:
    """Outcome of a least-squares plane fit."""

    plane: Plane
    point_count: int
    rms_mm: float
    max_abs_residual_mm: float
    #: Ratio between the second and first singular values.  Near zero means the
    #: input points are almost collinear and the plane is not determined.
    planarity_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "plane": self.plane.to_dict(),
            "point_count": self.point_count,
            "rms_mm": self.rms_mm,
            "max_abs_residual_mm": self.max_abs_residual_mm,
            "planarity_ratio": self.planarity_ratio,
        }


@dataclass
class AxisFitResult:
    """Outcome of fitting a rotation axis to a circular trajectory."""

    origin: np.ndarray
    direction: np.ndarray
    radius_mm: float
    point_count: int
    rms_mm: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin": [float(value) for value in self.origin],
            "direction": [float(value) for value in self.direction],
            "radius_mm": self.radius_mm,
            "point_count": self.point_count,
            "rms_mm": self.rms_mm,
        }


def normalize(vectors: np.ndarray) -> np.ndarray:
    """Normalise one vector or a ``(N, 3)`` stack of vectors."""

    array = np.asarray(vectors, dtype=np.float64)
    single = array.ndim == 1

    array = np.atleast_2d(array)
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    norms = np.where(norms < EPSILON, 1.0, norms)

    result = array / norms

    return result[0] if single else result


def pixels_to_camera_rays(
    pixels: np.ndarray,
    camera_matrix: np.ndarray,
    distortion: np.ndarray | None = None,
    *,
    undistort: bool = True,
) -> np.ndarray:
    """Convert pixel coordinates into unit ray directions in the camera frame.

    Args:
        pixels: ``(N, 2)`` array of ``(u, v)`` pixel coordinates.
        camera_matrix: ``3x3`` intrinsic matrix ``K``.
        distortion: OpenCV distortion coefficients, or ``None``.
        undistort: When ``True`` and coefficients are supplied, lens distortion
            is removed with ``cv2.undistortPoints`` before back-projection.

    Returns:
        ``(N, 3)`` array of unit direction vectors starting at the optical
        centre.
    """

    pixels = np.asarray(pixels, dtype=np.float64).reshape(-1, 2)

    if pixels.size == 0:
        return np.zeros((0, 3), dtype=np.float64)

    camera_matrix = np.asarray(camera_matrix, dtype=np.float64).reshape(3, 3)

    has_distortion = (
        undistort
        and distortion is not None
        and np.any(np.abs(np.asarray(distortion, dtype=np.float64)) > 1e-12)
    )

    if has_distortion:
        import cv2  # Imported lazily so pure-math tests do not need OpenCV.

        undistorted = cv2.undistortPoints(
            pixels.reshape(-1, 1, 2).astype(np.float64),
            camera_matrix,
            np.asarray(distortion, dtype=np.float64).reshape(1, -1),
        )
        normalized = undistorted.reshape(-1, 2)
    else:
        fx = camera_matrix[0, 0]
        fy = camera_matrix[1, 1]
        cx = camera_matrix[0, 2]
        cy = camera_matrix[1, 2]

        normalized = np.empty_like(pixels)
        normalized[:, 0] = (pixels[:, 0] - cx) / fx
        normalized[:, 1] = (pixels[:, 1] - cy) / fy

    directions = np.column_stack(
        [normalized[:, 0], normalized[:, 1], np.ones(len(normalized), dtype=np.float64)]
    )

    return normalize(directions)


@dataclass
class RayPlaneIntersection:
    """Intersection of a ray bundle with a plane, with rejection accounting."""

    points: np.ndarray
    valid: np.ndarray
    near_parallel: np.ndarray
    behind_camera: np.ndarray
    out_of_depth: np.ndarray

    def counts(self) -> dict[str, int]:
        return {
            "accepted": int(np.count_nonzero(self.valid)),
            "rejected_near_parallel": int(np.count_nonzero(self.near_parallel)),
            "rejected_behind_camera": int(np.count_nonzero(self.behind_camera)),
            "rejected_out_of_depth": int(np.count_nonzero(self.out_of_depth)),
        }


def intersect_rays_with_plane_detailed(
    directions: np.ndarray,
    plane: Plane,
    *,
    origin: np.ndarray | None = None,
    min_incidence: float = 0.02,
    min_depth_mm: float = 0.0,
    max_depth_mm: float = float("inf"),
) -> RayPlaneIntersection:
    """Intersect camera rays with the laser plane, reporting why rays failed.

    A ray ``X(t) = o + t * d`` meets ``n . X + p = 0`` at
    ``t = -(n . o + p) / (n . d)``.

    Rays are rejected when they are nearly parallel to the plane (the
    intersection would be numerically meaningless), when they meet it behind the
    camera, or when the resulting depth falls outside the configured window.
    The three rejection masks are mutually exclusive, so their counts add up.
    """

    directions = np.asarray(directions, dtype=np.float64).reshape(-1, 3)

    if directions.size == 0:
        empty_points = np.zeros((0, 3), dtype=np.float64)
        empty_mask = np.zeros(0, dtype=bool)

        return RayPlaneIntersection(
            points=empty_points,
            valid=empty_mask,
            near_parallel=empty_mask.copy(),
            behind_camera=empty_mask.copy(),
            out_of_depth=empty_mask.copy(),
        )

    if origin is None:
        origin_vector = np.zeros(3, dtype=np.float64)
    else:
        origin_vector = np.asarray(origin, dtype=np.float64).reshape(3)

    denominator = directions @ plane.normal

    near_parallel = np.abs(denominator) < float(min_incidence)
    usable = ~near_parallel

    numerator = -(float(origin_vector @ plane.normal) + plane.offset)

    t = np.full(len(directions), np.nan, dtype=np.float64)
    np.divide(numerator, denominator, out=t, where=usable)

    usable &= np.isfinite(t)

    with np.errstate(invalid="ignore"):
        behind_camera = usable & (t <= 0.0)

    usable &= ~behind_camera

    points = np.full((len(directions), 3), np.nan, dtype=np.float64)
    points[usable] = origin_vector + t[usable, None] * directions[usable]

    depth = np.full(len(directions), np.nan, dtype=np.float64)
    depth[usable] = points[usable, 2]

    with np.errstate(invalid="ignore"):
        out_of_depth = usable & (
            (depth < float(min_depth_mm)) | (depth > float(max_depth_mm))
        )

    valid = usable & ~out_of_depth

    points[~valid] = np.nan

    return RayPlaneIntersection(
        points=points,
        valid=valid,
        near_parallel=near_parallel,
        behind_camera=behind_camera,
        out_of_depth=out_of_depth,
    )


def intersect_rays_with_plane(
    directions: np.ndarray,
    plane: Plane,
    *,
    origin: np.ndarray | None = None,
    min_incidence: float = 0.02,
    min_depth_mm: float = 0.0,
    max_depth_mm: float = float("inf"),
) -> tuple[np.ndarray, np.ndarray]:
    """Convenience wrapper returning only ``(points, valid)``."""

    result = intersect_rays_with_plane_detailed(
        directions,
        plane,
        origin=origin,
        min_incidence=min_incidence,
        min_depth_mm=min_depth_mm,
        max_depth_mm=max_depth_mm,
    )

    return result.points, result.valid


def fit_plane(points: np.ndarray) -> PlaneFitResult:
    """Least-squares plane fit through a 3D point set (total least squares).

    Uses the SVD of the mean-centred points: the normal is the singular vector
    of the smallest singular value.  The ratio ``s1 / s0`` is reported so the
    caller can reject a degenerate, almost collinear input instead of trusting
    an arbitrary normal.
    """

    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    finite = np.all(np.isfinite(points), axis=1)
    points = points[finite]

    if len(points) < 3:
        raise ValueError("At least 3 finite points are required to fit a plane.")

    centroid = points.mean(axis=0)
    centered = points - centroid

    _, singular_values, right_vectors = np.linalg.svd(centered, full_matrices=False)

    normal = right_vectors[-1]
    plane = Plane.from_point_and_normal(centroid, normal)

    residuals = plane.signed_distance(points)

    planarity_ratio = 0.0

    if len(singular_values) >= 2 and singular_values[0] > EPSILON:
        planarity_ratio = float(singular_values[1] / singular_values[0])

    return PlaneFitResult(
        plane=plane,
        point_count=int(len(points)),
        rms_mm=float(np.sqrt(np.mean(residuals**2))),
        max_abs_residual_mm=float(np.max(np.abs(residuals))),
        planarity_ratio=planarity_ratio,
    )


def plane_from_pose(rotation_matrix: np.ndarray, translation: np.ndarray) -> Plane:
    """Plane of a calibration board, expressed in the camera frame.

    ``solvePnP`` returns the transform mapping board coordinates to camera
    coordinates (``X_cam = R @ X_board + t``).  The board occupies ``Z = 0`` in
    its own frame, so its normal in the camera frame is the third column of
    ``R`` and it passes through ``t``.
    """

    rotation_matrix = np.asarray(rotation_matrix, dtype=np.float64).reshape(3, 3)
    translation = np.asarray(translation, dtype=np.float64).reshape(3)

    return Plane.from_point_and_normal(translation, rotation_matrix[:, 2])


def rotation_matrix_about_axis(axis: np.ndarray, angle_rad: float) -> np.ndarray:
    """Rodrigues rotation matrix for ``angle_rad`` about a unit ``axis``."""

    axis = normalize(np.asarray(axis, dtype=np.float64).reshape(3))

    cos_a = float(np.cos(angle_rad))
    sin_a = float(np.sin(angle_rad))

    cross = np.array(
        [
            [0.0, -axis[2], axis[1]],
            [axis[2], 0.0, -axis[0]],
            [-axis[1], axis[0], 0.0],
        ],
        dtype=np.float64,
    )

    return cos_a * np.eye(3) + sin_a * cross + (1.0 - cos_a) * np.outer(axis, axis)


def build_axis_frame(axis_direction: np.ndarray) -> np.ndarray:
    """Return a ``3x3`` rotation whose rows are an orthonormal basis ``[u, v, w]``.

    ``w`` is the supplied axis; ``u`` and ``v`` complete a right-handed frame.
    Multiplying a camera-frame offset by this matrix expresses it in turntable
    coordinates.
    """

    w = normalize(np.asarray(axis_direction, dtype=np.float64).reshape(3))

    # Pick the world axis least aligned with w so the cross product is stable.
    helper = np.array([1.0, 0.0, 0.0]) if abs(w[0]) < 0.9 else np.array([0.0, 1.0, 0.0])

    u = normalize(np.cross(helper, w))
    v = np.cross(w, u)

    return np.vstack([u, v, w])


def camera_to_turntable(
    points_camera: np.ndarray,
    axis_origin: np.ndarray,
    axis_direction: np.ndarray,
) -> np.ndarray:
    """Express camera-frame points in turntable coordinates (``+Z`` = axis)."""

    points_camera = np.asarray(points_camera, dtype=np.float64).reshape(-1, 3)

    if points_camera.size == 0:
        return np.zeros((0, 3), dtype=np.float64)

    origin = np.asarray(axis_origin, dtype=np.float64).reshape(3)
    frame = build_axis_frame(axis_direction)

    return (points_camera - origin) @ frame.T


def turntable_to_camera(
    points_local: np.ndarray,
    axis_origin: np.ndarray,
    axis_direction: np.ndarray,
) -> np.ndarray:
    """Inverse of :func:`camera_to_turntable`."""

    points_local = np.asarray(points_local, dtype=np.float64).reshape(-1, 3)

    if points_local.size == 0:
        return np.zeros((0, 3), dtype=np.float64)

    origin = np.asarray(axis_origin, dtype=np.float64).reshape(3)
    frame = build_axis_frame(axis_direction)

    return points_local @ frame + origin


def derotate_about_z(points_local: np.ndarray, angle_rad: float) -> np.ndarray:
    """Rotate turntable-frame points by ``-angle_rad`` about the local ``Z``.

    The platform carried the object to ``angle_rad`` before the capture, so
    undoing that rotation puts every capture into one common object frame.
    """

    points_local = np.asarray(points_local, dtype=np.float64).reshape(-1, 3)

    if points_local.size == 0:
        return np.zeros((0, 3), dtype=np.float64)

    cos_a = float(np.cos(-angle_rad))
    sin_a = float(np.sin(-angle_rad))

    result = np.empty_like(points_local)
    result[:, 0] = cos_a * points_local[:, 0] - sin_a * points_local[:, 1]
    result[:, 1] = sin_a * points_local[:, 0] + cos_a * points_local[:, 1]
    result[:, 2] = points_local[:, 2]

    return result


def transform_to_object_frame(
    points_camera: np.ndarray,
    angle_deg: float,
    axis_origin: np.ndarray,
    axis_direction: np.ndarray,
    *,
    rotation_direction: int = 1,
) -> np.ndarray:
    """Full camera -> object transform for one capture.

    Args:
        points_camera: ``(N, 3)`` triangulated points in the camera frame.
        angle_deg: Platform angle for this capture, in degrees.
        axis_origin: A point on the rotation axis, camera frame, millimetres.
        axis_direction: Unit rotation axis, camera frame.
        rotation_direction: ``+1`` or ``-1``, telling which way a positive motor
            angle turns the platform around ``axis_direction``.
    """

    local = camera_to_turntable(points_camera, axis_origin, axis_direction)
    angle_rad = float(np.radians(angle_deg)) * (1 if rotation_direction >= 0 else -1)

    return derotate_about_z(local, angle_rad)


def fit_circle_2d(points_2d: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Algebraic least-squares circle fit.

    Solves ``x^2 + y^2 + D*x + E*y + F = 0`` linearly, which gives the centre
    ``(-D/2, -E/2)`` and radius ``sqrt(centre^2 - F)``.

    Returns:
        ``(centre, radius, rms)``.
    """

    points_2d = np.asarray(points_2d, dtype=np.float64).reshape(-1, 2)

    if len(points_2d) < 3:
        raise ValueError("At least 3 points are required to fit a circle.")

    x = points_2d[:, 0]
    y = points_2d[:, 1]

    design = np.column_stack([x, y, np.ones(len(points_2d))])
    target = -(x**2 + y**2)

    solution, *_ = np.linalg.lstsq(design, target, rcond=None)

    centre = np.array([-solution[0] / 2.0, -solution[1] / 2.0])
    squared = centre[0] ** 2 + centre[1] ** 2 - solution[2]

    if squared <= 0:
        raise ValueError("Circle fit is degenerate: the points are collinear.")

    radius = float(np.sqrt(squared))
    distances = np.linalg.norm(points_2d - centre, axis=1)
    rms = float(np.sqrt(np.mean((distances - radius) ** 2)))

    return centre, radius, rms


def fit_rotation_axis(points: np.ndarray) -> AxisFitResult:
    """Recover a rotation axis from one physical point seen at several angles.

    The observations lie on a circle centred on the axis: fitting their plane
    gives the axis direction, and fitting the circle inside that plane gives a
    point on the axis.  This is what the turntable calibration tool uses when
    it tracks a calibration target across a full platform revolution.
    """

    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    finite = np.all(np.isfinite(points), axis=1)
    points = points[finite]

    if len(points) < 3:
        raise ValueError("At least 3 observations are required to fit a rotation axis.")

    plane_fit = fit_plane(points)
    direction = plane_fit.plane.normal

    frame = build_axis_frame(direction)
    centroid = points.mean(axis=0)
    local = (points - centroid) @ frame.T

    centre_2d, radius, circle_rms = fit_circle_2d(local[:, :2])

    centre_local = np.array([centre_2d[0], centre_2d[1], 0.0])
    origin = centroid + centre_local @ frame

    rms = float(np.sqrt(circle_rms**2 + plane_fit.rms_mm**2))

    return AxisFitResult(
        origin=origin,
        direction=direction,
        radius_mm=radius,
        point_count=int(len(points)),
        rms_mm=rms,
    )


def project_points(points_camera: np.ndarray, camera_matrix: np.ndarray) -> np.ndarray:
    """Pinhole projection of camera-frame points into pixels (no distortion)."""

    points_camera = np.asarray(points_camera, dtype=np.float64).reshape(-1, 3)
    camera_matrix = np.asarray(camera_matrix, dtype=np.float64).reshape(3, 3)

    if points_camera.size == 0:
        return np.zeros((0, 2), dtype=np.float64)

    z = points_camera[:, 2]
    safe_z = np.where(np.abs(z) < EPSILON, np.nan, z)

    u = camera_matrix[0, 0] * points_camera[:, 0] / safe_z + camera_matrix[0, 2]
    v = camera_matrix[1, 1] * points_camera[:, 1] / safe_z + camera_matrix[1, 2]

    return np.column_stack([u, v])
