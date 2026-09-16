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

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import cv2

from scanner_core import logger, paths
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

    # ------------------------------------------------------------------
    # Hardware lifecycle
    # ------------------------------------------------------------------
    def _ensure_hardware(self, config: ScannerConfig) -> HardwareSet:
        """Return the hardware set for the current mode, building it if needed.

        The set is cached so the live preview keeps one camera handle across
        scans instead of reopening the device every time.
        """

        with self._hardware_lock:
            if self._hardware is not None and self._hardware_mode is config.mode:
                return self._hardware

            self.release_hardware()

            hardware = build_hardware(config)

            self._hardware = hardware
            self._hardware_mode = config.mode

            return hardware

    def release_hardware(self) -> None:
        """Close every device.  Used on mode change and at shutdown."""

        with self._hardware_lock:
            if self._hardware is not None:
                self._hardware.close()

            self._hardware = None
            self._hardware_mode = None

    def preview_frame(self):
        """One frame for the MJPEG stream, or ``None`` when unavailable."""

        config = self._config_store.get()

        if config.mode is ScannerMode.OFFLINE:
            return None

        try:
            hardware = self._ensure_hardware(config)
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

        if hardware is None or self._hardware_mode is not config.mode:
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
