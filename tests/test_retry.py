from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from retry import _is_transient


class TestRetry:
    def test_is_transient_none(self) -> None:
        assert not _is_transient(None)

    def test_is_transient_empty(self) -> None:
        assert not _is_transient("")

    def test_is_transient_timeout(self) -> None:
        assert _is_transient("timeout (10s)")
        assert _is_transient("Timeout error")
        assert _is_transient("connection timed out")

    def test_is_transient_connection(self) -> None:
        assert _is_transient("connection error")
        assert _is_transient("Connection refused")

    def test_is_transient_other(self) -> None:
        assert not _is_transient("HTTP 404")
        assert not _is_transient("balance field not found")
