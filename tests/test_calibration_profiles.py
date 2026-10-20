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



def test_calibration_badges_require_saved_geometry_not_only_flag(monkeypatch, tmp_path):
    """A placeholder marked calibrated cannot appear as a valid profile."""
    monkeypatch.setattr(wf, "RUN_DIR", tmp_path / "work")
    monkeypatch.setattr(wf, "RUN_FILE", tmp_path / "work" / "run.json")
    monkeypatch.setattr(wf, "CAMERA_FILE", tmp_path / "camera.json")
    monkeypatch.setattr(wf, "AXIS_FILE", tmp_path / "axis.json")
    monkeypatch.setattr(wf, "LASER_FILE", tmp_path / "laser.json")
    for path in (wf.CAMERA_FILE, wf.AXIS_FILE, wf.LASER_FILE):
        path.write_text(json.dumps({"calibrated": True}), encoding="utf-8")
    profiles = wf.calibration_status()["profiles"]
    assert all(not item["calibrated"] for item in profiles.values())

    camera = {
        "calibrated": True,
        "camera_matrix": [[800, 0, 320], [0, 800, 240], [0, 0, 1]],
        "distortion_coefficients": [0, 0, 0, 0, 0],
        "image_size": [640, 480],
    }
    axis = {
        "calibrated": True,
        "axis_origin_camera_mm": [0, 0, 300],
        "axis_direction_camera": [0, 1, 0],
    }
    laser = {
        "calibrated": True,
        "plane_normal_camera": [1, 0, 0],
        "plane_offset_mm": -20,
    }
    for path, value in [
        (wf.CAMERA_FILE, camera),
        (wf.AXIS_FILE, axis),
        (wf.LASER_FILE, laser),
    ]:
        path.write_text(json.dumps(value), encoding="utf-8")
    profiles = wf.calibration_status()["profiles"]
    assert all(item["calibrated"] for item in profiles.values())


def test_calibration_page_labels_do_not_require_manual_second_step():
    from pathlib import Path

    html = Path("web_interface/templates/index.html").read_text(encoding="utf-8")
    assert html.count("Iniciar calibração") == 2
    assert html.count("Recalcular fotos salvas") == 2
    assert "Capturar sequência" not in html
    assert "Calcular com as fotos" not in html
    assert 'id="cal-feedback-title"' in html
    assert 'id="cal-progress-track"' in html
