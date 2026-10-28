from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import persistence
from persistence import (
    BalanceRecord,
    MarginRecord,
    get_balance_history,
    get_margin_history,
    insert_balance,
    insert_margin,
)


class TestPersistence:
    def test_init_db(self, temp_db: str) -> None:
        assert Path(temp_db).exists()

    def test_insert_balance(self, temp_db: str) -> None:
        record = BalanceRecord(
            customer_id="1",
            customer_name="TestCo",
            balance=-100.0,
            credit_limit=500.0,
            remaining=400.0,
        )
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
                "INSERT INTO balance_history "
                "(customer_id, customer_name, balance, credit_limit, remaining, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("99", "OldCo", -100.0, 500.0, 400.0, old_ts),
            )
            conn.commit()

        insert_balance(
            BalanceRecord(
                customer_id="1",
                customer_name="TestCo",
                balance=0.0,
                credit_limit=0.0,
                remaining=0.0,
            )
        )

        deleted = purge_old_records(30)
        assert deleted >= 1

        rows_old = get_balance_history(customer_id="99", hours=24)
        assert len(rows_old) == 0

        rows_recent = get_balance_history(customer_id="1", hours=24)
        assert len(rows_recent) == 1

    def test_purge_zero_skips(self, temp_db: str) -> None:
        from persistence import purge_old_records

        insert_balance(
            BalanceRecord(
                customer_id="1",
                customer_name="TestCo",
                balance=0.0,
                credit_limit=0.0,
                remaining=0.0,
            )
        )
        deleted = purge_old_records(0)
        assert deleted == 0

    def test_alert_state(self, temp_db: str) -> None:
        from persistence import get_alert_state, set_alert_state

        state, count, direction = get_alert_state("18", "balance")
        assert state == 0
        assert count == 0
        assert direction == ""

        set_alert_state("18", "balance", 1, count=1, direction="low")
        state, count, direction = get_alert_state("18", "balance")
        assert state == 1
        assert count == 1
        assert direction == "low"

        set_alert_state("18", "balance", 0, count=0, direction="")
        state, count, direction = get_alert_state("18", "balance")
        assert state == 0
        assert count == 0
        assert direction == ""

    def test_csv_export(self, temp_db: str) -> None:
        from persistence import export_balance_csv, export_margin_csv

        insert_balance(
            BalanceRecord(
                customer_id="1",
                customer_name="TestCo",
                balance=-100.0,
                credit_limit=500.0,
                remaining=400.0,
            )
        )
        insert_margin(MarginRecord(customer_id="1", customer_name="TestCo", margin=45.0, billed_min=100.0))

        bf = export_balance_csv(hours=24)
        mf = export_margin_csv(hours=24)
        assert os.path.exists(bf)
        assert os.path.exists(mf)
        os.unlink(bf)
        os.unlink(mf)

    def test_history_hours_filter_uses_local_time(self, temp_db: str) -> None:
        recent = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        old = (datetime.now() - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
        with persistence._get_connection() as conn:
            conn.execute(
                "INSERT INTO balance_history "
                "(customer_id, customer_name, balance, credit_limit, remaining, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("77", "RecentCo", 1.0, 0.0, 1.0, recent),
            )
            conn.execute(
                "INSERT INTO balance_history "
                "(customer_id, customer_name, balance, credit_limit, remaining, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("78", "OldCo", 2.0, 0.0, 2.0, old),
            )
            conn.commit()

        ids = {r["customer_id"] for r in get_balance_history(hours=2)}
        assert "77" in ids
        assert "78" not in ids

    def test_get_alert_states(self, temp_db: str) -> None:
        from persistence import get_alert_states, set_alert_state

        set_alert_state("18", "balance", 1, count=2)
        set_alert_state("18", "margin", 1, count=1, direction="low")

        by_key = {(r["customer_id"], r["alert_type"]): r for r in get_alert_states()}
        assert by_key[("18", "balance")]["state"] == 1
        assert by_key[("18", "balance")]["alert_count"] == 2
        assert by_key[("18", "margin")]["direction"] == "low"

    def test_alert_state_direction_column_on_fresh_db(self, temp_db: str) -> None:
        with persistence._get_connection() as conn:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(alert_state)").fetchall()]
        assert "direction" in cols

    def test_busy_timeout_is_set(self, temp_db: str) -> None:
        with persistence._get_connection() as conn:
            timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout == 5000

    def test_empty_csv_export_still_has_header(self, temp_db: str) -> None:
        from persistence import export_balance_csv

        out = export_balance_csv(filename=str(Path(temp_db).with_name("empty.csv")), hours=24)
        first_line = Path(out).read_text(encoding="utf-8").splitlines()[0]
        assert first_line.startswith("id,customer_id,customer_name")
