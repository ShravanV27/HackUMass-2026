"""Fail-closed Nessie pagination and sync deletes. Offline mocks only."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
import requests

from app.config import Settings
from app.db.repository import (
    CustomerSyncWriteResult,
    TigerRepository,
    _UPSERT_TRANSACTION,
)
from app.models.schemas import Account, Transaction, TransactionKind, TransactionStatus
from app.services.nessie_client import (
    AccountCollection,
    NessieClient,
    NessieClientError,
    TransactionFeed,
    _MAX_COLLECTION_PAGES,
    _TRANSACTION_PAGE_LIMIT,
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


def _response(payload: object, status: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.content = b"{}" if status < 400 else b""
    response.json.return_value = payload
    return response


def test_untrusted_payloads_are_not_complete_collections() -> None:
    one = [{"id": "a"}]
    assert _is_complete_collection(None, []) is False
    assert _is_complete_collection([], []) is False
    assert _is_complete_collection({}, []) is False
    assert _is_complete_collection({"items": []}, []) is False
    assert _is_complete_collection({"message": "no accounts"}, []) is False
    assert _is_complete_collection({"id": "solo", "balance": 1}, [{"id": "solo"}]) is False
    assert _is_complete_collection({"items": one, "has_more": "true"}, one) is False
    assert _is_complete_collection({"items": one, "has_more": 1}, one) is False
    assert _is_complete_collection({"items": one, "has_more": "false", "total": "nope"}, one) is False
    full = [{"id": str(i)} for i in range(_TRANSACTION_PAGE_LIMIT)]
    assert (
        _is_complete_collection(
            {"items": full}, full, limit=_TRANSACTION_PAGE_LIMIT
        )
        is False
    )
    assert (
        _is_complete_collection(
            {"items": full, "has_more": False, "total": _TRANSACTION_PAGE_LIMIT},
            full,
            limit=_TRANSACTION_PAGE_LIMIT,
        )
        is True
    )
    assert _is_complete_collection({"items": one, "has_more": False}, one) is False
    assert (
        _is_complete_collection(
            {"items": one, "has_more": False},
            one,
            limit=_TRANSACTION_PAGE_LIMIT,
            followed_link=True,
            last_page_count=1,
        )
        is True
    )


def test_client_rejects_empty_null_and_error_bodies(settings: Settings) -> None:
    session = MagicMock()

    session.get.return_value = _response(None)
    session.get.return_value.content = b""
    client = NessieClient(settings, session=session)
    empty = client.fetch_account_transactions("acct")
    assert empty.complete is False
    assert empty.items == []

    session.get.return_value = _response(None)
    session.get.return_value.content = b"null"
    assert client.fetch_accounts_for_customer("nessie-cust-1").complete is False

    session.get.return_value = _response({})
    assert client.fetch_accounts_for_customer("nessie-cust-1").complete is False

    session.get.return_value = _response({"message": "unavailable"})
    collection = client.fetch_accounts_for_customer("nessie-cust-1")
    assert collection.complete is False
    assert collection.reported_total is None

    session.get.return_value = _response({"id": "solo", "type": "Checking", "balance": 1})
    single = client.fetch_accounts_for_customer("nessie-cust-1")
    assert single.complete is False
    assert len(single.items) == 1


def test_client_follows_next_cursor_until_explicit_end(settings: Settings) -> None:
    session = MagicMock()

    def fake_get(url, headers=None, params=None, timeout=None):
        cursor = (params or {}).get("cursor")
        if cursor == "p2":
            return _response(
                {
                    "items": [
                        {
                            "id": "txn_2",
                            "type": "deposit",
                            "amount": 4,
                            "status": "completed",
                        }
                    ],
                    "has_more": False,
                }
            )
        return _response(
            {
                "items": [
                    {
                        "id": "txn_1",
                        "type": "purchase",
                        "amount": 3,
                        "status": "completed",
                    }
                ],
                "has_more": True,
                "next_cursor": "p2",
                "total": 2,
            }
        )

    session.get.side_effect = fake_get
    client = NessieClient(settings, session=session)
    feed = client.fetch_account_transactions("acct")
    assert feed.complete is True
    assert [row["id"] for row in feed.items] == ["txn_1", "txn_2"]
    assert session.get.call_count == 2


def test_truncated_page_without_cursor_stays_incomplete(settings: Settings) -> None:
    session = MagicMock()
    session.get.return_value = _response(
        {
            "items": [{"id": "txn_1", "type": "purchase", "amount": 1}],
            "has_more": "true",
        }
    )
    client = NessieClient(settings, session=session)
    feed = client.fetch_account_transactions("acct")
    assert feed.complete is False
    assert len(feed.items) == 1
    assert session.get.call_count == 1


def test_repeated_cursor_stops_and_stays_incomplete(settings: Settings) -> None:
    session = MagicMock()
    session.get.return_value = _response(
        {
            "items": [{"id": "txn_1", "type": "purchase", "amount": 1}],
            "has_more": 1,
            "next_cursor": "again",
        }
    )
    client = NessieClient(settings, session=session)
    feed = client.fetch_account_transactions("acct")
    assert feed.complete is False
    # Page 1, page 2 (same cursor), then the third request is the repeat.
    assert session.get.call_count == 2


def test_max_pages_stops_incomplete(settings: Settings) -> None:
    session = MagicMock()
    calls = {"n": 0}

    def fake_get(url, headers=None, params=None, timeout=None):
        calls["n"] += 1
        page = calls["n"]
        return _response(
            {
                "items": [{"id": f"txn_{page}", "type": "purchase", "amount": 1}],
                "has_more": True,
                "next_cursor": f"c{page}",
            }
        )

    session.get.side_effect = fake_get
    client = NessieClient(settings, session=session)
    feed = client.fetch_account_transactions("acct")
    assert feed.complete is False
    assert session.get.call_count == _MAX_COLLECTION_PAGES
    assert len(feed.items) == _MAX_COLLECTION_PAGES


def test_second_page_http_500_keeps_first_page_incomplete(settings: Settings) -> None:
    session = MagicMock()

    def fake_get(url, headers=None, params=None, timeout=None):
        if (params or {}).get("cursor") == "p2":
            return _response({"message": "boom"}, status=500)
        return _response(
            {
                "items": [{"id": "txn_1", "type": "purchase", "amount": 9}],
                "has_more": True,
                "next": "/accounts/acct/transactions?cursor=p2",
            }
        )

    session.get.side_effect = fake_get
    client = NessieClient(settings, session=session)
    feed = client.fetch_account_transactions("acct")
    assert feed.complete is False
    assert feed.items[0]["id"] == "txn_1"


def test_second_page_timeout_stays_incomplete(settings: Settings) -> None:
    """A timed-out later page must not look like a finished snapshot."""
    session = MagicMock()

    def fake_get(url, headers=None, params=None, timeout=None):
        if (params or {}).get("cursor") == "p2":
            raise requests.Timeout("timed out")
        return _response(
            {
                "items": [{"id": "txn_1", "type": "purchase", "amount": 9}],
                "has_more": True,
                "next": "/accounts/acct/transactions?cursor=p2",
            }
        )

    session.get.side_effect = fake_get
    client = NessieClient(settings, session=session)
    feed = client.fetch_account_transactions("acct")
    assert feed.complete is False
    assert [row["id"] for row in feed.items] == ["txn_1"]


def test_first_page_timeout_does_not_commit(settings: Settings) -> None:
    session = MagicMock()
    session.get.side_effect = requests.Timeout("timed out")
    repo = MagicMock()
    client = NessieClient(settings, session=session)
    with pytest.raises(NessieClientError):
        NessieSyncService(settings, client, repo).sync_customer("demo-customer-001")
    repo.commit_customer_sync.assert_not_called()


def test_off_host_next_url_does_not_follow(settings: Settings) -> None:
    session = MagicMock()
    session.get.return_value = _response(
        {
            "items": [{"id": "txn_1", "type": "purchase", "amount": 1}],
            "has_more": True,
            "next": "https://evil.example/accounts/acct/transactions?cursor=x",
        }
    )
    client = NessieClient(settings, session=session)
    feed = client.fetch_account_transactions("acct")
    assert feed.complete is False
    assert session.get.call_count == 1


def _repo_capture() -> tuple[MagicMock, dict]:
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
    return repo, captured


def test_zero_accounts_do_not_mark_feed_complete(settings: Settings) -> None:
    nessie = MagicMock()
    nessie.fetch_accounts_for_customer.return_value = AccountCollection(
        items=[], complete=True, reported_total=None
    )
    repo, captured = _repo_capture()
    NessieSyncService(settings, nessie, repo).sync_customer("demo-customer-001")
    assert captured["transactions_complete"] is False
    assert captured["accounts"] == []


def test_explicit_zero_account_total_is_complete(settings: Settings) -> None:
    nessie = MagicMock()
    nessie.fetch_accounts_for_customer.return_value = AccountCollection(
        items=[], complete=True, reported_total=0
    )
    repo, captured = _repo_capture()
    NessieSyncService(settings, nessie, repo).sync_customer("demo-customer-001")
    assert captured["transactions_complete"] is True


def test_classic_405_fallback_is_incomplete(settings: Settings) -> None:
    session = MagicMock()

    def fake_get(url, headers=None, params=None, timeout=None):
        if url.endswith("/transactions"):
            return _response({"items": [], "has_more": False, "total": 0})
        if url.endswith("/customers/nessie-cust-1/accounts"):
            return _response(
                {
                    "items": [
                        {
                            "id": "acc-1",
                            "type": "Checking",
                            "nickname": "Main",
                            "balance": 10,
                        }
                    ],
                    "has_more": False,
                    "total": 1,
                }
            )
        response = _response([], status=405)
        response.content = b""
        return response

    session.get.side_effect = fake_get
    client = NessieClient(settings, session=session)
    repo, captured = _repo_capture()
    NessieSyncService(settings, client, repo).sync_customer("demo-customer-001")
    assert captured["reconcile"] is True
    assert captured["transactions_complete"] is False
    assert captured["transactions"] == []


def test_http_500_on_second_account_does_not_commit(settings: Settings) -> None:
    nessie = MagicMock()
    nessie.fetch_accounts_for_customer.return_value = AccountCollection(
        items=[
            {"id": "acc-1", "type": "Checking", "nickname": "A", "balance": 10},
            {"id": "acc-2", "type": "Savings", "nickname": "B", "balance": 20},
        ],
        complete=True,
        reported_total=2,
    )

    def fetch(account_id: str) -> TransactionFeed:
        if account_id == "acc-2":
            raise NessieClientError("Nessie HTTP 500", status_code=500)
        return TransactionFeed(
            items=[
                {
                    "id": "txn_1",
                    "type": "purchase",
                    "amount": 5,
                    "status": "completed",
                    "transaction_date": "2026-11-01",
                }
            ],
            complete=True,
        )

    nessie.fetch_account_transactions.side_effect = fetch
    repo = MagicMock(spec=TigerRepository)
    with pytest.raises(NessieClientError):
        NessieSyncService(settings, nessie, repo).sync_customer("demo-customer-001")
    repo.commit_customer_sync.assert_not_called()


def _cursor_repo() -> tuple[TigerRepository, MagicMock]:
    conn = MagicMock()
    conn.transaction.return_value.__enter__ = MagicMock(return_value=None)
    conn.transaction.return_value.__exit__ = MagicMock(return_value=None)
    cursor = MagicMock()
    cursor.rowcount = 0
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = None
    return TigerRepository(conn), cursor


def _delete_sql(cursor: MagicMock) -> list[str]:
    return [
        call.args[0]
        for call in cursor.execute.call_args_list
        if isinstance(call.args[0], str) and "DELETE" in call.args[0].upper()
    ]


def test_repository_default_does_not_snapshot_delete() -> None:
    repo, cursor = _cursor_repo()
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
                description="x",
                category="Purchase",
                posted_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
            )
        ],
        reconcile=True,
    )
    assert _delete_sql(cursor) == []


def test_incomplete_empty_keep_set_deletes_nothing() -> None:
    repo, cursor = _cursor_repo()
    repo.commit_customer_sync(
        customer_id="demo-customer-001",
        accounts=[],
        transactions=[],
        reconcile=True,
        transactions_complete=False,
    )
    assert _delete_sql(cursor) == []


def test_upsert_sql_refuses_to_reassign_another_customer() -> None:
    assert "WHERE transactions.customer_id = EXCLUDED.customer_id" in _UPSERT_TRANSACTION
    repo, cursor = _cursor_repo()
    posted = datetime(2026, 11, 1, tzinfo=timezone.utc)
    first = Transaction(
        transaction_id="purchase:shared",
        customer_id="customer-a",
        account_id="acc-a",
        amount=Decimal("-5.00"),
        kind=TransactionKind.EXPENSE,
        status=TransactionStatus.POSTED,
        description="a",
        category="Purchase",
        posted_at=posted,
    )
    second = first.model_copy(update={"customer_id": "customer-b", "account_id": "acc-b"})
    repo.commit_customer_sync(
        customer_id="customer-a",
        accounts=[],
        transactions=[first],
        reconcile=False,
    )
    repo.commit_customer_sync(
        customer_id="customer-b",
        accounts=[],
        transactions=[second],
        reconcile=False,
    )
    upserts = [
        call
        for call in cursor.executemany.call_args_list
        if isinstance(call.args[0], str) and "INSERT INTO transactions" in call.args[0]
    ]
    assert len(upserts) == 2
    assert "WHERE transactions.customer_id = EXCLUDED.customer_id" in upserts[0].args[0]
    assert upserts[0].args[1][0]["customer_id"] == "customer-a"
    assert upserts[1].args[1][0]["customer_id"] == "customer-b"
    assert upserts[0].args[1][0]["posted_at"] == upserts[1].args[1][0]["posted_at"]
