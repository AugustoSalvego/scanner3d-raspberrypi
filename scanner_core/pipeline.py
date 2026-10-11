"""Scan orchestration.

``ScanController`` owns the whole acquisition lifecycle and is the only place
allowed to start a scan.  Concurrency is handled with a non-blocking mutex, so
two simultaneous ``POST /start-scan`` requests cannot open two sessions: the
second one loses the race and is told the scanner is busy.

The acquisition loop is deliberately boring:

    move to an absolute target -> let the platform settle -> flush the camera
    buffer -> capture -> record where the platform actually was -> repeat

Reconstruction then runs from the frames on disk, which keeps memory flat no
matter how many captures a scan has - important on a Raspberry Pi 3.

The ``finally`` block always releases the motor coils, switches a
software-controlled laser off, and persists the session metadata.  The camera is
intentionally left open so the live preview survives the end of a scan.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import cv2

from scanner_core import logger, paths
from scanner_core.calibration.auto import (
    DEFAULT_STOPS as AUTO_CALIBRATION_STOPS,
    DIAGNOSTICS_RUN as AUTO_CALIBRATION_DIAGNOSTICS,
    STEPS as AUTO_CALIBRATION_STEPS,
    AutoCalibrationReport,
    capture_step,
    compute_auto_calibration,
    load_run,
    reset_run,
    run_directory,
    summarize_run,
)
from scanner_core.calibration.camera import CheckerboardSpec
from scanner_core.calibration.store import CalibrationStore, calibration_store
from scanner_core.config import ConfigStore, ScannerConfig, ScannerMode, config_store
from scanner_core.errors import (
    CalibrationError,
    ConfigurationError,
    ReconstructionError,
    ScanAlreadyRunning,
    ScanCancelled,
    ScannerError,
    SessionError,
)
from scanner_core.hardware.factory import HardwareSet, build_hardware
from scanner_core.laser_detection import LaserLineDetector
from scanner_core.point_cloud import write_ply
from scanner_core.reconstruction import (
    CaptureInput,
    ReconstructionResult,
    Reconstructor,
    captures_from_session_metadata,
)
from scanner_core.scan_plan import closing_steps, plan_capture_positions
from scanner_core.session import (
    CaptureRecord,
    PointCloudRecord,
    ScanSession,
    SessionManager,
    SessionStatus,
    session_manager,
)
from scanner_core.state import ScanPhase, StateStore, state_store


@dataclass
class ScanOutcome:
    """What a finished scan produced."""

    session_id: str
    status: SessionStatus
    point_cloud_path: Path | None = None
    point_count: int = 0
    statistics: dict[str, Any] | None = None
    message: str = ""


class ScanController:
    """Runs scans, reconstructions and single captures."""

    def __init__(
        self,
        *,
        configuration: ConfigStore | None = None,
        state: StateStore | None = None,
        sessions: SessionManager | None = None,
        calibration: CalibrationStore | None = None,
    ) -> None:
        self._config_store = configuration or config_store
        self._state = state or state_store
        self._sessions = sessions or session_manager
        self._calibration = calibration or calibration_store

        # Non-blocking: a losing caller is told the scanner is busy instead of
        # queueing up behind a scan that may take minutes.
        self._scan_mutex = threading.Lock()
        self._cancel_event = threading.Event()
        self._hardware_lock = threading.RLock()
        self._hardware: HardwareSet | None = None
        self._hardware_mode: ScannerMode | None = None
        self._hardware_signature: str | None = None

        # The automatic calibration shares the scan mutex, the cancel event and
        # the devices: only one of them can drive the motor at a time.
        self.auto_calibration_stops = AUTO_CALIBRATION_STOPS
        self._auto_lock = threading.Lock()
        self._auto_progress: dict[str, Any] = {
            "running": False,
            "step": None,
            "done": 0,
            "total": 0,
            "board_found": 0,
            "message": None,
            "error": None,
        }

    # ------------------------------------------------------------------
    # Hardware lifecycle
    # ------------------------------------------------------------------
    @staticmethod
    def hardware_signature(config: ScannerConfig) -> str:
        """Everything the devices are built from.

        The cached hardware set is only reused while this is unchanged.  Keying
        the cache on the mode alone meant a new resolution, motor setting or
        simulated object was silently ignored until the server restarted.
        """

        plain = config.to_dict()

        return json.dumps(
            {
                "mode": plain["mode"],
                "camera": plain["camera"],
                "motor": plain["motor"],
                "laser": plain["laser"],
                "simulation": plain["simulation"],
            },
            sort_keys=True,
        )

    def _ensure_hardware(self, config: ScannerConfig) -> HardwareSet:
        """Return the hardware set for ``config``, building it if needed.

        The set is cached so the live preview keeps one camera handle across
        scans instead of reopening the device every time, but it is rebuilt as
        soon as any setting it was built from changes.
        """

        signature = self.hardware_signature(config)

        with self._hardware_lock:
            if self._hardware is not None and self._hardware_signature == signature:
                return self._hardware

            self.release_hardware()

            hardware = build_hardware(config, store=self._calibration)

            self._hardware = hardware
            self._hardware_mode = config.mode
            self._hardware_signature = signature

            return hardware

    def _current_or_new_hardware(self, config: ScannerConfig) -> HardwareSet:
        """Hardware for a *reader* such as the live preview.

        While a scan or a calibration owns the devices they must never be
        rebuilt underneath it - that would close the motor mid-move - so the
        running set is returned as is.  Otherwise this is ``_ensure_hardware``.
        """

        with self._hardware_lock:
            if self.is_running and self._hardware is not None:
                return self._hardware

        return self._ensure_hardware(config)

    def release_hardware(self) -> None:
        """Close every device.  Used on mode change and at shutdown."""

        with self._hardware_lock:
            if self._hardware is not None:
                self._hardware.close()

            self._hardware = None
            self._hardware_mode = None
            self._hardware_signature = None

    def preview_frame(self):
        """One frame for the MJPEG stream, or ``None`` when unavailable."""

        config = self._config_store.get()

        if config.mode is ScannerMode.OFFLINE:
            return None

        try:
            hardware = self._current_or_new_hardware(config)
        except ScannerError as error:
            logger.debug(f"Preview unavailable: {error}")

            return None

        try:
            if not hardware.camera.is_open:
                hardware.camera.open()

            return hardware.camera.read()
        except ScannerError as error:
            logger.debug(f"Preview frame failed: {error}")

            return None

    def hardware_status(self) -> dict[str, Any]:
        """Device availability for the health endpoint, without opening anything."""

        config = self._config_store.get()

        with self._hardware_lock:
            hardware = self._hardware

        if hardware is None or self._hardware_signature != self.hardware_signature(config):
            return {
                "mode": config.mode.value,
                "camera": {"configured": True, "available": False, "open": False},
                "motor": {
                    "configured": config.mode is not ScannerMode.OFFLINE,
                    "available": False,
                    "open": False,
                },
                "laser": {
                    "configured": True,
                    "controllable": config.laser.is_software_controlled,
                    "open": False,
                },
            }

        camera = hardware.camera.describe()
        motor = hardware.motor.describe()
        laser = hardware.laser.describe()

        return {
            "mode": config.mode.value,
            "camera": {
                "configured": True,
                "available": bool(camera.get("open")),
                "open": bool(camera.get("open")),
                **camera,
            },
            "motor": {
                "configured": True,
                "available": bool(motor.get("open")),
                **motor,
            },
            "laser": {"configured": True, **laser},
        }

    # ------------------------------------------------------------------
    # Single capture
    # ------------------------------------------------------------------
    def capture_still(self) -> Path:
        """Take one frame and store it under ``outputs/captures``."""

        config = self._config_store.get()

        if config.mode is ScannerMode.OFFLINE:
            raise ConfigurationError(
                "Offline mode does not drive the camera. Switch to simulation or "
                "physical mode to capture."
            )

        if self.is_running:
            raise ScanAlreadyRunning(
                "A scan or calibration is running; wait for it to finish before capturing."
            )

        hardware = self._ensure_hardware(config)

        if not hardware.camera.is_open:
            hardware.camera.open()

        frame = hardware.camera.capture()

        if frame is None:
            raise ScannerError("The camera did not return a frame.")

        paths.CAPTURES_DIR.mkdir(parents=True, exist_ok=True)

        filename = f"capture_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]}.jpg"
        target = paths.CAPTURES_DIR / filename

        if not cv2.imwrite(str(target), frame):
            raise ScannerError(f"The captured frame could not be written to {filename}.")

        self._state.update(last_capture=filename)

        logger.info(f"Captured {filename}.")

        return target

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._scan_mutex.locked()

    def request_stop(self) -> bool:
        """Ask a running scan to stop.  Returns ``False`` when idle."""

        if not self.is_running:
            return False

        self._cancel_event.set()

        logger.warning("Stop requested by the operator.")

        return True

    def start_scan_async(self) -> str:
        """Start a scan in a background thread and return the session id.

        The session is created synchronously so the caller immediately knows
        which session to follow.
        """

        if not self._scan_mutex.acquire(blocking=False):
            raise ScanAlreadyRunning("A scan is already running.")

        try:
            config = self._config_store.get()
            session = self._prepare_scan(config)
        except BaseException:
            self._scan_mutex.release()
            raise

        thread = threading.Thread(
            target=self._run_scan_locked,
            args=(config, session),
            name=f"scan-{session.session_id}",
            daemon=True,
        )
        thread.start()

        return session.session_id

    def run_scan(self) -> ScanOutcome:
        """Run a scan synchronously.  Used by the CLI and the tests."""

        if not self._scan_mutex.acquire(blocking=False):
            raise ScanAlreadyRunning("A scan is already running.")

        try:
            config = self._config_store.get()
            session = self._prepare_scan(config)
        except BaseException:
            self._scan_mutex.release()
            raise

        return self._run_scan_locked(config, session)

    # ------------------------------------------------------------------
    def _prepare_scan(self, config: ScannerConfig) -> ScanSession:
        """Validate everything and create the session, before any hardware moves."""

        if config.mode is ScannerMode.OFFLINE:
            raise ConfigurationError(
                "Offline mode cannot acquire new scans. Switch to simulation or "
                "physical mode, or reconstruct an existing session instead."
            )

        hardware = self._ensure_hardware(config)

        allow_synthetic = config.mode is ScannerMode.SIMULATION

        if config.mode is ScannerMode.PHYSICAL:
            # Always scan with the calibration that is on disk *now*.  The
            # cached hardware set may have been built before the operator
            # calibrated, and reusing its copy made a fresh calibration look
            # "missing" until the server was restarted.
            hardware.calibration = self._calibration.load_bundle()

        # Fail before the motor turns, not half way through a scan.
        hardware.calibration.require_ready(allow_synthetic=allow_synthetic)

        if config.scan.capture_laser_off_frames and not hardware.laser.controllable:
            raise ConfigurationError(
                "Laser-off frames were requested but this laser cannot be switched "
                "by software. Set laser.control = 'gpio' with a wired pin, or turn "
                "scan.capture_laser_off_frames off."
            )

        plan = plan_capture_positions(
            config.scan.capture_count, config.motor.steps_per_revolution
        )

        calibration_snapshot = {
            "camera": None if hardware.calibration.camera is None else {
                "source": hardware.calibration.camera.source,
                "created_at": hardware.calibration.camera.created_at,
                "rms_reprojection_error": hardware.calibration.camera.rms_reprojection_error,
                "image_size": list(hardware.calibration.camera.image_size),
            },
            "laser": None if hardware.calibration.laser is None else {
                "source": hardware.calibration.laser.source,
                "created_at": hardware.calibration.laser.created_at,
                "plane_normal": list(map(float, hardware.calibration.laser.plane_normal)),
                "plane_offset": float(hardware.calibration.laser.plane_offset),
                "rms_mm": hardware.calibration.laser.rms_mm,
            },
            "turntable": None if hardware.calibration.turntable is None else {
                "source": hardware.calibration.turntable.source,
                "method": hardware.calibration.turntable.method,
                "axis_origin": list(map(float, hardware.calibration.turntable.axis_origin)),
                "axis_direction": list(map(float, hardware.calibration.turntable.axis_direction)),
                "rotation_direction": hardware.calibration.turntable.rotation_direction,
                "steps_per_revolution": hardware.calibration.turntable.steps_per_revolution,
            },
        }

        session = self._sessions.create(
            mode=config.mode.value,
            settings=config.to_dict(),
            calibration=calibration_snapshot,
        )

        session.set_plan([entry.to_dict() for entry in plan])

        self._cancel_event.clear()
        self._state.begin_scan(session.session_id, len(plan))

        return session

    def _run_scan_locked(self, config: ScannerConfig, session: ScanSession) -> ScanOutcome:
        """Execute a prepared scan.  The scan mutex is held on entry."""

        hardware = self._ensure_hardware(config)

        status = SessionStatus.ERROR
        outcome = ScanOutcome(session_id=session.session_id, status=status)

        try:
            hardware.camera.open()
            hardware.motor.open()
            hardware.laser.open()

            hardware.motor.reset_position()

            if hardware.laser.controllable:
                hardware.laser.set_enabled(True)

            session.mark_started()
            self._state.update(phase=ScanPhase.SCANNING)

            logger.info(
                f"Scan {session.session_id} started: {config.scan.capture_count} captures "
                f"over one revolution in {config.mode.value} mode."
            )

            self._acquire(config, session, hardware)

            if config.scan.auto_reconstruct:
                self._state.update(phase=ScanPhase.RECONSTRUCTING)

                result = self._reconstruct_session(
                    session,
                    config,
                    hardware.calibration,
                    allow_synthetic=config.mode is ScannerMode.SIMULATION,
                )

                outcome.point_cloud_path = result[0]
                outcome.point_count = result[1]
                outcome.statistics = result[2]

            status = SessionStatus.COMPLETED
            outcome.message = "Scan completed."

        except ScanCancelled:
            status = SessionStatus.CANCELLED
            outcome.message = "Scan cancelled by the operator."

            logger.warning(f"Scan {session.session_id} cancelled.")

        except ScannerError as error:
            status = SessionStatus.ERROR
            outcome.message = str(error)

            logger.error(f"Scan {session.session_id} failed: {error}")

        except Exception as error:  # noqa: BLE001 - keep the scanner alive
            status = SessionStatus.ERROR
            outcome.message = f"Unexpected failure: {error}"

            logger.error(
                f"Scan {session.session_id} failed unexpectedly: {error}", exc_info=True
            )

        finally:
            # Whatever happened, the hardware must end up in a safe state and
            # the session must end up in an honest one.
            try:
                if hardware.laser.controllable:
                    hardware.laser.set_enabled(False)
            except Exception:  # noqa: BLE001 - best effort
                pass

            try:
                hardware.motor.release()
            except Exception:  # noqa: BLE001 - best effort
                pass

            try:
                hardware.motor.close()
            except Exception:  # noqa: BLE001 - best effort
                pass

            try:
                hardware.laser.close()
            except Exception:  # noqa: BLE001 - best effort
                pass

            try:
                session.finish(
                    status,
                    error=outcome.message if status is SessionStatus.ERROR else None,
                )
            except Exception:  # noqa: BLE001 - never mask the original failure
                logger.error("Session metadata could not be persisted.", exc_info=True)

            phase = {
                SessionStatus.COMPLETED: ScanPhase.COMPLETED,
                SessionStatus.CANCELLED: ScanPhase.CANCELLED,
                SessionStatus.ERROR: ScanPhase.ERROR,
            }[status]

            self._state.end_scan(
                phase, error=outcome.message if status is SessionStatus.ERROR else None
            )

            self._cancel_event.clear()
            self._scan_mutex.release()

        outcome.status = status

        return outcome

    # ------------------------------------------------------------------
    def _acquire(
        self,
        config: ScannerConfig,
        session: ScanSession,
        hardware: HardwareSet,
    ) -> None:
        """Move, settle and capture around one full revolution."""

        plan = plan_capture_positions(
            config.scan.capture_count, config.motor.steps_per_revolution
        )

        should_cancel = self._cancel_event.is_set

        for entry in plan:
            if should_cancel():
                raise ScanCancelled("Scan cancelled before capture.")

            self._state.update(motor_target=entry.target_step)

            hardware.motor.move_to(entry.target_step, should_cancel=should_cancel)

            # Let the platform stop vibrating before looking at it.
            self._sleep_cancellable(config.scan.settle_delay)

            frame = hardware.camera.capture(flush_frames=config.camera.flush_frames)

            if frame is None:
                raise ScannerError(
                    f"The camera returned no frame at capture {entry.index + 1}."
                )

            timestamp = datetime.now()
            filename = f"capture_{entry.index:04d}_{timestamp.strftime('%H%M%S_%f')[:-3]}.jpg"

            target = session.captures_path / filename

            if not cv2.imwrite(str(target), frame):
                raise ScannerError(f"Capture {filename} could not be written to disk.")

            laser_off_filename: str | None = None

            if config.scan.capture_laser_off_frames and hardware.laser.controllable:
                hardware.laser.set_enabled(False)

                off_frame = hardware.camera.capture(flush_frames=config.camera.flush_frames)

                hardware.laser.set_enabled(True)

                if off_frame is not None:
                    laser_off_filename = (
                        f"capture_{entry.index:04d}_"
                        f"{timestamp.strftime('%H%M%S_%f')[:-3]}_off.jpg"
                    )
                    cv2.imwrite(str(session.captures_path / laser_off_filename), off_frame)

            motor_position = hardware.motor.position
            angle = (motor_position / config.motor.steps_per_revolution) * 360.0

            session.add_capture(
                CaptureRecord(
                    index=entry.index,
                    filename=filename,
                    angle_deg=round(angle, 6),
                    target_step=entry.target_step,
                    motor_position=motor_position,
                    timestamp=timestamp.isoformat(timespec="milliseconds"),
                    laser_state="on" if hardware.laser.is_on else "unknown",
                    laser_off_filename=laser_off_filename,
                )
            )

            self._state.record_capture(
                capture_index=entry.index + 1,
                filename=filename,
                angle_deg=round(angle, 3),
                motor_position=motor_position,
            )

            self._sleep_cancellable(config.scan.capture_delay)

        # Complete the revolution so the platform ends where it started.
        remaining = closing_steps(plan, config.motor.steps_per_revolution)

        if remaining and not should_cancel():
            try:
                hardware.motor.move_to(
                    config.motor.steps_per_revolution, should_cancel=should_cancel
                )
            except ScanCancelled:
                # The captures are already safe; returning home is a courtesy.
                logger.warning("Cancelled while returning the platform to its origin.")

    def _sleep_cancellable(self, seconds: float) -> None:
        """Sleep, but wake up immediately if the operator presses Stop."""

        if seconds <= 0:
            return

        if self._cancel_event.wait(timeout=seconds):
            raise ScanCancelled("Scan cancelled while waiting.")

    # ------------------------------------------------------------------
    # Automatic calibration
    # ------------------------------------------------------------------
    @property
    def auto_calibration_directory(self) -> Path:
        return run_directory(self._calibration.directory)

    def _update_auto_progress(self, **values: Any) -> None:
        with self._auto_lock:
            self._auto_progress.update(values)

    def auto_calibration_status(self) -> dict[str, Any]:
        """Live progress of a running step plus a summary of the stored run."""

        with self._auto_lock:
            progress = dict(self._auto_progress)

        try:
            run = summarize_run(load_run(self.auto_calibration_directory))
        except CalibrationError as error:
            run = summarize_run(None)
            run["error"] = str(error)

        return {"progress": progress, "run": run}

    def start_auto_calibration_step(self, step: int, spec: CheckerboardSpec) -> None:
        """Turn the platform once and photograph the board, in the background."""

        if step not in AUTO_CALIBRATION_STEPS:
            raise CalibrationError(f"Unknown calibration step {step}; use 1, 2 or 3.")

        spec.validate()

        if not self._scan_mutex.acquire(blocking=False):
            raise ScanAlreadyRunning("A scan or a calibration step is already running.")

        try:
            config = self._config_store.get()

            if config.mode is not ScannerMode.PHYSICAL:
                raise ConfigurationError(
                    "The automatic calibration drives the physical scanner. Switch to "
                    "physical mode first; simulation mode already uses the exact "
                    "geometry of its virtual rig."
                )

            hardware = self._ensure_hardware(config)

            self._cancel_event.clear()
            self._update_auto_progress(
                running=True,
                step=step,
                done=0,
                total=self.auto_calibration_stops,
                board_found=0,
                message=f"Step {step} running.",
                error=None,
            )
        except BaseException:
            self._scan_mutex.release()
            raise

        threading.Thread(
            target=self._run_auto_step_locked,
            args=(config, hardware, step, spec),
            name=f"auto-calibration-step-{step}",
            daemon=True,
        ).start()

    def _run_auto_step_locked(
        self,
        config: ScannerConfig,
        hardware: HardwareSet,
        step: int,
        spec: CheckerboardSpec,
    ) -> None:
        """Body of a calibration step.  The scan mutex is held on entry."""

        try:
            logger.info(f"Automatic calibration step {step} started.")

            record = capture_step(
                step,
                camera=hardware.camera,
                motor=hardware.motor,
                spec=spec,
                directory=self.auto_calibration_directory,
                steps_per_revolution=config.motor.steps_per_revolution,
                stops=self.auto_calibration_stops,
                settle_delay=config.scan.settle_delay,
                flush_frames=config.camera.flush_frames,
                progress=lambda values: self._update_auto_progress(**values),
                should_cancel=self._cancel_event.is_set,
            )

            frames = record["frames"]
            found = sum(1 for frame in frames if frame.get("corners"))
            message = f"Step {step} done: board found in {found} of {len(frames)} photographs."

            logger.info(message)
            self._update_auto_progress(message=message, error=None)

        except ScanCancelled:
            logger.warning(f"Automatic calibration step {step} cancelled.")
            self._update_auto_progress(message=f"Step {step} cancelled.", error=None)

        except ScannerError as error:
            logger.error(f"Automatic calibration step {step} failed: {error}")
            self._update_auto_progress(message=f"Step {step} failed.", error=str(error))

        except Exception as error:  # noqa: BLE001 - keep the scanner alive
            logger.error(
                f"Automatic calibration step {step} failed unexpectedly: {error}", exc_info=True
            )
            self._update_auto_progress(
                message=f"Step {step} failed.", error=f"Unexpected failure: {error}"
            )

        finally:
            for close in (hardware.motor.release, hardware.motor.close):
                try:
                    close()
                except Exception:  # noqa: BLE001 - best effort
                    pass

            self._update_auto_progress(running=False)
            self._cancel_event.clear()
            self._scan_mutex.release()

    def compute_auto_calibration(self) -> AutoCalibrationReport:
        """Compute and, if everything passes, save the calibration."""

        if not self._scan_mutex.acquire(blocking=False):
            raise ScanAlreadyRunning("Wait for the running scan or calibration step to finish.")

        try:
            config = self._config_store.get()

            report = compute_auto_calibration(
                self.auto_calibration_directory,
                LaserLineDetector(config.detector),
                store=self._calibration,
                diagnostics_dir=paths.DIAGNOSTICS_DIR / AUTO_CALIBRATION_DIAGNOSTICS,
            )
        finally:
            self._scan_mutex.release()

        if report.saved:
            logger.info("Automatic calibration saved (lens, turntable and laser plane).")
        else:
            logger.warning(
                "Automatic calibration not saved: " + "; ".join(report.issues or ["see report"])
            )

        return report

    def reset_auto_calibration(self) -> None:
        """Forget every photograph of the current automatic calibration."""

        if self.is_running:
            raise ScanAlreadyRunning("Wait for the running scan or calibration step to finish.")

        reset_run(self.auto_calibration_directory)

        self._update_auto_progress(
            step=None, done=0, total=0, board_found=0, message=None, error=None
        )

    # ------------------------------------------------------------------
    # Reconstruction
    # ------------------------------------------------------------------
    def _reconstruct_session(
        self,
        session: ScanSession,
        config: ScannerConfig,
        calibration,
        *,
        allow_synthetic: bool,
    ) -> tuple[Path, int, dict[str, Any]]:
        metadata = session.metadata()

        detector = LaserLineDetector(config.detector)

        reconstructor = Reconstructor(
            calibration,
            detector,
            config.reconstruction,
            config.filtering,
            allow_synthetic_calibration=allow_synthetic,
        )

        captures = captures_from_session_metadata(session.path, metadata)

        result: ReconstructionResult = reconstructor.reconstruct(
            captures,
            should_cancel=self._cancel_event.is_set,
        )

        filename = f"{session.session_id}_{datetime.now().strftime('%H%M%S')}.ply"
        target = session.point_clouds_path / filename

        write_ply(
            target,
            result.cloud,
            binary=True,
            comments=[
                f"session {session.session_id}",
                f"mode {metadata.get('mode')}",
                f"captures {result.statistics.get('frames_accepted')}"
                f"/{result.statistics.get('frames_total')}",
            ],
        )

        session.add_point_cloud(
            PointCloudRecord(
                filename=filename,
                point_count=result.point_count,
                created_at=datetime.now().isoformat(timespec="seconds"),
                statistics=result.cloud.summary(),
            )
        )
        session.set_reconstruction_statistics(result.statistics)

        self._state.record_point_cloud(
            path=str(target),
            session_id=session.session_id,
            point_count=result.point_count,
            statistics=result.statistics,
        )

        if not config.scan.save_raw_frames:
            self._discard_raw_frames(session)

        logger.info(
            f"Reconstruction produced {result.point_count} points "
            f"({result.statistics.get('frames_accepted')} of "
            f"{result.statistics.get('frames_total')} frames used)."
        )

        return target, result.point_count, result.statistics

    @staticmethod
    def _discard_raw_frames(session: ScanSession) -> None:
        """Delete the raw captures once a reconstruction succeeded.

        Only used when ``scan.save_raw_frames`` is off, to save SD card space.
        The metadata records that the session can no longer be re-reconstructed.
        """

        removed = 0

        for path in session.capture_files():
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass

        session.set_reconstruction_statistics(
            {
                **session.metadata().get("reconstruction", {}),
                "raw_frames_discarded": removed,
                "offline_reconstruction_possible": False,
            }
        )

        logger.info(f"Discarded {removed} raw frames (scan.save_raw_frames is off).")

    def reconstruct_existing_session(
        self,
        session_id: str,
        *,
        config: ScannerConfig | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> ScanOutcome:
        """Re-run reconstruction on an already captured session.

        This is what makes tuning practical: capture once, then iterate on the
        detector thresholds or a fresh calibration without touching the motor.
        """

        if self.is_running:
            raise ScanAlreadyRunning(
                "A scan is running; wait for it to finish before reconstructing."
            )

        config = config or self._config_store.get()
        session = self._sessions.load(session_id)

        metadata = session.metadata()

        if not metadata.get("captures"):
            raise SessionError(f"Session {session_id} has no captures to reconstruct.")

        allow_synthetic = str(metadata.get("mode")) == ScannerMode.SIMULATION.value

        if allow_synthetic:
            # Rebuild the exact synthetic rig the session was recorded with.
            from scanner_core.config import SimulationConfig
            from scanner_core.simulation import SyntheticScene

            recorded = (metadata.get("settings") or {}).get("simulation") or {}
            simulation_config = SimulationConfig(**recorded) if recorded else config.simulation
            simulation_config.validate()

            scene = SyntheticScene(simulation_config)
            calibration = scene.build_calibration_bundle(
                steps_per_revolution=config.motor.steps_per_revolution
            )
        else:
            calibration = self._calibration.load_bundle()

        calibration.require_ready(allow_synthetic=allow_synthetic)

        self._state.update(phase=ScanPhase.RECONSTRUCTING)

        try:
            target, count, statistics = self._reconstruct_session(
                session, config, calibration, allow_synthetic=allow_synthetic
            )
        except (CalibrationError, ReconstructionError) as error:
            self._state.update(phase=ScanPhase.ERROR, last_error=str(error))
            session.add_error(str(error))

            raise
        finally:
            if self._state.get().phase is ScanPhase.RECONSTRUCTING:
                self._state.update(phase=ScanPhase.COMPLETED)

        return ScanOutcome(
            session_id=session_id,
            status=SessionStatus(metadata.get("status", SessionStatus.COMPLETED.value)),
            point_cloud_path=target,
            point_count=count,
            statistics=statistics,
            message=f"Reconstructed {count} points from {session_id}.",
        )


def reconstruct_captures(
    captures: list[CaptureInput],
    calibration,
    config: ScannerConfig,
    *,
    allow_synthetic: bool = False,
) -> ReconstructionResult:
    """Reconstruct a list of captures without touching sessions or hardware.

    Used by the offline CLI and by the tests.
    """

    reconstructor = Reconstructor(
        calibration,
        LaserLineDetector(config.detector),
        config.reconstruction,
        config.filtering,
        allow_synthetic_calibration=allow_synthetic,
    )

    return reconstructor.reconstruct(captures)


#: Process-wide controller used by the web application.
scan_controller = ScanController()
