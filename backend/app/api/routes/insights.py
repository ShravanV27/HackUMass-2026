"""Phase 3 alerts + Gemini insights / copilot routes."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_data_provider, verify_customer
from app.config import get_settings
from app.models.schemas import (
    AlertsResponse,
    CopilotRequest,
    CopilotResponse,
    InsightsResponse,
)
from app.services.insights_service import (
    answer_copilot,
    build_alerts_response,
    build_insights_response,
)
from app.services.providers.base import DataProvider

router = APIRouter(tags=["insights"])


@router.get(
    "/customers/{customer_id}/alerts",
    response_model=AlertsResponse,
)
def list_alerts(
    customer_id: str = Depends(verify_customer),
    horizon_days: int = Query(default=14, ge=1, le=90),
    minimum_buffer: Decimal = Query(default=Decimal("250.00"), ge=0),
    provider: DataProvider = Depends(get_data_provider),
) -> AlertsResponse:
    return build_alerts_response(
        customer_id=customer_id,
        provider=provider,
        settings=get_settings(),
        horizon_days=horizon_days,
        minimum_buffer=minimum_buffer,
    )


@router.get(
    "/customers/{customer_id}/insights",
    response_model=InsightsResponse,
)
def customer_insights(
    customer_id: str = Depends(verify_customer),
    provider: DataProvider = Depends(get_data_provider),
) -> InsightsResponse:
    return build_insights_response(
        customer_id=customer_id,
        provider=provider,
        settings=get_settings(),
    )


@router.post(
    "/customers/{customer_id}/copilot",
    response_model=CopilotResponse,
)
def copilot(
    body: CopilotRequest,
    customer_id: str = Depends(verify_customer),
    provider: DataProvider = Depends(get_data_provider),
) -> CopilotResponse:
    return answer_copilot(
        customer_id=customer_id,
        provider=provider,
        body=body,
        settings=get_settings(),
    )
