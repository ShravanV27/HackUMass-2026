"""FastAPI dependencies — auth-ready customer resolution."""

from __future__ import annotations

from fastapi import HTTPException, Path, status

from app.config import Settings, get_settings
from app.services.providers.base import DataProvider
from app.services.providers.factory import build_provider


def get_current_customer_id(
    settings: Settings | None = None,
) -> str:
    """Resolve the authenticated customer on the server.

    Today this returns the fixed demo customer. In a future Sign in with Apple
    flow, validate the Apple identity token here and map `sub` → customer_id.
    Never treat a client-supplied customer_id as authentication.
    """
    cfg = settings or get_settings()
    return cfg.demo_customer_id


def require_customer_access(customer_id: str, authenticated_customer_id: str) -> None:
    """Authorize path customer_id against the server-resolved identity."""
    if customer_id != authenticated_customer_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="customer_id does not match the authenticated customer",
        )


def verify_customer(
    customer_id: str = Path(..., min_length=1, description="Customer identifier"),
) -> str:
    """Path customer_id checked against server-side auth context."""
    authenticated = get_current_customer_id()
    require_customer_access(customer_id, authenticated)
    return customer_id


def get_data_provider() -> DataProvider:
    # Rebuild each request so tests can flip settings without cache issues.
    return build_provider(get_settings())
