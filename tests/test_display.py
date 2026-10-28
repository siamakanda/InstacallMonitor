from __future__ import annotations

import pytest

import display
from display import dim, green, magenta, red, yellow


@pytest.fixture(autouse=True)
def _enable_colors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(display, "COLORS_ENABLED", True)


class TestDisplay:
    def test_red(self) -> None:
        assert "\033[31m" in red("test")

    def test_green(self) -> None:
        assert "\033[32m" in green("test")

    def test_yellow(self) -> None:
        assert "\033[33m" in yellow("test")

    def test_magenta(self) -> None:
        assert "\033[35m" in magenta("test")

    def test_dim(self) -> None:
        assert "\033[2m" in dim("test")

    def test_colors_disabled_strips_codes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(display, "COLORS_ENABLED", False)
        assert red("test") == "test"
