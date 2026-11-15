"""Postgres round-trip for the cash and snapshot SQL added in the quality pass.

Skipped unless ``CENTSABLE_TEST_DATABASE_URL`` points at a throwaway database.
Never point that variable at ``DATABASE_URL`` or any shared TigerData instance.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlparse

import pytest

from app.db.repository import TigerRepository
from app.domain.predictive_engine import sql_bounds_for_half_open_days
from app.models.schemas import Account, Transaction, TransactionKind, TransactionStatus

pytestmark = pytest.mark.skipif(
    not os.environ.get("CENTSABLE_TEST_DATABASE_URL"),
    reason="Optional throwaway Postgres; skipped in the default offline suite.",
)


def _normalize_db_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path or ""
    return f"{parsed.scheme}://{host}{port}{path}"


def _refuse_shared_database(url: str) -> None:
    shared = (os.environ.get("DATABASE_URL") or "").strip()
    if shared and _normalize_db_url(url) == _normalize_db_url(shared):
        pytest.fail(
            "Refusing SQL round-trip against DATABASE_URL. "
            "Point CENTSABLE_TEST_DATABASE_URL at a throwaway database."
        )


def _account(account_id: str, customer_id: str, account_type: str) -> Account:
    return Account(
        account_id=account_id,
        customer_id=customer_id,
        name=account_type,
        account_type=account_type,
        balance=Decimal("100.00"),
    )


def _txn(
    transaction_id: str,
    *,
    customer_id: str = "sql-a",
    account_id: str = "checking-a",
    amount: str = "-4.00",
    category: str = "Dining",
    description: str = "latte",
    posted_at: datetime | None = None,
    kind: TransactionKind = TransactionKind.EXPENSE,
    status: TransactionStatus = TransactionStatus.POSTED,
) -> Transaction:
    return Transaction(
        transaction_id=transaction_id,
        customer_id=customer_id,
        account_id=account_id,
        amount=Decimal(amount),
        kind=kind,
        status=status,
        description=description,
        category=category,
        posted_at=posted_at or datetime(2026, 11, 10, 15, tzinfo=timezone.utc),
    )


@pytest.fixture
def repo():
    import psycopg

    url = os.environ["CENTSABLE_TEST_DATABASE_URL"]
    _refuse_shared_database(url)
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS transactions")
            cur.execute("DROP TABLE IF EXISTS accounts")
            cur.execute(
                """
                CREATE TABLE accounts (
                    account_id TEXT PRIMARY KEY,
                    customer_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    account_type TEXT NOT NULL,
                    balance NUMERIC(18, 2) NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'USD',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE transactions (
                    transaction_id TEXT NOT NULL,
                    customer_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    amount NUMERIC(18, 2) NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    description TEXT NOT NULL,
                    category TEXT NOT NULL,
                    posted_at TIMESTAMPTZ NOT NULL,
                    counterparty_account_id TEXT NULL,
                    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (transaction_id, posted_at)
                )
                """
            )
        conn.commit()
        yield TigerRepository(conn)


def _seed(repo: TigerRepository) -> None:
    day = datetime(2026, 11, 10, 15, tzinfo=timezone.utc)
    repo.commit_customer_sync(
        customer_id="sql-a",
        accounts=[
            _account("checking-a", "sql-a", "Checking"),
            _account("card-a", "sql-a", "Credit Card"),
        ],
        transactions=[
            _txn("coffee", amount="-4.00", category="Dining", description="latte", posted_at=day),
            _txn(
                "groceries",
                amount="-10.00",
                category="Groceries",
                description="store",
                posted_at=day + timedelta(hours=1),
            ),
            _txn(
                "phone",
                amount="-40.00",
                category="Utilities",
                description="Phone bill",
                posted_at=day + timedelta(hours=2),
            ),
            _txn(
                "swipe",
                account_id="card-a",
                amount="-99.00",
                category="Dining",
                description="card coffee",
                posted_at=day + timedelta(hours=3),
            ),
            _txn(
                "paycheck",
                amount="20.00",
                category="Paycheck",
                description="payroll",
                kind=TransactionKind.INCOME,
                posted_at=day + timedelta(hours=4),
            ),
        ],
        reconcile=False,
    )
    repo.commit_customer_sync(
        customer_id="sql-b",
        accounts=[_account("checking-b", "sql-b", "Checking")],
        transactions=[
            _txn(
                "other",
                customer_id="sql-b",
                account_id="checking-b",
                amount="-7.00",
                posted_at=day,
            )
        ],
        reconcile=False,
    )


def test_cash_sql_excludes_card_swipes_and_bill_like_rows(repo: TigerRepository) -> None:
    _seed(repo)
    start = datetime(2026, 11, 10, tzinfo=timezone.utc)
    end = datetime(2026, 11, 11, tzinfo=timezone.utc)

    discretionary = repo.discretionary_daily_spending(
        "sql-a",
        start_at=start,
        end_at=end,
        exclude_categories=["groceries"],
        exclude_description_tokens=["phone"],
    )
    assert sum(row["total_spent"] for row in discretionary) == Decimal("4.00")
    assert discretionary[0]["txn_count"] == 1

    # An empty exclusion list must still run and still drop card swipes.
    unfiltered = repo.discretionary_daily_spending(
        "sql-a",
        start_at=start,
        end_at=end,
        exclude_categories=[],
        exclude_description_tokens=[],
    )
    assert sum(row["total_spent"] for row in unfiltered) == Decimal("54.00")

    categories = {
        row["category"]: row["total_spent"]
        for row in repo.spending_by_category("sql-a", start_at=start, end_at=end)
    }
    assert categories["Dining"] == Decimal("4.00")
    assert "card coffee" not in categories
    assert categories["Groceries"] == Decimal("10.00")

    cash = repo.cashflow_in_window("sql-a", start_at=start, end_at=end)
    assert cash["expenses_posted"] == Decimal("54.00")
    assert cash["income_posted"] == Decimal("20.00")
    assert cash["net_posted"] == Decimal("-34.00")

    daily = repo.daily_spending(
        "sql-a", start_at=start, end_at=end, prefer_continuous_aggregate=False
    )
    assert sum(row["total_spent"] for row in daily) == Decimal("54.00")


def test_sql_window_matches_half_open_utc_days(repo: TigerRepository) -> None:
    start_at, end_at = sql_bounds_for_half_open_days(
        start_exclusive=datetime(2026, 11, 9, tzinfo=timezone.utc).date(),
        end_inclusive=datetime(2026, 11, 10, tzinfo=timezone.utc).date(),
    )
    repo.commit_customer_sync(
        customer_id="sql-a",
        accounts=[_account("checking-a", "sql-a", "Checking")],
        transactions=[
            _txn("before", amount="-1.00", posted_at=start_at - timedelta(hours=1)),
            _txn("open", amount="-2.00", posted_at=start_at),
            _txn("inside", amount="-3.00", posted_at=end_at - timedelta(minutes=1)),
            _txn("end", amount="-4.00", posted_at=end_at),
        ],
        reconcile=False,
    )
    rows = repo.spending_by_category("sql-a", start_at=start_at, end_at=end_at)
    assert rows[0]["total_spent"] == Decimal("5.00")
    assert rows[0]["txn_count"] == 2


def test_complete_snapshot_deletes_only_missing_rows(repo: TigerRepository) -> None:
    _seed(repo)
    kept = _txn("coffee")
    result = repo.commit_customer_sync(
        customer_id="sql-a",
        accounts=[
            _account("checking-a", "sql-a", "Checking"),
            _account("card-a", "sql-a", "Credit Card"),
        ],
        transactions=[kept],
        reconcile=True,
        transactions_complete=True,
    )
    assert result.transactions_deleted == 4
    remaining = repo.list_transactions("sql-a")
    assert [txn.transaction_id for txn in remaining] == ["coffee"]
    assert [txn.transaction_id for txn in repo.list_transactions("sql-b")] == ["other"]


def _install_account_owner_guard(conn) -> None:
    from pathlib import Path

    from app.db.repository import _split_sql

    sql = (
        Path(__file__).resolve().parents[2] / "app" / "db" / "schema.sql"
    ).read_text(encoding="utf-8")
    statements = [
        part
        for part in _split_sql(sql)
        if "accounts_reject_customer_reassign" in part
        or "accounts_customer_owner" in part
    ]
    assert len(statements) == 3
    with conn.cursor() as cur:
        for statement in statements:
            cur.execute(statement)
    conn.commit()


def test_account_id_cannot_move_to_another_customer(repo: TigerRepository) -> None:
    import psycopg

    _install_account_owner_guard(repo._conn)
    original = _account("shared-acct", "customer-a", "Checking").model_copy(
        update={"name": "Owner", "balance": Decimal("10.00")}
    )
    repo.commit_customer_sync(
        customer_id="customer-a",
        accounts=[original],
        transactions=[],
        reconcile=False,
    )
    repo.commit_customer_sync(
        customer_id="customer-a",
        accounts=[original.model_copy(update={"balance": Decimal("15.00")})],
        transactions=[],
        reconcile=False,
    )
    repo.commit_customer_sync(
        customer_id="customer-b",
        accounts=[
            original.model_copy(
                update={
                    "customer_id": "customer-b",
                    "name": "Stolen",
                    "account_type": "Credit Card",
                    "balance": Decimal("999.00"),
                }
            )
        ],
        transactions=[],
        reconcile=False,
    )

    owned = repo.list_accounts("customer-a")
    assert len(owned) == 1
    assert owned[0].name == "Owner"
    assert owned[0].account_type == "Checking"
    assert owned[0].balance == Decimal("15.00")
    assert repo.list_accounts("customer-b") == []

    with pytest.raises(psycopg.errors.CheckViolation):
        with repo._conn.cursor() as cur:
            cur.execute(
                "UPDATE accounts SET customer_id = %s WHERE account_id = %s",
                ("customer-b", "shared-acct"),
            )
    repo._conn.rollback()
    assert repo.list_accounts("customer-a")[0].customer_id == "customer-a"

    with repo._conn.cursor() as cur:
        cur.execute(
            "DELETE FROM transactions WHERE customer_id = %s",
            ("customer-b",),
        )
        cur.execute("DELETE FROM accounts WHERE customer_id = %s", ("customer-b",))
        assert cur.rowcount == 0
    repo._conn.commit()
    assert [account.account_id for account in repo.list_accounts("customer-a")] == [
        "shared-acct"
    ]

    with repo._conn.cursor() as cur:
        cur.execute("DELETE FROM accounts WHERE customer_id = %s", ("customer-a",))
        assert cur.rowcount == 1
    repo._conn.commit()
    assert repo.list_accounts("customer-a") == []


def test_incomplete_feed_does_not_delete(repo: TigerRepository) -> None:
    _seed(repo)
    result = repo.commit_customer_sync(
        customer_id="sql-a",
        accounts=[],
        transactions=[],
        reconcile=True,
        transactions_complete=False,
    )
    assert result.transactions_deleted == 0
    assert result.accounts_deleted == 0
    assert len(repo.list_transactions("sql-a")) == 5
    assert len(repo.list_accounts("sql-a")) == 2
