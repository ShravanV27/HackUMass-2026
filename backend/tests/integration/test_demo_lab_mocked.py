"""Integration: Demo Lab live path with mocked Nessie writes + sync."""

from __future__ import annotations

from decimal import Decimal
import pytest

from app.config import Settings
from app.models.schemas import (
    Account,
    DemoDepositRequest,
    DemoPurchaseRequest,
    ScheduledBill,
    Transaction,
    TransactionKind,
    TransactionStatus,
)
from app.services.demo_lab import DemoLabError, run_deposit, run_purchase
from app.services.providers.live import LiveDataProvider
from datetime import datetime, timezone


class _FakeLiveProvider(LiveDataProvider):
    """Live-mode provider that serves fixed rows and records force_sync."""

    def __init__(
        self, settings: Settings, *, update_balance_on_sync: bool = True
    ) -> None:
        super().__init__(settings, sync_on_read=False)
        self.force_sync_calls = 0
        self.balance_patch_calls = 0
        self.update_balance_on_sync = update_balance_on_sync
        self._accounts = [
            Account(
                account_id="acct-1",
                customer_id=settings.demo_customer_id,
                name="Checking",
                account_type="Checking",
                balance=Decimal("1000.00"),
            )
        ]
        self._txns: list[Transaction] = []
        self._bills: list[ScheduledBill] = []

    def list_accounts(self, customer_id: str) -> list[Account]:
        return list(self._accounts)

    def list_transactions(
        self, customer_id: str, account_id: str | None = None
    ) -> list[Transaction]:
        return list(self._txns)

    def list_scheduled_bills(self, customer_id: str) -> list[ScheduledBill]:
        return list(self._bills)

    def force_sync(self, customer_id: str) -> dict[str, int]:
        self.force_sync_calls += 1
        # Default: simulate Nessie updating balance_cents with the purchase.
        balance = (
            Decimal("950.00")
            if self.update_balance_on_sync
            else Decimal("1000.00")
        )
        self._accounts = [
            Account(
                account_id="acct-1",
                customer_id=customer_id,
                name="Checking",
                account_type="Checking",
                balance=balance,
            )
        ]
        self._txns = [
            Transaction(
                transaction_id="p1",
                customer_id=customer_id,
                account_id="acct-1",
                amount=Decimal("-50.00"),
                kind=TransactionKind.EXPENSE,
                status=(
                    TransactionStatus.POSTED
                    if self.update_balance_on_sync
                    else TransactionStatus.PENDING
                ),
                description="Mocked Nessie purchase",
                category="Shopping",
                posted_at=datetime.now(tz=timezone.utc),
            )
        ]
        return {"accounts_upserted": 1, "transactions_upserted": 1}

    def apply_local_balance_delta(
        self, customer_id: str, account_id: str, delta: Decimal
    ) -> bool:
        self.balance_patch_calls += 1
        updated: list[Account] = []
        for account in self._accounts:
            if account.account_id == account_id:
                updated.append(
                    account.model_copy(
                        update={
                            "balance": (account.balance + delta).quantize(
                                Decimal("0.01")
                            )
                        }
                    )
                )
            else:
                updated.append(account)
        self._accounts = updated
        self._txns = [
            txn.model_copy(update={"status": TransactionStatus.POSTED})
            if txn.status == TransactionStatus.PENDING
            else txn
            for txn in self._txns
        ]
        return True


def test_live_purchase_calls_nessie_then_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        use_mock_data=False,
        allow_nessie_writes=True,
        demo_customer_id="demo-customer-001",
        nessie_customer_id="nessie-1",
        nessie_api_key="test-key",
        database_url="postgresql://unused",
    )
    provider = _FakeLiveProvider(settings)

    created = {"_id": "purchase-99"}

    class _Writer:
        def create_purchase(self, *args, **kwargs):
            return created

    monkeypatch.setattr(
        "app.services.demo_lab.NessieWriteClient",
        lambda settings: _Writer(),
    )

    result = run_purchase(
        customer_id="demo-customer-001",
        provider=provider,
        body=DemoPurchaseRequest(
            account_id="acct-1",
            amount=Decimal("50.00"),
            description="Gear",
            status="posted",
            minimum_buffer=Decimal("0.00"),
        ),
        settings=settings,
    )
    assert result.mode == "live"
    assert result.synced is True
    assert provider.force_sync_calls == 1
    assert provider.balance_patch_calls == 0
    assert result.resource_id == "purchase-99"
    assert result.after.safe_to_spend < result.before.safe_to_spend
    assert provider.list_accounts("demo-customer-001")[0].balance == Decimal(
        "950.00"
    )


def test_live_purchase_patches_stale_nessie_balance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        use_mock_data=False,
        allow_nessie_writes=True,
        demo_customer_id="demo-customer-001",
        nessie_customer_id="nessie-1",
        nessie_api_key="test-key",
        database_url="postgresql://unused",
    )
    provider = _FakeLiveProvider(settings, update_balance_on_sync=False)

    class _Writer:
        def create_purchase(self, *args, **kwargs):
            return {"_id": "purchase-stale"}

    monkeypatch.setattr(
        "app.services.demo_lab.NessieWriteClient",
        lambda settings: _Writer(),
    )
    monkeypatch.setattr("app.services.demo_lab._BALANCE_RETRY_DELAY_S", 0)
    monkeypatch.setattr("app.services.demo_lab._BALANCE_SYNC_ATTEMPTS", 2)

    result = run_purchase(
        customer_id="demo-customer-001",
        provider=provider,
        body=DemoPurchaseRequest(
            account_id="acct-1",
            amount=Decimal("50.00"),
            description="Gear",
            status="posted",
            minimum_buffer=Decimal("0.00"),
        ),
        settings=settings,
    )
    assert result.synced is True
    assert provider.force_sync_calls == 2
    assert provider.balance_patch_calls == 1
    assert provider.list_accounts("demo-customer-001")[0].balance == Decimal(
        "950.00"
    )


def test_live_deposit_patches_stale_nessie_balance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        use_mock_data=False,
        allow_nessie_writes=True,
        demo_customer_id="demo-customer-001",
        nessie_customer_id="nessie-1",
        nessie_api_key="test-key",
        database_url="postgresql://unused",
    )
    provider = _FakeLiveProvider(settings, update_balance_on_sync=False)

    class _Writer:
        def create_deposit(self, *args, **kwargs):
            return {"_id": "deposit-stale"}

    monkeypatch.setattr(
        "app.services.demo_lab.NessieWriteClient",
        lambda settings: _Writer(),
    )
    monkeypatch.setattr("app.services.demo_lab._BALANCE_RETRY_DELAY_S", 0)
    monkeypatch.setattr("app.services.demo_lab._BALANCE_SYNC_ATTEMPTS", 2)

    # force_sync leaves balance flat; deposit path should optimistic-patch +50.
    def _stale_deposit_sync(customer_id: str) -> dict[str, int]:
        provider.force_sync_calls += 1
        provider._accounts = [
            Account(
                account_id="acct-1",
                customer_id=customer_id,
                name="Checking",
                account_type="Checking",
                balance=Decimal("1000.00"),
            )
        ]
        provider._txns = [
            Transaction(
                transaction_id="d1",
                customer_id=customer_id,
                account_id="acct-1",
                amount=Decimal("50.00"),
                kind=TransactionKind.INCOME,
                status=TransactionStatus.PENDING,
                description="Paycheck",
                category="Income",
                posted_at=datetime.now(tz=timezone.utc),
            )
        ]
        return {"accounts_upserted": 1, "transactions_upserted": 1}

    provider.force_sync = _stale_deposit_sync  # type: ignore[method-assign]

    result = run_deposit(
        customer_id="demo-customer-001",
        provider=provider,
        body=DemoDepositRequest(
            account_id="acct-1",
            amount=Decimal("50.00"),
            description="Paycheck",
            status="posted",
            minimum_buffer=Decimal("0.00"),
        ),
        settings=settings,
    )
    assert result.synced is True
    assert provider.balance_patch_calls == 1
    assert provider.list_accounts("demo-customer-001")[0].balance == Decimal(
        "1050.00"
    )
    assert result.after.safe_to_spend > result.before.safe_to_spend


def test_live_purchase_blocked_without_flag() -> None:
    settings = Settings(
        use_mock_data=False,
        allow_nessie_writes=False,
        demo_customer_id="demo-customer-001",
        nessie_api_key="test-key",
    )
    provider = _FakeLiveProvider(settings)
    with pytest.raises(DemoLabError) as exc:
        run_purchase(
            customer_id="demo-customer-001",
            provider=provider,
            body=DemoPurchaseRequest(account_id="acct-1", amount=Decimal("10.00")),
            settings=settings,
        )
    assert exc.value.status_code == 403
