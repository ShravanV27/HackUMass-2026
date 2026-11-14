"""Predictive endpoints (M5 + Phase 1 PredictiveEngine).

Existing /forecast stays unchanged. Gemini is never used for numbers here.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_data_provider, verify_customer
from app.api.prediction_context import load_prediction_inputs
from app.domain.predictive_engine import (
    backtest_spend_forecast,
    build_engine_forecast,
    classify_behavior,
    compute_engine_safe_to_spend,
    compute_overdraft_risk,
    resolve_horizon_days,
    simulate_scenarios,
    simulate_what_if_with_engine,
)
from app.domain.predictions import compute_spending_drift
from app.models.schemas import (
    BacktestResponse,
    BehaviorSegmentResponse,
    CashflowForecastResponse,
    OverdraftRiskResponse,
    SafeToSpendResponse,
    ScenarioRequest,
    ScenarioSimulationResponse,
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
    horizon: str | None = Query(
        default=None,
        description="Optional alias: week (7) or month (30).",
    ),
    horizon_days: int | None = Query(default=None, ge=1, le=90),
    lookback_days: int = Query(default=DEFAULT_LOOKBACK, ge=1, le=365),
    provider: DataProvider = Depends(get_data_provider),
) -> CashflowForecastResponse:
    try:
        days, horizon_name = resolve_horizon_days(
            horizon=horizon,
            horizon_days=horizon_days if horizon_days is not None else DEFAULT_HORIZON,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    as_of = date.today()
    accounts, txns, bills, payment_cycle, history = load_prediction_inputs(
        customer_id,
        provider,
        lookback_days=lookback_days,
        as_of=as_of,
    )
    buffer = payment_cycle.minimum_buffer
    return build_engine_forecast(
        customer_id=customer_id,
        accounts=accounts,
        transactions=txns,
        scheduled_bills=bills,
        as_of=as_of,
        horizon_days=days,
        lookback_days=lookback_days,
        payment_cycle=payment_cycle,
        minimum_buffer=buffer,
        daily_spend_history=history,
        horizon_name=horizon_name,
    )


@router.get(
    "/customers/{customer_id}/predictions/safe-to-spend",
    response_model=SafeToSpendResponse,
)
def safe_to_spend(
    customer_id: str = Depends(verify_customer),
    horizon: str | None = Query(default=None),
    horizon_days: int | None = Query(default=None, ge=1, le=90),
    lookback_days: int = Query(default=DEFAULT_LOOKBACK, ge=1, le=365),
    minimum_buffer: Decimal | None = Query(default=None, ge=0),
    provider: DataProvider = Depends(get_data_provider),
) -> SafeToSpendResponse:
    try:
        days, _name = resolve_horizon_days(
            horizon=horizon,
            horizon_days=horizon_days if horizon_days is not None else DEFAULT_HORIZON,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    as_of = date.today()
    accounts, txns, bills, payment_cycle, history = load_prediction_inputs(
        customer_id,
        provider,
        lookback_days=lookback_days,
        as_of=as_of,
    )
    buffer = (
        minimum_buffer
        if minimum_buffer is not None
        else payment_cycle.minimum_buffer
    )
    return compute_engine_safe_to_spend(
        customer_id=customer_id,
        accounts=accounts,
        transactions=txns,
        scheduled_bills=bills,
        as_of=as_of,
        horizon_days=days,
        lookback_days=lookback_days,
        minimum_buffer=buffer,
        payment_cycle=payment_cycle,
        daily_spend_history=history,
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
    as_of = date.today()
    accounts, txns, bills, payment_cycle, _history = load_prediction_inputs(
        customer_id,
        provider,
        lookback_days=lookback_days,
        as_of=as_of,
    )
    return simulate_what_if_with_engine(
        customer_id=customer_id,
        accounts=accounts,
        transactions=txns,
        scheduled_bills=bills,
        purchase_amount=body.amount,
        description=body.description,
        as_of=as_of,
        horizon_days=body.horizon_days,
        lookback_days=lookback_days,
        minimum_buffer=body.minimum_buffer,
        payment_cycle=payment_cycle,
        purchase_date=body.purchase_date,
    )


@router.get(
    "/customers/{customer_id}/predictions/backtest",
    response_model=BacktestResponse,
)
def prediction_backtest(
    customer_id: str = Depends(verify_customer),
    horizon_days: int = Query(default=7, ge=1, le=30),
    lookback_days: int = Query(default=DEFAULT_LOOKBACK, ge=1, le=365),
    windows: int = Query(default=4, ge=1, le=12),
    provider: DataProvider = Depends(get_data_provider),
) -> BacktestResponse:
    as_of = date.today()
    accounts, txns, bills, payment_cycle, _history = load_prediction_inputs(
        customer_id,
        provider,
        lookback_days=lookback_days + horizon_days * windows,
        as_of=as_of,
    )
    return backtest_spend_forecast(
        customer_id=customer_id,
        accounts=accounts,
        transactions=txns,
        scheduled_bills=bills,
        as_of=as_of,
        horizon_days=horizon_days,
        lookback_days=lookback_days,
        windows=windows,
        payment_cycle=payment_cycle,
    )


@router.get(
    "/customers/{customer_id}/predictions/overdraft-risk",
    response_model=OverdraftRiskResponse,
)
def overdraft_risk(
    customer_id: str = Depends(verify_customer),
    horizon: str | None = Query(default=None),
    horizon_days: int | None = Query(default=None, ge=1, le=90),
    lookback_days: int = Query(default=DEFAULT_LOOKBACK, ge=1, le=365),
    minimum_buffer: Decimal | None = Query(default=None, ge=0),
    provider: DataProvider = Depends(get_data_provider),
) -> OverdraftRiskResponse:
    try:
        days, _name = resolve_horizon_days(
            horizon=horizon,
            horizon_days=horizon_days if horizon_days is not None else DEFAULT_HORIZON,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    as_of = date.today()
    accounts, txns, bills, payment_cycle, history = load_prediction_inputs(
        customer_id,
        provider,
        lookback_days=lookback_days,
        as_of=as_of,
    )
    buffer = (
        minimum_buffer
        if minimum_buffer is not None
        else payment_cycle.minimum_buffer
    )
    return compute_overdraft_risk(
        customer_id=customer_id,
        accounts=accounts,
        transactions=txns,
        scheduled_bills=bills,
        as_of=as_of,
        horizon_days=days,
        lookback_days=lookback_days,
        minimum_buffer=buffer,
        payment_cycle=payment_cycle,
        daily_spend_history=history,
    )


@router.get(
    "/customers/{customer_id}/predictions/behavior",
    response_model=BehaviorSegmentResponse,
)
def behavior_segment(
    customer_id: str = Depends(verify_customer),
    lookback_days: int = Query(default=28, ge=7, le=365),
    recent_days: int = Query(default=7, ge=1, le=90),
    baseline_days: int = Query(default=28, ge=1, le=365),
    provider: DataProvider = Depends(get_data_provider),
) -> BehaviorSegmentResponse:
    as_of = date.today()
    accounts, txns, bills, payment_cycle, _history = load_prediction_inputs(
        customer_id,
        provider,
        lookback_days=lookback_days,
        as_of=as_of,
    )
    return classify_behavior(
        customer_id=customer_id,
        accounts=accounts,
        transactions=txns,
        scheduled_bills=bills,
        as_of=as_of,
        lookback_days=lookback_days,
        recent_days=recent_days,
        baseline_days=baseline_days,
        payment_cycle=payment_cycle,
    )


@router.post(
    "/customers/{customer_id}/predictions/scenarios",
    response_model=ScenarioSimulationResponse,
)
def scenario_simulation(
    body: ScenarioRequest,
    customer_id: str = Depends(verify_customer),
    provider: DataProvider = Depends(get_data_provider),
) -> ScenarioSimulationResponse:
    as_of = date.today()
    accounts, txns, bills, payment_cycle, history = load_prediction_inputs(
        customer_id,
        provider,
        lookback_days=body.lookback_days,
        as_of=as_of,
    )
    return simulate_scenarios(
        customer_id=customer_id,
        accounts=accounts,
        transactions=txns,
        scheduled_bills=bills,
        as_of=as_of,
        request=body,
        payment_cycle=payment_cycle,
        daily_spend_history=history,
    )
