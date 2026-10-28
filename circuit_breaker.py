from __future__ import annotations

import threading
import time
from enum import Enum


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at = 0.0
        self._half_open_at = 0.0

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    @property
    def is_open(self) -> bool:
        # True whenever requests are being blocked (OPEN or awaiting a probe).
        with self._lock:
            return self._state in (CircuitState.OPEN, CircuitState.HALF_OPEN)

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failure_count

    def before_request(self) -> bool:
        with self._lock:
            now = time.monotonic()
            if self._state == CircuitState.OPEN:
                if now - self._opened_at >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_at = now
                    return True
                return False
            if self._state == CircuitState.HALF_OPEN:
                # Allow a fresh probe only if the previous probe never reported an
                # outcome (a caller returned true without calling on_success/on_failure).
                # This prevents the breaker latching permanently in HALF_OPEN.
                if now - self._half_open_at >= self.recovery_timeout:
                    self._half_open_at = now
                    return True
                return False
            return True

    def on_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._state = CircuitState.CLOSED

    def on_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()

    def reset(self) -> None:
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._opened_at = 0.0
            self._half_open_at = 0.0

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "name": self.name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "opened_at": self._opened_at if self._opened_at else None,
            }
