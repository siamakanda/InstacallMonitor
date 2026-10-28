from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
from dataclasses import fields
from logging.handlers import RotatingFileHandler
from pathlib import Path

import tomli as tomllib

from credentials import get_credentials
from settings_models import (
    DEFAULT_BALANCE_BELOW,
    GlobalSettings,
    Settings,
    WatchTarget,
    fmt_val,
)
from settings_validation import validate_settings

__all__ = [
    "BASE_EDIT_URL",
    "DB_FILE",
    "DEFAULT_BALANCE_BELOW",
    "GlobalSettings",
    "HEADERS",
    "LOGIN_URL",
    "LOG_FILE",
    "SETTINGS_FILE",
    "SUMMARY_REPORT_URL",
    "Settings",
    "SettingsError",
    "WatchTarget",
    "get_credentials",
    "get_settings_mtime",
    "get_settings_version",
    "load_settings",
    "read_settings",
    "reload_settings",
    "save_settings",
    "setup_logging",
    "validate_settings",
]


class SettingsError(Exception):
    """Raised when settings.toml exists but cannot be parsed."""


LOGIN_URL = "https://switchportal.instacall.digital/login"
BASE_EDIT_URL = "https://switchportal.instacall.digital/customers?edit="
SUMMARY_REPORT_URL = "https://switchportal.instacall.digital/summary_report"

HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
}

_BASE_DIR = Path(__file__).resolve().parent

LOG_FILE = str(_BASE_DIR / "balance_monitor.log")
SETTINGS_FILE = str(_BASE_DIR / "settings.toml")
DB_FILE = str(_BASE_DIR / "instacallmonitor.db")

_SETTINGS_TEMPLATE = """\
# InstacallMonitor Settings
# Edit this file to configure monitoring thresholds and watch targets.
# Changes take effect on the next check cycle (hot-reload).

[settings]
# Interval between check cycles, in seconds
check_interval = 600
# HTTP request timeout, in seconds
request_timeout = 10
# Enable audible siren alerts (Windows only)
audio = true
# Minimum seconds between repeated alerts for the same customer
cooldown = 300
# Days of history to keep in the database
db_retention_days = 30
# Summary direction: "outbound" or "inbound"
summary_direction = "outbound"
# Summary interval: "5m", "15m", "1h", "24h" etc.
summary_interval = "5m"
# Margin below this percentage triggers low-margin alert
margin_below = 30.0
# Margin above this percentage triggers high-margin alert
margin_above = 75.0
# Recovery deadband prevents alert flapping (must be < half the margin range)
margin_deadband = 3.0
# Only alert when billed minutes exceed this value
billed_above = 70.0
# Number of concurrent worker threads for scraping
max_workers = 4
# Siren sound parameters
siren_loops = 10
siren_min_freq = 2200
siren_max_freq = 3500
siren_step_freq = 130
siren_tone_duration = 50
# Retry behaviour for transient failures
retry_max_attempts = 3
retry_base_delay = 1.0
retry_max_delay = 30.0
retry_jitter_max = 1.0
# Circuit breaker — opens after N consecutive failures, recovers after timeout
circuit_failure_threshold = 5
circuit_recovery_timeout = 60.0
# Web dashboard — local HTTP UI for live balances, margins and history
web_dashboard = true
web_dashboard_host = "127.0.0.1"
web_dashboard_port = 8765
web_dashboard_refresh = 5

# Add one [[watch]] block for each customer to monitor.
# The "customer" field is the numeric customer ID from the Instacall portal.
# All other fields are optional overrides of the global defaults above.

[[watch]]
customer = "18"
name = ""
balance_below = -500.0
"""
_settings_mtime: float = 0.0
_settings_version: int = 0
_settings_lock = threading.Lock()


def _extract_global_comments() -> dict[str, str]:
    comments: dict[str, str] = {}
    pending: list[str] = []
    in_settings = False
    for raw in _SETTINGS_TEMPLATE.splitlines():
        stripped = raw.strip()
        if stripped == "[settings]":
            in_settings = True
            pending = []
            continue
        if not in_settings:
            continue
        if stripped.startswith("["):
            break
        if stripped.startswith("#"):
            pending.append(raw.rstrip())
            continue
        if "=" in stripped:
            name = stripped.split("=", 1)[0].strip()
            if pending:
                comments[name] = "\n".join(pending)
                pending = []
            continue
        pending = []
    return comments


_GLOBAL_COMMENTS: dict[str, str] = _extract_global_comments()


def get_settings_mtime() -> float:
    try:
        return os.path.getmtime(SETTINGS_FILE)
    except OSError:
        return 0.0


def get_settings_version() -> int:
    with _settings_lock:
        return _settings_version


def _parse_settings_file() -> Settings:
    with open(SETTINGS_FILE, "rb") as f:
        data = tomllib.load(f)
    settings = Settings.from_dict(data)
    for w in settings.watch:
        if w.balance_below is None:
            w.balance_below = DEFAULT_BALANCE_BELOW
    return settings


def read_settings() -> Settings:
    """Parse settings.toml without touching hot-reload bookkeeping."""
    return _parse_settings_file()


def load_settings() -> Settings:
    global _settings_mtime, _settings_version
    try:
        mtime = get_settings_mtime()
        settings = _parse_settings_file()
        with _settings_lock:
            _settings_mtime = mtime
            _settings_version = 1
        return settings
    except FileNotFoundError:
        _write_template()
        return Settings(watch=[WatchTarget(customer="18", balance_below=DEFAULT_BALANCE_BELOW)])
    except Exception as e:
        # Never fall back to a default watch on a parse error: that would silently
        # monitor the wrong customer. Fail loudly instead.
        raise SettingsError(f"Corrupted settings file {SETTINGS_FILE}: {e}") from e


def _write_template() -> None:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            f.write(_SETTINGS_TEMPLATE)
        logging.info(f"Created settings template at {SETTINGS_FILE}")
    except OSError:
        pass


def reload_settings(current: Settings) -> Settings | None:
    """Return freshly parsed settings if settings.toml changed, else None.

    Change detection/logging is left to the caller (app._try_hot_reload) so the
    diff is computed once.
    """
    global _settings_mtime, _settings_version
    new_mtime = get_settings_mtime()
    with _settings_lock:
        if new_mtime <= _settings_mtime:
            return None

    try:
        new_settings = _parse_settings_file()
    except FileNotFoundError:
        logging.warning("Settings file deleted; keeping current settings.")
        return None
    except Exception as e:
        logging.warning(f"Settings reload failed (keeping current): {e}")
        return None

    with _settings_lock:
        _settings_mtime = new_mtime
        _settings_version += 1
    return new_settings


def _toml_str(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    )
    return f'"{escaped}"'


def save_settings(settings: Settings) -> None:
    if os.path.exists(SETTINGS_FILE):
        try:
            shutil.copy2(SETTINGS_FILE, SETTINGS_FILE + ".bak")
        except OSError:
            pass

    lines: list[str] = [
        "# InstacallMonitor - Settings",
        "# Edit this file to configure monitoring thresholds and watch targets.",
        "# Changes take effect on the next check cycle (hot-reload).",
        "",
        "[settings]",
    ]
    for f in fields(GlobalSettings):
        comment = _GLOBAL_COMMENTS.get(f.name)
        if comment:
            lines.append(comment)
        val = getattr(settings.global_, f.name)
        if isinstance(val, str):
            lines.append(f"{f.name} = {_toml_str(val)}")
        elif isinstance(val, bool):
            lines.append(f"{f.name} = {str(val).lower()}")
        else:
            lines.append(f"{f.name} = {val}")

    lines.append("")
    lines.append("# Add one [[watch]] block for each customer to monitor.")
    lines.append('# The "customer" field is the numeric customer ID from the Instacall portal.')
    lines.append("# All other fields are optional overrides of the global defaults above.")
    lines.append("")
    for w in settings.watch:
        lines.append("[[watch]]")
        lines.append(f"customer = {_toml_str(w.customer)}")
        if w.name:
            lines.append(f"name = {_toml_str(w.name)}")
        lines.append(f"balance_below = {fmt_val(w.balance_below)}")
        for key in ("margin_below", "margin_above", "margin_deadband", "billed_above"):
            val = getattr(w, key)
            if val is not None:
                lines.append(f"{key} = {fmt_val(val)}")
        lines.append("")

    content = "\n".join(lines) + "\n"
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=_BASE_DIR, delete=False, suffix=".tmp", encoding="utf-8") as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        os.replace(tmp_path, SETTINGS_FILE)
    except BaseException:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def setup_logging() -> None:
    handler: logging.Handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=5)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S"))
    root = logging.getLogger("")
    root.handlers.clear()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    root.addHandler(console)
