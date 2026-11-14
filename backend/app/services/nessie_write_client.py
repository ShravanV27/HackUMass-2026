"""Nessie write client for Demo Lab (POST only — no DELETE / purge / reset).

Nessie **v2** create DTOs (OpenAPI):
- DepositCreateDTO: required ``amount_cents``; optional medium/description
- PurchaseCreateDTO: required ``merchant_id`` + ``amount_cents``; optional medium/description
- BillCreateDTO: status/payee/payment_amount (+ optional dates)

Classic fields (``amount``, ``status``, ``transaction_date``, ``purchase_date``)
are rejected with HTTP 422 on v2-prod.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import requests

from app.config import Settings
from app.services.nessie_client import NessieClientError, _as_list


CENT = Decimal("0.01")


def dollars_to_cents(amount: Decimal) -> int:
    """Convert dollar Decimal to integer cents for Nessie v2 write DTOs."""
    if amount <= 0:
        raise NessieClientError("amount must be positive")
    cents = int((amount / CENT).to_integral_value(rounding=ROUND_HALF_UP))
    if cents < 1:
        raise NessieClientError("amount must be at least 0.01")
    return cents


def build_deposit_body(
    *,
    amount: Decimal,
    description: str,
    medium: str = "balance",
) -> dict[str, Any]:
    """OpenAPI DepositCreateDTO — no status / amount / transaction_date."""
    return {
        "amount_cents": dollars_to_cents(amount),
        "medium": medium or "balance",
        "description": (description or "Demo Lab deposit")[:200],
    }


def build_purchase_body(
    *,
    amount: Decimal,
    description: str,
    merchant_id: str,
    medium: str = "balance",
) -> dict[str, Any]:
    """OpenAPI PurchaseCreateDTO — no status / amount / purchase_date."""
    mid = (merchant_id or "").strip()
    if not mid:
        raise NessieClientError("merchant_id is required for purchases")
    return {
        "merchant_id": mid,
        "amount_cents": dollars_to_cents(amount),
        "medium": medium or "balance",
        "description": (description or "Demo Lab purchase")[:200],
    }


def build_bill_body(
    *,
    payee: str,
    amount: Decimal,
    status: str = "pending",
    nickname: str | None = None,
    payment_date: date | None = None,
    recurring_date: int | None = None,
) -> dict[str, Any]:
    """OpenAPI BillCreateDTO."""
    allowed = {"pending", "cancelled", "completed", "recurring"}
    if recurring_date is not None:
        nessie_status = "recurring"
    else:
        # Map app "posted" → Nessie "completed".
        raw = (status or "pending").strip().lower()
        if raw == "posted":
            raw = "completed"
        nessie_status = raw if raw in allowed else "pending"
    body: dict[str, Any] = {
        "status": nessie_status,
        "payee": (payee or "Demo payee")[:80],
        "payment_amount": float(amount),
    }
    if nickname:
        body["nickname"] = nickname[:80]
    if payment_date is not None:
        body["payment_date"] = payment_date.isoformat()
    if recurring_date is not None:
        body["recurring_date"] = max(1, min(31, int(recurring_date)))
    return body


class NessieWriteClient:
    """POST purchases/deposits/bills. Never issues DELETE."""

    def __init__(
        self,
        settings: Settings,
        *,
        session: requests.Session | None = None,
        timeout: float = 20.0,
    ) -> None:
        if not settings.nessie_api_key:
            raise NessieClientError("NESSIE_API_KEY is not configured")
        self._base_url = settings.nessie_base_url.rstrip("/")
        self._api_key = settings.nessie_api_key
        self._session = session or requests.Session()
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        url = f"{self._base_url}{path}"
        try:
            response = self._session.post(
                url,
                headers=self._headers(),
                params={"key": self._api_key},
                json=body,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise NessieClientError(f"Nessie write failed: {exc}") from exc
        if response.status_code >= 400:
            detail = (response.text or "").strip().replace("\n", " ")[:500]
            raise NessieClientError(
                f"Nessie HTTP {response.status_code} for POST {path}: {detail}",
                status_code=response.status_code,
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise NessieClientError("Nessie returned invalid JSON") from exc

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        url = f"{self._base_url}{path}"
        query = {"key": self._api_key}
        if params:
            query.update(params)
        try:
            response = self._session.get(
                url,
                headers=self._headers(),
                params=query,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise NessieClientError(f"Nessie request failed: {exc}") from exc
        if response.status_code >= 400:
            detail = (response.text or "").strip().replace("\n", " ")[:500]
            raise NessieClientError(
                f"Nessie HTTP {response.status_code} for GET {path}: {detail}",
                status_code=response.status_code,
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise NessieClientError("Nessie returned invalid JSON") from exc

    def get_customer(self, nessie_customer_id: str) -> dict[str, Any]:
        payload = self._get(f"/customers/{nessie_customer_id}")
        if isinstance(payload, dict):
            return payload
        raise NessieClientError("Unexpected customer payload")

    def resolve_merchant_id(self, preferred: str | None = None) -> str:
        if preferred and preferred.strip():
            return preferred.strip()
        merchants = _as_list(self._get("/merchants"))
        for row in merchants:
            mid = str(row.get("_id") or row.get("id") or "")
            if mid:
                return mid
        raise NessieClientError(
            "No merchants available for this API key; create a merchant or pass merchant_id"
        )

    def create_purchase(
        self,
        account_id: str,
        *,
        amount: Decimal,
        description: str,
        status: str = "pending",  # ignored on v2; kept for call-site compatibility
        merchant_id: str | None = None,
        purchase_date: date | None = None,  # ignored on v2
    ) -> dict[str, Any]:
        del status, purchase_date  # not part of PurchaseCreateDTO
        body = build_purchase_body(
            amount=amount,
            description=description,
            merchant_id=self.resolve_merchant_id(merchant_id),
        )
        return self._created(self._post(f"/accounts/{account_id}/purchases", body))

    def create_deposit(
        self,
        account_id: str,
        *,
        amount: Decimal,
        description: str,
        status: str = "pending",  # ignored on v2; kept for call-site compatibility
        transaction_date: date | None = None,  # ignored on v2
    ) -> dict[str, Any]:
        del status, transaction_date  # not part of DepositCreateDTO
        body = build_deposit_body(amount=amount, description=description)
        return self._created(self._post(f"/accounts/{account_id}/deposits", body))

    def create_bill(
        self,
        account_id: str,
        *,
        payee: str,
        amount: Decimal,
        status: str = "pending",
        nickname: str | None = None,
        payment_date: date | None = None,
        recurring_date: int | None = None,
    ) -> dict[str, Any]:
        body = build_bill_body(
            payee=payee,
            amount=amount,
            status=status,
            nickname=nickname,
            payment_date=payment_date,
            recurring_date=recurring_date,
        )
        return self._created(self._post(f"/accounts/{account_id}/bills", body))

    @staticmethod
    def _created(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            created = payload.get("objectCreated") or payload.get("object_created")
            if isinstance(created, dict):
                return created
            return payload
        return {}
