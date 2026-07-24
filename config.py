from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field, fields
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

import tomli as tomllib
from dotenv import load_dotenv

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
STATUS_FILE = str(_BASE_DIR / "monitor.status")
SETTINGS_FILE = str(_BASE_DIR / "settings.toml")
SETTINGS_FILE_JSON = str(_BASE_DIR / "settings.json")
DB_FILE = str(_BASE_DIR / "instacallmonitor.db")
PROFILES_FILE = str(_BASE_DIR / "profiles.json")

DEFAULT_BALANCE_BELOW = -365.0


def _coerce_type(value: Any, target_type: Any) -> Any:
    if value is None:
        return None
    origin = getattr(target_type, '__origin__', None)
    if origin is not None:
        args = getattr(target_type, '__args__', ())
        for arg in args:
            if arg is not type(None):
                target_type = arg
                break
    if target_type is bool and not isinstance(value, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "on")
        return bool(value)
    if target_type is int and not isinstance(value, int):
        try:
            return int(value)
        except (ValueError, TypeError):
            return value
    if target_type is float and not isinstance(value, float):
        try:
            return float(value)
        except (ValueError, TypeError):
            return value
    return value


@dataclass
class GlobalSettings:
    check_interval: int = 600
    request_timeout: int = 10
    summary_direction: str = "outbound"
    summary_interval: str = "5m"
    summary_show_all: bool = False
    audio: bool = True
    siren_loops: int = 10
    siren_min_freq: int = 2200
    siren_max_freq: int = 3500
    siren_step_freq: int = 130
    siren_tone_duration: int = 50
    cooldown: int = 300
    webhook_url: str = ""
    webhook_type: str = "none"
    telegram_chat_id: str = ""
    active_hours_start: str = ""
    active_hours_end: str = ""
    active_days: str = ""
    db_retention_days: int = 30
    logging_json: bool = False
    health_port: int = 0
    active_profile: str = "default"
    quiet: bool = False

    margin_below: float = 30.0
    margin_critical: float = 25.0
    billed_above: float = 70.0

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GlobalSettings:
        field_names = {f.name for f in fields(cls)}
        field_types = {f.name: f.type for f in fields(cls)}
        old_to_new = {
            "check_interval_seconds": "check_interval",
            "alert_cooldown_seconds": "cooldown",
            "audio_enabled": "audio",
            "margin_threshold": "margin_below",
            "margin_rearm_threshold": "margin_critical",
            "billed_min_threshold": "billed_above",
        }
        filtered: dict[str, Any] = {}
        for k, v in data.items():
            key = old_to_new.get(k, k)
            if key in field_names:
                ft = field_types.get(key)
                filtered[key] = _coerce_type(v, ft)
        return cls(**filtered)


@dataclass
class WatchTarget:
    customer: str
    name: str = ""
    balance_below: Optional[float] = None
    balance_critical: Optional[float] = None
    margin_below: Optional[float] = None
    margin_critical: Optional[float] = None
    billed_above: Optional[float] = None

    def resolve_balance_below(self) -> float:
        return self.balance_below if self.balance_below is not None else DEFAULT_BALANCE_BELOW

    def resolve_balance_critical(self) -> float:
        if self.balance_critical is not None:
            return self.balance_critical
        return self.resolve_balance_below()

    def resolve_margin_below(self, default: float) -> float:
        return self.margin_below if self.margin_below is not None else default

    def resolve_margin_critical(self, default: float) -> float:
        return self.margin_critical if self.margin_critical is not None else default

    def resolve_billed_above(self, default: float) -> float:
        return self.billed_above if self.billed_above is not None else default

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"customer": self.customer}
        if self.name:
            result["name"] = self.name
        for fname in ("balance_below", "balance_critical", "margin_below", "margin_critical", "billed_above"):
            val = getattr(self, fname)
            if val is not None:
                result[fname] = val
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WatchTarget:
        old_to_new = {
            "customer_id": "customer",
            "balance_threshold": "balance_below",
            "balance_rearm_threshold": "balance_critical",
            "margin_threshold": "margin_below",
            "margin_rearm_threshold": "margin_critical",
            "billed_min_threshold": "billed_above",
        }
        mapped: dict[str, Any] = {}
        for k, v in data.items():
            key = old_to_new.get(k, k)
            mapped[key] = v
        return cls(
            customer=str(mapped.get("customer", "")),
            name=str(mapped.get("name", "")),
            balance_below=_coerce_type(mapped.get("balance_below"), Optional[float]),
            balance_critical=_coerce_type(mapped.get("balance_critical"), Optional[float]),
            margin_below=_coerce_type(mapped.get("margin_below"), Optional[float]),
            margin_critical=_coerce_type(mapped.get("margin_critical"), Optional[float]),
            billed_above=_coerce_type(mapped.get("billed_above"), Optional[float]),
        )


@dataclass
class Settings:
    global_: GlobalSettings = field(default_factory=GlobalSettings)
    watch: list[WatchTarget] = field(default_factory=list)

    @property
    def customer_ids(self) -> list[str]:
        return sorted(w.customer for w in self.watch) if self.watch else []

    def get_watch(self, customer_id: str) -> WatchTarget:
        for w in self.watch:
            if w.customer == customer_id:
                return w
        return WatchTarget(customer=customer_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "settings": self.global_.to_dict(),
            "watch": [w.to_dict() for w in self.watch],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        global_data = data.get("settings", data.get("global", {}))
        watch_data = data.get("watch", data.get("customers", []))

        if isinstance(watch_data, list) and not watch_data and data.get("customer_ids"):
            cids = data["customer_ids"]
            if isinstance(cids, list):
                watch_data = [{"customer": c} for c in cids]
        elif isinstance(watch_data, list) and not watch_data and global_data.get("customer_ids"):
            cids = global_data.pop("customer_ids", [])
            if isinstance(cids, list):
                watch_data = [{"customer": c} for c in cids]

        gs = GlobalSettings.from_dict(global_data)
        targets = [WatchTarget.from_dict(w) for w in watch_data]

        if targets:
            any_balance = any(w.balance_below is not None for w in targets)
            if not any_balance and isinstance(global_data, dict):
                old_bal = global_data.get("balance_threshold", global_data.get("balance_below"))
                if old_bal is not None:
                    for w in targets:
                        if w.balance_below is None:
                            w.balance_below = float(old_bal)

        return Settings(global_=gs, watch=targets)


def _migrate_json_to_toml() -> bool:
    json_path = Path(SETTINGS_FILE_JSON)
    toml_path = Path(SETTINGS_FILE)
    if not json_path.exists() or toml_path.exists():
        return False
    try:
        with open(json_path, 'r') as f:
            data = json.load(f)
        watch_data = []
        cids = data.pop("customer_ids", [])
        balance_threshold = data.pop("balance_threshold", DEFAULT_BALANCE_BELOW)
        for cid in cids:
            watch_data.append({"customer": str(cid), "balance_below": balance_threshold})
        settings = Settings(
            global_=GlobalSettings.from_dict(data),
            watch=[WatchTarget.from_dict(w) for w in watch_data],
        )
        save_settings(settings)
        json_path.rename(json_path.with_suffix(".json.bak"))
        logging.info("Migrated settings.json -> settings.toml")
        return True
    except (json.JSONDecodeError, OSError) as e:
        logging.warning(f"Migration failed: {e}")
        return False


def load_settings() -> Settings:
    _migrate_json_to_toml()
    try:
        with open(SETTINGS_FILE, 'rb') as f:
            data = tomllib.load(f)
        settings = Settings.from_dict(data)

        if not settings.watch:
            settings.watch = [WatchTarget(customer="18", balance_below=DEFAULT_BALANCE_BELOW)]
        changed = False
        for w in settings.watch:
            if w.balance_below is None:
                w.balance_below = DEFAULT_BALANCE_BELOW
                changed = True
        if changed:
            save_settings(settings)

        return settings
    except FileNotFoundError:
        return Settings(watch=[WatchTarget(customer="18", balance_below=DEFAULT_BALANCE_BELOW)])
    except Exception as e:
        logging.error(f"Corrupted settings file, using defaults: {e}")
        return Settings(watch=[WatchTarget(customer="18", balance_below=DEFAULT_BALANCE_BELOW)])


def save_settings(settings: Settings) -> None:
    lines: list[str] = [
        "# InstacallMonitor - Settings",
        "",
        "# -- Application Settings --",
        "",
        "[settings]",
    ]

    field_labels: dict[str, str] = {
        "check_interval": "seconds between monitoring cycles",
        "request_timeout": "HTTP request timeout (seconds)",
        "cooldown": "minimum seconds between alerts per customer",
        "audio": "enable audible siren alerts",
        "quiet": "suppress non-alert console output",
        "db_retention_days": "auto-purge history older than N days (0 = keep forever)",
        "summary_direction": "outbound or inbound",
        "summary_interval": "5m, 10m, 15m, 1h, etc.",
        "summary_show_all": "show all customers in summary output",
        "active_hours_start": "HH:MM (empty = 24/7)",
        "active_hours_end": "HH:MM (empty = 24/7)",
        "active_days": "mon,tue,wed,thu,fri,sat,sun (empty = all)",
        "webhook_type": "none, telegram, or slack",
        "webhook_url": "Slack/Telegram webhook URL",
        "telegram_chat_id": "Telegram chat ID",
        "logging_json": "true = structured JSON log output",
        "health_port": "0 = disabled, e.g. 8081 for Uptime Kuma",
        "active_profile": "current profile name",
        "siren_loops": "number of siren sweep loops",
        "siren_min_freq": "lowest siren frequency (Hz)",
        "siren_max_freq": "highest siren frequency (Hz)",
        "siren_step_freq": "frequency step between beeps (Hz)",
        "siren_tone_duration": "duration of each beep (ms)",
        "margin_below": "alert when margin drops below this %",
        "margin_critical": "escalation - second alert at lower threshold",
        "billed_above": "only alert if billed minutes exceed this",
    }

    for f in fields(GlobalSettings):
        val = getattr(settings.global_, f.name)
        label = field_labels.get(f.name, "")
        comment = f"  # {label}" if label else ""
        if isinstance(val, str):
            lines.append(f'{f.name} = "{val}"{comment}')
        elif isinstance(val, bool):
            lines.append(f'{f.name} = {str(val).lower()}{comment}')
        else:
            lines.append(f'{f.name} = {val}{comment}')

    lines.append("")
    lines.append("# -- Monitored Customers --")
    lines.append("# Each [[watch]] block is a customer with a balance threshold.")
    lines.append("# balance_below is required - it applies to THIS customer only.")
    lines.append("# All other fields are optional global overrides.")
    lines.append("")

    for w in settings.watch:
        lines.append("[[watch]]")
        lines.append(f'customer = "{w.customer}"')
        if w.name:
            lines.append(f'name = "{w.name}"')
        lines.append(f"balance_below = {_format_float(w.balance_below)}")
        if w.balance_critical is not None and w.balance_critical != w.balance_below:
            lines.append(f"balance_critical = {_format_float(w.balance_critical)}")
        for fname, key in [("margin_below", "margin_below"), ("margin_critical", "margin_critical"),
                           ("billed_above", "billed_above")]:
            val = getattr(w, fname)
            if val is not None:
                lines.append(f"{key} = {_format_float(val)}")
        lines.append("")

    with tempfile.NamedTemporaryFile(mode='w', dir=_BASE_DIR, delete=False, suffix='.tmp',
                                     encoding='utf-8') as tmp:
        tmp.write("\n".join(lines) + "\n")
        tmp_path = tmp.name
    os.replace(tmp_path, SETTINGS_FILE)


def _format_float(val: Optional[float]) -> str:
    if val is None:
        return "0.0"
    if val == int(val):
        return str(int(val))
    return str(val)


def get_credentials() -> tuple[str, str]:
    load_dotenv()
    username = os.getenv("PORTAL_USERNAME")
    password = os.getenv("PORTAL_PASSWORD")
    if not username or not password:
        raise ValueError("PORTAL_USERNAME or PASSWORD not found in .env file.")
    return username, password


def validate_settings(settings: Settings) -> list[str]:
    errors: list[str] = []
    g = settings.global_

    if not settings.watch:
        errors.append("At least one [[watch]] customer must be configured")
    else:
        for w in settings.watch:
            if w.balance_below is None:
                errors.append(f"watch customer '{w.customer}' is missing balance_below")

    for key in ["check_interval", "request_timeout"]:
        val = getattr(g, key, 0)
        if not isinstance(val, (int, float)) or val <= 0:
            errors.append(f"{key} must be a positive number")

    for key in ["siren_loops", "siren_min_freq", "siren_max_freq", "siren_step_freq", "siren_tone_duration"]:
        val = getattr(g, key, 0)
        if not isinstance(val, (int, float)) or val <= 0:
            errors.append(f"{key} must be a positive number")

    if g.summary_direction not in ("outbound", "inbound"):
        errors.append("summary_direction must be 'outbound' or 'inbound'")

    if g.webhook_type not in ("none", "telegram", "slack"):
        errors.append("webhook_type must be 'none', 'telegram', or 'slack'")

    if g.cooldown < 0:
        errors.append("cooldown must be >= 0")

    if g.margin_critical > g.margin_below:
        errors.append("margin_critical must be <= margin_below (more severe)")

    if g.db_retention_days < 0:
        errors.append("db_retention_days must be >= 0")

    if g.active_hours_start:
        m = re.match(r"^(\d{2}):(\d{2})$", g.active_hours_start)
        if not m or not (0 <= int(m.group(1)) <= 23 and 0 <= int(m.group(2)) <= 59):
            errors.append("active_hours_start must be valid HH:MM (00:00-23:59)")
    if g.active_hours_end:
        m = re.match(r"^(\d{2}):(\d{2})$", g.active_hours_end)
        if not m or not (0 <= int(m.group(1)) <= 23 and 0 <= int(m.group(2)) <= 59):
            errors.append("active_hours_end must be valid HH:MM (00:00-23:59)")

    return errors


def write_status(alive: bool, last_check: str = "", error_count: int = 0, last_error: str = "") -> None:
    data: dict[str, Any] = {
        "alive": alive,
        "last_check": last_check,
        "error_count": error_count,
        "last_error": last_error,
    }
    try:
        fd, tmp = tempfile.mkstemp(dir=_BASE_DIR, prefix=".monitor_status_", suffix=".tmp")
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(data, f)
            os.replace(tmp, STATUS_FILE)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    except OSError:
        pass


def setup_logging() -> None:
    handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=5)
    try:
        settings = load_settings()
    except Exception:
        settings = Settings()
    if settings.global_.logging_json:
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s - %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S"
        ))
    root = logging.getLogger('')
    root.handlers.clear()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    root.addHandler(console)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "ts": self.formatTime(record, "%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "name": record.name,
        })


def load_profiles() -> dict[str, Settings]:
    if not Path(PROFILES_FILE).exists():
        return {"default": Settings()}
    try:
        with open(PROFILES_FILE, 'r') as f:
            data = json.load(f)
        return {name: Settings.from_dict(cfg) for name, cfg in data.items()}
    except (json.JSONDecodeError, TypeError, AttributeError):
        return {"default": Settings()}


def save_profiles(profiles: dict[str, Settings]) -> None:
    data = {name: cfg.to_dict() for name, cfg in profiles.items()}
    with open(PROFILES_FILE, 'w') as f:
        json.dump(data, f, indent=2)
