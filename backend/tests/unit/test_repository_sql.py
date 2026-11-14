"""Unit tests for repository SQL helpers (no live database required)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.db.repository import TigerRepository, TigerSchemaError, _split_sql


def test_schema_sql_splits_and_includes_hypertable() -> None:
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[2] / "app" / "db" / "schema.sql").read_text(
        encoding="utf-8"
    )
    parts = _split_sql(sql)
    assert any("CREATE TABLE IF NOT EXISTS accounts" in p for p in parts)
    assert any("CREATE TABLE IF NOT EXISTS transactions" in p for p in parts)
    assert any("create_hypertable" in p.lower() for p in parts)
    assert any("PRIMARY KEY (transaction_id, posted_at)" in p for p in parts)


def test_ensure_schema_requires_hypertable_when_requested() -> None:
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    # Table DDL succeeds; hypertable check returns no row.
    cursor.fetchone.return_value = None

    repo = TigerRepository(conn)
    with pytest.raises(TigerSchemaError, match="hypertable"):
        repo.ensure_schema(require_hypertable=True)
