from __future__ import annotations

import csv
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator, Optional

import config


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class BalanceRecord:
    customer_id: str
    customer_name: str
    balance: Optional[float]
    credit_limit: Optional[float]
    remaining: Optional[float]
    recorded_at: str = field(default_factory=_now)

    def to_tuple(self) -> tuple[str, str, Optional[float], Optional[float], Optional[float], str]:
        return (self.customer_id, self.customer_name, self.balance, self.credit_limit, self.remaining, self.recorded_at)


@dataclass
class MarginRecord:
    customer_id: str
    customer_name: str
    margin: Optional[float]
    billed_min: Optional[float]
    recorded_at: str = field(default_factory=_now)

    def to_tuple(self) -> tuple[str, str, Optional[float], Optional[float], str]:
        return (self.customer_id, self.customer_name, self.margin, self.billed_min, self.recorded_at)


CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS balance_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    balance REAL,
    credit_limit REAL,
    remaining REAL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS margin_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    margin REAL,
    billed_min REAL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_state (
    customer_id TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    state INTEGER NOT NULL DEFAULT 0,
    alert_count INTEGER NOT NULL DEFAULT 0,
    last_alerted_at TEXT,
    direction TEXT,
    PRIMARY KEY (customer_id, alert_type)
);

CREATE INDEX IF NOT EXISTS idx_balance_customer ON balance_history(customer_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_margin_customer ON margin_history(customer_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_balance_recorded ON balance_history(recorded_at);
CREATE INDEX IF NOT EXISTS idx_margin_recorded ON margin_history(recorded_at);
"""


@contextmanager
def _get_connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(config.DB_FILE)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _get_connection() as conn:
        conn.executescript(CREATE_TABLES_SQL)
        for migration in (
            "ALTER TABLE alert_state ADD COLUMN alert_count INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE alert_state ADD COLUMN direction TEXT",
        ):
            try:
                conn.execute(migration)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise


def insert_balance(record: BalanceRecord) -> None:
    with _get_connection() as conn:
        conn.execute(
            "INSERT INTO balance_history (customer_id, customer_name, balance, credit_limit, remaining, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            record.to_tuple(),
        )
        conn.commit()


def insert_margin(record: MarginRecord) -> None:
    with _get_connection() as conn:
        conn.execute(
            "INSERT INTO margin_history (customer_id, customer_name, margin, billed_min, recorded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            record.to_tuple(),
        )
        conn.commit()


_BALANCE_COLS = ("id", "customer_id", "customer_name", "balance", "credit_limit", "remaining", "recorded_at")
_MARGIN_COLS = ("id", "customer_id", "customer_name", "margin", "billed_min", "recorded_at")


def _history(
    table: str, cols: tuple[str, ...], customer_id: Optional[str], hours: int, limit: int
) -> list[dict[str, object]]:
    select = ", ".join(cols)
    with _get_connection() as conn:
        if customer_id:
            rows = conn.execute(
                f"SELECT {select} FROM {table} WHERE customer_id = ? "
                "AND recorded_at > datetime('now', 'localtime', ?) "
                "ORDER BY recorded_at DESC LIMIT ?",
                (customer_id, f"-{hours} hours", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {select} FROM {table} WHERE recorded_at > datetime('now', 'localtime', ?) "
                "ORDER BY recorded_at DESC LIMIT ?",
                (f"-{hours} hours", limit),
            ).fetchall()
    return [dict(zip(cols, row)) for row in rows]


def get_balance_history(
    customer_id: Optional[str] = None, hours: int = 24, limit: int = 500
) -> list[dict[str, object]]:
    return _history("balance_history", _BALANCE_COLS, customer_id, hours, limit)


def get_margin_history(customer_id: Optional[str] = None, hours: int = 24, limit: int = 500) -> list[dict[str, object]]:
    return _history("margin_history", _MARGIN_COLS, customer_id, hours, limit)


def purge_old_records(retention_days: int) -> int:
    if retention_days <= 0:
        return 0
    with _get_connection() as conn:
        total = 0
        for table in ("balance_history", "margin_history"):
            c = conn.execute(
                f"DELETE FROM {table} WHERE recorded_at < datetime('now', 'localtime', ?)",
                (f"-{retention_days} days",),
            ).rowcount
            total += c
        conn.commit()
    return total


def get_alert_state(customer_id: str, alert_type: str) -> tuple[int, int, str]:
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT state, alert_count, coalesce(direction, '') "
            "FROM alert_state WHERE customer_id = ? AND alert_type = ?",
            (customer_id, alert_type),
        ).fetchone()
        return (row[0], row[1], row[2] or "") if row else (0, 0, "")


def set_alert_state(customer_id: str, alert_type: str, state: int, count: int = 0, direction: str = "") -> None:
    ts = _now() if state != 0 else None
    with _get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO alert_state "
            "(customer_id, alert_type, state, alert_count, last_alerted_at, direction) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (customer_id, alert_type, state, count, ts, direction if state != 0 else None),
        )
        conn.commit()


def get_alert_states() -> list[dict[str, object]]:
    with _get_connection() as conn:
        rows = conn.execute(
            "SELECT customer_id, alert_type, state, alert_count, last_alerted_at, coalesce(direction, '') "
            "FROM alert_state ORDER BY customer_id, alert_type"
        ).fetchall()
    cols = ["customer_id", "alert_type", "state", "alert_count", "last_alerted_at", "direction"]
    return [dict(zip(cols, row)) for row in rows]


def _write_csv(filename: Optional[str], prefix: str, fieldnames: list[str], rows: list[dict[str, object]]) -> str:
    if filename is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{prefix}_export_{ts}.csv"
    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return filename


def export_balance_csv(filename: Optional[str] = None, customer_id: Optional[str] = None, hours: int = 168) -> str:
    rows = get_balance_history(customer_id=customer_id, hours=hours, limit=50000)
    return _write_csv(filename, "balance", list(_BALANCE_COLS), rows)


def export_margin_csv(filename: Optional[str] = None, customer_id: Optional[str] = None, hours: int = 168) -> str:
    rows = get_margin_history(customer_id=customer_id, hours=hours, limit=50000)
    return _write_csv(filename, "margin", list(_MARGIN_COLS), rows)
