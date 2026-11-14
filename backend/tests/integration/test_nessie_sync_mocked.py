"""Integration tests for Nessie sync + live provider using mocked externals."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.db.repository import TigerRepository
from app.models.schemas import Account, Transaction, TransactionKind, TransactionStatus
from app.services.gemini_client import GeminiClient, GeminiClientError
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
    from app.db.repository import CustomerSyncWriteResult
    from app.services.nessie_client import AccountCollection

    nessie = MagicMock()
    nessie.fetch_accounts_for_customer.return_value = AccountCollection(
        items=[
            {
                "_id": "acc-1",
                "type": "Checking",
                "nickname": "Main",
                "balance": 100,
                "customer_id": "nessie-cust-1",
            }
        ],
        complete=True,
    )
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
    repo.commit_customer_sync.side_effect = (
        lambda **kwargs: CustomerSyncWriteResult(
            accounts_upserted=len(kwargs["accounts"]),
            transactions_upserted=len(kwargs["transactions"]),
            accounts_deleted=0,
            transactions_deleted=0,
            reconciled=kwargs["reconcile"],
        )
    )

    result = NessieSyncService(live_settings, nessie, repo).sync_customer(
        "demo-customer-001"
    )

    assert result.accounts_upserted == 1
    assert result.transactions_upserted == 2
    assert result.reconciled is True
    repo.ensure_schema.assert_called_once()
    kwargs = repo.commit_customer_sync.call_args.kwargs
    assert kwargs["customer_id"] == "demo-customer-001"
    assert kwargs["reconcile"] is True
    assert kwargs["accounts"][0].account_id == "acc-1"
    assert kwargs["accounts"][0].customer_id == "demo-customer-001"
    assert {t.kind for t in kwargs["transactions"]} == {
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


def test_explain_dashboard_without_gemini_key_returns_deterministic_summary() -> None:
    """Missing GEMINI_API_KEY must not break the live dashboard."""
    settings = Settings(
        use_mock_data=False,
        demo_customer_id="demo-customer-001",
        nessie_api_key="test-key",
        nessie_base_url="https://example.test",
        nessie_customer_id="nessie-cust-1",
        database_url="postgresql://unused/db",
        gemini_api_key=None,
        gemini_model="gemini-3.5-flash-lite",
    )
    provider = LiveDataProvider(settings, sync_on_read=False)
    summary = "Posted net $12.34; including pending $10.00; projected daily rate $-1.50."
    insight = provider.explain_dashboard("demo-customer-001", summary)

    assert summary in insight
    assert "Live metrics for demo-customer-001" in insight
    assert "Gemini unavailable" in insight
    assert "GEMINI_API_KEY is not configured" in insight
    # No secret material should appear (key was None anyway).
    assert "Bearer" not in insight


def test_explain_dashboard_redacts_secret_fragments_in_errors(
    live_settings: Settings,
) -> None:
    secret = "sk-live-should-never-leak"
    settings = Settings(
        use_mock_data=False,
        demo_customer_id="demo-customer-001",
        nessie_api_key="test-key",
        nessie_base_url="https://example.test",
        nessie_customer_id="nessie-cust-1",
        database_url="postgresql://unused/db",
        gemini_api_key=secret,
        gemini_model="gemini-3.5-flash-lite",
    )
    broken = MagicMock()
    broken.explain_dashboard.side_effect = GeminiClientError(
        f"upstream rejected key {secret}"
    )
    provider = LiveDataProvider(settings, gemini=broken, sync_on_read=False)
    insight = provider.explain_dashboard("demo-customer-001", "Posted net $1.00")

    assert "Posted net $1.00" in insight
    assert secret not in insight
    assert "***REDACTED***" in insight


def test_explain_dashboard_uses_gemini_when_injected(
    live_settings: Settings,
) -> None:
    gemini = MagicMock()
    gemini.explain_dashboard.return_value = "Cash flow looks steady."
    provider = LiveDataProvider(
        live_settings, gemini=gemini, sync_on_read=False
    )
    assert (
        provider.explain_dashboard("demo-customer-001", "Posted net $10.00")
        == "Cash flow looks steady."
    )
    gemini.explain_dashboard.assert_called_once()


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
    assert "do not invent balances" in kwargs["contents"].lower()
    assert "Do not suggest or execute transfers" in kwargs["contents"]
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


def test_live_provider_lists_scheduled_bills_from_nessie(
    live_settings: Settings,
) -> None:
    nessie = MagicMock()
    nessie.list_bills_for_customer.return_value = [
        {
            "_id": "bill-1",
            "status": "pending",
            "nickname": "Phone plan",
            "payee": "Verizon",
            "payment_amount": 45.0,
            "upcoming_payment_date": "2026-11-20",
            "recurring_date": 20,
            "account_id": "acc-1",
        }
    ]

    fake_conn = MagicMock()
    repo = MagicMock(spec=TigerRepository)

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
            sync_on_read=True,
        )
        bills = provider.list_scheduled_bills("demo-customer-001")
        # Cached — second call must not hit Nessie again.
        bills_again = provider.list_scheduled_bills("demo-customer-001")

    assert len(bills) == 1
    assert bills[0].name == "Phone plan"
    assert bills[0].amount == Decimal("45.00")
    assert bills_again == bills
    nessie.list_bills_for_customer.assert_called_once_with("nessie-cust-1")


def test_live_provider_bills_graceful_on_nessie_error(
    live_settings: Settings,
) -> None:
    from app.services.nessie_client import NessieClientError

    nessie = MagicMock()
    nessie.list_bills_for_customer.side_effect = NessieClientError("down")

    fake_conn = MagicMock()
    repo = MagicMock(spec=TigerRepository)

    with (
        patch("app.services.providers.live.get_connection") as get_conn,
        patch("app.services.providers.live.TigerRepository", return_value=repo),
        patch("app.services.providers.live.NessieSyncService") as sync_cls,
    ):
        get_conn.return_value.__enter__.return_value = fake_conn
        get_conn.return_value.__exit__.return_value = None
        sync_cls.return_value.sync_customer.return_value = MagicMock()

        provider = LiveDataProvider(
            live_settings, nessie=nessie, sync_on_read=False
        )
        assert provider.list_scheduled_bills("demo-customer-001") == []


def test_sync_customer_requires_hypertable(live_settings: Settings) -> None:
    from app.db.repository import TigerSchemaError

    nessie = MagicMock()
    nessie.list_accounts_for_customer.return_value = []
    repo = MagicMock(spec=TigerRepository)
    repo.ensure_schema.side_effect = TigerSchemaError("hypertable missing")

    with pytest.raises(TigerSchemaError):
        NessieSyncService(live_settings, nessie, repo).sync_customer(
            "demo-customer-001"
        )
    repo.ensure_schema.assert_called_once_with(require_hypertable=True)
