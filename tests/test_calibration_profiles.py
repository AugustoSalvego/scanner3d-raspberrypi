"""Calibration updates should never destroy the last valid profiles."""
import json

import pytest

from scanner_core import calibration_workflow as wf


def test_profile_commit_rolls_back_if_write_fails(monkeypatch, tmp_path):
    camera = tmp_path / "camera.json"
    axis = tmp_path / "axis.json"
    laser = tmp_path / "laser.json"
    for p in (camera, axis, laser):
        p.write_text(json.dumps({"calibrated": True, "version": "old"}))
    original = {p: p.read_bytes() for p in (camera, axis, laser)}
    real_save = wf._save_json
    calls = []

    def fail_second(path, value):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("Injected write failure")
        real_save(path, value)

    monkeypatch.setattr(wf, "_save_json", fail_second)
    with pytest.raises(OSError, match="Injected write failure"):
        wf._publish_profiles({
            camera: {"calibrated": True, "version": "new"},
            axis: {"calibrated": True, "version": "new"},
            laser: {"calibrated": True, "version": "new"},
        })
    assert all(p.read_bytes() == original[p] for p in original)


def test_pending_geometry_is_not_active(monkeypatch, tmp_path):
    monkeypatch.setattr(wf, "RUN_DIR", tmp_path / "run")
    source = {"calibrated": True, "square_size_mm": 10.0}
    wf._save_json(wf.RUN_DIR / "pending_geometry.json", {
        "camera": source, "axis": source,
    })
    assert wf._pending_geometry() == {"camera": source, "axis": source}
    assert not (tmp_path / "camera_calibration.json").exists()
