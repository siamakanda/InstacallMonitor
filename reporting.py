from __future__ import annotations

from datetime import datetime
from typing import Any

from alerts import get_session_alert_count
from display import bold, cyan, dim, green, red, yellow
from persistence import export_balance_csv, export_margin_csv
from runtime import Runtime
from settings_models import Settings


def print_banner(settings: Settings, hot_reload: bool) -> None:
    g = settings.global_
    cids = settings.customer_ids
    audio_str = green("ON") if g.audio else dim("OFF")
    hot_reload_str = green("ON") if hot_reload else dim("OFF")
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


def print_dashboard_url(url: str) -> None:
    print(f"  {dim('Dashboard')}  {cyan(url)}")
    print()


def print_session_summary(runtime: Runtime) -> None:
    uptime = runtime.uptime()
    hours = int(uptime // 3600)
    minutes = int((uptime % 3600) // 60)
    seconds = int(uptime % 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s" if hours else f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
    alerts = get_session_alert_count()
    errors = runtime.error_count
    error_str = red(f"{errors}") if errors else green("0")
    alert_str = red(f"{alerts}") if alerts else green("0")
    print()
    print(f"  {bold('Session Summary')}")
    print(f"  {dim('─' * 40)}")
    print(f"  Uptime:       {uptime_str}")
    print(f"  Cycles:       {runtime.cycle_count}")
    print(f"  Alerts:       {alert_str}")
    print(f"  Errors:       {error_str}")
    if runtime.last_error:
        print(f"  Last Error:   {dim(runtime.last_error)}")
    print()


def run_export(args: dict[str, Any]) -> None:
    cid = str(args.get("export_customer", "")) or None
    b_file = export_balance_csv(customer_id=cid)
    m_file = export_margin_csv(customer_id=cid)
    print(f"  {green('Exported:')} {b_file}")
    print(f"  {green('Exported:')} {m_file}")
