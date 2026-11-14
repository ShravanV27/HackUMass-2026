"""Read-only Capital One Nessie v2 HTTP client.

Uses the same Bearer auth pattern as the root smoke test. Only GET requests
are issued — no create/update/delete (avoids destructive sandbox writes).

Nessie v2 (`v2-prod-api`) notes:
- Collection GETs often return ``{"items": [...]}``.
- ``GET /accounts/{id}/purchases|deposits|withdrawals|transfers`` returns
  HTTP 405 (Allow: POST) — those paths are write-only on v2.
- Transaction history is listed via ``GET /accounts/{id}/transactions``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from app.config import Settings


class NessieClientError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class AccountCollection:
    """Account list plus whether the response looks complete (safe to reconcile)."""

    items: list[dict[str, Any]]
    complete: bool


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

    def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        tolerate_statuses: frozenset[int] | None = None,
    ) -> Any:
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
            if tolerate_statuses and response.status_code in tolerate_statuses:
                return []
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
        return self.fetch_accounts_for_customer(nessie_customer_id).items

    def list_all_accounts(self) -> list[dict[str, Any]]:
        return self.fetch_all_accounts().items

    def fetch_accounts_for_customer(self, nessie_customer_id: str) -> AccountCollection:
        payload = self._get(f"/customers/{nessie_customer_id}/accounts")
        items = _as_list(payload)
        return AccountCollection(
            items=items, complete=_is_complete_collection(payload, items)
        )

    def fetch_all_accounts(self) -> AccountCollection:
        payload = self._get("/accounts")
        items = _as_list(payload)
        return AccountCollection(
            items=items, complete=_is_complete_collection(payload, items)
        )

    def list_account_transactions(self, account_id: str) -> list[dict[str, Any]]:
        """Primary v2 history feed (GET allowed).

        Nessie v2 defaults ``limit`` to 100 and caps at 5000. Request the max so
        historical simulations are not truncated to the newest page.
        Account transaction responses are a single page (no cursor in OpenAPI).
        """
        return _as_list(
            self._get(
                f"/accounts/{account_id}/transactions",
                params={"limit": 5000},
            )
        )

    def list_purchases(self, account_id: str) -> list[dict[str, Any]]:
        # v2: POST-only (405). Classic API may still support GET.
        return _as_list(
            self._get(
                f"/accounts/{account_id}/purchases",
                tolerate_statuses=frozenset({404, 405}),
            )
        )

    def list_deposits(self, account_id: str) -> list[dict[str, Any]]:
        return _as_list(
            self._get(
                f"/accounts/{account_id}/deposits",
                tolerate_statuses=frozenset({404, 405}),
            )
        )

    def list_withdrawals(self, account_id: str) -> list[dict[str, Any]]:
        return _as_list(
            self._get(
                f"/accounts/{account_id}/withdrawals",
                tolerate_statuses=frozenset({404, 405}),
            )
        )

    def list_transfers(self, account_id: str) -> list[dict[str, Any]]:
        return _as_list(
            self._get(
                f"/accounts/{account_id}/transfers",
                tolerate_statuses=frozenset({404, 405}),
            )
        )

    def list_bills_for_customer(self, nessie_customer_id: str) -> list[dict[str, Any]]:
        """Scheduled/recurring obligations for one Nessie customer.

        Tries ``GET /customers/{id}/bills`` when present, then falls back to
        per-account ``GET /accounts/{id}/bills`` (Nessie v2 OpenAPI shape),
        and finally key-scoped ``GET /bills`` filtered by the customer's
        account ids.
        """
        direct = _as_list(
            self._get(
                f"/customers/{nessie_customer_id}/bills",
                tolerate_statuses=frozenset({404, 405}),
            )
        )
        if direct:
            return direct

        accounts = self.list_accounts_for_customer(nessie_customer_id)
        account_ids = {
            str(row.get("id") or row.get("_id") or "")
            for row in accounts
            if row.get("id") or row.get("_id")
        }
        collected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for account_id in sorted(account_ids):
            for bill in self.list_bills_for_account(account_id):
                bill_id = str(bill.get("id") or bill.get("_id") or "")
                if bill_id and bill_id in seen:
                    continue
                if bill_id:
                    seen.add(bill_id)
                collected.append(bill)
        if collected:
            return collected

        for bill in _as_list(
            self._get("/bills", tolerate_statuses=frozenset({404, 405}))
        ):
            account_id = str(bill.get("account_id") or "")
            if account_id and account_id in account_ids:
                collected.append(bill)
        return collected

    def list_bills_for_account(self, account_id: str) -> list[dict[str, Any]]:
        return _as_list(
            self._get(
                f"/accounts/{account_id}/bills",
                tolerate_statuses=frozenset({404, 405}),
            )
        )


# Nessie v2 commonly wraps collections as {"items": [...]}. Older/classic
# shapes may use results/data or resource-named keys.
_COLLECTION_KEYS = (
    "items",
    "results",
    "data",
    "accounts",
    "transactions",
    "purchases",
    "deposits",
    "withdrawals",
    "transfers",
    "bills",
)


def _as_list(payload: Any) -> list[dict[str, Any]]:
    """Normalize Nessie list payloads to a list of resource dicts."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in _COLLECTION_KEYS:
            inner = payload.get(key)
            if isinstance(inner, list):
                return [row for row in inner if isinstance(row, dict)]
        # Bare resource object (single account/txn), not a collection envelope.
        if payload.get("_id") is not None or payload.get("id") is not None:
            return [payload]
        return []
    return []


def _is_complete_collection(payload: Any, items: list[dict[str, Any]]) -> bool:
    """Return False when pagination / truncation metadata suggests a partial page.

    Incomplete collections must not trigger TigerData orphan deletion.
    """
    if payload is None:
        return True
    if isinstance(payload, list):
        return True
    if not isinstance(payload, dict):
        return True

    if (
        payload.get("next")
        or payload.get("next_cursor")
        or payload.get("next_page")
        or payload.get("next_url")
    ):
        return False
    if payload.get("has_more") is True or payload.get("hasMore") is True:
        return False

    for key in ("total", "count", "total_count", "totalCount"):
        raw_total = payload.get(key)
        if raw_total is None:
            continue
        try:
            total = int(raw_total)
        except (TypeError, ValueError):
            continue
        if total > len(items):
            return False

    return True
