from __future__ import annotations

import threading
import time

import pytest

import app
from cli import apply_cli_overrides, parse_args
from config import GlobalSettings, Settings, WatchTarget
from runtime import Runtime


class TestParseArgs:
    def test_web_flags(self) -> None:
        args = parse_args(["--no-web", "--web-port", "9000"])
        assert args["no_web"] is True
        assert args["web_port"] == 9000

    def test_threshold_flags(self) -> None:
        args = parse_args(["--interval", "60", "--margin-below", "20.5"])
        assert args["interval"] == 60
        assert args["margin_below"] == 20.5

    def test_invalid_value_ignored(self) -> None:
        args = parse_args(["--web-port", "not-a-number"])
        assert "web_port" not in args

    def test_tail_defaults_on_invalid(self) -> None:
        args = parse_args(["--tail", "nope"])
        assert args["tail"] == 20

    def test_unknown_flag_warns(self, capsys: pytest.CaptureFixture[str]) -> None:
        args = parse_args(["--nope"])
        assert args == {}
        assert "Unknown option" in capsys.readouterr().out

    def test_missing_value_warns(self, capsys: pytest.CaptureFixture[str]) -> None:
        args = parse_args(["--interval"])
        assert "interval" not in args
        assert "Missing value" in capsys.readouterr().out


class TestApplyCliOverrides:
    def _settings(self) -> Settings:
        return Settings(
            global_=GlobalSettings(),
            watch=[WatchTarget(customer="18", balance_below=-500.0)],
        )

    def test_no_web_disables_dashboard(self) -> None:
        settings = self._settings()
        apply_cli_overrides(settings, {"no_web": True})
        assert settings.global_.web_dashboard is False

    def test_web_port_override(self) -> None:
        settings = self._settings()
        apply_cli_overrides(settings, {"web_port": 9999})
        assert settings.global_.web_dashboard_port == 9999

    def test_threshold_override(self) -> None:
        settings = self._settings()
        apply_cli_overrides(settings, {"margin_below": 22.0, "interval": 45})
        assert settings.global_.margin_below == 22.0
        assert settings.global_.check_interval == 45


class TestHotReloadKeepsCliOverrides:
    def test_reload_reapplies_cli_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        current = Settings(
            global_=GlobalSettings(margin_below=30.0),
            watch=[WatchTarget(customer="18", balance_below=-500.0)],
        )
        reloaded = Settings(
            global_=GlobalSettings(margin_below=30.0),
            watch=[WatchTarget(customer="18", balance_below=-500.0)],
        )
        monkeypatch.setattr(app, "reload_settings", lambda _current: reloaded)

        result = app._try_hot_reload(current, {"margin_below": 20.0, "web_port": 9000})

        assert result.global_.margin_below == 20.0
        assert result.global_.web_dashboard_port == 9000


@pytest.mark.slow
class TestSleepInterruptible:
    def test_wakes_immediately_on_event(self) -> None:
        shutdown = threading.Event()
        wake = threading.Event()
        wake.set()
        started = time.time()
        assert app._sleep_interruptible(30, shutdown, wake) is True
        assert time.time() - started < 3

    def test_returns_after_short_sleep_without_event(self) -> None:
        shutdown = threading.Event()
        started = time.time()
        assert app._sleep_interruptible(1, shutdown) is False
        assert time.time() - started >= 1


class TestRuntime:
    def test_counts_and_health(self) -> None:
        runtime = Runtime()
        runtime.start()
        runtime.record_cycle("2026-01-01 00:00:00")
        runtime.note_error("boom")
        health = runtime.health(settings_version=2, customers_monitored=3)
        assert health["cycle_count"] == 1
        assert health["error_count"] == 1
        assert health["last_error"] == "boom"
        assert health["last_check"] == "2026-01-01 00:00:00"
        assert health["alive"] is True
        runtime.shutdown.set()
        assert runtime.health(settings_version=2, customers_monitored=3)["alive"] is False
