from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
import persistence
from persistence import (
    BalanceRecord,
    MarginRecord,
    get_balance_history,
    get_margin_history,
    init_db,
    insert_balance,
    insert_margin,
)


@pytest.fixture
def temp_db(monkeypatch: pytest.MonkeyPatch) -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr(config, "DB_FILE", path)
    init_db()
    yield path
    try:
        os.unlink(path)
    except PermissionError:
        pass


class TestPersistence:
    def test_init_db(self, temp_db: str) -> None:
        assert Path(temp_db).exists()

    def test_insert_balance(self, temp_db: str) -> None:
        record = BalanceRecord(customer_id="1", customer_name="TestCo", balance=-100.0, credit_limit=500.0, remaining=400.0)
        insert_balance(record)
        rows = get_balance_history(customer_id="1", hours=24)
        assert len(rows) >= 1

    def test_insert_margin(self, temp_db: str) -> None:
        record = MarginRecord(customer_id="1", customer_name="TestCo", margin=45.0, billed_min=100.0)
        insert_margin(record)
        rows = get_margin_history(customer_id="1", hours=24)
        assert len(rows) >= 1

    def test_get_balance_history_all(self, temp_db: str) -> None:
        insert_balance(BalanceRecord(customer_id="1", customer_name="A", balance=1.0, credit_limit=0.0, remaining=1.0))
        insert_balance(BalanceRecord(customer_id="2", customer_name="B", balance=2.0, credit_limit=0.0, remaining=2.0))
        rows = get_balance_history(hours=24)
        assert len(rows) >= 2

    def test_get_margin_history_filtered(self, temp_db: str) -> None:
        insert_margin(MarginRecord(customer_id="1", customer_name="A", margin=10.0, billed_min=50.0))
        insert_margin(MarginRecord(customer_id="2", customer_name="B", margin=20.0, billed_min=100.0))
        rows = get_margin_history(customer_id="1", hours=24)
        assert len(rows) >= 1

    def test_purge_old_records(self, temp_db: str) -> None:
        from persistence import purge_old_records

        old_ts = (datetime.now() - timedelta(days=60)).isoformat()
        with persistence._get_connection() as conn:
            conn.execute(
                "INSERT INTO balance_history (customer_id, customer_name, balance, credit_limit, remaining, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("99", "OldCo", -100.0, 500.0, 400.0, old_ts),
            )
            conn.commit()

        insert_balance(BalanceRecord(customer_id="1", customer_name="TestCo", balance=0.0, credit_limit=0.0, remaining=0.0))

        deleted = purge_old_records(30)
        assert deleted >= 1

        rows_old = get_balance_history(customer_id="99", hours=24)
        assert len(rows_old) == 0

        rows_recent = get_balance_history(customer_id="1", hours=24)
        assert len(rows_recent) == 1

    def test_purge_zero_skips(self, temp_db: str) -> None:
        from persistence import purge_old_records

        insert_balance(BalanceRecord(customer_id="1", customer_name="TestCo", balance=0.0, credit_limit=0.0, remaining=0.0))
        deleted = purge_old_records(0)
        assert deleted == 0
