from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

import alerts
import app
import config
import persistence
from config import GlobalSettings, Settings, WatchTarget
from runtime import Runtime
from scrapers import BalanceResult


def _settings(**kw) -> Settings:
    g = GlobalSettings(audio=False, web_dashboard=False, check_interval=1, cooldown=0, **kw)
    return Settings(global_=g, watch=[WatchTarget(customer="18", balance_below=-500.0)])


class Portal:
    """Fake portal that replaces the app's network seams."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.session = MagicMock()
        self.balance_results: dict[str, BalanceResult] = {}
        self.summary: tuple[dict, str | None] = ({}, None)
        self.notifications: list[dict] = []
        self.states: list = []
        self.write_status_calls = 0

        monkeypatch.setattr(app.auth, "create_session", lambda: self.session)
        monkeypatch.setattr(app, "_login_with_spinner", lambda session, timeout: True)
        monkeypatch.setattr(app, "fetch_balance", self._fetch_balance)
        monkeypatch.setattr(app, "fetch_summary_report", lambda session, settings: self.summary)
        monkeypatch.setattr(app, "create_dashboard", self._create_dashboard)
        monkeypatch.setattr(alerts, "_safe_notify", self._notify)

    def _fetch_balance(self, session, customer_id: str, timeout: int) -> BalanceResult:
        return self.balance_results.get(customer_id, (None, None, None, "no result"))

    def _create_dashboard(self, state, settings):
        self.states.append(state)
        return None

    def _notify(self, **kwargs) -> bool:
        self.notifications.append(kwargs)
        return True


def _run_cycles(monkeypatch: pytest.MonkeyPatch, portal: Portal, settings: Settings, n: int = 1) -> Runtime:
    runtime = Runtime()
    runtime.start()
    portal.write_status_calls = 0

    def fake_write_status(runtime_, settings_):
        portal.write_status_calls += 1
        if portal.write_status_calls >= n:
            runtime.shutdown.set()

    monkeypatch.setattr(app, "write_status", fake_write_status)
    app.monitor_loop(settings, {"quiet": True, "no_hot_reload": True}, runtime)
    return runtime


def test_monitor_loop_single_cycle(monkeypatch: pytest.MonkeyPatch, temp_db: str) -> None:
    portal = Portal(monkeypatch)
    portal.balance_results["18"] = ("TestCo", -100.0, 500.0, None)
    portal.summary = ({"18": {"name": "TestCo", "margin": 50.0, "billed_min": 100.0}}, None)

    runtime = _run_cycles(monkeypatch, portal, _settings(), 1)

    assert runtime.cycle_count == 1
    assert portal.session.close.called

    balance_rows = persistence.get_balance_history(customer_id="18", hours=24)
    assert len(balance_rows) == 1
    assert balance_rows[0]["balance"] == -100.0

    margin_rows = persistence.get_margin_history(customer_id="18", hours=24)
    assert len(margin_rows) == 1
    assert margin_rows[0]["margin"] == 50.0

    customers = portal.states[0].snapshot()["customers"]
    assert customers[0]["customer_id"] == "18"
    assert customers[0]["balance"] == -100.0


def test_monitor_loop_dedups_unchanged_values(monkeypatch: pytest.MonkeyPatch, temp_db: str) -> None:
    portal = Portal(monkeypatch)
    portal.balance_results["18"] = ("TestCo", -100.0, 500.0, None)
    portal.summary = ({}, None)

    runtime = _run_cycles(monkeypatch, portal, _settings(), 2)

    assert runtime.cycle_count == 2
    assert len(persistence.get_balance_history(customer_id="18", hours=24)) == 1


def test_balance_alert_fires_and_recovers(monkeypatch: pytest.MonkeyPatch, temp_db: str) -> None:
    portal = Portal(monkeypatch)
    portal.summary = ({}, None)

    portal.balance_results["18"] = ("TestCo", -600.0, 500.0, None)  # below -500 threshold
    _run_cycles(monkeypatch, portal, _settings(), 1)
    assert persistence.get_alert_state("18", "balance")[0] == 1
    assert portal.notifications

    portal.balance_results["18"] = ("TestCo", -100.0, 500.0, None)  # recovered
    _run_cycles(monkeypatch, portal, _settings(), 1)
    assert persistence.get_alert_state("18", "balance")[0] == 0


def test_monitor_loop_closes_session_when_setup_raises(monkeypatch: pytest.MonkeyPatch, temp_db: str) -> None:
    portal = Portal(monkeypatch)

    def boom(state, settings):
        raise RuntimeError("dashboard boom")

    monkeypatch.setattr(app, "create_dashboard", boom)
    written: list[int] = []
    monkeypatch.setattr(app, "write_status", lambda runtime_, settings_: written.append(1))

    runtime = Runtime()
    runtime.start()
    with pytest.raises(RuntimeError):
        app.monitor_loop(_settings(), {"quiet": True, "no_hot_reload": True}, runtime)

    assert portal.session.close.called
    assert written  # the finally block still wrote status


def test_run_once_persists_and_closes(monkeypatch: pytest.MonkeyPatch, temp_db: str) -> None:
    portal = Portal(monkeypatch)
    portal.balance_results["18"] = ("TestCo", -100.0, 500.0, None)
    portal.summary = ({"18": {"name": "TestCo", "margin": 50.0, "billed_min": 100.0}}, None)

    app.run_once(_settings())

    assert portal.session.close.called
    assert len(persistence.get_balance_history(customer_id="18", hours=24)) == 1
    assert len(persistence.get_margin_history(customer_id="18", hours=24)) == 1


def test_run_once_closes_on_summary_error(monkeypatch: pytest.MonkeyPatch, temp_db: str) -> None:
    portal = Portal(monkeypatch)
    portal.balance_results["18"] = ("TestCo", -100.0, 500.0, None)
    portal.summary = ({}, "boom")

    app.run_once(_settings())

    assert portal.session.close.called
    assert len(persistence.get_balance_history(customer_id="18", hours=24)) == 1


def test_run_once_closes_on_login_failure(monkeypatch: pytest.MonkeyPatch, temp_db: str) -> None:
    portal = Portal(monkeypatch)
    monkeypatch.setattr(app, "_login_with_spinner", lambda session, timeout: False)

    app.run_once(_settings())

    assert portal.session.close.called
    assert persistence.get_balance_history(customer_id="18", hours=24) == []


def test_main_exits_nonzero_on_corrupt_settings(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    bad = tmp_path / "settings.toml"
    bad.write_text("this is = not valid toml [[[", encoding="utf-8")
    monkeypatch.setattr(config, "SETTINGS_FILE", str(bad))
    monkeypatch.setattr(sys, "argv", ["monitor.py"])
    monkeypatch.setattr(app.display, "init", lambda: None)
    monkeypatch.setattr(app, "setup_logging", lambda: None)

    with pytest.raises(SystemExit) as exc:
        app.main()

    assert exc.value.code == 1
