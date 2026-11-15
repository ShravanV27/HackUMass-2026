"""Unit tests for M5 predictive calculations (engine-backed)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.domain.predictions import (
    build_cashflow_forecast,
    compute_safe_to_spend,
    compute_spending_drift,
    simulate_what_if_purchase,
)
from app.models.schemas import (
    Account,
    ScheduledBill,
    Transaction,
    TransactionKind,
    TransactionStatus,
)


def _account(balance: str = "1000.00") -> Account:
    return Account(
        account_id="a1",
        customer_id="c1",
        name="Checking",
        account_type="Checking",
        balance=Decimal(balance),
    )


def _expense(day: int, amount: str, category: str = "Food") -> Transaction:
    return Transaction(
        transaction_id=f"e-{day}-{amount}",
        customer_id="c1",
        account_id="a1",
        amount=-Decimal(amount),
        kind=TransactionKind.EXPENSE,
        status=TransactionStatus.POSTED,
        description="x",
        category=category,
        posted_at=datetime(2026, 11, day, tzinfo=timezone.utc),
    )


def test_cashflow_separates_scheduled_bills_from_estimated_spend() -> None:
    bills = [
        ScheduledBill(
            bill_id="b1",
            customer_id="c1",
            account_id="a1",
            name="Phone",
            amount=Decimal("40.00"),
            category="Utilities",
            next_due_date=date(2026, 11, 16),
        )
    ]
    # $300 discretionary on Sunday Nov 1 over 30-day lookback => flat $10/day.
    # Weekday model: Sunday rate 75; other weekdays 0.
    txns = [_expense(1, "300.00", "Food")]
    forecast = build_cashflow_forecast(
        customer_id="c1",
        accounts=[_account("1000.00")],
        transactions=txns,
        scheduled_bills=bills,
        as_of=date(2026, 11, 14),
        horizon_days=3,
        lookback_days=30,
    )
    assert forecast.daily_estimated_spend == Decimal("10.00")
    assert forecast.scheduled_bills_total == Decimal("40.00")
    # Nov 15 2026 = Sunday. Forward: Sun 75 + Mon 0(+bill) + Tue 0
    assert forecast.estimated_spend_total == Decimal("75.00")
    assert forecast.points[0].projected_balance == Decimal("1000.00")
    assert forecast.points[1].estimated_spend == Decimal("75.00")  # Sun
    assert forecast.points[2].scheduled_bill_total == Decimal("40.00")
    assert forecast.points[2].scheduled_bill_names == ["Phone"]
    assert forecast.points[2].estimated_spend == Decimal("0.00")  # Mon
    # 1000 - 75 - 40 - 0 = 885
    assert forecast.ending_balance == Decimal("885.00")
    assert forecast.spend_model == "weekday"
    assert forecast.overdraft_risk in {"none", "watch", "likely"}


def test_cashflow_does_not_add_future_income() -> None:
    forecast = build_cashflow_forecast(
        customer_id="c1",
        accounts=[_account("500.00")],
        transactions=[],
        scheduled_bills=[],
        as_of=date(2026, 11, 14),
        horizon_days=2,
        lookback_days=30,
    )
    assert forecast.income_assumed is False
    assert all(
        p.projected_balance <= Decimal("500.00") for p in forecast.points
    )


def test_safe_to_spend_applies_buffer_and_known_pending() -> None:
    txns = [
        Transaction(
            transaction_id="p1",
            customer_id="c1",
            account_id="a1",
            amount=Decimal("-50.00"),
            kind=TransactionKind.EXPENSE,
            status=TransactionStatus.PENDING,
            description="hold",
            category="Food",
            posted_at=datetime(2026, 11, 14, tzinfo=timezone.utc),
        ),
        _expense(1, "300.00"),
    ]
    result = compute_safe_to_spend(
        customer_id="c1",
        accounts=[_account("1000.00")],
        transactions=txns,
        scheduled_bills=[
            ScheduledBill(
                bill_id="b1",
                customer_id="c1",
                account_id="a1",
                name="Phone",
                amount=Decimal("40.00"),
                category="Utilities",
                next_due_date=date(2026, 11, 20),
            )
        ],
        as_of=date(2026, 11, 14),
        horizon_days=14,
        lookback_days=30,
        minimum_buffer=Decimal("100.00"),
    )
    # Weekday est over 14 forward days: Sundays Nov 16 + Nov 23 = 75*2 = 150
    # safe = 1000 - 50 - 40 - 150 - 100 = 660
    assert result.safe_to_spend == Decimal("660.00")
    assert result.minimum_buffer == Decimal("100.00")
    assert result.estimated_spend_total == Decimal("150.00")


def test_spending_drift_up() -> None:
    txns = [
        _expense(10, "20.00"),
        _expense(12, "30.00"),
        _expense(1, "10.00"),
    ]
    drift = compute_spending_drift(
        customer_id="c1",
        transactions=txns,
        as_of=date(2026, 11, 14),
        recent_days=7,
        baseline_days=7,
    )
    assert drift.recent_spend == Decimal("50.00")
    assert drift.direction in {"up", "down", "flat"}
    assert drift.drift_amount == _money_diff(drift.recent_spend, drift.baseline_spend)


def _money_diff(a: Decimal, b: Decimal) -> Decimal:
    return (a - b).quantize(Decimal("0.01"))


def test_what_if_marks_unaffordable_purchase() -> None:
    result = simulate_what_if_purchase(
        customer_id="c1",
        accounts=[_account("400.00")],
        transactions=[_expense(1, "30.00")],
        scheduled_bills=[],
        purchase_amount=Decimal("500.00"),
        description="TV",
        as_of=date(2026, 11, 14),
        horizon_days=14,
        lookback_days=30,
        minimum_buffer=Decimal("250.00"),
    )
    assert result.affordable is False
    assert result.safe_to_spend_after < Decimal("0.00")


def test_safe_to_spend_floors_at_zero() -> None:
    """Customer-facing STS never goes negative; what-if after may still be signed."""
    from app.domain.predictive_engine import compute_engine_safe_to_spend

    result = compute_engine_safe_to_spend(
        customer_id="c1",
        accounts=[_account("100.00")],
        transactions=[_expense(1, "30.00")],
        scheduled_bills=[
            ScheduledBill(
                bill_id="b1",
                customer_id="c1",
                account_id="a1",
                name="Rent",
                amount=Decimal("900.00"),
                category="Housing",
                next_due_date=date(2026, 11, 20),
            )
        ],
        as_of=date(2026, 11, 14),
        horizon_days=14,
        lookback_days=30,
        minimum_buffer=Decimal("250.00"),
    )
    assert result.safe_to_spend == Decimal("0.00")


def test_invalid_horizon_raises() -> None:
    with pytest.raises(ValueError):
        build_cashflow_forecast(
            customer_id="c1",
            accounts=[_account()],
            transactions=[],
            scheduled_bills=[],
            as_of=date(2026, 11, 14),
            horizon_days=0,
        )


def test_credit_card_balance_excluded_from_spendable_cash() -> None:
    accounts = [
        _account("1000.00"),
        Account(
            account_id="cc1",
            customer_id="c1",
            name="Travel Card",
            account_type="Credit Card",
            balance=Decimal("-500.00"),
        ),
    ]
    forecast = build_cashflow_forecast(
        customer_id="c1",
        accounts=accounts,
        transactions=[],
        scheduled_bills=[],
        as_of=date(2026, 11, 14),
        horizon_days=2,
        lookback_days=30,
    )
    assert forecast.starting_balance == Decimal("1000.00")
    safe = compute_safe_to_spend(
        customer_id="c1",
        accounts=accounts,
        transactions=[],
        scheduled_bills=[],
        as_of=date(2026, 11, 14),
        horizon_days=2,
        lookback_days=30,
        minimum_buffer=Decimal("0.00"),
    )
    assert safe.available_balance == Decimal("1000.00")


def test_empty_history_and_bills_are_graceful() -> None:
    forecast = build_cashflow_forecast(
        customer_id="c1",
        accounts=[_account("200.00")],
        transactions=[],
        scheduled_bills=[],
        as_of=date(2026, 11, 14),
        horizon_days=3,
        lookback_days=30,
    )
    assert forecast.daily_estimated_spend == Decimal("0.00")
    assert forecast.scheduled_bills_total == Decimal("0.00")
    assert forecast.ending_balance == Decimal("200.00")


def test_transfers_and_pending_excluded_from_estimated_spend() -> None:
    txns = [
        _expense(1, "300.00", "Food"),
        Transaction(
            transaction_id="xfer",
            customer_id="c1",
            account_id="a1",
            amount=Decimal("-100.00"),
            kind=TransactionKind.TRANSFER,
            status=TransactionStatus.POSTED,
            description="to savings",
            category="Transfer",
            posted_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
        ),
        Transaction(
            transaction_id="pend",
            customer_id="c1",
            account_id="a1",
            amount=Decimal("-80.00"),
            kind=TransactionKind.EXPENSE,
            status=TransactionStatus.PENDING,
            description="hold",
            category="Food",
            posted_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
        ),
    ]
    forecast = build_cashflow_forecast(
        customer_id="c1",
        accounts=[_account("1000.00")],
        transactions=txns,
        scheduled_bills=[],
        as_of=date(2026, 11, 14),
        horizon_days=1,
        lookback_days=30,
    )
    assert forecast.daily_estimated_spend == Decimal("10.00")


def test_known_bill_category_excluded_even_outside_horizon() -> None:
    """Historical rent must not inflate daily est when rent bill is known."""
    bills = [
        ScheduledBill(
            bill_id="rent",
            customer_id="c1",
            account_id="a1",
            name="Rent",
            amount=Decimal("980.00"),
            category="Housing",
            next_due_date=date(2026, 12, 5),  # outside 14-day horizon from Nov 14
        )
    ]
    txns = [
        _expense(5, "980.00", "Housing"),
        _expense(1, "300.00", "Food"),
    ]
    forecast = build_cashflow_forecast(
        customer_id="c1",
        accounts=[_account("2000.00")],
        transactions=txns,
        scheduled_bills=bills,
        as_of=date(2026, 11, 14),
        horizon_days=14,
        lookback_days=30,
    )
    assert forecast.scheduled_bills_total == Decimal("0.00")
    assert forecast.daily_estimated_spend == Decimal("10.00")
