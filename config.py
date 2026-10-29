from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from credentials import get_credentials
from settings_models import (
    DEFAULT_BALANCE_BELOW,
    FIELD_SPEC,
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
# A PyInstaller one-file build unpacks to a temp dir, so runtime files (settings,
# DB, log, status) must live next to the executable to persist between runs.
RUNTIME_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else _BASE_DIR

LOG_FILE = str(RUNTIME_DIR / "balance_monitor.log")
SETTINGS_FILE = str(RUNTIME_DIR / "settings.toml")
DB_FILE = str(RUNTIME_DIR / "instacallmonitor.db")


def _toml_str(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _toml_literal(value: object) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, str):
        return _toml_str(value)
    return str(value)


def _generate_template() -> str:
    """Render the default settings.toml from the single FIELD_SPEC definition."""
    defaults = GlobalSettings()
    lines = [
        "# InstacallMonitor Settings",
        "# Edit this file to configure monitoring thresholds and watch targets.",
        "# Changes take effect on the next check cycle (hot-reload).",
        "",
        "[settings]",
    ]
    group = ""
    for spec in FIELD_SPEC:
        if spec.group != group:
            lines.append("")
            group = spec.group
        if spec.comment:
            lines.append(f"# {spec.comment}")
        lines.append(f"{spec.name} = {_toml_literal(getattr(defaults, spec.name))}")
    lines += [
        "",
        "# Add one [[watch]] block for each customer to monitor.",
        '# The "customer" field is the numeric customer ID from the Instacall portal.',
        "# All other fields are optional overrides of the global defaults above.",
        "",
        "[[watch]]",
        'customer = "18"',
        'name = ""',
        "balance_below = -500.0",
    ]
    return "\n".join(lines) + "\n"


_SETTINGS_TEMPLATE = _generate_template()
_settings_mtime: float = 0.0
_settings_version: int = 0
_settings_lock = threading.Lock()


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
    group = ""
    for spec in FIELD_SPEC:
        if spec.group != group:
            lines.append("")
            group = spec.group
        if spec.comment:
            lines.append(f"# {spec.comment}")
        lines.append(f"{spec.name} = {_toml_literal(getattr(settings.global_, spec.name))}")

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
        target_dir = os.path.dirname(os.path.abspath(SETTINGS_FILE)) or "."
        with tempfile.NamedTemporaryFile(
            mode="w", dir=target_dir, delete=False, suffix=".tmp", encoding="utf-8"
        ) as tmp:
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
