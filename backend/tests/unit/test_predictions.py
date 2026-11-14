"""Unit tests for M5 predictive calculations."""

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
    # $300 discretionary over 30 days => $10/day. Housing excluded via bill category? 
    # Only Utilities excluded; Food counts.
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
    assert forecast.estimated_spend_total == Decimal("30.00")  # 3 forward days
    # Day 0: 1000; day1: -10; day2: -40-10; day3: -10
    assert forecast.points[0].projected_balance == Decimal("1000.00")
    assert forecast.points[1].estimated_spend == Decimal("10.00")
    assert forecast.points[1].scheduled_bill_total == Decimal("0.00")
    assert forecast.points[2].scheduled_bill_total == Decimal("40.00")
    assert forecast.points[2].scheduled_bill_names == ["Phone"]
    # 1000 - 10 - 50 - 10 = 930
    assert forecast.ending_balance == Decimal("930.00")


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
    # est = 300/30 = 10/day * 14 = 140; bills 40; pending 50; buffer 100
    # safe = 1000 - 50 - 40 - 140 - 100 = 670
    assert result.safe_to_spend == Decimal("670.00")
    assert result.minimum_buffer == Decimal("100.00")


def test_spending_drift_up() -> None:
    txns = [
        _expense(10, "20.00"),  # recent window Nov 8-14 if as_of=14, recent_days=7 -> after Nov 7
        _expense(12, "30.00"),
        _expense(1, "10.00"),  # baseline
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
