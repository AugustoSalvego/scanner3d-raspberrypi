"""Local scanner dashboard. Run one server process for each physical scanner."""
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
import cv2
from flask import Flask, Response, request, render_template, send_file
from scanner_core.calibration import get_calibration_status
from scanner_core.logger import get_logs
from scanner_core.pipeline import controller as default_controller, ScanBusy
from scanner_core.runtime_settings import get_settings, update_settings, toggle_simulation_mode, set_simulation_mode
from scanner_core.session import atomic_json, confined_path, timestamp
from scanner_core.simulation import render_frame
from scanner_core.viewer import get_viewer_status
from web_interface.api_response import api_success, api_error

def create_app(scan_controller=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    scanner = scan_controller or default_controller
    root = scanner.store.root

    def body(optional=False):
        if optional and not request.data:
            return {}
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object")
        return value

    def ply_path(relative):
        if not relative.endswith(".ply"):
            raise ValueError("A .ply filename is required")
        # Preserve original standalone filename routes, also allow session paths.
        key = relative if "/" in relative else "point_clouds/" + relative
        if not (key.startswith("point_clouds/") or key.startswith("scans/")):
            raise ValueError("Invalid point-cloud location")
        return confined_path(root, key)

    def point_cloud_files():
        result = []
        for base in (root / "point_clouds", root / "scans"):
            if not base.exists():
                continue
            for path in base.rglob("*.ply"):
                try:
                    key = path.relative_to(root).as_posix()
                    path = confined_path(root, key)
                    stat = path.stat()
                except (ValueError, OSError):
                    continue
                result.append({
                    "name": key, "size_kb": round(stat.st_size / 1024, 2),
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                    "download_url": "/download-ply/" + quote(key, safe="/"),
                    "delete_url": "/delete-ply/" + quote(key, safe="/"),
                })
        return sorted(result, key=lambda entry: entry["modified_at"], reverse=True)

    @app.errorhandler(ScanBusy)
    def busy(error):
        return api_error(str(error), status_code=409)

    @app.errorhandler(ValueError)
    def invalid(error):
        return api_error(str(error), status_code=400)

    @app.errorhandler(FileNotFoundError)
    def missing(error):
        return api_error("Requested file was not found", status_code=404)

    @app.errorhandler(RuntimeError)
    @app.errorhandler(OSError)
    def hardware_error(error):
        return api_error(str(error), status_code=503)

    @app.route("/")
    def home():
        return render_template("index.html")

    @app.route("/health")
    def health():
        return api_success("Scanner service is online", data={"status": "ok"}, status="ok")

    @app.route("/status")
    def status():
        data = scanner.status()
        return api_success(data={"status": data}, scanner_status=data)

    @app.route("/logs")
    def logs():
        data = get_logs()
        return api_success(data={"logs": data}, logs=data)

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        if request.method == "GET":
            data = scanner.settings_provider()
        else:
            with scanner.idle_operation():
                data, errors = update_settings(body())
                if errors:
                    return api_error("; ".join(errors), data={"errors": errors, "settings": data})
        return api_success(data={"settings": data}, settings=data)

    @app.route("/simulation-mode", methods=["GET", "POST"])
    def simulation_mode():
        if request.method == "POST":
            with scanner.idle_operation():
                data = body(optional=True)
                if set(data) - {"enabled"}:
                    raise ValueError("Only enabled is accepted")
                settings = set_simulation_mode(data["enabled"]) if "enabled" in data else toggle_simulation_mode()
        else:
            settings = scanner.settings_provider()
        enabled = settings["simulation_mode"]
        return api_success(data={"simulation_mode": enabled}, simulation_mode=enabled)

    @app.route("/start-scan", methods=["POST"])
    def start_scan():
        session_id = scanner.start()
        return api_success("Scan started", data={"session_id": session_id})

    @app.route("/stop-scan", methods=["POST"])
    def stop_scan():
        return api_success("Cancellation requested", data={"requested": scanner.stop()})

    @app.route("/generate-ply", methods=["POST"])
    def generate_ply():
        data = body(optional=True)
        if set(data) - {"session_id"}:
            raise ValueError("Only session_id is accepted; use the offline CLI for external manifests")
        session_id = data.get("session_id")
        if session_id is None:
            sessions = scanner.store.list()
            session_id = next((item["session_id"] for item in sessions if item.get("captures") and item.get("calibration")), None)
        if session_id is None:
            raise ValueError("No saved session with images and calibration is available")
        return api_success("Reconstruction started", data={"session_id": scanner.start_reconstruction(session_id)})

    @app.route("/scan-sessions")
    def scan_sessions():
        data = scanner.store.list()
        for item in data:
            item["metadata_url"] = f"/sessions/{item['session_id']}/files/metadata.json"
            report = item.get("reconstruction", {})
            preview = report.get("preview")
            if preview:
                try:
                    folder = scanner.store.folder(item["session_id"])
                    path = Path(preview)
                    relative = path.resolve().relative_to(folder).as_posix() if path.is_absolute() else path.as_posix()
                    confined_path(folder, relative)
                    item["preview_url"] = f"/sessions/{item['session_id']}/files/{quote(relative, safe='/')}"
                except (ValueError, OSError):
                    pass
        return api_success(data={"sessions": data}, sessions=data)

    @app.route("/sessions/<session_id>/files/<path:filename>")
    def session_file(session_id, filename):
        path = confined_path(scanner.store.folder(session_id), filename)
        if path.suffix.lower() not in (".json", ".png", ".jpg", ".jpeg", ".ply", ".txt"):
            raise ValueError("Unsupported file type")
        return send_file(path, as_attachment=request.args.get("download") == "1")

    @app.route("/capture", methods=["POST"])
    def capture_image():
        with scanner.idle_operation():
            settings = scanner.settings_provider()
            filename = "capture_manual_" + uuid.uuid4().hex + ".png"
            folder = root / "captures"
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / filename
            if settings["mode"] == "simulation":
                frame = render_frame(0, width=settings["camera"]["width"], height=settings["camera"]["height"])
                if not cv2.imwrite(str(target), frame):
                    raise RuntimeError("Could not write synthetic image")
                info = {"source": "synthetic", "resolution": [frame.shape[1], frame.shape[0]], "timestamp": timestamp()}
            elif settings["mode"] == "physical":
                scanner.camera.configure(**settings["camera"])
                info = scanner.camera.capture_to(target)
                info["source"] = "physical"
            else:
                raise ValueError("Offline mode does not acquire images")
            info.update(path=filename, status="captured", settings=settings)
            atomic_json(target.with_suffix(".json"), info)
        return api_success("Image captured", data={"file": filename, "capture": info}, file=filename)

    @app.route("/captures/<filename>")
    def capture_file(filename):
        return send_file(confined_path(root / "captures", filename))

    @app.route("/clear-captures", methods=["POST"])
    def clear_captures():
        with scanner.idle_operation():
            count = 0
            folder = root / "captures"
            if folder.exists():
                for entry in folder.iterdir():
                    if entry.suffix.lower() in (".png", ".jpg", ".jpeg", ".json") and entry.is_file():
                        confined_path(folder, entry.name).unlink()
                        count += 1
        return api_success(data={"deleted": count}, deleted=count)

    @app.route("/reset-camera", methods=["POST"])
    def reset_camera():
        with scanner.idle_operation():
            scanner.camera.release()
        return api_success("Camera released; next request will reopen it")

    @app.route("/camera/status")
    def camera_status():
        capability = scanner.camera.capabilities
        data = capability() if callable(capability) else capability
        return api_success(data={"camera": data})

    @app.route("/video")
    def video():
        settings = scanner.settings_provider()
        if settings["mode"] == "physical":
            with scanner.idle_operation():
                scanner.camera.configure(**settings["camera"])
        elif settings["mode"] == "offline":
            raise ValueError("Offline mode has no live preview")
        def frames():
            while True:
                current = scanner.settings_provider()
                if current["mode"] != settings["mode"]:
                    return
                if settings["mode"] == "simulation":
                    frame = render_frame(0, width=settings["camera"]["width"], height=settings["camera"]["height"])
                else:
                    try:
                        frame = scanner.camera.get_frame(preview=True)
                    except (RuntimeError, OSError):
                        return
                if frame is not None:
                    ok, image = cv2.imencode(".jpg", frame)
                    if not ok:
                        return
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + image.tobytes() + b"\r\n"
                time.sleep(0.1)
        return Response(frames(), mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.route("/point-clouds")
    def list_point_clouds():
        data = point_cloud_files()
        return api_success(data={"files": data}, files=data)

    @app.route("/download-ply")
    def download_ply():
        files = point_cloud_files()
        if not files:
            raise FileNotFoundError("No PLY")
        return send_file(ply_path(files[0]["name"]), as_attachment=True)

    @app.route("/download-ply/<path:filename>")
    def download_ply_file(filename):
        return send_file(ply_path(filename), as_attachment=True)

    @app.route("/delete-ply/<path:filename>", methods=["POST"])
    def delete_ply_file(filename):
        with scanner.idle_operation():
            ply_path(filename).unlink()
        return api_success("PLY deleted")

    @app.route("/calibration/status")
    def calibration_status():
        data = get_calibration_status(scanner.settings_provider()["calibration_path"])
        return api_success(data={"calibration": data}, calibration=data)

    @app.route("/viewer/status")
    def viewer_status():
        data = get_viewer_status()
        return api_success(data={"viewer": data}, viewer=data)

    @app.route("/api-info")
    def api_info():
        return api_success(data={"name": "Scanner 3D API", "version": scanner.status()["version"],
                                 "routes": sorted(str(rule) for rule in app.url_map.iter_rules())})
    return app

app = create_app()

if __name__ == "__main__":
    app.run(debug=False, use_reloader=False, host="0.0.0.0", port=5000, threaded=True)
