"""Account endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_data_provider, verify_customer
from app.models.schemas import AccountsResponse
from app.services.providers.base import DataProvider

router = APIRouter(tags=["accounts"])


@router.get(
    "/customers/{customer_id}/accounts",
    response_model=AccountsResponse,
)
def list_accounts(
    customer_id: str = Depends(verify_customer),
    provider: DataProvider = Depends(get_data_provider),
) -> AccountsResponse:
    accounts = provider.list_accounts(customer_id)
    return AccountsResponse(customer_id=customer_id, accounts=accounts)
