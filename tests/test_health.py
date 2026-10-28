from __future__ import annotations

from pathlib import Path

import pytest

from config import Settings, WatchTarget
from health import read_status, render_status, render_tail, write_status
from runtime import Runtime


def _settings() -> Settings:
    return Settings(watch=[WatchTarget(customer="18", balance_below=-500.0)])


def test_status_roundtrip(tmp_path: Path) -> None:
    path = str(tmp_path / "monitor.status")
    runtime = Runtime()
    runtime.start()
    runtime.record_cycle("2026-01-01 00:00:00")

    write_status(runtime, _settings(), path=path)

    status = read_status(path)
    assert status is not None
    assert status["cycle_count"] == 1
    assert status["customers_monitored"] == 1
    assert status["alive"] is True
    assert status["last_check"] == "2026-01-01 00:00:00"


def test_read_status_missing(tmp_path: Path) -> None:
    assert read_status(str(tmp_path / "does-not-exist")) is None


def test_read_status_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "bad.status"
    path.write_text("{not valid json", encoding="utf-8")
    assert read_status(str(path)) is None


def test_write_status_leaves_no_temp_files(tmp_path: Path) -> None:
    path = str(tmp_path / "monitor.status")
    runtime = Runtime()
    runtime.start()
    write_status(runtime, _settings(), path=path)
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "monitor.status"]
    assert leftovers == []
    assert read_status(path) is not None


def test_render_status_survives_non_numeric_uptime(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "bad-uptime.status"
    path.write_text('{"alive": true, "uptime_seconds": "oops"}', encoding="utf-8")
    render_status(str(path))  # must not raise
    assert "Uptime" in capsys.readouterr().out


def test_render_tail_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    render_tail(10, str(tmp_path / "nope.log"))
    assert "No log file" in capsys.readouterr().out
