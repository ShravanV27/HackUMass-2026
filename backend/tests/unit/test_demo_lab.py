"""Unit tests for Demo Lab mock mutations and write gating."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.config import Settings
from app.models.schemas import DemoDepositRequest, DemoPurchaseRequest
from app.services.demo_lab import DemoLabError, require_writes_enabled, run_deposit, run_purchase
from app.services.providers.mock import MockDataProvider
from app.services.session_bindings import bind_nessie_customer, get_bound_nessie_customer_id


def test_require_writes_disabled_by_default() -> None:
    settings = Settings(allow_nessie_writes=False, use_mock_data=True)
    with pytest.raises(DemoLabError) as exc:
        require_writes_enabled(settings)
    assert exc.value.status_code == 403


def test_mock_purchase_reduces_safe_to_spend() -> None:
    settings = Settings(
        allow_nessie_writes=True,
        use_mock_data=True,
        demo_customer_id="demo-customer-001",
    )
    provider = MockDataProvider()
    body = DemoPurchaseRequest(
        account_id="acct-checking-001",
        amount=Decimal("50.00"),
        description="Coffee gear",
        status="posted",
        minimum_buffer=Decimal("100.00"),
    )
    result = run_purchase(
        customer_id="demo-customer-001",
        provider=provider,
        body=body,
        settings=settings,
    )
    assert result.action == "purchase"
    assert result.mode == "mock"
    assert result.synced is False
    assert result.after.safe_to_spend < result.before.safe_to_spend
    assert result.delta_safe_to_spend < Decimal("0.00")


def test_mock_deposit_increases_safe_to_spend() -> None:
    settings = Settings(
        allow_nessie_writes=True,
        use_mock_data=True,
        demo_customer_id="demo-customer-001",
    )
    provider = MockDataProvider()
    body = DemoDepositRequest(
        account_id="acct-checking-001",
        amount=Decimal("200.00"),
        description="Payday boost",
        status="posted",
        minimum_buffer=Decimal("100.00"),
    )
    result = run_deposit(
        customer_id="demo-customer-001",
        provider=provider,
        body=body,
        settings=settings,
    )
    assert result.after.safe_to_spend > result.before.safe_to_spend
    assert result.delta_safe_to_spend > Decimal("0.00")


def test_session_binding_roundtrip() -> None:
    bind_nessie_customer("demo-customer-001", "nessie-abc")
    assert get_bound_nessie_customer_id("demo-customer-001") == "nessie-abc"


def test_purchase_rejects_foreign_account() -> None:
    settings = Settings(allow_nessie_writes=True, use_mock_data=True)
    provider = MockDataProvider()
    with pytest.raises(DemoLabError) as exc:
        run_purchase(
            customer_id="demo-customer-001",
            provider=provider,
            body=DemoPurchaseRequest(
                account_id="not-an-account",
                amount=Decimal("10.00"),
            ),
            settings=settings,
        )
    assert exc.value.status_code == 404
