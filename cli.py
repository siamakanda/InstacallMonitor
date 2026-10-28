from __future__ import annotations

import sys
from typing import Any, Callable

from display import red
from settings_models import Settings

Args = dict[str, Any]

_VALUE_FLAGS: dict[str, tuple[str, Callable[[str], Any]]] = {
    "--interval": ("interval", int),
    "--balance-below": ("balance_below", float),
    "--margin-below": ("margin_below", float),
    "--billed-above": ("billed_above", float),
    "--margin-above": ("margin_above", float),
    "--margin-deadband": ("margin_deadband", float),
    "--cooldown": ("cooldown", int),
    "--export-customer": ("export_customer", str),
    "--web-port": ("web_port", int),
    "--tail": ("tail", int),
}

_FLAGS: dict[str, str] = {
    "--quiet": "quiet",
    "--run-once": "run_once",
    "--export": "export",
    "--no-hot-reload": "no_hot_reload",
    "--no-web": "no_web",
    "--help": "help",
    "--status": "status",
}


def parse_args(argv: list[str] | None = None) -> Args:
    argv = sys.argv[1:] if argv is None else argv
    args: Args = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in _VALUE_FLAGS:
            if i + 1 >= len(argv):
                print(f"  {red('[WARN]')} Missing value for {token}")
                i += 1
                continue
            key, caster = _VALUE_FLAGS[token]
            raw = argv[i + 1]
            try:
                args[key] = caster(raw)
            except ValueError:
                if token == "--tail":
                    args[key] = 20
                else:
                    print(f"  {red('[WARN]')} Invalid value for {token}: {raw}")
            i += 2
        elif token in _FLAGS:
            args[_FLAGS[token]] = True
            i += 1
        else:
            print(f"  {red('[WARN]')} Unknown option ignored: {token}")
            i += 1
    return args


def print_help() -> None:
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
    print("  --no-web             Disable the web dashboard")
    print("  --web-port N         Override the dashboard port")
    print("  --status             Show monitor health status and exit")
    print("  --tail N             Show last N log entries and exit (default: 20)")
    print("  --help               Show this help")
    print()
    print("Settings in settings.toml  |  Credentials in .env")


def apply_cli_overrides(settings: Settings, args: Args) -> Settings:
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
        value = float(args["balance_below"])
        for w in settings.watch:
            w.balance_below = value
    if "web_port" in args:
        g.web_dashboard_port = int(args["web_port"])
    if args.get("no_web"):
        g.web_dashboard = False
    return settings
