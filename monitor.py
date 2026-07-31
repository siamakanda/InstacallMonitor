from __future__ import annotations

import atexit
import concurrent.futures
import json
import logging
import os
import signal
import sys
import threading
import time
import traceback
from datetime import datetime
from typing import Optional

import auth
from alerts import SirenManager, check_balance_alert, check_margin_alert
from config import (
    Settings,
    get_credentials,
    get_settings_version,
    load_settings,
    reload_settings,
    setup_logging,
    validate_settings,
)
from display import bold, cyan, dim, green, print_balance_line, print_summary_line, red, yellow
from persistence import (
    BalanceRecord,
    MarginRecord,
    export_balance_csv,
    export_margin_csv,
    init_db,
    insert_balance,
    insert_margin,
    purge_old_records,
)
from scrapers import configure_scrapers, fetch_balance, fetch_summary_report

_SHUTDOWN = threading.Event()
_STATUS_LOCK = threading.Lock()
_START_TIME = 0.0
_CYCLE_COUNT = 0
_ERROR_COUNT = 0
_LAST_ERROR: Optional[str] = None
_LAST_CHECK: Optional[str] = None
_STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor.status")


def _write_health_status(settings: Settings) -> None:
    global _LAST_CHECK, _ERROR_COUNT, _LAST_ERROR, _CYCLE_COUNT
    with _STATUS_LOCK:
        uptime = time.time() - _START_TIME if _START_TIME else 0.0
        status = {
            "alive": not _SHUTDOWN.is_set(),
            "last_check": _LAST_CHECK,
            "error_count": _ERROR_COUNT,
            "last_error": _LAST_ERROR,
            "uptime_seconds": round(uptime, 1),
            "cycle_count": _CYCLE_COUNT,
            "settings_version": get_settings_version(),
            "customers_monitored": len(settings.customer_ids),
        }
    try:
        with open(_STATUS_FILE, "w") as f:
            json.dump(status, f, indent=2)
    except OSError:
        pass


def _on_exit(settings: Optional[Settings]) -> None:
    def _cleanup() -> None:
        _SHUTDOWN.set()
        _write_health_status(settings) if settings else None

    return _cleanup


def _parse_args() -> dict[str, object]:
    args: dict[str, object] = {}
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--interval" and i + 1 < len(argv):
            try:
                args["interval"] = int(argv[i + 1])
            except ValueError:
                pass
            i += 2
        elif a == "--balance-below" and i + 1 < len(argv):
            try:
                args["balance_below"] = float(argv[i + 1])
            except ValueError:
                pass
            i += 2
        elif a == "--margin-below" and i + 1 < len(argv):
            try:
                args["margin_below"] = float(argv[i + 1])
            except ValueError:
                pass
            i += 2
        elif a == "--billed-above" and i + 1 < len(argv):
            try:
                args["billed_above"] = float(argv[i + 1])
            except ValueError:
                pass
            i += 2
        elif a == "--margin-above" and i + 1 < len(argv):
            try:
                args["margin_above"] = float(argv[i + 1])
            except ValueError:
                pass
            i += 2
        elif a == "--margin-deadband" and i + 1 < len(argv):
            try:
                args["margin_deadband"] = float(argv[i + 1])
            except ValueError:
                pass
            i += 2
        elif a == "--cooldown" and i + 1 < len(argv):
            try:
                args["cooldown"] = int(argv[i + 1])
            except ValueError:
                pass
            i += 2
        elif a == "--quiet":
            args["quiet"] = True
            i += 1
        elif a == "--run-once":
            args["run_once"] = True
            i += 1
        elif a == "--export":
            args["export"] = True
            i += 1
        elif a == "--export-customer" and i + 1 < len(argv):
            args["export_customer"] = argv[i + 1]
            i += 2
        elif a == "--no-hot-reload":
            args["no_hot_reload"] = True
            i += 1
        elif a == "--help":
            args["help"] = True
            i += 1
        else:
            i += 1
    return args


def _print_help() -> None:
    print("InstacallMonitor - Balance & Margin Monitor")
    print()
    print("Usage: python monitor.py [OPTIONS]")
    print()
    print("Options:")
    print("  --interval N         Check interval in seconds (overrides settings.toml)")
    print("  --balance-below N    Override all balance thresholds")
    print("  --margin-below N     Override margin lower threshold (%)")
    print("  --margin-above N     Override margin upper threshold (%)")
    print("  --margin-deadband N  Override margin recovery deadband (%)")
    print("  --billed-above N     Override billed minutes threshold")
    print("  --cooldown N         Alert cooldown in seconds")
    print("  --quiet              Suppress non-alert output")
    print("  --run-once           Run one check cycle and exit")
    print("  --export             Export balance + margin CSV and exit")
    print("  --export-customer ID Export for specific customer only")
    print("  --no-hot-reload      Disable settings hot-reload")
    print("  --help               Show this help")
    print()
    print("Settings in settings.toml  |  Credentials in .env")


def _apply_cli_overrides(settings: Settings, args: dict[str, object]) -> Settings:
    g = settings.global_
    if "interval" in args:
        g.check_interval = int(args["interval"])
    if "margin_below" in args:
        g.margin_below = float(args["margin_below"])
    if "margin_above" in args:
        g.margin_above = float(args["margin_above"])
    if "margin_deadband" in args:
        g.margin_deadband = float(args["margin_deadband"])
    if "billed_above" in args:
        g.billed_above = float(args["billed_above"])
    if "cooldown" in args:
        g.cooldown = int(args["cooldown"])
    if "balance_below" in args:
        val = float(args["balance_below"])
        for w in settings.watch:
            w.balance_below = val
    if args.get("quiet"):
        g._quiet = True
    return settings


def _qprint(settings: Settings, *a, **kw) -> None:
    if not getattr(settings.global_, "_quiet", False):
        print(*a, **kw)


def _sleep_interruptible(seconds: int) -> bool:
    for _ in range(seconds):
        if _SHUTDOWN.is_set():
            return True
        time.sleep(1)
    return False


def _print_banner(settings: Settings) -> None:
    g = settings.global_
    cids = settings.customer_ids
    audio_str = green("ON") if g.audio else dim("OFF")
    hot_reload_str = green("ON") if not getattr(g, "_no_hot_reload", False) else dim("OFF")
    print()
    print(f"  {bold('InstacallMonitor')}  {dim(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}")
    print(f"  {dim('─' * 50)}")
    print(f"  {bold(str(len(cids)))} customers monitored  |  Every {bold(str(g.check_interval))}s")
    print(
        f"  Margin range {yellow(f'{g.margin_below:.0f}%–{g.margin_above:.0f}%')}"
        f"  |  Deadband {yellow(f'{g.margin_deadband:.0f}%')}"
        f"  |  Billed above {yellow(f'{g.billed_above:.0f} min')}"
    )
    for w in settings.watch:
        bal = w.resolve_balance_below()
        print(f"    {w.customer}:  balance below {red(f'{bal:+.1f}')}")
    print(f"  Audio {audio_str}  |  Cooldown {g.cooldown}s  |  DB retention {g.db_retention_days}d")
    print(f"  Workers {bold(str(g.max_workers))}  |  Hot-reload {hot_reload_str}")
    print(f"  {dim('Ctrl+C to stop.')}")
    print()


def _run_export(settings: Settings, args: dict[str, object]) -> None:
    cid = str(args.get("export_customer", "")) or None
    b_file = export_balance_csv(customer_id=cid)
    m_file = export_margin_csv(customer_id=cid)
    print(f"  {green('Exported:')} {b_file}")
    print(f"  {green('Exported:')} {m_file}")


def _run_once(settings: Settings) -> None:
    g = settings.global_
    session = auth.create_session()
    if not auth.perform_login(session, g.request_timeout):
        print("  Login failed.")
        session.close()
        return
    init_db()
    configure_scrapers(g)

    print()
    print(f"  {bold('Quick Check')}  {dim(datetime.now().strftime('%H:%M:%S'))}")
    print(f"  {dim('─' * 50)}")

    customer_ids = settings.customer_ids
    with concurrent.futures.ThreadPoolExecutor(max_workers=g.max_workers) as executor:
        future_to_cid = {
            executor.submit(fetch_balance, session, cid, g.request_timeout): cid
            for cid in customer_ids
        }
        results: dict[str, tuple] = {}
        for future in concurrent.futures.as_completed(future_to_cid):
            cid = future_to_cid[future]
            try:
                results[cid] = future.result()
            except Exception as e:
                results[cid] = (None, None, None, f"thread error: {e}")

    for cid in customer_ids:
        name, balance, credit_limit, error = results.get(cid, (None, None, None, "no result"))
        ts = datetime.now().strftime("%H:%M:%S")
        print_balance_line(name or "N/A", cid, balance, credit_limit, error, ts=ts)
        if balance is not None:
            remaining = credit_limit + balance if credit_limit is not None else None
            insert_balance(BalanceRecord(
                customer_id=cid, customer_name=name or "N/A",
                balance=balance, credit_limit=credit_limit, remaining=remaining,
            ))

    print()
    print(f"  {bold('Summary')}")
    print(f"  {dim('─' * 50)}")
    summary, err = fetch_summary_report(session, settings)
    if summary:
        for cid, data in summary.items():
            ts = datetime.now().strftime("%H:%M:%S")
            print_summary_line(data, cid, monitored=(cid in settings.customer_ids), ts=ts,
                               margin_below=g.margin_below, margin_above=g.margin_above)
            m = data.get("margin")
            b = data.get("billed_min")
            insert_margin(MarginRecord(
                customer_id=cid, customer_name=str(data.get("name", "N/A")),
                margin=float(m) if isinstance(m, (int, float)) else None,
                billed_min=float(b) if isinstance(b, (int, float)) else None,
            ))
    else:
        print(f"  {dim('No data' + (f' — {err}' if err else ''))}")

    session.close()
    print()


def _try_hot_reload(current: Settings, args: dict[str, object]) -> Settings:
    if args.get("no_hot_reload"):
        return current

    new_settings = reload_settings(current)
    if new_settings is None:
        return current

    new_settings = _apply_cli_overrides(new_settings, args)
    errors = validate_settings(new_settings)
    if errors:
        for e in errors:
            print(f"  {red('[WARN]')} Settings reload validation failed: {e}")
        logging.warning(f"Settings reload validation failed: {'; '.join(errors)}")
        return current

    old_ids = set(current.customer_ids)
    new_ids = set(new_settings.customer_ids)
    added = sorted(new_ids - old_ids)
    removed = sorted(old_ids - new_ids)

    if added or removed:
        parts: list[str] = []
        if added:
            parts.append(f"added {', '.join(added)}")
        if removed:
            parts.append(f"removed {', '.join(removed)}")
        print(f"  {cyan('[RELOAD]')} Customers: {', '.join(parts)}")
        logging.info(f"Hot-reload customers: {', '.join(parts)}")

    same = new_ids & old_ids
    for cid in sorted(same):
        old_w = current.get_watch(cid)
        new_w = new_settings.get_watch(cid)
        changes: list[str] = []
        for attr in ("balance_below", "margin_below", "margin_above", "margin_deadband", "billed_above"):
            oval = getattr(old_w, attr, None)
            nval = getattr(new_w, attr, None)
            if oval != nval:
                changes.append(f"{attr}: {oval} -> {nval}")
        if changes:
            print(f"  {cyan('[RELOAD]')} Customer {cid}: {'; '.join(changes)}")

    g1 = current.global_
    g2 = new_settings.global_
    g_changes: list[str] = []
    for attr in (
        "check_interval", "cooldown", "margin_below", "margin_above", "margin_deadband",
        "billed_above", "max_workers", "audio", "circuit_failure_threshold",
        "circuit_recovery_timeout",
    ):
        ov = getattr(g1, attr, None)
        nv = getattr(g2, attr, None)
        if ov != nv:
            g_changes.append(f"{attr}: {ov} -> {nv}")
    if g_changes:
        parts_str = "; ".join(g_changes[:3])
        if len(g_changes) > 3:
            parts_str += f" (+{len(g_changes) - 3} more)"
        print(f"  {cyan('[RELOAD]')} Settings: {parts_str}")
        logging.info(f"Hot-reload settings: {parts_str}")

        configure_scrapers(g2)

    print(f"  {cyan('[RELOAD]')} Settings reloaded (v{get_settings_version()})")
    return new_settings


def _monitor_loop(settings: Settings) -> None:
    global _CYCLE_COUNT, _ERROR_COUNT, _LAST_ERROR, _LAST_CHECK

    g = settings.global_
    customer_ids = settings.customer_ids
    interval = g.check_interval
    timeout = g.request_timeout

    args = {"no_hot_reload": getattr(g, "_no_hot_reload", False)}

    session = auth.create_session()
    if not auth.perform_login(session, g.request_timeout):
        print("  Login failed. Exiting.")
        session.close()
        return

    init_db()
    configure_scrapers(g)

    mgr = SirenManager(
        siren_loops=g.siren_loops,
        siren_min_freq=g.siren_min_freq,
        siren_max_freq=g.siren_max_freq,
        siren_step_freq=g.siren_step_freq,
        siren_tone_duration=g.siren_tone_duration,
    )

    last_balance: dict[str, tuple[float, Optional[float]]] = {}
    last_margin: dict[str, tuple[Optional[float], Optional[float]]] = {}

    _print_banner(settings)

    try:
        while not _SHUTDOWN.is_set():
            settings = _try_hot_reload(settings, args)
            g = settings.global_
            customer_ids = settings.customer_ids
            interval = g.check_interval
            timeout = g.request_timeout

            now_ts = datetime.now().strftime("%H:%M:%S")
            _qprint(
                settings,
                f"  [{now_ts}] Checking balances ({len(customer_ids)} customers, {g.max_workers} workers)...",
            )

            with concurrent.futures.ThreadPoolExecutor(max_workers=g.max_workers) as executor:
                future_to_cid = {
                    executor.submit(fetch_balance, session, cid, timeout): cid
                    for cid in customer_ids
                }
                results: dict[str, tuple] = {}
                for future in concurrent.futures.as_completed(future_to_cid):
                    if _SHUTDOWN.is_set():
                        executor.shutdown(wait=False, cancel_futures=True)
                        break
                    cid = future_to_cid[future]
                    try:
                        results[cid] = future.result()
                    except Exception as e:
                        results[cid] = (None, None, None, f"thread error: {e}")

            if _SHUTDOWN.is_set():
                break

            for cid in customer_ids:
                name, balance, credit_limit, error = results.get(cid, (None, None, None, "no result"))
                ts = datetime.now().strftime("%H:%M:%S")
                print_balance_line(name or "N/A", cid, balance, credit_limit, error, ts=ts)

                if error:
                    _ERROR_COUNT += 1
                    _LAST_ERROR = f"{cid}: {error}"

                if balance is not None:
                    prev = last_balance.get(cid)
                    curr = (balance, credit_limit)
                    if prev != curr:
                        last_balance[cid] = curr
                        remaining = credit_limit + balance if credit_limit is not None else None
                        try:
                            insert_balance(BalanceRecord(
                                customer_id=cid, customer_name=name or "N/A",
                                balance=balance, credit_limit=credit_limit, remaining=remaining,
                            ))
                        except Exception as e:
                            logging.error(f"DB insert balance failed for {cid}: {e}")
                            _ERROR_COUNT += 1
                            _LAST_ERROR = str(e)

                    try:
                        check_balance_alert(mgr, cid, balance, name or "N/A", settings)
                    except Exception as e:
                        logging.error(f"Alert check failed for {cid}: {e}\n{traceback.format_exc()}")
                        _ERROR_COUNT += 1
                        _LAST_ERROR = str(e)

            _qprint(settings)

            if _SHUTDOWN.is_set():
                break

            summary, summary_error = fetch_summary_report(session, settings)
            if summary_error:
                _ERROR_COUNT += 1
                _LAST_ERROR = f"summary: {summary_error}"

            if not summary:
                _qprint(settings, f"  {dim('Summary: no data' + (f' — {summary_error}' if summary_error else ''))}")
            else:
                _qprint(settings, f"  Summary ({len(summary)} customers)")
                _qprint(settings, f"  {dim('─' * 50)}")

                for cid, data in summary.items():
                    monitored = cid in customer_ids
                    ts = datetime.now().strftime("%H:%M:%S")
                    print_summary_line(data, cid, monitored=monitored, ts=ts,
                                       margin_below=g.margin_below, margin_above=g.margin_above)

                    margin = data.get("margin")
                    billed_min = data.get("billed_min")

                    m_prev = last_margin.get(cid)
                    m_curr = (
                        float(margin) if isinstance(margin, (int, float)) else None,
                        float(billed_min) if isinstance(billed_min, (int, float)) else None,
                    )
                    if m_prev != m_curr:
                        last_margin[cid] = m_curr
                        try:
                            insert_margin(MarginRecord(
                                customer_id=cid, customer_name=str(data.get("name", "N/A")),
                                margin=float(margin) if isinstance(margin, (int, float)) else None,
                                billed_min=float(billed_min) if isinstance(billed_min, (int, float)) else None,
                            ))
                        except Exception as e:
                            logging.error(f"DB insert margin failed for {cid}: {e}")
                            _ERROR_COUNT += 1
                            _LAST_ERROR = str(e)

                    if isinstance(margin, (int, float)) and isinstance(billed_min, (int, float)):
                        try:
                            check_margin_alert(mgr, cid, float(margin), float(billed_min),
                                              str(data.get("name", "N/A")), settings, monitored=monitored)
                        except Exception as e:
                            logging.error(f"Margin alert check failed for {cid}: {e}")
                            _ERROR_COUNT += 1
                            _LAST_ERROR = str(e)

            try:
                purge_old_records(g.db_retention_days)
            except Exception as e:
                logging.error(f"DB purge failed: {e}")
                _ERROR_COUNT += 1
                _LAST_ERROR = str(e)

            _CYCLE_COUNT += 1
            _LAST_CHECK = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _write_health_status(settings)

            next_time = datetime.now().strftime("%H:%M:%S")
            _qprint(settings)
            _qprint(settings, f"  {dim('─' * 50)}")
            _qprint(settings, f"  [{next_time}] Cycle #{_CYCLE_COUNT} complete. Next in {interval}s")
            _qprint(settings)

            if _sleep_interruptible(interval) and not _SHUTDOWN.is_set():
                pass

    except KeyboardInterrupt:
        _SHUTDOWN.set()
        _qprint(settings)
        _qprint(settings, f"  {bold('Shutting down...')}")
    finally:
        _SHUTDOWN.set()
        _write_health_status(settings)
        session.close()


def main() -> None:
    global _START_TIME, _ERROR_COUNT, _LAST_ERROR, _LAST_CHECK, _CYCLE_COUNT

    setup_logging()
    args = _parse_args()

    if args.get("help"):
        _print_help()
        sys.exit(0)

    settings = load_settings()
    settings = _apply_cli_overrides(settings, args)

    if args.get("no_hot_reload"):
        settings.global_._no_hot_reload = True

    errors = validate_settings(settings)
    if errors:
        print("Invalid settings:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    try:
        get_credentials()
    except ValueError as e:
        print(f"Credential error: {e}")
        sys.exit(1)

    init_db()

    _START_TIME = time.time()
    atexit.register(lambda: _on_exit(settings)())

    try:
        signal.signal(signal.SIGINT, lambda s, f: _SHUTDOWN.set())
    except (ValueError, AttributeError):
        pass  # SIGINT already handled via KeyboardInterrupt in _monitor_loop

    if args.get("export"):
        _run_export(settings, args)
        sys.exit(0)

    if args.get("run_once"):
        _CYCLE_COUNT = 1
        _LAST_CHECK = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _START_TIME = time.time()
        _run_once(settings)
        sys.exit(0)

    _monitor_loop(settings)


if __name__ == "__main__":
    main()
