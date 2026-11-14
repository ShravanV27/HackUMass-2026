"""Read-only Capital One Nessie v2 HTTP client.

Uses the same Bearer auth pattern as the root smoke test. Only GET requests
are issued — no create/update/delete (avoids destructive sandbox writes).
"""

from __future__ import annotations

from typing import Any

import requests

from app.config import Settings


class NessieClientError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class NessieClient:
    """Thin wrapper around Nessie REST GET endpoints."""

    def __init__(
        self,
        settings: Settings,
        *,
        session: requests.Session | None = None,
        timeout: float = 15.0,
    ) -> None:
        if not settings.nessie_api_key:
            raise NessieClientError("NESSIE_API_KEY is not configured")
        self._base_url = settings.nessie_base_url.rstrip("/")
        self._api_key = settings.nessie_api_key
        self._session = session or requests.Session()
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        url = f"{self._base_url}{path}"
        # Include key query for classic Nessie compatibility; Bearer matches smoke test.
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
            raise NessieClientError(
                f"Nessie HTTP {response.status_code} for {path}",
                status_code=response.status_code,
            )
        if not response.content:
            return []
        try:
            return response.json()
        except ValueError as exc:
            raise NessieClientError("Nessie returned invalid JSON") from exc

    def list_accounts_for_customer(self, nessie_customer_id: str) -> list[dict[str, Any]]:
        payload = self._get(f"/customers/{nessie_customer_id}/accounts")
        return _as_list(payload)

    def list_all_accounts(self) -> list[dict[str, Any]]:
        payload = self._get("/accounts")
        return _as_list(payload)

    def list_purchases(self, account_id: str) -> list[dict[str, Any]]:
        return _as_list(self._get(f"/accounts/{account_id}/purchases"))

    def list_deposits(self, account_id: str) -> list[dict[str, Any]]:
        return _as_list(self._get(f"/accounts/{account_id}/deposits"))

    def list_withdrawals(self, account_id: str) -> list[dict[str, Any]]:
        return _as_list(self._get(f"/accounts/{account_id}/withdrawals"))

    def list_transfers(self, account_id: str) -> list[dict[str, Any]]:
        return _as_list(self._get(f"/accounts/{account_id}/transfers"))


def _as_list(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        # Some Nessie envelopes wrap results.
        for key in ("results", "data", "accounts", "purchases", "deposits"):
            inner = payload.get(key)
            if isinstance(inner, list):
                return [row for row in inner if isinstance(row, dict)]
        return [payload]
    return []
