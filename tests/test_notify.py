from __future__ import annotations

import sys
import types

import pytest

from notify import safe_notify


def _fake_plyer(notify_fn) -> types.ModuleType:
    module = types.ModuleType("plyer")
    setattr(module, "notification", types.SimpleNamespace(notify=notify_fn))
    return module


def test_safe_notify_returns_true(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_notify(**kwargs):
        calls.append(kwargs)

    monkeypatch.setitem(sys.modules, "plyer", _fake_plyer(fake_notify))
    assert safe_notify("Title", "Message") is True
    assert calls and calls[0]["title"] == "Title"


def test_safe_notify_returns_false_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**kwargs):
        raise RuntimeError("no notification daemon")

    monkeypatch.setitem(sys.modules, "plyer", _fake_plyer(boom))
    assert safe_notify("Title", "Message") is False
