from __future__ import annotations

import re

from settings_models import Settings, is_finite_number

_MAX_INTERVAL = 86400  # 1 day
_MAX_TIMEOUT = 600  # 10 minutes
_MAX_RECOVERY = 3600  # 1 hour
_SUMMARY_INTERVAL_RE = re.compile(r"\d+[smhd]")


def validate_settings(settings: Settings) -> list[str]:
    errors: list[str] = []
    g = settings.global_

    if not settings.watch:
        errors.append("at least one [[watch]] block is required in settings.toml")
    else:
        seen: set[str] = set()
        for w in settings.watch:
            if not w.customer:
                errors.append("each [[watch]] block must have a non-empty 'customer' id")
            elif w.customer in seen:
                errors.append(f"duplicate customer id '{w.customer}' in [[watch]] blocks")
            else:
                seen.add(w.customer)
            if w.balance_below is None:
                errors.append(f"customer '{w.customer}' is missing 'balance_below' — set it in their [[watch]] block")
            elif not is_finite_number(w.balance_below):
                errors.append(f"customer '{w.customer}': balance_below must be a number (got {w.balance_below!r})")
            for attr in ("margin_below", "margin_above", "margin_deadband", "billed_above"):
                val = getattr(w, attr)
                if val is not None and not is_finite_number(val):
                    errors.append(f"customer '{w.customer}': {attr} must be a number (got {val!r})")

            _check_margin_range(
                errors,
                f"customer '{w.customer}': ",
                w.resolve_margin_below(g.margin_below),
                w.resolve_margin_above(g.margin_above),
                w.resolve_margin_deadband(g.margin_deadband),
            )

    _check_interval(errors, "check_interval", g.check_interval, _MAX_INTERVAL)
    _check_interval(errors, "request_timeout", g.request_timeout, _MAX_TIMEOUT)

    if not is_finite_number(g.cooldown):
        errors.append(f"cooldown must be a number (got {g.cooldown!r})")
    elif g.cooldown < 0:
        errors.append(f"cooldown must be >= 0 (got {g.cooldown})")

    if not is_finite_number(g.margin_below) or not is_finite_number(g.margin_above):
        errors.append(f"margin_below and margin_above must be numbers (got {g.margin_below!r}, {g.margin_above!r})")
    elif not is_finite_number(g.margin_deadband):
        errors.append(f"margin_deadband must be a number (got {g.margin_deadband!r})")
    else:
        _check_margin_range(errors, "", g.margin_below, g.margin_above, g.margin_deadband)

    if not is_finite_number(g.db_retention_days):
        errors.append(f"db_retention_days must be a number (got {g.db_retention_days!r})")
    elif g.db_retention_days < 0:
        errors.append(f"db_retention_days must be >= 0 (got {g.db_retention_days})")

    if g.summary_direction not in ("outbound", "inbound"):
        errors.append(f"summary_direction must be 'outbound' or 'inbound' (got '{g.summary_direction}')")

    if not isinstance(g.summary_interval, str) or not _SUMMARY_INTERVAL_RE.fullmatch(g.summary_interval):
        errors.append(f"summary_interval must look like '5m', '15m', '1h' etc. (got {g.summary_interval!r})")

    if not is_finite_number(g.max_workers):
        errors.append(f"max_workers must be a number (got {g.max_workers!r})")
    elif g.max_workers < 1:
        errors.append(f"max_workers must be >= 1 (got {g.max_workers})")

    if not is_finite_number(g.retry_max_attempts):
        errors.append(f"retry_max_attempts must be a number (got {g.retry_max_attempts!r})")
    elif g.retry_max_attempts < 0:
        errors.append(f"retry_max_attempts must be >= 0 (got {g.retry_max_attempts})")
    if not is_finite_number(g.retry_base_delay):
        errors.append(f"retry_base_delay must be a number (got {g.retry_base_delay!r})")
    elif g.retry_base_delay < 0:
        errors.append(f"retry_base_delay must be >= 0 (got {g.retry_base_delay})")
    if not is_finite_number(g.retry_max_delay):
        errors.append(f"retry_max_delay must be a number (got {g.retry_max_delay!r})")
    elif is_finite_number(g.retry_base_delay) and g.retry_max_delay < g.retry_base_delay:
        errors.append(f"retry_max_delay ({g.retry_max_delay}) must be >= retry_base_delay ({g.retry_base_delay})")
    if not is_finite_number(g.retry_jitter_max):
        errors.append(f"retry_jitter_max must be a number (got {g.retry_jitter_max!r})")
    elif g.retry_jitter_max < 0:
        errors.append(f"retry_jitter_max must be >= 0 (got {g.retry_jitter_max})")

    if not is_finite_number(g.circuit_failure_threshold):
        errors.append(f"circuit_failure_threshold must be a number (got {g.circuit_failure_threshold!r})")
    elif g.circuit_failure_threshold < 1:
        errors.append(f"circuit_failure_threshold must be >= 1 (got {g.circuit_failure_threshold})")
    _check_interval(errors, "circuit_recovery_timeout", g.circuit_recovery_timeout, _MAX_RECOVERY)

    if not is_finite_number(g.siren_loops):
        errors.append(f"siren_loops must be a number (got {g.siren_loops!r})")
    elif g.siren_loops < 0:
        errors.append(f"siren_loops must be >= 0 (got {g.siren_loops})")
    if not is_finite_number(g.siren_min_freq) or not is_finite_number(g.siren_max_freq):
        errors.append(
            f"siren_min_freq and siren_max_freq must be numbers (got {g.siren_min_freq!r}, {g.siren_max_freq!r})"
        )
    elif g.siren_max_freq <= g.siren_min_freq:
        errors.append(f"siren_max_freq ({g.siren_max_freq}) must be > siren_min_freq ({g.siren_min_freq})")
    _check_positive(errors, "siren_step_freq", g.siren_step_freq)
    _check_positive(errors, "siren_tone_duration", g.siren_tone_duration)

    if not is_finite_number(g.web_dashboard_port):
        errors.append(f"web_dashboard_port must be a number (got {g.web_dashboard_port!r})")
    elif not (0 < g.web_dashboard_port < 65536):
        errors.append(f"web_dashboard_port must be between 1 and 65535 (got {g.web_dashboard_port})")
    if not is_finite_number(g.web_dashboard_refresh):
        errors.append(f"web_dashboard_refresh must be a number (got {g.web_dashboard_refresh!r})")
    elif g.web_dashboard_refresh <= 0:
        errors.append(f"web_dashboard_refresh must be > 0 (got {g.web_dashboard_refresh})")
    if not isinstance(g.web_dashboard_host, str) or not g.web_dashboard_host.strip():
        errors.append("web_dashboard_host must be a non-empty string")
    elif any(ord(ch) < 32 for ch in g.web_dashboard_host):
        errors.append("web_dashboard_host must not contain control characters")

    return errors


def _check_margin_range(errors: list[str], prefix: str, low: object, high: object, deadband: object) -> None:
    if not (is_finite_number(low) and is_finite_number(high)):
        return
    if high <= low:
        errors.append(f"{prefix}margin_above ({high}) must be > margin_below ({low})")
        return
    if not is_finite_number(deadband):
        return
    half = (high - low) / 2
    if deadband < 0:
        errors.append(f"{prefix}margin_deadband ({deadband}) must be >= 0")
    elif deadband >= half:
        errors.append(f"{prefix}margin_deadband ({deadband}) must be < (margin_above - margin_below) / 2 = {half:.1f}")


def _check_interval(errors: list[str], name: str, value: object, upper: int) -> None:
    if not is_finite_number(value):
        errors.append(f"{name} must be a number (got {value!r})")
    elif value <= 0:
        errors.append(f"{name} must be > 0 (got {value})")
    elif value > upper:
        errors.append(f"{name} must be <= {upper} (got {value})")


def _check_positive(errors: list[str], name: str, value: object) -> None:
    if not is_finite_number(value):
        errors.append(f"{name} must be a number (got {value!r})")
    elif value <= 0:
        errors.append(f"{name} must be > 0 (got {value})")
