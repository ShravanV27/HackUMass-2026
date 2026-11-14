"""Unit tests for Nessie → internal model mapping."""

from __future__ import annotations

from decimal import Decimal

from app.models.schemas import TransactionKind, TransactionStatus
from app.services.nessie_mapper import (
    collect_account_transactions,
    map_account,
    map_deposit,
    map_purchase,
    map_transfer,
    map_withdrawal,
)


def test_map_account() -> None:
    account = map_account(
        {
            "_id": "acc-1",
            "type": "Checking",
            "nickname": "Main",
            "balance": 1250.5,
            "customer_id": "nessie-cust",
        },
        app_customer_id="demo-customer-001",
    )
    assert account.account_id == "acc-1"
    assert account.customer_id == "demo-customer-001"
    assert account.balance == Decimal("1250.5")
    assert account.name == "Main"


def test_map_purchase_is_negative_expense() -> None:
    txn = map_purchase(
        {
            "_id": "p1",
            "amount": 42.1,
            "status": "completed",
            "description": "Coffee",
            "purchase_date": "2026-11-01",
        },
        app_customer_id="demo-customer-001",
        account_id="acc-1",
    )
    assert txn.transaction_id == "purchase:p1"
    assert txn.kind == TransactionKind.EXPENSE
    assert txn.status == TransactionStatus.POSTED
    assert txn.amount == Decimal("-42.1")


def test_map_deposit_is_positive_income() -> None:
    txn = map_deposit(
        {
            "_id": "d1",
            "amount": 500,
            "status": "pending",
            "description": "Paycheck",
            "transaction_date": "2026-11-02",
        },
        app_customer_id="demo-customer-001",
        account_id="acc-1",
    )
    assert txn.kind == TransactionKind.INCOME
    assert txn.status == TransactionStatus.PENDING
    assert txn.amount == Decimal("500")


def test_map_withdrawal_expense() -> None:
    txn = map_withdrawal(
        {
            "_id": "w1",
            "amount": 20,
            "status": "completed",
            "transaction_date": "2026-11-03",
        },
        app_customer_id="demo-customer-001",
        account_id="acc-1",
    )
    assert txn.kind == TransactionKind.EXPENSE
    assert txn.amount == Decimal("-20")


def test_map_transfer_direction() -> None:
    outgoing = map_transfer(
        {
            "_id": "t1",
            "amount": 100,
            "status": "completed",
            "payer_id": "acc-1",
            "payee_id": "acc-2",
            "transaction_date": "2026-11-04",
        },
        app_customer_id="demo-customer-001",
        account_id="acc-1",
    )
    incoming = map_transfer(
        {
            "_id": "t1",
            "amount": 100,
            "status": "completed",
            "payer_id": "acc-1",
            "payee_id": "acc-2",
            "transaction_date": "2026-11-04",
        },
        app_customer_id="demo-customer-001",
        account_id="acc-2",
    )
    assert outgoing.kind == TransactionKind.TRANSFER
    assert outgoing.amount == Decimal("-100")
    assert incoming.amount == Decimal("100")


def test_collect_account_transactions_combines_types() -> None:
    txns = collect_account_transactions(
        app_customer_id="demo-customer-001",
        account_id="acc-1",
        purchases=[{"_id": "p1", "amount": 10, "status": "completed", "purchase_date": "2026-11-01"}],
        deposits=[{"_id": "d1", "amount": 50, "status": "completed", "transaction_date": "2026-11-01"}],
        withdrawals=[],
        transfers=[],
    )
    assert len(txns) == 2
    kinds = {t.kind for t in txns}
    assert kinds == {TransactionKind.EXPENSE, TransactionKind.INCOME}
