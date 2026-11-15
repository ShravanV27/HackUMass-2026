"""Deterministic M5 predictive calculations.

Phase 1: cashflow / safe-to-spend / what-if delegate to PredictiveEngine
(weekday spend model, rolled bills). Drift remains local.

Rules:
- Never invent future income or bills (unless PaymentCycleConfig is supplied
  via the engine APIs).
- Scheduled bills are known obligations only (rolled by cadence in the engine).
- Estimated spend is derived from historical posted discretionary expenses.
- Categories covered by *any* known scheduled bills are excluded from
  estimated spend to avoid double-counting rent/utilities already scheduled.
- Transfers and pending transactions never inflate estimated spend / drift.
- Credit-card balances are excluded from spendable cash.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from app.domain.calculations import counted_posted_spending
from app.domain.predictive_engine import (
    build_engine_forecast,
    compute_engine_safe_to_spend,
    simulate_what_if_with_engine,
)
from app.models.schemas import (
    Account,
    CashflowForecastResponse,
    PaymentCycleConfig,
    SafeToSpendResponse,
    ScheduledBill,
    SpendingDriftResponse,
    Transaction,
    TransactionKind,
    TransactionStatus,
    WhatIfResponse,
)

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _as_utc_date(value: datetime | date) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.date()
        return value.astimezone(timezone.utc).date()
    return value


def bills_due_in_horizon(
    bills: list[ScheduledBill],
    *,
    as_of: date,
    horizon_days: int,
) -> list[ScheduledBill]:
    """Backward-compatible single-occurrence filter (no cadence roll)."""
    end = as_of + timedelta(days=horizon_days)
    return [
        bill
        for bill in bills
        if as_of < bill.next_due_date <= end
    ]


def daily_estimated_spend(
    transactions: list[Transaction],
    *,
    customer_id: str,
    as_of: date,
    lookback_days: int,
    exclude_categories: set[str],
    exclude_description_tokens: set[str] | None = None,
) -> Decimal:
    """Average daily posted discretionary spend over the lookback window.

    Missing history yields 0.00 (never invents spend). Transfers/pending are
    excluded. Bill categories and bill-name tokens are excluded to avoid
    double-counting confirmed obligations.
    """
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")

    window_start = as_of - timedelta(days=lookback_days)
    tokens = {t.lower() for t in (exclude_description_tokens or set()) if t}
    total = ZERO
    for txn in transactions:
        if txn.customer_id != customer_id:
            continue
        if txn.kind != TransactionKind.EXPENSE:
            continue
        if txn.status != TransactionStatus.POSTED:
            continue
        if txn.category in exclude_categories:
            continue
        if tokens and any(token in txn.description.lower() for token in tokens):
            continue
        txn_day = _as_utc_date(txn.posted_at)
        if window_start < txn_day <= as_of:
            total += abs(txn.amount)
    return _money(total / Decimal(lookback_days))


def build_cashflow_forecast(
    *,
    customer_id: str,
    accounts: list[Account],
    transactions: list[Transaction],
    scheduled_bills: list[ScheduledBill],
    as_of: date,
    horizon_days: int = 14,
    lookback_days: int = 30,
    payment_cycle: PaymentCycleConfig | None = None,
) -> CashflowForecastResponse:
    """Cash runway via PredictiveEngine (weekday model, rolled bills)."""
    return build_engine_forecast(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=scheduled_bills,
        as_of=as_of,
        horizon_days=horizon_days,
        lookback_days=lookback_days,
        payment_cycle=payment_cycle,
    )


def compute_safe_to_spend(
    *,
    customer_id: str,
    accounts: list[Account],
    transactions: list[Transaction],
    scheduled_bills: list[ScheduledBill],
    as_of: date,
    horizon_days: int = 14,
    lookback_days: int = 30,
    minimum_buffer: Decimal = Decimal("250.00"),
    payment_cycle: PaymentCycleConfig | None = None,
) -> SafeToSpendResponse:
    return compute_engine_safe_to_spend(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=scheduled_bills,
        as_of=as_of,
        horizon_days=horizon_days,
        lookback_days=lookback_days,
        minimum_buffer=minimum_buffer,
        payment_cycle=payment_cycle,
    )


def compute_spending_drift(
    *,
    customer_id: str,
    transactions: list[Transaction],
    as_of: date,
    recent_days: int = 7,
    baseline_days: int = 28,
    accounts: list[Account] | None = None,
) -> SpendingDriftResponse:
    if recent_days < 1 or baseline_days < 1:
        raise ValueError("recent_days and baseline_days must be >= 1")

    recent_start = as_of - timedelta(days=recent_days)
    baseline_end = recent_start
    baseline_start = baseline_end - timedelta(days=baseline_days)

    recent = _posted_expense_total(
        transactions, customer_id, start=recent_start, end=as_of, accounts=accounts
    )
    # Normalize baseline to a recent-length comparable average * recent_days.
    baseline_raw = _posted_expense_total(
        transactions,
        customer_id,
        start=baseline_start,
        end=baseline_end,
        accounts=accounts,
    )
    baseline_comparable = _money(
        (baseline_raw / Decimal(baseline_days)) * Decimal(recent_days)
    )
    drift_amount = _money(recent - baseline_comparable)
    if baseline_comparable == ZERO:
        drift_pct = ZERO if drift_amount == ZERO else Decimal("100.00")
    else:
        drift_pct = _money((drift_amount / baseline_comparable) * Decimal("100"))

    if drift_amount > ZERO:
        direction: str = "up"
    elif drift_amount < ZERO:
        direction = "down"
    else:
        direction = "flat"

    return SpendingDriftResponse(
        customer_id=customer_id,
        as_of=as_of,
        recent_days=recent_days,
        baseline_days=baseline_days,
        recent_spend=recent,
        baseline_spend=baseline_comparable,
        drift_amount=drift_amount,
        drift_pct=drift_pct,
        direction=direction,  # type: ignore[arg-type]
    )


def simulate_what_if_purchase(
    *,
    customer_id: str,
    accounts: list[Account],
    transactions: list[Transaction],
    scheduled_bills: list[ScheduledBill],
    purchase_amount: Decimal,
    description: str,
    as_of: date,
    horizon_days: int = 14,
    lookback_days: int = 30,
    minimum_buffer: Decimal = Decimal("250.00"),
    payment_cycle: PaymentCycleConfig | None = None,
) -> WhatIfResponse:
    return simulate_what_if_with_engine(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=scheduled_bills,
        purchase_amount=purchase_amount,
        description=description,
        as_of=as_of,
        horizon_days=horizon_days,
        lookback_days=lookback_days,
        minimum_buffer=minimum_buffer,
        payment_cycle=payment_cycle,
    )


def _posted_expense_total(
    transactions: list[Transaction],
    customer_id: str,
    *,
    start: date,
    end: date,
    accounts: list[Account] | None = None,
) -> Decimal:
    total = sum(
        (
            amount
            for _txn, amount in counted_posted_spending(
                customer_id,
                transactions,
                accounts,
                start=start,
                end=end,
            )
        ),
        ZERO,
    )
    return _money(total)
