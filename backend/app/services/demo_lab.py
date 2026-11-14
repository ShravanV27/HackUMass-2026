"""Demo Lab orchestration: snapshot → write → sync → recompute impact.

Hypothetical /scenarios stay separate — this module only performs real mock
overlay mutations or gated Nessie POSTs (never DELETE/reset).
"""

from __future__ import annotations

import time
from datetime import date
from decimal import Decimal
from typing import Literal

from app.config import Settings, get_settings
from app.domain.predictive_engine import (
    build_engine_forecast,
    compute_engine_safe_to_spend,
    compute_overdraft_risk,
)
from app.models.schemas import (
    DemoActionResponse,
    DemoBillRequest,
    DemoDepositRequest,
    DemoPurchaseRequest,
    PredictionSnapshot,
)
from app.services import mock_demo_state
from app.services.customer_settings import get_payment_cycle
from app.services.nessie_client import NessieClientError
from app.services.nessie_sync import resolve_nessie_customer_id
from app.services.nessie_write_client import NessieWriteClient
from app.services.providers.base import DataProvider
from app.services.providers.live import LiveDataProvider

ZERO = Decimal("0.00")
_BALANCE_RETRY_DELAY_S = 0.35
_BALANCE_SYNC_ATTEMPTS = 3


class DemoLabError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def require_writes_enabled(settings: Settings | None = None) -> Settings:
    cfg = settings or get_settings()
    if not cfg.allow_nessie_writes:
        raise DemoLabError(
            "Demo Lab writes are disabled. Set ALLOW_NESSIE_WRITES=true.",
            status_code=403,
        )
    return cfg


def snapshot_predictions(
    *,
    customer_id: str,
    provider: DataProvider,
    horizon_days: int,
    lookback_days: int,
    minimum_buffer: Decimal,
    settings: Settings | None = None,
) -> PredictionSnapshot:
    cfg = settings or get_settings()
    as_of = date.today()
    accounts = provider.list_accounts(customer_id)
    transactions = provider.list_transactions(customer_id)
    bills = provider.list_scheduled_bills(customer_id)
    payment_cycle, _ = get_payment_cycle(customer_id, settings=cfg)
    buffer = minimum_buffer
    safe = compute_engine_safe_to_spend(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=bills,
        as_of=as_of,
        horizon_days=horizon_days,
        lookback_days=lookback_days,
        minimum_buffer=buffer,
        payment_cycle=payment_cycle,
    )
    forecast = build_engine_forecast(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=bills,
        as_of=as_of,
        horizon_days=horizon_days,
        lookback_days=lookback_days,
        payment_cycle=payment_cycle,
        minimum_buffer=buffer,
    )
    risk = compute_overdraft_risk(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=bills,
        as_of=as_of,
        horizon_days=horizon_days,
        lookback_days=lookback_days,
        minimum_buffer=buffer,
        payment_cycle=payment_cycle,
    )
    return PredictionSnapshot(
        safe_to_spend=safe.safe_to_spend,
        min_projected_balance=forecast.min_balance,
        ending_balance=forecast.ending_balance,
        overdraft_risk=risk.overdraft_risk,
        days_until_zero=risk.days_until_zero,
        scheduled_bills_total=forecast.scheduled_bills_total,
        estimated_spend_total=forecast.estimated_spend_total,
    )


def _owned_account(provider: DataProvider, customer_id: str, account_id: str) -> None:
    accounts = provider.list_accounts(customer_id)
    if not any(a.account_id == account_id for a in accounts):
        raise DemoLabError(
            f"account_id {account_id} is not owned by this customer",
            status_code=404,
        )


def _account_balance(
    provider: DataProvider, customer_id: str, account_id: str
) -> Decimal | None:
    for account in provider.list_accounts(customer_id):
        if account.account_id == account_id:
            return account.balance
    return None


def _resync(provider: DataProvider, customer_id: str) -> bool:
    if isinstance(provider, LiveDataProvider):
        provider.force_sync(customer_id)
        return True
    return False


def _resync_after_cash_write(
    provider: DataProvider,
    customer_id: str,
    *,
    account_id: str,
    balance_before: Decimal | None,
    expected_delta: Decimal,
) -> bool:
    """Force-sync, retry briefly, then optimistically patch Tiger if Nessie lags.

    Capital One Nessie often returns the new deposit/purchase in the transaction
    feed before ``balance_cents`` moves. Without a local correction, Home keeps
    showing the pre-write balance while safe-to-spend already moves via pending.
    """
    if not isinstance(provider, LiveDataProvider):
        return False
    if balance_before is None:
        provider.force_sync(customer_id)
        return True

    expected = (balance_before + expected_delta).quantize(Decimal("0.01"))
    for attempt in range(_BALANCE_SYNC_ATTEMPTS):
        provider.force_sync(customer_id)
        current = _account_balance(provider, customer_id, account_id)
        if current is not None and current.quantize(Decimal("0.01")) == expected:
            return True
        # Any movement toward the write is enough — avoid double-patching.
        if current is not None and current != balance_before:
            return True
        if attempt + 1 < _BALANCE_SYNC_ATTEMPTS:
            time.sleep(_BALANCE_RETRY_DELAY_S)

    provider.apply_local_balance_delta(customer_id, account_id, expected_delta)
    return True


def run_purchase(
    *,
    customer_id: str,
    provider: DataProvider,
    body: DemoPurchaseRequest,
    settings: Settings | None = None,
) -> DemoActionResponse:
    cfg = require_writes_enabled(settings)
    _owned_account(provider, customer_id, body.account_id)
    balance_before = _account_balance(provider, customer_id, body.account_id)
    before = snapshot_predictions(
        customer_id=customer_id,
        provider=provider,
        horizon_days=body.horizon_days,
        lookback_days=body.lookback_days,
        minimum_buffer=body.minimum_buffer,
        settings=cfg,
    )
    # Mock overlay uses posted/pending; Nessie v2 deposit/purchase DTOs ignore status.
    mock_status = _mock_txn_status(body.status)
    if provider.mode == "mock":
        resource_id = mock_demo_state.apply_purchase(
            customer_id=customer_id,
            account_id=body.account_id,
            amount=body.amount,
            description=body.description,
            status=mock_status,
        )
        synced = False
        note = "Mock purchase applied in-memory (no Nessie call)."
    else:
        resource_id = _live_purchase(cfg, customer_id, body)
        synced = _resync_after_cash_write(
            provider,
            customer_id,
            account_id=body.account_id,
            balance_before=balance_before,
            expected_delta=-abs(body.amount),
        )
        note = "Nessie purchase created; TigerData resynced; predictions recomputed."

    after = snapshot_predictions(
        customer_id=customer_id,
        provider=provider,
        horizon_days=body.horizon_days,
        lookback_days=body.lookback_days,
        minimum_buffer=body.minimum_buffer,
        settings=cfg,
    )
    return _response(
        customer_id=customer_id,
        mode=provider.mode,
        action="purchase",
        resource_id=resource_id,
        account_id=body.account_id,
        amount=body.amount,
        description=body.description,
        synced=synced,
        before=before,
        after=after,
        note=note,
    )


def run_deposit(
    *,
    customer_id: str,
    provider: DataProvider,
    body: DemoDepositRequest,
    settings: Settings | None = None,
) -> DemoActionResponse:
    cfg = require_writes_enabled(settings)
    _owned_account(provider, customer_id, body.account_id)
    balance_before = _account_balance(provider, customer_id, body.account_id)
    before = snapshot_predictions(
        customer_id=customer_id,
        provider=provider,
        horizon_days=body.horizon_days,
        lookback_days=body.lookback_days,
        minimum_buffer=body.minimum_buffer,
        settings=cfg,
    )
    mock_status = _mock_txn_status(body.status)
    if provider.mode == "mock":
        resource_id = mock_demo_state.apply_deposit(
            customer_id=customer_id,
            account_id=body.account_id,
            amount=body.amount,
            description=body.description,
            status=mock_status,
        )
        synced = False
        note = "Mock deposit applied in-memory (no Nessie call)."
    else:
        resource_id = _live_deposit(cfg, customer_id, body)
        synced = _resync_after_cash_write(
            provider,
            customer_id,
            account_id=body.account_id,
            balance_before=balance_before,
            expected_delta=abs(body.amount),
        )
        note = "Nessie deposit created; TigerData resynced; predictions recomputed."

    after = snapshot_predictions(
        customer_id=customer_id,
        provider=provider,
        horizon_days=body.horizon_days,
        lookback_days=body.lookback_days,
        minimum_buffer=body.minimum_buffer,
        settings=cfg,
    )
    return _response(
        customer_id=customer_id,
        mode=provider.mode,
        action="deposit",
        resource_id=resource_id,
        account_id=body.account_id,
        amount=body.amount,
        description=body.description,
        synced=synced,
        before=before,
        after=after,
        note=note,
    )


def run_bill(
    *,
    customer_id: str,
    provider: DataProvider,
    body: DemoBillRequest,
    settings: Settings | None = None,
) -> DemoActionResponse:
    cfg = require_writes_enabled(settings)
    _owned_account(provider, customer_id, body.account_id)
    before = snapshot_predictions(
        customer_id=customer_id,
        provider=provider,
        horizon_days=body.horizon_days,
        lookback_days=body.lookback_days,
        minimum_buffer=body.minimum_buffer,
        settings=cfg,
    )
    if provider.mode == "mock":
        resource_id = mock_demo_state.apply_bill(
            customer_id=customer_id,
            account_id=body.account_id,
            payee=body.payee,
            amount=body.amount,
            payment_date=body.payment_date,
            recurring_day=body.recurring_date,
        )
        synced = False
        note = "Mock bill applied in-memory (no Nessie call)."
    else:
        resource_id = _live_bill(cfg, customer_id, body)
        # Bills are read from Nessie on demand — clear bills cache via force_sync.
        synced = _resync(provider, customer_id)
        if isinstance(provider, LiveDataProvider):
            provider.invalidate_bills_cache(customer_id)
        note = "Nessie bill created; TigerData resynced; predictions recomputed."

    after = snapshot_predictions(
        customer_id=customer_id,
        provider=provider,
        horizon_days=body.horizon_days,
        lookback_days=body.lookback_days,
        minimum_buffer=body.minimum_buffer,
        settings=cfg,
    )
    return _response(
        customer_id=customer_id,
        mode=provider.mode,
        action="bill",
        resource_id=resource_id,
        account_id=body.account_id,
        amount=body.amount,
        description=body.payee,
        synced=synced,
        before=before,
        after=after,
        note=note,
    )


def _mock_txn_status(status: str) -> str:
    """Map API status onto mock TransactionStatus values."""
    raw = (status or "pending").strip().lower()
    if raw in {"posted", "completed"}:
        return "posted"
    return "pending"


def _live_purchase(
    cfg: Settings,
    customer_id: str,
    body: DemoPurchaseRequest,
) -> str:
    _ensure_live_binding(cfg, customer_id)
    try:
        writer = NessieWriteClient(cfg)
        created = writer.create_purchase(
            body.account_id,
            amount=body.amount,
            description=body.description,
            merchant_id=body.merchant_id,
        )
    except NessieClientError as exc:
        # Surface upstream validation (e.g. 422) clearly in the API detail.
        raise DemoLabError(str(exc), status_code=502) from exc
    return str(created.get("_id") or created.get("id") or "purchase")


def _live_deposit(
    cfg: Settings,
    customer_id: str,
    body: DemoDepositRequest,
) -> str:
    _ensure_live_binding(cfg, customer_id)
    try:
        writer = NessieWriteClient(cfg)
        created = writer.create_deposit(
            body.account_id,
            amount=body.amount,
            description=body.description,
        )
    except NessieClientError as exc:
        raise DemoLabError(str(exc), status_code=502) from exc
    return str(created.get("_id") or created.get("id") or "deposit")


def _live_bill(
    cfg: Settings, customer_id: str, body: DemoBillRequest
) -> str:
    _ensure_live_binding(cfg, customer_id)
    try:
        writer = NessieWriteClient(cfg)
        created = writer.create_bill(
            body.account_id,
            payee=body.payee,
            amount=body.amount,
            nickname=body.nickname,
            payment_date=body.payment_date,
            recurring_date=body.recurring_date,
        )
    except NessieClientError as exc:
        raise DemoLabError(str(exc), status_code=502) from exc
    return str(created.get("_id") or created.get("id") or "bill")


def _ensure_live_binding(cfg: Settings, customer_id: str) -> str:
    try:
        return resolve_nessie_customer_id(cfg, customer_id)
    except PermissionError as exc:
        raise DemoLabError(str(exc), status_code=403) from exc


def _response(
    *,
    customer_id: str,
    mode: Literal["mock", "live"],
    action: Literal["purchase", "deposit", "bill"],
    resource_id: str,
    account_id: str,
    amount: Decimal,
    description: str,
    synced: bool,
    before: PredictionSnapshot,
    after: PredictionSnapshot,
    note: str,
) -> DemoActionResponse:
    return DemoActionResponse(
        customer_id=customer_id,
        mode=mode,
        action=action,
        resource_id=resource_id,
        account_id=account_id,
        amount=amount,
        description=description,
        synced=synced,
        before=before,
        after=after,
        delta_safe_to_spend=after.safe_to_spend - before.safe_to_spend,
        delta_min_balance=after.min_projected_balance - before.min_projected_balance,
        note=note,
    )


def bind_customer_session(
    *,
    app_customer_id: str,
    nessie_customer_id: str,
    provider: DataProvider,
    settings: Settings | None = None,
) -> str:
    """Validate (live) and bind a Nessie sandbox customer to the app session."""
    from app.services.session_bindings import bind_nessie_customer

    cfg = settings or get_settings()
    cleaned = nessie_customer_id.strip()
    if not cleaned:
        raise DemoLabError("nessie_customer_id is required", status_code=422)

    if provider.mode == "live":
        try:
            writer = NessieWriteClient(cfg)
            writer.get_customer(cleaned)
        except NessieClientError as exc:
            raise DemoLabError(
                f"Unable to verify Nessie customer: {exc}",
                status_code=404 if getattr(exc, "status_code", None) == 404 else 502,
            ) from exc

    bound = bind_nessie_customer(app_customer_id, cleaned)
    if isinstance(provider, LiveDataProvider):
        provider.invalidate_sync(app_customer_id)
    return bound
