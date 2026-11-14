"""Live SQL analytics vs raw TigerData consistency checks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.db.connection import get_connection
from app.db.repository import TigerRepository
from app.models.schemas import TransactionKind, TransactionStatus
from app.services.analytics_service import spending_window_summary
from app.services.providers.live import LiveDataProvider

pytestmark = pytest.mark.live


def test_sql_spending_matches_raw_transactions(synced_live_customer) -> None:
    settings = synced_live_customer["settings"]
    customer_id = synced_live_customer["customer_id"]

    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=60)

    provider = LiveDataProvider(settings, sync_on_read=False)
    summary = spending_window_summary(
        settings=settings,
        provider=provider,
        customer_id=customer_id,
        start_at=start,
        end_at=end,
    )
    assert summary.source in {"sql", "continuous_aggregate"}
    assert summary.customer_id == customer_id

    with get_connection(settings) as conn:
        repo = TigerRepository(conn)
        txns = repo.list_transactions(customer_id)

    expected_expense = Decimal("0.00")
    expected_income = Decimal("0.00")
    category_totals: dict[str, Decimal] = {}
    for txn in txns:
        posted = txn.posted_at
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        if posted < start or posted >= end:
            continue
        if txn.kind == TransactionKind.TRANSFER:
            continue
        if txn.kind == TransactionKind.EXPENSE and txn.status == TransactionStatus.POSTED:
            spent = abs(txn.amount)
            expected_expense += spent
            category_totals[txn.category] = (
                category_totals.get(txn.category, Decimal("0")) + spent
            )
        if txn.kind == TransactionKind.INCOME and txn.status == TransactionStatus.POSTED:
            expected_income += txn.amount

    assert summary.expenses_posted == expected_expense.quantize(Decimal("0.01"))
    assert summary.income_posted == expected_income.quantize(Decimal("0.01"))

    sql_by_cat = {c.category: c.total_spent for c in summary.categories}
    for category, total in category_totals.items():
        assert sql_by_cat.get(category) == total.quantize(Decimal("0.01"))

    # Daily series totals should reconcile to expenses_posted.
    daily_sum = sum((p.total_spent for p in summary.daily), Decimal("0"))
    assert daily_sum.quantize(Decimal("0.01")) == summary.expenses_posted
