"""Flask endpoint tests, including the file-serving security rules."""

from __future__ import annotations

import json

import numpy as np
import pytest

from scanner_core import paths
from scanner_core.point_cloud import PointCloud, write_ply


@pytest.fixture
def client(isolated_outputs, monkeypatch):
    """A test client on a fresh app bound to the temporary output tree."""

    from scanner_core.config import config_store
    from scanner_core.state import state_store
    from web_interface.app import create_app

    config_store.replace(config_store.get().__class__().validate())
    state_store.reset()

    app = create_app()
    app.config.update(TESTING=True)

    with app.test_client() as test_client:
        yield test_client


def payload(response):
    return json.loads(response.data)


def data(response):
    body = payload(response)

    assert body["success"] is True, body.get("message")

    return body["data"]


class TestEnvelope:
    @pytest.mark.parametrize(
        "url",
        [
            "/api/health",
            "/api/status",
            "/api/logs",
            "/api/info",
            "/api/settings",
            "/api/settings/mode",
            "/api/calibration",
            "/api/point-clouds",
            "/api/scan/sessions",
            "/api/camera/captures",
            "/api/viewer/status",
        ],
    )
    def test_every_get_returns_the_same_envelope(self, client, url):
        response = client.get(url)

        assert response.status_code == 200

        body = payload(response)

        assert set(body) == {"success", "message", "data"}
        assert body["success"] is True
        assert isinstance(body["data"], dict)

    def test_unknown_route_is_json_not_html(self, client):
        response = client.get("/api/not-a-route")

        assert response.status_code == 404

        body = payload(response)

        assert body["success"] is False


class TestStatus:
    def test_reports_components_separately(self, client):
        status = data(client.get("/api/status"))

        assert set(status) >= {
            "service",
            "mode",
            "camera",
            "motor",
            "laser",
            "calibration",
            "scan",
            "badge",
        }

    def test_health_is_not_just_flask_answering(self, client):
        health = data(client.get("/api/health"))

        assert "calibration" in health
        assert "camera" in health
        assert health["calibration"]["ready_for_physical_scan"] is False

    def test_logs_have_structure(self, client):
        logs = data(client.get("/api/logs"))["logs"]

        assert isinstance(logs, list)

        if logs:
            assert set(logs[0]) >= {"time", "level", "message"}


class TestSettings:
    def test_patch_updates_a_value(self, client):
        response = client.patch("/api/settings", json={"scan": {"capture_count": 42}})

        assert response.status_code == 200
        assert data(response)["settings"]["scan"]["capture_count"] == 42

    def test_invalid_patch_is_rejected_with_422(self, client):
        before = data(client.get("/api/settings"))["settings"]["scan"]["capture_count"]

        response = client.patch("/api/settings", json={"scan": {"capture_count": 0}})

        assert response.status_code == 422
        assert payload(response)["success"] is False

        after = data(client.get("/api/settings"))["settings"]["scan"]["capture_count"]

        assert after == before

    def test_unknown_setting_is_rejected(self, client):
        response = client.patch("/api/settings", json={"scan": {"nonsense": 1}})

        assert response.status_code == 422

    def test_non_object_body_is_rejected(self, client):
        response = client.patch("/api/settings", json=[1, 2, 3])

        assert response.status_code == 400

    def test_mode_can_be_changed(self, client):
        response = client.post("/api/settings/mode", json={"mode": "offline"})

        assert response.status_code == 200
        assert data(response)["mode"] == "offline"
        assert data(client.get("/api/status"))["mode"] == "offline"

    def test_unknown_mode_is_rejected(self, client):
        response = client.post("/api/settings/mode", json={"mode": "telepathy"})

        assert response.status_code == 422

    def test_missing_mode_is_rejected(self, client):
        assert client.post("/api/settings/mode", json={}).status_code == 400


class TestScanEndpoints:
    def test_stop_without_a_scan_is_a_conflict(self, client):
        response = client.post("/api/scan/stop")

        assert response.status_code == 409

    def test_offline_mode_refuses_to_start(self, client):
        client.post("/api/settings/mode", json={"mode": "offline"})

        response = client.post("/api/scan/start")

        assert response.status_code == 422
        assert "offline" in payload(response)["message"].lower()

    def test_sessions_list_is_empty_initially(self, client):
        assert data(client.get("/api/scan/sessions"))["sessions"] == []

    def test_unknown_session_is_404(self, client):
        response = client.get("/api/scan/sessions/scan_nope")

        assert response.status_code == 404

    def test_session_id_traversal_is_refused(self, client):
        response = client.get("/api/scan/sessions/..")

        assert response.status_code in (400, 404)

    def test_reconstructing_an_unknown_session_is_404(self, client):
        response = client.post("/api/scan/sessions/scan_nope/reconstruct")

        assert response.status_code == 404


class TestPointCloudEndpoints:
    def _write_cloud(self, name="test.ply", count=500):
        rng = np.random.default_rng(9)

        cloud = PointCloud(
            points=rng.normal(0, 25, (count, 3)),
            colors=rng.integers(0, 256, (count, 3)).astype(np.uint8),
        )

        return write_ply(paths.POINT_CLOUDS_DIR / name, cloud)

    def test_listing_includes_a_written_cloud(self, client):
        self._write_cloud()

        files = data(client.get("/api/point-clouds"))["files"]

        assert any(item["name"] == "test.ply" for item in files)
        assert files[0]["session_id"] is None

    def test_download(self, client):
        self._write_cloud()

        response = client.get("/download/point-cloud/test.ply")

        assert response.status_code == 200
        assert response.data.startswith(b"ply")

    def test_delete(self, client):
        path = self._write_cloud()

        assert client.delete("/api/point-clouds/test.ply").status_code == 200
        assert not path.exists()

    def test_delete_missing_is_404(self, client):
        assert client.delete("/api/point-clouds/absent.ply").status_code == 404

    def test_binary_viewer_feed(self, client):
        self._write_cloud(count=800)

        response = client.get("/api/point-clouds/data?file=test.ply")

        assert response.status_code == 200
        assert response.mimetype == "application/octet-stream"

        buffer = response.data
        count = int.from_bytes(buffer[0:4], "little")
        flags = int.from_bytes(buffer[4:8], "little")

        assert count == 800
        assert flags & 1  # colours present
        assert len(buffer) == 8 + count * 12 + count * 3
        assert response.headers["X-Point-Count"] == "800"

    def test_viewer_feed_decimates_large_clouds(self, client):
        self._write_cloud(count=5000)

        response = client.get("/api/point-clouds/data?file=test.ply&max_points=1000")

        count = int.from_bytes(response.data[0:4], "little")

        assert count <= 1000
        assert response.headers["X-Point-Count-Total"] == "5000"

    def test_viewer_feed_requires_a_file(self, client):
        assert client.get("/api/point-clouds/data").status_code == 400

    def test_viewer_feed_missing_file_is_404(self, client):
        assert client.get("/api/point-clouds/data?file=absent.ply").status_code == 404

    def test_download_without_a_scan_is_404(self, client):
        assert client.get("/download/point-cloud").status_code == 404


class TestFileSecurity:
    """The old /captures/<filename> route had no validation at all."""

    @pytest.mark.parametrize(
        "path",
        [
            "/captures/../../../etc/passwd",
            "/captures/..%2f..%2fsecret.jpg",
            "/captures/%2e%2e%2fsecret.jpg",
            "/captures/a/b/c/d.jpg",
            "/download/point-cloud/../../secret.ply",
            "/download/point-cloud/..%5c..%5csecret.ply",
            "/diagnostics/../../secret/x.jpg",
        ],
    )
    def test_traversal_attempts_never_succeed(self, client, path):
        response = client.get(path)

        assert response.status_code in (400, 404)
        assert b"root:" not in response.data

    @pytest.mark.parametrize(
        "path",
        ["/captures/notes.txt", "/download/point-cloud/notes.txt", "/diagnostics/run/x.exe"],
    )
    def test_only_expected_extensions_are_served(self, client, path):
        response = client.get(path)

        assert response.status_code in (400, 404)

    def test_a_real_capture_is_served(self, client):
        import cv2

        image = np.zeros((16, 16, 3), dtype=np.uint8)
        cv2.imwrite(str(paths.CAPTURES_DIR / "frame.jpg"), image)

        response = client.get("/captures/frame.jpg")

        assert response.status_code == 200
        assert response.mimetype == "image/jpeg"

    def test_deleting_outside_the_root_is_refused(self, client):
        response = client.delete("/api/point-clouds/..%2f..%2fsecret.ply")

        assert response.status_code in (400, 404)


class TestCalibrationEndpoints:
    def test_status_reports_missing_calibration(self, client):
        calibration = data(client.get("/api/calibration"))["calibration"]

        assert calibration["ready_for_physical_scan"] is False
        assert calibration["camera"]["present"] is False
        assert calibration["blocking_issues"]

    def test_camera_calibration_needs_images(self, client):
        response = client.post("/api/calibration/camera", json={"folder": "camera"})

        assert response.status_code in (400, 422)
        assert payload(response)["success"] is False

    def test_laser_calibration_needs_the_camera_first(self, client):
        response = client.post("/api/calibration/laser", json={})

        assert response.status_code == 422
        assert "camera calibration" in payload(response)["message"].lower()

    def test_manual_turntable_requires_all_fields(self, client):
        response = client.post("/api/calibration/turntable/manual", json={"axis_origin": [0, 0, 1]})

        assert response.status_code == 400

    def test_manual_turntable_rejects_a_degenerate_axis(self, client):
        """An origin at the camera centre cannot be right."""

        response = client.post(
            "/api/calibration/turntable/manual",
            json={
                "axis_origin": [0, 0, 0],
                "axis_direction": [0, -1, 0],
                "steps_per_revolution": 4096,
            },
        )

        assert response.status_code == 422

    def test_manual_turntable_accepts_measured_values(self, client):
        response = client.post(
            "/api/calibration/turntable/manual",
            json={
                "axis_origin": [0, 60, 320],
                "axis_direction": [0, -1, 0],
                "steps_per_revolution": 4096,
                "rotation_direction": 1,
            },
        )

        assert response.status_code == 201

        calibration = data(client.get("/api/calibration"))["calibration"]

        assert calibration["turntable"]["valid"] is True
        # Camera and laser are still missing, so scanning stays blocked.
        assert calibration["ready_for_physical_scan"] is False

    def test_a_calibration_file_that_merely_exists_is_not_valid(self, client):
        """The exact bug in the old store: trusting \"calibrated\": true."""

        paths.CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)

        (paths.CALIBRATION_DIR / "camera_calibration.json").write_text(
            json.dumps({"calibrated": True, "camera_matrix": None}), encoding="utf-8"
        )

        calibration = data(client.get("/api/calibration"))["calibration"]

        assert calibration["camera"]["valid"] is False
        assert calibration["ready_for_physical_scan"] is False


class TestCameraEndpoints:
    def test_captures_listing_starts_empty(self, client):
        assert data(client.get("/api/camera/captures"))["captures"] == []

    def test_clear_captures_on_an_empty_folder(self, client):
        assert data(client.post("/api/camera/captures/clear"))["deleted"] == 0

    def test_capture_in_simulation_mode(self, client):
        response = client.post("/api/camera/capture")

        assert response.status_code == 201

        body = data(response)

        assert body["file"]["name"].endswith(".jpg")
        assert client.get(body["url"]).status_code == 200

    def test_reset_releases_the_hardware(self, client):
        assert client.post("/api/camera/reset").status_code == 200


class TestFullScanThroughTheApi:
    def test_scan_and_view(self, client):
        client.patch(
            "/api/settings",
            json={
                "scan": {"capture_count": 6, "settle_delay": 0.0, "capture_delay": 0.0},
                "simulation": {"image_width": 320, "image_height": 240, "focal_length_px": 350.0},
                "detector": {"min_points": 10},
            },
        )

        response = client.post("/api/scan/start")

        assert response.status_code == 201

        session_id = data(response)["session_id"]

        # The scan runs in a background thread; wait for it to settle.
        for _ in range(600):
            status = data(client.get("/api/status"))["scan"]

            if not status["running"] and status["phase"] in {"completed", "error", "cancelled"}:
                break

            import time

            time.sleep(0.05)

        assert status["phase"] == "completed", status.get("last_error")

        session = data(client.get(f"/api/scan/sessions/{session_id}"))["session"]

        assert session["status"] == "completed"
        assert len(session["captures"]) == 6
        assert len(session["point_clouds"]) == 1

        cloud = session["point_clouds"][0]

        assert cloud["point_count"] > 100

        # The cloud is listed, downloadable and viewable.
        files = data(client.get("/api/point-clouds"))["files"]

        assert any(item["session_id"] == session_id for item in files)

        assert client.get(cloud["download_url"]).status_code == 200

        viewer = client.get(
            f"/api/point-clouds/data?file={cloud['filename']}&session={session_id}"
        )

        assert viewer.status_code == 200
        assert int(viewer.headers["X-Point-Count"]) == cloud["point_count"]

        # And the frames are served.
        assert client.get(session["captures"][0]["url"]).status_code == 200

    def test_session_can_be_deleted(self, client):
        client.patch(
            "/api/settings",
            json={
                "scan": {"capture_count": 4, "settle_delay": 0.0, "capture_delay": 0.0},
                "simulation": {"image_width": 320, "image_height": 240, "focal_length_px": 350.0},
                "detector": {"min_points": 10},
            },
        )

        session_id = data(client.post("/api/scan/start"))["session_id"]

        import time

        for _ in range(600):
            if not data(client.get("/api/status"))["scan"]["running"]:
                break

            time.sleep(0.05)

        assert client.delete(f"/api/scan/sessions/{session_id}").status_code == 200
        assert client.get(f"/api/scan/sessions/{session_id}").status_code == 404
