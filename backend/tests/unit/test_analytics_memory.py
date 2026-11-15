"""In-memory spending window analytics (mock path)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config import Settings
from app.models.schemas import Account, Transaction, TransactionKind, TransactionStatus
from app.services.analytics_service import spending_window_summary
from app.services.providers.base import DataProvider


class _StubProvider(DataProvider):
    @property
    def mode(self):
        return "mock"

    def list_accounts(self, customer_id: str) -> list[Account]:
        return []

    def list_transactions(self, customer_id: str, account_id: str | None = None):
        return [
            Transaction(
                transaction_id="1",
                customer_id=customer_id,
                account_id="a1",
                amount=Decimal("-40.00"),
                kind=TransactionKind.EXPENSE,
                status=TransactionStatus.POSTED,
                description="Food",
                category="Food",
                posted_at=datetime(2026, 11, 5, tzinfo=timezone.utc),
            ),
            Transaction(
                transaction_id="2",
                customer_id=customer_id,
                account_id="a1",
                amount=Decimal("100.00"),
                kind=TransactionKind.INCOME,
                status=TransactionStatus.POSTED,
                description="Pay",
                category="Income",
                posted_at=datetime(2026, 11, 6, tzinfo=timezone.utc),
            ),
            Transaction(
                transaction_id="3",
                customer_id=customer_id,
                account_id="a1",
                amount=Decimal("-10.00"),
                kind=TransactionKind.EXPENSE,
                status=TransactionStatus.POSTED,
                description="Old",
                category="Food",
                posted_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            ),
        ]

    def explain_dashboard(self, customer_id: str, summary_text: str) -> str:
        return summary_text


def test_memory_spending_respects_time_window() -> None:
    settings = Settings(use_mock_data=True, demo_customer_id="demo-customer-001")
    summary = spending_window_summary(
        settings=settings,
        provider=_StubProvider(),
        customer_id="demo-customer-001",
        start_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 11, 30, tzinfo=timezone.utc),
    )
    assert summary.source == "memory"
    assert summary.expenses_posted == Decimal("40.00")
    assert summary.income_posted == Decimal("100.00")
    assert summary.categories[0].category == "Food"
    assert summary.categories[0].total_spent == Decimal("40.00")
    assert summary.categories[0].txn_count == 1


def test_memory_daily_bucket_uses_utc_date() -> None:
    eastern = timezone(timedelta(hours=-5))

    class _OffsetProvider(_StubProvider):
        def list_transactions(self, customer_id: str, account_id: str | None = None):
            return [
                Transaction(
                    transaction_id="late",
                    customer_id=customer_id,
                    account_id="a1",
                    amount=Decimal("-40.00"),
                    kind=TransactionKind.EXPENSE,
                    status=TransactionStatus.POSTED,
                    description="Dinner",
                    category="Food",
                    # 2026-10-31 22:00 EST == 2026-11-01 03:00 UTC
                    posted_at=datetime(2026, 10, 31, 22, 0, tzinfo=eastern),
                )
            ]

    settings = Settings(use_mock_data=True, demo_customer_id="demo-customer-001")
    summary = spending_window_summary(
        settings=settings,
        provider=_OffsetProvider(),
        customer_id="demo-customer-001",
        start_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
    )
    assert summary.expenses_posted == Decimal("40.00")
    assert len(summary.daily) == 1
    assert summary.daily[0].day.isoformat() == "2026-11-01"
