"""Smoke tests for the calibration API; hardware drivers are not invoked."""
from web_interface.app import app


def test_calibration_status_returns_guided_workflow():
    client = app.test_client()
    response = client.get("/api/calibration/workflow")
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"]
    assert data["data"]["board"] == [9, 6]
    assert data["data"]["stops_per_revolution"] == 36
    assert "camera" in data["data"]["profiles"]
    assert data["data"]["right_search_step_deg"] == 5.0
    assert data["data"]["left_capture_step_deg"] == 3.0
    assert data["data"]["settle_seconds"] == 0.4


def test_reject_invalid_capture_parameters_without_touching_motor():
    client = app.test_client()
    response = client.post("/api/calibration/workflow/capture", json={
        "kind": "unknown", "placement": 1, "square_size_mm": 10
    })
    assert response.status_code == 409
    assert response.get_json()["success"] is False

    response = client.post("/api/calibration/workflow/capture", json={
        "kind": "camera", "placement": 0, "square_size_mm": 10
    })
    assert response.status_code == 409


def test_diagnostics_filename_is_whitelisted():
    client = app.test_client()
    response = client.get("/api/calibration/workflow/diagnostics/invalid")
    assert response.status_code == 404


def test_reject_tilted_placements_in_upright_only_mode():
    client = app.test_client()
    response = client.post("/api/calibration/workflow/capture", json={
        "kind": "camera", "placement": 2, "square_size_mm": 10.0
    })
    assert response.status_code == 409
    assert response.get_json()["success"] is False
