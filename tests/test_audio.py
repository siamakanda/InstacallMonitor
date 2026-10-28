from __future__ import annotations

import subprocess

import audio
from audio import build_alternating, build_rising_falling, build_steady, render_wav


def _raiser(exc: Exception):
    def _f(*args, **kwargs):
        raise exc

    return _f


class TestToneSequences:
    def test_rising_falling_length(self) -> None:
        seq = build_rising_falling(2200, 3500, 130, 50, 1)
        assert len(seq) == 20
        assert all(freq > 0 for freq, _ in seq)

    def test_rising_falling_loops(self) -> None:
        seq = build_rising_falling(2200, 3500, 130, 50, 3)
        assert len(seq) == 60

    def test_alternating_pattern(self) -> None:
        seq = build_alternating(2200, 3500, 100, 2)
        assert seq == [
            (2200, 100),
            (3500, 100),
            (2200, 100),
            (3500, 100),
            (2200, 100),
            (3500, 100),
            (2200, 100),
            (3500, 100),
        ]

    def test_steady_contains_silence(self) -> None:
        seq = build_steady(2200, 100, 100, 2)
        assert seq == [(2200, 100), (0, 100), (2200, 100), (0, 100)]

    def test_zero_loops(self) -> None:
        assert build_rising_falling(2200, 3500, 130, 50, 0) == []
        assert build_alternating(2200, 3500, 100, 0) == []
        assert build_steady(2200, 100, 100, 0) == []


class TestRenderWav:
    def test_ripple_header_and_length(self) -> None:
        data = render_wav([(440, 50)], sample_rate=8000)
        assert data[:4] == b"RIFF"
        assert data[8:12] == b"WAVE"
        assert len(data) == 44 + 400 * 2

    def test_silence_is_all_zero_frames(self) -> None:
        data = render_wav([(0, 10)], sample_rate=8000)
        assert len(data) == 44 + 80 * 2
        assert set(data[44:]) <= {0}

    def test_empty_sequence(self) -> None:
        data = render_wav([], sample_rate=8000)
        assert data[:4] == b"RIFF"
        assert len(data) == 44


class TestPlaySafety:
    def test_play_never_raises(self, monkeypatch) -> None:
        monkeypatch.setattr(audio, "_play_windows", _raiser(RuntimeError("no audio device")))
        monkeypatch.setattr(audio, "_play_posix", _raiser(RuntimeError("no player")))
        audio.play([(440, 10)])  # must not raise

    def test_play_posix_swallows_player_timeout(self, monkeypatch) -> None:
        monkeypatch.setattr(audio, "_find_player", lambda: ["/bin/echo"])
        monkeypatch.setattr(audio.subprocess, "run", _raiser(subprocess.TimeoutExpired(cmd="echo", timeout=1)))
        audio._play_posix([(440, 10)])  # must not raise

    def test_play_posix_swallows_player_error(self, monkeypatch) -> None:
        monkeypatch.setattr(audio, "_find_player", lambda: ["/bin/echo"])
        monkeypatch.setattr(audio.subprocess, "run", _raiser(OSError("player died")))
        audio._play_posix([(440, 10)])  # must not raise

    def test_empty_sequence_is_noop(self) -> None:
        audio.play([])  # must not raise
