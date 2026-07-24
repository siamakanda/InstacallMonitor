from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

import requests

from auth import create_session, perform_login
from config import STATUS_FILE, Settings, WatchTarget, save_settings
from display import bold, dim, green, red, yellow
from monitor import run_monitor
from persistence import get_alert_history, get_balance_history, get_margin_history, init_db
from scrapers import (
    SummaryRow,
    fetch_balance,
    fetch_summary_report,
    summaries_to_rows,
)

W = 80
_ANSI_RE = re.compile(r'\033\[[0-9;]*m')

# box-drawing Unicode constants (avoid backslash-in-fstring on Python 3.10)
_BH = "\u2500"  # horizontal
_BV = "\u2502"  # vertical
_BTL = "\u256d"  # top-left
_BTR = "\u256e"  # top-right
_BBL = "\u2570"  # bottom-left
_BBR = "\u256f"  # bottom-right
_BML = "\u251c"  # middle-left
_BMR = "\u2524"  # middle-right
_WARN = "\u26a0"
_CHECK = "\u2713"
_PLAY = "\u25b6"
_STOP = "\u25a0"
_EM = "\u2014"
_HELLIP = "\u2026"

_monitor_thread: Optional[threading.Thread] = None
_monitor_stop: Optional[threading.Event] = None

if sys.platform == "win32":
    os.system("")


def _monitor_running() -> bool:
    try:
        with open(STATUS_FILE, 'r') as f:
            return json.load(f).get("alive", False)
    except Exception:
        return False


def _clear_screen() -> None:
    os.system("cls" if sys.platform == "win32" else "clear")


def _visible_len(text: str) -> int:
    return len(_ANSI_RE.sub('', text))


def _truncate(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return text[:width - 1] + _HELLIP


def _col(text: str, width: int, align: str = '<') -> str:
    visible = _visible_len(text)
    if align == '>':
        return ' ' * max(0, width - visible) + text
    return text + ' ' * max(0, width - visible)


def _pad_inner(line: str, inner: int) -> str:
    visible = _visible_len(line)
    return line + ' ' * max(0, inner - visible)


# --- formatting helpers ---

def _fmt_bal(balance: Optional[float]) -> str:
    if balance is None:
        return dim(_EM)
    if balance < 0:
        return red(f"${balance:.2f}")
    return green(f"${balance:.2f}")


def _fmt_credit(credit_limit: Optional[float]) -> str:
    if credit_limit is None:
        return dim(_EM)
    return f"${credit_limit:.2f}"


def _fmt_rem(balance: Optional[float], credit_limit: Optional[float]) -> str:
    if balance is None or credit_limit is None:
        return dim(_EM)
    val = credit_limit + balance
    if val < 0:
        return red(f"${val:.2f}")
    return green(f"${val:.2f}")


def _fmt_margin(margin: Optional[float]) -> str:
    if margin is None:
        return dim(_EM)
    v = f"{margin:.1f}%"
    if margin < 30:
        return red(v)
    if margin < 50:
        return yellow(v)
    return green(v)


def _fmt_billed(billed: Optional[float]) -> str:
    if billed is None:
        return dim(_EM)
    return f"{billed:,.1f}"


def _fmt_calls(val: Optional[float]) -> str:
    if val is None:
        return dim(_EM)
    return f"{val:,.0f}"


def _fmt_asr(val: Optional[float]) -> str:
    if val is None:
        return dim(_EM)
    return f"{val:.1f}%"


def _fmt_profit(val: Optional[float]) -> str:
    if val is None:
        return dim(_EM)
    return f"${val:.2f}"


# --- data fetching ---

def _fetch_balances_dashboard(
    session: requests.Session,
    customer_ids: list[str],
    timeout: int = 10,
) -> list[tuple[str, Optional[str], Optional[float], Optional[float], Optional[str]]]:
    if not customer_ids:
        return []
    results: list[tuple[str, Optional[str], Optional[float], Optional[float], Optional[str]]] = []

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(customer_ids)))) as executor:
        futures = {executor.submit(fetch_balance, session, cid, timeout): cid for cid in customer_ids}
        for future in as_completed(futures):
            cid = futures[future]
            try:
                name, balance, credit_limit, error = future.result()
            except Exception as e:
                name, balance, credit_limit, error = None, None, None, str(e)
            results.append((cid, name, balance, credit_limit, error))

    results.sort(key=lambda x: x[0])
    return results


def fetch_dashboard_data(
    session: requests.Session,
    settings: Settings,
) -> tuple[list[SummaryRow], dict[str, tuple[str, Optional[float], Optional[float]]], Optional[str]]:
    timeout = settings.global_.request_timeout

    summary_dict, summary_error = fetch_summary_report(session, settings)
    summary_rows = summaries_to_rows(summary_dict)
    summary_rows.sort(key=lambda r: r.calls if r.calls is not None else -1, reverse=True)

    all_ids = list(set(settings.customer_ids) | {str(r.customer_id) for r in summary_rows})
    balance_results = _fetch_balances_dashboard(session, all_ids, timeout)

    balance_lookup: dict[str, tuple[str, Optional[float], Optional[float]]] = {}
    for cid, name, balance, credit_limit, _error in balance_results:
        balance_lookup[cid] = (name or "", balance, credit_limit)

    return summary_rows, balance_lookup, summary_error


# --- keyboard ---

def _keyboard_poll(timeout: float) -> Optional[str]:
    if sys.platform == "win32":
        try:
            import msvcrt
        except ImportError:
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            if msvcrt.kbhit():
                key = msvcrt.getch()
                try:
                    return key.decode("utf-8").upper()
                except UnicodeDecodeError:
                    return "?"
            time.sleep(0.05)
        return None
    else:
        try:
            import select
            ready, _, _ = select.select([sys.stdin], [], [], timeout)
            if ready:
                return sys.stdin.readline().strip().upper()
        except (ImportError, OSError):
            pass
        time.sleep(timeout)
        return None


# --- rendering helpers ---

def _box_top() -> None:
    inner = W - 2
    print(dim("  " + _BTL + _BH * inner + _BTR))


def _box_bottom() -> None:
    inner = W - 2
    print(dim("  " + _BBL + _BH * inner + _BBR))


def _box_sep() -> None:
    inner = W - 2
    print(dim("  " + _BML + _BH * inner + _BMR))


def _box_thin_sep() -> None:
    inner = W - 2
    print(dim("  " + _BV + _BH * inner + _BV))


def _box_line(text: str) -> None:
    inner = W - 2
    print(dim("  " + _BV + " ") + _pad_inner(text, inner) + dim(" " + _BV))


# --- render functions ---

def _render_header(
    settings: Settings,
    running: bool,
    next_refresh: float,
    last_check: str,
    errors: Optional[str],
    alert_count: int = 0,
) -> None:
    remaining = max(0, int(next_refresh - time.time()))
    if remaining == 0:
        countdown = "refreshing..."
    else:
        countdown = f"next in {remaining}s"

    g = settings.global_
    status = green(_PLAY + " RUNNING") if running else dim(_STOP + " STOPPED")

    alert_badge = ""
    if alert_count > 0:
        alert_badge = "  " + red(f"{_WARN} {alert_count} alert" + ("s" if alert_count > 1 else ""))

    line1 = f"InstacallMonitor  {dim(last_check)}  {status}  {bold(countdown)}{alert_badge}"
    _box_line(line1)

    sub = f"{len(settings.watch)} monitored  interval {g.check_interval}s"
    if errors:
        sub = red(_WARN + " " + errors)
    _box_line(sub)


def _render_alert_rows(
    balance_results: list[tuple[str, Optional[str], Optional[float], Optional[float], Optional[str]]],
    summary_rows: list[SummaryRow],
    settings: Settings,
    mar_count: int = 0,
) -> None:
    watch_ids = set(settings.customer_ids)
    g = settings.global_

    _box_line("  " + bold(f"BALANCE ({len(settings.watch)} monitored)"))
    _box_thin_sep()

    watched_shown = 0
    for cid, name, balance, credit_limit, error in balance_results:
        if cid not in watch_ids:
            continue
        watched_shown += 1
        if balance is None:
            display_name = _truncate(f"{name or cid} ({cid})", 22)
            _box_line(f"  {dim('? BALANCE')}  {display_name:22}  {red('FETCH FAILED')}")
            continue
        cust = settings.get_watch(cid)
        threshold = cust.resolve_balance_below()
        critical = cust.resolve_balance_critical()
        breached = balance < threshold

        display_name = _truncate(f"{name or cid} ({cid})", 22)
        bal_v = f"${balance:+.0f}"
        bal_colored = red(bal_v) if balance < 0 else green(bal_v)
        cr_v = f"${credit_limit:.0f}" if credit_limit is not None else dim(_EM)
        rem_v = f"${credit_limit + balance:.0f}" if credit_limit is not None else dim(_EM)
        if credit_limit is not None:
            rem_colored = red(rem_v) if (credit_limit + balance < 0) else green(rem_v)
        else:
            rem_colored = rem_v

        if critical != threshold:
            thr_str = f"< {threshold:+.0f} / {critical:+.0f}"
        else:
            thr_str = f"< {threshold:+.0f}"

        if breached:
            tag = red(_WARN + " BALANCE")
            thr_colored = red(f"[{thr_str}]")
        else:
            tag = green(_CHECK + " BALANCE")
            thr_colored = dim(f"[{thr_str}]")

        line = f"  {tag}  {display_name:22} {bal_colored}  Credit {cr_v}  Remain {rem_colored}  {thr_colored}"
        _box_line(line)

    for cid in sorted(watch_ids):
        if any(r[0] == cid for r in balance_results):
            continue
        w = settings.get_watch(cid)
        display_name = _truncate(f"{w.name or ''} ({cid})", 22)
        _box_line(f"  {dim('? BALANCE')}  {display_name:22}  {dim('no data yet')}")

    if watched_shown == 0:
        _box_line("  " + dim("No monitored customers configured."))

    _box_thin_sep()

    alert_lines: list[str] = []
    for row in summary_rows:
        marg = row.margin
        billed = row.billed_min
        if marg is None or billed is None:
            continue
        cust = settings.get_watch(row.customer_id)
        margin_below = cust.resolve_margin_below(g.margin_below)
        billed_above = cust.resolve_billed_above(g.billed_above)
        if not (marg < margin_below and billed > billed_above):
            continue

        display_name = _truncate(f"{row.customer_name} ({row.customer_id})", 22)
        billed_str = f"{billed:,.1f} min"
        margin_str = _fmt_margin(marg)
        margin_critical = cust.resolve_margin_critical(g.margin_critical)
        if margin_critical < margin_below:
            thr = f"< {margin_below:.0f}% / {margin_critical:.0f}%"
        else:
            thr = f"< {margin_below:.0f}%"
        tag = yellow(_WARN + " MARGIN")
        line = f"  {tag}  {display_name:22}  {billed_str}  margin {margin_str}  [{thr}]"
        alert_lines.append(line)

    _box_line("  " + bold(f"MARGIN ALERTS ({mar_count})"))
    if not alert_lines:
        msg = green(_CHECK + " All clear")
        _box_line("  " + msg)
    else:
        for line in alert_lines:
            _box_line(line)


def _render_summary_table(
    summary_rows: list[SummaryRow],
    balance_lookup: dict[str, tuple[str, Optional[float], Optional[float]]],
    settings: Settings,
) -> None:
    g = settings.global_

    count = len(summary_rows)
    hdr = bold(f"SUMMARY {_EM} {count} customers")
    _box_line("  " + hdr)
    _box_thin_sep()

    if not summary_rows:
        _box_line("  " + dim("No data"))
        return

    cw, ca, co, an, bl, pr, mg, ba = 22, 8, 7, 7, 9, 8, 8, 9

    header = (
        _col("CUSTOMER", cw, "<")
        + _col("CALLS", ca, ">")
        + _col("CONN", co, ">")
        + _col("ASR", an, ">")
        + _col("BILLED", bl, ">")
        + _col("PROFIT", pr, ">")
        + _col("MARGIN", mg, ">")
        + _col("BALANCE", ba, ">")
    )
    _box_line(dim(header))
    _box_thin_sep()

    total_calls = 0.0
    total_conn = 0.0
    total_billed = 0.0
    total_profit = 0.0
    margin_billed_sum = 0.0
    margin_weighted_sum = 0.0
    watch_ids = set(settings.customer_ids)

    max_rows = 15
    shown = 0

    for row in summary_rows:
        cid = row.customer_id

        calls = row.calls
        conn = row.connected
        asr = row.asr
        billed = row.billed_min
        profit = row.profit
        margin = row.margin

        if calls is not None:
            total_calls += calls
        if conn is not None:
            total_conn += conn
        if billed is not None:
            total_billed += billed
            if margin is not None:
                margin_weighted_sum += margin * billed
                margin_billed_sum += billed
        if profit is not None:
            total_profit += profit

        if shown >= max_rows:
            continue

        breached = False
        if margin is not None and billed is not None:
            cust = settings.get_watch(cid)
            mb = cust.resolve_margin_below(g.margin_below)
            ba_thresh = cust.resolve_billed_above(g.billed_above)
            breached = margin < mb and billed > ba_thresh

        m_str = _fmt_margin(margin)
        if breached:
            m_str = m_str + yellow(" " + _WARN)

        bal_val: Optional[float] = None
        if cid in balance_lookup:
            bal_val = balance_lookup[cid][1]
        if bal_val is not None:
            bal_col = red(f"${bal_val:+.0f}") if bal_val < 0 else green(f"${bal_val:+.0f}")
        else:
            bal_col = dim(_EM)

        display_name = _truncate(f"{cid} {row.customer_name}", cw)
        if cid in watch_ids:
            display_name = bold(display_name)

        r = (
            _col(display_name, cw, "<")
            + _col(_fmt_calls(calls), ca, ">")
            + _col(_fmt_calls(conn), co, ">")
            + _col(_fmt_asr(asr), an, ">")
            + _col(_fmt_billed(billed), bl, ">")
            + _col(_fmt_profit(profit), pr, ">")
            + _col(m_str, mg, ">")
            + _col(bal_col, ba, ">")
        )
        _box_line(r)
        shown += 1

    if len(summary_rows) > max_rows:
        _box_line("  " + dim(f"... and {len(summary_rows) - max_rows} more customers"))

    _box_thin_sep()

    avg_asr = (total_conn / total_calls * 100) if total_calls > 0 else None
    avg_margin = (margin_weighted_sum / margin_billed_sum) if margin_billed_sum > 0 else None

    tl = (
        _col(bold("TOTAL"), cw, "<")
        + _col(bold(_fmt_calls(total_calls)), ca, ">")
        + _col(bold(_fmt_calls(total_conn)), co, ">")
        + _col(bold(_fmt_asr(avg_asr)), an, ">")
        + _col(bold(_fmt_billed(total_billed)), bl, ">")
        + _col(bold(_fmt_profit(total_profit)), pr, ">")
        + _col(bold(_fmt_margin(avg_margin)), mg, ">")
        + _col(bold(dim(_EM)), ba, ">")
    )
    _box_line(tl)


def _render_footer(running: bool) -> None:
    toggle = "Stop" if running else "Start"
    shortcuts = (
        f"{bold('[S]')} {toggle}  "
        f"{bold('[R]')} Refresh  "
        f"{bold('[C]')} Config  "
        f"{bold('[H]')} History  "
        f"{bold('[E]')} Export  "
        f"{bold('[T]')} Test  "
        f"{bold('[Q]')} Quit"
    )
    _box_line(shortcuts)


def _render_dashboard(
    summary_rows: list[SummaryRow],
    balance_results: list[tuple[str, Optional[str], Optional[float], Optional[float], Optional[str]]],
    balance_lookup: dict[str, tuple[str, Optional[float], Optional[float]]],
    settings: Settings,
    last_check: str,
    errors: Optional[str],
    next_refresh: float,
) -> None:
    _clear_screen()
    running = _monitor_running()
    watch_ids = set(settings.customer_ids)
    g = settings.global_

    bal_alerts = 0
    for cid, _name, balance, _credit, _error in balance_results:
        if cid not in watch_ids or balance is None:
            continue
        cust = settings.get_watch(cid)
        if balance < cust.resolve_balance_below():
            bal_alerts += 1

    mar_alerts = 0
    for row in summary_rows:
        if row.margin is None or row.billed_min is None:
            continue
        cust = settings.get_watch(row.customer_id)
        if (row.margin < cust.resolve_margin_below(g.margin_below)
                and row.billed_min > cust.resolve_billed_above(g.billed_above)):
            mar_alerts += 1

    _box_top()
    _render_header(settings, running, next_refresh, last_check, errors, bal_alerts + mar_alerts)
    _box_sep()
    _render_alert_rows(balance_results, summary_rows, settings, mar_alerts)
    _box_sep()
    _render_summary_table(summary_rows, balance_lookup, settings)
    _box_sep()
    _render_footer(running)
    _box_bottom()


# --- monitor toggle ---

def do_toggle_monitor(settings: Settings) -> None:
    global _monitor_thread, _monitor_stop
    try:
        with open(STATUS_FILE, 'r') as f:
            alive = json.load(f).get("alive", False)
    except Exception:
        alive = False

    if alive:
        if _monitor_stop:
            _monitor_stop.set()
        if _monitor_thread and _monitor_thread.is_alive():
            _monitor_thread.join(timeout=5)
    else:
        _monitor_stop = threading.Event()
        _monitor_thread = threading.Thread(
            target=run_monitor, args=(settings, _monitor_stop),
            daemon=True, name="monitor-bg"
        )
        _monitor_thread.start()


# --- overlays ---

def _config_overlay(settings: Settings) -> Settings:
    g = settings.global_

    while True:
        _clear_screen()
        inner = 64
        print(dim("  " + _BTL + _BH * inner + _BTR))
        _box_line_overlay(bold("CONFIGURATION"), inner)
        print(dim("  " + _BML + _BH * inner + _BMR))

        entries = [
            ("Interval (s)", str(g.check_interval)),
            ("Margin below (%)", f"{g.margin_below:.1f}"),
            ("Margin critical (%)", f"{g.margin_critical:.1f}"),
            ("Billed above (min)", f"{g.billed_above:.1f}"),
            ("Cooldown (s)", str(g.cooldown)),
            ("Audio", "ON" if g.audio else "OFF"),
            ("Webhook", g.webhook_type if g.webhook_type != "none" else "none"),
            ("Summary direction", g.summary_direction),
            ("Summary interval", g.summary_interval),
            ("Active hours start", g.active_hours_start or "24/7"),
            ("Active hours end", g.active_hours_end or "24/7"),
            ("Active days", g.active_days or "all"),
            ("DB retention (d)", str(g.db_retention_days)),
            ("Health port", str(g.health_port)),
            ("Watch targets", f"{len(settings.watch)} customers"),
        ]

        for i, (label, val) in enumerate(entries, 1):
            line = f"{_col(str(i) + '.', 4, '>')} {_col(label, 26, '<')} {val}"
            _box_line_overlay(line, inner)

        print(dim("  " + _BML + _BH * inner + _BMR))
        _box_line_overlay(bold("W") + " Watch targets  " + bold("Q") + " Back", inner)
        print(dim("  " + _BBL + _BH * inner + _BBR))
        print()

        choice = input(f"  {dim('# or W/Q >')} ").strip().upper()
        if choice == "Q":
            break
        elif choice == "W":
            settings = _watch_overlay(settings)
            continue

        try:
            idx = int(choice) - 1
        except ValueError:
            continue

        _edit_global_setting(settings, idx)
        save_settings(settings)

    return settings


def _box_line_overlay(text: str, inner: int) -> None:
    print(dim("  " + _BV + " ") + _pad_inner(text, inner) + dim(" " + _BV))


def _edit_global_setting(settings: Settings, idx: int) -> None:
    g = settings.global_
    field_map = [
        ("check_interval", "Interval (s)", int),
        ("margin_below", "Margin below (%)", float),
        ("margin_critical", "Margin critical (%)", float),
        ("billed_above", "Billed above (min)", float),
        ("cooldown", "Cooldown (s)", int),
        ("audio", "Audio (on/off)", lambda v: v.lower() in ("on", "true", "1", "yes")),
        ("webhook_type", "Webhook (none/telegram/slack)", str),
        ("summary_direction", "Summary direction (outbound/inbound)", str),
        ("summary_interval", "Summary interval (5m/10m/1h)", str),
        ("active_hours_start", "Active hours start (HH:MM)", str),
        ("active_hours_end", "Active hours end (HH:MM)", str),
        ("active_days", "Active days (mon,tue,...)", str),
        ("db_retention_days", "DB retention days", int),
        ("health_port", "Health port (0=off)", int),
    ]

    if idx < 0 or idx >= len(field_map):
        return

    key, label, convert = field_map[idx]
    current = getattr(g, key)
    new = input(f"  {label} [{current}]: ").strip()
    if new:
        try:
            setattr(g, key, convert(new))
            print(f"  {green('Saved.')}")
        except (ValueError, TypeError) as e:
            print(f"  {red(f'Error: {e}')}")
    input(f"  {dim('Press Enter...')}")


def _watch_overlay(settings: Settings) -> Settings:
    while True:
        _clear_screen()
        inner = 64
        print(dim("  " + _BTL + _BH * inner + _BTR))
        _box_line_overlay(bold("WATCH TARGETS"), inner)
        print(dim("  " + _BML + _BH * inner + _BMR))

        for i, w in enumerate(settings.watch, 1):
            bal = w.balance_below
            bal_s = f"{bal:+.1f}" if bal is not None else "N/A"
            extras = []
            if w.balance_critical is not None and w.balance_critical != w.balance_below:
                extras.append(f"crit={w.balance_critical:+}")
            if w.margin_below is not None:
                extras.append(f"m<={w.margin_below}%")
            if w.billed_above is not None:
                extras.append(f"b>={w.billed_above}")
            extra_s = "  " + dim(" ".join(extras)) if extras else ""
            line = (
                f"{_col(str(i), 3, '>')}  {w.customer:5}  "
                f"{_truncate(w.name, 16):16}  bal < {bal_s}{extra_s}"
            )
            _box_line_overlay(line, inner)

        print(dim("  " + _BML + _BH * inner + _BMR))
        footer = (
            bold("A") + "dd  "
            + bold("E") + "dit #  "
            + bold("R") + "emove #  "
            + bold("Q") + " Back"
        )
        _box_line_overlay(footer, inner)
        print(dim("  " + _BBL + _BH * inner + _BBR))
        print()

        choice = input(f"  {dim('>')} ").strip().upper()
        if choice == "Q":
            break
        elif choice == "A":
            cid = input("  Customer ID: ").strip()
            if cid and not any(w.customer == cid for w in settings.watch):
                name = input("  Name (optional): ").strip()
                bal_s = input("  Balance below [-365]: ").strip()
                try:
                    bal = float(bal_s) if bal_s else -365.0
                except ValueError:
                    bal = -365.0
                settings.watch.append(WatchTarget(customer=cid, name=name, balance_below=bal))
                save_settings(settings)
                print(f"  {green('Added.')}")
            else:
                print(f"  {dim('Invalid or duplicate.')}")
            input(f"  {dim('Press Enter...')}")
        elif choice == "E":
            try:
                idx = int(input("  Edit #: ").strip()) - 1
                if 0 <= idx < len(settings.watch):
                    _edit_watch_target(settings.watch[idx], settings)
                else:
                    print(f"  {dim('Invalid #')}")
            except ValueError:
                print(f"  {dim('Invalid #')}")
            input(f"  {dim('Press Enter...')}")
        elif choice == "R":
            try:
                idx = int(input("  Remove #: ").strip()) - 1
                if 0 <= idx < len(settings.watch):
                    removed = settings.watch.pop(idx)
                    save_settings(settings)
                    print(f"  {red('Removed')} {removed.customer}")
                else:
                    print(f"  {dim('Invalid #')}")
            except ValueError:
                print(f"  {dim('Invalid #')}")
            input(f"  {dim('Press Enter...')}")

    return settings


def _edit_watch_target(w: WatchTarget, settings: Settings) -> None:
    while True:
        print()
        print(f"  {bold('Edit')} {w.customer}  {dim(f'({w.name})') if w.name else ''}")
        print(f"  1. Balance below: {w.balance_below:+.1f}")
        crit_display = w.balance_critical if w.balance_critical is not None else "none"
        print(f"  2. Balance critical: {crit_display}")
        mb_display = w.margin_below if w.margin_below is not None else "default"
        print(f"  3. Margin below (%): {mb_display}")
        ba_display = w.billed_above if w.billed_above is not None else "default"
        print(f"  4. Billed above: {ba_display}")
        print(f"  5. Name: {w.name or 'none'}")
        print(f"  {dim('Q')}. Back")
        print()

        choice = input(f"  {dim('>')} ").strip().upper()
        if choice == "Q":
            break
        elif choice == "1":
            v = input(f"  Balance below [{w.balance_below:+.1f}]: ").strip()
            if v:
                try:
                    w.balance_below = float(v)
                    save_settings(settings)
                except ValueError:
                    print(f"  {red('Invalid')}")
        elif choice == "2":
            crit_val = w.balance_critical if w.balance_critical is not None else "none"
            v = input(f"  Balance critical [{crit_val}]: ").strip()
            w.balance_critical = float(v) if v else None
            save_settings(settings)
        elif choice == "3":
            mb_val = w.margin_below if w.margin_below is not None else "default"
            v = input(f"  Margin below % [{mb_val}]: ").strip()
            w.margin_below = float(v) if v else None
            save_settings(settings)
        elif choice == "4":
            ba_val = w.billed_above if w.billed_above is not None else "default"
            v = input(f"  Billed above [{ba_val}]: ").strip()
            w.billed_above = float(v) if v else None
            save_settings(settings)
        elif choice == "5":
            v = input(f"  Name [{w.name or ''}]: ").strip()
            w.name = v if v else ""
            save_settings(settings)


def _history_overlay() -> None:
    init_db()
    while True:
        _clear_screen()
        inner = 50
        print(dim("  " + _BTL + _BH * inner + _BTR))
        _box_line_overlay(bold("HISTORY"), inner)
        print(dim("  " + _BML + _BH * inner + _BMR))
        _box_line_overlay(
            bold("B") + " Balances  " + bold("M") + " Margins  " + bold("A") + " Alerts",
            inner,
        )
        print(dim("  " + _BML + _BH * inner + _BMR))
        _box_line_overlay(bold("Q") + " Back", inner)
        print(dim("  " + _BBL + _BH * inner + _BBR))
        print()

        c = input(f"  {dim('>')} ").strip().upper()
        if c == "Q":
            break
        elif c == "B":
            _show_history("balance")
        elif c == "M":
            _show_history("margin")
        elif c == "A":
            _show_history("alert")


def _show_history(kind: str) -> None:
    cid = input("  Customer ID (Enter=all): ").strip() or None
    hs = input("  Hours [24]: ").strip()
    try:
        hours = int(hs) if hs else 24
    except ValueError:
        hours = 24

    _clear_screen()
    sep = _BH * 72
    if kind == "balance":
        rows = get_balance_history(customer_id=cid, hours=hours, limit=30)
        print(f"  {bold('Balance History')}  ({len(rows)} rows)")
        print(dim("  " + sep))
        for r in rows:
            ts = (r['recorded_at'] or '')[:19]
            name = (r['customer_name'] or '')[:20]
            bal = r['balance'] if r['balance'] is not None else 'N/A'
            print(f"  {dim(ts)}  {name:20}  {r['customer_id']:>4}  {bal}")
    elif kind == "margin":
        rows = get_margin_history(customer_id=cid, hours=hours, limit=30)
        print(f"  {bold('Margin History')}  ({len(rows)} rows)")
        print(dim("  " + sep))
        for r in rows:
            ts = (r['recorded_at'] or '')[:19]
            name = (r['customer_name'] or '')[:20]
            m = f"{r['margin']:.1f}%" if r['margin'] is not None else 'N/A'
            b = f"{r['billed_min']:.1f}" if r['billed_min'] is not None else 'N/A'
            print(f"  {dim(ts)}  {name:20}  {r['customer_id']:>4}  margin {m}  billed {b}")
    elif kind == "alert":
        rows = get_alert_history(customer_id=cid, hours=hours, limit=30)
        print(f"  {bold('Alert History')}  ({len(rows)} rows)")
        print(dim("  " + sep))
        for r in rows:
            ts = (r['recorded_at'] or '')[:19]
            val = r['value'] if r['value'] is not None else 'N/A'
            sev = r['severity']
            sev_str = red(sev) if sev in ('dropped', 'escalated') else green(sev)
            print(f"  {dim(ts)}  {r['customer_id']:>4}  {r['alert_type']:7}  {sev_str:10}  value={val}")
    print()
    input(f"  {dim('Press Enter...')}")


def _export_overlay() -> None:
    from export import export_balance_csv, export_margin_csv

    _clear_screen()
    inner = 40
    print(dim("  " + _BTL + _BH * inner + _BTR))
    _box_line_overlay(bold("EXPORT"), inner)
    print(dim("  " + _BML + _BH * inner + _BMR))
    _box_line_overlay(bold("B") + " Balance CSV  " + bold("M") + " Margin CSV", inner)
    print(dim("  " + _BML + _BH * inner + _BMR))
    _box_line_overlay(bold("Q") + " Back", inner)
    print(dim("  " + _BBL + _BH * inner + _BBR))
    print()

    choice = input(f"  {dim('>')} ").strip().upper()
    if choice == "B":
        cid = input("  Customer ID (Enter=all): ").strip() or None
        filename = export_balance_csv(customer_id=cid)
        print(f"  {green('Exported:')} {filename}")
    elif choice == "M":
        cid = input("  Customer ID (Enter=all): ").strip() or None
        filename = export_margin_csv(customer_id=cid)
        print(f"  {green('Exported:')} {filename}")
    input(f"  {dim('Press Enter...')}")


def _test_alert(settings: Settings) -> None:
    from alerts import trigger_test_alert
    _clear_screen()
    print()
    trigger_test_alert(settings)
    print()
    print(f"  {dim('Test alert triggered.')}")
    input(f"  {dim('Press Enter...')}")


# --- main loop ---

def dashboard_loop(settings: Settings) -> None:
    g = settings.global_
    interval = g.check_interval

    session = create_session()
    if not perform_login(session, g.request_timeout):
        print("Login failed. Exiting.")
        session.close()
        return

    init_db()

    _clear_screen()
    print("  " + bold("Loading dashboard data..."))
    print()

    last_check = ""
    errors: Optional[str] = None
    summary_rows: list[SummaryRow] = []
    balance_lookup: dict[str, tuple[str, Optional[float], Optional[float]]] = {}

    next_refresh = time.time()
    last_countdown = -1

    while True:
        if time.time() >= next_refresh:
            try:
                summary_rows, balance_lookup, summary_error = fetch_dashboard_data(session, settings)
                last_check = datetime.now().strftime("%H:%M:%S")
                errors = summary_error
            except Exception as e:
                logging.error(f"Dashboard fetch failed: {e}")
                errors = str(e)

            next_refresh = time.time() + interval
            last_countdown = -1

        balance_results: list[tuple[str, Optional[str], Optional[float], Optional[float], Optional[str]]] = [
            (cid, name, bal, cred, None) for cid, (name, bal, cred) in balance_lookup.items()
        ]
        for row in summary_rows:
            if row.customer_id not in balance_lookup:
                balance_results.append((row.customer_id, "", None, None, None))
        balance_results.sort(key=lambda x: x[0])

        current_countdown = max(0, int(next_refresh - time.time()))
        remaining = max(0.1, next_refresh - time.time())
        key = _keyboard_poll(min(0.2, remaining))

        if current_countdown != last_countdown or key is not None:
            _render_dashboard(summary_rows, balance_results, balance_lookup, settings, last_check, errors, next_refresh)
            last_countdown = current_countdown

        if key is None:
            continue

        if key == "Q":
            break
        elif key == "S":
            do_toggle_monitor(settings)
            _render_dashboard(summary_rows, balance_results, balance_lookup, settings, last_check, errors, next_refresh)
        elif key == "R":
            next_refresh = 0
            last_countdown = -1
        elif key == "C":
            settings = _config_overlay(settings)
            _clear_screen()
            last_countdown = -1
        elif key == "H":
            _history_overlay()
            _clear_screen()
            last_countdown = -1
        elif key == "E":
            _export_overlay()
            _clear_screen()
            last_countdown = -1
        elif key == "T":
            _test_alert(settings)
            _clear_screen()
            last_countdown = -1

    session.close()

    if _monitor_stop:
        _monitor_stop.set()
    if _monitor_thread and _monitor_thread.is_alive():
        _monitor_thread.join(timeout=5)
