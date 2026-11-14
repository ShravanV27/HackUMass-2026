"""Analytics and dashboard endpoints."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_data_provider, verify_customer
from app.config import Settings, get_settings
from app.domain.calculations import (
    build_linear_forecast,
    summarize_money_flow,
    total_available_balance,
)
from app.models.schemas import (
    DashboardResponse,
    ForecastSummary,
    MoneyFlowSummary,
    SpendingWindowSummary,
)
from app.services.analytics_service import spending_window_summary
from app.services.providers.base import DataProvider

router = APIRouter(tags=["analytics"])

DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_HORIZON_DAYS = 14


@router.get(
    "/customers/{customer_id}/money-flow",
    response_model=MoneyFlowSummary,
)
def money_flow(
    customer_id: str = Depends(verify_customer),
    provider: DataProvider = Depends(get_data_provider),
) -> MoneyFlowSummary:
    txns = provider.list_transactions(customer_id)
    return summarize_money_flow(customer_id, txns)


@router.get(
    "/customers/{customer_id}/forecast",
    response_model=ForecastSummary,
)
def forecast(
    customer_id: str = Depends(verify_customer),
    horizon_days: int = Query(default=DEFAULT_HORIZON_DAYS, ge=0, le=90),
    lookback_days: int = Query(default=DEFAULT_LOOKBACK_DAYS, ge=1, le=365),
    provider: DataProvider = Depends(get_data_provider),
) -> ForecastSummary:
    accounts = provider.list_accounts(customer_id)
    txns = provider.list_transactions(customer_id)
    flow = summarize_money_flow(customer_id, txns)
    starting = total_available_balance(accounts)
    return build_linear_forecast(
        customer_id,
        starting,
        flow,
        lookback_days=lookback_days,
        horizon_days=horizon_days,
        as_of=date.today(),
        mode=provider.mode,
    )


@router.get(
    "/customers/{customer_id}/dashboard",
    response_model=DashboardResponse,
)
def dashboard(
    customer_id: str = Depends(verify_customer),
    horizon_days: int = Query(default=DEFAULT_HORIZON_DAYS, ge=0, le=90),
    lookback_days: int = Query(default=DEFAULT_LOOKBACK_DAYS, ge=1, le=365),
    provider: DataProvider = Depends(get_data_provider),
) -> DashboardResponse:
    accounts = provider.list_accounts(customer_id)
    txns = provider.list_transactions(customer_id)
    flow = summarize_money_flow(customer_id, txns)
    starting = total_available_balance(accounts)
    forecast_summary = build_linear_forecast(
        customer_id,
        starting,
        flow,
        lookback_days=lookback_days,
        horizon_days=horizon_days,
        as_of=date.today(),
        mode=provider.mode,
    )
    summary_text = (
        f"Posted net {_fmt(flow.net_posted)}; "
        f"including pending {_fmt(flow.net_including_pending)}; "
        f"projected daily rate {_fmt(forecast_summary.daily_net_rate)}."
    )
    insight = provider.explain_dashboard(customer_id, summary_text)
    return DashboardResponse(
        customer_id=customer_id,
        mode=provider.mode,
        accounts=accounts,
        money_flow=flow,
        forecast=forecast_summary,
        insight=insight,
    )


@router.get(
    "/customers/{customer_id}/analytics/spending",
    response_model=SpendingWindowSummary,
)
def spending_analytics(
    customer_id: str = Depends(verify_customer),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    lookback_days: int = Query(default=DEFAULT_LOOKBACK_DAYS, ge=1, le=365),
    provider: DataProvider = Depends(get_data_provider),
    settings: Settings = Depends(get_settings),
) -> SpendingWindowSummary:
    """Time-window spending summary (SQL in live mode, in-memory in mock)."""
    end = end_at or datetime.now(tz=timezone.utc)
    start = start_at or (end - timedelta(days=lookback_days))
    return spending_window_summary(
        settings=settings,
        provider=provider,
        customer_id=customer_id,
        start_at=start,
        end_at=end,
    )


def _fmt(value: Decimal) -> str:
    return f"${value:,.2f}"
