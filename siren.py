from __future__ import annotations

import threading
import time

import audio

# Siren tone parameters — fixed rather than user-configurable.
_SIREN_LOOPS = 10
_SIREN_MIN_FREQ = 2200
_SIREN_MAX_FREQ = 3500
_SIREN_STEP_FREQ = 130
_SIREN_TONE_DURATION = 50


class SirenManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._playing = False
        self._timestamps: dict[str, float] = {}

    @property
    def is_playing(self) -> bool:
        with self._lock:
            return self._playing

    def can_alert(self, customer_id: str, alert_type: str, cooldown: float) -> bool:
        with self._lock:
            key = f"{customer_id}:{alert_type}"
            # Monotonic clock: wall-clock jumps (NTP/DST) must not suppress or
            # spike alert cooldowns.
            now = time.monotonic()
            last = self._timestamps.get(key, 0.0)
            if now - last < cooldown:
                return False
            self._timestamps[key] = now
            return True

    def acquire(self) -> bool:
        with self._lock:
            if self._playing:
                return False
            self._playing = True
            return True

    def release(self) -> None:
        with self._lock:
            self._playing = False

    def play_rising_falling(self) -> None:
        audio.play(
            audio.build_rising_falling(
                _SIREN_MIN_FREQ,
                _SIREN_MAX_FREQ,
                _SIREN_STEP_FREQ,
                _SIREN_TONE_DURATION,
                _SIREN_LOOPS,
            )
        )

    def play_alternating(self) -> None:
        audio.play(audio.build_alternating(_SIREN_MIN_FREQ, _SIREN_MAX_FREQ, 100, _SIREN_LOOPS))

    def play_steady(self) -> None:
        audio.play(audio.build_steady(_SIREN_MIN_FREQ, 100, 100, _SIREN_LOOPS * 4))
