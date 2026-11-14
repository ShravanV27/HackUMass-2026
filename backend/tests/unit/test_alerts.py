"""Unit tests for deterministic Phase 3 alerts."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.domain.alerts import evaluate_alerts
from app.models.schemas import (
    Account,
    ScheduledBill,
    Transaction,
    TransactionKind,
    TransactionStatus,
)


def _account(balance: str = "500.00") -> Account:
    return Account(
        account_id="a1",
        customer_id="c1",
        name="Checking",
        account_type="Checking",
        balance=Decimal(balance),
    )


def _expense(day: int, amount: str, category: str = "Food") -> Transaction:
    return Transaction(
        transaction_id=f"e-{day}-{amount}-{category}",
        customer_id="c1",
        account_id="a1",
        amount=-Decimal(amount),
        kind=TransactionKind.EXPENSE,
        status=TransactionStatus.POSTED,
        description="x",
        category=category,
        posted_at=datetime(2026, 11, day, tzinfo=timezone.utc),
    )


def test_overdraft_alert_fires() -> None:
    bills = [
        ScheduledBill(
            bill_id="rent",
            customer_id="c1",
            account_id="a1",
            name="Rent",
            amount=Decimal("900.00"),
            category="Housing",
            next_due_date=date(2026, 11, 16),
            cadence="one_time",
        )
    ]
    alerts = evaluate_alerts(
        customer_id="c1",
        accounts=[_account("400.00")],
        transactions=[_expense(1, "300.00")],
        scheduled_bills=bills,
        as_of=date(2026, 11, 14),
        horizon_days=7,
        minimum_buffer=Decimal("250.00"),
    )
    kinds = {a.kind for a in alerts}
    assert "overdraft_risk" in kinds
    od = next(a for a in alerts if a.kind == "overdraft_risk")
    assert od.severity in {"watch", "urgent"}
    assert "min_projected_balance" in od.evidence


def test_drift_and_bill_cluster_alerts() -> None:
    # Heavy recent spend vs light baseline.
    txns = [
        _expense(1, "10.00"),
        _expense(10, "80.00"),
        _expense(12, "90.00"),
        _expense(13, "70.00"),
    ]
    bills = [
        ScheduledBill(
            bill_id="b1",
            customer_id="c1",
            account_id="a1",
            name="Phone",
            amount=Decimal("40.00"),
            category="Utilities",
            next_due_date=date(2026, 11, 16),
            cadence="one_time",
        ),
        ScheduledBill(
            bill_id="b2",
            customer_id="c1",
            account_id="a1",
            name="Internet",
            amount=Decimal("70.00"),
            category="Utilities",
            next_due_date=date(2026, 11, 18),
            cadence="one_time",
        ),
    ]
    alerts = evaluate_alerts(
        customer_id="c1",
        accounts=[_account("5000.00")],
        transactions=txns,
        scheduled_bills=bills,
        as_of=date(2026, 11, 14),
        horizon_days=14,
        minimum_buffer=Decimal("100.00"),
        recent_days=7,
        baseline_days=7,
    )
    kinds = {a.kind for a in alerts}
    assert "bill_cluster" in kinds
    assert "spending_drift" in kinds


def test_category_spike_from_supplied_totals() -> None:
    alerts = evaluate_alerts(
        customer_id="c1",
        accounts=[_account("3000.00")],
        transactions=[],
        scheduled_bills=[],
        as_of=date(2026, 11, 14),
        category_totals_recent={"Dining": Decimal("120.00")},
        category_totals_baseline={"Dining": Decimal("80.00")},  # 7d comparable ~20
        recent_days=7,
        baseline_days=28,
        minimum_buffer=Decimal("50.00"),
    )
    spikes = [a for a in alerts if a.kind == "category_spike"]
    assert spikes
    assert spikes[0].evidence["category"] == "Dining"
