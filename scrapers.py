from __future__ import annotations

import logging
import random
import re
import time
import traceback
from datetime import date
from typing import Optional

import requests
from bs4 import BeautifulSoup, Tag

from auth import perform_login
from circuit_breaker import CircuitBreaker
from config import BASE_EDIT_URL, SUMMARY_REPORT_URL, GlobalSettings, Settings


def _needs_relogin(response: requests.Response) -> bool:
    return "login" in response.url.lower()

BalanceResult = tuple[Optional[str], Optional[float], Optional[float], Optional[str]]
SummaryDict = dict[str, dict[str, object]]

SUMMARY_COLUMN_MAP: dict[int, str] = {
    2: "calls",
    3: "connected",
    4: "asr",
    5: "aloc",
    7: "billed_min",
    8: "profit",
    12: "margin",
}

_RETRY_DELAYS = [2, 5]

_balance_breaker = CircuitBreaker(name="balance")
_summary_breaker = CircuitBreaker(name="summary_report")
_retry_settings: GlobalSettings | None = None


def configure_scrapers(settings: GlobalSettings) -> None:
    global _retry_settings, _balance_breaker, _summary_breaker
    _retry_settings = settings
    _balance_breaker.failure_threshold = settings.circuit_failure_threshold
    _balance_breaker.recovery_timeout = settings.circuit_recovery_timeout
    _summary_breaker.failure_threshold = settings.circuit_failure_threshold
    _summary_breaker.recovery_timeout = settings.circuit_recovery_timeout


def _compute_retry_delays() -> list[float]:
    if _retry_settings is None:
        return list(_RETRY_DELAYS)
    s = _retry_settings
    delays: list[float] = []
    for attempt in range(1, s.retry_max_attempts):
        delay = min(s.retry_base_delay * (2 ** (attempt - 1)), s.retry_max_delay)
        jitter = random.uniform(0, s.retry_jitter_max)
        delays.append(delay + jitter)
    return delays


def _is_transient(error: str | None) -> bool:
    if not error:
        return False
    e = str(error).lower()
    return "timeout" in e or "connection" in e


def parse_customer_page(html: str) -> BalanceResult:
    soup = BeautifulSoup(html, "html.parser")

    name_input = (
        soup.find("input", {"name": "name"})
        or soup.find("input", {"id": "name"})
        or soup.find("input", {"name": "customer_name"})
        or soup.find("input", {"name": "company"})
        or soup.find("input", {"id": "customer_name"})
    )
    customer_name = name_input.get("value", "N/A").strip() if name_input else "N/A"

    balance_input = soup.find("input", {"name": "balance"}) or soup.find("input", {"id": "balance"})
    balance: Optional[float] = None
    if balance_input and balance_input.get("value"):
        try:
            balance = float(balance_input["value"])
        except ValueError:
            return None, None, None, f"non-numeric balance: '{balance_input['value']}'"
    else:
        return None, None, None, "balance field not found"

    credit_input = (
        soup.find("input", {"name": "credit_limit"})
        or soup.find("input", {"id": "credit_limit"})
        or soup.find("input", {"name": "credit"})
        or soup.find("input", {"id": "credit"})
    )
    credit_limit: Optional[float] = None
    if credit_input and credit_input.get("value"):
        try:
            credit_limit = float(credit_input["value"])
        except ValueError:
            credit_limit = None

    return customer_name, balance, credit_limit, None


def _extract_cell_value(cell: Tag) -> Optional[float]:
    num_span = cell.find("span", class_="rpt-num")
    if num_span:
        text = num_span.get_text(strip=True)
        text = text.replace(",", "").replace("$", "").replace("s", "")
        try:
            return float(text)
        except ValueError:
            return None

    pill_span = cell.find("span", class_="rpt-asr-pill")
    if pill_span:
        text = pill_span.get_text(strip=True).replace("%", "")
        try:
            return float(text)
        except ValueError:
            return None

    plain = cell.get_text(strip=True)
    if plain:
        try:
            return float(plain.replace(",", "").replace("$", "").replace("s", ""))
        except ValueError:
            pass
    return None


def _detect_column_map_from_thead(cust_panel: Tag) -> dict[int, str]:
    thead = cust_panel.find("thead")
    if not thead:
        return {}
    header_row = thead.find("tr")
    if not header_row:
        return {}
    header_cells = header_row.find_all("th")
    if len(header_cells) < 4:
        return {}

    keyword_map: dict[str, str] = {
        "call": "calls", "calls": "calls",
        "connect": "connected", "connected": "connected",
        "asr": "asr",
        "aloc": "aloc",
        "billed": "billed_min", "billed min": "billed_min",
        "profit": "profit",
        "margin": "margin",
    }

    col_map: dict[int, str] = {}
    for i, th in enumerate(header_cells):
        text = th.get_text(strip=True).lower()
        for keyword, fname in keyword_map.items():
            if keyword in text and fname not in col_map.values():
                col_map[i] = fname
                break
    return col_map


def parse_summary_page(html: str) -> Optional[SummaryDict]:
    soup = BeautifulSoup(html, "html.parser")
    cust_panel = soup.find("div", id="panel-cust")
    if not cust_panel:
        return None
    tbody = cust_panel.find("tbody")
    if not tbody:
        return None

    col_map = _detect_column_map_from_thead(cust_panel)
    effective_map = col_map if col_map else dict(SUMMARY_COLUMN_MAP)

    results: SummaryDict = {}
    for row in tbody.find_all("tr", recursive=False):
        classes = row.get("class", [])
        if "sr-trunk-row" in classes:
            continue
        cells = row.find_all("td")
        if len(cells) < 13:
            continue
        vol_name = cells[1].find("span", class_="sr-vol-name")
        customer_name = vol_name.get_text(strip=True) if vol_name else "N/A"

        row_data: dict[str, object] = {"name": customer_name}

        for cell_idx, field_name in effective_map.items():
            if cell_idx < len(cells):
                val = _extract_cell_value(cells[cell_idx])
                if val is not None:
                    row_data[field_name] = val

        if "billed_min" not in row_data:
            billed_min: Optional[float] = None
            billed_span = cells[7].find("span", class_="rpt-num") if len(cells) > 7 else None
            if billed_span:
                try:
                    billed_min = float(billed_span.get_text(strip=True).replace(",", ""))
                except ValueError:
                    pass
            row_data["billed_min"] = billed_min

        if "margin" not in row_data:
            margin: Optional[float] = None
            margin_span = cells[12].find("span", class_="rpt-asr-pill") if len(cells) > 12 else None
            if margin_span:
                text = margin_span.get_text(strip=True).replace("%", "")
                try:
                    margin = float(text)
                except ValueError:
                    pass
            row_data["margin"] = margin

        expand_btn = cells[0].find("button", class_="sr-expand-btn")
        if expand_btn and expand_btn.get("onclick"):
            match = re.search(r"ct(\d+)", expand_btn.get("onclick", ""))
            if match:
                results[match.group(1)] = row_data

    return results


def fetch_balance(session: requests.Session, customer_id: str, timeout: int = 10) -> BalanceResult:
    edit_url = f"{BASE_EDIT_URL}{customer_id}"
    last_error: Optional[str] = None

    retry_delays = _compute_retry_delays()
    total_attempts = 1 + len(retry_delays)

    for attempt in range(total_attempts):
        if not _balance_breaker.before_request():
            last_error = f"circuit open (failures: {_balance_breaker.failure_count})"
            return None, None, None, last_error

        try:
            resp = session.get(edit_url, timeout=timeout, allow_redirects=True)
            if _needs_relogin(resp):
                logging.warning("Session expired. Re-logging in...")
                if not perform_login(session, timeout):
                    last_error = "re-login failed"
                    _balance_breaker.on_failure()
                    if attempt < len(retry_delays):
                        time.sleep(retry_delays[attempt])
                        continue
                    return None, None, None, last_error
                resp = session.get(edit_url, timeout=timeout, allow_redirects=True)
            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}"
                if attempt < len(retry_delays) and resp.status_code >= 500:
                    _balance_breaker.on_failure()
                    time.sleep(retry_delays[attempt])
                    continue
                if resp.status_code >= 500:
                    _balance_breaker.on_failure()
                return None, None, None, last_error
            _balance_breaker.on_success()
            return parse_customer_page(resp.text)
        except requests.exceptions.Timeout:
            last_error = f"timeout ({timeout}s)"
        except requests.exceptions.ConnectionError:
            last_error = "connection error"
        except Exception as e:
            logging.error(f"Customer {customer_id} - error: {e}\n{traceback.format_exc()}")
            last_error = str(e)

        _balance_breaker.on_failure()
        if attempt < len(retry_delays) and _is_transient(last_error):
            time.sleep(retry_delays[attempt])

    return None, None, None, last_error


def fetch_summary_report(session: requests.Session, settings: Settings) -> tuple[SummaryDict, Optional[str]]:
    g = settings.global_
    today = date.today().isoformat()
    timeout = g.request_timeout or 10
    params = {
        "direction": g.summary_direction,
        "interval": g.summary_interval,
        "date_from": today,
        "date_to": today,
    }
    last_error: Optional[str] = None

    retry_delays = _compute_retry_delays()
    total_attempts = 1 + len(retry_delays)

    for attempt in range(total_attempts):
        if not _summary_breaker.before_request():
            last_error = f"circuit open (failures: {_summary_breaker.failure_count})"
            return {}, last_error

        try:
            resp = session.get(SUMMARY_REPORT_URL, params=params, timeout=timeout, allow_redirects=True)
        except requests.exceptions.Timeout:
            last_error = f"timeout ({timeout}s)"
            _summary_breaker.on_failure()
            if attempt < len(retry_delays):
                time.sleep(retry_delays[attempt])
                continue
            return {}, last_error
        except requests.exceptions.ConnectionError:
            last_error = "connection error"
            _summary_breaker.on_failure()
            if attempt < len(retry_delays):
                time.sleep(retry_delays[attempt])
                continue
            return {}, last_error
        except Exception as e:
            logging.error(f"Summary - error: {e}\n{traceback.format_exc()}")
            _summary_breaker.on_failure()
            return {}, str(e)

        if _needs_relogin(resp):
            logging.warning("Session expired. Re-logging in...")
            if not perform_login(session, timeout):
                last_error = "re-login failed"
                _summary_breaker.on_failure()
                if attempt < len(retry_delays):
                    time.sleep(retry_delays[attempt])
                    continue
                return {}, last_error
            resp = session.get(SUMMARY_REPORT_URL, params=params, timeout=timeout, allow_redirects=True)

        if resp.status_code != 200:
            last_error = f"HTTP {resp.status_code}"
            if attempt < len(retry_delays) and resp.status_code >= 500:
                _summary_breaker.on_failure()
                time.sleep(retry_delays[attempt])
                continue
            if resp.status_code >= 500:
                _summary_breaker.on_failure()
            return {}, last_error

        try:
            results = parse_summary_page(resp.text)
        except Exception as e:
            last_error = f"parse error: {e}"
            logging.error(f"Summary parse error: {e}")
            if attempt < len(retry_delays):
                time.sleep(retry_delays[attempt])
                continue
            return {}, last_error

        if results is None:
            if attempt < len(retry_delays):
                time.sleep(retry_delays[attempt])
                continue
            return {}, "failed to parse summary page"

        _summary_breaker.on_success()
        logging.info(f"Summary - {len(results)} customers found.")
        return results, None

    return {}, last_error
