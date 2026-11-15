"""Offline regression tests for Nessie → TigerData account reconciliation."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.db.repository import CustomerSyncWriteResult, TigerRepository
from app.models.schemas import Account, Transaction, TransactionKind, TransactionStatus
from app.services.nessie_client import (
    AccountCollection,
    NessieClientError,
    TransactionFeed,
    _is_complete_collection,
)
from app.services.nessie_sync import NessieSyncService


@pytest.fixture
def settings() -> Settings:
    return Settings(
        use_mock_data=False,
        demo_customer_id="demo-customer-001",
        nessie_api_key="test-key",
        nessie_base_url="https://example.test",
        nessie_customer_id="nessie-cust-1",
        database_url="postgresql://unused/db",
    )


def _acct(account_id: str, *, balance: str = "100.00") -> dict:
    return {
        "id": account_id,
        "type": "Checking",
        "nickname": account_id,
        "balance": balance,
        "customer_id": "nessie-cust-1",
    }


def test_collection_complete_detects_pagination() -> None:
    items = [{"id": "a"}]
    # Missing metadata is incomplete. Explicit has_more false + matching total is complete.
    assert _is_complete_collection(items, items) is False
    assert _is_complete_collection({"items": items}, items) is False
    assert _is_complete_collection({"items": items, "total": 1}, items) is False
    assert (
        _is_complete_collection(
            {"items": items, "has_more": False, "total": 1}, items
        )
        is True
    )
    assert (
        _is_complete_collection({"items": items, "total": 5}, items) is False
    )
    assert (
        _is_complete_collection({"items": items, "next": "/page/2"}, items)
        is False
    )
    assert (
        _is_complete_collection(
            {"items": items, "next_cursor": "opaque-token"}, items
        )
        is False
    )
    assert (
        _is_complete_collection({"items": items, "has_more": True}, items)
        is False
    )
    assert _is_complete_collection({"items": [], "total": 0}, []) is True


def test_sync_reconciles_when_account_ids_change(settings: Settings) -> None:
    """Stale IDs for the same app customer must be deleted on complete fetch."""
    nessie = MagicMock()
    nessie.fetch_accounts_for_customer.return_value = AccountCollection(
        items=[_acct("acc-new-1", balance="2500"), _acct("acc-new-2", balance="10000")],
        complete=True,
    )
    nessie.list_account_transactions.return_value = []

    repo = MagicMock(spec=TigerRepository)
    captured: dict = {}

    def capture(**kwargs):
        captured.update(kwargs)
        return CustomerSyncWriteResult(
            accounts_upserted=len(kwargs["accounts"]),
            transactions_upserted=len(kwargs["transactions"]),
            accounts_deleted=3,
            transactions_deleted=1,
            reconciled=kwargs["reconcile"],
        )

    repo.commit_customer_sync.side_effect = capture

    result = NessieSyncService(settings, nessie, repo).sync_customer(
        "demo-customer-001"
    )

    assert result.reconciled is True
    assert result.accounts_deleted == 3
    assert captured["reconcile"] is True
    assert captured["customer_id"] == "demo-customer-001"
    assert {a.account_id for a in captured["accounts"]} == {
        "acc-new-1",
        "acc-new-2",
    }


def test_sync_skips_reconcile_on_incomplete_page(settings: Settings) -> None:
    nessie = MagicMock()
    nessie.fetch_accounts_for_customer.return_value = AccountCollection(
        items=[_acct("acc-partial")],
        complete=False,
    )
    nessie.list_account_transactions.return_value = []
    repo = MagicMock(spec=TigerRepository)
    repo.commit_customer_sync.side_effect = (
        lambda **kwargs: CustomerSyncWriteResult(
            accounts_upserted=len(kwargs["accounts"]),
            transactions_upserted=0,
            accounts_deleted=0,
            transactions_deleted=0,
            reconciled=kwargs["reconcile"],
        )
    )

    result = NessieSyncService(settings, nessie, repo).sync_customer(
        "demo-customer-001"
    )

    assert result.reconciled is False
    assert result.accounts_deleted == 0
    assert repo.commit_customer_sync.call_args.kwargs["reconcile"] is False


def test_incomplete_transaction_feed_skips_snapshot_reconcile(
    settings: Settings,
) -> None:
    nessie = MagicMock()
    nessie.fetch_accounts_for_customer.return_value = AccountCollection(
        items=[_acct("acc-1")],
        complete=True,
    )
    nessie.fetch_account_transactions.return_value = TransactionFeed(
        items=[
            {
                "id": "txn-partial",
                "type": "purchase",
                "amount": 8,
                "status": "completed",
                "transaction_date": "2026-11-01",
            }
        ],
        complete=False,
    )
    repo = MagicMock(spec=TigerRepository)
    captured: dict = {}

    def capture(**kwargs):
        captured.update(kwargs)
        return CustomerSyncWriteResult(
            accounts_upserted=len(kwargs["accounts"]),
            transactions_upserted=len(kwargs["transactions"]),
            accounts_deleted=0,
            transactions_deleted=0,
            reconciled=kwargs["reconcile"],
        )

    repo.commit_customer_sync.side_effect = capture
    result = NessieSyncService(settings, nessie, repo).sync_customer(
        "demo-customer-001"
    )
    assert result.reconciled is True
    assert captured["transactions_complete"] is False
    assert len(captured["transactions"]) == 1
    assert captured["transactions"][0].transaction_id == "purchase:txn-partial"


def test_sync_does_not_write_when_nessie_fetch_fails(settings: Settings) -> None:
    nessie = MagicMock()
    nessie.fetch_accounts_for_customer.side_effect = NessieClientError(
        "Nessie HTTP 500", status_code=500
    )
    repo = MagicMock(spec=TigerRepository)

    with pytest.raises(NessieClientError):
        NessieSyncService(settings, nessie, repo).sync_customer("demo-customer-001")

    repo.commit_customer_sync.assert_not_called()
    repo.upsert_accounts.assert_not_called()
    repo.upsert_transactions.assert_not_called()


def test_delete_stale_scoped_to_customer_only() -> None:
    """Repository prune SQL must bind customer_id and never touch other customers."""
    conn = MagicMock()
    txn_ctx = MagicMock()
    conn.transaction.return_value.__enter__ = MagicMock(return_value=txn_ctx)
    conn.transaction.return_value.__exit__ = MagicMock(return_value=None)

    cursor = MagicMock()
    cursor.rowcount = 2
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = None

    repo = TigerRepository(conn)
    accounts = [
        Account(
            account_id="keep-1",
            customer_id="demo-customer-001",
            name="Checking",
            account_type="Checking",
            balance=Decimal("2500.00"),
        )
    ]
    txns = [
        Transaction(
            transaction_id="t1",
            customer_id="demo-customer-001",
            account_id="keep-1",
            amount=Decimal("-10.00"),
            kind=TransactionKind.EXPENSE,
            status=TransactionStatus.POSTED,
            description="x",
            category="Purchase",
            posted_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        )
    ]

    result = repo.commit_customer_sync(
        customer_id="demo-customer-001",
        accounts=accounts,
        transactions=txns,
        reconcile=True,
        transactions_complete=True,
    )

    assert result.reconciled is True
    assert result.transactions_upserted == 1
    # Find DELETE statements and ensure customer_id is always bound.
    delete_calls = [
        call
        for call in cursor.execute.call_args_list
        if isinstance(call.args[0], str) and call.args[0].lstrip().upper().startswith("DELETE")
    ]
    assert len(delete_calls) == 2  # transactions then accounts
    txn_sql, txn_params = delete_calls[0].args
    acct_sql, acct_params = delete_calls[1].args
    assert "customer_id = %s" in txn_sql
    assert txn_params[0] == "demo-customer-001"
    assert "t1" in txn_params[1]
    assert datetime(2026, 11, 1, tzinfo=timezone.utc) in txn_params[2]
    assert "unnest" in txn_sql.lower()
    assert "customer_id = %s" in acct_sql
    assert acct_params[0] == "demo-customer-001"
    assert "keep-1" in acct_params[1]


def test_delete_stale_not_invoked_without_reconcile() -> None:
    conn = MagicMock()
    conn.transaction.return_value.__enter__ = MagicMock(return_value=None)
    conn.transaction.return_value.__exit__ = MagicMock(return_value=None)
    cursor = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = None

    repo = TigerRepository(conn)
    result = repo.commit_customer_sync(
        customer_id="demo-customer-001",
        accounts=[],
        transactions=[],
        reconcile=False,
    )
    assert result.reconciled is False
    assert result.accounts_deleted == 0
    delete_calls = [
        call
        for call in cursor.execute.call_args_list
        if isinstance(call.args[0], str) and "DELETE" in call.args[0].upper()
    ]
    assert delete_calls == []


def test_reconcile_drops_transactions_missing_from_complete_snapshot() -> None:
    """A kept account must not retain rows Nessie no longer returns."""
    conn = MagicMock()
    conn.transaction.return_value.__enter__ = MagicMock(return_value=None)
    conn.transaction.return_value.__exit__ = MagicMock(return_value=None)
    cursor = MagicMock()
    cursor.rowcount = 1
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = None

    kept_at = datetime(2026, 11, 2, tzinfo=timezone.utc)
    stale_at = datetime(2026, 11, 1, tzinfo=timezone.utc)
    repo = TigerRepository(conn)
    repo.commit_customer_sync(
        customer_id="demo-customer-001",
        accounts=[
            Account(
                account_id="keep-1",
                customer_id="demo-customer-001",
                name="Checking",
                account_type="Checking",
                balance=Decimal("10.00"),
            )
        ],
        transactions=[
            Transaction(
                transaction_id="purchase:same",
                customer_id="demo-customer-001",
                account_id="keep-1",
                amount=Decimal("-4.00"),
                kind=TransactionKind.EXPENSE,
                status=TransactionStatus.POSTED,
                description="old copy",
                category="Purchase",
                posted_at=stale_at,
            ),
            Transaction(
                transaction_id="purchase:same",
                customer_id="demo-customer-001",
                account_id="keep-1",
                amount=Decimal("-5.00"),
                kind=TransactionKind.EXPENSE,
                status=TransactionStatus.POSTED,
                description="current",
                category="Purchase",
                posted_at=kept_at,
            ),
            Transaction(
                transaction_id="purchase:keep",
                customer_id="demo-customer-001",
                account_id="keep-1",
                amount=Decimal("-1.00"),
                kind=TransactionKind.EXPENSE,
                status=TransactionStatus.POSTED,
                description="stay",
                category="Purchase",
                posted_at=kept_at,
            ),
        ],
        reconcile=True,
        transactions_complete=True,
    )

    upserts = [
        call
        for call in cursor.executemany.call_args_list
        if isinstance(call.args[0], str) and "INSERT INTO transactions" in call.args[0]
    ]
    assert len(upserts) == 1
    rows = upserts[0].args[1]
    assert {row["transaction_id"] for row in rows} == {
        "purchase:same",
        "purchase:keep",
    }
    same = next(row for row in rows if row["transaction_id"] == "purchase:same")
    assert same["posted_at"] == kept_at
    assert same["amount"] == Decimal("-5.00")

    txn_delete = next(
        call
        for call in cursor.execute.call_args_list
        if isinstance(call.args[0], str) and "unnest" in call.args[0].lower()
    )
    kept_ids = txn_delete.args[1][1]
    kept_times = txn_delete.args[1][2]
    assert "purchase:same" in kept_ids
    assert "purchase:keep" in kept_ids
    assert kept_at in kept_times
    assert stale_at not in kept_times


def test_partial_transaction_feed_does_not_delete_by_snapshot() -> None:
    conn = MagicMock()
    conn.transaction.return_value.__enter__ = MagicMock(return_value=None)
    conn.transaction.return_value.__exit__ = MagicMock(return_value=None)
    cursor = MagicMock()
    cursor.rowcount = 0
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = None

    repo = TigerRepository(conn)
    repo.commit_customer_sync(
        customer_id="demo-customer-001",
        accounts=[
            Account(
                account_id="keep-1",
                customer_id="demo-customer-001",
                name="Checking",
                account_type="Checking",
                balance=Decimal("10.00"),
            )
        ],
        transactions=[
            Transaction(
                transaction_id="t1",
                customer_id="demo-customer-001",
                account_id="keep-1",
                amount=Decimal("-1.00"),
                kind=TransactionKind.EXPENSE,
                status=TransactionStatus.POSTED,
                description="partial",
                category="Purchase",
                posted_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
            )
        ],
        reconcile=True,
        transactions_complete=False,
    )
    delete_sql = [
        call.args[0]
        for call in cursor.execute.call_args_list
        if isinstance(call.args[0], str) and "DELETE" in call.args[0].upper()
    ]
    assert delete_sql == []
