from __future__ import annotations

import pytest

import credentials
from credentials import get_credentials


def test_missing_credentials_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials, "_ENV_LOADED", True)
    monkeypatch.delenv("PORTAL_USERNAME", raising=False)
    monkeypatch.delenv("PORTAL_PASSWORD", raising=False)
    with pytest.raises(ValueError) as exc:
        get_credentials()
    assert "PORTAL_PASSWORD" in str(exc.value)


def test_credentials_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials, "_ENV_LOADED", True)
    monkeypatch.setenv("PORTAL_USERNAME", "user")
    monkeypatch.setenv("PORTAL_PASSWORD", "secret")
    assert get_credentials() == ("user", "secret")
