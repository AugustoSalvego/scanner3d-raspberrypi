"""Regression tests for safe persistent scan-session bookkeeping."""
import json
from pathlib import Path

import pytest

from scanner_core import session


def test_unique_session_directories_even_with_same_clock(monkeypatch, tmp_path):
    monkeypatch.setattr(session, "SCANS_FOLDER", str(tmp_path))
    first = session.create_scan_session()
    second = session.create_scan_session()
    assert first["session_id"] != second["session_id"]
    assert Path(first["session_path"]).is_dir()
    assert Path(second["session_path"]).is_dir()
    for item in (first, second):
        meta = json.loads((Path(item["session_path"]) / "metadata.json").read_text())
        assert meta["session_id"] == item["session_id"]
        assert meta["created_at_iso"]
        assert "display_name" in meta


def test_atomic_metadata_update_preserves_existing_file_on_failure(
    monkeypatch, tmp_path
):
    metadata_path = tmp_path / "metadata.json"
    session.save_metadata(str(tmp_path), {"status": "running"})
    original = metadata_path.read_bytes()

    def fail_replace(*args, **kwargs):
        raise OSError("Simulated interruption")

    monkeypatch.setattr(session.os, "replace", fail_replace)
    with pytest.raises(OSError, match="Simulated interruption"):
        session.save_metadata(str(tmp_path), {"status": "finished"})
    assert metadata_path.read_bytes() == original
    assert not list(tmp_path.glob(".metadata_*.tmp"))


def test_session_listing_survives_a_damaged_metadata_file(monkeypatch, tmp_path):
    monkeypatch.setattr(session, "SCANS_FOLDER", str(tmp_path))
    good = session.create_scan_session()
    bad = tmp_path / "scan_invalid"
    bad.mkdir()
    (bad / "metadata.json").write_text("{broken json")
    result = session.list_scan_sessions()
    assert [x["session_id"] for x in result] == [good["session_id"]]
