"""Live prediction math against synchronized Nessie → TigerData data."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.predictions import (
    build_cashflow_forecast,
    compute_safe_to_spend,
    compute_spending_drift,
    simulate_what_if_purchase,
)
from app.services.providers.live import LiveDataProvider

pytestmark = pytest.mark.live


@pytest.fixture
def live_provider(synced_live_customer) -> LiveDataProvider:
    return LiveDataProvider(
        synced_live_customer["settings"],
        sync_on_read=False,
    )


def test_cashflow_forecast_with_live_data(
    live_provider: LiveDataProvider, synced_live_customer
) -> None:
    customer_id = synced_live_customer["customer_id"]
    accounts = live_provider.list_accounts(customer_id)
    txns = live_provider.list_transactions(customer_id)
    bills = live_provider.list_scheduled_bills(customer_id)

    forecast = build_cashflow_forecast(
        customer_id=customer_id,
        accounts=accounts,
        transactions=txns,
        scheduled_bills=bills,
        as_of=date.today(),
        horizon_days=14,
        lookback_days=30,
    )
    assert forecast.customer_id == customer_id
    assert forecast.horizon_days == 14
    assert len(forecast.points) == 15
    assert forecast.daily_estimated_spend >= Decimal("0.00")
    assert forecast.scheduled_bills_total >= Decimal("0.00")
    cash_only = sum(
        (a.balance for a in accounts if "credit" not in a.account_type.lower()),
        Decimal("0"),
    ).quantize(Decimal("0.01"))
    assert forecast.starting_balance == cash_only
    # Deterministic: same inputs → same outputs.
    again = build_cashflow_forecast(
        customer_id=customer_id,
        accounts=accounts,
        transactions=txns,
        scheduled_bills=bills,
        as_of=date.today(),
        horizon_days=14,
        lookback_days=30,
    )
    assert again.model_dump() == forecast.model_dump()


def test_safe_to_spend_and_drift_with_live_data(
    live_provider: LiveDataProvider, synced_live_customer
) -> None:
    customer_id = synced_live_customer["customer_id"]
    accounts = live_provider.list_accounts(customer_id)
    txns = live_provider.list_transactions(customer_id)
    bills = live_provider.list_scheduled_bills(customer_id)

    safe = compute_safe_to_spend(
        customer_id=customer_id,
        accounts=accounts,
        transactions=txns,
        scheduled_bills=bills,
        as_of=date.today(),
        horizon_days=14,
        lookback_days=30,
        minimum_buffer=Decimal("250.00"),
    )
    assert safe.minimum_buffer == Decimal("250.00")
    assert safe.available_balance >= Decimal("0.00") or any(
        a.balance < 0 for a in accounts if "credit" not in a.account_type.lower()
    )

    drift = compute_spending_drift(
        customer_id=customer_id,
        transactions=txns,
        accounts=accounts,
        as_of=date.today(),
        recent_days=7,
        baseline_days=28,
    )
    assert drift.direction in {"up", "down", "flat"}
    assert drift.recent_spend >= Decimal("0.00")


def test_what_if_simulation_with_live_data(
    live_provider: LiveDataProvider, synced_live_customer
) -> None:
    customer_id = synced_live_customer["customer_id"]
    accounts = live_provider.list_accounts(customer_id)
    txns = live_provider.list_transactions(customer_id)
    bills = live_provider.list_scheduled_bills(customer_id)

    result = simulate_what_if_purchase(
        customer_id=customer_id,
        accounts=accounts,
        transactions=txns,
        scheduled_bills=bills,
        purchase_amount=Decimal("25.00"),
        description="live-test what-if",
        as_of=date.today(),
        horizon_days=14,
        lookback_days=30,
        minimum_buffer=Decimal("250.00"),
    )
    assert result.purchase_amount == Decimal("25.00")
    assert isinstance(result.affordable, bool)
    assert (
        result.safe_to_spend_after
        == (result.safe_to_spend_before - Decimal("25.00")).quantize(
            Decimal("0.01")
        )
    )
