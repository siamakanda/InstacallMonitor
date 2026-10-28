from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import config
import persistence
from config import GlobalSettings, Settings, WatchTarget
from webapp import MonitorState, create_dashboard


@pytest.fixture
def temp_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(config, "SETTINGS_FILE", str(tmp_path / "settings.toml"))
    monkeypatch.setattr(config, "_BASE_DIR", tmp_path)
    config.save_settings(
        Settings(
            global_=GlobalSettings(),
            watch=[WatchTarget(customer="18", balance_below=-500.0)],
        )
    )
    return tmp_path / "settings.toml"


def _fetch_json(url: str) -> tuple[int, dict]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: object, headers: dict | None = None) -> tuple[int, dict]:
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


class TestMonitorState:
    def test_snapshot_includes_customers(self) -> None:
        state = MonitorState()
        state.update_customer("18", name="TestCo", balance=-10.0)
        snapshot = state.snapshot()
        assert snapshot["alive"] is True
        assert snapshot["customers"][0]["customer_id"] == "18"
        assert snapshot["customers"][0]["balance"] == -10.0

    def test_update_merges_fields(self) -> None:
        state = MonitorState()
        state.update_customer("18", balance=-10.0)
        state.update_customer("18", margin=52.9)
        entry = state.snapshot()["customers"][0]
        assert entry["balance"] == -10.0
        assert entry["margin"] == 52.9

    def test_set_cycle(self) -> None:
        state = MonitorState()
        state.set_cycle(cycle_count=7, interval=30, alive=False)
        snapshot = state.snapshot()
        assert snapshot["cycle_count"] == 7
        assert snapshot["interval"] == 30
        assert snapshot["alive"] is False

    def test_reload_signalling(self) -> None:
        state = MonitorState()
        assert state.reload_requested() is False
        state.request_reload()
        assert state.reload_requested() is True
        assert state.reload_event.is_set()
        state.clear_reload()
        assert state.reload_requested() is False

    def test_stores_unmonitored_customers(self) -> None:
        state = MonitorState()
        state.update_customer("173", name="BALI TECH", monitored=False)
        entry = state.snapshot()["customers"][0]
        assert entry["customer_id"] == "173"
        assert entry["monitored"] is False


class TestDashboard:
    def _settings(self, port: int = 0) -> Settings:
        return Settings(
            global_=GlobalSettings(web_dashboard=True, web_dashboard_port=port),
            watch=[WatchTarget(customer="18", balance_below=-500.0)],
        )

    def test_disabled_returns_none(self) -> None:
        settings = Settings(
            global_=GlobalSettings(web_dashboard=False),
            watch=[WatchTarget(customer="18", balance_below=-500.0)],
        )
        assert create_dashboard(MonitorState(), settings) is None

    def test_endpoints(self, temp_db: str) -> None:
        persistence.insert_balance(
            persistence.BalanceRecord(
                customer_id="18",
                customer_name="TestCo",
                balance=-10.0,
                credit_limit=600.0,
                remaining=590.0,
            )
        )
        state = MonitorState()
        state.update_customer("18", name="TestCo", balance=-10.0, margin=52.9)
        handle = create_dashboard(state, self._settings())
        assert handle is not None
        try:
            base = handle.url
            status, data = _fetch_json(base + "/api/status")
            assert status == 200
            assert data["customers"][0]["customer_id"] == "18"

            _, data = _fetch_json(base + "/api/customers")
            assert data["customers"][0]["name"] == "TestCo"

            _, data = _fetch_json(base + "/api/history?customer_id=18&hours=24")
            assert len(data["balance"]) == 1
            assert data["balance"][0]["balance"] == -10.0
            assert "margin" in data

            _, data = _fetch_json(base + "/api/alerts")
            assert "alerts" in data

            with urllib.request.urlopen(base + "/", timeout=5) as response:
                html = response.read().decode("utf-8")
            assert "InstacallMonitor" in html
        finally:
            handle.stop()

    def test_unknown_path_returns_404(self, temp_db: str) -> None:
        handle = create_dashboard(MonitorState(), self._settings())
        assert handle is not None
        try:
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(handle.url + "/nope", timeout=5)
            assert exc.value.code == 404
        finally:
            handle.stop()

    def test_history_hours_clamped(self, temp_db: str) -> None:
        handle = create_dashboard(MonitorState(), self._settings())
        assert handle is not None
        try:
            status, data = _fetch_json(handle.url + "/api/history?customer_id=18&hours=99999999")
            assert status == 200
            assert data["ok"] is True
        finally:
            handle.stop()

    def test_security_headers_present(self, temp_db: str) -> None:
        handle = create_dashboard(MonitorState(), self._settings())
        assert handle is not None
        try:
            with urllib.request.urlopen(handle.url + "/api/status", timeout=5) as response:
                assert response.headers.get("X-Content-Type-Options") == "nosniff"
                assert response.headers.get("X-Frame-Options") == "DENY"
        finally:
            handle.stop()


class TestSettingsEditor:
    def _start(self, state: MonitorState):
        settings = Settings(
            global_=GlobalSettings(web_dashboard=True, web_dashboard_port=0),
            watch=[WatchTarget(customer="18", balance_below=-500.0)],
        )
        handle = create_dashboard(state, settings)
        assert handle is not None
        return handle

    def _payload(self, handle) -> dict:
        payload = Settings(
            global_=GlobalSettings(),
            watch=[WatchTarget(customer="18", balance_below=-500.0)],
        ).to_dict()
        payload["settings"]["web_dashboard_port"] = int(handle.url.rsplit(":", 1)[1])
        return payload

    def test_get_settings(self, temp_settings: Path) -> None:
        handle = self._start(MonitorState())
        try:
            status, data = _fetch_json(handle.url + "/api/settings")
            assert status == 200
            assert data["settings"]["settings"]["margin_below"] == 30.0
            assert data["settings"]["watch"][0]["customer"] == "18"
        finally:
            handle.stop()

    def test_post_valid_persists_and_requests_reload(self, temp_settings: Path) -> None:
        state = MonitorState()
        handle = self._start(state)
        try:
            payload = self._payload(handle)
            payload["settings"]["margin_below"] = 22.0
            status, data = _post_json(handle.url + "/api/settings", payload)
            assert status == 200
            assert data["ok"] is True
            assert state.reload_requested() is True
            assert config.read_settings().global_.margin_below == 22.0
        finally:
            handle.stop()

    def test_post_invalid_rejected_without_writing(self, temp_settings: Path) -> None:
        handle = self._start(MonitorState())
        try:
            payload = self._payload(handle)
            payload["settings"]["margin_below"] = 90.0
            status, data = _post_json(handle.url + "/api/settings", payload)
            assert status == 400
            assert data["ok"] is False
            assert data["errors"]
            assert config.read_settings().global_.margin_below == 30.0
        finally:
            handle.stop()

    def test_post_wrong_content_type_rejected(self, temp_settings: Path) -> None:
        handle = self._start(MonitorState())
        try:
            status, _ = _post_json(handle.url + "/api/settings", self._payload(handle), {"Content-Type": "text/plain"})
            assert status == 415
        finally:
            handle.stop()

    def test_post_cross_origin_rejected(self, temp_settings: Path) -> None:
        handle = self._start(MonitorState())
        try:
            status, _ = _post_json(
                handle.url + "/api/settings", self._payload(handle), {"Origin": "http://evil.example"}
            )
            assert status == 403
        finally:
            handle.stop()

    def test_post_port_change_warns(self, temp_settings: Path) -> None:
        handle = self._start(MonitorState())
        try:
            payload = self._payload(handle)
            payload["settings"]["web_dashboard_port"] = 65000
            status, data = _post_json(handle.url + "/api/settings", payload)
            assert status == 200
            assert any("restart" in w.lower() for w in data["warnings"])
        finally:
            handle.stop()

    def test_empty_text_field_is_validated_not_silently_dropped(self, temp_settings: Path) -> None:
        handle = self._start(MonitorState())
        try:
            payload = self._payload(handle)
            payload["settings"]["web_dashboard_host"] = ""
            status, data = _post_json(handle.url + "/api/settings", payload)
            assert status == 400
            assert any("web_dashboard_host" in e for e in data["errors"])
        finally:
            handle.stop()

    def test_get_settings_includes_ok(self, temp_settings: Path) -> None:
        handle = self._start(MonitorState())
        try:
            status, data = _fetch_json(handle.url + "/api/settings")
            assert status == 200
            assert data["ok"] is True
        finally:
            handle.stop()
