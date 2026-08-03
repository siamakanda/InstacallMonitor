from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from alerts import SirenManager, check_margin_alert
from config import GlobalSettings, Settings, WatchTarget


class TestSirenManager:
    def test_cooldown_allows_first(self) -> None:
        mgr = SirenManager()
        assert mgr.can_alert("18", "balance", 300)

    def test_cooldown_blocks_within_window(self) -> None:
        mgr = SirenManager()
        assert mgr.can_alert("18", "balance", 300)
        assert not mgr.can_alert("18", "balance", 300)

    def test_different_customers_independent(self) -> None:
        mgr = SirenManager()
        assert mgr.can_alert("18", "balance", 300)
        assert mgr.can_alert("99", "balance", 300)

    def test_different_types_independent(self) -> None:
        mgr = SirenManager()
        assert mgr.can_alert("18", "balance", 300)
        assert mgr.can_alert("18", "margin", 300)

    def test_zero_cooldown_allows_repeat(self) -> None:
        mgr = SirenManager()
        assert mgr.can_alert("18", "balance", 0)
        assert mgr.can_alert("18", "balance", 0)

    def test_play_steady_exists(self) -> None:
        mgr = SirenManager()
        assert hasattr(mgr, "play_steady")
        assert callable(mgr.play_steady)


class TestMarginAlert:
    DEFAULT_KW = dict(
        margin_below=30.0, margin_above=75.0, margin_deadband=3.0,
        cooldown=0, billed_above=0.0,
    )

    def _make_settings(self, **kw) -> Settings:
        merged = {**self.DEFAULT_KW, **kw}
        g = GlobalSettings(**merged)
        return Settings(global_=g, watch=[WatchTarget(customer="18", balance_below=-500.0)])

    def test_low_margin_triggers_alert(self) -> None:
        settings = self._make_settings()
        mgr = SirenManager()

        with patch("alerts.get_alert_state", return_value=(0, 0, "")), \
             patch("alerts.set_alert_state"), \
             patch("alerts._safe_notify"), \
             patch("alerts._play_siren_async") as mock_siren:
            check_margin_alert(mgr, "18", 22.0, 100.0, "TestCo", settings, monitored=True)
            mock_siren.assert_called_once()

    def test_high_margin_triggers_alert(self) -> None:
        settings = self._make_settings()
        mgr = SirenManager()

        with patch("alerts.get_alert_state", return_value=(0, 0, "")), \
             patch("alerts.set_alert_state"), \
             patch("alerts._safe_notify"), \
             patch("alerts._play_siren_async") as mock_siren:
            check_margin_alert(mgr, "18", 82.0, 100.0, "TestCo", settings, monitored=True)
            mock_siren.assert_called_once()

    def test_in_range_suppresses_alert(self) -> None:
        settings = self._make_settings()
        mgr = SirenManager()

        with patch("alerts.get_alert_state", return_value=(0, 0, "")), \
             patch("alerts.set_alert_state"), \
             patch("alerts._play_siren_async") as mock_siren:
            check_margin_alert(mgr, "18", 50.0, 100.0, "TestCo", settings, monitored=True)
            mock_siren.assert_not_called()

    def test_deadband_suppresses_alert_on_low_edge(self) -> None:
        settings = self._make_settings()
        mgr = SirenManager()

        with patch("alerts.get_alert_state", return_value=(0, 0, "")), \
             patch("alerts.set_alert_state"), \
             patch("alerts._play_siren_async") as mock_siren:
            check_margin_alert(mgr, "18", 31.0, 100.0, "TestCo", settings, monitored=True)
            mock_siren.assert_not_called()

    def test_deadband_suppresses_alert_on_high_edge(self) -> None:
        settings = self._make_settings()
        mgr = SirenManager()

        with patch("alerts.get_alert_state", return_value=(0, 0, "")), \
             patch("alerts.set_alert_state"), \
             patch("alerts._play_siren_async") as mock_siren:
            check_margin_alert(mgr, "18", 73.0, 100.0, "TestCo", settings, monitored=True)
            mock_siren.assert_not_called()

    def test_billed_above_filters_both_directions(self) -> None:
        settings = self._make_settings(billed_above=70.0)
        mgr = SirenManager()

        with patch("alerts.get_alert_state", return_value=(0, 0, "")), \
             patch("alerts._play_siren_async") as mock_siren:
            check_margin_alert(mgr, "18", 22.0, 50.0, "TestCo", settings, monitored=True)
            mock_siren.assert_not_called()

            check_margin_alert(mgr, "18", 82.0, 50.0, "TestCo", settings, monitored=True)
            mock_siren.assert_not_called()

    def test_recovery_when_back_in_range(self) -> None:
        settings = self._make_settings()
        mgr = SirenManager()

        with patch("alerts.get_alert_state", return_value=(1, 1, "low")), \
             patch("alerts.set_alert_state") as mock_set, \
             patch("alerts._safe_notify"):
            check_margin_alert(mgr, "18", 40.0, 100.0, "TestCo", settings, monitored=True)
            mock_set.assert_called_with("18", "margin", 0, count=0, direction="")

    def test_unmonitored_skips_alert(self) -> None:
        settings = self._make_settings()
        mgr = SirenManager()

        with patch("alerts._play_siren_async") as mock_siren:
            check_margin_alert(mgr, "18", 22.0, 100.0, "TestCo", settings, monitored=False)
            mock_siren.assert_not_called()
