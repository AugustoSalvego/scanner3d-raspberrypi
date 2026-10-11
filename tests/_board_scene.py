"""A synthetic checkerboard standing on a turntable, for calibration tests.

This is the calibration counterpart of :mod:`scanner_core.simulation`: the
geometry is known exactly, so a calibration recovered from the rendered images
can be compared against the truth.

Layout, loosely modelled on the reference FabScan-style box: the camera looks
at the turntable from about 18 cm away, slightly from above; the board stands
on the platform 2-3 cm in front of the axis, optionally leaning back or forward
("picture frame" poses); the laser sheet contains the rotation axis.

World frame: turntable axis through the origin, ``+Z`` up.  The camera sits on
the ``-X`` side looking towards ``+X``.

The board follows the project's checkerboard convention: inner corner ``i`` is
at ``(square * (i % columns), square * (i // columns), 0)``; seen from the front
the board ``+X`` goes right and ``+Y`` goes down, so corner 0 is top-left.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from scanner_core.geometry import rotation_matrix_about_axis


@dataclass
class BoardTurntableScene:
    width: int = 640
    height: int = 480
    focal_px: float = 520.0
    distortion: tuple[float, ...] = (-0.12, 0.05, 0.0, 0.0, 0.0)
    camera_distance_mm: float = 180.0
    camera_height_mm: float = 60.0
    pitch_deg: float = 10.0
    axis_tilt_deg: float = 0.0
    laser_angle_deg: float = 32.0
    #: Shift of the laser sheet along its normal, in mm.  Changing it between
    #: placements imitates a laser holder that moves when the board is handled.
    laser_offset_mm: float = 0.0
    columns: int = 9
    rows: int = 6
    square_mm: float = 10.0
    margin_mm: float = 10.0
    board_offset_mm: float = 25.0
    #: +1: a positive commanded angle turns the platform anticlockwise seen
    #: from above.  -1: the motor is wired the other way round.
    rotation_sense: int = 1
    noise: float = 2.0
    seed: int = 7
    texture_px_per_mm: float = 8.0
    _rng: np.random.Generator = field(init=False, repr=False)
    _remap: tuple[np.ndarray, np.ndarray] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = np.random.default_rng(self.seed)

        self.camera_matrix = np.array(
            [
                [self.focal_px, 0.0, self.width / 2.0 + 6.0],
                [0.0, self.focal_px, self.height / 2.0 - 4.0],
                [0.0, 0.0, 1.0],
            ]
        )
        self.dist = np.array(self.distortion, dtype=np.float64)

        p = np.radians(self.pitch_deg)
        forward = np.array([np.cos(p), 0.0, -np.sin(p)])
        down = np.array([-np.sin(p), 0.0, -np.cos(p)])
        right = np.cross(down, forward)

        # World -> camera rotation and camera centre.
        self.R_wc = np.vstack([right, down, forward])
        self.C_w = np.array([-self.camera_distance_mm, 0.0, self.camera_height_mm])

        tilt = rotation_matrix_about_axis(np.array([1.0, 0.0, 0.0]), np.radians(self.axis_tilt_deg))
        self.axis_w = tilt @ np.array([0.0, 0.0, 1.0])

        a = np.radians(self.laser_angle_deg)
        self.laser_normal_w = np.array([-np.sin(a), np.cos(a), 0.0])
        self.laser_source_w = -170.0 * np.array([np.cos(a), np.sin(a), 0.0])

    # ------------------------------------------------------------------
    # Ground truth in camera coordinates
    # ------------------------------------------------------------------
    @property
    def axis_origin_camera(self) -> np.ndarray:
        return self.R_wc @ (np.zeros(3) - self.C_w)

    @property
    def axis_direction_camera(self) -> np.ndarray:
        """The rotation axis, pointing up."""

        return self.R_wc @ self.axis_w

    @property
    def laser_plane_camera(self) -> tuple[np.ndarray, float]:
        normal = self.R_wc @ self.laser_normal_w
        point = self.R_wc @ (self.laser_offset_mm * self.laser_normal_w - self.C_w)

        return normal, float(-normal @ point)

    def object_points(self) -> np.ndarray:
        grid = np.zeros((self.rows * self.columns, 3))
        grid[:, :2] = np.mgrid[0 : self.columns, 0 : self.rows].T.reshape(-1, 2)

        return grid * self.square_mm

    def board_pose(self, angle_deg: float, tilt_deg: float) -> tuple[np.ndarray, np.ndarray]:
        """``(R, t)`` with ``X_camera = R @ X_board + t``.

        ``tilt_deg`` > 0 leans the board back (top away from the camera) like a
        picture frame; < 0 leans it forward.
        """

        tau = np.radians(tilt_deg)
        paper_x = np.array([0.0, -1.0, 0.0])
        paper_y = np.array([-np.sin(tau), 0.0, -np.cos(tau)])
        paper_z = np.cross(paper_x, paper_y)

        # The bottom edge of the paper rests on the platform.
        pattern_bottom = (self.rows) * self.square_mm
        paper_bottom = pattern_bottom + self.margin_mm
        centre_x = (self.columns - 1) * self.square_mm / 2.0

        bottom_centre = np.array([-self.board_offset_mm, 0.0, 0.0])
        origin = bottom_centre - paper_bottom * paper_y - centre_x * paper_x

        turn = rotation_matrix_about_axis(self.axis_w, np.radians(angle_deg) * self.rotation_sense)

        R_board_world = turn @ np.column_stack([paper_x, paper_y, paper_z])
        origin_w = turn @ origin

        return self.R_wc @ R_board_world, self.R_wc @ (origin_w - self.C_w)

    def board_visible(self, angle_deg: float, tilt_deg: float) -> bool:
        R, t = self.board_pose(angle_deg, tilt_deg)

        # The camera is on the printed side when it lies against +Z_board.
        return float(t @ R[:, 2]) > 0.0

    def true_corners(self, angle_deg: float, tilt_deg: float) -> np.ndarray:
        R, t = self.board_pose(angle_deg, tilt_deg)
        rvec, _ = cv2.Rodrigues(R)
        projected, _ = cv2.projectPoints(self.object_points(), rvec, t, self.camera_matrix, self.dist)

        return projected.reshape(-1, 2)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _texture(self, R: np.ndarray, t: np.ndarray, laser: bool) -> np.ndarray:
        s = self.texture_px_per_mm
        lo = -self.square_mm - self.margin_mm
        width_mm = (self.columns + 1) * self.square_mm + 2 * self.margin_mm
        height_mm = (self.rows + 1) * self.square_mm + 2 * self.margin_mm

        w, h = int(round(width_mm * s)), int(round(height_mm * s))
        tex = np.full((h, w, 3), 228.0)

        u = (np.arange(w) + 0.5) / s + lo
        v = (np.arange(h) + 0.5) / s + lo
        uu, vv = np.meshgrid(u, v)

        col = np.floor(uu / self.square_mm).astype(int) + 1
        row = np.floor(vv / self.square_mm).astype(int) + 1
        inside = (
            (uu >= -self.square_mm)
            & (uu < self.columns * self.square_mm)
            & (vv >= -self.square_mm)
            & (vv < self.rows * self.square_mm)
        )
        black = inside & ((row + col) % 2 == 0)
        tex[black] = 24.0

        if laser:
            normal, offset = self.laser_plane_camera
            a = float(normal @ R[:, 0])
            b = float(normal @ R[:, 1])
            c = float(normal @ t + offset)
            norm = np.hypot(a, b)

            if norm > 1e-9:
                distance = np.abs(a * uu + b * vv + c) / norm
                profile = np.exp(-0.5 * (distance / 0.45) ** 2)

                tex[:, :, 2] = np.maximum(tex[:, :, 2], 255.0 * profile)
                tex[:, :, 1] = tex[:, :, 1] * (1.0 - 0.65 * profile)
                tex[:, :, 0] = tex[:, :, 0] * (1.0 - 0.6 * profile)

        return tex, lo

    def _distortion_maps(self) -> tuple[np.ndarray, np.ndarray]:
        if self._remap is None:
            xs, ys = np.meshgrid(np.arange(self.width, dtype=np.float64), np.arange(self.height, dtype=np.float64))
            pts = np.column_stack([xs.ravel(), ys.ravel()]).reshape(-1, 1, 2)
            und = cv2.undistortPoints(pts, self.camera_matrix, self.dist, P=self.camera_matrix)
            und = und.reshape(self.height, self.width, 2).astype(np.float32)
            self._remap = (und[:, :, 0].copy(), und[:, :, 1].copy())

        return self._remap

    def render(self, angle_deg: float, tilt_deg: float = 0.0, *, laser: bool = True) -> np.ndarray:
        R, t = self.board_pose(angle_deg, tilt_deg)

        ideal = np.full((self.height, self.width, 3), 18.0)

        laser_on_front = laser and float((self.R_wc @ (self.laser_source_w - self.C_w) - t) @ R[:, 2]) < 0.0
        front = self.board_visible(angle_deg, tilt_deg)

        if front:
            tex, lo = self._texture(R, t, laser_on_front)
        else:
            tex, lo = self._texture(R, t, False)
            tex[:] = 120.0  # the grey backing plate

        s = self.texture_px_per_mm
        to_board = np.array([[1.0 / s, 0.0, lo], [0.0, 1.0 / s, lo], [0.0, 0.0, 1.0]])
        H = self.camera_matrix @ np.column_stack([R[:, 0], R[:, 1], t]) @ to_board

        warped = cv2.warpPerspective(tex.astype(np.float32), H, (self.width, self.height), flags=cv2.INTER_AREA)
        mask = cv2.warpPerspective(np.ones(tex.shape[:2], np.float32), H, (self.width, self.height))

        ideal = ideal * (1.0 - mask[:, :, None]) + warped

        map_x, map_y = self._distortion_maps()
        image = cv2.remap(ideal.astype(np.float32), map_x, map_y, cv2.INTER_LINEAR, borderValue=(18, 18, 18))

        image = cv2.GaussianBlur(image, (3, 3), 0.6)

        if self.noise > 0:
            image = image + self._rng.normal(0.0, self.noise, image.shape)

        return np.clip(image, 0, 255).astype(np.uint8)
