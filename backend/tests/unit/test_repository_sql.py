"""Unit tests for repository SQL helpers (no live database required)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.db.repository import TigerRepository, TigerSchemaError, _split_sql


def test_cash_sql_excludes_credit_card_purchases() -> None:
    """Card swipes must not inflate SQL cash outflow, history, or category totals."""
    from app.db.repository import (
        _CASHFLOW_WINDOW,
        _DAILY_SPENDING_SQL,
        _DISCRETIONARY_DAILY_SQL,
        _SPENDING_BY_CATEGORY,
    )

    for sql in (
        _CASHFLOW_WINDOW,
        _DISCRETIONARY_DAILY_SQL,
        _DAILY_SPENDING_SQL,
        _SPENDING_BY_CATEGORY,
    ):
        assert "position('credit' in lower(a.account_type))" in sql


def test_account_upsert_cannot_reassign_customer() -> None:
    from app.db.repository import _UPSERT_ACCOUNT

    assert "WHERE accounts.customer_id = EXCLUDED.customer_id" in _UPSERT_ACCOUNT
    update_clause = _UPSERT_ACCOUNT.split("DO UPDATE SET", 1)[1].split("WHERE", 1)[0]
    assert "customer_id" not in update_clause


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
    function = next(
        part
        for part in parts
        if "CREATE OR REPLACE FUNCTION accounts_reject_customer_reassign" in part
    )
    assert "RAISE EXCEPTION" in function
    assert "CREATE TRIGGER" not in function
    assert any("CREATE TRIGGER accounts_customer_owner" in part for part in parts)


def test_split_sql_keeps_dollar_quoted_body_together() -> None:
    sql = (
        "CREATE FUNCTION f() RETURNS void AS $body$\n"
        "BEGIN\n"
        "  PERFORM 1;\n"
        "END;\n"
        "$body$;\n"
        "SELECT 1;"
    )
    parts = _split_sql(sql)
    assert len(parts) == 2
    assert "PERFORM 1;" in parts[0]
    assert parts[1] == "SELECT 1"


def test_ensure_schema_requires_hypertable_when_requested() -> None:
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    # Table DDL succeeds; hypertable check returns no row.
    cursor.fetchone.return_value = None

    repo = TigerRepository(conn)
    with pytest.raises(TigerSchemaError, match="hypertable"):
        repo.ensure_schema(require_hypertable=True)
