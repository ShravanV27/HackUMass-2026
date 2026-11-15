"""Pass 3 regressions: card cash vs spending, and an already-empty runway."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.domain.alerts import category_spend_totals, evaluate_alerts
from app.domain.predictions import compute_safe_to_spend, compute_spending_drift
from app.domain.predictive_engine import classify_behavior, compute_overdraft_risk
from app.models.schemas import (
    Account,
    Transaction,
    TransactionKind,
    TransactionStatus,
)


def _cash(balance: str = "1000.00") -> Account:
    return Account(
        account_id="checking",
        customer_id="c1",
        name="Checking",
        account_type="Checking",
        balance=Decimal(balance),
    )


def _card() -> Account:
    return Account(
        account_id="cc",
        customer_id="c1",
        name="Travel Card",
        account_type="Credit Card",
        balance=Decimal("-40.00"),
    )


def _txn(
    txn_id: str,
    account_id: str,
    amount: str,
    *,
    day: int,
    kind: TransactionKind = TransactionKind.EXPENSE,
    status: TransactionStatus = TransactionStatus.POSTED,
    category: str = "Groceries",
    description: str = "store",
    counterparty: str | None = None,
) -> Transaction:
    return Transaction(
        transaction_id=txn_id,
        customer_id="c1",
        account_id=account_id,
        amount=Decimal(amount),
        kind=kind,
        status=status,
        description=description,
        category=category,
        posted_at=datetime(2026, 11, day, tzinfo=timezone.utc),
        counterparty_account_id=counterparty,
    )


def test_pending_card_activity_does_not_move_safe_to_spend() -> None:
    accounts = [_cash("1000.00"), _card()]
    txns = [
        _txn(
            "swipe",
            "cc",
            "-200.00",
            day=14,
            status=TransactionStatus.PENDING,
            description="pending groceries",
        ),
        _txn(
            "refund",
            "cc",
            "25.00",
            day=14,
            kind=TransactionKind.INCOME,
            status=TransactionStatus.PENDING,
            category="Refund",
            description="card credit",
        ),
        _txn(
            "hold",
            "checking",
            "-40.00",
            day=14,
            status=TransactionStatus.PENDING,
            description="debit hold",
        ),
    ]
    result = compute_safe_to_spend(
        customer_id="c1",
        accounts=accounts,
        transactions=txns,
        scheduled_bills=[],
        as_of=date(2026, 11, 14),
        horizon_days=7,
        lookback_days=30,
        minimum_buffer=Decimal("0.00"),
    )
    assert result.pending_expenses == Decimal("40.00")
    assert result.pending_income == Decimal("0.00")
    assert result.available_balance == Decimal("1000.00")
    # 1000 - 40 pending cash. The $200 swipe and $25 card credit are not cash.
    assert result.safe_to_spend == Decimal("960.00")


def test_drift_counts_card_purchase_and_payment_once() -> None:
    accounts = [_cash(), _card()]
    txns = [
        _txn("swipe", "cc", "-40.00", day=10, description="market"),
        _txn(
            "pay",
            "checking",
            "-40.00",
            day=12,
            category="Payment",
            description="visa payment",
            counterparty="cc",
        ),
        _txn("cash", "checking", "-10.00", day=11, category="Coffee"),
    ]
    drift = compute_spending_drift(
        customer_id="c1",
        transactions=txns,
        accounts=accounts,
        as_of=date(2026, 11, 14),
        recent_days=7,
        baseline_days=7,
    )
    assert drift.recent_spend == Decimal("50.00")

    payment_only = compute_spending_drift(
        customer_id="c1",
        transactions=[txns[1]],
        accounts=accounts,
        as_of=date(2026, 11, 14),
        recent_days=7,
        baseline_days=7,
    )
    assert payment_only.recent_spend == Decimal("40.00")


def test_category_spike_skips_the_matched_card_payment() -> None:
    accounts = [_cash("5000.00"), _card()]
    txns = [
        _txn("old", "checking", "-10.00", day=1, category="Groceries"),
        _txn("swipe", "cc", "-80.00", day=12, category="Groceries"),
        _txn(
            "pay",
            "checking",
            "-80.00",
            day=13,
            category="Payment",
            description="card payment",
            counterparty="cc",
        ),
    ]
    totals = category_spend_totals(
        txns,
        customer_id="c1",
        start=date(2026, 11, 7),
        end=date(2026, 11, 14),
        accounts=accounts,
    )
    assert totals == {"Groceries": Decimal("80.00")}

    alerts = evaluate_alerts(
        customer_id="c1",
        accounts=accounts,
        transactions=txns,
        scheduled_bills=[],
        as_of=date(2026, 11, 14),
        recent_days=7,
        baseline_days=7,
        minimum_buffer=Decimal("0.00"),
        horizon_days=7,
    )
    spikes = [a for a in alerts if a.kind == "category_spike"]
    assert len(spikes) == 1
    assert spikes[0].evidence["category"] == "Groceries"
    assert spikes[0].evidence["recent_total"] == "80.00"


def test_behavior_weekend_share_uses_the_swipe_not_the_payment() -> None:
    # 2026-11-08 is Sunday; 2026-11-10 is Tuesday.
    accounts = [_cash("2000.00"), _card()]
    txns = [
        _txn("swipe", "cc", "-100.00", day=8, description="weekend market"),
        _txn(
            "pay",
            "checking",
            "-100.00",
            day=10,
            category="Payment",
            description="visa payment",
            counterparty="cc",
        ),
    ]
    behavior = classify_behavior(
        customer_id="c1",
        accounts=accounts,
        transactions=txns,
        scheduled_bills=[],
        as_of=date(2026, 11, 14),
        lookback_days=28,
    )
    assert behavior.evidence.weekend_spend_share == Decimal("100.00")
    assert behavior.evidence.top_categories == ["Groceries"]
    assert behavior.segment == "weekend_heavy"


def test_already_negative_cash_is_zero_days_until_zero() -> None:
    risk = compute_overdraft_risk(
        customer_id="c1",
        accounts=[_cash("-20.00")],
        transactions=[],
        scheduled_bills=[],
        as_of=date(2026, 11, 14),
        horizon_days=7,
        lookback_days=30,
        minimum_buffer=Decimal("50.00"),
    )
    assert risk.min_projected_balance == Decimal("-20.00")
    assert risk.days_until_zero == 0
    assert risk.days_until_below_buffer == 0
    assert risk.overdraft_risk == "likely"
