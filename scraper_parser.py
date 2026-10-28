from __future__ import annotations

import logging
import re
from typing import Optional, TypeGuard

from bs4 import BeautifulSoup, Tag

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

_Selector = tuple[str, dict[str, str]]
_UNICODE_MINUS = "\u2212"
_NBSP = "\xa0"


def _find(soup: BeautifulSoup, selectors: list[_Selector]) -> Optional[Tag]:
    for name, attrs in selectors:
        found = soup.find(name, attrs)
        if isinstance(found, Tag):
            return found
    return None


def _attr_str(tag: Optional[Tag], name: str) -> str:
    if tag is None:
        return ""
    value = tag.get(name)
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value:
        return str(value[0])
    return ""


def _parse_number(text: str) -> Optional[float]:
    if not text:
        return None
    cleaned = (
        text.replace(",", "").replace("$", "").replace("%", "").replace(_NBSP, "").replace(_UNICODE_MINUS, "-").strip()
    )
    if not cleaned:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def _is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _span_number(cells: list[Tag], idx: int, class_name: str) -> Optional[float]:
    if idx >= len(cells):
        return None
    span = cells[idx].find("span", class_=class_name)
    if not isinstance(span, Tag):
        return None
    return _parse_number(span.get_text(strip=True))


def parse_customer_page(html: str) -> BalanceResult:
    soup = BeautifulSoup(html, "html.parser")

    name_input = _find(
        soup,
        [
            ("input", {"name": "name"}),
            ("input", {"id": "name"}),
            ("input", {"name": "customer_name"}),
            ("input", {"name": "company"}),
            ("input", {"id": "customer_name"}),
        ],
    )
    name_value = _attr_str(name_input, "value").strip()
    if not name_value:
        logging.warning("Customer page: name field missing or empty; using 'N/A'")
    customer_name = name_value or "N/A"

    balance_input = _find(
        soup,
        [
            ("input", {"name": "balance"}),
            ("input", {"id": "balance"}),
        ],
    )
    balance_raw = _attr_str(balance_input, "value")
    if not balance_raw:
        return None, None, None, "balance field not found"
    balance = _parse_number(balance_raw)
    if balance is None:
        return None, None, None, f"non-numeric balance: '{balance_raw}'"

    credit_input = _find(
        soup,
        [
            ("input", {"name": "credit_limit"}),
            ("input", {"id": "credit_limit"}),
            ("input", {"name": "credit"}),
            ("input", {"id": "credit"}),
        ],
    )
    credit_raw = _attr_str(credit_input, "value")
    credit_limit: Optional[float] = None
    if credit_raw:
        credit_limit = _parse_number(credit_raw)
        if credit_limit is not None and credit_limit < 0:
            logging.warning("Customer page: negative credit_limit %s; ignoring", credit_limit)
            credit_limit = None

    return customer_name, balance, credit_limit, None


def _extract_cell_value(cell: Tag) -> Optional[float]:
    num_span = cell.find("span", class_="rpt-num")
    if isinstance(num_span, Tag):
        return _parse_number(num_span.get_text(strip=True))

    pill_span = cell.find("span", class_="rpt-asr-pill")
    if isinstance(pill_span, Tag):
        return _parse_number(pill_span.get_text(strip=True))

    plain = cell.get_text(strip=True)
    if plain:
        return _parse_number(plain)
    return None


def _detect_column_map_from_thead(cust_panel: Tag) -> dict[int, str]:
    thead = cust_panel.find("thead")
    if not isinstance(thead, Tag):
        return {}
    header_row = thead.find("tr")
    if not isinstance(header_row, Tag):
        return {}
    header_cells = header_row.find_all("th")
    if len(header_cells) < 4:
        return {}

    keyword_map: dict[str, str] = {
        "call": "calls",
        "calls": "calls",
        "connect": "connected",
        "connected": "connected",
        "asr": "asr",
        "aloc": "aloc",
        "billed": "billed_min",
        "billed min": "billed_min",
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


def _validate_summary_row(customer_id: str, row_data: dict[str, object]) -> None:
    margin = row_data.get("margin")
    if _is_number(margin) and not (0 <= float(margin) <= 100):
        logging.warning("Summary: customer %s margin %s outside 0-100; ignoring", customer_id, margin)
        row_data["margin"] = None
    billed = row_data.get("billed_min")
    if _is_number(billed) and float(billed) < 0:
        logging.warning("Summary: customer %s billed_min %s is negative; ignoring", customer_id, billed)
        row_data["billed_min"] = None


def parse_summary_page(html: str) -> Optional[SummaryDict]:
    soup = BeautifulSoup(html, "html.parser")
    cust_panel = soup.find("div", id="panel-cust")
    if not isinstance(cust_panel, Tag):
        return None
    tbody = cust_panel.find("tbody")
    if not isinstance(tbody, Tag):
        return None

    col_map = _detect_column_map_from_thead(cust_panel)
    effective_map = col_map if col_map else dict(SUMMARY_COLUMN_MAP)

    results: SummaryDict = {}
    for row in tbody.find_all("tr", recursive=False):
        if not isinstance(row, Tag):
            continue
        classes = row.get("class")
        if isinstance(classes, list):
            class_list = classes
        elif isinstance(classes, str):
            class_list = [classes]
        else:
            class_list = []
        if "sr-trunk-row" in class_list:
            continue
        cells = row.find_all("td")
        if len(cells) < 13:
            logging.warning("Summary: skipping row with %d cells (< 13)", len(cells))
            continue

        vol_name = cells[1].find("span", class_="sr-vol-name")
        if isinstance(vol_name, Tag):
            customer_name = vol_name.get_text(strip=True)
        else:
            customer_name = "N/A"
            logging.warning("Summary: row has no customer name; using 'N/A'")

        row_data: dict[str, object] = {"name": customer_name}

        for cell_idx, field_name in effective_map.items():
            if cell_idx < len(cells):
                val = _extract_cell_value(cells[cell_idx])
                if val is not None:
                    row_data[field_name] = val

        if "billed_min" not in row_data:
            row_data["billed_min"] = _span_number(cells, 7, "rpt-num")

        if "margin" not in row_data:
            row_data["margin"] = _span_number(cells, 12, "rpt-asr-pill")

        expand_btn = cells[0].find("button", class_="sr-expand-btn")
        if not isinstance(expand_btn, Tag):
            logging.warning("Summary: row for %s has no expand button; skipping", customer_name)
            continue
        onclick = expand_btn.get("onclick")
        match = re.search(r"ct(\d+)", onclick) if isinstance(onclick, str) else None
        if match is None:
            logging.warning("Summary: could not parse customer id from onclick %r; skipping", onclick)
            continue

        customer_id = match.group(1)
        if customer_id in results:
            logging.warning("Summary: duplicate customer id %s in report; keeping first row", customer_id)
            continue

        _validate_summary_row(customer_id, row_data)
        results[customer_id] = row_data

    return results
