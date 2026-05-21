# Calibration Plan

This document describes the future calibration strategy for the Scanner3D Raspberry Pi project.

Calibration is not fully implemented yet. This document defines what will be needed when the project moves from simulated point cloud generation to real laser triangulation and physical 3D reconstruction.

---

## Why Calibration Is Needed

A real 3D laser scanner depends on geometry.

The system needs to understand the relationship between:

```text
camera position
laser plane
turntable center
object position
motor angle
image pixels
real-world coordinates
```

Without calibration, the software can still detect the laser line and generate an approximate point cloud, but the result will not be geometrically accurate.

Calibration helps convert image data into meaningful 3D coordinates.

---

## Current Status

Current software status:

```text
- Camera capture works
- Scan sessions work
- Captures are saved by scan step
- Simulated point cloud generation works
- PLY export works
- Real laser triangulation is not implemented yet
- Camera calibration is not implemented yet
```

Current hardware status:

```text
- Raspberry Pi prototype exists
- USB webcam is used
- Red laser is used
- Rotating platform is used
- Stepper motor is used
```

---

## Calibration Goals

The future calibration system should help estimate:

```text
1. Camera intrinsic parameters
2. Camera distortion
3. Laser plane position
4. Turntable center
5. Scale conversion
6. Motor angle per step
```

---

# 1. Camera Intrinsic Calibration

Camera intrinsic calibration estimates internal camera properties.

Important values:

```text
focal length
optical center
lens distortion
camera matrix
distortion coefficients
```

In OpenCV, this is usually done with:

```text
cv2.findChessboardCorners()
cv2.calibrateCamera()
cv2.undistort()
```

Typical calibration target:

```text
checkerboard pattern
```

Example output:

```text
camera_matrix
distortion_coefficients
```

Why this matters:

```text
cheap webcams can distort the image
distortion affects laser line position
wrong pixel position creates wrong 3D points
```

---

# 2. Laser Plane Calibration

A laser scanner works because the laser creates a plane of light.

The camera sees where this plane intersects the object.

To reconstruct 3D points, the software needs to estimate the laser plane.

Important relationship:

```text
camera ray
+
laser plane
=
3D point
```

Future goal:

```text
estimate laser plane equation
```

Example plane equation:

```text
ax + by + cz + d = 0
```

This is not implemented yet.

---

# 3. Turntable Calibration

The object rotates on a platform.

For every capture, the software needs to know the object angle.

Important values:

```text
number of steps per full rotation
angle per step
rotation direction
turntable center
platform stability
```

Example:

```text
360 degrees / 120 scan steps = 3 degrees per step
```

The turntable center is important because the point cloud will be built around the rotation axis.

---

# 4. Scale Calibration

The scanner needs a way to estimate real-world scale.

Possible methods:

```text
scan an object with known dimensions
use a calibration block
use a ruler/marker in the scene
measure platform diameter
```

Example:

```text
known object height = 50 mm
measured point cloud height = 0.82 units
scale factor = 50 / 0.82
```

This can be used later to convert scanner units into millimeters.

---

# 5. Practical Calibration Workflow

A future calibration workflow could be:

```text
1. Place checkerboard in front of camera
2. Capture multiple checkerboard images
3. Run camera calibration
4. Save camera matrix and distortion coefficients
5. Position laser correctly
6. Capture laser on flat calibration surface
7. Estimate laser plane
8. Place known object on turntable
9. Run test scan
10. Compare point cloud with real dimensions
11. Adjust scale and alignment
```

---

# 6. Calibration Files

Future calibration files may be stored in:

```text
outputs/calibration/
```

Possible files:

```text
camera_matrix.json
distortion_coefficients.json
laser_plane.json
turntable_config.json
scale_config.json
```

---

# 7. Future Calibration Module

A future module may be created at:

```text
scanner_core/calibration.py
```

Possible responsibilities:

```text
load calibration data
save calibration data
check if calibration exists
undistort camera frames
apply scale correction
provide calibration status
```

---

# 8. Calibration Status

The software should eventually expose calibration status to the dashboard.

Example status:

```json
{
  "camera_calibrated": false,
  "laser_calibrated": false,
  "turntable_calibrated": false,
  "scale_calibrated": false
}
```

This could later appear in the web interface:

```text
Calibration Status

Camera: Not calibrated
Laser Plane: Not calibrated
Turntable: Not calibrated
Scale: Not calibrated
```

---

# 9. What Will Not Be Done Yet

The following are not implemented in the current version:

```text
real camera calibration
checkerboard detection
laser plane estimation
real scale correction
automatic calibration wizard
camera undistortion in pipeline
```

These features will be added after the physical scanner is tested again.

---

# 10. Risks and Limitations

Calibration may be difficult because:

```text
cheap webcams have poor optics
laser line may be too thick
laser may saturate the image
platform may wobble
motor may lose steps
lighting conditions may change
camera may auto-adjust exposure
```

Main risk:

```text
bad image quality creates bad 3D reconstruction
```

Expected mitigation:

```text
better camera
dark background
stable lighting
fixed camera position
firm motor mount
consistent laser angle
manual exposure if possible
```

---

# 11. Roadmap

Calibration roadmap:

```text
Phase 1:
manual calibration documentation

Phase 2:
save/load calibration config files

Phase 3:
camera checkerboard calibration

Phase 4:
laser plane calibration

Phase 5:
turntable center calibration

Phase 6:
scale correction

Phase 7:
apply calibration to real point cloud reconstruction
```

---

# Summary

Calibration is one of the most important future parts of the Scanner3D project.

The current software is prepared for simulated scans and file/session organization. The next step, after reconnecting the physical scanner, will be to collect better real scan data and then gradually implement calibration.

The goal is not to make a perfect commercial scanner, but to build a technically explainable and functional low-cost 3D scanner prototype.