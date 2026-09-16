import copy
import json
import threading
from contextlib import nullcontext
from pathlib import Path
import pytest
from scanner_core.pipeline import ScanController, ScanBusy
from scanner_core.runtime_settings import DEFAULT_SETTINGS, get_settings, update_settings
from scanner_core.session import SessionStore, atomic_json, confined_path
from scanner_core.simulation import synthetic_calibration
from web_interface.app import create_app

@pytest.fixture
def configured(tmp_path):
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings.update(scan_steps=6, step_delay=0, capture_delay=0)
    store = SessionStore(tmp_path / "outputs")
    scanner = ScanController(store=store, settings_provider=lambda: copy.deepcopy(settings))
    return scanner, settings

def test_full_simulated_scan_and_persistence(configured):
    scanner, _ = configured
    session_id = scanner.start()
    state = scanner.wait(30)
    assert state["phase"] == "completed", state["last_error"]
    record = scanner.store.load(session_id)
    assert len(record["captures"]) == 6
    assert all(c["status"] == "captured" for c in record["captures"])
    assert record["captures"][0]["angle_deg"] == 0
    assert record["captures"][-1]["angle_deg"] < 360
    assert record["source"] == "synthetic"
    assert scanner.store.current_session is None
    assert state["current_session"] is None
    assert Path(state["last_point_cloud"]).is_file()
    restarted = SessionStore(scanner.store.root)
    assert restarted.list()[0]["status"] == "completed"
    assert record["settings"]["scan_steps"] == 6
    assert record["calibration"]["source"] == "synthetic"

def test_concurrent_scan_cancel_and_restart(configured):
    scanner, settings = configured
    settings["step_delay"] = 1
    first = scanner.start()
    with pytest.raises(ScanBusy):
        scanner.start()
    assert scanner.stop()
    state = scanner.wait(5)
    assert state["phase"] == "cancelled"
    assert scanner.store.load(first)["status"] == "cancelled"
    settings["step_delay"] = 0
    second = scanner.start()
    assert second != first
    assert scanner.wait(30)["phase"] == "completed"
    assert scanner.store.load(first)["status"] == "cancelled"

def test_capture_failure_releases_motor_and_never_completes(configured):
    scanner, settings = configured
    calibration = synthetic_calibration()
    calibration["source"] = "physical"
    path = scanner.store.root / "calibration" / "calibration.json"
    atomic_json(path, calibration)
    settings.update(mode="physical", simulation_mode=False, calibration_path=str(path))
    class BrokenCamera:
        def exclusive(self):
            return nullcontext()
        def configure(self, **kwargs):
            pass
        def capture_to(self, path, cancel_event=None):
            raise RuntimeError("USB capture failed")
    class Motor:
        position = 0
        closed = False
        def move(self, count, cancel_event=None):
            self.position += count
        def close(self):
            self.closed = True
    motor = Motor()
    scanner.camera = BrokenCamera()
    scanner.motor_factory = lambda _: motor
    session_id = scanner.start()
    state = scanner.wait(10)
    assert state["phase"] == "error"
    assert not state["point_cloud_generated"]
    record = scanner.store.load(session_id)
    assert record["status"] == "error"
    assert record["captures"][0]["status"] == "failed"
    assert record["point_clouds"] == []
    assert motor.closed
    assert scanner.store.current_session is None

def test_processing_failure_is_not_success(configured):
    scanner, _ = configured
    def fail(*args, **kwargs):
        raise ValueError("No laser points")
    scanner.reconstruct = fail
    session_id = scanner.start()
    state = scanner.wait(20)
    assert state["phase"] == "error"
    assert scanner.store.load(session_id)["status"] == "error"

def test_cancel_during_processing_remains_reserved(configured):
    scanner, _ = configured
    entered = threading.Event()
    release = threading.Event()
    def process(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        raise RuntimeError("processing cancelled")
    scanner.reconstruct = process
    scanner.start()
    assert entered.wait(10)
    scanner.stop()
    with pytest.raises(ScanBusy):
        scanner.start()
    release.set()
    assert scanner.wait(10)["phase"] == "cancelled"

def test_two_controllers_cannot_own_output_folder(configured):
    scanner, settings = configured
    settings["step_delay"] = 1
    other = ScanController(store=SessionStore(scanner.store.root), settings_provider=lambda: settings)
    scanner.start()
    try:
        with pytest.raises(ScanBusy):
            other.start()
    finally:
        scanner.stop()
        scanner.wait(5)

@pytest.mark.parametrize("change", [
    {"scan_steps": True}, {"scan_steps": 1.5}, {"scan_steps": "6"},
    {"capture_delay": float("nan")}, {"step_delay": float("inf")},
    {"simulation_mode": "false"}, {"camera": {"width": False}}, {"direction": 0},
    {"scan_steps": 8, "step_delay": -1}, {"unknown": 3},
])
def test_settings_validation_is_atomic(change):
    before = get_settings()
    after, errors = update_settings(change)
    assert errors
    assert before == after == get_settings()

def test_snapshot_is_independent(configured):
    scanner, settings = configured
    settings["step_delay"] = 0.01
    session_id = scanner.start()
    settings["scan_steps"] = 2
    assert scanner.wait(30)["phase"] == "completed"
    assert len(scanner.store.load(session_id)["captures"]) == 6

def test_api_status_files_preview_and_manual_capture_after_scan(configured):
    scanner, _ = configured
    app = create_app(scanner)
    app.testing = True
    client = app.test_client()
    assert client.get("/health").json["success"]
    assert client.post("/generate-ply").status_code == 400
    result = client.post("/start-scan")
    assert result.status_code == 200
    assert scanner.wait(30)["phase"] == "completed"
    state = client.get("/status").json["data"]["status"]
    assert state["phase"] == "completed"
    files = client.get("/point-clouds").json["data"]["files"]
    assert len(files) == 2
    assert client.get(files[0]["download_url"]).data.startswith(b"ply\n")
    sessions = client.get("/scan-sessions").json["data"]["sessions"]
    assert client.get(sessions[0]["metadata_url"]).status_code == 200
    assert client.get(sessions[0]["preview_url"]).mimetype == "image/png"
    result = client.post("/capture")
    assert result.status_code == 200
    assert client.get("/captures/" + result.json["file"]).status_code == 200
    assert len(scanner.store.load(sessions[0]["session_id"])["captures"]) == 6
    restarted = create_app(ScanController(store=SessionStore(scanner.store.root)))
    assert restarted.test_client().get("/download-ply").status_code == 200

def test_active_api_blocks_reset_settings_delete_and_manual_capture(configured):
    scanner, settings = configured
    settings["step_delay"] = 2
    client = create_app(scanner).test_client()
    scanner.start()
    try:
        for route in ("/start-scan", "/capture", "/clear-captures", "/reset-camera", "/delete-ply/no.ply"):
            assert client.post(route).status_code == 409
        assert client.post("/settings", json={"scan_steps": 3}).status_code == 409
    finally:
        scanner.stop()
        scanner.wait(5)

def test_path_traversal_and_invalid_bodies(configured, tmp_path):
    scanner, _ = configured
    client = create_app(scanner).test_client()
    assert client.get("/download-ply/../../README.md").status_code in (400, 404)
    assert client.get("/sessions/invalid/files/metadata.json").status_code == 400
    assert client.post("/settings", json=[]).status_code == 400
    assert client.post("/simulation-mode", json={"enabled": "false"}).status_code == 400
    outside = tmp_path / "secret.txt"
    outside.write_text("not exposed")
    with pytest.raises(ValueError):
        confined_path(scanner.store.root, "../secret.txt")

def test_reconstruction_requires_no_new_acquisition(configured):
    scanner, _ = configured
    session_id = scanner.start()
    assert scanner.wait(30)["phase"] == "completed"
    original = scanner.store.load(session_id)
    scanner.start_reconstruction(session_id)
    assert scanner.wait(30)["phase"] == "completed"
    current = scanner.store.load(session_id)
    assert current["captures"] == original["captures"]
    assert len(current["reconstructions"]) == 2
    assert len(current["point_clouds"]) == 4

def test_empty_capture_session_is_rejected(configured):
    scanner, settings = configured
    record = scanner.store.create(settings, synthetic_calibration(), "synthetic", "explicit test")
    scanner.store.finish(record, "cancelled")
    with pytest.raises(ValueError, match="no captures"):
        scanner.start_reconstruction(record["session_id"])
