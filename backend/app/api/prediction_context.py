"""Shared helpers for prediction routes — avoid duplicate provider fetches."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from app.config import Settings, get_settings
from app.db.connection import get_connection
from app.db.repository import TigerRepository
from app.domain.predictive_engine import (
    DailySpendObservation,
    sql_bounds_for_half_open_days,
)
from app.models.schemas import Account, PaymentCycleConfig, ScheduledBill, Transaction
from app.services.customer_settings import get_payment_cycle
from app.services.providers.base import DataProvider


def load_prediction_inputs(
    customer_id: str,
    provider: DataProvider,
    *,
    settings: Settings | None = None,
    lookback_days: int = 30,
    as_of: date | None = None,
) -> tuple[
    list[Account],
    list[Transaction],
    list[ScheduledBill],
    PaymentCycleConfig,
    list[DailySpendObservation] | None,
]:
    """Fetch accounts/txns/bills once; optionally weekday history from TigerData.

    Live path: uses already-synced provider reads (no extra Nessie sync). Tiger
    discretionary daily SQL is best-effort and skipped on failure so mock and
    degraded live still forecast from in-memory transactions.
    """
    cfg = settings or get_settings()
    day = as_of or date.today()
    accounts = provider.list_accounts(customer_id)
    transactions = provider.list_transactions(customer_id)
    bills = provider.list_scheduled_bills(customer_id)
    payment_cycle, _source = get_payment_cycle(customer_id, settings=cfg)

    history: list[DailySpendObservation] | None = None
    if provider.mode == "live" and cfg.database_url:
        history = _tiger_discretionary_history(
            customer_id,
            cfg,
            as_of=day,
            lookback_days=lookback_days,
            bills=bills,
        )
    return accounts, transactions, bills, payment_cycle, history


def _tiger_discretionary_history(
    customer_id: str,
    settings: Settings,
    *,
    as_of: date,
    lookback_days: int,
    bills: list[ScheduledBill],
) -> list[DailySpendObservation] | None:
    exclude = sorted({b.category for b in bills if b.customer_id == customer_id} | {"Bills"})
    bill_names = sorted(
        {
            bill.name.strip()
            for bill in bills
            if bill.customer_id == customer_id and bill.name and bill.name.strip()
        }
    )
    start, end = sql_bounds_for_half_open_days(
        start_exclusive=as_of - timedelta(days=lookback_days),
        end_inclusive=as_of,
    )
    try:
        with get_connection(settings) as conn:
            repo = TigerRepository(conn)
            # Schema already ensured during sync; do not force hypertable here.
            rows = repo.discretionary_daily_spending(
                customer_id,
                start_at=start,
                end_at=end,
                exclude_categories=exclude,
                exclude_description_tokens=bill_names,
            )
        return [
            DailySpendObservation(
                day=row["day"] if not isinstance(row["day"], datetime) else row["day"].date(),
                total_spent=Decimal(str(row["total_spent"])),
            )
            for row in rows
        ]
    except Exception:
        return None
