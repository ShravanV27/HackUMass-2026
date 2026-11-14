"""Demo Lab write routes — gated by ALLOW_NESSIE_WRITES; no Nessie deletes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_data_provider, get_current_customer_id, verify_customer
from app.config import get_settings
from app.models.schemas import (
    BindCustomerRequest,
    DemoActionResponse,
    DemoBillRequest,
    DemoDepositRequest,
    DemoPurchaseRequest,
    DemoStatusResponse,
    SessionResponse,
)
from app.services.demo_lab import (
    DemoLabError,
    bind_customer_session,
    require_writes_enabled,
    run_bill,
    run_deposit,
    run_purchase,
)
from app.services.providers.base import DataProvider
from app.services.session_bindings import get_bound_nessie_customer_id

router = APIRouter(tags=["demo"])


def _raise_demo(exc: DemoLabError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get(
    "/customers/{customer_id}/demo/status",
    response_model=DemoStatusResponse,
)
def demo_status(
    customer_id: str = Depends(verify_customer),
    provider: DataProvider = Depends(get_data_provider),
) -> DemoStatusResponse:
    settings = get_settings()
    bound = get_bound_nessie_customer_id(customer_id)
    writes = settings.allow_nessie_writes
    if writes:
        message = (
            "Demo Lab writes enabled. Actions create Nessie resources in live mode "
            "or mutate mock state — never delete/reset sandbox data."
            if provider.mode == "live"
            else "Demo Lab writes enabled (mock in-memory mutations)."
        )
    else:
        message = "Demo Lab writes disabled. Set ALLOW_NESSIE_WRITES=true to enable."
    return DemoStatusResponse(
        customer_id=customer_id,
        mode=provider.mode,
        writes_enabled=writes,
        bound_nessie_customer_id=bound,
        env_nessie_customer_id=settings.nessie_customer_id,
        message=message,
    )


@router.post(
    "/customers/{customer_id}/demo/purchase",
    response_model=DemoActionResponse,
)
def demo_purchase(
    body: DemoPurchaseRequest,
    customer_id: str = Depends(verify_customer),
    provider: DataProvider = Depends(get_data_provider),
) -> DemoActionResponse:
    try:
        require_writes_enabled(get_settings())
        return run_purchase(
            customer_id=customer_id,
            provider=provider,
            body=body,
            settings=get_settings(),
        )
    except DemoLabError as exc:
        _raise_demo(exc)
        raise  # pragma: no cover


@router.post(
    "/customers/{customer_id}/demo/deposit",
    response_model=DemoActionResponse,
)
def demo_deposit(
    body: DemoDepositRequest,
    customer_id: str = Depends(verify_customer),
    provider: DataProvider = Depends(get_data_provider),
) -> DemoActionResponse:
    try:
        return run_deposit(
            customer_id=customer_id,
            provider=provider,
            body=body,
            settings=get_settings(),
        )
    except DemoLabError as exc:
        _raise_demo(exc)
        raise  # pragma: no cover


@router.post(
    "/customers/{customer_id}/demo/bill",
    response_model=DemoActionResponse,
)
def demo_bill(
    body: DemoBillRequest,
    customer_id: str = Depends(verify_customer),
    provider: DataProvider = Depends(get_data_provider),
) -> DemoActionResponse:
    try:
        return run_bill(
            customer_id=customer_id,
            provider=provider,
            body=body,
            settings=get_settings(),
        )
    except DemoLabError as exc:
        _raise_demo(exc)
        raise  # pragma: no cover


@router.get("/session", response_model=SessionResponse)
def get_session(
    provider: DataProvider = Depends(get_data_provider),
) -> SessionResponse:
    settings = get_settings()
    app_id = get_current_customer_id(settings)
    bound = get_bound_nessie_customer_id(app_id)
    return SessionResponse(
        app_customer_id=app_id,
        mode=provider.mode,
        bound_nessie_customer_id=bound or settings.nessie_customer_id,
        writes_enabled=settings.allow_nessie_writes,
        message=(
            "Session ready."
            if (bound or settings.nessie_customer_id or provider.mode == "mock")
            else "Bind a Nessie sandbox customer id to use live Demo Lab."
        ),
    )


@router.post("/session/bind-customer", response_model=SessionResponse)
def bind_customer(
    body: BindCustomerRequest,
    provider: DataProvider = Depends(get_data_provider),
) -> SessionResponse:
    settings = get_settings()
    app_id = get_current_customer_id(settings)
    try:
        bound = bind_customer_session(
            app_customer_id=app_id,
            nessie_customer_id=body.nessie_customer_id,
            provider=provider,
            settings=settings,
        )
    except DemoLabError as exc:
        _raise_demo(exc)
        raise  # pragma: no cover
    return SessionResponse(
        app_customer_id=app_id,
        mode=provider.mode,
        bound_nessie_customer_id=bound,
        writes_enabled=settings.allow_nessie_writes,
        message=f"Bound Nessie customer {bound} for this server session.",
    )
