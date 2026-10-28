from __future__ import annotations

import io
import logging
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import wave
from functools import lru_cache

ToneSeq = list[tuple[int, int]]  # (frequency_hz, duration_ms); frequency 0 = silence

_SAMPLE_RATE = 44100
_AMPLITUDE = 0.4
_FADE_MS = 5


def build_rising_falling(min_freq: int, max_freq: int, step_freq: int, tone_ms: int, loops: int) -> ToneSeq:
    seq: ToneSeq = []
    for _ in range(max(loops, 0)):
        for freq in range(min_freq, max_freq, step_freq):
            seq.append((freq, tone_ms))
        for freq in range(max_freq, min_freq, -step_freq):
            seq.append((freq, tone_ms))
    return seq


def build_alternating(min_freq: int, max_freq: int, tone_ms: int, loops: int) -> ToneSeq:
    seq: ToneSeq = []
    for _ in range(max(loops, 0) * 2):
        seq.append((min_freq, tone_ms))
        seq.append((max_freq, tone_ms))
    return seq


def build_steady(freq: int, tone_ms: int, pause_ms: int, loops: int) -> ToneSeq:
    seq: ToneSeq = []
    for _ in range(max(loops, 0)):
        seq.append((freq, tone_ms))
        seq.append((0, pause_ms))
    return seq


def render_wav(seq: ToneSeq, sample_rate: int = _SAMPLE_RATE) -> bytes:
    return _render_wav_cached(tuple(seq), sample_rate)


@lru_cache(maxsize=64)
def _render_wav_cached(seq: tuple[tuple[int, int], ...], sample_rate: int) -> bytes:
    frames = bytearray()
    fade = max(int(sample_rate * _FADE_MS / 1000), 1)
    for freq, dur_ms in seq:
        n = int(sample_rate * dur_ms / 1000)
        if n <= 0:
            continue
        f = min(fade, n // 2)
        for i in range(n):
            if freq <= 0:
                sample = 0.0
            else:
                sample = _AMPLITUDE * math.sin(2 * math.pi * freq * i / sample_rate)
                if f:
                    if i < f:
                        sample *= i / f
                    elif i >= n - f:
                        sample *= (n - i) / f
            frames += struct.pack("<h", int(max(-1.0, min(1.0, sample)) * 32767))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def _find_player() -> list[str] | None:
    candidates = (
        ("afplay", ["afplay"]),
        ("aplay", ["aplay", "-q"]),
        ("paplay", ["paplay"]),
        ("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]),
    )
    for name, argv in candidates:
        path = shutil.which(name)
        if path:
            return [path, *argv[1:]]
    return None


def _play_windows(seq: ToneSeq) -> None:
    import winsound

    for freq, dur_ms in seq:
        if freq <= 0:
            time.sleep(dur_ms / 1000.0)
        else:
            winsound.Beep(int(freq), int(dur_ms))


def _duration_ms(seq: ToneSeq) -> int:
    return sum(dur for _, dur in seq)


def _play_posix(seq: ToneSeq) -> None:
    player = _find_player()
    if player is None:
        if sys.stdout.isatty():
            for _ in range(min(len(seq), 3)):
                sys.stdout.write("\a")
                sys.stdout.flush()
                time.sleep(0.2)
        else:
            logging.warning("No audio player found and stdout is not a TTY; siren not audible.")
        return

    data = render_wav(seq)
    fd, path = tempfile.mkstemp(suffix=".wav")
    try:
        try:
            handle = os.fdopen(fd, "wb")
        except Exception:
            os.close(fd)
            raise
        with handle:
            handle.write(data)
        # Cap playback so a hung player can never block the alert thread forever.
        timeout = max(_duration_ms(seq) / 1000.0 + 5.0, 5.0)
        subprocess.run(
            [*player, path],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logging.warning("Audio player timed out; killing it.")
    except Exception as e:
        logging.warning(f"Audio playback failed: {e}")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def play(seq: ToneSeq) -> None:
    if not seq:
        return
    try:
        if sys.platform == "win32":
            _play_windows(seq)
        else:
            _play_posix(seq)
    except Exception as e:
        logging.warning(f"Audio playback failed: {e}")
