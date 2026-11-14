"""M5 predictive endpoints (mock-first). Existing /forecast stays unchanged."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_data_provider, verify_customer
from app.domain.predictions import (
    build_cashflow_forecast,
    compute_safe_to_spend,
    compute_spending_drift,
    simulate_what_if_purchase,
)
from app.models.schemas import (
    CashflowForecastResponse,
    SafeToSpendResponse,
    SpendingDriftResponse,
    WhatIfRequest,
    WhatIfResponse,
)
from app.services.providers.base import DataProvider

router = APIRouter(tags=["predictions"])

DEFAULT_HORIZON = 14
DEFAULT_LOOKBACK = 30
DEFAULT_BUFFER = Decimal("250.00")


@router.get(
    "/customers/{customer_id}/predictions/cashflow",
    response_model=CashflowForecastResponse,
)
def predictive_cashflow(
    customer_id: str = Depends(verify_customer),
    horizon_days: int = Query(default=DEFAULT_HORIZON, ge=1, le=90),
    lookback_days: int = Query(default=DEFAULT_LOOKBACK, ge=1, le=365),
    provider: DataProvider = Depends(get_data_provider),
) -> CashflowForecastResponse:
    return build_cashflow_forecast(
        customer_id=customer_id,
        accounts=provider.list_accounts(customer_id),
        transactions=provider.list_transactions(customer_id),
        scheduled_bills=provider.list_scheduled_bills(customer_id),
        as_of=date.today(),
        horizon_days=horizon_days,
        lookback_days=lookback_days,
    )


@router.get(
    "/customers/{customer_id}/predictions/safe-to-spend",
    response_model=SafeToSpendResponse,
)
def safe_to_spend(
    customer_id: str = Depends(verify_customer),
    horizon_days: int = Query(default=DEFAULT_HORIZON, ge=1, le=90),
    lookback_days: int = Query(default=DEFAULT_LOOKBACK, ge=1, le=365),
    minimum_buffer: Decimal = Query(default=DEFAULT_BUFFER, ge=0),
    provider: DataProvider = Depends(get_data_provider),
) -> SafeToSpendResponse:
    return compute_safe_to_spend(
        customer_id=customer_id,
        accounts=provider.list_accounts(customer_id),
        transactions=provider.list_transactions(customer_id),
        scheduled_bills=provider.list_scheduled_bills(customer_id),
        as_of=date.today(),
        horizon_days=horizon_days,
        lookback_days=lookback_days,
        minimum_buffer=minimum_buffer,
    )


@router.get(
    "/customers/{customer_id}/predictions/drift",
    response_model=SpendingDriftResponse,
)
def spending_drift(
    customer_id: str = Depends(verify_customer),
    recent_days: int = Query(default=7, ge=1, le=90),
    baseline_days: int = Query(default=28, ge=1, le=365),
    provider: DataProvider = Depends(get_data_provider),
) -> SpendingDriftResponse:
    return compute_spending_drift(
        customer_id=customer_id,
        transactions=provider.list_transactions(customer_id),
        as_of=date.today(),
        recent_days=recent_days,
        baseline_days=baseline_days,
    )


@router.post(
    "/customers/{customer_id}/predictions/what-if",
    response_model=WhatIfResponse,
)
def what_if(
    body: WhatIfRequest,
    customer_id: str = Depends(verify_customer),
    lookback_days: int = Query(default=DEFAULT_LOOKBACK, ge=1, le=365),
    provider: DataProvider = Depends(get_data_provider),
) -> WhatIfResponse:
    return simulate_what_if_purchase(
        customer_id=customer_id,
        accounts=provider.list_accounts(customer_id),
        transactions=provider.list_transactions(customer_id),
        scheduled_bills=provider.list_scheduled_bills(customer_id),
        purchase_amount=body.amount,
        description=body.description,
        as_of=date.today(),
        horizon_days=body.horizon_days,
        lookback_days=lookback_days,
        minimum_buffer=body.minimum_buffer,
    )
