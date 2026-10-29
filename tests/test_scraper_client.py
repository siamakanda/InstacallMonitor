from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

import scraper_client
from scraper_client import ScraperClient


def _resp(status_code: int, url: str = "https://example.com/customers?edit=18") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.url = url
    resp.text = ""
    return resp


def test_timeout_retries_with_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scraper_client, "_RETRY_ATTEMPTS", 3)
    monkeypatch.setattr(scraper_client, "_RETRY_BASE_DELAY", 0.1)
    monkeypatch.setattr(scraper_client, "_RETRY_MAX_DELAY", 1.0)
    monkeypatch.setattr(scraper_client, "_RETRY_JITTER_MAX", 0.0)
    client = ScraperClient()
    session = MagicMock()
    session.get.side_effect = requests.exceptions.Timeout()
    sleeps: list[float] = []
    monkeypatch.setattr(scraper_client.time, "sleep", lambda s: sleeps.append(s))

    _, _, _, error = client.fetch_balance(session, "18", 10)

    assert error is not None and "timeout" in error
    assert session.get.call_count == 3  # 1 attempt + 2 retries
    assert len(sleeps) == 2  # transient errors back off between retries


def test_client_error_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scraper_client, "_RETRY_ATTEMPTS", 3)
    client = ScraperClient()
    session = MagicMock()
    session.get.return_value = _resp(404)
    monkeypatch.setattr(scraper_client.time, "sleep", lambda s: None)

    _, _, _, error = client.fetch_balance(session, "18", 10)

    assert error is not None and "HTTP 404" in error
    assert session.get.call_count == 1


def test_half_open_recovers_after_client_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ScraperClient()
    client._balance_breaker.failure_threshold = 2
    client._balance_breaker.recovery_timeout = 0.0
    session = MagicMock()
    session.get.return_value = _resp(404)
    monkeypatch.setattr(scraper_client.time, "sleep", lambda s: None)

    client._balance_breaker.on_failure()
    client._balance_breaker.on_failure()  # trip to OPEN

    _, _, _, first = client.fetch_balance(session, "18", 10)
    assert first is not None and "HTTP 404" in first

    # A 404 during the half-open probe must record an outcome, not latch forever.
    _, _, _, second = client.fetch_balance(session, "18", 10)
    assert "circuit open" not in (second or "")
