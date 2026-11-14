"""In-memory Demo Lab mutations for mock mode (no Nessie writes)."""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from app.models.schemas import (
    Account,
    ScheduledBill,
    Transaction,
    TransactionKind,
    TransactionStatus,
)

_FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "providers"
    / "fixtures"
    / "demo_customer.json"
)

_LOCK = threading.Lock()
_STATE: dict[str, dict[str, Any]] | None = None
_COUNTER = 0

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def clear_mock_demo_state() -> None:
    global _STATE, _COUNTER
    with _LOCK:
        _STATE = None
        _COUNTER = 0


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _load_fixture() -> dict[str, Any]:
    with _FIXTURE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _ensure_state(customer_id: str) -> dict[str, Any]:
    global _STATE
    if _STATE is None:
        fixture = _load_fixture()
        _STATE = {
            "customer_id": fixture["customer_id"],
            "accounts": [Account.model_validate(row) for row in fixture["accounts"]],
            "transactions": [
                Transaction.model_validate(row) for row in fixture["transactions"]
            ],
            "scheduled_bills": [
                ScheduledBill.model_validate(row)
                for row in fixture.get("scheduled_bills", [])
            ],
        }
    if _STATE["customer_id"] != customer_id:
        return _STATE
    return _STATE


def list_accounts(customer_id: str) -> list[Account] | None:
    with _LOCK:
        if _STATE is None:
            return None
        state = _ensure_state(customer_id)
        if state["customer_id"] != customer_id:
            return []
        return list(state["accounts"])


def list_transactions(customer_id: str) -> list[Transaction] | None:
    with _LOCK:
        if _STATE is None:
            return None
        state = _ensure_state(customer_id)
        if state["customer_id"] != customer_id:
            return []
        return list(state["transactions"])


def list_scheduled_bills(customer_id: str) -> list[ScheduledBill] | None:
    with _LOCK:
        if _STATE is None:
            return None
        state = _ensure_state(customer_id)
        if state["customer_id"] != customer_id:
            return []
        return list(state["scheduled_bills"])


def apply_purchase(
    *,
    customer_id: str,
    account_id: str,
    amount: Decimal,
    description: str,
    status: str,
) -> str:
    global _COUNTER
    with _LOCK:
        state = _ensure_state(customer_id)
        _COUNTER += 1
        txn_id = f"mock-purchase-{_COUNTER}"
        posted = status.lower() == "posted"
        _adjust_balance(state, account_id, -abs(amount), apply=posted)
        state["transactions"].insert(
            0,
            Transaction(
                transaction_id=txn_id,
                customer_id=customer_id,
                account_id=account_id,
                amount=_money(-abs(amount)),
                kind=TransactionKind.EXPENSE,
                status=(
                    TransactionStatus.POSTED if posted else TransactionStatus.PENDING
                ),
                description=description or "Demo Lab purchase",
                category="Shopping",
                posted_at=datetime.now(tz=timezone.utc),
            ),
        )
        return txn_id


def apply_deposit(
    *,
    customer_id: str,
    account_id: str,
    amount: Decimal,
    description: str,
    status: str,
) -> str:
    global _COUNTER
    with _LOCK:
        state = _ensure_state(customer_id)
        _COUNTER += 1
        txn_id = f"mock-deposit-{_COUNTER}"
        posted = status.lower() == "posted"
        _adjust_balance(state, account_id, abs(amount), apply=posted)
        state["transactions"].insert(
            0,
            Transaction(
                transaction_id=txn_id,
                customer_id=customer_id,
                account_id=account_id,
                amount=_money(abs(amount)),
                kind=TransactionKind.INCOME,
                status=(
                    TransactionStatus.POSTED if posted else TransactionStatus.PENDING
                ),
                description=description or "Demo Lab deposit",
                category="Income",
                posted_at=datetime.now(tz=timezone.utc),
            ),
        )
        return txn_id


def apply_bill(
    *,
    customer_id: str,
    account_id: str,
    payee: str,
    amount: Decimal,
    payment_date: date | None,
    recurring_day: int | None,
) -> str:
    global _COUNTER
    with _LOCK:
        state = _ensure_state(customer_id)
        _COUNTER += 1
        bill_id = f"mock-bill-{_COUNTER}"
        due = payment_date or (date.today() + timedelta(days=7))
        cadence = "monthly" if recurring_day is not None else "one_time"
        state["scheduled_bills"].append(
            ScheduledBill(
                bill_id=bill_id,
                customer_id=customer_id,
                account_id=account_id,
                name=payee or "Demo bill",
                amount=_money(abs(amount)),
                category="Bills",
                next_due_date=due,
                cadence=cadence,
            )
        )
        return bill_id


def _adjust_balance(
    state: dict[str, Any], account_id: str, delta: Decimal, *, apply: bool
) -> None:
    if not apply:
        return
    accounts: list[Account] = state["accounts"]
    updated: list[Account] = []
    found = False
    for account in accounts:
        if account.account_id == account_id:
            found = True
            updated.append(
                Account(
                    account_id=account.account_id,
                    customer_id=account.customer_id,
                    name=account.name,
                    account_type=account.account_type,
                    balance=_money(account.balance + delta),
                    currency=account.currency,
                )
            )
        else:
            updated.append(account)
    if not found:
        raise ValueError(f"Unknown account_id: {account_id}")
    state["accounts"] = updated
