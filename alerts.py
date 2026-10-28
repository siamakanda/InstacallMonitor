from __future__ import annotations

import logging
import threading
from typing import Callable

from notify import safe_notify as _safe_notify
from persistence import get_alert_state, set_alert_state
from settings_models import Settings
from siren import SirenManager

__all__ = [
    "SirenManager",
    "check_balance_alert",
    "check_margin_alert",
    "get_session_alert_count",
    "reset_session_stats",
]


class _SessionStats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0

    def increment(self) -> None:
        with self._lock:
            self._count += 1

    def count(self) -> int:
        with self._lock:
            return self._count

    def reset(self) -> None:
        with self._lock:
            self._count = 0


_stats = _SessionStats()

_SIREN_MAX = 3
_NOTIFY_MAX = 5


def get_session_alert_count() -> int:
    return _stats.count()


def reset_session_stats() -> None:
    _stats.reset()


def _play_siren_async(mgr: SirenManager, play_fn: Callable[[], None], label: str) -> bool:
    if not mgr.acquire():
        print(f"  Siren skipped (already playing) - {label}")
        return False

    print(f"  Siren: {label}")

    def _run() -> None:
        try:
            play_fn()
        finally:
            mgr.release()

    threading.Thread(target=_run, daemon=True).start()
    return True


def trigger_balance_alert(
    customer_id: str,
    balance: float,
    customer_name: str,
    settings: Settings,
    mgr: SirenManager,
) -> None:
    g = settings.global_
    w = settings.get_watch(customer_id)
    threshold = w.resolve_balance_below()

    notified = _safe_notify(
        title="BALANCE ALERT",
        message=f"{customer_name} (ID: {customer_id}) balance dropped to {balance:.4f}!",
        timeout=10,
    )
    logging.warning(f"ALERT for {customer_name} (ID: {customer_id}): Balance {balance:.4f} < {threshold:+.1f}")

    fired = notified
    if g.audio:
        fired = _play_siren_async(mgr, mgr.play_rising_falling, f"BALANCE {customer_name} ({customer_id})") or fired
    if fired:
        _stats.increment()


def trigger_margin_alert(
    customer_id: str,
    margin: float,
    billed_min: float,
    customer_name: str,
    settings: Settings,
    mgr: SirenManager,
    direction: str = "low",
) -> None:
    g = settings.global_
    w = settings.get_watch(customer_id)
    low = w.resolve_margin_below(g.margin_below)
    high = w.resolve_margin_above(g.margin_above)
    billed_threshold = w.resolve_billed_above(g.billed_above)

    if direction == "low":
        title = "MARGIN ALERT"
        msg = f"{customer_name} (ID: {customer_id}) Margin dropped to {margin:.1f}%! (Billed: {billed_min:.1f} min)"
        log_msg = f"Margin {margin:.1f}% < {low}%, Billed {billed_min:.1f} > {billed_threshold}"
    else:
        title = "MARGIN ALERT (HIGH)"
        msg = f"{customer_name} (ID: {customer_id}) Margin surged to {margin:.1f}%! (Billed: {billed_min:.1f} min)"
        log_msg = f"Margin {margin:.1f}% > {high}%, Billed {billed_min:.1f} > {billed_threshold}"

    notified = _safe_notify(title=title, message=msg, timeout=10)
    logging.warning(f"MARGIN ALERT for {customer_name} (ID: {customer_id}): {log_msg}")

    fired = notified
    if g.audio:
        play_fn = mgr.play_alternating if direction == "low" else mgr.play_steady
        label = f"MARGIN {'LOW' if direction == 'low' else 'HIGH'} {customer_name} ({customer_id})"
        fired = _play_siren_async(mgr, play_fn, label) or fired
    if fired:
        _stats.increment()


def trigger_recovery_alert(
    customer_id: str,
    recovered_value: float,
    customer_name: str,
    settings: Settings,
    alert_type: str,
    direction: str = "",
) -> None:
    if alert_type == "balance":
        title = "BALANCE RECOVERED"
        msg = f"{customer_name} (ID: {customer_id}) balance recovered to {recovered_value:.4f}"
    elif direction == "high":
        title = "MARGIN NORMALIZED"
        msg = f"{customer_name} (ID: {customer_id}) margin normalized to {recovered_value:.1f}%"
    else:
        title = "MARGIN RECOVERED"
        msg = f"{customer_name} (ID: {customer_id}) margin recovered to {recovered_value:.1f}%"

    _safe_notify(title=title, message=msg, timeout=10)
    logging.info(msg)


def check_balance_alert(
    mgr: SirenManager,
    customer_id: str,
    balance: float,
    customer_name: str,
    settings: Settings,
) -> None:
    w = settings.get_watch(customer_id)
    threshold = w.resolve_balance_below()
    cooldown = settings.global_.cooldown
    state, count, _direction = get_alert_state(customer_id, "balance")

    if balance < threshold:
        if state == 0 and mgr.can_alert(customer_id, "balance", cooldown):
            trigger_balance_alert(customer_id, balance, customer_name, settings, mgr)
            set_alert_state(customer_id, "balance", 1, count=1)
            print(f"  !! BALANCE ALERT for {customer_name} (ID: {customer_id})  [#1]")
        elif state == 1 and mgr.can_alert(customer_id, "balance", cooldown):
            new_count = count + 1
            trigger_balance_alert_escalated(customer_id, balance, customer_name, settings, mgr, new_count)
            set_alert_state(customer_id, "balance", 1, count=new_count)
            print(f"  !! BALANCE ALERT for {customer_name} (ID: {customer_id})  [#{new_count}]")
    elif state > 0 and balance >= threshold:
        trigger_recovery_alert(customer_id, balance, customer_name, settings, "balance")
        set_alert_state(customer_id, "balance", 0, count=0)


def check_margin_alert(
    mgr: SirenManager,
    customer_id: str,
    margin: float,
    billed_min: float,
    customer_name: str,
    settings: Settings,
    monitored: bool = False,
) -> None:
    if not monitored:
        # Clear any state left over from before the customer was removed from
        # [[watch]], so re-adding them starts fresh instead of resuming escalation.
        state, _count, _direction = get_alert_state(customer_id, "margin")
        if state != 0:
            set_alert_state(customer_id, "margin", 0, count=0, direction="")
        return

    g = settings.global_
    w = settings.get_watch(customer_id)
    low = w.resolve_margin_below(g.margin_below)
    high = w.resolve_margin_above(g.margin_above)
    db = w.resolve_margin_deadband(g.margin_deadband)
    billed_threshold = w.resolve_billed_above(g.billed_above)
    cooldown = g.cooldown
    state, count, prev_direction = get_alert_state(customer_id, "margin")

    bill_qualifies = billed_min > billed_threshold
    in_deadband = (low <= margin < low + db) or (high - db < margin <= high)
    in_range = (low + db) <= margin <= (high - db)
    low_alert = margin < low and bill_qualifies
    high_alert = margin > high and bill_qualifies
    is_alerting = low_alert or high_alert
    direction = "low" if low_alert else "high"

    if is_alerting:
        if not in_deadband:
            if state == 0 and mgr.can_alert(customer_id, "margin", cooldown):
                trigger_margin_alert(
                    customer_id,
                    margin,
                    billed_min,
                    customer_name,
                    settings,
                    mgr,
                    direction=direction,
                )
                set_alert_state(customer_id, "margin", 1, count=1, direction=direction)
                direction_tag = direction.upper()
                print(f"  !! MARGIN ALERT ({direction_tag}) for {customer_name} (ID: {customer_id})  [#1]")
            elif state == 1 and mgr.can_alert(customer_id, "margin", cooldown):
                new_count = count + 1
                trigger_margin_alert_escalated(
                    customer_id,
                    margin,
                    billed_min,
                    customer_name,
                    settings,
                    mgr,
                    new_count,
                    direction=direction,
                )
                set_alert_state(customer_id, "margin", 1, count=new_count, direction=direction)
                direction_tag = direction.upper()
                print(f"  !! MARGIN ALERT ({direction_tag}) for {customer_name} (ID: {customer_id})  [#{new_count}]")
    elif state > 0 and (in_range or not bill_qualifies):
        trigger_recovery_alert(customer_id, margin, customer_name, settings, "margin", direction=prev_direction)
        set_alert_state(customer_id, "margin", 0, count=0, direction="")


def _should_siren(count: int) -> bool:
    return count <= _SIREN_MAX


def _should_notify(count: int) -> bool:
    return count <= _NOTIFY_MAX


def trigger_balance_alert_escalated(
    customer_id: str,
    balance: float,
    customer_name: str,
    settings: Settings,
    mgr: SirenManager,
    count: int,
) -> None:
    g = settings.global_
    w = settings.get_watch(customer_id)
    threshold = w.resolve_balance_below()

    notified = False
    if _should_notify(count):
        notified = _safe_notify(
            title=f"BALANCE ALERT [#{count}]",
            message=f"{customer_name} (ID: {customer_id}) balance still at {balance:.4f}!",
            timeout=10,
        )
    logging.warning(
        f"ALERT [#{count}] for {customer_name} (ID: {customer_id}): Balance {balance:.4f} < {threshold:+.1f}"
    )

    fired = notified
    if g.audio and _should_siren(count):
        fired = (
            _play_siren_async(mgr, mgr.play_rising_falling, f"BALANCE #{count} {customer_name} ({customer_id})")
            or fired
        )
    if fired:
        _stats.increment()


def trigger_margin_alert_escalated(
    customer_id: str,
    margin: float,
    billed_min: float,
    customer_name: str,
    settings: Settings,
    mgr: SirenManager,
    count: int,
    direction: str = "low",
) -> None:
    g = settings.global_
    w = settings.get_watch(customer_id)
    low = w.resolve_margin_below(g.margin_below)
    high = w.resolve_margin_above(g.margin_above)
    billed_threshold = w.resolve_billed_above(g.billed_above)

    if direction == "low":
        log_msg = f"Margin {margin:.1f}% < {low}%, Billed {billed_min:.1f} > {billed_threshold}"
    else:
        log_msg = f"Margin {margin:.1f}% > {high}%, Billed {billed_min:.1f} > {billed_threshold}"

    notified = False
    if _should_notify(count):
        notified = _safe_notify(
            title=f"MARGIN ALERT [#{count}]",
            message=(
                f"{customer_name} (ID: {customer_id}) Margin still at {margin:.1f}%! (Billed: {billed_min:.1f} min)"
            ),
            timeout=10,
        )
    logging.warning(f"MARGIN ALERT [#{count}] for {customer_name} (ID: {customer_id}): {log_msg}")

    fired = notified
    if g.audio and _should_siren(count):
        play_fn = mgr.play_alternating if direction == "low" else mgr.play_steady
        fired = _play_siren_async(mgr, play_fn, f"MARGIN #{count} {customer_name} ({customer_id})") or fired
    if fired:
        _stats.increment()
