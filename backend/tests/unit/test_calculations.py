"""Unit tests for deterministic financial calculations."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.domain.calculations import (
    build_linear_forecast,
    summarize_money_flow,
    total_available_balance,
)
from app.models.schemas import (
    Account,
    Transaction,
    TransactionKind,
    TransactionStatus,
)
from app.services.providers.mock import MockDataProvider


def _txn(
    *,
    transaction_id: str,
    amount: str,
    kind: TransactionKind,
    status: TransactionStatus,
    account_id: str = "a1",
    counterparty: str | None = None,
) -> Transaction:
    return Transaction(
        transaction_id=transaction_id,
        customer_id="c1",
        account_id=account_id,
        amount=Decimal(amount),
        kind=kind,
        status=status,
        description="t",
        category="c",
        posted_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        counterparty_account_id=counterparty,
    )


def test_total_available_balance_sums_accounts() -> None:
    accounts = [
        Account(
            account_id="a1",
            customer_id="c1",
            name="Checking",
            account_type="Checking",
            balance=Decimal("100.10"),
        ),
        Account(
            account_id="a2",
            customer_id="c1",
            name="Savings",
            account_type="Savings",
            balance=Decimal("50.20"),
        ),
    ]
    assert total_available_balance(accounts) == Decimal("150.30")


def test_total_available_balance_excludes_credit_cards() -> None:
    accounts = [
        Account(
            account_id="a1",
            customer_id="c1",
            name="Checking",
            account_type="Checking",
            balance=Decimal("100.00"),
        ),
        Account(
            account_id="cc",
            customer_id="c1",
            name="Card",
            account_type="Credit Card",
            balance=Decimal("-40.00"),
        ),
    ]
    assert total_available_balance(accounts) == Decimal("100.00")


def test_income_and_expenses_posted_only() -> None:
    txns = [
        _txn(
            transaction_id="1",
            amount="1000.00",
            kind=TransactionKind.INCOME,
            status=TransactionStatus.POSTED,
        ),
        _txn(
            transaction_id="2",
            amount="-200.00",
            kind=TransactionKind.EXPENSE,
            status=TransactionStatus.POSTED,
        ),
        _txn(
            transaction_id="3",
            amount="-50.00",
            kind=TransactionKind.EXPENSE,
            status=TransactionStatus.PENDING,
        ),
        _txn(
            transaction_id="4",
            amount="25.00",
            kind=TransactionKind.INCOME,
            status=TransactionStatus.PENDING,
        ),
    ]
    summary = summarize_money_flow("c1", txns)
    assert summary.income_posted == Decimal("1000.00")
    assert summary.expenses_posted == Decimal("200.00")
    assert summary.pending_expenses == Decimal("50.00")
    assert summary.pending_income == Decimal("25.00")
    assert summary.net_posted == Decimal("800.00")
    assert summary.net_including_pending == Decimal("775.00")
    assert summary.transfer_volume == Decimal("0.00")


def test_internal_transfers_excluded_from_income_and_expenses() -> None:
    txns = [
        _txn(
            transaction_id="out",
            amount="-300.00",
            kind=TransactionKind.TRANSFER,
            status=TransactionStatus.POSTED,
            account_id="checking",
            counterparty="savings",
        ),
        _txn(
            transaction_id="in",
            amount="300.00",
            kind=TransactionKind.TRANSFER,
            status=TransactionStatus.POSTED,
            account_id="savings",
            counterparty="checking",
        ),
        _txn(
            transaction_id="pay",
            amount="500.00",
            kind=TransactionKind.INCOME,
            status=TransactionStatus.POSTED,
        ),
    ]
    summary = summarize_money_flow("c1", txns)
    assert summary.income_posted == Decimal("500.00")
    assert summary.expenses_posted == Decimal("0.00")
    assert summary.transfer_volume == Decimal("300.00")
    assert summary.net_posted == Decimal("500.00")


def test_unpaired_transfer_volume_uses_absolute_amount() -> None:
    txns = [
        _txn(
            transaction_id="solo",
            amount="-150.00",
            kind=TransactionKind.TRANSFER,
            status=TransactionStatus.POSTED,
        )
    ]
    summary = summarize_money_flow("c1", txns)
    assert summary.transfer_volume == Decimal("150.00")


def test_forecast_is_deterministic() -> None:
    flow = summarize_money_flow(
        "c1",
        [
            _txn(
                transaction_id="1",
                amount="300.00",
                kind=TransactionKind.INCOME,
                status=TransactionStatus.POSTED,
            ),
            _txn(
                transaction_id="2",
                amount="-30.00",
                kind=TransactionKind.EXPENSE,
                status=TransactionStatus.PENDING,
            ),
        ],
    )
    # net_including_pending = 270 over 30 days => 9.00/day
    forecast = build_linear_forecast(
        "c1",
        Decimal("1000.00"),
        flow,
        lookback_days=30,
        horizon_days=2,
        as_of=date(2026, 11, 14),
        mode="mock",
    )
    assert forecast.daily_net_rate == Decimal("9.00")
    assert forecast.points[0].projected_balance == Decimal("1000.00")
    assert forecast.points[1].projected_balance == Decimal("1009.00")
    assert forecast.points[2].projected_balance == Decimal("1018.00")
    assert forecast.points[2].date == date(2026, 11, 16)


def test_forecast_rejects_invalid_lookback() -> None:
    flow = summarize_money_flow("c1", [])
    with pytest.raises(ValueError):
        build_linear_forecast(
            "c1",
            Decimal("0"),
            flow,
            lookback_days=0,
            horizon_days=1,
            as_of=date(2026, 11, 14),
            mode="mock",
        )


def test_mock_fixture_money_flow_expectations() -> None:
    provider = MockDataProvider()
    customer_id = "demo-customer-001"
    summary = summarize_money_flow(
        customer_id, provider.list_transactions(customer_id)
    )
    assert summary.income_posted == Decimal("3200.00")
    # Food 145.32 + Rent 980 + Transit 64.50 + Shopping 500 = 1689.82
    assert summary.expenses_posted == Decimal("1689.82")
    assert summary.pending_expenses == Decimal("52.10")
    assert summary.pending_income == Decimal("200.00")
    assert summary.net_posted == Decimal("1510.18")
    assert summary.net_including_pending == Decimal("1658.08")
    assert summary.transfer_volume == Decimal("300.00")
