from __future__ import annotations

import sys
import time
from datetime import datetime
from typing import Optional

import auth
from alerts import SirenManager, check_balance_alert, check_margin_alert
from config import (
    Settings,
    get_credentials,
    load_settings,
    setup_logging,
    validate_settings,
)
from display import bold, dim, green, print_balance_line, print_summary_line, red, yellow
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
from scrapers import fetch_balance, fetch_summary_report


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
    print("  --margin-below N     Override margin threshold (%)")
    print("  --billed-above N    Override billed minutes threshold")
    print("  --cooldown N         Alert cooldown in seconds")
    print("  --quiet              Suppress non-alert output")
    print("  --run-once           Run one check cycle and exit")
    print("  --export             Export balance + margin CSV and exit")
    print("  --export-customer ID Export for specific customer only")
    print("  --help               Show this help")
    print()
    print("Settings in settings.toml  |  Credentials in .env")


def _apply_cli_overrides(settings: Settings, args: dict[str, object]) -> Settings:
    g = settings.global_
    if "interval" in args:
        g.check_interval = int(args["interval"])
    if "margin_below" in args:
        g.margin_below = float(args["margin_below"])
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


def _sleep(seconds: int) -> None:
    for _ in range(seconds):
        time.sleep(1)


def _print_banner(settings: Settings) -> None:
    g = settings.global_
    cids = settings.customer_ids
    audio_str = green("ON") if g.audio else dim("OFF")
    print()
    print(f"  {bold('InstacallMonitor')}  {dim(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}")
    print(f"  {dim('─' * 50)}")
    print(f"  {bold(str(len(cids)))} customers monitored  |  Every {bold(str(g.check_interval))}s")
    print(f"  Margin below {yellow(f'{g.margin_below:.0f}%')}  |  Billed above {yellow(f'{g.billed_above:.0f} min')}")
    for w in settings.watch:
        bal = w.resolve_balance_below()
        print(f"    {w.customer}:  balance below {red(f'{bal:+.1f}')}")
    print(f"  Audio  {audio_str}  |  Cooldown {g.cooldown}s  |  DB retention {g.db_retention_days}d")
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

    print()
    print(f"  {bold('Quick Check')}  {dim(datetime.now().strftime('%H:%M:%S'))}")
    print(f"  {dim('─' * 50)}")

    for cid in settings.customer_ids:
        name, balance, credit_limit, error = fetch_balance(session, cid, g.request_timeout)
        ts = datetime.now().strftime("%H:%M:%S")
        print_balance_line(name or "N/A", cid, balance, credit_limit, error, ts=ts)
        if balance is not None:
            remaining = credit_limit + balance if credit_limit is not None else None
            insert_balance(BalanceRecord(
                customer_id=cid, customer_name=name or "N/A",
                balance=balance, credit_limit=credit_limit, remaining=remaining,
            ))
        time.sleep(0.3)

    print()
    print(f"  {bold('Summary')}")
    print(f"  {dim('─' * 50)}")
    summary, err = fetch_summary_report(session, settings)
    if summary:
        for cid, data in summary.items():
            ts = datetime.now().strftime("%H:%M:%S")
            print_summary_line(data, cid, monitored=(cid in settings.customer_ids), ts=ts)
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


def _monitor_loop(settings: Settings) -> None:
    g = settings.global_
    customer_ids = settings.customer_ids
    interval = g.check_interval
    timeout = g.request_timeout

    session = auth.create_session()
    if not auth.perform_login(session, g.request_timeout):
        print("  Login failed. Exiting.")
        session.close()
        return

    init_db()
    mgr = SirenManager()

    last_balance: dict[str, tuple[float, Optional[float]]] = {}
    last_margin: dict[str, tuple[Optional[float], Optional[float]]] = {}

    _print_banner(settings)

    try:
        while True:
            now_ts = datetime.now().strftime("%H:%M:%S")
            _qprint(settings, f"  [{now_ts}] Checking balances ({len(customer_ids)} customers)...")

            for cid in customer_ids:
                name, balance, credit_limit, error = fetch_balance(session, cid, timeout)
                ts = datetime.now().strftime("%H:%M:%S")
                print_balance_line(name or "N/A", cid, balance, credit_limit, error, ts=ts)

                if balance is not None:
                    prev = last_balance.get(cid)
                    curr = (balance, credit_limit)
                    if prev != curr:
                        last_balance[cid] = curr
                        remaining = credit_limit + balance if credit_limit is not None else None
                        insert_balance(BalanceRecord(
                            customer_id=cid, customer_name=name or "N/A",
                            balance=balance, credit_limit=credit_limit, remaining=remaining,
                        ))

                    check_balance_alert(mgr, cid, balance, name or "N/A", settings)
                _sleep(1)

            _qprint(settings)

            summary, summary_error = fetch_summary_report(session, settings)
            if not summary:
                _qprint(settings, f"  {dim('Summary: no data' + (f' — {summary_error}' if summary_error else ''))}")
            else:
                _qprint(settings, f"  Summary ({len(summary)} customers)")
                _qprint(settings, f"  {dim('─' * 50)}")

                for cid, data in summary.items():
                    monitored = cid in customer_ids
                    ts = datetime.now().strftime("%H:%M:%S")
                    print_summary_line(data, cid, monitored=monitored, ts=ts)

                    margin = data.get("margin")
                    billed_min = data.get("billed_min")

                    m_prev = last_margin.get(cid)
                    m_curr = (
                        float(margin) if isinstance(margin, (int, float)) else None,
                        float(billed_min) if isinstance(billed_min, (int, float)) else None,
                    )
                    if m_prev != m_curr:
                        last_margin[cid] = m_curr
                        insert_margin(MarginRecord(
                            customer_id=cid, customer_name=str(data.get("name", "N/A")),
                            margin=float(margin) if isinstance(margin, (int, float)) else None,
                            billed_min=float(billed_min) if isinstance(billed_min, (int, float)) else None,
                        ))

                    if isinstance(margin, (int, float)) and isinstance(billed_min, (int, float)):
                        check_margin_alert(mgr, cid, float(margin), float(billed_min),
                                          str(data.get("name", "N/A")), settings, monitored=monitored)

            purge_old_records(g.db_retention_days)

            next_time = datetime.now().strftime("%H:%M:%S")
            _qprint(settings)
            _qprint(settings, f"  {dim('─' * 50)}")
            _qprint(settings, f"  [{next_time}] Cycle complete. Next in {interval}s")
            _qprint(settings)
            _sleep(interval)

    except KeyboardInterrupt:
        print(f"\n  {bold('Shutting down...')}")
    finally:
        session.close()


def main() -> None:
    setup_logging()
    args = _parse_args()

    if args.get("help"):
        _print_help()
        sys.exit(0)

    settings = load_settings()
    settings = _apply_cli_overrides(settings, args)

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

    if args.get("export"):
        _run_export(settings, args)
        sys.exit(0)

    if args.get("run_once"):
        _run_once(settings)
        sys.exit(0)

    _monitor_loop(settings)


if __name__ == "__main__":
    main()
