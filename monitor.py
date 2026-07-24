from __future__ import annotations

import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

import requests

from alerts import _alert_state, _siren_manager, trigger_balance_alert, trigger_margin_alert, trigger_recovery_alert
from auth import create_session, perform_login
from config import Settings, write_status
from display import bold, cyan, dim, fmt_pct, green, print_balance_line, print_summary_line, red, yellow
from health import start_health_server
from persistence import BalanceRecord, MarginRecord, init_db, insert_balance, insert_margin, purge_old_records
from scrapers import fetch_balance, fetch_summary_report


def is_within_active_hours(
    start: str, end: str, days: str, now: Optional[datetime] = None
) -> bool:
    if not start and not end:
        return True
    if now is None:
        now = datetime.now()
    if days:
        day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
        allowed = set()
        for part in days.lower().replace(" ", "").split(","):
            if part in day_map:
                allowed.add(day_map[part])
        if allowed and now.weekday() not in allowed:
            return False
    if start and end:
        try:
            start_h, start_m = map(int, start.split(":"))
            end_h, end_m = map(int, end.split(":"))
            now_minutes = now.hour * 60 + now.minute
            start_minutes = start_h * 60 + start_m
            end_minutes = end_h * 60 + end_m
            if start_minutes <= end_minutes:
                if not (start_minutes <= now_minutes < end_minutes):
                    return False
            else:
                if not (now_minutes >= start_minutes or now_minutes < end_minutes):
                    return False
        except (ValueError, IndexError):
            return True
    return True


def _sleep(seconds: float, stop_event: Optional[threading.Event] = None) -> bool:
    remaining = int(seconds)
    while remaining > 0:
        if stop_event is not None and stop_event.is_set():
            return True
        chunk = min(remaining, 1)
        time.sleep(chunk)
        remaining -= chunk
    return False


def _qprint(settings: Settings, *args, **kwargs) -> None:
    if not settings.global_.quiet:
        print(*args, **kwargs)


def _monitor_loop(settings: Settings, session: requests.Session,
                  stop_event: Optional[threading.Event] = None) -> None:
    g = settings.global_
    customer_ids = settings.customer_ids
    interval = g.check_interval
    timeout = g.request_timeout
    cooldown = g.cooldown

    last_balance_vals: dict[str, tuple[float, Optional[float]]] = {}
    last_margin_vals: dict[str, tuple[Optional[float], Optional[float]]] = {}
    error_count = 0
    last_error: Optional[str] = None

    init_db()
    purge_old_records(g.db_retention_days)

    write_status(alive=True, error_count=0)
    _qprint(settings, "  Running first check now...")

    try:
        first_cycle = True
        while True:
            last_check = time.strftime("%Y-%m-%d %H:%M:%S")

            if not is_within_active_hours(g.active_hours_start, g.active_hours_end, g.active_days):
                write_status(alive=True, last_check=last_check, error_count=error_count, last_error=last_error)
                next_time = time.strftime("%H:%M:%S", time.localtime(time.time() + interval))
                _qprint(settings, f"  [{time.strftime('%H:%M:%S')}] Outside active hours. Next wake at {next_time}")
                if _sleep(interval, stop_event):
                    break
                continue

            if first_cycle and len(customer_ids) > 1:
                error_count += _fetch_balances_parallel(settings, session, customer_ids, timeout,
                                       last_balance_vals, cooldown)
            else:
                for cid in customer_ids:
                    if _process_balance(settings, session, cid, timeout, cooldown,
                                     last_balance_vals):
                        error_count += 1
                    if _sleep(0.1, stop_event):
                        break
            first_cycle = False

            _qprint(settings)
            summary, summary_error = fetch_summary_report(session, settings)
            if not summary:
                error_count += 1
                last_error = summary_error or "summary report fetch returned empty"

            if error_count > 0:
                last_error = f"{error_count} fetch errors in last cycle"
            shown = 0
            total = len(summary)
            show_ids: set[str]
            if g.summary_show_all:
                show_ids = set(summary.keys())
            else:
                show_ids = set(customer_ids) | {
                    cid for cid, data in summary.items()
                    if (data.get('margin') is not None and data.get('billed_min') is not None
                        and data.get('margin') < g.margin_below
                        and data.get('billed_min') > g.billed_above)
                }

            _qprint(settings, f"  -- Summary ({len(show_ids)} shown / {total} total) --")
            for cid, data in summary.items():
                if cid not in show_ids:
                    continue

                margin = data.get('margin')
                billed_min = data.get('billed_min')
                name = str(data.get('name', 'N/A'))
                monitored = cid in customer_ids

                if (margin is not None and margin == 0 and billed_min is not None and billed_min == 0
                        and not monitored):
                    continue

                print_summary_line(data, cid, monitored=monitored,
                                   prefix=f"[M] [{time.strftime('%H:%M:%S')}]")
                shown += 1

                cust = settings.get_watch(cid)
                margin_blw = cust.resolve_margin_below(g.margin_below)
                margin_rearm = cust.resolve_margin_critical(g.margin_critical)
                billed_abv = cust.resolve_billed_above(g.billed_above)
                margin_escalation = margin_rearm < margin_blw

                if margin is not None:
                    logging.info(f"Customer {cid} ({name}) - Margin: {margin:.1f}% | Billed Min: {billed_min:.1f}")

                m_prev = last_margin_vals.get(cid)
                m_curr = (float(margin) if isinstance(margin, (int, float)) else None,
                           float(billed_min) if isinstance(billed_min, (int, float)) else None)
                if m_prev != m_curr:
                    last_margin_vals[cid] = m_curr
                    insert_margin(MarginRecord(
                        customer_id=cid, customer_name=name,
                        margin=float(margin) if isinstance(margin, (int, float)) else None,
                        billed_min=float(billed_min) if isinstance(billed_min, (int, float)) else None,
                    ))

                if margin is not None and billed_min is not None and monitored:
                    state = _alert_state.get_margin_state(cid)
                    if margin < margin_blw and billed_min > billed_abv:
                        if state == 0 and _siren_manager.can_margin_alert(cid, cooldown):
                            trigger_margin_alert(cid, margin, billed_min, name, settings)
                            _alert_state.set_margin_state(cid, 1)
                            print(f"  !! MARGIN ALERT for {name} (ID: {cid}) - state S1")
                        elif state == 1 and margin_escalation and margin < margin_rearm and _siren_manager.can_margin_alert(cid, cooldown):
                            trigger_margin_alert(cid, margin, billed_min, name, settings, escalated=True)
                            _alert_state.set_margin_state(cid, 2)
                            print(f"  !!! MARGIN ESCALATION for {name} (ID: {cid}) - state S2")
                    elif state > 0 and (margin >= margin_blw or billed_min <= billed_abv):
                        trigger_recovery_alert(cid, margin, name, settings, "margin")
                        logging.info(f"Customer {cid} ({name}) - Margin recovered to {margin:.1f}%. Alert disarmed.")
                        _alert_state.set_margin_state(cid, 0)

            write_status(alive=True, last_check=last_check, error_count=error_count, last_error=last_error)
            purge_old_records(g.db_retention_days)
            next_time = time.strftime("%H:%M:%S", time.localtime(time.time() + interval))
            _qprint(settings)
            _qprint(settings, f"  [{time.strftime('%H:%M:%S')}] Cycle complete. Next check at {next_time} (~{interval}s)")
            _qprint(settings, f"  {'-' * 40}")
            if _sleep(interval, stop_event):
                break

    except KeyboardInterrupt:
        print("\n\n[-] Interrupted. Shutting down...")
        logging.info("Monitor stopped by user (Ctrl+C).")
    finally:
        write_status(alive=False)


def _process_balance(settings: Settings, session: requests.Session,
                     cid: str, timeout: int, cooldown: int,
                     last_balance_vals: dict) -> bool:
    cust = settings.get_watch(cid)
    bal_below = cust.resolve_balance_below()
    bal_rearm = cust.resolve_balance_critical()
    balance_escalation = bal_rearm < bal_below

    customer_name, balance, credit_limit, fetch_error = fetch_balance(session, cid, timeout)

    if balance is not None:
        remaining = credit_limit + balance if credit_limit is not None else None
        print_balance_line(customer_name, cid, balance, credit_limit, fetch_error,
                           prefix=f"B [{time.strftime('%H:%M:%S')}]")

        prev = last_balance_vals.get(cid)
        curr = (balance, credit_limit)
        if prev != curr:
            last_balance_vals[cid] = curr
            insert_balance(BalanceRecord(
                customer_id=cid, customer_name=customer_name or "N/A",
                balance=balance, credit_limit=credit_limit, remaining=remaining,
            ))

        if credit_limit is not None:
            logging.info(f"Customer {cid} ({customer_name}) - Balance: {balance:.4f} | "
                         f"Credit: {credit_limit:.2f} | Remaining: {remaining:.2f}")
        else:
            logging.info(f"Customer {cid} ({customer_name}) - Balance: {balance:.4f}")

        state = _alert_state.get_balance_state(cid)
        if balance < bal_below:
            if state == 0 and _siren_manager.can_alert(cid, cooldown):
                trigger_balance_alert(cid, balance, customer_name or "N/A", settings)
                _alert_state.set_balance_state(cid, 1)
                print(f"  !! BALANCE ALERT for {customer_name} (ID: {cid}) - state S1")
            elif state == 1 and balance_escalation and balance < bal_rearm and _siren_manager.can_alert(cid, cooldown):
                trigger_balance_alert(cid, balance, customer_name or "N/A", settings, escalated=True)
                _alert_state.set_balance_state(cid, 2)
                print(f"  !!! BALANCE ESCALATION for {customer_name} (ID: {cid}) - state S2")
        elif state > 0 and balance >= bal_below:
            trigger_recovery_alert(cid, balance, customer_name or "N/A", settings, "balance")
            logging.info(f"Customer {cid} ({customer_name}) - Balance recovered to {balance:.4f}. Alert disarmed.")
            _alert_state.set_balance_state(cid, 0)
        return False
    else:
        logging.warning(f"Customer {cid} - fetch error: {fetch_error}")
        print_balance_line("N/A", cid, None, None, fetch_error,
                           prefix=f"B [{time.strftime('%H:%M:%S')}]")
        return True

def _fetch_balances_parallel(settings: Settings, session: requests.Session,
                             customer_ids: list[str], timeout: int,
                             last_balance_vals: dict, cooldown: int) -> int:
    errors = 0
    with ThreadPoolExecutor(max_workers=min(8, len(customer_ids))) as executor:
        futures = {
            executor.submit(fetch_balance, session, cid, timeout): cid
            for cid in customer_ids
        }
        for future in as_completed(futures):
            cid = futures[future]
            try:
                customer_name, balance, credit_limit, fetch_error = future.result()
            except Exception as e:
                customer_name, balance, credit_limit, fetch_error = None, None, None, str(e)
                errors += 1

            cust = settings.get_watch(cid)
            bal_below = cust.resolve_balance_below()
            bal_rearm = cust.resolve_balance_critical()
            balance_escalation = bal_rearm < bal_below

            if balance is not None:
                remaining = credit_limit + balance if credit_limit is not None else None
                print_balance_line(customer_name, cid, balance, credit_limit, fetch_error,
                                   prefix=f"B [{time.strftime('%H:%M:%S')}]")

                prev = last_balance_vals.get(cid)
                curr = (balance, credit_limit)
                if prev != curr:
                    last_balance_vals[cid] = curr
                    insert_balance(BalanceRecord(
                        customer_id=cid, customer_name=customer_name or "N/A",
                        balance=balance, credit_limit=credit_limit, remaining=remaining,
                    ))

                if credit_limit is not None:
                    logging.info(f"Customer {cid} ({customer_name}) - Balance: {balance:.4f} | "
                                 f"Credit: {credit_limit:.2f} | Remaining: {remaining:.2f}")
                else:
                    logging.info(f"Customer {cid} ({customer_name}) - Balance: {balance:.4f}")

                state = _alert_state.get_balance_state(cid)
                if balance < bal_below:
                    if state == 0 and _siren_manager.can_alert(cid, cooldown):
                        trigger_balance_alert(cid, balance, customer_name or "N/A", settings)
                        _alert_state.set_balance_state(cid, 1)
                        print(f"  !! BALANCE ALERT for {customer_name} (ID: {cid}) - state S1")
                    elif state == 1 and balance_escalation and balance < bal_rearm and _siren_manager.can_alert(cid, cooldown):
                        trigger_balance_alert(cid, balance, customer_name or "N/A", settings, escalated=True)
                        _alert_state.set_balance_state(cid, 2)
                        print(f"  !!! BALANCE ESCALATION for {customer_name} (ID: {cid}) - state S2")
                elif state > 0 and balance >= bal_below:
                    trigger_recovery_alert(cid, balance, customer_name or "N/A", settings, "balance")
                    logging.info(f"Customer {cid} ({customer_name}) - Balance recovered to {balance:.4f}. Alert disarmed.")
                    _alert_state.set_balance_state(cid, 0)
            else:
                errors += 1
                logging.warning(f"Customer {cid} - fetch error: {fetch_error}")
                print_balance_line("N/A", cid, None, None, fetch_error,
                                   prefix=f"B [{time.strftime('%H:%M:%S')}]")
    return errors


def run_monitor(settings: Settings, stop_event: Optional[threading.Event] = None) -> None:
    g = settings.global_
    customer_ids = settings.customer_ids
    interval = g.check_interval

    write_status(alive=True, last_check=time.strftime("%Y-%m-%d %H:%M:%S"))

    audio_str = green("ON") if g.audio else dim("OFF")
    webhook_str = cyan(g.webhook_type) if g.webhook_type != "none" else dim("none")
    quiet_str = dim("ON") if g.quiet else dim("OFF")
    active_str = "24/7"
    if g.active_hours_start and g.active_hours_end:
        days = f" ({g.active_days})" if g.active_days else " (all days)"
        active_str = f"{g.active_hours_start}-{g.active_hours_end}{days}"

    print()
    print(f"  {bold('Monitor Started')}  {dim(time.strftime('%Y-%m-%d %H:%M:%S'))}")
    print(f"  {dim('-' * 50)}")
    print(f"  Watching    {bold(str(len(customer_ids)))} customers  |  Every {bold(str(interval))}s")
    print(f"  Margin      below {yellow(fmt_pct(g.margin_below))}  |  Billed above {yellow(str(int(g.billed_above)))} min")
    for w in settings.watch:
        bal = w.balance_below
        bal_str = red(f"{bal:+.1f}") if bal is not None else dim("N/A")
        extra = f", critical {w.balance_critical:+.1f}" if w.balance_critical is not None and w.balance_critical != w.balance_below else ""
        print(f"    {w.customer}:  balance below {bal_str}{extra}")
    print(f"  Audio       {audio_str}  |  Webhooks  {webhook_str}  |  Active  {active_str}")
    print(f"  {dim('-' * 50)}")

    if g.health_port > 0:
        start_health_server(g.health_port)
        print(f"  {dim('Health')}     http://localhost:{g.health_port}/health")
    print(f"  {dim('Ctrl+C to stop.')}")
    if g.quiet:
        print(f"  {dim('Quiet mode ON — only alerts shown.')}")
    print()

    session = create_session()
    if not perform_login(session, g.request_timeout):
        print("  Login failed. Exiting.")
        write_status(alive=False, last_error="Initial login failed")
        session.close()
        return

    while True:
        if stop_event is not None and stop_event.is_set():
            break
        try:
            _monitor_loop(settings, session, stop_event)
            break
        except KeyboardInterrupt:
            break
        except Exception as e:
            logging.critical(f"Monitor crashed: {e}\n{traceback.format_exc()}")
            print(f"  CRASH: {e}")
            print("  Restarting in 10 seconds...")
            write_status(alive=False, last_error=str(e))
            try:
                session.close()
            except Exception:
                pass
            session = create_session()
            if not perform_login(session, g.request_timeout):
                logging.critical("Re-login failed after crash. Stopping monitor.")
                write_status(alive=False, last_error=str(e))
                break
            logging.info("Re-login successful after crash. Restarting monitor loop.")
            if _sleep(10, stop_event):
                break
