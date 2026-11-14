"""Transaction endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_data_provider, verify_customer
from app.models.schemas import TransactionsResponse
from app.services.providers.base import DataProvider

router = APIRouter(tags=["transactions"])


@router.get(
    "/customers/{customer_id}/transactions",
    response_model=TransactionsResponse,
)
def list_transactions(
    customer_id: str = Depends(verify_customer),
    account_id: str | None = Query(default=None),
    provider: DataProvider = Depends(get_data_provider),
) -> TransactionsResponse:
    if account_id is not None:
        owned = {a.account_id for a in provider.list_accounts(customer_id)}
        if account_id not in owned:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="account_id not found for customer",
            )
    txns = provider.list_transactions(customer_id, account_id=account_id)
    return TransactionsResponse(
        customer_id=customer_id,
        account_id=account_id,
        transactions=txns,
    )
