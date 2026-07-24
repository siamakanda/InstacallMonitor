from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Optional

from config import GlobalSettings, Settings
from notifications import send_webhook
from persistence import AlertEvent, get_alert_state, insert_alert_event, set_alert_state


class SirenManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._playing = False
        self._timestamps: dict[str, float] = {}

    @property
    def is_playing(self) -> bool:
        return self._playing

    def _can_alert(self, customer_id: str, alert_type: str, cooldown: float) -> bool:
        with self._lock:
            key = f"{customer_id}:{alert_type}"
            now = time.time()
            last = self._timestamps.get(key, 0)
            if now - last < cooldown:
                return False
            self._timestamps[key] = now
            return True

    def can_alert(self, customer_id: str, cooldown: float) -> bool:
        return self._can_alert(customer_id, "balance", cooldown)

    def can_margin_alert(self, customer_id: str, cooldown: float) -> bool:
        return self._can_alert(customer_id, "margin", cooldown)

    def play_rising_falling(self, global_: GlobalSettings) -> None:
        for _ in range(global_.siren_loops):
            for freq in range(global_.siren_min_freq, global_.siren_max_freq, global_.siren_step_freq):
                _beep(freq, global_.siren_tone_duration)
            for freq in range(global_.siren_max_freq, global_.siren_min_freq, -global_.siren_step_freq):
                _beep(freq, global_.siren_tone_duration)

    def play_alternating(self, global_: GlobalSettings) -> None:
        tone = max(global_.siren_tone_duration, 100)
        for _ in range(global_.siren_loops * 2):
            _beep(global_.siren_min_freq, tone)
            _beep(global_.siren_max_freq, tone)

    def _acquire(self) -> bool:
        with self._lock:
            if self._playing:
                return False
            self._playing = True
            return True

    def _release(self) -> None:
        with self._lock:
            self._playing = False


def _beep(freq: int, duration: int) -> None:
    if sys.platform == "win32":
        import winsound
        winsound.Beep(freq, duration)


def play_siren(
    manager: SirenManager,
    play_fn,
    customer_name: str,
    customer_id: str,
    alert_type: str,
) -> None:
    with manager._lock:
        if manager._playing:
            print(f"Siren skipped (already playing) - {alert_type} for {customer_name} (ID: {customer_id})")
            return
        manager._playing = True
    print(f"Siren for {customer_name} (ID: {customer_id}) - {alert_type}...")
    def _play_and_release():
        try:
            play_fn()
        finally:
            with manager._lock:
                manager._playing = False
    threading.Thread(target=_play_and_release, daemon=True).start()


class AlertStateManager:
    def get_balance_state(self, customer_id: str) -> int:
        return get_alert_state(customer_id, "balance").state

    def get_margin_state(self, customer_id: str) -> int:
        return get_alert_state(customer_id, "margin").state

    def set_balance_state(self, customer_id: str, state: int) -> None:
        set_alert_state(customer_id, "balance", state)

    def set_margin_state(self, customer_id: str, state: int) -> None:
        set_alert_state(customer_id, "margin", state)


_siren_manager = SirenManager()
_alert_state = AlertStateManager()


def _safe_notify(title: str, message: str, timeout: int = 10) -> None:
    try:
        _safe_notify(title=title, message=message, app_name="InstacallMonitor", timeout=timeout)
    except Exception as e:
        logging.warning(f"Desktop notification failed: {e}")


def _log_alert_event(customer_id: str, alert_type: str, severity: str,
                     value: Optional[float], threshold: Optional[float],
                     billed_min: Optional[float] = None) -> None:
    try:
        insert_alert_event(AlertEvent(
            customer_id=customer_id,
            alert_type=alert_type,
            severity=severity,
            value=value,
            threshold=threshold,
            billed_min=billed_min,
        ))
    except Exception as e:
        logging.warning(f"Failed to log alert event: {e}")


def trigger_balance_alert(
    customer_id: str,
    current_balance: float,
    customer_name: str,
    settings: Settings,
    escalated: bool = False,
) -> None:
    g = settings.global_
    w = settings.get_watch(customer_id)
    bal_below = w.resolve_balance_below()
    bal_critical = w.resolve_balance_critical()
    title = "BALANCE ESCALATION" if escalated else "BALANCE CRITICAL ALERT"
    severity = "escalated" if escalated else "dropped"

    _log_alert_event(customer_id, "balance", severity, current_balance,
                     bal_critical if escalated else bal_below)

    _safe_notify(
        title=title,
        message=f"{customer_name} (ID: {customer_id}) balance {severity} to {current_balance:.4f}!",
        timeout=10,
    )
    thresh_info = f" (escalation < {bal_critical:+.1f})" if escalated else ""
    logging.warning(
        f"ALERT TRIGGERED for {customer_name} (ID: {customer_id}): "
        f"Balance {current_balance:.4f} < {bal_below}{thresh_info}"
    )
    send_webhook(g, title,
                 f"{customer_name} (ID: {customer_id}) balance {severity} to {current_balance:.4f}")

    if g.audio:
        play_siren(
            _siren_manager,
            lambda: _siren_manager.play_rising_falling(g),
            customer_name,
            customer_id,
            "BALANCE" if not escalated else "BALANCE ESCALATION",
        )


def trigger_margin_alert(
    customer_id: str,
    margin: float,
    billed_min: float,
    customer_name: str,
    settings: Settings,
    escalated: bool = False,
) -> None:
    g = settings.global_
    title = "MARGIN ESCALATION" if escalated else "MARGIN CRITICAL ALERT"
    severity = "escalated" if escalated else "dropped"

    _log_alert_event(customer_id, "margin", severity, margin,
                     g.margin_critical if escalated else g.margin_below,
                     billed_min)

    _safe_notify(
        title=title,
        message=f"{customer_name} (ID: {customer_id}) Margin {severity} to "
                f"{margin:.1f}%! (Billed: {billed_min:.1f} min)",
        timeout=10,
    )
    rearm_thresh = g.margin_critical
    thresh_info = f" (escalation < {rearm_thresh}%)" if escalated else ""
    logging.warning(
        f"MARGIN ALERT for {customer_name} (ID: {customer_id}): "
        f"Margin {margin:.1f}% < {g.margin_below}%, "
        f"Billed {billed_min:.1f} > {g.billed_above}{thresh_info}"
    )
    send_webhook(g, title,
                 f"{customer_name} (ID: {customer_id}) Margin: {margin:.1f}% "
                 f"(Billed: {billed_min:.1f} min)")

    if g.audio:
        play_siren(
            _siren_manager,
            lambda: _siren_manager.play_alternating(g),
            customer_name,
            customer_id,
            "MARGIN" if not escalated else "MARGIN ESCALATION",
        )


def trigger_recovery_alert(
    customer_id: str,
    recovered_value: float,
    customer_name: str,
    settings: Settings,
    alert_type: str,
) -> None:
    g = settings.global_
    if alert_type == "balance":
        title = "BALANCE RECOVERED"
        msg = f"{customer_name} (ID: {customer_id}) balance recovered to {recovered_value:.4f}"
    else:
        title = "MARGIN RECOVERED"
        msg = f"{customer_name} (ID: {customer_id}) margin recovered to {recovered_value:.1f}%"

    _safe_notify(
        title=title,
        message=msg,
        timeout=10,
    )
    logging.info(msg)
    send_webhook(g, title, msg)

    _log_alert_event(customer_id, alert_type, "recovered", recovered_value, None)


def trigger_test_alert(settings: Settings) -> bool:
    g = settings.global_
    title = "TEST ALERT"
    msg = "This is a test alert from InstacallMonitor. Sirens and webhooks are working correctly."

    _safe_notify(
        title=title,
        message=msg,
        timeout=5,
    )
    logging.info("Test alert triggered.")
    send_webhook(g, title, msg)

    if g.audio:
        play_siren(
            _siren_manager,
            lambda: _siren_manager.play_rising_falling(g),
            "Test",
            "TEST",
            "TEST",
        )

    return True
