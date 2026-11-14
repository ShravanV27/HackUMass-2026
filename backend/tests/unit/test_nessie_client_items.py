"""Regression tests for Nessie v2 {"items": [...]} list envelopes."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.db.repository import TigerRepository
from app.services.nessie_client import NessieClient, _as_list
from app.services.nessie_mapper import map_account
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


def test_as_list_unwraps_items_envelope() -> None:
    payload = {
        "items": [
            {
                "_id": "acc-1",
                "type": "Checking",
                "nickname": "Everyday",
                "balance": 2450.75,
                "customer_id": "nessie-cust-1",
            },
            {
                "_id": "acc-2",
                "type": "Savings",
                "nickname": "Rainy Day",
                "balance": 5200,
                "customer_id": "nessie-cust-1",
            },
        ],
        "total": 2,
    }
    rows = _as_list(payload)
    assert len(rows) == 2
    assert rows[0]["_id"] == "acc-1"
    # Must not treat the envelope itself as an account row.
    assert "items" not in rows[0]


def test_as_list_unwraps_transaction_items() -> None:
    purchases = _as_list(
        {
            "items": [
                {
                    "_id": "p1",
                    "amount": 12.5,
                    "status": "completed",
                    "description": "Lunch",
                    "purchase_date": "2026-11-01",
                }
            ]
        }
    )
    deposits = _as_list({"items": [{"_id": "d1", "amount": 100, "status": "pending"}]})
    withdrawals = _as_list({"items": []})
    transfers = _as_list(
        {
            "items": [
                {
                    "_id": "t1",
                    "amount": 50,
                    "payer_id": "acc-1",
                    "payee_id": "acc-2",
                    "status": "completed",
                    "transaction_date": "2026-11-02",
                }
            ]
        }
    )
    assert len(purchases) == 1
    assert purchases[0]["_id"] == "p1"
    assert deposits[0]["_id"] == "d1"
    assert withdrawals == []
    assert transfers[0]["_id"] == "t1"


def test_as_list_still_supports_bare_array_and_single_resource() -> None:
    assert _as_list([{"_id": "a"}]) == [{"_id": "a"}]
    assert _as_list({"_id": "solo", "balance": 1}) == [{"_id": "solo", "balance": 1}]
    assert _as_list({"total": 0}) == []


def test_client_list_accounts_for_customer_unwraps_items(settings: Settings) -> None:
    session = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.content = b'{"items":[{"_id":"acc-1","type":"Checking","balance":10}]}'
    response.json.return_value = {
        "items": [
            {
                "_id": "acc-1",
                "type": "Checking",
                "nickname": "Main",
                "balance": 10,
                "customer_id": "nessie-cust-1",
            }
        ]
    }
    session.get.return_value = response

    client = NessieClient(settings, session=session)
    rows = client.list_accounts_for_customer("nessie-cust-1")

    assert len(rows) == 1
    account = map_account(rows[0], app_customer_id="demo-customer-001")
    assert account.account_id == "acc-1"
    assert account.balance == Decimal("10")


def test_sync_customer_with_realistic_v2_items_envelopes(settings: Settings) -> None:
    """End-to-end sync path: client unwrap → map_account must not see envelope."""
    session = MagicMock()

    def fake_get(url, headers=None, params=None, timeout=None):
        response = MagicMock()
        response.status_code = 200
        if url.endswith("/customers/nessie-cust-1/accounts"):
            payload = {
                "items": [
                    {
                        "id": "acct_01m7djs39b2adt8vfg8ztzpw0f",
                        "type": "Checking",
                        "nickname": "Main",
                        "balance": 100,
                        "customer_id": "nessie-cust-1",
                    }
                ]
            }
        elif url.endswith(
            "/accounts/acct_01m7djs39b2adt8vfg8ztzpw0f/transactions"
        ):
            payload = {
                "items": [
                    {
                        "id": "txn_01",
                        "type": "withdrawal",
                        "amount": 12.5,
                        "status": "completed",
                        "description": "Lunch",
                        "transaction_date": "2026-11-01",
                    },
                    {
                        "id": "txn_02",
                        "type": "deposit",
                        "amount": 200,
                        "status": "completed",
                        "description": "Pay",
                        "transaction_date": "2026-11-02",
                    },
                ]
            }
        else:
            payload = {"items": []}
        response.content = b"{}"
        response.json.return_value = payload
        return response

    session.get.side_effect = fake_get
    nessie = NessieClient(settings, session=session)
    repo = MagicMock(spec=TigerRepository)
    repo.upsert_accounts.side_effect = lambda accounts: len(accounts)
    repo.upsert_transactions.side_effect = lambda txns: len(txns)

    result = NessieSyncService(settings, nessie, repo).sync_customer(
        "demo-customer-001"
    )

    assert result.accounts_upserted == 1
    assert result.transactions_upserted == 2
    mapped = repo.upsert_accounts.call_args.args[0][0]
    assert mapped.account_id == "acct_01m7djs39b2adt8vfg8ztzpw0f"
    assert mapped.name == "Main"


def test_list_purchases_tolerates_v2_405(settings: Settings) -> None:
    session = MagicMock()
    response = MagicMock()
    response.status_code = 405
    response.headers = {"Allow": "POST"}
    response.content = b""
    session.get.return_value = response

    client = NessieClient(settings, session=session)
    assert client.list_purchases("acct_01abc") == []
    assert client.list_deposits("acct_01abc") == []
    assert client.list_withdrawals("acct_01abc") == []
    assert client.list_transfers("acct_01abc") == []


def test_list_account_transactions_uses_v2_path(settings: Settings) -> None:
    session = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.content = b'{"items":[{"id":"txn_1","type":"deposit","amount":5}]}'
    response.json.return_value = {
        "items": [{"id": "txn_1", "type": "deposit", "amount": 5, "status": "completed"}]
    }
    session.get.return_value = response

    client = NessieClient(settings, session=session)
    rows = client.list_account_transactions("acct_01abc")
    assert rows[0]["id"] == "txn_1"
    assert session.get.call_args.args[0].endswith(
        "/accounts/acct_01abc/transactions"
    )
