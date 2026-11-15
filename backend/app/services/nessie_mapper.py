"""Map Nessie JSON payloads into internal Account / Transaction models."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from app.models.schemas import (
    Account,
    ScheduledBill,
    Transaction,
    TransactionKind,
    TransactionStatus,
)

_CENT = Decimal("0.01")
_HUNDRED = Decimal("100")
# Stable sentinel for Nessie rows with no parseable timestamp. ``datetime.now()``
# made the TigerData PK ``(transaction_id, posted_at)`` change on every sync and
# pulled undated activity into the current lookback window.
UNDATED_POSTED_AT = datetime(1970, 1, 1, tzinfo=timezone.utc)


def map_account(raw: dict[str, Any], *, app_customer_id: str) -> Account:
    account_id = str(raw.get("_id") or raw.get("id") or "")
    if not account_id:
        raise ValueError("Nessie account missing _id")
    name = str(raw.get("nickname") or raw.get("type") or "Account")
    account_type = str(raw.get("type") or "Checking")
    # Nessie v2 uses integer balance_cents; classic API uses dollar `balance`.
    balance = _money_field(
        raw,
        dollar_keys=("balance",),
        cents_keys=(
            "balance_cents",
            "current_balance_cents",
            "available_balance_cents",
        ),
    )
    # App convention: Account.balance is summed as available funds. Credit-card
    # balances from Nessie are liabilities (amount owed) — store as negative.
    if _is_credit_card(account_type) and balance > 0:
        balance = -balance
    return Account(
        account_id=account_id,
        customer_id=app_customer_id,
        name=name,
        account_type=account_type,
        balance=balance,
        currency="USD",
    )


def map_purchase(
    raw: dict[str, Any],
    *,
    app_customer_id: str,
    account_id: str,
) -> Transaction:
    amount = abs(_transaction_amount(raw))
    return Transaction(
        transaction_id=_txn_id(raw, prefix="purchase"),
        customer_id=app_customer_id,
        account_id=account_id,
        amount=-amount,
        kind=TransactionKind.EXPENSE,
        status=_status(raw.get("status")),
        description=str(raw.get("description") or "Purchase"),
        category="Purchase",
        posted_at=_transaction_posted_at(raw),
        counterparty_account_id=None,
    )


def map_deposit(
    raw: dict[str, Any],
    *,
    app_customer_id: str,
    account_id: str,
) -> Transaction:
    amount = abs(_transaction_amount(raw))
    return Transaction(
        transaction_id=_txn_id(raw, prefix="deposit"),
        customer_id=app_customer_id,
        account_id=account_id,
        amount=amount,
        kind=TransactionKind.INCOME,
        status=_status(raw.get("status")),
        description=str(raw.get("description") or "Deposit"),
        category="Income",
        posted_at=_transaction_posted_at(raw),
        counterparty_account_id=None,
    )


def map_withdrawal(
    raw: dict[str, Any],
    *,
    app_customer_id: str,
    account_id: str,
) -> Transaction:
    amount = abs(_transaction_amount(raw))
    return Transaction(
        transaction_id=_txn_id(raw, prefix="withdrawal"),
        customer_id=app_customer_id,
        account_id=account_id,
        amount=-amount,
        kind=TransactionKind.EXPENSE,
        status=_status(raw.get("status")),
        description=str(raw.get("description") or "Withdrawal"),
        category="Withdrawal",
        posted_at=_transaction_posted_at(raw),
        counterparty_account_id=None,
    )


def map_transfer(
    raw: dict[str, Any],
    *,
    app_customer_id: str,
    account_id: str,
) -> Transaction:
    amount = abs(_transaction_amount(raw))
    payer = str(raw.get("payer_id") or "")
    payee = str(raw.get("payee_id") or "")
    if payer == account_id:
        signed = -amount
        counterparty = payee or None
    elif payee == account_id:
        signed = amount
        counterparty = payer or None
    else:
        # Listed under this account without clear direction — treat as outgoing.
        signed = -amount
        counterparty = payee or payer or None
    return Transaction(
        transaction_id=_txn_id(raw, prefix="transfer"),
        customer_id=app_customer_id,
        account_id=account_id,
        amount=signed,
        kind=TransactionKind.TRANSFER,
        status=_status(raw.get("status")),
        description=str(raw.get("description") or "Transfer"),
        category="Transfer",
        posted_at=_transaction_posted_at(raw),
        counterparty_account_id=counterparty,
    )


def map_feed_transaction(
    raw: dict[str, Any],
    *,
    app_customer_id: str,
    account_id: str,
) -> Transaction:
    """Map a row from GET /accounts/{id}/transactions (Nessie v2 feed)."""
    type_text = str(
        raw.get("type") or raw.get("transaction_type") or raw.get("kind") or ""
    ).strip().lower()

    if type_text in {"deposit", "credit", "income", "payroll", "refund"}:
        return map_deposit(
            raw, app_customer_id=app_customer_id, account_id=account_id
        )
    if type_text in {"purchase", "merchant"}:
        return map_purchase(
            raw, app_customer_id=app_customer_id, account_id=account_id
        )
    if type_text in {"withdrawal", "debit", "payment", "bill_payment", "atm"}:
        return map_withdrawal(
            raw, app_customer_id=app_customer_id, account_id=account_id
        )
    if "transfer" in type_text:
        return map_transfer(
            raw, app_customer_id=app_customer_id, account_id=account_id
        )

    # Signed-amount heuristic when type is missing/unknown.
    amount = _transaction_amount(raw)
    if amount >= 0:
        return map_deposit(
            raw, app_customer_id=app_customer_id, account_id=account_id
        )
    purchase_like = dict(raw)
    # Prefer dollar form for the recursive purchase mapper.
    purchase_like["amount"] = abs(amount)
    purchase_like.pop("amount_cents", None)
    return map_purchase(
        purchase_like, app_customer_id=app_customer_id, account_id=account_id
    )


def collect_account_transactions(
    *,
    app_customer_id: str,
    account_id: str,
    purchases: list[dict[str, Any]],
    deposits: list[dict[str, Any]],
    withdrawals: list[dict[str, Any]],
    transfers: list[dict[str, Any]],
) -> list[Transaction]:
    txns: list[Transaction] = []
    for row in purchases:
        txns.append(
            map_purchase(row, app_customer_id=app_customer_id, account_id=account_id)
        )
    for row in deposits:
        txns.append(
            map_deposit(row, app_customer_id=app_customer_id, account_id=account_id)
        )
    for row in withdrawals:
        txns.append(
            map_withdrawal(row, app_customer_id=app_customer_id, account_id=account_id)
        )
    for row in transfers:
        txns.append(
            map_transfer(row, app_customer_id=app_customer_id, account_id=account_id)
        )
    return txns


def collect_from_transaction_feed(
    *,
    app_customer_id: str,
    account_id: str,
    feed: list[dict[str, Any]],
) -> list[Transaction]:
    return [
        map_feed_transaction(
            row, app_customer_id=app_customer_id, account_id=account_id
        )
        for row in feed
    ]


def map_bill(
    raw: dict[str, Any],
    *,
    app_customer_id: str,
    as_of: date | None = None,
) -> ScheduledBill | None:
    """Map a Nessie bill into a ScheduledBill, or None if not an upcoming obligation.

    Skips cancelled/completed bills. Uses upcoming_payment_date when present;
    otherwise derives the next due date from recurring_date (day-of-month).
    """
    bill_id = str(raw.get("_id") or raw.get("id") or "")
    if not bill_id:
        raise ValueError("Nessie bill missing _id")

    status = str(raw.get("status") or "").strip().lower()
    if status in {"cancelled", "canceled", "completed", "paid"}:
        return None

    account_id = str(raw.get("account_id") or raw.get("accountId") or "")
    if not account_id:
        return None

    amount = abs(
        _money_field(
            raw,
            dollar_keys=("payment_amount", "amount"),
            cents_keys=("payment_amount_cents", "amount_cents"),
        )
    )
    if amount <= 0:
        return None

    today = as_of or datetime.now(tz=timezone.utc).date()
    next_due = _bill_next_due_date(raw, today=today)
    if next_due is None:
        return None

    name = str(
        raw.get("nickname")
        or raw.get("payee")
        or raw.get("description")
        or "Bill"
    )
    category = _bill_category(name, raw)
    cadence = "monthly" if raw.get("recurring_date") is not None else "one_time"

    return ScheduledBill(
        bill_id=f"bill:{bill_id}",
        customer_id=app_customer_id,
        account_id=account_id,
        name=name,
        amount=_quantize_money(amount),
        category=category,
        next_due_date=next_due,
        cadence=cadence,
    )


def collect_scheduled_bills(
    rows: list[dict[str, Any]],
    *,
    app_customer_id: str,
    as_of: date | None = None,
) -> list[ScheduledBill]:
    bills: list[ScheduledBill] = []
    for row in rows:
        mapped = map_bill(row, app_customer_id=app_customer_id, as_of=as_of)
        if mapped is not None:
            bills.append(mapped)
    return bills


def _bill_next_due_date(raw: dict[str, Any], *, today: date) -> date | None:
    for key in ("upcoming_payment_date", "payment_date", "due_date"):
        value = raw.get(key)
        if value in (None, ""):
            continue
        parsed = _parse_dt(value).date()
        if parsed > today:
            return parsed
        # Past one-time payment dates are not upcoming obligations.
        if raw.get("recurring_date") is None and key != "upcoming_payment_date":
            continue

    recurring = raw.get("recurring_date")
    if recurring is None or recurring == "":
        return None
    try:
        day = int(recurring)
    except (TypeError, ValueError):
        return None
    if day < 1 or day > 31:
        return None

    # Next occurrence of this day-of-month strictly after today.
    year, month = today.year, today.month
    for _ in range(0, 14):
        candidate_day = min(day, _days_in_month(year, month))
        candidate = date(year, month, candidate_day)
        if candidate > today:
            return candidate
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return None


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    return (nxt - timedelta(days=1)).day


def _bill_category(name: str, raw: dict[str, Any]) -> str:
    explicit = raw.get("category")
    if explicit:
        return str(explicit)
    text = name.strip().lower()
    if any(token in text for token in ("rent", "mortgage", "housing")):
        return "Housing"
    if any(token in text for token in ("internet", "phone", "utility", "electric", "gas", "water")):
        return "Utilities"
    if any(token in text for token in ("insurance",)):
        return "Insurance"
    return "Bills"


def _txn_id(raw: dict[str, Any], *, prefix: str) -> str:
    raw_id = str(raw.get("_id") or raw.get("id") or "")
    if not raw_id:
        raise ValueError(f"Nessie {prefix} missing _id")
    return f"{prefix}:{raw_id}"


def _is_credit_card(account_type: str) -> bool:
    text = account_type.strip().lower()
    return "credit" in text


def _to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid decimal value: {value!r}") from exc


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _money_field(
    raw: dict[str, Any],
    *,
    dollar_keys: tuple[str, ...],
    cents_keys: tuple[str, ...],
) -> Decimal:
    """Prefer classic dollar fields; otherwise convert integer *_cents / 100."""
    for key in dollar_keys:
        if key in raw and raw[key] is not None:
            return _quantize_money(_to_decimal(raw[key]))
    for key in cents_keys:
        if key in raw and raw[key] is not None:
            return _quantize_money(_to_decimal(raw[key]) / _HUNDRED)
    return Decimal("0.00")


def _transaction_amount(raw: dict[str, Any]) -> Decimal:
    return _money_field(
        raw,
        dollar_keys=("amount",),
        cents_keys=("amount_cents", "payment_amount_cents"),
    )


def _status(value: Any) -> TransactionStatus:
    text = str(value or "completed").strip().lower()
    if text in {"pending", "cancelled", "canceled", "recurring"}:
        # Treat cancelled/recurring as pending so they do not inflate posted totals.
        return TransactionStatus.PENDING
    return TransactionStatus.POSTED


def _transaction_posted_at(raw: dict[str, Any]) -> datetime:
    """Stable event time for TigerData PK ``(transaction_id, posted_at)``.

    Nessie v2 ``TransactionDTO`` exposes ``created_at`` / ``settle_at``.
    ``updated_at`` is not an event time — a later edit would move the
    TigerData key — so it is ignored. Classic payloads may use
    purchase/transaction dates. Missing dates use a stable sentinel rather
    than ``datetime.now()``.
    """
    for key in (
        "purchase_date",
        "transaction_date",
        "date",
        "posted_at",
        "created_at",
        "settle_at",
    ):
        value = raw.get(key)
        if value not in (None, ""):
            return _parse_dt(value)
    return UNDATED_POSTED_AT


def _parse_dt(value: Any) -> datetime:
    if value is None or value == "":
        return UNDATED_POSTED_AT
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    # Nessie often returns YYYY-MM-DD
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return UNDATED_POSTED_AT
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
