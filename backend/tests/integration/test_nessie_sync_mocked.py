"""Integration tests for Nessie sync + live provider using mocked externals."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.db.repository import TigerRepository
from app.models.schemas import Account, Transaction, TransactionKind, TransactionStatus
from app.services.gemini_client import GeminiClient
from app.services.nessie_sync import NessieSyncService
from app.services.providers.live import LiveDataProvider


@pytest.fixture
def live_settings() -> Settings:
    return Settings(
        use_mock_data=False,
        demo_customer_id="demo-customer-001",
        nessie_api_key="test-key",
        nessie_base_url="https://example.test",
        nessie_customer_id="nessie-cust-1",
        database_url="postgresql://unused/db",
        gemini_api_key="gemini-test",
        gemini_model="gemini-3.5-flash-lite",
    )


def test_nessie_sync_upserts_mapped_rows(live_settings: Settings) -> None:
    nessie = MagicMock()
    nessie.list_accounts_for_customer.return_value = [
        {
            "_id": "acc-1",
            "type": "Checking",
            "nickname": "Main",
            "balance": 100,
            "customer_id": "nessie-cust-1",
        }
    ]
    nessie.list_account_transactions.return_value = [
        {
            "id": "p1",
            "type": "withdrawal",
            "amount": 12.5,
            "status": "completed",
            "description": "Lunch",
            "transaction_date": "2026-11-01",
        },
        {
            "id": "d1",
            "type": "deposit",
            "amount": 200,
            "status": "completed",
            "description": "Pay",
            "transaction_date": "2026-11-02",
        },
    ]

    repo = MagicMock(spec=TigerRepository)
    repo.upsert_accounts.side_effect = lambda accounts: len(accounts)
    repo.upsert_transactions.side_effect = lambda txns: len(txns)

    result = NessieSyncService(live_settings, nessie, repo).sync_customer(
        "demo-customer-001"
    )

    assert result.accounts_upserted == 1
    assert result.transactions_upserted == 2
    repo.ensure_schema.assert_called_once()
    accounts = repo.upsert_accounts.call_args.args[0]
    assert accounts[0].account_id == "acc-1"
    assert accounts[0].customer_id == "demo-customer-001"
    txns = repo.upsert_transactions.call_args.args[0]
    assert {t.kind for t in txns} == {
        TransactionKind.EXPENSE,
        TransactionKind.INCOME,
    }
    nessie.list_account_transactions.assert_called_once_with("acc-1")


def test_live_provider_reads_from_repo_after_sync(live_settings: Settings) -> None:
    account = Account(
        account_id="acc-1",
        customer_id="demo-customer-001",
        name="Main",
        account_type="Checking",
        balance=Decimal("100.00"),
    )
    txn = Transaction(
        transaction_id="purchase:p1",
        customer_id="demo-customer-001",
        account_id="acc-1",
        amount=Decimal("-12.50"),
        kind=TransactionKind.EXPENSE,
        status=TransactionStatus.POSTED,
        description="Lunch",
        category="Purchase",
        posted_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
    )

    fake_conn = MagicMock()
    repo = MagicMock(spec=TigerRepository)
    repo.list_accounts.return_value = [account]
    repo.list_transactions.return_value = [txn]

    nessie = MagicMock()
    gemini = MagicMock()
    gemini.explain_dashboard.return_value = "Looks stable."

    with (
        patch("app.services.providers.live.get_connection") as get_conn,
        patch("app.services.providers.live.TigerRepository", return_value=repo),
        patch("app.services.providers.live.NessieSyncService") as sync_cls,
    ):
        get_conn.return_value.__enter__.return_value = fake_conn
        get_conn.return_value.__exit__.return_value = None
        sync_cls.return_value.sync_customer.return_value = MagicMock()

        provider = LiveDataProvider(
            live_settings,
            nessie=nessie,
            gemini=gemini,
            sync_on_read=True,
        )
        accounts = provider.list_accounts("demo-customer-001")
        txns = provider.list_transactions("demo-customer-001")
        insight = provider.explain_dashboard(
            "demo-customer-001", "Posted net $10.00"
        )

    assert accounts == [account]
    assert txns == [txn]
    assert insight == "Looks stable."
    # Sync once per provider instance even across list_accounts + list_transactions.
    assert sync_cls.return_value.sync_customer.call_count == 1


def test_gemini_client_uses_configured_model(live_settings: Settings) -> None:
    fake_models = MagicMock()
    fake_models.generate_content.return_value = MagicMock(text="  Keep spending steady.  ")
    fake_client = MagicMock()
    fake_client.models = fake_models

    client = GeminiClient(live_settings, client=fake_client)
    text = client.explain_dashboard("demo-customer-001", "Posted net $10.00")

    assert text == "Keep spending steady."
    kwargs = fake_models.generate_content.call_args.kwargs
    assert kwargs["model"] == "gemini-3.5-flash-lite"
    assert "Do not invent numbers" in kwargs["contents"]
    assert "Posted net $10.00" in kwargs["contents"]


def test_nessie_client_get_uses_bearer_and_key_query(live_settings: Settings) -> None:
    from app.services.nessie_client import NessieClient

    session = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.content = b'[{"_id":"acc-1"}]'
    response.json.return_value = [{"_id": "acc-1"}]
    session.get.return_value = response

    client = NessieClient(live_settings, session=session)
    rows = client.list_accounts_for_customer("nessie-cust-1")

    assert rows == [{"_id": "acc-1"}]
    args, kwargs = session.get.call_args
    assert args[0].endswith("/customers/nessie-cust-1/accounts")
    assert kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert kwargs["params"]["key"] == "test-key"
