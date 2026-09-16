"""A synthetic scanner used by simulation mode and by the test-suite.

The point of this module is *not* to fake a result.  It is to build a virtual
rig whose geometry is known exactly, render physically consistent images of a
laser stripe falling on a known solid, and then push those images through the
very same detector, triangulator and filtering chain that the physical scanner
uses.  That gives two things the old simulated helix never could:

* an end-to-end test of the real pipeline that runs on any laptop;
* ground truth - the reconstructed points can be compared against the analytic
  surface of the object, so a regression in the maths is caught immediately.

Geometry of the virtual rig, in camera coordinates (``+X`` right, ``+Y`` down,
``+Z`` forward):

* the camera sits at the origin looking down ``+Z``;
* the turntable axis is vertical, passing through
  ``(0, camera_height_mm, camera_distance_mm)`` with direction ``(0, -1, 0)``;
* the laser sheet is a vertical plane containing that axis, rotated by
  ``laser_angle_deg`` away from the degenerate edge-on configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from scanner_core.calibration.store import (
    CALIBRATION_SCHEMA_VERSION,
    SOURCE_SYNTHETIC,
    CalibrationBundle,
    CameraCalibration,
    LaserPlaneCalibration,
    TurntableCalibration,
)
from scanner_core.config import SimulationConfig
from scanner_core.geometry import (
    Plane,
    build_axis_frame,
    camera_to_turntable,
    derotate_about_z,
    pixels_to_camera_rays,
)

#: Minimum number of lit scan lines for a rendered frame to be worth returning.
MIN_RENDERED_LINES = 4


@dataclass
class RenderedFrame:
    """One synthetic capture plus the ground truth behind it."""

    image: np.ndarray
    angle_deg: float
    #: Ground-truth 3D points of the illuminated surface, in the object frame.
    truth_points: np.ndarray
    laser_pixel_count: int


class SyntheticScene:
    """A virtual camera, laser plane, turntable and solid object."""

    def __init__(self, config: SimulationConfig | None = None) -> None:
        self.config = config or SimulationConfig()
        self.config.validate()

        width = self.config.image_width
        height = self.config.image_height
        focal = self.config.focal_length_px

        self.camera_matrix = np.array(
            [
                [focal, 0.0, width / 2.0],
                [0.0, focal, height / 2.0],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )

        # A synthetic pinhole camera has no lens distortion by construction.
        self.distortion = np.zeros(5, dtype=np.float64)

        self.axis_origin = np.array(
            [0.0, self.config.camera_height_mm, self.config.camera_distance_mm],
            dtype=np.float64,
        )
        self.axis_direction = np.array([0.0, -1.0, 0.0], dtype=np.float64)

        angle = np.radians(self.config.laser_angle_deg)
        normal = np.array([np.cos(angle), 0.0, np.sin(angle)], dtype=np.float64)

        self.laser_plane = Plane.from_point_and_normal(self.axis_origin, normal)

        self._frame = build_axis_frame(self.axis_direction)
        self._rng = np.random.default_rng(self.config.seed)

        self._ray_cache: np.ndarray | None = None

    # ------------------------------------------------------------------
    # Calibration matching this rig exactly
    # ------------------------------------------------------------------
    def build_calibration_bundle(self, steps_per_revolution: int = 4096) -> CalibrationBundle:
        """Calibration profiles that describe this virtual rig exactly.

        Every profile is tagged ``source="synthetic"``.  They are never written
        to the calibration store and a physical scan refuses to use them, so a
        simulated run can never be mistaken for a calibrated one.
        """

        camera = CameraCalibration(
            schema_version=CALIBRATION_SCHEMA_VERSION,
            source=SOURCE_SYNTHETIC,
            notes="Exact intrinsics of the simulation rig; not a physical calibration.",
            camera_matrix=self.camera_matrix.copy(),
            distortion_coefficients=self.distortion.copy(),
            image_size=(self.config.image_width, self.config.image_height),
            rms_reprojection_error=0.0,
            image_count=0,
            checkerboard={},
        )
        camera.validate()

        laser = LaserPlaneCalibration(
            schema_version=CALIBRATION_SCHEMA_VERSION,
            source=SOURCE_SYNTHETIC,
            notes="Exact laser plane of the simulation rig.",
            plane_normal=self.laser_plane.normal.copy(),
            plane_offset=self.laser_plane.offset,
            point_count=0,
            pose_count=0,
            rms_mm=0.0,
            max_residual_mm=0.0,
            planarity_ratio=1.0,
            camera_reference={"source": SOURCE_SYNTHETIC},
        )
        laser.validate()

        turntable = TurntableCalibration(
            schema_version=CALIBRATION_SCHEMA_VERSION,
            source=SOURCE_SYNTHETIC,
            notes="Exact rotation axis of the simulation rig.",
            axis_origin=self.axis_origin.copy(),
            axis_direction=self.axis_direction.copy(),
            rotation_direction=1,
            steps_per_revolution=int(steps_per_revolution),
            reference_angle_deg=0.0,
            radius_mm=float(self.config.shape_radius_mm),
            rms_mm=0.0,
            observation_count=0,
            method=SOURCE_SYNTHETIC,
        )
        turntable.validate()

        return CalibrationBundle(camera=camera, laser=laser, turntable=turntable)

    # ------------------------------------------------------------------
    # Analytic object
    # ------------------------------------------------------------------
    def surface_residual(self, points_object: np.ndarray) -> np.ndarray:
        """Distance from each object-frame point to the true object surface.

        This is the ground truth a test compares the reconstruction against.
        """

        points = np.asarray(points_object, dtype=np.float64).reshape(-1, 3)

        if points.size == 0:
            return np.zeros(0, dtype=np.float64)

        radius = self.config.shape_radius_mm
        shape = self.config.shape

        if shape == "cylinder":
            return np.abs(np.hypot(points[:, 0], points[:, 1]) - radius)

        if shape == "sphere":
            centre = np.array([0.0, 0.0, radius], dtype=np.float64)
            return np.abs(np.linalg.norm(points - centre, axis=1) - radius)

        # Cube: distance to the nearest face of the axis aligned box.
        half = radius
        height = self.config.shape_height_mm

        dx = np.minimum(np.abs(points[:, 0] - half), np.abs(points[:, 0] + half))
        dy = np.minimum(np.abs(points[:, 1] - half), np.abs(points[:, 1] + half))
        dz = np.minimum(np.abs(points[:, 2] - height), np.abs(points[:, 2]))

        return np.minimum(np.minimum(dx, dy), dz)

    def _ray_object_entry(
        self, origins: np.ndarray, directions: np.ndarray
    ) -> np.ndarray:
        """Distance along each ray to the first hit on the object.

        Rays are given in the *object* frame, where the solid is axis aligned
        with ``+Z``.  ``NaN`` marks a ray that misses.
        """

        shape = self.config.shape
        radius = float(self.config.shape_radius_mm)
        height = float(self.config.shape_height_mm)

        if shape == "sphere":
            centre = np.array([0.0, 0.0, radius], dtype=np.float64)
            offset = origins - centre

            a = np.einsum("ij,ij->i", directions, directions)
            b = 2.0 * np.einsum("ij,ij->i", offset, directions)
            c = np.einsum("ij,ij->i", offset, offset) - radius**2

            discriminant = b**2 - 4 * a * c
            hit = discriminant >= 0

            t = np.full(len(origins), np.nan)
            root = np.sqrt(np.where(hit, discriminant, 0.0))

            t_near = (-b - root) / (2 * a)
            t_far = (-b + root) / (2 * a)

            chosen = np.where(t_near > 0, t_near, t_far)
            t[hit & (chosen > 0)] = chosen[hit & (chosen > 0)]

            return t

        if shape == "cylinder":
            # Infinite cylinder about Z, then clipped to 0 <= z <= height.
            a = directions[:, 0] ** 2 + directions[:, 1] ** 2
            b = 2.0 * (origins[:, 0] * directions[:, 0] + origins[:, 1] * directions[:, 1])
            c = origins[:, 0] ** 2 + origins[:, 1] ** 2 - radius**2

            parallel = a < 1e-12
            safe_a = np.where(parallel, 1.0, a)

            discriminant = b**2 - 4 * safe_a * c
            hit = (~parallel) & (discriminant >= 0)

            root = np.sqrt(np.where(hit, discriminant, 0.0))

            t_near = (-b - root) / (2 * safe_a)
            t_far = (-b + root) / (2 * safe_a)

            t = np.full(len(origins), np.nan)

            for candidate in (t_near, t_far):
                z = origins[:, 2] + candidate * directions[:, 2]
                usable = hit & (candidate > 0) & (z >= 0.0) & (z <= height)
                fill = usable & ~np.isfinite(t)
                t[fill] = candidate[fill]

            return t

        # Cube: slab method on an axis aligned box.
        lower = np.array([-radius, -radius, 0.0])
        upper = np.array([radius, radius, height])

        with np.errstate(divide="ignore", invalid="ignore"):
            inverse = 1.0 / directions

            t1 = (lower - origins) * inverse
            t2 = (upper - origins) * inverse

        t_min = np.nanmax(np.minimum(t1, t2), axis=1)
        t_max = np.nanmin(np.maximum(t1, t2), axis=1)

        hit = (t_max >= np.maximum(t_min, 0.0)) & np.isfinite(t_min)

        t = np.full(len(origins), np.nan)
        chosen = np.where(t_min > 0, t_min, t_max)
        t[hit & (chosen > 0)] = chosen[hit & (chosen > 0)]

        return t

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _all_camera_rays(self) -> np.ndarray:
        """Unit ray direction for every pixel centre, cached between frames."""

        if self._ray_cache is None:
            width = self.config.image_width
            height = self.config.image_height

            columns, rows = np.meshgrid(
                np.arange(width, dtype=np.float64),
                np.arange(height, dtype=np.float64),
            )
            pixels = np.column_stack([columns.ravel(), rows.ravel()])

            self._ray_cache = pixels_to_camera_rays(
                pixels, self.camera_matrix, None, undistort=False
            )

        return self._ray_cache

    def render(self, angle_deg: float, *, laser_on: bool = True) -> RenderedFrame:
        """Render one capture with the platform at ``angle_deg``.

        The stripe is found as the *zero crossing* of ``t_plane - t_object``
        along each image row, where ``t_plane`` is where a pixel ray meets the
        laser plane and ``t_object`` is where it first meets the solid.  Those
        two distances are equal exactly on the illuminated curve, so the
        crossing gives the stripe centre to sub-pixel accuracy.

        A fixed millimetre tolerance would not do: the depth step between
        neighbouring pixels scales with the focal length, so any fixed
        threshold either misses the stripe entirely on a wide-angle rig or
        smears it into a band on a narrow one.
        """

        width = self.config.image_width
        height = self.config.image_height

        image = self._render_background()

        empty = RenderedFrame(
            image=image,
            angle_deg=angle_deg,
            truth_points=np.zeros((0, 3), dtype=np.float64),
            laser_pixel_count=0,
        )

        if not laser_on:
            return empty

        directions = self._all_camera_rays()

        # Where does every pixel ray meet the laser plane?
        denominator = directions @ self.laser_plane.normal
        usable = np.abs(denominator) > 1e-9

        t_plane = np.full(len(directions), np.nan)
        np.divide(-self.laser_plane.offset, denominator, out=t_plane, where=usable)

        with np.errstate(invalid="ignore"):
            usable &= t_plane > 0

        # Work in object coordinates so the analytic solid stays axis aligned.
        angle_rad = float(np.radians(angle_deg))

        origin_local = camera_to_turntable(
            np.zeros((1, 3)), self.axis_origin, self.axis_direction
        )
        origin_object = derotate_about_z(origin_local, angle_rad)[0]

        directions_local = directions @ self._frame.T
        directions_object = derotate_about_z(directions_local, angle_rad)

        origins_object = np.broadcast_to(origin_object, directions_object.shape)

        t_object = self._ray_object_entry(origins_object, directions_object)

        delta = np.where(usable & np.isfinite(t_object), t_plane - t_object, np.nan)

        rows, columns, depths = self._stripe_crossings(
            delta.reshape(height, width), t_object.reshape(height, width)
        )

        if len(rows) < MIN_RENDERED_LINES:
            return empty

        # The true 3D point is the plane intersection along the interpolated ray.
        pixels = np.column_stack([columns, rows.astype(np.float64)])
        stripe_rays = pixels_to_camera_rays(pixels, self.camera_matrix, None, undistort=False)

        stripe_denominator = stripe_rays @ self.laser_plane.normal
        stripe_t = -self.laser_plane.offset / stripe_denominator

        points_camera = stripe_t[:, None] * stripe_rays
        points_local = camera_to_turntable(points_camera, self.axis_origin, self.axis_direction)
        truth_points = derotate_about_z(points_local, angle_rad)

        image = self._draw_laser(image, rows, columns)

        return RenderedFrame(
            image=image,
            angle_deg=angle_deg,
            truth_points=truth_points,
            laser_pixel_count=int(len(rows)),
        )

    @staticmethod
    def _stripe_crossings(
        delta: np.ndarray, t_object: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Sub-pixel stripe position per row, from the sign change in ``delta``.

        When a row contains more than one crossing - the laser plane can meet
        both the near and the far side of a solid - the nearest one wins,
        because that is the only surface the camera can actually see.
        """

        left = delta[:, :-1]
        right = delta[:, 1:]

        with np.errstate(invalid="ignore"):
            both_finite = np.isfinite(left) & np.isfinite(right)
            crossing = both_finite & ((left == 0) | (np.sign(left) != np.sign(right)))

        row_index, column_index = np.nonzero(crossing)

        if len(row_index) == 0:
            empty = np.zeros(0)

            return empty.astype(int), empty, empty

        a = left[row_index, column_index]
        b = right[row_index, column_index]

        denominator = a - b
        fraction = np.where(np.abs(denominator) > 1e-12, a / denominator, 0.0)
        fraction = np.clip(fraction, 0.0, 1.0)

        sub_columns = column_index + fraction
        depth = t_object[row_index, column_index]

        # Keep the nearest crossing on each row.
        order = np.lexsort((depth, row_index))

        sorted_rows = row_index[order]
        keep = np.ones(len(sorted_rows), dtype=bool)
        keep[1:] = sorted_rows[1:] != sorted_rows[:-1]

        selected = order[keep]

        return row_index[selected], sub_columns[selected], depth[selected]

    def _render_background(self) -> np.ndarray:
        """A dark, slightly textured scene, like a scanner enclosure."""

        height = self.config.image_height
        width = self.config.image_width

        image = np.full((height, width, 3), 14, dtype=np.uint8)

        if self.config.noise_level > 0:
            noise = self._rng.normal(0.0, self.config.noise_level, (height, width, 3))
            image = np.clip(image.astype(np.float64) + noise, 0, 255).astype(np.uint8)

        return image

    def _draw_laser(
        self, image: np.ndarray, rows: np.ndarray, columns: np.ndarray
    ) -> np.ndarray:
        """Draw a red stripe with a Gaussian cross-section.

        Drawing a soft profile rather than a hard binary mask is what makes the
        sub-pixel centroid meaningful: the detector has to interpolate, exactly
        as it does on a real frame.  Centring the profile on the fractional
        column also means the detector is measured against a stripe that really
        does sit between pixels.
        """

        height, width = image.shape[:2]
        sigma = max(0.4, float(self.config.laser_line_thickness_px) / 2.0)

        pixel_columns = np.arange(width, dtype=np.float64)[None, :]

        profile = np.zeros((height, width), dtype=np.float64)

        distance = pixel_columns - columns[:, None]
        profile[rows] = np.exp(-0.5 * (distance / sigma) ** 2)

        intensity = np.clip(profile * 255.0, 0, 255)

        result = image.astype(np.float64)

        # A real red laser saturates the red channel first and bleeds a little
        # into the others at the core.
        result[:, :, 2] = np.maximum(result[:, :, 2], intensity)
        result[:, :, 1] = np.maximum(result[:, :, 1], intensity * 0.18)
        result[:, :, 0] = np.maximum(result[:, :, 0], intensity * 0.12)

        return np.clip(result, 0, 255).astype(np.uint8)

    @property
    def scan_axis_is_rows(self) -> bool:
        """The synthetic laser stripe is vertical, so it is sampled per row."""

        return True

    def describe(self) -> dict[str, Any]:
        return {
            "shape": self.config.shape,
            "shape_radius_mm": self.config.shape_radius_mm,
            "shape_height_mm": self.config.shape_height_mm,
            "image_size": [self.config.image_width, self.config.image_height],
            "focal_length_px": self.config.focal_length_px,
            "laser_angle_deg": self.config.laser_angle_deg,
            "axis_origin": self.axis_origin.tolist(),
            "axis_direction": self.axis_direction.tolist(),
            "laser_plane": self.laser_plane.to_dict(),
        }


class SimulationRig:
    """Shares one :class:`SyntheticScene` between the fake camera and motor."""

    def __init__(self, config: SimulationConfig | None = None) -> None:
        self.scene = SyntheticScene(config)
        self._angle_deg = 0.0
        self._laser_on = True

    @property
    def angle_deg(self) -> float:
        return self._angle_deg

    def set_angle(self, angle_deg: float) -> None:
        self._angle_deg = float(angle_deg) % 360.0

    @property
    def laser_on(self) -> bool:
        return self._laser_on

    def set_laser(self, enabled: bool) -> None:
        self._laser_on = bool(enabled)

    def render_current(self) -> np.ndarray:
        return self.scene.render(self._angle_deg, laser_on=self._laser_on).image
