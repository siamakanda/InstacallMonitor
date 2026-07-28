from __future__ import annotations

import logging
import os
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
SETTINGS_FILE = str(_BASE_DIR / "settings.toml")
DB_FILE = str(_BASE_DIR / "instacallmonitor.db")

DEFAULT_BALANCE_BELOW = -500.0


def _coerce(value: Any, target: Any) -> Any:
    if value is None:
        return None
    origin = getattr(target, "__origin__", None)
    if origin is not None:
        args = getattr(target, "__args__", ())
        for arg in args:
            if arg is not type(None):
                target = arg
                break
    if target is bool and not isinstance(value, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes", "on")
        return bool(value)
    if target is int and not isinstance(value, int):
        try:
            return int(value)
        except (ValueError, TypeError):
            return value
    if target is float and not isinstance(value, float):
        try:
            return float(value)
        except (ValueError, TypeError):
            return value
    return value


@dataclass
class GlobalSettings:
    check_interval: int = 600
    request_timeout: int = 10
    audio: bool = True
    cooldown: int = 300
    db_retention_days: int = 30
    summary_direction: str = "outbound"
    summary_interval: str = "5m"
    margin_below: float = 30.0
    billed_above: float = 70.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GlobalSettings:
        field_names = {f.name for f in fields(cls)}
        field_types = {f.name: f.type for f in fields(cls)}
        filtered: dict[str, Any] = {}
        for k, v in data.items():
            if k in field_names:
                filtered[k] = _coerce(v, field_types.get(k))
        return cls(**filtered)


@dataclass
class WatchTarget:
    customer: str
    name: str = ""
    balance_below: Optional[float] = None
    margin_below: Optional[float] = None
    billed_above: Optional[float] = None

    def resolve_balance_below(self) -> float:
        return self.balance_below if self.balance_below is not None else DEFAULT_BALANCE_BELOW

    def resolve_margin_below(self, default: float) -> float:
        return self.margin_below if self.margin_below is not None else default

    def resolve_billed_above(self, default: float) -> float:
        return self.billed_above if self.billed_above is not None else default

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"customer": self.customer}
        if self.name:
            result["name"] = self.name
        for fname in ("balance_below", "margin_below", "billed_above"):
            val = getattr(self, fname)
            if val is not None:
                result[fname] = val
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WatchTarget:
        return cls(
            customer=str(data.get("customer", "")),
            name=str(data.get("name", "")),
            balance_below=_coerce(data.get("balance_below"), Optional[float]),
            margin_below=_coerce(data.get("margin_below"), Optional[float]),
            billed_above=_coerce(data.get("billed_above"), Optional[float]),
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
            "settings": {f.name: getattr(self.global_, f.name) for f in fields(GlobalSettings)},
            "watch": [w.to_dict() for w in self.watch],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        global_data = data.get("settings", {})
        watch_data = data.get("watch", [])
        gs = GlobalSettings.from_dict(global_data)
        targets = [WatchTarget.from_dict(w) for w in watch_data]
        return cls(global_=gs, watch=targets)


def _fmt_val(val: Any) -> str:
    if val is None:
        return "0.0"
    if isinstance(val, float) and val == int(val):
        return str(int(val))
    return str(val)


def load_settings() -> Settings:
    try:
        with open(SETTINGS_FILE, "rb") as f:
            data = tomllib.load(f)
        settings = Settings.from_dict(data)
        if not settings.watch:
            settings.watch = [WatchTarget(customer="18", balance_below=DEFAULT_BALANCE_BELOW)]
        for w in settings.watch:
            if w.balance_below is None:
                w.balance_below = DEFAULT_BALANCE_BELOW
        return settings
    except FileNotFoundError:
        return Settings(watch=[WatchTarget(customer="18", balance_below=DEFAULT_BALANCE_BELOW)])
    except Exception as e:
        logging.error(f"Corrupted settings file: {e}")
        return Settings(watch=[WatchTarget(customer="18", balance_below=DEFAULT_BALANCE_BELOW)])


def save_settings(settings: Settings) -> None:
    lines: list[str] = [
        "# InstacallMonitor - Settings",
        "",
        "[settings]",
    ]
    for f in fields(GlobalSettings):
        val = getattr(settings.global_, f.name)
        if isinstance(val, str):
            lines.append(f'{f.name} = "{val}"')
        elif isinstance(val, bool):
            lines.append(f"{f.name} = {str(val).lower()}")
        else:
            lines.append(f"{f.name} = {val}")

    lines.append("")
    for w in settings.watch:
        lines.append("[[watch]]")
        lines.append(f'customer = "{w.customer}"')
        if w.name:
            lines.append(f'name = "{w.name}"')
        lines.append(f"balance_below = {_fmt_val(w.balance_below)}")
        for key in ("margin_below", "billed_above"):
            val = getattr(w, key)
            if val is not None:
                lines.append(f"{key} = {_fmt_val(val)}")
        lines.append("")

    content = "\n".join(lines) + "\n"
    with tempfile.NamedTemporaryFile(mode="w", dir=_BASE_DIR, delete=False, suffix=".tmp", encoding="utf-8") as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    os.replace(tmp_path, SETTINGS_FILE)


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

    for key in ("check_interval", "request_timeout"):
        val = getattr(g, key, 0)
        if not isinstance(val, (int, float)) or val <= 0:
            errors.append(f"{key} must be a positive number")

    if g.cooldown < 0:
        errors.append("cooldown must be >= 0")
    if g.db_retention_days < 0:
        errors.append("db_retention_days must be >= 0")
    if g.summary_direction not in ("outbound", "inbound"):
        errors.append("summary_direction must be 'outbound' or 'inbound'")

    return errors


def setup_logging() -> None:
    handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=5)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S"))
    root = logging.getLogger("")
    root.handlers.clear()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    root.addHandler(console)
