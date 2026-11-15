"""Credit-card purchases are spending; cash leaves when the card is paid."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.domain.calculations import credit_card_cash_flow, summarize_money_flow
from app.domain.predictive_engine import build_engine_forecast
from app.models.schemas import Account, Transaction, TransactionKind, TransactionStatus
from app.services.analytics_service import spending_window_summary
from app.services.providers.base import DataProvider


AS_OF = date(2026, 11, 14)


def _account(account_id: str, account_type: str, balance: str) -> Account:
    return Account(
        account_id=account_id,
        customer_id="c1",
        name=account_type,
        account_type=account_type,
        balance=Decimal(balance),
    )


def _txn(
    txn_id: str,
    account_id: str,
    amount: str,
    *,
    description: str,
    category: str,
    day: int,
    counterparty: str | None = None,
) -> Transaction:
    return Transaction(
        transaction_id=txn_id,
        customer_id="c1",
        account_id=account_id,
        amount=Decimal(amount),
        kind=TransactionKind.EXPENSE,
        status=TransactionStatus.POSTED,
        description=description,
        category=category,
        posted_at=datetime(2026, 11, day, 12, tzinfo=timezone.utc),
        counterparty_account_id=counterparty,
    )


def _accounts() -> list[Account]:
    return [
        _account("checking", "Checking", "2000.00"),
        _account("card", "Credit Card", "-400.00"),
    ]


def test_purchase_then_payment_counts_spending_once() -> None:
    txns = [
        _txn("swipe", "card", "-80.00", description="Groceries", category="Food", day=2),
        _txn(
            "pay",
            "checking",
            "-80.00",
            description="Credit card payment",
            category="Card Payment",
            day=10,
            counterparty="card",
        ),
    ]
    flow = credit_card_cash_flow("c1", txns, _accounts())
    assert flow.spending == Decimal("80.00")
    assert flow.cash_outflow == Decimal("80.00")
    assert flow.card_purchases == Decimal("80.00")
    assert flow.card_payments == Decimal("80.00")

    forecast = build_engine_forecast(
        customer_id="c1",
        accounts=_accounts(),
        transactions=txns,
        scheduled_bills=[],
        as_of=AS_OF,
        horizon_days=1,
        lookback_days=30,
    )
    # Cash runway uses the payment only, not swipe + payment.
    assert forecast.daily_estimated_spend == Decimal("2.67")
    assert any("Credit-card purchases" in note for note in forecast.assumptions)


def test_payment_without_purchases_in_window_still_counts() -> None:
    txns = [
        _txn(
            "pay",
            "checking",
            "-125.00",
            description="Visa payment thank you",
            category="Card Payment",
            day=8,
        )
    ]
    flow = credit_card_cash_flow("c1", txns, _accounts())
    assert flow.card_purchases == Decimal("0.00")
    assert flow.spending == Decimal("125.00")
    assert flow.cash_outflow == Decimal("125.00")
    assert flow.unmatched_payment == Decimal("125.00")

    forecast = build_engine_forecast(
        customer_id="c1",
        accounts=_accounts(),
        transactions=txns,
        scheduled_bills=[],
        as_of=AS_OF,
        horizon_days=1,
        lookback_days=30,
    )
    assert forecast.daily_estimated_spend == Decimal("4.17")


def test_mixed_accounts_do_not_double_count() -> None:
    txns = [
        _txn("debit", "checking", "-20.00", description="Coffee", category="Food", day=3),
        _txn("swipe", "card", "-50.00", description="Hardware", category="Shopping", day=4),
        _txn(
            "pay",
            "checking",
            "-50.00",
            description="Credit card payment",
            category="Card Payment",
            day=12,
            counterparty="card",
        ),
    ]
    flow = credit_card_cash_flow("c1", txns, _accounts())
    assert flow.spending == Decimal("70.00")
    assert flow.cash_outflow == Decimal("70.00")

    money = summarize_money_flow("c1", txns, _accounts())
    assert money.expenses_posted == Decimal("70.00")

    forecast = build_engine_forecast(
        customer_id="c1",
        accounts=_accounts(),
        transactions=txns,
        scheduled_bills=[],
        as_of=AS_OF,
        horizon_days=1,
        lookback_days=30,
    )
    assert forecast.daily_estimated_spend == Decimal("2.33")


def test_unpaid_card_purchase_is_not_immediate_cash() -> None:
    txns = [
        _txn("swipe", "card", "-40.00", description="Fuel", category="Transport", day=5)
    ]
    flow = credit_card_cash_flow("c1", txns, _accounts())
    assert flow.spending == Decimal("40.00")
    assert flow.cash_outflow == Decimal("0.00")
    forecast = build_engine_forecast(
        customer_id="c1",
        accounts=_accounts(),
        transactions=txns,
        scheduled_bills=[],
        as_of=AS_OF,
        horizon_days=1,
        lookback_days=30,
    )
    assert forecast.daily_estimated_spend == Decimal("0.00")


class _CardProvider(DataProvider):
    def __init__(self, txns: list[Transaction]) -> None:
        self._txns = txns

    @property
    def mode(self):
        return "mock"

    def list_accounts(self, customer_id: str) -> list[Account]:
        return _accounts()

    def list_transactions(self, customer_id: str, account_id: str | None = None):
        return self._txns

    def list_scheduled_bills(self, customer_id: str):
        return []

    def explain_dashboard(self, customer_id: str, summary_text: str) -> str:
        return summary_text


def test_memory_window_purchase_then_payment_counts_once() -> None:
    from app.config import Settings

    txns = [
        _txn("swipe", "card", "-80.00", description="Groceries", category="Food", day=2),
        _txn(
            "pay",
            "checking",
            "-80.00",
            description="Credit card payment",
            category="Card Payment",
            day=10,
            counterparty="card",
        ),
    ]
    summary = spending_window_summary(
        settings=Settings(use_mock_data=True, demo_customer_id="c1"),
        provider=_CardProvider(txns),
        customer_id="c1",
        start_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 11, 30, tzinfo=timezone.utc),
    )
    assert summary.expenses_posted == Decimal("80.00")
    categories = {row.category: row.total_spent for row in summary.categories}
    assert categories == {"Food": Decimal("80.00")}
    assert sum(point.total_spent for point in summary.daily) == Decimal("80.00")
