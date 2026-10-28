from __future__ import annotations

import pytest

from config import Settings, WatchTarget
from reporting import print_banner, print_session_summary
from runtime import Runtime


def _settings() -> Settings:
    return Settings(watch=[WatchTarget(customer="18", balance_below=-500.0)])


def test_print_banner_runs(capsys: pytest.CaptureFixture[str]) -> None:
    print_banner(_settings(), hot_reload=True)
    out = capsys.readouterr().out
    assert "InstacallMonitor" in out
    assert "18" in out


def test_print_banner_gated_colors(capsys: pytest.CaptureFixture[str]) -> None:
    # Under pytest stdout is not a tty, so colours are disabled by default.
    print_banner(_settings(), hot_reload=False)
    assert "\033[" not in capsys.readouterr().out


def test_print_session_summary_runs(capsys: pytest.CaptureFixture[str]) -> None:
    runtime = Runtime()
    runtime.start()
    runtime.record_cycle("2026-01-01 00:00:00")
    runtime.note_error("boom")
    print_session_summary(runtime)
    out = capsys.readouterr().out
    assert "Session Summary" in out
    assert "boom" in out
