from __future__ import annotations

import logging
import sys
import threading
import time

from config import Settings
from persistence import get_alert_state, set_alert_state


class SirenManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._playing = False
        self._timestamps: dict[str, float] = {}

    @property
    def is_playing(self) -> bool:
        return self._playing

    def can_alert(self, customer_id: str, alert_type: str, cooldown: float) -> bool:
        with self._lock:
            key = f"{customer_id}:{alert_type}"
            now = time.time()
            last = self._timestamps.get(key, 0)
            if now - last < cooldown:
                return False
            self._timestamps[key] = now
            return True

    def _acquire(self) -> bool:
        with self._lock:
            if self._playing:
                return False
            self._playing = True
            return True

    def _release(self) -> None:
        with self._lock:
            self._playing = False

    def play_rising_falling(self) -> None:
        freq_min, freq_max, step, duration = 2200, 3500, 130, 50
        loops = 10
        for _ in range(loops):
            for freq in range(freq_min, freq_max, step):
                _beep(freq, duration)
            for freq in range(freq_max, freq_min, -step):
                _beep(freq, duration)

    def play_alternating(self) -> None:
        freq_min, freq_max = 2200, 3500
        tone = 100
        for _ in range(20):
            _beep(freq_min, tone)
            _beep(freq_max, tone)


def _beep(freq: int, duration: int) -> None:
    if sys.platform == "win32":
        import winsound
        winsound.Beep(freq, duration)


def _play_siren_async(mgr: SirenManager, play_fn, label: str) -> None:
    with mgr._lock:
        if mgr._playing:
            print(f"  Siren skipped (already playing) - {label}")
            return
        mgr._playing = True

    print(f"  Siren: {label}")

    def _run() -> None:
        try:
            play_fn()
        finally:
            mgr._release()

    threading.Thread(target=_run, daemon=True).start()


def _safe_notify(title: str, message: str, timeout: int = 10) -> None:
    try:
        from plyer import notification
        notification.notify(title=title, message=message, app_name="InstacallMonitor", timeout=timeout)
    except Exception as e:
        logging.warning(f"Desktop notification failed: {e}")


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

    _safe_notify(
        title="BALANCE ALERT",
        message=f"{customer_name} (ID: {customer_id}) balance dropped to {balance:.4f}!",
        timeout=10,
    )
    logging.warning(
        f"ALERT for {customer_name} (ID: {customer_id}): Balance {balance:.4f} < {threshold:+.1f}"
    )

    if g.audio:
        _play_siren_async(mgr, mgr.play_rising_falling, f"BALANCE {customer_name} ({customer_id})")


def trigger_margin_alert(
    customer_id: str,
    margin: float,
    billed_min: float,
    customer_name: str,
    settings: Settings,
    mgr: SirenManager,
) -> None:
    g = settings.global_

    _safe_notify(
        title="MARGIN ALERT",
        message=f"{customer_name} (ID: {customer_id}) Margin dropped to {margin:.1f}%! (Billed: {billed_min:.1f} min)",
        timeout=10,
    )
    logging.warning(
        f"MARGIN ALERT for {customer_name} (ID: {customer_id}): "
        f"Margin {margin:.1f}% < {g.margin_below}%, Billed {billed_min:.1f} > {g.billed_above}"
    )

    if g.audio:
        _play_siren_async(mgr, mgr.play_alternating, f"MARGIN {customer_name} ({customer_id})")


def trigger_recovery_alert(
    customer_id: str,
    recovered_value: float,
    customer_name: str,
    settings: Settings,
    alert_type: str,
) -> None:
    if alert_type == "balance":
        title = "BALANCE RECOVERED"
        msg = f"{customer_name} (ID: {customer_id}) balance recovered to {recovered_value:.4f}"
    else:
        title = "MARGIN RECOVERED"
        msg = f"{customer_name} (ID: {customer_id}) margin recovered to {recovered_value:.1f}%"

    _safe_notify(title=title, message=msg, timeout=10)
    logging.info(msg)


# -- Alert evaluation helpers used by monitor loop --

_SIREN_MAX = 3
_NOTIFY_MAX = 5


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
    state, count = get_alert_state(customer_id, "balance")

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
        return

    g = settings.global_
    w = settings.get_watch(customer_id)
    threshold = w.resolve_margin_below(g.margin_below)
    billed_threshold = w.resolve_billed_above(g.billed_above)
    cooldown = g.cooldown
    state, count = get_alert_state(customer_id, "margin")

    if margin < threshold and billed_min > billed_threshold:
        if state == 0 and mgr.can_alert(customer_id, "margin", cooldown):
            trigger_margin_alert(customer_id, margin, billed_min, customer_name, settings, mgr)
            set_alert_state(customer_id, "margin", 1, count=1)
            print(f"  !! MARGIN ALERT for {customer_name} (ID: {customer_id})  [#1]")
        elif state == 1 and mgr.can_alert(customer_id, "margin", cooldown):
            new_count = count + 1
            trigger_margin_alert_escalated(customer_id, margin, billed_min, customer_name, settings, mgr, new_count)
            set_alert_state(customer_id, "margin", 1, count=new_count)
            print(f"  !! MARGIN ALERT for {customer_name} (ID: {customer_id})  [#{new_count}]")
    elif state > 0 and (margin >= threshold or billed_min <= billed_threshold):
        trigger_recovery_alert(customer_id, margin, customer_name, settings, "margin")
        set_alert_state(customer_id, "margin", 0, count=0)


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

    if _should_notify(count):
        _safe_notify(
            title=f"BALANCE ALERT [#{count}]",
            message=f"{customer_name} (ID: {customer_id}) balance still at {balance:.4f}!",
            timeout=10,
        )
    logging.warning(
        f"ALERT [#{count}] for {customer_name} (ID: {customer_id}): Balance {balance:.4f} < {threshold:+.1f}"
    )

    if g.audio and _should_siren(count):
        _play_siren_async(mgr, mgr.play_rising_falling, f"BALANCE #{count} {customer_name} ({customer_id})")


def trigger_margin_alert_escalated(
    customer_id: str,
    margin: float,
    billed_min: float,
    customer_name: str,
    settings: Settings,
    mgr: SirenManager,
    count: int,
) -> None:
    g = settings.global_

    if _should_notify(count):
        _safe_notify(
            title=f"MARGIN ALERT [#{count}]",
            message=(
                f"{customer_name} (ID: {customer_id}) Margin still at {margin:.1f}%!"
                f" (Billed: {billed_min:.1f} min)"
            ),
            timeout=10,
        )
    logging.warning(
        f"MARGIN ALERT [#{count}] for {customer_name} (ID: {customer_id}): "
        f"Margin {margin:.1f}% < {g.margin_below}%, Billed {billed_min:.1f} > {g.billed_above}"
    )

    if g.audio and _should_siren(count):
        _play_siren_async(mgr, mgr.play_alternating, f"MARGIN #{count} {customer_name} ({customer_id})")
