from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import GlobalSettings, Settings, WatchTarget, validate_settings


class TestGlobalSettings:
    def test_default_global(self) -> None:
        g = GlobalSettings()
        assert g.check_interval == 600
        assert g.margin_below == 30.0
        assert g.margin_critical == 25.0
        assert g.cooldown == 300
        assert g.audio is True
        assert g.billed_above == 70.0
        assert g.webhook_type == "none"


class TestWatchTarget:
    def test_resolve_uses_default(self) -> None:
        w = WatchTarget(customer="18", balance_below=-500.0)
        assert w.resolve_balance_below() == -500.0
        assert w.resolve_margin_below(30.0) == 30.0

    def test_resolve_uses_override(self) -> None:
        w = WatchTarget(customer="18", balance_below=-600.0, margin_below=25.0)
        assert w.resolve_balance_below() == -600.0
        assert w.resolve_margin_below(30.0) == 25.0

    def test_resolve_balance_critical_defaults_to_below(self) -> None:
        w = WatchTarget(customer="18", balance_below=-500.0)
        assert w.resolve_balance_critical() == -500.0

    def test_resolve_balance_critical_explicit(self) -> None:
        w = WatchTarget(customer="18", balance_below=-500.0, balance_critical=-600.0)
        assert w.resolve_balance_critical() == -600.0

    def test_resolve_mixed(self) -> None:
        w = WatchTarget(customer="18", balance_below=-600.0)
        assert w.resolve_balance_below() == -600.0
        assert w.resolve_margin_below(30.0) == 30.0


class TestSettings:
    def test_default_settings(self) -> None:
        s = Settings(watch=[WatchTarget(customer="18", balance_below=-365.0)])
        assert s.customer_ids == ["18"]
        assert s.global_.check_interval == 600
        assert s.global_.margin_below == 30.0
        assert s.global_.margin_critical == 25.0
        assert s.global_.cooldown == 300
        assert s.global_.webhook_type == "none"

    def test_from_dict(self) -> None:
        data = {
            "settings": {"check_interval": 120},
            "watch": [
                {"customer": "42", "balance_below": -500.0},
                {"customer": "99", "balance_below": -200.0, "margin_below": 25.0},
            ],
        }
        s = Settings.from_dict(data)
        assert s.customer_ids == ["42", "99"]
        assert s.global_.check_interval == 120
        w99 = s.get_watch("99")
        assert w99.resolve_balance_below() == -200.0
        assert w99.resolve_margin_below(30.0) == 25.0

    def test_to_dict_roundtrip(self) -> None:
        g = GlobalSettings(check_interval=60)
        s = Settings(
            global_=g,
            watch=[WatchTarget(customer="18", balance_below=-500.0)],
        )
        d = s.to_dict()
        s2 = Settings.from_dict(d)
        assert s2.customer_ids == s.customer_ids
        assert s2.global_.check_interval == s.global_.check_interval

    def test_get_watch_returns_defaults(self) -> None:
        s = Settings(watch=[WatchTarget(customer="18", balance_below=-365.0)])
        w = s.get_watch("99")
        assert w.customer == "99"
        assert w.balance_below is None

    def test_validate_valid(self) -> None:
        s = Settings(watch=[WatchTarget(customer="18", balance_below=-365.0)])
        assert validate_settings(s) == []

    def test_validate_empty_watch(self) -> None:
        errors = validate_settings(Settings())
        assert any("watch" in e.lower() for e in errors)

    def test_validate_missing_balance_below(self) -> None:
        s = Settings(watch=[WatchTarget(customer="1")])
        errors = validate_settings(s)
        assert any("balance_below" in e for e in errors)

    def test_validate_bad_direction(self) -> None:
        g = GlobalSettings(summary_direction="invalid")
        s = Settings(global_=g, watch=[WatchTarget(customer="1", balance_below=-365.0)])
        errors = validate_settings(s)
        assert any("summary_direction" in e for e in errors)

    def test_validate_bad_webhook_type(self) -> None:
        g = GlobalSettings(webhook_type="discord")
        s = Settings(global_=g, watch=[WatchTarget(customer="1", balance_below=-365.0)])
        errors = validate_settings(s)
        assert any("webhook_type" in e for e in errors)

    def test_validate_negative_cooldown(self) -> None:
        g = GlobalSettings(cooldown=-1)
        s = Settings(global_=g, watch=[WatchTarget(customer="1", balance_below=-365.0)])
        errors = validate_settings(s)
        assert any("cooldown" in e for e in errors)

    def test_validate_margin_critical_not_worse(self) -> None:
        g = GlobalSettings(margin_below=30.0, margin_critical=40.0)
        s = Settings(global_=g, watch=[WatchTarget(customer="1", balance_below=-365.0)])
        errors = validate_settings(s)
        assert any("margin_critical" in e for e in errors)

    def test_validate_margin_critical_equal_is_valid(self) -> None:
        g = GlobalSettings(margin_below=30.0, margin_critical=30.0)
        s = Settings(global_=g, watch=[WatchTarget(customer="1", balance_below=-365.0)])
        assert validate_settings(s) == []
