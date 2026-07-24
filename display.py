from __future__ import annotations

import os
import re
import sys
from typing import Any, Callable, Optional

if sys.platform == "win32":
    os.system("")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_ANSI_RE = re.compile(r'\033\[[0-9;]*m')

_HAS_UNICODE = False
try:
    "\u2550".encode(sys.stdout.encoding or "ascii")
    _HAS_UNICODE = True
except (UnicodeEncodeError, UnicodeDecodeError):
    pass

if _HAS_UNICODE:
    _H = "\u2550"
    _V = "\u2551"
    _TL = "\u2554"
    _TR = "\u2557"
    _BL = "\u255a"
    _BR = "\u255d"
    _ML = "\u2560"
    _MR = "\u2563"
    _SEP = "\u2500"
    _X = "\u256c"
else:
    _H = "="
    _V = "|"
    _TL = "+"
    _TR = "+"
    _BL = "+"
    _BR = "+"
    _ML = "+"
    _MR = "+"
    _SEP = "-"
    _X = "+"

W = 64


def _visible_len(text: str) -> int:
    return len(_ANSI_RE.sub('', text))


def _color(code: int, text: str) -> str:
    clean = _ANSI_RE.sub('', text)
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


def white(text: str) -> str:
    return _color(97, text)


# ── formatting helpers ───────────────────────────────────────────


def fmt_margin(margin: Optional[float]) -> str:
    return f"{margin:.1f}%" if margin is not None else "-"


def fmt_billed_min(billed_min: Optional[float]) -> str:
    return f"{billed_min:.1f}" if billed_min is not None else "-"


def fmt_balance(balance: Optional[float]) -> str:
    return f"{balance:.4f}" if balance is not None else "-"


def fmt_pct(val: float) -> str:
    return f"{val:.0f}%" if val == int(val) else f"{val:.1f}%"


def badge_ok() -> str:
    return green(" OK ")


def badge_warn() -> str:
    return yellow(" ⚠ ")


def badge_crit() -> str:
    return red(" !! ")


def badge_on() -> str:
    return green(" ON")


def badge_off() -> str:
    return dim("OFF")


# ── box helpers ───────────────────────────────────────────────────


def box_top(title: str, width: int = W) -> None:
    inner = width - 2
    title_vis = _visible_len(title)
    print(dim(f"  {_TL}{_H * inner}{_TR}"))
    pad = (inner - title_vis) // 2
    print(dim(f"  {_V}") + " " * pad + bold(title) + " " * (inner - pad - title_vis) + dim(f"{_V}"))


def box_bottom(width: int = W) -> None:
    print(dim(f"  {_BL}{_H * (width - 2)}{_BR}"))


def box_sep(width: int = W) -> None:
    print(dim(f"  {_ML}{_SEP * (width - 2)}{_MR}"))


def box_row(label: str, value: str, width: int = W) -> None:
    gap = width - 2 - _visible_len(label) - _visible_len(value)
    if gap < 1:
        gap = 1
    print(dim(f"  {_V} ") + label + " " * gap + value + dim(f" {_V}"))


def box_empty(width: int = W) -> None:
    print(dim(f"  {_V}{' ' * (width - 2)}{_V}"))


# ── table helpers ─────────────────────────────────────────────────


def table_header(cols: list[tuple[str, int]], width: int = W) -> None:
    header = dim(f"  {_V} ")
    for i, (name, w) in enumerate(cols):
        header += bold(name[:w].ljust(w))
        if i < len(cols) - 1:
            header += dim(" │ ")
    header += dim(f" {_V}")
    print(header)


def table_row(cells: list[str], widths: list[int], width: int = W) -> None:
    row = dim(f"  {_V} ")
    for i, (cell, w) in enumerate(zip(cells, widths)):
        row += cell.ljust(w - max(0, _visible_len(cell) - len(cell)))
        if i < len(widths) - 1:
            row += dim(" │ ")
    row += dim(f" {_V}")
    print(row)


def table_row_direct(row_str: str, width: int = W) -> None:
    padding = width - 2 - _visible_len(row_str)
    if padding < 0:
        padding = 0
    print(dim(f"  {_V} ") + row_str + " " * padding + dim(f" {_V}"))


# ── output lines (backward-compat) ────────────────────────────────


def print_balance_line(
    customer_name: str,
    cid: str,
    balance: Optional[float],
    credit_limit: Optional[float],
    error: Optional[str] = None,
    prefix: str = "[B]",
) -> None:
    tag = dim(prefix)
    if balance is not None:
        remaining: Optional[float] = None
        if credit_limit is not None:
            remaining = credit_limit + balance
        bal_str = red(fmt_balance(balance)) if balance < 0 else green(fmt_balance(balance))
        bal_pad = " " * max(0, 10 - _visible_len(bal_str))
        credit_str = f"  Credit: {credit_limit:.2f}" if credit_limit is not None else ""
        remaining_str = f"  Remaining: {remaining:.2f}" if remaining is not None else ""
        print(f"  {tag} {customer_name[:25]:25s}  Balance {bal_str}{bal_pad}{credit_str}{remaining_str}")
    else:
        print(f"  {tag} ID {cid}  {red(f'FETCH FAILED ({error})')}")


def print_summary_line(
    data: dict[str, object],
    cid: str,
    monitored: bool = False,
    prefix: str = "[M]",
) -> None:
    tag = dim(prefix)
    name = str(data.get("name", "N/A"))[:30]
    margin = data.get("margin")
    billed_min = data.get("billed_min")
    mon = yellow(" ▲") if monitored else ""

    if isinstance(margin, float):
        if margin < 30:
            m_str = red(f"{margin:.1f}%")
        elif margin < 50:
            m_str = yellow(f"{margin:.1f}%")
        else:
            m_str = green(f"{margin:.1f}%")
    else:
        m_str = dim("N/A")
    m_pad = " " * max(0, 10 - _visible_len(m_str))

    if isinstance(billed_min, float):
        b_str = f"{billed_min:.1f} min"
    else:
        b_str = dim("N/A")
    b_pad = " " * max(0, 10 - _visible_len(b_str))

    print(f"  {tag} {name:30s}  Margin {m_str}{m_pad}  |  Billed {b_str}{b_pad}{mon}")


ColFormatter = Callable[[Any], str]


def _pad_value(value: str, width: int, align: str = "left") -> str:
    visible = _visible_len(value)
    if visible > width:
        value = value[: width - 3] + ".."
        visible = _visible_len(value)
    if align == "right":
        return " " * (width - visible) + value
    return value + " " * (width - visible)


def section_header(title: str, width: int = 80) -> None:
    print()
    print(bold(f" {title}"))
    print(dim(" " + "\u2500" * (width - 2)))


def render_minimal_table(
    headers: list[str],
    rows: list[list[Any]],
    col_widths: Optional[list[int]] = None,
    formatters: Optional[dict[str, ColFormatter]] = None,
) -> None:
    if not rows:
        print(dim("  No data"))
        return

    ncols = len(headers)
    if col_widths is None:
        col_widths = [12] * ncols
    if formatters is None:
        formatters = {}

    header_parts: list[str] = []
    for i, h in enumerate(headers):
        header_parts.append(_pad_value(bold(h), col_widths[i]))
    print(" " + "  ".join(header_parts))

    sep_parts: list[str] = []
    for i in range(ncols):
        sep_parts.append(dim("\u2500" * col_widths[i]))
    print(" " + dim("  ").join(sep_parts))

    for row in rows:
        parts: list[str] = []
        for i in range(ncols):
            val = row[i] if i < len(row) else ""
            fmt = formatters.get(headers[i])
            if fmt:
                val = fmt(val)
            val_str = str(val) if not isinstance(val, str) else val
            parts.append(_pad_value(val_str, col_widths[i]))
        print(" " + "  ".join(parts))
