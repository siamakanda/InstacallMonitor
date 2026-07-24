from __future__ import annotations

import logging
import re
import traceback
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import requests
from bs4 import BeautifulSoup, Tag

from auth import ensure_authenticated
from config import BASE_EDIT_URL, SUMMARY_REPORT_URL, Settings
from retry import retry_with_backoff

BalanceResult = tuple[Optional[str], Optional[float], Optional[float], Optional[str]]
SummaryResult = tuple[dict[str, dict[str, object]], Optional[str]]

SUMMARY_COLUMN_MAP: dict[int, str] = {
    2: "calls",
    3: "connected",
    4: "asr",
    5: "aloc",
    7: "billed_min",
    8: "profit",
    12: "margin",
}


@dataclass
class SummaryRow:
    customer_id: str
    customer_name: str
    calls: Optional[float] = None
    connected: Optional[float] = None
    asr: Optional[float] = None
    aloc: Optional[float] = None
    billed_min: Optional[float] = None
    profit: Optional[float] = None
    margin: Optional[float] = None
    balance: Optional[float] = None
    raw_cells: dict[int, str] = field(default_factory=dict)

    @classmethod
    def from_summary_dict(cls, customer_id: str, data: dict[str, object]) -> SummaryRow:
        return cls(
            customer_id=customer_id,
            customer_name=str(data.get("name", "")),
            calls=_as_float(data.get("calls")),
            connected=_as_float(data.get("connected")),
            asr=_as_float(data.get("asr")),
            aloc=_as_float(data.get("aloc")),
            billed_min=_as_float(data.get("billed_min")),
            profit=_as_float(data.get("profit")),
            margin=_as_float(data.get("margin")),
        )


def _as_float(val: object) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def parse_customer_page(html: str) -> BalanceResult:
    soup = BeautifulSoup(html, 'html.parser')

    name_input = (
        soup.find('input', {'name': 'name'})
        or soup.find('input', {'id': 'name'})
        or soup.find('input', {'name': 'customer_name'})
        or soup.find('input', {'name': 'company'})
        or soup.find('input', {'id': 'customer_name'})
    )
    customer_name = name_input.get('value', 'N/A').strip() if name_input else 'N/A'

    balance_input = soup.find('input', {'name': 'balance'}) or soup.find('input', {'id': 'balance'})
    balance: Optional[float] = None
    if balance_input and balance_input.get('value'):
        try:
            balance = float(balance_input['value'])
        except ValueError:
            return None, None, None, f"non-numeric balance: '{balance_input['value']}'"
    else:
        return None, None, None, "balance field not found"

    credit_input = (
        soup.find('input', {'name': 'credit_limit'})
        or soup.find('input', {'id': 'credit_limit'})
        or soup.find('input', {'name': 'credit'})
        or soup.find('input', {'id': 'credit'})
    )
    credit_limit: Optional[float] = None
    if credit_input and credit_input.get('value'):
        try:
            credit_limit = float(credit_input['value'])
        except ValueError:
            credit_limit = None

    return customer_name, balance, credit_limit, None


def _extract_cell_value(cell: Tag) -> Optional[float]:
    num_span = cell.find('span', class_='rpt-num')
    if num_span:
        text = num_span.get_text(strip=True)
        text = text.replace(",", "").replace("$", "").replace("s", "")
        try:
            return float(text)
        except ValueError:
            return None

    pill_span = cell.find('span', class_='rpt-asr-pill')
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
    thead = cust_panel.find('thead')
    if not thead:
        return {}
    header_row = thead.find('tr')
    if not header_row:
        return {}
    header_cells = header_row.find_all('th')
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


def parse_summary_page(html: str) -> Optional[dict[str, dict[str, object]]]:
    soup = BeautifulSoup(html, 'html.parser')
    cust_panel = soup.find('div', id='panel-cust')
    if not cust_panel:
        return None
    tbody = cust_panel.find('tbody')
    if not tbody:
        return None

    col_map = _detect_column_map_from_thead(cust_panel)
    effective_map = col_map if col_map else dict(SUMMARY_COLUMN_MAP)

    results: dict[str, dict[str, object]] = {}
    for row in tbody.find_all('tr', recursive=False):
        classes = row.get('class', [])
        if 'sr-trunk-row' in classes:
            continue
        cells = row.find_all('td')
        if len(cells) < 13:
            continue
        vol_name = cells[1].find('span', class_='sr-vol-name')
        customer_name = vol_name.get_text(strip=True) if vol_name else 'N/A'

        billed_min: Optional[float] = None
        billed_span = cells[7].find('span', class_='rpt-num')
        if billed_span:
            try:
                billed_min = float(billed_span.get_text(strip=True).replace(',', ''))
            except ValueError:
                pass

        margin: Optional[float] = None
        margin_span = cells[12].find('span', class_='rpt-asr-pill')
        if margin_span:
            text = margin_span.get_text(strip=True).replace('%', '')
            try:
                margin = float(text)
            except ValueError:
                pass

        row_data: dict[str, object] = {
            'name': customer_name,
            'margin': margin,
            'billed_min': billed_min,
        }

        for cell_idx, field_name in effective_map.items():
            if field_name in ("billed_min", "margin"):
                continue
            if cell_idx < len(cells):
                val = _extract_cell_value(cells[cell_idx])
                if val is not None:
                    row_data[field_name] = val

        expand_btn = cells[0].find('button', class_='sr-expand-btn')
        cust_id_from_html: Optional[str] = None
        if expand_btn and expand_btn.get('onclick'):
            match = re.search(r"ct(\d+)", expand_btn.get('onclick', ''))
            if match:
                cust_id_from_html = match.group(1)

        if cust_id_from_html:
            results[cust_id_from_html] = row_data

    return results


def _extract_balance_error(result: BalanceResult) -> Optional[str]:
    return result[3] if result and len(result) == 4 else None


def _extract_summary_error(result: SummaryResult) -> Optional[str]:
    return result[1] if result and len(result) == 2 else None


def fetch_balance(session: requests.Session, customer_id: str, timeout: int = 10) -> BalanceResult:
    return retry_with_backoff(_do_fetch_balance, _extract_balance_error, session, customer_id, timeout)


def _do_fetch_balance(session: requests.Session, customer_id: str, timeout: int = 10) -> BalanceResult:
    edit_url = f"{BASE_EDIT_URL}{customer_id}"
    try:
        resp = session.get(edit_url, timeout=timeout, allow_redirects=True)
        if not ensure_authenticated(session, resp, timeout):
            return None, None, None, "re-login failed"
        if "login" in resp.url.lower():
            try:
                resp = session.get(edit_url, timeout=timeout, allow_redirects=True)
            except requests.exceptions.Timeout:
                return None, None, None, f"timeout after re-login ({timeout}s)"
            except requests.exceptions.ConnectionError:
                return None, None, None, "connection error after re-login"
            except Exception as e:
                return None, None, None, str(e)
            if not ensure_authenticated(session, resp, timeout):
                return None, None, None, "re-login failed on second attempt"
        if resp.status_code != 200:
            return None, None, None, f"HTTP {resp.status_code}"
        return parse_customer_page(resp.text)
    except requests.exceptions.Timeout:
        return None, None, None, f"timeout ({timeout}s)"
    except requests.exceptions.ConnectionError:
        return None, None, None, "connection error"
    except Exception as e:
        logging.error(f"Customer {customer_id} - Unexpected error: {e}\n{traceback.format_exc()}")
        return None, None, None, str(e)


def fetch_summary_report(session: requests.Session, settings: Settings) -> tuple[dict[str, dict[str, object]], Optional[str]]:
    result = retry_with_backoff(_do_fetch_summary, _extract_summary_error, session, settings)
    return result


def summaries_to_rows(summary_dict: dict[str, dict[str, object]]) -> list[SummaryRow]:
    return [SummaryRow.from_summary_dict(cid, data) for cid, data in summary_dict.items()]


def _do_fetch_summary(session: requests.Session, settings: Settings) -> SummaryResult:
    g = settings.global_
    today = date.today().isoformat()
    timeout = g.request_timeout or 10
    params = {
        "direction": g.summary_direction,
        "interval": g.summary_interval,
        "date_from": today,
        "date_to": today,
    }
    error: Optional[str] = None

    try:
        resp = session.get(SUMMARY_REPORT_URL, params=params, timeout=timeout, allow_redirects=True)
    except requests.exceptions.Timeout:
        error = f"timeout ({timeout}s)"
        logging.error(f"Summary report - {error}")
        return {}, error
    except requests.exceptions.ConnectionError:
        error = "connection error"
        logging.error(f"Summary report - {error}")
        return {}, error
    except Exception as e:
        error = str(e)
        logging.error(f"Summary report - unexpected error: {e}\n{traceback.format_exc()}")
        return {}, error

    if not ensure_authenticated(session, resp, timeout):
        error = "re-login failed"
        return {}, error

    if "login" in resp.url.lower():
        try:
            resp = session.get(SUMMARY_REPORT_URL, params=params, timeout=timeout, allow_redirects=True)
        except requests.exceptions.Timeout:
            error = f"timeout after re-login ({timeout}s)"
            return {}, error
        except requests.exceptions.ConnectionError:
            error = "connection error after re-login"
            return {}, error
        except Exception as e:
            error = f"unexpected error after re-login: {e}"
            logging.error(f"Summary report - {error}")
            return {}, error
        if not ensure_authenticated(session, resp, timeout):
            error = "re-login failed on second attempt"
            return {}, error

    if resp.status_code != 200:
        error = f"HTTP {resp.status_code}"
        logging.error(f"Summary report returned {error}")
        return {}, error

    try:
        results = parse_summary_page(resp.text)
    except Exception as e:
        error = f"parse error: {e}"
        logging.error(f"Summary report - {error}\n{traceback.format_exc()}")
        return {}, error

    if results is None:
        error = "failed to parse summary page"
        return {}, error

    logging.info(f"Summary report parsed - {len(results)} customers found.")
    return results, None
