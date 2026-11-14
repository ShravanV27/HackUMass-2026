"""Unit tests for Nessie → internal model mapping."""

from __future__ import annotations

from decimal import Decimal

from app.models.schemas import TransactionKind, TransactionStatus
from datetime import date

from app.services.nessie_mapper import (
    collect_account_transactions,
    collect_from_transaction_feed,
    collect_scheduled_bills,
    map_account,
    map_bill,
    map_deposit,
    map_feed_transaction,
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


def test_v2_created_at_is_stable_across_repeated_mapping() -> None:
    """Regression: missing classic date fields must not use datetime.now().

    TigerData PK is (transaction_id, posted_at). Unstable posted_at caused
    re-sync to insert duplicates (e.g. 200 → 400 rows).
    """
    raw = {
        "id": "txn_01m7stable",
        "kind": "purchase",
        "status": "completed",
        "medium": "balance",
        "amount_cents": 2500,
        "description": "Coffee",
        "created_at": "2026-10-01T12:34:56Z",
        "settle_at": "2026-10-01T12:34:56Z",
        "updated_at": "2026-10-01T12:34:56Z",
    }
    first = map_feed_transaction(
        raw, app_customer_id="demo-customer-001", account_id="acct_1"
    )
    second = map_feed_transaction(
        raw, app_customer_id="demo-customer-001", account_id="acct_1"
    )
    assert first.transaction_id == second.transaction_id == "purchase:txn_01m7stable"
    assert first.posted_at == second.posted_at
    assert first.posted_at.isoformat().startswith("2026-10-01T12:34:56")


def test_sync_upsert_key_stable_when_mapping_twice() -> None:
    """Same Nessie row must produce the same TigerData conflict key twice."""
    feed = [
        {
            "id": f"txn_{i}",
            "kind": "purchase",
            "status": "completed",
            "amount_cents": 100 + i,
            "created_at": f"2026-09-{(i % 28) + 1:02d}T10:00:00Z",
        }
        for i in range(5)
    ]
    first = collect_from_transaction_feed(
        app_customer_id="demo-customer-001",
        account_id="acct_1",
        feed=feed,
    )
    second = collect_from_transaction_feed(
        app_customer_id="demo-customer-001",
        account_id="acct_1",
        feed=feed,
    )
    keys_first = {(t.transaction_id, t.posted_at) for t in first}
    keys_second = {(t.transaction_id, t.posted_at) for t in second}
    assert keys_first == keys_second
    assert len(keys_first) == 5


def test_map_bill_upcoming_payment_date() -> None:
    bill = map_bill(
        {
            "_id": "bill-1",
            "status": "pending",
            "payee": "Verizon",
            "nickname": "Phone plan",
            "payment_amount": 45.0,
            "upcoming_payment_date": "2026-11-20",
            "recurring_date": 20,
            "account_id": "acc-1",
        },
        app_customer_id="demo-customer-001",
        as_of=date(2026, 11, 14),
    )
    assert bill is not None
    assert bill.bill_id == "bill:bill-1"
    assert bill.amount == Decimal("45.00")
    assert bill.next_due_date == date(2026, 11, 20)
    assert bill.category == "Utilities"
    assert bill.customer_id == "demo-customer-001"


def test_map_bill_v2_payment_amount_cents_and_recurring() -> None:
    bill = map_bill(
        {
            "id": "bill_01",
            "status": "recurring",
            "payee": "Landlord",
            "nickname": "Rent",
            "payment_amount_cents": 98000,
            "recurring_date": 5,
            "account_id": "acct_01",
        },
        app_customer_id="demo-customer-001",
        as_of=date(2026, 11, 14),
    )
    assert bill is not None
    assert bill.amount == Decimal("980.00")
    assert bill.next_due_date == date(2026, 12, 5)
    assert bill.category == "Housing"


def test_map_bill_skips_cancelled_and_zero() -> None:
    assert (
        map_bill(
            {
                "_id": "b1",
                "status": "cancelled",
                "payment_amount": 10,
                "upcoming_payment_date": "2026-11-20",
                "account_id": "a1",
            },
            app_customer_id="demo-customer-001",
            as_of=date(2026, 11, 14),
        )
        is None
    )
    assert (
        map_bill(
            {
                "_id": "b2",
                "status": "pending",
                "payment_amount": 0,
                "upcoming_payment_date": "2026-11-20",
                "account_id": "a1",
            },
            app_customer_id="demo-customer-001",
            as_of=date(2026, 11, 14),
        )
        is None
    )


def test_collect_scheduled_bills_filters() -> None:
    bills = collect_scheduled_bills(
        [
            {
                "_id": "ok",
                "status": "pending",
                "nickname": "Internet",
                "payment_amount": 70,
                "upcoming_payment_date": "2026-11-25",
                "account_id": "a1",
            },
            {
                "_id": "skip",
                "status": "completed",
                "payment_amount": 10,
                "upcoming_payment_date": "2026-11-18",
                "account_id": "a1",
            },
        ],
        app_customer_id="demo-customer-001",
        as_of=date(2026, 11, 14),
    )
    assert len(bills) == 1
    assert bills[0].name == "Internet"
