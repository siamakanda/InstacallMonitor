from __future__ import annotations

import os
import re
import sys
from typing import Optional

if sys.platform == "win32":
    os.system("")

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _visible_len(text: str) -> int:
    return len(_ANSI_RE.sub("", text))


def _color(code: int, text: str) -> str:
    clean = _ANSI_RE.sub("", text)
    return f"\033[{code}m{clean}\033[0m"


def dim(text: str) -> str:
    return _color(2, text)


def bold(text: str) -> str:
    return _color(1, text)


def red(text: str) -> str:
    return _color(31, text)


def green(text: str) -> str:
    return _color(32, text)


def yellow(text: str) -> str:
    return _color(33, text)


def cyan(text: str) -> str:
    return _color(36, text)


def print_balance_line(
    customer_name: str,
    cid: str,
    balance: Optional[float],
    credit_limit: Optional[float],
    error: Optional[str] = None,
    ts: str = "",
) -> None:
    tag = green("[B]") if balance is not None and balance >= 0 else (red("[B]") if balance is not None else dim("[B]"))
    ts_part = f"[{ts}] " if ts else ""

    if balance is not None:
        remaining: Optional[float] = credit_limit + balance if credit_limit is not None else None
        bal_str = red(f"{balance:.4f}") if balance < 0 else green(f"{balance:.4f}")
        credit_str = f"  Credit: {credit_limit:.2f}" if credit_limit is not None else ""
        remaining_str = f"  Remaining: {remaining:.2f}" if remaining is not None else ""
        print(f"  {tag} {ts_part}{customer_name[:25]:25s}  Balance {bal_str}{credit_str}{remaining_str}")
    else:
        print(f"  {tag} {ts_part}ID {cid}  {red(f'FETCH FAILED ({error})')}")


def print_summary_line(
    data: dict[str, object],
    cid: str,
    monitored: bool = False,
    ts: str = "",
) -> None:
    tag = yellow("[M]") if monitored else dim("[M]")
    ts_part = f"[{ts}] " if ts else ""
    name = str(data.get("name", "N/A"))[:30]
    margin = data.get("margin")
    billed_min = data.get("billed_min")
    mon = yellow(" [MONITORED]") if monitored else ""

    if isinstance(margin, (int, float)):
        if margin < 30:
            m_str = red(f"{margin:.1f}%")
        elif margin < 50:
            m_str = yellow(f"{margin:.1f}%")
        else:
            m_str = green(f"{margin:.1f}%")
    else:
        m_str = dim("N/A")

    if isinstance(billed_min, (int, float)):
        b_str = f"{billed_min:.1f} min"
    else:
        b_str = dim("N/A")

    print(f"  {tag} {ts_part}{name:30s}  Margin {m_str}  |  Billed {b_str}{mon}")
