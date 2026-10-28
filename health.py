from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any, Optional

from config import LOG_FILE, get_settings_version
from display import bold, dim, green, red
from runtime import Runtime
from settings_models import Settings

STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor.status")


def write_status(runtime: Runtime, settings: Settings, path: str = STATUS_FILE) -> None:
    status = runtime.health(
        settings_version=get_settings_version(),
        customers_monitored=len(settings.customer_ids),
    )
    tmp_path = ""
    try:
        directory = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".monitor.status.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(status, f, indent=2)
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as e:
        logging.warning("Failed to write status file %s: %s", path, e)


def read_status(path: str = STATUS_FILE) -> Optional[dict[str, Any]]:
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def render_status(path: str = STATUS_FILE) -> None:
    status = read_status(path)
    if status is None:
        print(f"  {dim('No monitor.status file found — monitor may not be running.')}")
        return
    alive = green("YES") if status.get("alive") else red("NO")
    uptime = status.get("uptime_seconds", 0)
    if isinstance(uptime, bool) or not isinstance(uptime, (int, float)):
        uptime = 0
    print()
    print(f"  {bold('Monitor Status')}")
    print(f"  {dim('─' * 40)}")
    print(f"  Alive:           {alive}")
    print(f"  Uptime:          {float(uptime):.0f}s")
    print(f"  Cycles:          {status.get('cycle_count', 0)}")
    print(f"  Errors:          {status.get('error_count', 0)}")
    print(f"  Last Check:      {status.get('last_check', 'N/A')}")
    print(f"  Last Error:      {status.get('last_error', 'None')}")
    print(f"  Customers:       {status.get('customers_monitored', 0)}")
    print(f"  Settings v:      {status.get('settings_version', 0)}")
    print()


def render_tail(lines: int, path: str = LOG_FILE) -> None:
    lines = max(lines, 1)
    max_bytes = lines * 500
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            if file_size > max_bytes:
                f.seek(-max_bytes, os.SEEK_END)
            content = f.read().decode(errors="replace")
    except OSError:
        print(f"  {dim('No log file found — monitor may not have been started.')}")
        return
    tail_lines = content.splitlines()[-lines:]
    print(f"  {bold('Last')} {len(tail_lines)} {bold('log entries:')}")
    print(f"  {dim('─' * 60)}")
    for line in tail_lines:
        print(f"  {dim(line)}")
    print()
