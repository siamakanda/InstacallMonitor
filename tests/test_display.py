from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from display import fmt_billed_min, fmt_margin


class TestDisplay:
    def test_fmt_margin_with_value(self) -> None:
        assert fmt_margin(45.67) == "45.7%"

    def test_fmt_margin_none(self) -> None:
        assert fmt_margin(None) == "-"

    def test_fmt_billed_min_with_value(self) -> None:
        assert fmt_billed_min(1234.5) == "1234.5"

    def test_fmt_billed_min_none(self) -> None:
        assert fmt_billed_min(None) == "-"
