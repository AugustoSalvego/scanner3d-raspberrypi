"""Hardware-free regression for the right-center-left calibration trajectory.

Synthetic camera observations and a fake motor verify control flow only.
They are NOT a claim that the user's real hardware is calibrated.
"""
import json

import numpy as np

from scanner_core import calibration_workflow as wf


def test_right_boundary_then_center_then_left_photos(monkeypatch, tmp_path):
    folder = tmp_path / "calibration"
    monkeypatch.setattr(wf, "RUN_DIR", folder)
    monkeypatch.setattr(wf, "RUN_FILE", folder / "run.json")
    monkeypatch.setattr(wf, "SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(wf, "MOTOR_HALF_STEPS_PER_REV", 4096)

    moves = []
    fake_context = {"motor": None}
    class FakeMotor:
        def __init__(self, phase_delay=None):
            self.position = 0

        def __enter__(self):
            fake_context["motor"] = self
            return self

        def __exit__(self, *_):
            return False

        def move(self, half_steps, cancelled=None):
            assert not (cancelled and cancelled())
            self.position += int(half_steps)
            moves.append(self.position * 360.0 / 4096.0)

    def fake_frame():
        motor = fake_context["motor"]
        angle = motor.position * 360.0 / 4096.0
        img = np.zeros((90, 120, 3), dtype=np.uint8)
        img[0, 0, 0] = int(round(angle)) + 120
        return img

    # The webcam sees the printed face from 0 to about ±75 degrees.
    corners = np.array([(8+9*c, 8+9*r) for r in range(6) for c in range(9)],
                       dtype=np.float32).reshape(-1, 1, 2)
    def fake_detect(img):
        angle = float(img[0, 0, 0]) - 120.0
        if -75 <= angle <= 70:
            return corners.copy()
        return None

    computations = []
    monkeypatch.setattr(wf, "TurntableMotor", FakeMotor)
    monkeypatch.setattr(wf.camera_device, "get_frame", fake_frame)
    monkeypatch.setattr(wf, "find_board", fake_detect)
    monkeypatch.setattr(
        wf, "_compute_worker",
        lambda kind, release_gate=True: computations.append((kind, release_gate))
    )

    wf._stop.clear()
    assert wf.hardware_gate.acquire(blocking=False)
    try:
        wf._capture_worker("camera", 1, 10.0)
    finally:
        if wf.hardware_gate.locked():
            wf.hardware_gate.release()

    run = json.loads((folder / "run.json").read_text())
    stage = run["stages"]["camera1"]
    assert stage["status"] == "complete", stage.get("error")
    assert 69 <= stage["right_last_visible_deg"] <= 70
    assert stage["left_last_visible_deg"] == 75.0
    assert 20 <= len(stage["frames"]) <= wf.STOPS
    assert computations == [("camera", False)]

    # The discovery path goes RIGHT (positive), comes back to zero,
    # and image files appear only with LEFT (non-positive) angle metadata.
    assert max(moves) >= 80
    assert any(abs(position) < .05 for position in moves[15:])
    assert min(moves) <= -80
    assert abs(moves[-1]) < .05
    angles = [item["angle_deg"] for item in stage["frames"]]
    assert max(angles) <= 0.05
    assert min(angles) <= -74.9
    assert len(angles) == len(set(angles))
    for item in stage["frames"]:
        assert (folder / item["filename"]).is_file()


def test_archive_before_replacing_existing_camera_photos(monkeypatch, tmp_path):
    monkeypatch.setattr(wf, "RUN_DIR", tmp_path)
    monkeypatch.setattr(wf, "RUN_FILE", tmp_path / "run.json")
    old = tmp_path / "camera1"
    old.mkdir()
    (old / "frame_000.jpg").write_text("keep-old-photo")
    run = {"stages": {"camera1": {"status": "complete", "frames": ["old"]}}}
    wf._archive_previous_stage(run, "camera1", old)
    archived = list((tmp_path / "history").glob("camera1_*"))
    assert len(archived) == 1
    assert (archived[0] / "photos" / "frame_000.jpg").read_text() == "keep-old-photo"
    old_stage = json.loads((archived[0] / "stage_metadata.json").read_text())
    assert old_stage["status"] == "complete"
