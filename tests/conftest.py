from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import config  # noqa: E402
import persistence  # noqa: E402


@pytest.fixture
def temp_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    """Point the DB at a fresh temp file and initialise the schema."""
    path = tmp_path / "test.db"
    monkeypatch.setattr(config, "DB_FILE", str(path))
    persistence.init_db()
    return str(path)
