from __future__ import annotations

import math
from dataclasses import dataclass, field, fields
from typing import Any, Optional, TypeGuard, get_type_hints

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


def is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_finite_number(value: object) -> TypeGuard[int | float]:
    return is_number(value) and math.isfinite(float(value))


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
    margin_above: float = 75.0
    margin_deadband: float = 3.0
    billed_above: float = 70.0
    max_workers: int = 4
    web_dashboard: bool = True
    web_dashboard_port: int = 8765
    web_dashboard_refresh: int = 5

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GlobalSettings:
        field_names = {f.name for f in fields(cls)}
        # Resolve annotations to real types: `from __future__ import annotations`
        # turns dataclass field types into strings, which _coerce cannot match.
        field_types = get_type_hints(cls)
        filtered: dict[str, Any] = {}
        for k, v in data.items():
            if k in field_names:
                filtered[k] = _coerce(v, field_types.get(k))
        return cls(**filtered)


@dataclass(frozen=True)
class FieldSpec:
    """One editable ``[settings]`` field.

    Single source of truth: drives both the generated ``settings.toml`` template
    (config.py) and the web dashboard form (dashboard_ui.py).
    """

    name: str
    label: str
    group: str
    kind: str  # "int" | "float" | "bool" | "text" | "select"
    comment: str = ""
    options: tuple[str, ...] = ()


FIELD_SPEC: tuple[FieldSpec, ...] = (
    FieldSpec("check_interval", "Check interval (s)", "General", "int", "Interval between check cycles, in seconds"),
    FieldSpec("request_timeout", "Request timeout (s)", "General", "int", "HTTP request timeout, in seconds"),
    FieldSpec("audio", "Audio sirens", "General", "bool", "Enable audible siren alerts"),
    FieldSpec("cooldown", "Alert cooldown (s)", "General", "int", "Minimum seconds between repeated alerts"),
    FieldSpec("db_retention_days", "DB retention (days)", "General", "int", "Days of history to keep in the database"),
    FieldSpec(
        "summary_direction",
        "Summary direction",
        "General",
        "select",
        'Summary direction: "outbound" or "inbound"',
        ("outbound", "inbound"),
    ),
    FieldSpec("summary_interval", "Summary interval", "General", "text", 'Summary interval: "5m", "15m", "1h" etc.'),
    FieldSpec("max_workers", "Max workers", "General", "int", "Number of concurrent worker threads for scraping"),
    FieldSpec("margin_below", "Margin below (%)", "Thresholds", "float", "Margin below this % triggers a low alert"),
    FieldSpec("margin_above", "Margin above (%)", "Thresholds", "float", "Margin above this % triggers a high alert"),
    FieldSpec(
        "margin_deadband", "Margin deadband (%)", "Thresholds", "float", "Recovery deadband prevents alert flapping"
    ),
    FieldSpec(
        "billed_above", "Billed above (min)", "Thresholds", "float", "Only alert when billed minutes exceed this"
    ),
    FieldSpec("web_dashboard", "Dashboard enabled", "Dashboard", "bool", "Serve the local HTTP dashboard"),
    FieldSpec("web_dashboard_port", "Dashboard port", "Dashboard", "int", "Dashboard port (loopback only)"),
    FieldSpec("web_dashboard_refresh", "Refresh (s)", "Dashboard", "int", "Browser auto-refresh interval, in seconds"),
)


@dataclass
class WatchTarget:
    customer: str
    name: str = ""
    balance_below: Optional[float] = None
    margin_below: Optional[float] = None
    margin_above: Optional[float] = None
    margin_deadband: Optional[float] = None
    billed_above: Optional[float] = None

    def resolve_balance_below(self) -> float:
        return self.balance_below if self.balance_below is not None else DEFAULT_BALANCE_BELOW

    def resolve_margin_below(self, default: float) -> float:
        return self.margin_below if self.margin_below is not None else default

    def resolve_margin_above(self, default: float) -> float:
        return self.margin_above if self.margin_above is not None else default

    def resolve_margin_deadband(self, default: float) -> float:
        return self.margin_deadband if self.margin_deadband is not None else default

    def resolve_billed_above(self, default: float) -> float:
        return self.billed_above if self.billed_above is not None else default

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"customer": self.customer}
        if self.name:
            result["name"] = self.name
        for fname in ("balance_below", "margin_below", "margin_above", "margin_deadband", "billed_above"):
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
            margin_above=_coerce(data.get("margin_above"), Optional[float]),
            margin_deadband=_coerce(data.get("margin_deadband"), Optional[float]),
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


def fmt_val(val: Any) -> str:
    if val is None:
        return "0.0"
    if isinstance(val, float):
        if not math.isfinite(val):
            return str(val)
        if val == int(val):
            return str(int(val))
    return str(val)
