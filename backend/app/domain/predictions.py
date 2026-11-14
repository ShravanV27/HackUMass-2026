"""Deterministic M5 predictive calculations.

Rules:
- Never invent future income or bills.
- Scheduled bills are known obligations only.
- Estimated spend is derived from historical posted discretionary expenses.
- Categories covered by *any* known scheduled bills are excluded from
  estimated spend to avoid double-counting rent/utilities already scheduled.
- Transfers and pending transactions never inflate estimated spend / drift.
- Credit-card balances are excluded from spendable cash.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from app.domain.calculations import spendable_cash_balance
from app.models.schemas import (
    Account,
    CashflowDayPoint,
    CashflowForecastResponse,
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
) -> CashflowForecastResponse:
    if horizon_days < 1:
        raise ValueError("horizon_days must be >= 1")

    # Cash only — credit-card debt must not count as spendable runway.
    starting = spendable_cash_balance(accounts)
    customer_bills = [b for b in scheduled_bills if b.customer_id == customer_id]
    due = bills_due_in_horizon(
        customer_bills,
        as_of=as_of,
        horizon_days=horizon_days,
    )
    # Exclude categories for *all* known bills (not only those due in-horizon)
    # so historical rent/utilities do not inflate discretionary estimates.
    exclude = {b.category for b in customer_bills} | {"Bills"}
    bill_names = {b.name for b in customer_bills}
    daily_est = daily_estimated_spend(
        transactions,
        customer_id=customer_id,
        as_of=as_of,
        lookback_days=lookback_days,
        exclude_categories=exclude,
        exclude_description_tokens=bill_names,
    )

    bills_by_day: dict[date, list[ScheduledBill]] = {}
    for bill in due:
        bills_by_day.setdefault(bill.next_due_date, []).append(bill)

    points: list[CashflowDayPoint] = []
    balance = starting
    min_balance = starting
    scheduled_total = ZERO
    estimated_total = ZERO

    points.append(
        CashflowDayPoint(
            date=as_of,
            projected_balance=balance,
            scheduled_bill_total=ZERO,
            estimated_spend=ZERO,
            scheduled_bill_names=[],
        )
    )

    for offset in range(1, horizon_days + 1):
        day = as_of + timedelta(days=offset)
        day_bills = bills_by_day.get(day, [])
        bill_total = _money(sum((b.amount for b in day_bills), ZERO))
        # Estimated spend applies to each forward day; bills are separate.
        est = daily_est
        balance = _money(balance - bill_total - est)
        scheduled_total += bill_total
        estimated_total += est
        min_balance = min(min_balance, balance)
        points.append(
            CashflowDayPoint(
                date=day,
                projected_balance=balance,
                scheduled_bill_total=bill_total,
                estimated_spend=est,
                scheduled_bill_names=[b.name for b in day_bills],
            )
        )

    return CashflowForecastResponse(
        customer_id=customer_id,
        as_of=as_of,
        horizon_days=horizon_days,
        starting_balance=starting,
        daily_estimated_spend=daily_est,
        scheduled_bills_in_horizon=due,
        estimated_spend_total=_money(estimated_total),
        scheduled_bills_total=_money(scheduled_total),
        ending_balance=balance,
        min_balance=_money(min_balance),
        points=points,
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
) -> SafeToSpendResponse:
    if minimum_buffer < 0:
        raise ValueError("minimum_buffer must be non-negative")

    available = spendable_cash_balance(accounts)
    pending_expenses = ZERO
    pending_income = ZERO
    for txn in transactions:
        if txn.customer_id != customer_id:
            continue
        if txn.status != TransactionStatus.PENDING:
            continue
        # Transfers never count as pending income/expense for safe-to-spend.
        if txn.kind == TransactionKind.EXPENSE:
            pending_expenses += abs(txn.amount)
        elif txn.kind == TransactionKind.INCOME:
            pending_income += txn.amount

    forecast = build_cashflow_forecast(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=scheduled_bills,
        as_of=as_of,
        horizon_days=horizon_days,
        lookback_days=lookback_days,
    )

    # Known pending income may clear; never invent additional income.
    safe = _money(
        available
        + pending_income
        - pending_expenses
        - forecast.scheduled_bills_total
        - forecast.estimated_spend_total
        - minimum_buffer
    )

    return SafeToSpendResponse(
        customer_id=customer_id,
        as_of=as_of,
        horizon_days=horizon_days,
        available_balance=available,
        pending_expenses=_money(pending_expenses),
        pending_income=_money(pending_income),
        scheduled_bills_total=forecast.scheduled_bills_total,
        estimated_spend_total=forecast.estimated_spend_total,
        minimum_buffer=_money(minimum_buffer),
        safe_to_spend=safe,
    )


def compute_spending_drift(
    *,
    customer_id: str,
    transactions: list[Transaction],
    as_of: date,
    recent_days: int = 7,
    baseline_days: int = 28,
) -> SpendingDriftResponse:
    if recent_days < 1 or baseline_days < 1:
        raise ValueError("recent_days and baseline_days must be >= 1")

    recent_start = as_of - timedelta(days=recent_days)
    baseline_end = recent_start
    baseline_start = baseline_end - timedelta(days=baseline_days)

    recent = _posted_expense_total(
        transactions, customer_id, start=recent_start, end=as_of
    )
    # Normalize baseline to a recent-length comparable average * recent_days.
    baseline_raw = _posted_expense_total(
        transactions, customer_id, start=baseline_start, end=baseline_end
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
) -> WhatIfResponse:
    if purchase_amount <= 0:
        raise ValueError("purchase_amount must be positive")

    before = compute_safe_to_spend(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=scheduled_bills,
        as_of=as_of,
        horizon_days=horizon_days,
        lookback_days=lookback_days,
        minimum_buffer=minimum_buffer,
    )
    after_safe = _money(before.safe_to_spend - purchase_amount)
    affordable = after_safe >= ZERO

    # Apply purchase against the first non-credit account for min-balance check.
    cash_indexes = [
        idx
        for idx, a in enumerate(accounts)
        if "credit" not in a.account_type.strip().lower()
    ]
    target_idx = cash_indexes[0] if cash_indexes else 0
    adjusted_accounts = [
        Account(
            account_id=a.account_id,
            customer_id=a.customer_id,
            name=a.name,
            account_type=a.account_type,
            balance=(
                _money(a.balance - purchase_amount)
                if idx == target_idx
                else a.balance
            ),
            currency=a.currency,
        )
        for idx, a in enumerate(accounts)
    ]
    if not cash_indexes:
        projected_min = _money(-purchase_amount)
    else:
        forecast = build_cashflow_forecast(
            customer_id=customer_id,
            accounts=adjusted_accounts,
            transactions=transactions,
            scheduled_bills=scheduled_bills,
            as_of=as_of,
            horizon_days=horizon_days,
            lookback_days=lookback_days,
        )
        projected_min = forecast.min_balance

    note = (
        "Affordable within safe-to-spend after buffer, bills, and estimated spend."
        if affordable
        else "Exceeds safe-to-spend; would consume buffer or projected runway."
    )
    return WhatIfResponse(
        customer_id=customer_id,
        purchase_amount=_money(purchase_amount),
        description=description,
        safe_to_spend_before=before.safe_to_spend,
        safe_to_spend_after=after_safe,
        affordable=affordable,
        projected_min_balance_after=projected_min,
        note=note,
    )


def _posted_expense_total(
    transactions: list[Transaction],
    customer_id: str,
    *,
    start: date,
    end: date,
) -> Decimal:
    total = ZERO
    for txn in transactions:
        if txn.customer_id != customer_id:
            continue
        if txn.kind != TransactionKind.EXPENSE:
            continue
        if txn.status != TransactionStatus.POSTED:
            continue
        day = _as_utc_date(txn.posted_at)
        if start < day <= end:
            total += abs(txn.amount)
    return _money(total)
