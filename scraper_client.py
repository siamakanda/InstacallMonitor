from __future__ import annotations

import logging
import random
import threading
import time
import traceback
from datetime import date
from typing import Optional
from urllib.parse import quote

import requests

from auth import perform_login
from circuit_breaker import CircuitBreaker
from config import BASE_EDIT_URL, SUMMARY_REPORT_URL, Settings
from scraper_parser import BalanceResult, SummaryDict, parse_customer_page, parse_summary_page

# Advanced resilience tuning — deliberately fixed rather than user-configurable.
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0
_RETRY_MAX_DELAY = 30.0
_RETRY_JITTER_MAX = 1.0
_NON_TRANSIENT_DELAY = 0.5
_CIRCUIT_FAILURE_THRESHOLD = 5
_CIRCUIT_RECOVERY_TIMEOUT = 60.0
_RELOGIN_LOCK = threading.Lock()


def _retry_delays() -> list[float]:
    delays: list[float] = []
    for attempt in range(1, _RETRY_ATTEMPTS):
        delay = min(_RETRY_BASE_DELAY * (2 ** (attempt - 1)), _RETRY_MAX_DELAY)
        delays.append(delay + random.uniform(0, _RETRY_JITTER_MAX))
    return delays


def _needs_relogin(response: requests.Response) -> bool:
    return "login" in response.url.lower()


def _is_transient(error: str | None) -> bool:
    if not error:
        return False
    e = str(error).lower()
    return "timeout" in e or "connection" in e


class ScraperClient:
    """HTTP fetch layer: owns the circuit breakers and the retry policy."""

    def __init__(self) -> None:
        self._balance_breaker = CircuitBreaker(
            name="balance",
            failure_threshold=_CIRCUIT_FAILURE_THRESHOLD,
            recovery_timeout=_CIRCUIT_RECOVERY_TIMEOUT,
        )
        self._summary_breaker = CircuitBreaker(
            name="summary_report",
            failure_threshold=_CIRCUIT_FAILURE_THRESHOLD,
            recovery_timeout=_CIRCUIT_RECOVERY_TIMEOUT,
        )

    def _relogin(self, session: requests.Session, timeout: int) -> bool:
        # Serialise re-logins: worker threads share one Session, so concurrent
        # perform_login calls would clobber each other's cookies.
        with _RELOGIN_LOCK:
            return perform_login(session, timeout)

    def fetch_balance(self, session: requests.Session, customer_id: str, timeout: int = 10) -> BalanceResult:
        edit_url = f"{BASE_EDIT_URL}{quote(customer_id, safe='')}"
        last_error: Optional[str] = None

        retry_delays = _retry_delays()
        total_attempts = 1 + len(retry_delays)

        for attempt in range(total_attempts):
            if not self._balance_breaker.before_request():
                return None, None, None, f"circuit open (failures: {self._balance_breaker.failure_count})"

            transient = False
            try:
                resp = session.get(edit_url, timeout=timeout, allow_redirects=True)
                if _needs_relogin(resp):
                    logging.warning("Session expired. Re-logging in...")
                    if not self._relogin(session, timeout):
                        self._balance_breaker.on_failure()
                        last_error = "re-login failed"
                        if attempt < len(retry_delays):
                            time.sleep(retry_delays[attempt])
                            continue
                        return None, None, None, last_error
                    resp = session.get(edit_url, timeout=timeout, allow_redirects=True)
                    if _needs_relogin(resp):
                        self._balance_breaker.on_failure()
                        last_error = "still redirected to login after re-login"
                        if attempt < len(retry_delays):
                            time.sleep(retry_delays[attempt])
                            continue
                        return None, None, None, last_error

                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}"
                    self._balance_breaker.on_failure()
                    if resp.status_code >= 500 and attempt < len(retry_delays):
                        time.sleep(retry_delays[attempt])
                        continue
                    return None, None, None, last_error

                try:
                    result = parse_customer_page(resp.text)
                except Exception as e:
                    # The request succeeded — a parse failure is not a network fault.
                    logging.error(f"Customer {customer_id} - parse error: {e}")
                    self._balance_breaker.on_success()
                    last_error = f"parse error: {e}"
                    if attempt < len(retry_delays):
                        time.sleep(retry_delays[attempt])
                        continue
                    return None, None, None, last_error

                self._balance_breaker.on_success()
                return result
            except requests.exceptions.Timeout:
                last_error = f"timeout ({timeout}s)"
                transient = True
            except requests.exceptions.ConnectionError:
                last_error = "connection error"
                transient = True
            except Exception as e:
                logging.error(f"Customer {customer_id} - error: {e}\n{traceback.format_exc()}")
                last_error = str(e)
                transient = _is_transient(last_error)

            self._balance_breaker.on_failure()
            if attempt < len(retry_delays):
                time.sleep(retry_delays[attempt] if transient else _NON_TRANSIENT_DELAY)

        return None, None, None, last_error

    def fetch_summary_report(self, session: requests.Session, settings: Settings) -> tuple[SummaryDict, Optional[str]]:
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

        retry_delays = _retry_delays()
        total_attempts = 1 + len(retry_delays)

        for attempt in range(total_attempts):
            if not self._summary_breaker.before_request():
                return {}, f"circuit open (failures: {self._summary_breaker.failure_count})"

            try:
                resp = session.get(SUMMARY_REPORT_URL, params=params, timeout=timeout, allow_redirects=True)
            except requests.exceptions.Timeout:
                last_error = f"timeout ({timeout}s)"
                self._summary_breaker.on_failure()
                if attempt < len(retry_delays):
                    time.sleep(retry_delays[attempt])
                    continue
                return {}, last_error
            except requests.exceptions.ConnectionError:
                last_error = "connection error"
                self._summary_breaker.on_failure()
                if attempt < len(retry_delays):
                    time.sleep(retry_delays[attempt])
                    continue
                return {}, last_error
            except Exception as e:
                logging.error(f"Summary - error: {e}\n{traceback.format_exc()}")
                self._summary_breaker.on_failure()
                return {}, str(e)

            if _needs_relogin(resp):
                logging.warning("Session expired. Re-logging in...")
                if not self._relogin(session, timeout):
                    self._summary_breaker.on_failure()
                    last_error = "re-login failed"
                    if attempt < len(retry_delays):
                        time.sleep(retry_delays[attempt])
                        continue
                    return {}, last_error
                try:
                    resp = session.get(SUMMARY_REPORT_URL, params=params, timeout=timeout, allow_redirects=True)
                except Exception as e:
                    logging.error(f"Summary re-fetch error: {e}")
                    self._summary_breaker.on_failure()
                    last_error = f"re-fetch failed: {e}"
                    if attempt < len(retry_delays):
                        time.sleep(retry_delays[attempt])
                        continue
                    return {}, last_error
                if _needs_relogin(resp):
                    self._summary_breaker.on_failure()
                    last_error = "still redirected to login after re-login"
                    if attempt < len(retry_delays):
                        time.sleep(retry_delays[attempt])
                        continue
                    return {}, last_error

            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}"
                self._summary_breaker.on_failure()
                if resp.status_code >= 500 and attempt < len(retry_delays):
                    time.sleep(retry_delays[attempt])
                    continue
                return {}, last_error

            try:
                results = parse_summary_page(resp.text)
            except Exception as e:
                logging.error(f"Summary parse error: {e}")
                self._summary_breaker.on_success()
                last_error = f"parse error: {e}"
                if attempt < len(retry_delays):
                    time.sleep(retry_delays[attempt])
                    continue
                return {}, last_error

            if results is None:
                # Page shape not recognised — a parse failure, not a network fault.
                self._summary_breaker.on_success()
                last_error = "failed to parse summary page"
                if attempt < len(retry_delays):
                    time.sleep(retry_delays[attempt])
                    continue
                return {}, last_error

            if not results:
                logging.warning("Summary report parsed successfully but contained no customers.")

            self._summary_breaker.on_success()
            logging.info(f"Summary - {len(results)} customers found.")
            return results, None

        return {}, last_error
