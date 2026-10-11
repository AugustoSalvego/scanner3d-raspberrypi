# Physical calibration — chatgpt-version

## Current status

Camera/turntable/laser calibration has been **implemented, but not yet physically validated**.
Do not represent these profiles as measured or accurate until the Raspberry Pi
actually captures a set and reports numerical quality metrics.

The scanner must be steady: camera and laser fixed, turntable cannot lean, motor
must not miss steps. A tilted, off-centre calibration board can make the axis
calibration unreliable. No laser exposure to eyes.

## Checkerboard

* **9 columns x 6 rows of INTERNAL corners** (10 x 7 printed black/white squares).
* Square size is nominally **10 mm** according to the provided checkerboard PDF,
  but print at **100% actual size**, measure ten squares with a ruler, and enter
  the measured size (for example 9.98 mm) in the dashboard.
* Glue to a flat, rigid, preferably light backing. Stand it vertically,
  about 20 mm to the side of the platform centre, facing the fixed camera.
  The entire platform must rotate without leaning under the board.
* The number of motor half-steps per actual turntable revolution is initially
  assumed to be 4096 in `scanner_core/config.py`. THIS IS NOT CALIBRATED.
  Check the physical revolution before trusting the recovered geometry.

## How to use

1. `git fetch origin && git switch chatgpt-version && git pull --ff-only` on the Pi
   (if branch only exists remotely: `git switch --track origin/chatgpt-version`).
2. Reuse your venv with system OpenCV, NumPy, Flask and gpiozero packages.
   `python -m web_interface.app`, then browse to `http://scanner.local:5000`.
3. Open **Calibrate**. Enter square size measured with a real ruler.
4. Turn **laser OFF**, **green contrast LEDs OFF**, turn on soft **WHITE light**.
5. Upright board: **Camera 1**: 36 photos in one revolution.
6. Board tilted backward ~15-25 degrees: **Camera 2**.
7. Board tilted forward ~15-25 degrees: **Camera 3**.
8. Press **Compute Camera + Axis** and wait for the RMS result.
   Photos where the board was facing away are automatically excluded.
   If the app rejects the calibration, inspect the diagnostic overlay, change
   lighting, support, or board placement and retake the weak step(s).
9. Turn on the **RED laser**. Keep enough diffuse white light for all squares
   to remain visible (laser-only darkness makes chessboard detection impossible).
   Disable green lights. Keep camera and laser mechanically fixed.
10. Press **Laser 1** (upright), optionally **Laser 2 / Laser 3** with matching
    backward/forward tilt. Then **Compute Laser Plane**.
11. Read camera RMS in pixels, axis disagreement in millimetres/degrees, and
    laser-plane RMS in millimetres. The profiles are saved to
    `outputs/calibration/{camera_calibration,turntable_calibration,laser_calibration}.json`.
    They refer to the EXACT current hardware placement/resolution.

While scanning/calibrating: never move the camera, laser, board or housing.
The Stop action interrupts the motor and stores the partial photo list; a
partial step cannot be used as a finished calibration step.

## Data and diagnosis

```text
outputs/calibration/
  camera_calibration.json       lens matrix and distortion, RMS
  turntable_calibration.json    axis origin/direction in camera coordinates
  laser_calibration.json        plane normal and offset in camera coordinates
  chatgpt_version/
    run.json                     capture stage, motor angle, 54 detected corners
    camera1/frame_000.jpg ...    white light, laser off
    camera2/ ...
    camera3/ ...
    laser1/frame_000.jpg ...     red line on visible checkerboard
    diagnostics/camera1.jpg ...  last successfully detected board corners
```

## Known limitations

- Camera and axis estimation require at least 12 detected checkerboard views
  overall and at least six detected views per placement.
- The motor uses OPEN-LOOP commanded steps: backlash, wrong steps per revolution
  or wobble will still degrade the result.
- Laser line is extracted with an HSV red detector. Some cameras saturate the
  line and may require channel-relative or laser-off subtraction refinement.
- The laser calibration needs enough images where the board is recognized
  **and** the projected red line crosses its printed area.
- Scanner physical `Start Scan` currently takes photos and records the angles,
  **but real ray/plane PLY reconstruction and mesh export are the next milestone**.
  The existing `generate-ply` is demo-only, and must never be claimed as
  a scan of the photographed object.
- These geometric solvers are original integrations of standard camera
  calibration, pose estimation, least squares and plane fitting, using OpenCV
  and NumPy. Cite the libraries and algorithms in the TCC.

## Developer verification

On development computer or Pi:
```bash
python -m pip install pytest
python -m compileall -q scanner_core web_interface
python -m pytest tests/test_calibration_geometry.py -q
```

Then verify physical camera preview, 36-step capture for each placement, motor
complete-turn accuracy and checkerboard overlay. Only report calibration
as successful after these real tests have run.
