# Hardware Integration Plan

This document describes the planned steps to integrate the Scanner3D software with the physical Raspberry Pi scanner hardware.

The goal is to avoid testing everything at once. The integration must be done step by step, validating each hardware component before running a full scan.

---

## Current Software Status

The software already includes:

- Web dashboard
- Live camera stream
- Capture system
- Scan pipeline
- Simulation mode
- Runtime settings
- Point cloud generation
- PLY download
- Scan sessions
- Metadata generation
- Calibration planning
- Future 3D viewer planning

The current software can run on PC/notebook in simulation mode.

---

## Target Hardware

Planned hardware:

- Raspberry Pi 3
- USB webcam
- Red laser module
- Rotating platform
- Stepper motor
- Motor driver
- External power supply
- Physical scanner frame

Possible motor options:

- 28BYJ-48 + ULN2003
- NEMA 17 + DRV8825

---

## Integration Strategy

The integration must be done in layers:

```text
1. Raspberry Pi setup
2. Web server test
3. Camera test
4. Capture test
5. Laser visual test
6. Motor test
7. Pipeline test with simulated motor
8. Pipeline test with real motor
9. Real scan session test
10. Real point cloud generation