"""Scanner3D core.

Layout:

``config`` / ``paths`` / ``logger`` / ``state`` / ``errors``
    Foundations: typed configuration, output layout with safe path resolution,
    logging, the single source of live state, and the exception hierarchy.

``geometry`` / ``laser_detection`` / ``reconstruction`` / ``point_cloud``
    The vision and maths chain: sub-pixel laser extraction, camera rays,
    ray/plane triangulation, turntable de-rotation, PLY input/output and
    filtering.

``calibration``
    Camera intrinsics, laser plane and turntable axis, with content validation.

``hardware``
    Adapters for the real webcam, the 28BYJ-48 stepper and the laser, plus
    their synthetic counterparts.

``simulation``
    A virtual rig with analytic ground truth, so the real pipeline can be
    exercised and tested without any hardware.

``session`` / ``scan_plan`` / ``pipeline`` / ``status``
    Scan storage, capture planning, orchestration and the composite health
    report.
"""

from scanner_core.config import APP_VERSION

__all__ = ["APP_VERSION"]
