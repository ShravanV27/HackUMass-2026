"""Unit tests for Nessie → internal model mapping."""

from __future__ import annotations

from decimal import Decimal

from app.models.schemas import TransactionKind, TransactionStatus
from app.services.nessie_mapper import (
    collect_account_transactions,
    collect_from_transaction_feed,
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
    assert account.balance == Decimal("1250.50")
    assert account.name == "Main"


def test_map_account_v2_balance_cents() -> None:
    account = map_account(
        {
            "id": "acct_01m7djs39b2adt8vfg8ztzpw0f",
            "type": "Checking",
            "nickname": "Everyday Checking",
            "balance_cents": 245075,
            "customer_id": "cust_01abc",
        },
        app_customer_id="demo-customer-001",
    )
    assert account.account_id == "acct_01m7djs39b2adt8vfg8ztzpw0f"
    assert account.balance == Decimal("2450.75")


def test_map_account_v2_prefers_dollar_balance_when_present() -> None:
    account = map_account(
        {
            "id": "acct_01",
            "type": "Savings",
            "nickname": "Save",
            "balance": "100.00",
            "balance_cents": 999999,
        },
        app_customer_id="demo-customer-001",
    )
    assert account.balance == Decimal("100.00")


def test_map_credit_card_balance_as_liability() -> None:
    """Credit balances are amount owed — negative so available funds net correctly."""
    account = map_account(
        {
            "id": "acct_cc_01",
            "type": "Credit Card",
            "nickname": "Travel Card",
            "balance_cents": 152599,
        },
        app_customer_id="demo-customer-001",
    )
    assert account.account_type == "Credit Card"
    assert account.balance == Decimal("-1525.99")


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


def test_collect_from_v2_transaction_feed() -> None:
    txns = collect_from_transaction_feed(
        app_customer_id="demo-customer-001",
        account_id="acct_01abc",
        feed=[
            {
                "id": "txn_w",
                "type": "withdrawal",
                "amount": 12.5,
                "status": "completed",
                "description": "Lunch",
                "transaction_date": "2026-11-01",
            },
            {
                "id": "txn_d",
                "type": "deposit",
                "amount": 200,
                "status": "completed",
                "transaction_date": "2026-11-02",
            },
        ],
    )
    assert len(txns) == 2
    by_id = {t.transaction_id: t for t in txns}
    assert by_id["withdrawal:txn_w"].amount == Decimal("-12.50")
    assert by_id["deposit:txn_d"].amount == Decimal("200.00")


def test_v2_transaction_amount_cents() -> None:
    withdrawal = map_withdrawal(
        {
            "id": "txn_01",
            "type": "withdrawal",
            "amount_cents": 1299,
            "status": "completed",
            "description": "Coffee",
            "transaction_date": "2026-11-01",
        },
        app_customer_id="demo-customer-001",
        account_id="acct_01abc",
    )
    deposit = map_deposit(
        {
            "id": "txn_02",
            "type": "deposit",
            "amount_cents": 320000,
            "status": "completed",
            "transaction_date": "2026-11-02",
        },
        app_customer_id="demo-customer-001",
        account_id="acct_01abc",
    )
    assert withdrawal.amount == Decimal("-12.99")
    assert deposit.amount == Decimal("3200.00")


def test_v2_feed_with_amount_cents() -> None:
    txns = collect_from_transaction_feed(
        app_customer_id="demo-customer-001",
        account_id="acct_01abc",
        feed=[
            {
                "id": "txn_p",
                "type": "purchase",
                "amount_cents": 4599,
                "status": "completed",
                "description": "Groceries",
                "transaction_date": "2026-11-03",
            }
        ],
    )
    assert txns[0].amount == Decimal("-45.99")
    assert txns[0].kind == TransactionKind.EXPENSE
