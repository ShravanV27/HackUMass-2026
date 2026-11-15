"""Spending / time-window analytics with SQL (live) or in-memory (mock) paths."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal

from app.config import Settings
from app.db.connection import get_connection
from app.db.repository import TigerRepository
from app.domain.calculations import credit_account_ids, is_card_payment
from app.models.schemas import (
    CategorySpend,
    DailySpendPoint,
    SpendingWindowSummary,
    TransactionKind,
)
from app.services.providers.base import DataProvider


def spending_window_summary(
    *,
    settings: Settings,
    provider: DataProvider,
    customer_id: str,
    start_at: datetime,
    end_at: datetime,
) -> SpendingWindowSummary:
    if start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=timezone.utc)
    if end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=timezone.utc)

    if not settings.use_mock_data and settings.database_url:
        # Warm live sync once; LiveDataProvider skips duplicate Nessie pulls.
        provider.list_transactions(customer_id)
        with get_connection(settings) as conn:
            repo = TigerRepository(conn)
            categories = [
                CategorySpend(
                    category=row["category"],
                    total_spent=row["total_spent"],
                    txn_count=row["txn_count"],
                )
                for row in repo.spending_by_category(
                    customer_id, start_at=start_at, end_at=end_at
                )
            ]
            # Continuous aggregates sum every expense, including card swipes.
            # The filtered query keeps card purchases out of cash totals.
            daily_rows = repo.daily_spending(
                customer_id,
                start_at=start_at,
                end_at=end_at,
                prefer_continuous_aggregate=False,
            )
            daily = [
                DailySpendPoint(
                    day=row["day"],
                    total_spent=row["total_spent"],
                    txn_count=row["txn_count"],
                )
                for row in daily_rows
            ]
            cashflow = repo.cashflow_in_window(
                customer_id, start_at=start_at, end_at=end_at
            )
        source = (
            "continuous_aggregate"
            if daily_rows and daily_rows[0].get("source") == "continuous_aggregate"
            else "sql"
        )
        return SpendingWindowSummary(
            customer_id=customer_id,
            start_at=start_at,
            end_at=end_at,
            source=source,  # type: ignore[arg-type]
            categories=categories,
            daily=daily,
            **cashflow,
        )

    return _memory_spending_window(
        provider=provider,
        customer_id=customer_id,
        start_at=start_at,
        end_at=end_at,
    )


def _memory_spending_window(
    *,
    provider: DataProvider,
    customer_id: str,
    start_at: datetime,
    end_at: datetime,
) -> SpendingWindowSummary:
    txns = provider.list_transactions(customer_id)
    accounts = provider.list_accounts(customer_id)
    card_ids = credit_account_ids(accounts, customer_id)
    category_totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    category_counts: dict[str, int] = defaultdict(int)
    income_posted = Decimal("0")
    expenses_posted = Decimal("0")
    pending_income = Decimal("0")
    pending_expenses = Decimal("0")
    transfer_abs = Decimal("0")
    transfer_legs = 0

    daily_totals: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    daily_counts: dict[date, int] = defaultdict(int)
    card_purchase_total = Decimal("0")
    card_payment_total = Decimal("0")
    payment_spots: list[tuple[str, date, Decimal]] = []

    for txn in txns:
        posted = txn.posted_at
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        if posted < start_at or posted >= end_at:
            continue

        if txn.kind == TransactionKind.TRANSFER:
            transfer_abs += abs(txn.amount)
            transfer_legs += 1
            continue

        if txn.kind == TransactionKind.INCOME:
            if txn.status.value == "posted":
                income_posted += txn.amount
            else:
                pending_income += txn.amount
            continue

        if txn.kind == TransactionKind.EXPENSE:
            spent = abs(txn.amount)
            day = posted.astimezone(timezone.utc).date()
            on_card = txn.account_id in card_ids
            payment = is_card_payment(txn, card_ids)
            if txn.status.value != "posted":
                # A pending swipe on the card has not left cash yet.
                if not on_card:
                    pending_expenses += spent
                continue
            if on_card:
                # Spending counts on the purchase. Cash waits for the payment.
                card_purchase_total += spent
                category_totals[txn.category] += spent
                category_counts[txn.category] += 1
                daily_totals[day] += spent
                daily_counts[day] += 1
                continue
            if payment:
                card_payment_total += spent
                expenses_posted += spent
                payment_spots.append((txn.category, day, spent))
                continue
            expenses_posted += spent
            category_totals[txn.category] += spent
            category_counts[txn.category] += 1
            daily_totals[day] += spent
            daily_counts[day] += 1

    # A card payment with no (or only partial) purchases in the window is the
    # only record of that spend, so the unmatched remainder counts once.
    unmatched_payment = card_payment_total - min(card_purchase_total, card_payment_total)
    if unmatched_payment > 0 and payment_spots:
        category, day, _amount = payment_spots[-1]
        category_totals[category] += unmatched_payment
        category_counts[category] += 1
        daily_totals[day] += unmatched_payment
        daily_counts[day] += 1

    if transfer_legs >= 2 and transfer_legs % 2 == 0:
        transfer_volume = (transfer_abs / 2).quantize(Decimal("0.01"))
    else:
        transfer_volume = transfer_abs.quantize(Decimal("0.01"))

    categories = [
        CategorySpend(
            category=category,
            total_spent=total.quantize(Decimal("0.01")),
            txn_count=category_counts[category],
        )
        for category, total in sorted(
            category_totals.items(), key=lambda item: item[1], reverse=True
        )
    ]
    daily = [
        DailySpendPoint(
            day=day,
            total_spent=total.quantize(Decimal("0.01")),
            txn_count=daily_counts[day],
        )
        for day, total in sorted(daily_totals.items())
    ]
    return SpendingWindowSummary(
        customer_id=customer_id,
        start_at=start_at,
        end_at=end_at,
        source="memory",
        categories=categories,
        daily=daily,
        income_posted=income_posted.quantize(Decimal("0.01")),
        expenses_posted=expenses_posted.quantize(Decimal("0.01")),
        pending_income=pending_income.quantize(Decimal("0.01")),
        pending_expenses=pending_expenses.quantize(Decimal("0.01")),
        transfer_volume=transfer_volume,
        net_posted=(income_posted - expenses_posted).quantize(Decimal("0.01")),
        net_including_pending=(
            (income_posted + pending_income) - (expenses_posted + pending_expenses)
        ).quantize(Decimal("0.01")),
    )
