from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Runtime:
    """Mutable process state shared by the loop, commands and the health writer."""

    shutdown: threading.Event = field(default_factory=threading.Event, repr=False, compare=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    started_at: float = 0.0
    _cycle_count: int = 0
    _error_count: int = 0
    _last_error: Optional[str] = None
    _last_check: Optional[str] = None

    def start(self) -> None:
        with self.lock:
            self.started_at = time.time()

    def uptime(self) -> float:
        # started_at is a float; a plain read is atomic and avoids re-entering the
        # lock from health().
        return time.time() - self.started_at if self.started_at else 0.0

    @property
    def cycle_count(self) -> int:
        with self.lock:
            return self._cycle_count

    @property
    def error_count(self) -> int:
        with self.lock:
            return self._error_count

    @property
    def last_error(self) -> Optional[str]:
        with self.lock:
            return self._last_error

    @property
    def last_check(self) -> Optional[str]:
        with self.lock:
            return self._last_check

    def note_error(self, message: str) -> None:
        with self.lock:
            self._error_count += 1
            self._last_error = message

    def record_cycle(self, timestamp: str) -> None:
        with self.lock:
            self._cycle_count += 1
            self._last_check = timestamp

    def health(self, *, settings_version: int, customers_monitored: int) -> dict[str, object]:
        uptime = self.uptime()
        with self.lock:
            return {
                "alive": not self.shutdown.is_set(),
                "last_check": self._last_check,
                "error_count": self._error_count,
                "last_error": self._last_error,
                "uptime_seconds": round(uptime, 1),
                "cycle_count": self._cycle_count,
                "settings_version": settings_version,
                "customers_monitored": customers_monitored,
            }
