"""Software-only checks for the scan-to-reconstruction handoff."""
import importlib
import json

from scanner_core import session


def test_successful_physical_capture_starts_automatic_reconstruction(monkeypatch):
    server = importlib.import_module("web_interface.app")
    monkeypatch.setattr(server, "execute_scan", lambda: {
        "status": "finished", "session": "scan_example", "simulation": False,
    })
    calls = []
    monkeypatch.setattr(
        server, "_reconstruction_worker", lambda **kwargs: calls.append(kwargs)
    )
    assert server._scan_start_lock.acquire(blocking=False)
    assert server.hardware_gate.acquire(blocking=False)
    server._run_scan_exclusive()
    assert calls == [{"session_id": "scan_example", "release_gate": False}]
    assert server._scan_start_lock.acquire(blocking=False)
    server._scan_start_lock.release()
    assert server.hardware_gate.acquire(blocking=False)
    server.hardware_gate.release()


def test_interrupted_capture_does_not_start_reconstruction(monkeypatch):
    server = importlib.import_module("web_interface.app")
    monkeypatch.setattr(server, "execute_scan", lambda: {
        "status": "stopped", "session": "scan_example", "simulation": False,
    })
    calls = []
    monkeypatch.setattr(
        server, "_reconstruction_worker", lambda **kwargs: calls.append(kwargs)
    )
    assert server._scan_start_lock.acquire(blocking=False)
    assert server.hardware_gate.acquire(blocking=False)
    server._run_scan_exclusive()
    assert calls == []


def test_reconstruction_result_is_saved_with_the_right_session(monkeypatch, tmp_path):
    monkeypatch.setattr(session, "SCANS_FOLDER", str(tmp_path))
    created = session.create_scan_session()
    identifier = created["session_id"]
    session.finish_current_session()
    session.set_reconstruction_status(identifier, "running")
    session.set_reconstruction_status(
        identifier, "finished", filename="real_" + identifier + ".ply"
    )
    session.set_reconstruction_status(
        identifier, "finished", filename="real_" + identifier + ".ply"
    )
    metadata = json.loads(
        (tmp_path / identifier / "metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["reconstruction_status"] == "finished"
    assert metadata["reconstruction_error"] is None
    assert metadata["point_clouds"] == ["real_" + identifier + ".ply"]
