"""Exclusive acquisition/reconstruction lifecycle; hardware is opened only on demand."""
import copy
import threading
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path
import cv2
from scanner_core.calibration import load_calibration, validate_calibration
from scanner_core.camera import get_camera
from scanner_core.config import APP_VERSION
from scanner_core.logger import add_log
from scanner_core.motor import StepperMotor, SimulatedMotor, capture_positions
from scanner_core.point_cloud import reconstruct_session
from scanner_core.runtime_settings import get_settings, validate_settings
from scanner_core.session import SessionStore, OutputLock, _default_store, timestamp
from scanner_core.simulation import synthetic_calibration, render_frame

class ScanBusy(RuntimeError):
    pass

class ScanCancelled(RuntimeError):
    pass

class ScanController:
    def __init__(self, store=None, settings_provider=get_settings, camera=None,
                 motor_factory=None, reconstruct=reconstruct_session):
        self.store = store if store is not None else SessionStore()
        self.settings_provider = settings_provider
        self.camera = camera if camera is not None else get_camera()
        self.motor_factory = motor_factory
        self.reconstruct = reconstruct
        self.lock = threading.RLock()
        self.output_lock = OutputLock(self.store.root)
        self.thread = None
        self.cancel_event = threading.Event()
        self.state = {
            "version": APP_VERSION, "running": False, "phase": "idle",
            "motor_position": 0, "frames_captured": 0, "last_capture": None,
            "point_cloud_generated": False, "last_point_cloud": None, "last_error": None,
            "current_session": None, "last_session": None, "last_scan_time": None,
        }

    def status(self):
        with self.lock:
            state = copy.deepcopy(self.state)
            settings = self.settings_provider()
            state.update({key: settings[key] for key in ("scan_steps", "step_delay", "capture_delay", "simulation_mode", "mode")})
            state["status_badge"] = state["phase"].upper()
            return state

    @contextmanager
    def idle_operation(self):
        """Serialize reset/settings/delete/manual capture with scan reservation."""
        with self.lock:
            if self.state["running"]:
                raise ScanBusy("A scan is active; wait for it to finish or cancel")
            try:
                self.output_lock.acquire()
            except RuntimeError as error:
                raise ScanBusy(str(error)) from error
            try:
                yield
            finally:
                self.output_lock.release()

    def _reserve(self, record, worker):
        if self.state["running"]:
            raise ScanBusy("A scan is already active")
        self.cancel_event = threading.Event()
        self.state.update(
            running=True, phase=record["status"], frames_captured=0, motor_position=0,
            last_capture=None, point_cloud_generated=False, last_point_cloud=None,
            last_error=None, current_session=record["session_id"], last_session=record["session_id"],
        )
        thread = threading.Thread(target=worker, args=(record, self.cancel_event), daemon=False,
                                  name=record["session_id"])
        self.thread = thread
        try:
            thread.start()
        except BaseException:
            self.state.update(running=False, phase="error", current_session=None)
            self.store.finish(record, "error", "Worker could not start")
            self.output_lock.release()
            raise
        return record["session_id"]

    def start(self):
        with self.lock:
            if self.state["running"]:
                raise ScanBusy("A scan is already active")
            settings = validate_settings(self.settings_provider())
            if settings["mode"] == "offline":
                raise ValueError("Offline mode requires an existing session or an explicit manifest")
            if settings["mode"] == "simulation":
                calibration = validate_calibration(synthetic_calibration(
                    settings["camera"]["width"], settings["camera"]["height"]))
                source = "synthetic"
            else:
                calibration_path = Path(settings["calibration_path"]).resolve()
                if self.store.root not in calibration_path.parents:
                    raise ValueError("Physical calibration must be inside the authorized output folder")
                calibration = load_calibration(calibration_path, allow_synthetic=False,
                    expected_resolution=[settings["camera"]["width"], settings["camera"]["height"]])
                source = "physical"
            try:
                self.output_lock.acquire()
            except RuntimeError as error:
                raise ScanBusy(str(error)) from error
            try:
                record = self.store.create(settings, calibration, source,
                    "commanded_half_steps_open_loop; relative software zero, no position sensor")
                return self._reserve(record, self._run_new)
            except BaseException:
                self.output_lock.release()
                raise

    def start_reconstruction(self, session_id):
        with self.lock:
            if self.state["running"]:
                raise ScanBusy("A scan is already active")
            record = self.store.load(session_id)
            calibration = validate_calibration(record["calibration"])
            if not record.get("captures"):
                raise ValueError("Session contains no captures; an explicit image manifest is required")
            record["calibration"] = calibration
            try:
                self.output_lock.acquire()
            except RuntimeError as error:
                raise ScanBusy(str(error)) from error
            self.store.current_session = record
            result = self._reserve(record, self._run_existing)
            self.state["phase"] = "processing"
            return result

    def _check_cancel(self, event):
        if event.is_set():
            raise ScanCancelled("Scan cancelled")

    def _wait(self, seconds, event):
        if event.wait(seconds):
            raise ScanCancelled("Scan cancelled")

    def _set(self, **values):
        with self.lock:
            self.state.update(values)

    def _make_motor(self, settings):
        if self.motor_factory:
            return self.motor_factory(settings)
        if settings["mode"] == "simulation":
            return SimulatedMotor(direction=settings["direction"])
        return StepperMotor(pins=settings["motor_pins"], step_delay=settings["motor_step_delay"],
                            direction=settings["direction"])

    def _acquire(self, record, event):
        settings = record["settings"]
        folder = self.store.folder(record["session_id"])
        physical = record["source"] == "physical"
        motor = None
        with self.camera.exclusive() if physical else nullcontext():
            try:
                if physical:
                    self.camera.configure(**settings["camera"])
                motor = self._make_motor(settings)
                prepare = getattr(motor, 'prepare', None)
                if prepare is not None:
                    prepare(cancel_event=event)
                previous = 0
                for index, position in enumerate(capture_positions(settings["scan_steps"], settings["steps_per_revolution"])):
                    self._check_cancel(event)
                    motor.move(position - previous, cancel_event=event)
                    previous = position
                    self._set(motor_position=motor.position)
                    self._wait(settings["step_delay"], event)
                    angle = motor.position * 360.0 / settings["steps_per_revolution"]
                    capture_id = f"capture_{index:05d}"
                    relative = f"captures/{capture_id}.png"
                    capture = {
                        "id": capture_id, "path": relative, "index": index,
                        "motor_position": motor.position, "angle_deg": angle,
                        "angle_source": record["angle_source"], "timestamp": timestamp(),
                        "resolution": None, "status": "pending",
                    }
                    try:
                        if physical:
                            details = self.camera.capture_to(folder / relative, cancel_event=event)
                            capture.update({key: value for key, value in details.items() if key != "path"})
                        else:
                            frame = render_frame(angle, index=index, width=settings["camera"]["width"],
                                                 height=settings["camera"]["height"])
                            if not cv2.imwrite(str(folder / relative), frame):
                                raise RuntimeError("Synthetic image could not be saved")
                            capture.update(resolution=[frame.shape[1], frame.shape[0]], capture_result="success")
                        capture["status"] = "captured"
                    except Exception as error:
                        capture.update(status="cancelled" if event.is_set() else "failed", error=str(error))
                        raise
                    finally:
                        record["captures"].append(capture)
                        self.store.save(record)
                    self._set(frames_captured=index + 1, last_capture=relative)
                    self._wait(settings["capture_delay"], event)
                if physical:
                    capabilities = getattr(self.camera, "capabilities", None)
                    if callable(capabilities):
                        record["camera_capabilities"] = capabilities()
                    elif isinstance(capabilities, dict):
                        record["camera_capabilities"] = capabilities
                    self.store.save(record)
            finally:
                if motor is not None:
                    motor.close()

    def _process(self, record, event):
        self._check_cancel(event)
        self._set(phase="processing")
        folder = self.store.folder(record["session_id"])
        report = self.reconstruct(folder, record["calibration"], settings=record["settings"], cancel_event=event)
        self._check_cancel(event)
        record["reconstruction"] = report
        record.setdefault("reconstructions", []).append(report)
        for key in ("raw_ply", "filtered_ply"):
            path = Path(report[key])
            relative = path.resolve().relative_to(folder.resolve()).as_posix() if path.is_absolute() else path.as_posix()
            if relative not in record["point_clouds"]:
                record["point_clouds"].append(relative)
        self.store.save(record)
        filtered = Path(report["filtered_ply"])
        if not filtered.is_absolute():
            filtered = folder / filtered
        self._set(point_cloud_generated=True, last_point_cloud=str(filtered), last_scan_time=timestamp())
        return report

    def _run_new(self, record, event):
        terminal, error = "error", None
        try:
            add_log(f"Scan started: {record['session_id']} ({record['source']})")
            self._acquire(record, event)
            self._check_cancel(event)
            if not record["captures"]:
                raise ValueError("No images captured")
            record["status"] = "processing"
            self.store.save(record)
            self._process(record, event)
            terminal = "completed"
        except Exception as exception:
            terminal = "cancelled" if event.is_set() else "error"
            error = str(exception)
            add_log(f"Scan {terminal}: {error}")
        finally:
            try:
                self.store.finish(record, terminal, error)
            except Exception as exception:
                terminal, error = "error", f"Could not persist terminal metadata: {exception}"
            finally:
                with self.lock:
                    self.state.update(running=False, phase=terminal, current_session=None, last_error=error)
                    if terminal != "completed":
                        self.state["point_cloud_generated"] = False
                    self.output_lock.release()
                add_log(f"Session {record['session_id']}: {terminal}")

    def _run_existing(self, record, event):
        terminal, error = "error", None
        try:
            self._process(record, event)
            terminal = "completed"
        except Exception as exception:
            terminal = "cancelled" if event.is_set() else "error"
            error = str(exception)
        finally:
            with self.lock:
                try:
                    record["last_reconstruction_status"] = terminal
                    record["last_reconstruction_error"] = error
                    self.store.save(record)
                except Exception as exception:
                    terminal, error = "error", str(exception)
                finally:
                    self.store.current_session = None
                    self.state.update(running=False, phase=terminal, current_session=None, last_error=error)
                    if terminal != "completed":
                        self.state["point_cloud_generated"] = False
                    self.output_lock.release()

    def stop(self):
        with self.lock:
            if self.state["running"]:
                self.cancel_event.set()
                self.state["phase"] = "cancelling"
                add_log("Cancellation requested; waiting for worker cleanup")
                return True
            return False

    def wait(self, timeout=None):
        with self.lock:
            thread = self.thread
        if thread:
            thread.join(timeout)
        return self.status()

controller = ScanController(store=_default_store)

def execute_scan():
    controller.start()
    return controller.wait()

def stop():
    return controller.stop()
