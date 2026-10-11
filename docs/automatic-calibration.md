# Automatic calibration

The scanner needs three calibrations before a physical scan can produce metric
points: the lens, the turntable axis and the laser plane. The automatic
calibration measures all three, and the scan volume, from **one turn of the
platform with the checkerboard standing on it**.

## What you need

- The printed checkerboard, glued flat on a rigid backing. The reference board
  has 9 x 6 inner corners and 10 mm squares.
- The square size measured with a ruler after printing: measure ten squares in
  a row and divide by ten. Type that value in the dashboard, not the nominal one.
- The scanner in **physical** mode, with the camera fixed in its final position.
- The laser on, and **fixed so it cannot turn or slide** in its holder. Its line
  must stay in the same place for the whole calibration and for every scan
  after it.
- **Laser detection** (Scan Configuration card) set to match the lighting:
  *Red channel* in a box lit with green or blue LEDs, where the laser looks
  white on the paper; *Colour (HSV)* under white or warm light.

## Procedure

In the dashboard, **Calibration** card:

1. Stand the board upright on the platform, 2-3 cm in front of the centre pin,
   printed side facing the camera.
2. Press **Calibrate**.
3. Do not touch the box until it finishes (about 4 minutes on the Raspberry Pi
   3B+ at 36 stops).

The platform turns once, stopping 36 times and photographing every stop, then
returns to where it started and takes one more photograph there. The result is
computed and, if everything passes, saved. The board is only visible for about
a third of the turn; that is expected.

- **Stop** ends the turn at the next stop of the platform.
- **Compute again** recomputes from the same photographs, for example after
  changing the laser detection. No new turn is needed.
- **Download photos (zip)** gives every photograph, the run record and the
  overlays, for a diagnosis on another computer.

Each **Calibrate** starts from scratch: the photographs of the previous run are
deleted first.

## What is computed

From the stored photographs, never from live frames:

- **Lens**: intrinsics and distortion from every photograph showing the board.
  `k3` is held at zero: the board only covers part of the image, and a free
  `k3` fits that region and then diverges towards the image edges. A
  photograph with an outlying reprojection error is dropped once.
- **Turntable axis**: the board centre describes a circle around the axis.
- **Laser plane**: the laser line is located on the printed area of the board
  in every photograph (everything else is masked out) and back-projected onto
  the board plane, which the lens calibration makes known in millimetres.
- **Laser stability**: the first and the closing photograph show the same
  scene, so the laser line must fall on the same pixels in both. More than
  2 px apart means the laser (or the board) moved during the turn, and the
  laser plane is refused. On the real scanner two photographs of an unchanged
  scene agree within 0.05 px.
- **Scan volume**: every photograph is triangulated with the new calibration.
  The platform is the dense band of laser points at the bottom: heights are
  measured from it from now on, and points less than about 2-4 mm above it
  are left out of scans (they are the platform, not the object). The laser on
  the box does not move while the platform turns: the nearest of those static
  points, less 5 mm, is the scan radius. Either is skipped, with a warning,
  when it is not found; the configured limits then apply. The learnt volume
  can only tighten the configured `reconstruction` limits, never widen them.

Lens, turntable and laser are saved **together or not at all**: a run that
cannot produce a consistent set never overwrites the calibration on disk.

## Accuracy: one turn against three placements

The automatic calibration can also use the board leaning back and leaning
forward (steps 2 and 3, API only). On the real scanner, step 1 alone gave a
focal length within 0.5 % of the three-placement result, and on the synthetic
rig one turn triangulates with about 0.5-0.66 mm mean error against
0.37-0.48 mm for three placements (640x480 test images). One turn was chosen:
a third of the time, and no handling of the box between placements, which is
what moved the laser on the real scanner.

## Reading the result

The dashboard shows one line per calibration and an overlay image: the
detected corners, and in green the laser points that were used.

- *Lens reprojection error*: below 1 px is good.
- *Turntable fit error*: below 0.5 mm is good.
- *Scan volume*: how far from the centre an object may reach, and how much
  above the platform is left out.
- *Laser: line found on N of M photographs*: if N is low, open the overlay. A
  laser line visible in the photograph but not marked in green means the
  detector does not match how this camera sees the laser: try the other
  **Laser detection** method, then **Compute again**.
- *Laser: the laser moved during the turn*: fix the laser holder, check the
  board stands firmly, do not touch the box, and calibrate again.

## Files

```
outputs/calibration/auto/
    run.json            board, motor steps, every photograph with its motor
                        position, angle and detected corners, last result
    step1/frame_000.jpg ...
    step1/closing.jpg   the extra photograph after the full turn
outputs/diagnostics/auto_calibration/
    step1.jpg           overlay from the last computation
```

## API

| Method | Route | |
| --- | --- | --- |
| `GET` | `/api/calibration/auto` | Progress of a running turn, stored run summary, last result |
| `POST` | `/api/calibration/auto/one-turn` | The **Calibrate** button: delete the old photographs, turn once, compute and save. Body: `{"checkerboard": {"columns", "rows", "square_size_mm"}}`. Physical mode only. |
| `POST` | `/api/calibration/auto/step/<1\|2\|3>` | Capture one placement without computing (multi-placement calibration) |
| `POST` | `/api/calibration/auto/stop` | Stop the running turn |
| `POST` | `/api/calibration/auto/compute` | Compute and save from the stored photographs. `201` when saved, `422` with the report when not |
| `POST` | `/api/calibration/auto/reset` | Delete the stored photographs |
| `GET` | `/api/calibration/auto/download` | The photographs, run record and overlays as a zip |

## Accuracy on the synthetic rig

The tests render a 9 x 6 board on a turntable with known geometry, lens
distortion and image noise, optionally with the platform and the back wall
lit by the laser (`tests/_board_scene.py`). These numbers come from rendered
images; the physical scanner will be worse, by an amount that only a scan of
an object with known dimensions can measure.
