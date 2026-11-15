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

from dataclasses import dataclass
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


def _is_credit_card(account_type: str) -> bool:
    return "credit" in account_type.strip().lower()


def total_available_balance(accounts: list[Account]) -> Decimal:
    """Spendable cash across asset accounts.

    Credit-card balances are liabilities (and may be stored negative after Nessie
    mapping). They must never count as available cash for runway / safe-to-spend.
    """
    return _money(
        sum(
            (a.balance for a in accounts if not _is_credit_card(a.account_type)),
            ZERO,
        )
    )


def spendable_cash_balance(accounts: list[Account]) -> Decimal:
    """Alias for total_available_balance — cash only, no credit lines."""
    return total_available_balance(accounts)


def credit_account_ids(
    accounts: list[Account], customer_id: str | None = None
) -> set[str]:
    """Account ids whose balances are liabilities, not spendable cash."""
    return {
        account.account_id
        for account in accounts
        if (customer_id is None or account.customer_id == customer_id)
        and _is_credit_card(account.account_type)
    }


def is_card_payment(txn: Transaction, credit_ids: set[str]) -> bool:
    """Cash-account row that pays a credit card, not a purchase on the card."""
    if not credit_ids or txn.account_id in credit_ids:
        return False
    if txn.kind not in {TransactionKind.EXPENSE, TransactionKind.TRANSFER}:
        return False
    if txn.counterparty_account_id and txn.counterparty_account_id in credit_ids:
        return True
    text = f"{txn.description} {txn.category}".lower()
    if "payment thank you" in text or "autopay" in text:
        return True
    if "payment" in text and any(
        token in text for token in ("card", "visa", "mastercard", "amex", "discover")
    ):
        return True
    return False


@dataclass(frozen=True)
class CreditCardCashFlow:
    """Spending counts once. Cash outflow is the payment, not the swipe."""

    spending: Decimal
    cash_outflow: Decimal
    card_purchases: Decimal
    card_payments: Decimal
    unmatched_payment: Decimal


def credit_card_cash_flow(
    customer_id: str,
    transactions: list[Transaction],
    accounts: list[Account],
) -> CreditCardCashFlow:
    """Split card swipes from the cash that later leaves checking.

    Purchases on a credit account count as spending and do not reduce cash.
    A checking/savings payment counts as cash outflow. When that payment
    matches card purchases in this set, it is not a second spend. A payment
    with no matching purchases still counts (the swipes are outside the set).
    Other cash-account expenses count as both spending and cash outflow.
    """
    credit_ids = credit_account_ids(accounts, customer_id)
    card_purchases = ZERO
    card_payments = ZERO
    other_cash = ZERO
    for txn in transactions:
        if txn.customer_id != customer_id or txn.status != TransactionStatus.POSTED:
            continue
        amount = abs(txn.amount)
        if txn.account_id in credit_ids and txn.kind == TransactionKind.EXPENSE:
            card_purchases += amount
            continue
        if (
            is_card_payment(txn, credit_ids)
            and txn.kind == TransactionKind.EXPENSE
            and txn.amount < 0
        ):
            card_payments += amount
            continue
        if txn.account_id not in credit_ids and txn.kind == TransactionKind.EXPENSE:
            other_cash += amount
    card_purchases = _money(card_purchases)
    card_payments = _money(card_payments)
    other_cash = _money(other_cash)
    matched = card_purchases if card_purchases <= card_payments else card_payments
    unmatched = _money(card_payments - matched)
    return CreditCardCashFlow(
        spending=_money(other_cash + card_purchases + unmatched),
        cash_outflow=_money(other_cash + card_payments),
        card_purchases=card_purchases,
        card_payments=card_payments,
        unmatched_payment=unmatched,
    )


def summarize_money_flow(
    customer_id: str,
    transactions: list[Transaction],
    accounts: list[Account] | None = None,
) -> MoneyFlowSummary:
    """Classify income, expenses, pending, and internal transfers."""
    income_posted = ZERO
    expenses_posted = ZERO
    pending_income = ZERO
    pending_expenses = ZERO
    transfer_abs = ZERO
    transfer_ids: set[str] = set()
    card_ids = (
        credit_account_ids(accounts, customer_id) if accounts is not None else None
    )

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
            # Card swipes are not cash leaving the bank. The payment is.
            # Without an account list, keep the historical "every expense" sum.
            if card_ids is not None and txn.account_id in card_ids:
                continue
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
