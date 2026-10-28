from __future__ import annotations

import logging
import traceback
from datetime import datetime
from typing import Optional

from alerts import SirenManager, check_balance_alert, check_margin_alert
from display import print_balance_line, print_summary_line
from persistence import BalanceRecord, MarginRecord, insert_balance, insert_margin
from runtime import Runtime
from scrapers import BalanceResult
from settings_models import Settings
from webapp import MonitorState


def process_balances(
    results: dict[str, BalanceResult],
    mgr: SirenManager,
    settings: Settings,
    state: MonitorState,
    runtime: Runtime,
    last_balance: dict[str, tuple[float, Optional[float]]],
) -> None:
    """Persist balance results, evaluate balance alerts and update dashboard state."""
    for cid in settings.customer_ids:
        name, balance, credit_limit, error = results.get(cid, (None, None, None, "no result"))
        ts = datetime.now().strftime("%H:%M:%S")
        print_balance_line(name or "N/A", cid, balance, credit_limit, error, ts=ts)

        if error:
            runtime.note_error(f"{cid}: {error}")

        if balance is not None:
            prev = last_balance.get(cid)
            curr = (balance, credit_limit)
            if prev != curr:
                last_balance[cid] = curr
                remaining = credit_limit + balance if credit_limit is not None else None
                try:
                    insert_balance(
                        BalanceRecord(
                            customer_id=cid,
                            customer_name=name or "N/A",
                            balance=balance,
                            credit_limit=credit_limit,
                            remaining=remaining,
                        )
                    )
                except Exception as e:
                    logging.error(f"DB insert balance failed for {cid}: {e}")
                    runtime.note_error(str(e))

            try:
                check_balance_alert(mgr, cid, balance, name or "N/A", settings)
            except Exception as e:
                logging.error(f"Alert check failed for {cid}: {e}\n{traceback.format_exc()}")
                runtime.note_error(str(e))

        balance_below = settings.get_watch(cid).resolve_balance_below()
        state.update_customer(
            cid,
            name=name or "N/A",
            monitored=True,
            balance=balance,
            credit_limit=credit_limit,
            remaining=(credit_limit + balance) if (balance is not None and credit_limit is not None) else None,
            balance_below=balance_below,
            balance_state=1 if (balance is not None and balance < balance_below) else 0,
            error=error,
        )


def process_summary(
    summary: dict[str, dict[str, object]],
    mgr: SirenManager,
    settings: Settings,
    state: MonitorState,
    runtime: Runtime,
    last_margin: dict[str, tuple[Optional[float], Optional[float]]],
) -> None:
    """Persist margin data, evaluate margin alerts and update dashboard state."""
    g = settings.global_
    for cid, data in summary.items():
        monitored = cid in settings.customer_ids
        ts = datetime.now().strftime("%H:%M:%S")
        print_summary_line(
            data, cid, monitored=monitored, ts=ts, margin_below=g.margin_below, margin_above=g.margin_above
        )

        margin = data.get("margin")
        billed_min = data.get("billed_min")

        m_prev = last_margin.get(cid)
        m_curr = (
            float(margin) if isinstance(margin, (int, float)) else None,
            float(billed_min) if isinstance(billed_min, (int, float)) else None,
        )
        if m_prev != m_curr:
            last_margin[cid] = m_curr
            try:
                insert_margin(
                    MarginRecord(
                        customer_id=cid,
                        customer_name=str(data.get("name", "N/A")),
                        margin=float(margin) if isinstance(margin, (int, float)) else None,
                        billed_min=float(billed_min) if isinstance(billed_min, (int, float)) else None,
                    )
                )
            except Exception as e:
                logging.error(f"DB insert margin failed for {cid}: {e}")
                runtime.note_error(str(e))

        if isinstance(margin, (int, float)) and isinstance(billed_min, (int, float)):
            try:
                check_margin_alert(
                    mgr,
                    cid,
                    float(margin),
                    float(billed_min),
                    str(data.get("name", "N/A")),
                    settings,
                    monitored=monitored,
                )
            except Exception as e:
                logging.error(f"Margin alert check failed for {cid}: {e}")
                runtime.note_error(str(e))

        w = settings.get_watch(cid)
        low = w.resolve_margin_below(g.margin_below)
        high = w.resolve_margin_above(g.margin_above)
        billed_threshold = w.resolve_billed_above(g.billed_above)
        margin_val = float(margin) if isinstance(margin, (int, float)) else None
        billed_val = float(billed_min) if isinstance(billed_min, (int, float)) else None
        m_state = 0
        m_dir = ""
        if monitored and margin_val is not None and billed_val is not None and billed_val > billed_threshold:
            if margin_val < low:
                m_state, m_dir = 1, "low"
            elif margin_val > high:
                m_state, m_dir = 1, "high"
        state.update_customer(
            cid,
            name=str(data.get("name", "N/A")),
            monitored=monitored,
            margin=margin_val,
            billed_min=billed_val,
            margin_below=low,
            margin_above=high,
            margin_deadband=w.resolve_margin_deadband(g.margin_deadband),
            billed_above=billed_threshold,
            margin_state=m_state,
            margin_direction=m_dir,
        )
