"""Unit tests for repository SQL helpers (no live database required)."""

from __future__ import annotations

from app.db.repository import _split_sql


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
