"""Customer settings — payment cycles for PredictiveEngine."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import verify_customer
from app.config import get_settings
from app.models.schemas import PaymentCycleConfig, PaymentCycleSettingsResponse
from app.services.customer_settings import get_payment_cycle, put_payment_cycle

router = APIRouter(tags=["settings"])


@router.get(
    "/customers/{customer_id}/settings/payment-cycles",
    response_model=PaymentCycleSettingsResponse,
)
def read_payment_cycles(
    customer_id: str = Depends(verify_customer),
) -> PaymentCycleSettingsResponse:
    cycle, source = get_payment_cycle(customer_id, settings=get_settings())
    return PaymentCycleSettingsResponse(
        customer_id=customer_id,
        payment_cycle=cycle,
        source=source,
    )


@router.put(
    "/customers/{customer_id}/settings/payment-cycles",
    response_model=PaymentCycleSettingsResponse,
)
def write_payment_cycles(
    body: PaymentCycleConfig,
    customer_id: str = Depends(verify_customer),
) -> PaymentCycleSettingsResponse:
    try:
        cycle, source = put_payment_cycle(
            customer_id, body, settings=get_settings()
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return PaymentCycleSettingsResponse(
        customer_id=customer_id,
        payment_cycle=cycle,
        source=source,
    )
