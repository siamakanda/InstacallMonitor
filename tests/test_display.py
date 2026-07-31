from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from display import dim, green, magenta, red, yellow


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
