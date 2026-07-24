from __future__ import annotations

import asyncio
import logging
import time
from datetime import date
from typing import Optional

import aiohttp

from auth import create_session, perform_login
from config import BASE_EDIT_URL, SUMMARY_REPORT_URL, Settings
from display import print_balance_line, print_summary_line
from persistence import (
    BalanceRecord,
    MarginRecord,
    get_balance_history,
    get_margin_history,
    init_db,
    insert_balance,
    insert_margin,
)
from retry import _is_transient
from scrapers import fetch_balance, fetch_summary_report, parse_customer_page, parse_summary_page


def _balance_changed(cid: str, balance: float, credit_limit: Optional[float]) -> bool:
    rows = get_balance_history(customer_id=cid, hours=24, limit=1)
    if not rows:
        return True
    r = rows[0]
    return r.get("balance") != balance or r.get("credit_limit") != credit_limit


def _margin_changed(cid: str, margin: Optional[float], billed_min: Optional[float]) -> bool:
    rows = get_margin_history(customer_id=cid, hours=24, limit=1)
    if not rows:
        return True
    r = rows[0]
    return r.get("margin") != margin or r.get("billed_min") != billed_min


def run_quick_check_full(settings: Settings) -> None:
    customer_ids = settings.customer_ids
    timeout = settings.global_.request_timeout
    session = create_session()

    print("  Logging in...")
    if not perform_login(session, timeout):
        print("  Login failed.")
        session.close()
        return

    init_db()

    print()
    print("  Balances")
    print("  " + "-" * 30)
    for cid in customer_ids:
        name, balance, credit_limit, error = fetch_balance(session, cid, timeout)
        print_balance_line(name or "N/A", cid, balance, credit_limit, error)
        if balance is not None:
            remaining = credit_limit + balance if credit_limit is not None else None
            if _balance_changed(cid, balance, credit_limit):
                insert_balance(BalanceRecord(
                    customer_id=cid, customer_name=name or "N/A",
                    balance=balance, credit_limit=credit_limit, remaining=remaining,
                ))
        time.sleep(0.3)

    print()
    print("  Summary Report")
    print("  " + "-" * 30)
    summary, _ = fetch_summary_report(session, settings)
    if not summary:
        print("  No data.")
    else:
        for cid, data in summary.items():
            print_summary_line(data, cid, monitored=(cid in customer_ids))
            margin = data.get('margin')
            billed_min = data.get('billed_min')
            m = float(margin) if isinstance(margin, (int, float)) else None
            b = float(billed_min) if isinstance(billed_min, (int, float)) else None
            if _margin_changed(cid, m, b):
                insert_margin(MarginRecord(
                    customer_id=cid, customer_name=str(data.get('name', 'N/A')),
                    margin=m, billed_min=b,
                ))
    session.close()
    print()


def run_quick_check_balance(settings: Settings) -> None:
    customer_ids = settings.customer_ids
    timeout = settings.global_.request_timeout
    session = create_session()

    print("  Logging in...")
    if not perform_login(session, timeout):
        print("  Login failed.")
        session.close()
        return

    init_db()

    print()
    print("  Balances")
    print("  " + "-" * 30)
    for cid in customer_ids:
        name, balance, credit_limit, error = fetch_balance(session, cid, timeout)
        print_balance_line(name or "N/A", cid, balance, credit_limit, error)
        if balance is not None:
            remaining = credit_limit + balance if credit_limit is not None else None
            if _balance_changed(cid, balance, credit_limit):
                insert_balance(BalanceRecord(
                    customer_id=cid, customer_name=name or "N/A",
                    balance=balance, credit_limit=credit_limit, remaining=remaining,
                ))
        time.sleep(0.3)
    session.close()
    print()


def run_quick_check_summary(settings: Settings) -> None:
    session = create_session()

    print("  Logging in...")
    if not perform_login(session, settings.global_.request_timeout):
        print("  Login failed.")
        session.close()
        return

    init_db()

    print()
    print("  Summary Report")
    print("  " + "-" * 30)
    summary, _ = fetch_summary_report(session, settings)
    if not summary:
        print("  No data.")
    else:
        for cid, data in summary.items():
            print_summary_line(data, cid, monitored=(cid in settings.customer_ids))
            margin = data.get('margin')
            billed_min = data.get('billed_min')
            m = float(margin) if isinstance(margin, (int, float)) else None
            b = float(billed_min) if isinstance(billed_min, (int, float)) else None
            if _margin_changed(cid, m, b):
                insert_margin(MarginRecord(
                    customer_id=cid, customer_name=str(data.get('name', 'N/A')),
                    margin=m, billed_min=b,
                ))
    session.close()
    print()


# -- Async parallel scraping --


async def _async_fetch_balance(
    session: aiohttp.ClientSession, customer_id: str, timeout: int = 10, retries: int = 2,
) -> tuple[Optional[str], Optional[float], Optional[float], Optional[str]]:
    edit_url = f"{BASE_EDIT_URL}{customer_id}"
    last_error: Optional[str] = None
    for attempt in range(retries + 1):
        try:
            async with session.get(edit_url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status != 200:
                    last_error = f"HTTP {resp.status}"
                    if attempt < retries and resp.status >= 500:
                        await asyncio.sleep(2)
                        continue
                    return None, None, None, last_error
                html = await resp.text()
            return parse_customer_page(html)
        except asyncio.TimeoutError:
            last_error = f"timeout ({timeout}s)"
        except Exception as e:
            last_error = str(e)
        if attempt < retries and _is_transient(last_error):
            delay = 2 if attempt == 0 else 5
            logging.warning(f"Async retry {attempt + 1}/{retries + 1} for customer {customer_id} in {delay}s...")
            await asyncio.sleep(delay)
    return None, None, None, last_error


async def _async_fetch_balances_parallel(
    session: aiohttp.ClientSession, customer_ids: list[str], timeout: int = 10,
) -> list[tuple[str, Optional[str], Optional[float], Optional[float], Optional[str]]]:
    tasks = [_async_fetch_balance(session, cid, timeout) for cid in customer_ids]
    results = await asyncio.gather(*tasks)
    return [(cid, name, balance, credit, error) for cid, (name, balance, credit, error) in zip(customer_ids, results)]


async def _async_fetch_summary(
    session: aiohttp.ClientSession, settings: Settings, retries: int = 2,
) -> Optional[dict[str, dict[str, object]]]:
    g = settings.global_
    today = date.today().isoformat()
    timeout = g.request_timeout
    params = {
        "direction": g.summary_direction, "interval": g.summary_interval,
        "date_from": today, "date_to": today,
    }
    last_error: Optional[str] = None
    for attempt in range(retries + 1):
        try:
            async with session.get(SUMMARY_REPORT_URL, params=params,
                                   timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status != 200:
                    last_error = f"HTTP {resp.status}"
                    if attempt < retries and resp.status >= 500:
                        await asyncio.sleep(2)
                        continue
                    logging.error(f"Summary report async - {last_error}")
                    return None
                html = await resp.text()
            results = parse_summary_page(html)
            if results is not None:
                logging.info(f"Summary report async - {len(results)} customers found.")
            return results
        except asyncio.TimeoutError:
            last_error = f"timeout ({timeout}s)"
        except Exception as e:
            last_error = str(e)
            logging.error(f"Summary report async - {e}")
        if attempt < retries and _is_transient(last_error):
            delay = 2 if attempt == 0 else 5
            await asyncio.sleep(delay)
    logging.error(f"Summary report async - all retries exhausted: {last_error}")
    return None


def run_quick_check_parallel(settings: Settings) -> None:
    customer_ids = settings.customer_ids
    timeout = settings.global_.request_timeout
    session = create_session()

    print("  Logging in...")
    if not perform_login(session, timeout):
        print("  Login failed.")
        session.close()
        return

    init_db()
    cookies = {c.name: c.value for c in session.cookies}
    headers = dict(session.headers)
    session.close()

    async def _run() -> None:
        async with aiohttp.ClientSession(cookies=cookies, headers=headers) as aio_session:
            print()
            print("  Balances (async parallel)")
            print("  " + "-" * 30)
            results = await _async_fetch_balances_parallel(aio_session, customer_ids, timeout)
            for cid, name, balance, credit_limit, error in results:
                print_balance_line(name or "N/A", cid, balance, credit_limit, error)
                if balance is not None:
                    remaining = credit_limit + balance if credit_limit is not None else None
                    if _balance_changed(cid, balance, credit_limit):
                        insert_balance(BalanceRecord(
                            customer_id=cid, customer_name=name or "N/A",
                            balance=balance, credit_limit=credit_limit, remaining=remaining,
                        ))

            print()
            print("  Summary Report (async)")
            print("  " + "-" * 30)
            summary_data = await _async_fetch_summary(aio_session, settings)
            if not summary_data:
                print("  No data.")
            else:
                for cid, data in summary_data.items():
                    print_summary_line(data, cid, monitored=(cid in customer_ids))
                    m = data.get('margin')
                    b = data.get('billed_min')
                    margin_val = float(m) if isinstance(m, (int, float)) else None
                    billed_val = float(b) if isinstance(b, (int, float)) else None
                    if _margin_changed(cid, margin_val, billed_val):
                        insert_margin(MarginRecord(
                            customer_id=cid, customer_name=str(data.get('name', 'N/A')),
                            margin=margin_val, billed_min=billed_val,
                        ))
            print()

    asyncio.run(_run())
