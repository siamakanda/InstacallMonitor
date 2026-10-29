from __future__ import annotations

import atexit
import concurrent.futures
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime
from typing import Any, Optional

import auth
import display
from alerts import SirenManager, reset_session_stats
from cli import Args, apply_cli_overrides, parse_args, print_help
from config import (
    SETTINGS_FILE,
    SettingsError,
    get_credentials,
    get_settings_version,
    load_settings,
    reload_settings,
    setup_logging,
    validate_settings,
)
from cycles import process_balances, process_summary
from display import bold, cyan, dim, green, red
from health import render_status, render_tail, write_status
from persistence import init_db, purge_old_records
from reporting import print_banner, print_dashboard_url, print_session_summary, run_export
from runtime import Runtime
from scrapers import BalanceResult, fetch_balance, fetch_summary_report
from settings_diff import diff_settings
from settings_models import Settings
from webapp import MonitorState, create_dashboard

_RELOAD_GLOBAL_ATTRS = (
    "check_interval",
    "cooldown",
    "margin_below",
    "margin_above",
    "margin_deadband",
    "billed_above",
    "max_workers",
    "audio",
)


def _login_with_spinner(session, timeout: int) -> bool:
    result_holder: list[bool] = []
    done = threading.Event()

    def _do_login() -> None:
        try:
            result_holder.append(auth.perform_login(session, timeout))
        except Exception:
            logging.exception("Login worker crashed")
            result_holder.append(False)
        finally:
            done.set()

    t = threading.Thread(target=_do_login, daemon=True)
    t.start()
    chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    i = 0
    while not done.is_set():
        sys.stderr.write(f"\r  {chars[i % len(chars)]} Logging in...")
        sys.stderr.flush()
        time.sleep(0.1)
        i += 1
    sys.stderr.write("\r" + " " * 30 + "\r")
    sys.stderr.flush()
    t.join()
    return result_holder[0] if result_holder else False


def _qprint(quiet: bool, *a, **kw) -> None:
    if not quiet:
        print(*a, **kw)


def _sleep_interruptible(seconds: int, shutdown: threading.Event, wake: threading.Event | None = None) -> None:
    for _ in range(max(seconds, 1)):
        if shutdown.is_set() or (wake is not None and wake.is_set()):
            return
        time.sleep(1)


def _collect_balances(
    executor: concurrent.futures.ThreadPoolExecutor,
    session,
    customer_ids: list[str],
    timeout: int,
    shutdown: threading.Event | None = None,
) -> dict[str, BalanceResult]:
    future_to_cid = {executor.submit(fetch_balance, session, cid, timeout): cid for cid in customer_ids}
    results: dict[str, BalanceResult] = {}
    for future in concurrent.futures.as_completed(future_to_cid):
        if shutdown is not None and shutdown.is_set():
            executor.shutdown(wait=False, cancel_futures=True)
            break
        cid = future_to_cid[future]
        try:
            results[cid] = future.result()
        except Exception as e:
            results[cid] = (None, None, None, f"thread error: {e}")
    return results


def run_once(settings: Settings, quiet: bool = False) -> None:
    """Run a single monitoring cycle through the same pipeline as the loop."""
    g = settings.global_
    session = auth.create_session()
    try:
        if not _login_with_spinner(session, g.request_timeout):
            _qprint(quiet, "  Login failed.")
            return
        init_db()

        state = MonitorState()
        runtime = Runtime()
        runtime.start()
        mgr = SirenManager()

        _qprint(quiet)
        _qprint(quiet, f"  {bold('Quick Check')}  {dim(datetime.now().strftime('%H:%M:%S'))}")
        _qprint(quiet, f"  {dim('─' * 50)}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=g.max_workers) as executor:
            results = _collect_balances(executor, session, settings.customer_ids, g.request_timeout)

        process_balances(results, mgr, settings, state, runtime, {})

        _qprint(quiet)
        _qprint(quiet, f"  {bold('Summary')}")
        _qprint(quiet, f"  {dim('─' * 50)}")
        summary, err = fetch_summary_report(session, settings)
        if summary:
            process_summary(summary, mgr, settings, state, runtime, {})
        else:
            _qprint(quiet, f"  {dim('No data' + (f' — {err}' if err else ''))}")
    finally:
        session.close()
        _qprint(quiet)


def _try_hot_reload(current: Settings, args: Args) -> Settings:
    if args.get("no_hot_reload"):
        return current

    new_settings = reload_settings(current)
    if new_settings is None:
        return current

    new_settings = apply_cli_overrides(new_settings, args)
    errors = validate_settings(new_settings)
    if errors:
        for e in errors:
            print(f"  {red('[WARN]')} Settings reload validation failed: {e}")
        logging.warning(f"Settings reload validation failed: {'; '.join(errors)}")
        return current

    diff = diff_settings(current, new_settings)

    if diff.added or diff.removed:
        parts: list[str] = []
        if diff.added:
            parts.append(f"added {', '.join(diff.added)}")
        if diff.removed:
            parts.append(f"removed {', '.join(diff.removed)}")
        print(f"  {cyan('[RELOAD]')} Customers: {', '.join(parts)}")
        logging.info(f"Hot-reload customers: {', '.join(parts)}")

    for cid, changes in sorted(diff.customer_changes.items()):
        if changes:
            print(f"  {cyan('[RELOAD]')} Customer {cid}: {'; '.join(changes)}")

    global_changes = [c for c in diff.global_changes if c.split(":", 1)[0].strip() in _RELOAD_GLOBAL_ATTRS]
    if global_changes:
        parts_str = "; ".join(global_changes[:3])
        if len(global_changes) > 3:
            parts_str += f" (+{len(global_changes) - 3} more)"
        print(f"  {cyan('[RELOAD]')} Settings: {parts_str}")
        logging.info(f"Hot-reload settings: {parts_str}")

    print(f"  {cyan('[RELOAD]')} Settings reloaded (v{get_settings_version()})")
    return new_settings


def monitor_loop(settings: Settings, args: Args, runtime: Runtime) -> None:
    quiet = bool(args.get("quiet"))

    session: Any = None
    executor: Any = None
    dashboard: Any = None
    reached_loop = False
    state = MonitorState()

    try:
        g = settings.global_
        customer_ids = settings.customer_ids
        interval = g.check_interval
        timeout = g.request_timeout

        session = auth.create_session()
        if not _login_with_spinner(session, g.request_timeout):
            print("  Login failed. Exiting.")
            return

        init_db()

        mgr = SirenManager()

        state.set_cycle(interval=interval, settings_version=get_settings_version())
        dashboard = create_dashboard(state, settings)

        last_balance: dict[str, tuple[float, Optional[float]]] = {}
        last_margin: dict[str, tuple[Optional[float], Optional[float]]] = {}

        print_banner(settings, hot_reload=not args.get("no_hot_reload"))
        if dashboard is not None:
            print_dashboard_url(dashboard.url)

        workers = g.max_workers
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        reached_loop = True

        while not runtime.shutdown.is_set():
            settings = _try_hot_reload(settings, args)
            g = settings.global_
            customer_ids = settings.customer_ids
            interval = g.check_interval
            timeout = g.request_timeout

            if g.max_workers != workers:
                executor.shutdown(wait=False)
                workers = g.max_workers
                executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)

            state.set_cycle(
                interval=interval,
                settings_version=get_settings_version(),
                cycle_count=runtime.cycle_count,
            )

            now_ts = datetime.now().strftime("%H:%M:%S")
            _qprint(
                quiet,
                f"  [{now_ts}] Checking balances ({len(customer_ids)} customers, {g.max_workers} workers)...",
            )

            results = _collect_balances(executor, session, customer_ids, timeout, runtime.shutdown)

            if runtime.shutdown.is_set():
                break

            process_balances(results, mgr, settings, state, runtime, last_balance)

            _qprint(quiet)

            if runtime.shutdown.is_set():
                break

            summary, summary_error = fetch_summary_report(session, settings)
            if summary_error:
                runtime.note_error(f"summary: {summary_error}")

            if not summary:
                _qprint(quiet, f"  {dim('Summary: no data' + (f' — {summary_error}' if summary_error else ''))}")
            else:
                _qprint(quiet, f"  Summary ({len(summary)} customers)")
                _qprint(quiet, f"  {dim('─' * 50)}")

                process_summary(summary, mgr, settings, state, runtime, last_margin)

            try:
                purge_old_records(g.db_retention_days)
            except Exception as e:
                logging.error(f"DB purge failed: {e}")
                runtime.note_error(str(e))

            runtime.record_cycle(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            write_status(runtime, settings)
            state.set_cycle(
                cycle_count=runtime.cycle_count,
                last_check=runtime.last_check,
                error_count=runtime.error_count,
                last_error=runtime.last_error,
                interval=interval,
                settings_version=get_settings_version(),
            )

            next_time = datetime.now().strftime("%H:%M:%S")
            _qprint(quiet)
            _qprint(quiet, f"  {dim('─' * 50)}")
            _qprint(quiet, f"  [{next_time}] Cycle #{runtime.cycle_count} complete. Next in {interval}s")
            _qprint(quiet)

            _sleep_interruptible(interval, runtime.shutdown, state.reload_event)
            state.clear_reload()

    finally:
        runtime.shutdown.set()
        write_status(runtime, settings)
        state.set_cycle(alive=False, error_count=runtime.error_count, last_error=runtime.last_error)
        if dashboard is not None:
            dashboard.stop()
        if executor is not None:
            executor.shutdown(wait=False)
        if session is not None:
            session.close()
        if reached_loop:
            print_session_summary(runtime)


def main() -> None:
    display.init()
    setup_logging()
    reset_session_stats()
    args = parse_args()

    if args.get("help"):
        print_help()
        sys.exit(0)

    if args.get("status"):
        render_status()
        sys.exit(0)

    if "tail" in args:
        render_tail(int(args["tail"]))
        sys.exit(0)

    settings_file_existed = os.path.exists(SETTINGS_FILE)

    try:
        settings = load_settings()
    except SettingsError as exc:
        print(f"  {red('[ERROR]')} {exc}")
        print(f"  {dim('Fix or remove settings.toml, then restart.')}")
        sys.exit(1)
    settings = apply_cli_overrides(settings, args)

    if not settings_file_existed:
        print(f"  {green('[INFO]')} Created {bold('settings.toml')} — edit it to configure monitored customers.")
        print(f"  {dim('    If this is your first run, also create a .env file.')}")
        print(f"  {dim('    See .env.example for the required fields.')}")
        print()

    errors = validate_settings(settings)
    if errors:
        print(f"  {red('[ERROR]')} Invalid settings {'(see settings.toml)' if settings_file_existed else ''}:")
        for e in errors:
            print(f"    - {e}")
        print()
        sys.exit(1)

    try:
        get_credentials()
    except ValueError as e:
        print(f"  {red('[ERROR]')} {e}")
        print(f"  {dim('Create a .env file with PORTAL_USERNAME and PORTAL_PASSWORD, or copy .env.example.')}")
        sys.exit(1)

    init_db()

    runtime = Runtime()
    runtime.start()

    def _on_exit() -> None:
        runtime.shutdown.set()
        write_status(runtime, settings)

    atexit.register(_on_exit)

    def _request_shutdown(signum: int, frame: Any) -> None:
        runtime.shutdown.set()

    for _sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(_sig, _request_shutdown)
        except (ValueError, AttributeError, OSError):
            pass

    if args.get("export"):
        run_export(args)
        sys.exit(0)

    if args.get("run_once"):
        runtime.record_cycle(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        run_once(settings, quiet=bool(args.get("quiet")))
        sys.exit(0)

    monitor_loop(settings, args, runtime)
