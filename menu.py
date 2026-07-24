from __future__ import annotations

import sys

from config import (
    Settings,
    get_credentials,
    load_settings,
    setup_logging,
    validate_settings,
)
from dashboard import dashboard_loop
from persistence import init_db


def _parse_cli_overrides(settings: Settings) -> Settings:
    args = sys.argv[1:]
    i = 0
    g = settings.global_
    while i < len(args):
        arg = args[i]
        if arg == "--balance-below" and i + 1 < len(args):
            try:
                val = float(args[i + 1])
                for w in settings.watch:
                    w.balance_below = val
                i += 2
            except ValueError:
                i += 2
        elif arg == "--margin-below" and i + 1 < len(args):
            try:
                g.margin_below = float(args[i + 1])
                i += 2
            except ValueError:
                i += 2
        elif arg == "--interval" and i + 1 < len(args):
            try:
                g.check_interval = int(args[i + 1])
                i += 2
            except ValueError:
                i += 2
        elif arg == "--quiet":
            g.quiet = True
            i += 1
        elif arg == "--billed-above" and i + 1 < len(args):
            try:
                g.billed_above = float(args[i + 1])
                i += 2
            except ValueError:
                i += 2
        elif arg == "--cooldown" and i + 1 < len(args):
            try:
                g.cooldown = int(args[i + 1])
                i += 2
            except ValueError:
                i += 2
        elif arg == "--run-once":
            g.quiet = True
            g._run_once = True
            i += 1
        else:
            i += 1
    return settings


def main() -> None:
    setup_logging()
    settings = load_settings()
    settings = _parse_cli_overrides(settings)

    errors = validate_settings(settings)
    if errors:
        print("Invalid settings:")
        for e in errors:
            print(f"  - {e}")
        input("Press Enter to exit...")
        sys.exit(1)

    try:
        get_credentials()
    except ValueError as e:
        print(f"Credential error: {e}")
        input("Press Enter to exit...")
        sys.exit(1)

    init_db()

    if getattr(settings.global_, '_run_once', False):
        from quick import run_quick_check_parallel
        run_quick_check_parallel(settings)
        sys.exit(0)

    dashboard_loop(settings)

    sys.exit(0)


if __name__ == "__main__":
    main()
