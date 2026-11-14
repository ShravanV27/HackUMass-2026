"""Pure, deterministic personal-finance calculations.

Rules:
- Income: positive posted amounts with kind=income
- Expenses: negative posted amounts with kind=expense (stored as signed values)
- Pending: included only when explicitly requested via including_pending
- Internal transfers: kind=transfer; excluded from income/expense totals;
  transfer_volume is the absolute sum of transfer legs / 2 (each transfer
  appears once per account, so volume uses abs(amount) summed then halved
  when both legs are present; if only one leg is present, use abs(amount))
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from app.models.schemas import (
    Account,
    ForecastPoint,
    ForecastSummary,
    MoneyFlowSummary,
    Transaction,
    TransactionKind,
    TransactionStatus,
)

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def total_available_balance(accounts: list[Account]) -> Decimal:
    """Sum of ledger balances across the customer's accounts."""
    return _money(sum((a.balance for a in accounts), ZERO))


def summarize_money_flow(
    customer_id: str,
    transactions: list[Transaction],
) -> MoneyFlowSummary:
    """Classify income, expenses, pending, and internal transfers."""
    income_posted = ZERO
    expenses_posted = ZERO
    pending_income = ZERO
    pending_expenses = ZERO
    transfer_abs = ZERO
    transfer_ids: set[str] = set()

    for txn in transactions:
        if txn.customer_id != customer_id:
            continue

        if txn.kind == TransactionKind.TRANSFER:
            # Pair legs by shared description+date when possible; otherwise
            # count each transfer amount once via abs and later halve if even.
            transfer_abs += abs(txn.amount)
            transfer_ids.add(txn.transaction_id)
            continue

        if txn.kind == TransactionKind.INCOME:
            if txn.status == TransactionStatus.POSTED:
                income_posted += txn.amount
            else:
                pending_income += txn.amount
            continue

        if txn.kind == TransactionKind.EXPENSE:
            # Expenses are stored as negative amounts.
            if txn.status == TransactionStatus.POSTED:
                expenses_posted += abs(txn.amount)
            else:
                pending_expenses += abs(txn.amount)

    # If an even number of transfer legs exist, assume paired internal moves.
    if len(transfer_ids) >= 2 and len(transfer_ids) % 2 == 0:
        transfer_volume = _money(transfer_abs / 2)
    else:
        transfer_volume = _money(transfer_abs)

    net_posted = _money(income_posted - expenses_posted)
    net_including_pending = _money(
        (income_posted + pending_income) - (expenses_posted + pending_expenses)
    )

    return MoneyFlowSummary(
        customer_id=customer_id,
        income_posted=_money(income_posted),
        expenses_posted=_money(expenses_posted),
        pending_expenses=_money(pending_expenses),
        pending_income=_money(pending_income),
        net_posted=net_posted,
        net_including_pending=net_including_pending,
        transfer_volume=transfer_volume,
    )


def build_linear_forecast(
    customer_id: str,
    starting_balance: Decimal,
    money_flow: MoneyFlowSummary,
    *,
    lookback_days: int,
    horizon_days: int,
    as_of: date,
    mode: str,
) -> ForecastSummary:
    """Project balance using average daily net including pending cash flows.

    daily_net_rate = net_including_pending / lookback_days
    Day n balance = starting_balance + n * daily_net_rate
    """
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    if horizon_days < 0:
        raise ValueError("horizon_days must be non-negative")

    daily_net_rate = _money(money_flow.net_including_pending / Decimal(lookback_days))
    points: list[ForecastPoint] = []
    for day in range(0, horizon_days + 1):
        projected = _money(starting_balance + (daily_net_rate * Decimal(day)))
        points.append(
            ForecastPoint(
                date=as_of + timedelta(days=day),
                projected_balance=projected,
            )
        )

    return ForecastSummary(
        customer_id=customer_id,
        starting_balance=_money(starting_balance),
        daily_net_rate=daily_net_rate,
        horizon_days=horizon_days,
        points=points,
        mode="mock" if mode == "mock" else "live",
    )
