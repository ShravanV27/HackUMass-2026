"""On-demand Nessie → TigerData sync (no Nessie writes / deletes)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_data_provider, verify_customer
from app.models.schemas import SyncResponse
from app.services.providers.base import DataProvider
from app.services.providers.live import LiveDataProvider

router = APIRouter(tags=["sync"])


@router.post(
    "/customers/{customer_id}/sync",
    response_model=SyncResponse,
)
def force_sync(
    customer_id: str = Depends(verify_customer),
    provider: DataProvider = Depends(get_data_provider),
) -> SyncResponse:
    if provider.mode == "mock":
        return SyncResponse(
            customer_id=customer_id,
            mode="mock",
            status="skipped",
            message="Mock mode has no Nessie/TigerData sync.",
        )
    if not isinstance(provider, LiveDataProvider):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Live provider unavailable",
        )
    try:
        result = provider.force_sync(customer_id)
    except Exception as exc:  # noqa: BLE001 — surface sync failures cleanly
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Sync failed: {exc}",
        ) from exc
    return SyncResponse(
        customer_id=customer_id,
        mode="live",
        status="synced",
        message="Nessie snapshot synced into TigerData.",
        accounts_upserted=int(result.get("accounts_upserted", 0)),
        transactions_upserted=int(result.get("transactions_upserted", 0)),
    )
