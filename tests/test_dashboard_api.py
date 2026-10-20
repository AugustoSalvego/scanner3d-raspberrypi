"""Tests for persistent settings and the history HTTP endpoints."""
import json
from pathlib import Path

from scanner_core import runtime_settings as settings
from scanner_core import session
from web_interface.app import app


def test_angle_is_freeform_persisted_and_invalid_updates_are_atomic(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    old = settings.get_settings()
    try:
        updated, errors = settings.update_settings({"angle_deg": "7,5"})
        assert errors == []
        assert updated["scan_steps"] == 48
        assert updated["angle_deg"] == 7.5
        assert json.loads(settings.SETTINGS_FILE.read_text())["angle_deg"] == 7.5
        current, errors = settings.update_settings({
            "angle_deg": "7", "step_delay": -100,
        })
        assert errors
        assert current == updated
        assert json.loads(settings.SETTINGS_FILE.read_text())["angle_deg"] == 7.5
        assert settings._load_settings()["scan_steps"] == 48
    finally:
        with settings._settings_lock:
            settings._runtime_settings.update(old)


def test_history_rename_preview_and_delete(monkeypatch, tmp_path):
    folder = tmp_path / "scans"
    clouds = tmp_path / "clouds"
    monkeypatch.setattr(session, "SCANS_FOLDER", str(folder))
    monkeypatch.setattr(session, "POINT_CLOUD_FOLDER", str(clouds))
    clouds.mkdir()
    current = session.create_scan_session()
    session_id = current["session_id"]
    session.finish_current_session()
    ply = clouds / ("real_" + session_id + ".ply")
    ply.write_text(
        "ply\nformat ascii 1.0\nelement vertex 1\n"
        "property float x\nproperty float y\n"
        "property float z\nend_header\n0 1 2\n"
    )
    with app.test_client() as client:
        items = client.get("/api/sessions").get_json()["data"]["sessions"]
        assert any(x["session_id"] == session_id and x["ply_available"] for x in items)
        response = client.post(
            "/api/sessions/" + session_id + "/rename",
            json={"name": "Peça de teste"},
        )
        assert response.status_code == 200
        assert session.read_scan_session(session_id)["display_name"] == "Peça de teste"
        response = client.get("/api/sessions/" + session_id + "/ply")
        assert response.status_code == 200
        assert b"element vertex 1" in response.data
        response = client.delete("/api/sessions/" + session_id)
        assert response.status_code == 200
        assert not folder.joinpath(session_id).exists()
        assert not ply.exists()


def test_history_rejects_traversal_and_invalid_names(monkeypatch, tmp_path):
    monkeypatch.setattr(session, "SCANS_FOLDER", str(tmp_path))
    current = session.create_scan_session()
    ident = current["session_id"]
    client = app.test_client()
    response = client.post("/api/sessions/" + ident + "/rename", json={"name": "  "})
    assert response.status_code == 400
    response = client.get("/api/sessions/scan_missing/ply")
    assert response.status_code == 404
    response = client.delete("/api/sessions/scan_not_found")
    assert response.status_code == 404
