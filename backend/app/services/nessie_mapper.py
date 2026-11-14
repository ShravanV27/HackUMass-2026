"""Map Nessie JSON payloads into internal Account / Transaction models."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models.schemas import (
    Account,
    Transaction,
    TransactionKind,
    TransactionStatus,
)


def map_account(raw: dict[str, Any], *, app_customer_id: str) -> Account:
    account_id = str(raw.get("_id") or raw.get("id") or "")
    if not account_id:
        raise ValueError("Nessie account missing _id")
    balance = _to_decimal(raw.get("balance", 0))
    name = str(raw.get("nickname") or raw.get("type") or "Account")
    account_type = str(raw.get("type") or "Checking")
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
    amount = abs(_to_decimal(raw.get("amount", 0)))
    return Transaction(
        transaction_id=_txn_id(raw, prefix="purchase"),
        customer_id=app_customer_id,
        account_id=account_id,
        amount=-amount,
        kind=TransactionKind.EXPENSE,
        status=_status(raw.get("status")),
        description=str(raw.get("description") or "Purchase"),
        category="Purchase",
        posted_at=_parse_dt(raw.get("purchase_date") or raw.get("transaction_date")),
        counterparty_account_id=None,
    )


def map_deposit(
    raw: dict[str, Any],
    *,
    app_customer_id: str,
    account_id: str,
) -> Transaction:
    amount = abs(_to_decimal(raw.get("amount", 0)))
    return Transaction(
        transaction_id=_txn_id(raw, prefix="deposit"),
        customer_id=app_customer_id,
        account_id=account_id,
        amount=amount,
        kind=TransactionKind.INCOME,
        status=_status(raw.get("status")),
        description=str(raw.get("description") or "Deposit"),
        category="Income",
        posted_at=_parse_dt(raw.get("transaction_date") or raw.get("purchase_date")),
        counterparty_account_id=None,
    )


def map_withdrawal(
    raw: dict[str, Any],
    *,
    app_customer_id: str,
    account_id: str,
) -> Transaction:
    amount = abs(_to_decimal(raw.get("amount", 0)))
    return Transaction(
        transaction_id=_txn_id(raw, prefix="withdrawal"),
        customer_id=app_customer_id,
        account_id=account_id,
        amount=-amount,
        kind=TransactionKind.EXPENSE,
        status=_status(raw.get("status")),
        description=str(raw.get("description") or "Withdrawal"),
        category="Withdrawal",
        posted_at=_parse_dt(raw.get("transaction_date")),
        counterparty_account_id=None,
    )


def map_transfer(
    raw: dict[str, Any],
    *,
    app_customer_id: str,
    account_id: str,
) -> Transaction:
    amount = abs(_to_decimal(raw.get("amount", 0)))
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
        posted_at=_parse_dt(raw.get("transaction_date")),
        counterparty_account_id=counterparty,
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


def _txn_id(raw: dict[str, Any], *, prefix: str) -> str:
    raw_id = str(raw.get("_id") or raw.get("id") or "")
    if not raw_id:
        raise ValueError(f"Nessie {prefix} missing _id")
    return f"{prefix}:{raw_id}"


def _to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid decimal value: {value!r}") from exc


def _status(value: Any) -> TransactionStatus:
    text = str(value or "completed").strip().lower()
    if text in {"pending", "cancelled", "canceled", "recurring"}:
        # Treat cancelled/recurring as pending so they do not inflate posted totals.
        return TransactionStatus.PENDING
    return TransactionStatus.POSTED


def _parse_dt(value: Any) -> datetime:
    if value is None or value == "":
        return datetime.now(tz=timezone.utc)
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
        return datetime.now(tz=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
