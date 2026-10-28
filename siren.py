from __future__ import annotations

import threading
import time

import audio


class SirenManager:
    def __init__(
        self,
        siren_loops: int = 10,
        siren_min_freq: int = 2200,
        siren_max_freq: int = 3500,
        siren_step_freq: int = 130,
        siren_tone_duration: int = 50,
    ) -> None:
        self._lock = threading.Lock()
        self._playing = False
        self._timestamps: dict[str, float] = {}
        self.siren_loops = siren_loops
        self.siren_min_freq = siren_min_freq
        self.siren_max_freq = siren_max_freq
        self.siren_step_freq = siren_step_freq
        self.siren_tone_duration = siren_tone_duration

    @property
    def is_playing(self) -> bool:
        return self._playing

    def can_alert(self, customer_id: str, alert_type: str, cooldown: float) -> bool:
        with self._lock:
            key = f"{customer_id}:{alert_type}"
            now = time.time()
            last = self._timestamps.get(key, 0)
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
                self.siren_min_freq,
                self.siren_max_freq,
                self.siren_step_freq,
                self.siren_tone_duration,
                self.siren_loops,
            )
        )

    def play_alternating(self) -> None:
        audio.play(audio.build_alternating(self.siren_min_freq, self.siren_max_freq, 100, self.siren_loops))

    def play_steady(self) -> None:
        audio.play(audio.build_steady(self.siren_min_freq, 100, 100, self.siren_loops * 4))
