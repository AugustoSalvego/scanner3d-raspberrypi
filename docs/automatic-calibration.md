# Automatic calibration

The scanner needs three calibrations before a physical scan can produce metric
points: the lens, the turntable axis and the laser plane. The automatic
calibration measures all three with the checkerboard standing on the
turntable. The camera never moves; only the board is repositioned between
steps.

## What you need

- The printed checkerboard, glued flat on a rigid backing. The reference board
  has 9 x 6 inner corners and 10 mm squares.
- The square size measured with a ruler after printing: measure ten squares in
  a row and divide by ten. Type that value in the dashboard, not the nominal one.
- The scanner in **physical** mode, with the camera and the laser fixed in
  their final position.
- The laser on. The reference build cannot switch it by software, and the board
  is still found with the laser line crossing it.
- The laser **fixed so it cannot turn or slide** in its holder. Its line must
  stay in the same place for the whole calibration (and afterwards, for every
  scan). If it moves, the laser part of the calibration is refused (see below).
- **Laser detection** (Scan Configuration card) set to match the lighting:
  *Red channel* in a box lit with green or blue LEDs, where the laser looks
  white on the paper; *Colour (HSV)* under white or warm light.

## Procedure

In the dashboard, **Calibration** card, **Automatic calibration** section:

| Step | Board placement | Then |
| --- | --- | --- |
| 1 | Standing upright 2-3 cm in front of the centre pin, printed side facing the camera | **Run step 1** |
| 2 | Same place, tilted back 15-25 degrees like a picture frame, facing the camera | **Run step 2** |
| 3 | Same place, tilted forward 15-25 degrees, facing the camera | **Run step 3** |

Each step turns the platform once, stopping 36 times, and photographs every
stop. The board is only visible for about a third of the revolution; that is
expected. The status next to each step reports how many photographs show the
board. At the end of a step the platform is back where it started, facing the
camera.

Then press **Compute and save**.

- **Stop** ends the running step at the next stop of the platform.
- Running a step again replaces only that step's photographs.
- **Start over** deletes the photographs of all three steps.

## What is computed

From the stored photographs, never from live frames, so **Compute and save**
can be pressed again (for example after adjusting the laser detector) without
capturing again:

- **Lens**: intrinsics and distortion from every photograph showing the board.
  `k3` is held at zero: the board only covers part of the image, and a free
  `k3` fits that region and then diverges towards the image edges. A
  photograph with an outlying reprojection error is dropped once.
- **Turntable axis**: the board centre describes a circle around the axis
  during each step. Each step gives an independent estimate; they must agree
  within 2 degrees and 1.5 mm. If they do not, the platform probably tilts
  under the board (an off-centre load on the stepper shaft) or the board moved
  during a revolution, and nothing is saved.
- **Laser plane**: the laser line is located on the printed area of the board
  in every photograph (everything else is masked out) and back-projected onto
  the board plane, which the lens calibration makes known in millimetres.
  Points from all photographs are pooled into one plane.
- **Laser stability**: before pooling, the points of each step are fitted as
  parallel planes sharing one direction, and the gap between them is reported
  per step (`shift_by_step_mm`, measured against step 1; positive means the
  line moved right in the image). More than 1 mm between steps means the laser
  moved between the placements, and the laser plane is refused, since no single
  plane describes a laser that was in two places.

Lens, turntable and laser are saved **together or not at all**: a run that
cannot produce a consistent set never overwrites the calibration on disk.

## Reading the result

The dashboard shows one line per calibration and one overlay image per step:
the detected corners, and in green the laser points that were used.

- *Lens reprojection error*: below 1 px is good.
- *Turntable: the placements agree within ...*: large values mean wobble.
- *Laser: line found on N of M photographs*: if N is low, open the overlays. A
  laser line visible in the photograph but not marked in green means the
  detector does not match how this camera sees the laser: try the other
  **Laser detection** method, then **Compute and save** again (no new
  photographs needed).
- *Laser: the laser moved between the steps (step 2: -3.8 mm, ...)*: the laser
  holder turned or slid, or the box was bumped. Fix the laser firmly, do not
  touch the box while a step runs, and run the three steps again: these
  photographs cannot be reused for the laser.

## Files

```
outputs/calibration/auto/
    run.json            board, motor steps, every photograph with its motor
                        position, angle and detected corners, last result
    step1/frame_000.jpg ...
outputs/diagnostics/auto_calibration/
    step1.jpg ...       overlays from the last computation
```

## API

| Method | Route | |
| --- | --- | --- |
| `GET` | `/api/calibration/auto` | Progress of a running step, stored run summary, last result |
| `POST` | `/api/calibration/auto/step/<1\|2\|3>` | Start a step. Body: `{"checkerboard": {"columns", "rows", "square_size_mm"}}`. Physical mode only. |
| `POST` | `/api/calibration/auto/stop` | Stop the running step |
| `POST` | `/api/calibration/auto/compute` | Compute and save. `201` when saved, `422` with the report when not |
| `POST` | `/api/calibration/auto/reset` | Delete the stored photographs |

## Accuracy on the synthetic rig

The tests render a 9 x 6 board on a turntable with known geometry, lens
distortion and image noise (`tests/_board_scene.py`). At 1280x720 with 36 stops
the recovered calibration triangulates laser points with about 0.31 mm mean and
0.41 mm maximum error, and the axis is within 0.02 degrees. These numbers come
from rendered images; the physical scanner will be worse, by an amount that
only a scan of an object with known dimensions can measure.
