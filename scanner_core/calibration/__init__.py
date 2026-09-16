"""Calibration subsystem.

``store``
    Data models, content validation and JSON persistence.
``camera``
    Checkerboard intrinsics and board pose estimation.
``laser``
    Laser plane fitting from board poses.
``turntable``
    Rotation axis, direction and motor resolution.
"""

from scanner_core.calibration.camera import (
    CheckerboardSpec,
    CornerDetection,
    calibrate_camera_from_detections,
    calibrate_camera_from_folder,
    detect_corners_in_images,
    estimate_board_pose,
    find_checkerboard,
    list_calibration_images,
    undistort_image,
)
from scanner_core.calibration.laser import (
    LaserCalibrationReport,
    LaserPoseSample,
    calibrate_laser_plane_from_folder,
    collect_laser_points_on_board,
    fit_laser_plane,
)
from scanner_core.calibration.store import (
    CALIBRATION_SCHEMA_VERSION,
    SOURCE_SYNTHETIC,
    CalibrationBundle,
    CalibrationStore,
    CameraCalibration,
    LaserPlaneCalibration,
    TurntableCalibration,
    calibration_store,
)
from scanner_core.calibration.turntable import (
    AxisObservation,
    TurntableEstimate,
    build_manual_turntable_profile,
    build_turntable_profile,
    estimate_turntable_axis,
    track_target_across_images,
)

__all__ = [
    "AxisObservation",
    "CALIBRATION_SCHEMA_VERSION",
    "CalibrationBundle",
    "CalibrationStore",
    "CameraCalibration",
    "CheckerboardSpec",
    "CornerDetection",
    "LaserCalibrationReport",
    "LaserPlaneCalibration",
    "LaserPoseSample",
    "SOURCE_SYNTHETIC",
    "TurntableCalibration",
    "TurntableEstimate",
    "build_manual_turntable_profile",
    "build_turntable_profile",
    "calibrate_camera_from_detections",
    "calibrate_camera_from_folder",
    "calibrate_laser_plane_from_folder",
    "calibration_store",
    "collect_laser_points_on_board",
    "detect_corners_in_images",
    "estimate_board_pose",
    "estimate_turntable_axis",
    "find_checkerboard",
    "fit_laser_plane",
    "list_calibration_images",
    "track_target_across_images",
    "undistort_image",
]
