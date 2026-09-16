"""Continuous 28BYJ-48 / ULN2003 half-step driver; importing does not touch GPIO."""
import math
import threading
import time

HALF_STEP_SEQUENCE = (
    (1, 0, 0, 0), (1, 1, 0, 0), (0, 1, 0, 0), (0, 1, 1, 0),
    (0, 0, 1, 0), (0, 0, 1, 1), (0, 0, 0, 1), (1, 0, 0, 1),
)
DEFAULT_PINS = (17, 27, 22, 23)


class MotorError(RuntimeError):
    pass


class MotorCancelled(MotorError):
    pass


def capture_positions(captures, steps_per_revolution):
    """Absolute commanded half-step targets in [0, revolution), never 360 degrees."""
    if type(captures) is not int or type(steps_per_revolution) is not int:
        raise ValueError("Capture count and steps per revolution must be integers")
    if not 1 <= captures <= steps_per_revolution:
        raise ValueError("Capture count must be positive and no greater than steps per revolution")
    return [index * steps_per_revolution // captures for index in range(captures)]


class GPIOBackend:
    """One write is one coil state. Devices are created only on the first write."""
    def __init__(self, pins):
        self.pins = tuple(pins)
        self.devices = []

    def _open(self):
        if self.devices:
            return
        try:
            from gpiozero import OutputDevice
            for pin in self.pins:
                self.devices.append(OutputDevice(pin, initial_value=False))
        except Exception as error:
            self.close()
            raise MotorError(f"GPIO unavailable; physical motor was not started: {error}") from error

    def write(self, state):
        self._open()
        for device, value in zip(self.devices, state):
            device.value = value

    def release(self):
        errors = []
        for device in self.devices:
            try:
                device.off()
            except Exception as error:
                errors.append(error)
        if errors:
            raise MotorError(f"Unable to disable all coils: {errors[0]}")

    def close(self):
        errors = []
        for device in self.devices:
            try:
                device.off()
            except Exception as error:
                errors.append(error)
            finally:
                try:
                    device.close()
                except Exception as error:
                    errors.append(error)
        self.devices = []
        if errors:
            raise MotorError(f"GPIO cleanup failed: {errors[0]}")


class StepperMotor:
    """Position counts commanded transitions, not measured displacement or homing.

    First energizing a phase is alignment and is not counted as a transition.
    Eight subsequent transitions are one sequence cycle, not eight cycles.
    Backend injection requires write(tuple), release(), and close().
    """
    simulated = False

    def __init__(self, pins=DEFAULT_PINS, step_delay=0.002, direction=1, backend=None):
        if not isinstance(pins, (list, tuple)) or len(pins) != 4:
            raise ValueError("Exactly four BCM pins are required")
        if any(type(pin) is not int or not 0 <= pin <= 27 for pin in pins) or len(set(pins)) != 4:
            raise ValueError("BCM pins must be four distinct integers between 0 and 27")
        if isinstance(step_delay, bool) or not isinstance(step_delay, (int, float)) or not math.isfinite(step_delay) or step_delay <= 0:
            raise ValueError("Half-step delay must be finite and greater than zero")
        if type(direction) is not int or direction not in (-1, 1):
            raise ValueError("Direction must be +1 or -1")
        self.pins = tuple(pins)
        self.step_delay = float(step_delay)
        self.direction = direction
        self.position = 0
        self.phase = 0
        self._energized = False
        self._closed = False
        self._lock = threading.RLock()
        self._backend = backend if backend is not None else GPIOBackend(pins)

    def _wait(self, cancel_event):
        if cancel_event is None:
            time.sleep(self.step_delay)
        elif cancel_event.wait(self.step_delay):
            raise MotorCancelled("Motor movement cancelled")

    def move(self, half_steps, cancel_event=None):
        if type(half_steps) is not int:
            raise ValueError("Movement must be an integer number of half-step transitions")
        with self._lock:
            if self._closed:
                raise MotorError("Motor is closed")
            start = self.position
            try:
                if cancel_event is not None and cancel_event.is_set():
                    raise MotorCancelled("Motor movement cancelled")
                if not half_steps:
                    return 0
                if not self._energized:
                    self._backend.write(HALF_STEP_SEQUENCE[self.phase])
                    self._energized = True
                    self._wait(cancel_event)
                increment = self.direction * (1 if half_steps > 0 else -1)
                for _ in range(abs(half_steps)):
                    if cancel_event is not None and cancel_event.is_set():
                        raise MotorCancelled("Motor movement cancelled")
                    next_phase = (self.phase + increment) % len(HALF_STEP_SEQUENCE)
                    self._backend.write(HALF_STEP_SEQUENCE[next_phase])
                    self.phase = next_phase
                    self.position += increment
                    self._wait(cancel_event)
                return self.position - start
            except BaseException:
                self.release()
                raise

    def release(self):
        with self._lock:
            try:
                self._backend.release()
            finally:
                self._energized = False

    def close(self):
        with self._lock:
            if not self._closed:
                try:
                    self._backend.close()
                finally:
                    self._energized = False
                    self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class _SyntheticBackend:
    def write(self, state):
        pass

    def release(self):
        pass

    def close(self):
        pass


class SimulatedMotor(StepperMotor):
    """Explicit synthetic motor: never imports a GPIO library."""
    simulated = True

    def __init__(self, pins=DEFAULT_PINS, step_delay=0.002, direction=1, backend=None):
        super().__init__(pins, step_delay, direction, backend or _SyntheticBackend())

    def _wait(self, cancel_event):
        if cancel_event is not None and cancel_event.is_set():
            raise MotorCancelled("Synthetic movement cancelled")


SyntheticMotor = SimulatedMotor


def rotate_step():
    raise MotorError("Use StepperMotor.move(half_steps) or explicitly select SimulatedMotor")


def reset():
    raise MotorError("A command counter reset is not physical homing; create a new scan origin")
