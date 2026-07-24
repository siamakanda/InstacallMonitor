from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from alerts import SirenManager


class TestSirenManager:
    def test_cooldown_allows_first(self) -> None:
        mgr = SirenManager()
        assert mgr.can_alert("18", 300)

    def test_cooldown_blocks_within_window(self) -> None:
        mgr = SirenManager()
        assert mgr.can_alert("18", 300)
        assert not mgr.can_alert("18", 300)

    def test_different_customers_independent(self) -> None:
        mgr = SirenManager()
        assert mgr.can_alert("18", 300)
        assert mgr.can_alert("99", 300)

    def test_margin_cooldown_separate(self) -> None:
        mgr = SirenManager()
        assert mgr.can_alert("18", 300)
        assert mgr.can_margin_alert("18", 300)

    def test_zero_cooldown_allows_repeat(self) -> None:
        mgr = SirenManager()
        assert mgr.can_alert("18", 0)
        assert mgr.can_alert("18", 0)
