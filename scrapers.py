from __future__ import annotations

from typing import Optional

import requests

from config import Settings
from scraper_client import ScraperClient, _is_transient
from scraper_parser import (
    SUMMARY_COLUMN_MAP,
    BalanceResult,
    SummaryDict,
    parse_customer_page,
    parse_summary_page,
)

__all__ = [
    "SUMMARY_COLUMN_MAP",
    "BalanceResult",
    "ScraperClient",
    "SummaryDict",
    "_is_transient",
    "fetch_balance",
    "fetch_summary_report",
    "parse_customer_page",
    "parse_summary_page",
]

_client = ScraperClient()


def fetch_balance(session: requests.Session, customer_id: str, timeout: int = 10) -> BalanceResult:
    return _client.fetch_balance(session, customer_id, timeout)


def fetch_summary_report(session: requests.Session, settings: Settings) -> tuple[SummaryDict, Optional[str]]:
    return _client.fetch_summary_report(session, settings)
