from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import config


def _now() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


@dataclass
class BalanceRecord:
    customer_id: str
    customer_name: str
    balance: Optional[float]
    credit_limit: Optional[float]
    remaining: Optional[float]
    recorded_at: str = field(default_factory=_now, init=False)

    def to_tuple(self) -> tuple[str, str, Optional[float], Optional[float], Optional[float], str]:
        return (self.customer_id, self.customer_name, self.balance, self.credit_limit, self.remaining, self.recorded_at)


@dataclass
class MarginRecord:
    customer_id: str
    customer_name: str
    margin: Optional[float]
    billed_min: Optional[float]
    recorded_at: str = field(default_factory=_now, init=False)

    def to_tuple(self) -> tuple[str, str, Optional[float], Optional[float], str]:
        return (self.customer_id, self.customer_name, self.margin, self.billed_min, self.recorded_at)


@dataclass
class AlertState:
    customer_id: str
    alert_type: str
    state: int = 0
    last_alerted_at: Optional[str] = None

    def to_tuple(self) -> tuple[str, str, int, Optional[str]]:
        return (self.customer_id, self.alert_type, self.state, self.last_alerted_at)


@dataclass
class AlertEvent:
    customer_id: str
    alert_type: str
    severity: str
    value: Optional[float]
    threshold: Optional[float]
    billed_min: Optional[float] = None
    recorded_at: str = field(default_factory=_now, init=False)

    def to_tuple(self) -> tuple[str, str, str, Optional[float], Optional[float], Optional[float], str]:
        return (self.customer_id, self.alert_type, self.severity, self.value, self.threshold, self.billed_min, self.recorded_at)


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
    last_alerted_at TEXT,
    PRIMARY KEY (customer_id, alert_type)
);

CREATE TABLE IF NOT EXISTS alert_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    value REAL,
    threshold REAL,
    billed_min REAL,
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_balance_customer ON balance_history(customer_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_margin_customer ON margin_history(customer_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_alert_state_lookup ON alert_state(customer_id, alert_type);
CREATE INDEX IF NOT EXISTS idx_alert_history_customer ON alert_history(customer_id, recorded_at);
"""


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_FILE)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _get_connection() as conn:
        conn.executescript(CREATE_TABLES_SQL)


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


def get_balance_history(
    customer_id: Optional[str] = None, hours: int = 24, limit: int = 500
) -> list[dict[str, object]]:
    cutoff = datetime.now().isoformat()
    with _get_connection() as conn:
        if customer_id:
            rows = conn.execute(
                "SELECT * FROM balance_history WHERE customer_id = ? AND recorded_at > datetime(?, ?) "
                "ORDER BY recorded_at DESC LIMIT ?",
                (customer_id, cutoff, f"-{hours} hours", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM balance_history WHERE recorded_at > datetime(?, ?) "
                "ORDER BY recorded_at DESC LIMIT ?",
                (cutoff, f"-{hours} hours", limit),
            ).fetchall()
    cols = ["id", "customer_id", "customer_name", "balance", "credit_limit", "remaining", "recorded_at"]
    return [dict(zip(cols, row)) for row in rows]


def get_margin_history(
    customer_id: Optional[str] = None, hours: int = 24, limit: int = 500
) -> list[dict[str, object]]:
    cutoff = datetime.now().isoformat()
    with _get_connection() as conn:
        if customer_id:
            rows = conn.execute(
                "SELECT * FROM margin_history WHERE customer_id = ? AND recorded_at > datetime(?, ?) "
                "ORDER BY recorded_at DESC LIMIT ?",
                (customer_id, cutoff, f"-{hours} hours", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM margin_history WHERE recorded_at > datetime(?, ?) "
                "ORDER BY recorded_at DESC LIMIT ?",
                (cutoff, f"-{hours} hours", limit),
            ).fetchall()
    cols = ["id", "customer_id", "customer_name", "margin", "billed_min", "recorded_at"]
    return [dict(zip(cols, row)) for row in rows]


def purge_old_records(retention_days: int) -> int:
    if retention_days <= 0:
        return 0
    with _get_connection() as conn:
        total = 0
        for table in ("balance_history", "margin_history", "alert_history"):
            c = conn.execute(
                f"DELETE FROM {table} WHERE recorded_at < datetime('now', 'localtime', ?)",
                (f'-{retention_days} days',),
            ).rowcount
            total += c
        conn.commit()
    return total


def get_alert_state(customer_id: str, alert_type: str) -> AlertState:
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT customer_id, alert_type, state, last_alerted_at FROM alert_state "
            "WHERE customer_id = ? AND alert_type = ?",
            (customer_id, alert_type),
        ).fetchone()
        if row:
            return AlertState(
                customer_id=row[0], alert_type=row[1], state=row[2], last_alerted_at=row[3],
            )
        return AlertState(customer_id=customer_id, alert_type=alert_type)


def set_alert_state(customer_id: str, alert_type: str, state: int, last_alerted_at: Optional[str] = None) -> None:
    if last_alerted_at is None:
        if state == 0:
            existing = get_alert_state(customer_id, alert_type)
            last_alerted_at = existing.last_alerted_at
        else:
            last_alerted_at = _now()
    with _get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO alert_state (customer_id, alert_type, state, last_alerted_at) "
            "VALUES (?, ?, ?, ?)",
            (customer_id, alert_type, state, last_alerted_at),
        )
        conn.commit()


def insert_alert_event(event: AlertEvent) -> None:
    with _get_connection() as conn:
        conn.execute(
            "INSERT INTO alert_history (customer_id, alert_type, severity, value, threshold, billed_min, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            event.to_tuple(),
        )
        conn.commit()


def get_alert_history(
    customer_id: Optional[str] = None, hours: int = 24, limit: int = 100
) -> list[dict[str, object]]:
    cutoff = datetime.now().isoformat()
    with _get_connection() as conn:
        if customer_id:
            rows = conn.execute(
                "SELECT * FROM alert_history WHERE customer_id = ? AND recorded_at > datetime(?, ?) "
                "ORDER BY recorded_at DESC LIMIT ?",
                (customer_id, cutoff, f"-{hours} hours", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM alert_history WHERE recorded_at > datetime(?, ?) "
                "ORDER BY recorded_at DESC LIMIT ?",
                (cutoff, f"-{hours} hours", limit),
            ).fetchall()
    cols = ["id", "customer_id", "alert_type", "severity", "value", "threshold", "billed_min", "recorded_at"]
    return [dict(zip(cols, row)) for row in rows]
