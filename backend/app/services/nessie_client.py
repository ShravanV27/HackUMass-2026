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
from urllib.parse import parse_qsl, urlparse

import requests

from app.config import Settings

# Nessie caps a transaction page at 5000. A page that fills the requested
# limit is not proof the feed ended.
_TRANSACTION_PAGE_LIMIT = 5000
# Stop walking cursors even if a feed keeps advertising a new page.
_MAX_COLLECTION_PAGES = 20


class NessieClientError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class AccountCollection:
    """Account list plus whether the response looks complete (safe to reconcile).

    ``reported_total`` is the explicit numeric ``total`` from the payload when
    Nessie sent one. ``0`` is the only signal that an empty account list is a
    real empty customer rather than a missing body.
    """

    items: list[dict[str, Any]]
    complete: bool
    reported_total: int | None = None


@dataclass(frozen=True)
class TransactionFeed:
    """One account's transaction page plus whether it is safe to treat as complete."""

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
        items, complete, reported_total = self._collect_pages(
            f"/customers/{nessie_customer_id}/accounts"
        )
        return AccountCollection(
            items=items, complete=complete, reported_total=reported_total
        )

    def fetch_all_accounts(self) -> AccountCollection:
        items, complete, reported_total = self._collect_pages("/accounts")
        return AccountCollection(
            items=items, complete=complete, reported_total=reported_total
        )

    def list_account_transactions(self, account_id: str) -> list[dict[str, Any]]:
        """Primary v2 history feed (GET allowed)."""
        return self.fetch_account_transactions(account_id).items

    def fetch_account_transactions(self, account_id: str) -> TransactionFeed:
        """Primary v2 history feed plus completeness (safe to drop missing rows).

        Nessie v2 defaults ``limit`` to 100 and caps at 5000. Request the max and
        follow ``next`` / ``has_more`` until the feed ends. A truncated, failed,
        or looping page stays incomplete so sync will not delete TigerData rows
        that were never fetched.
        """
        items, complete, _reported_total = self._collect_pages(
            f"/accounts/{account_id}/transactions",
            params={"limit": _TRANSACTION_PAGE_LIMIT},
            limit=_TRANSACTION_PAGE_LIMIT,
        )
        return TransactionFeed(items=items, complete=complete)

    def _collect_pages(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> tuple[list[dict[str, Any]], bool, int | None]:
        """Follow Nessie collection pages. Fail closed on gaps, loops, and errors.

        The first-page error still propagates so a down feed does not commit.
        A later page that fails, times out, repeats a cursor, or exceeds
        ``_MAX_COLLECTION_PAGES`` returns the rows already fetched and
        ``complete=False``.
        """
        collected: list[dict[str, Any]] = []
        seen: set[str] = set()
        request_path = path.split("?", 1)[0]
        request_params = {
            key: value
            for key, value in dict(params or {}).items()
            if key != "key"
        }
        reported_total: int | None = None
        followed_link = False

        for page_index in range(1, _MAX_COLLECTION_PAGES + 1):
            identity = _request_identity(request_path, request_params)
            if identity in seen:
                return collected, False, reported_total
            seen.add(identity)
            try:
                payload = self._get(request_path, params=request_params or None)
            except NessieClientError:
                if page_index == 1:
                    raise
                return collected, False, reported_total

            page_items = _as_list(payload)
            collected.extend(page_items)
            found_total, total = _parse_total(payload)
            if found_total and total is not None:
                reported_total = total

            if _wants_another_page(payload):
                if page_index >= _MAX_COLLECTION_PAGES:
                    return collected, False, reported_total
                nxt = self._follow_target(payload, request_path, request_params, limit)
                if nxt is None:
                    return collected, False, reported_total
                request_path, request_params = nxt
                followed_link = True
                continue

            complete = _is_complete_collection(
                payload,
                collected,
                limit=limit,
                followed_link=followed_link,
                last_page_count=len(page_items),
            )
            if reported_total is not None and reported_total != len(collected):
                complete = False
            return collected, complete, reported_total

        return collected, False, reported_total

    def _follow_target(
        self,
        payload: Any,
        current_path: str,
        current_params: dict[str, Any],
        limit: int | None,
    ) -> tuple[str, dict[str, Any]] | None:
        """Next request, or None when the page says more data but has no cursor."""
        if not isinstance(payload, dict):
            return None
        for key in ("next", "next_url"):
            raw = payload.get(key)
            if isinstance(raw, str) and raw.strip():
                parsed = _parse_next_url(raw.strip(), self._base_url)
                if parsed is None:
                    return None
                path, params = parsed
                if limit is not None and "limit" not in params:
                    params["limit"] = limit
                return path, params

        cursor = payload.get("next_cursor")
        if not isinstance(cursor, str) or not cursor.strip():
            cursor = payload.get("nextCursor")
        if isinstance(cursor, str) and cursor.strip():
            params = dict(current_params)
            params["cursor"] = cursor.strip()
            params.pop("key", None)
            return current_path.split("?", 1)[0], params

        page = payload.get("next_page", payload.get("nextPage"))
        page_number: int | None = None
        if isinstance(page, int) and not isinstance(page, bool) and page > 0:
            page_number = page
        elif isinstance(page, str) and page.strip().isdigit() and int(page.strip()) > 0:
            page_number = int(page.strip())
        if page_number is None:
            return None
        params = dict(current_params)
        params["page"] = page_number
        params.pop("key", None)
        if limit is not None and "limit" not in params:
            params["limit"] = limit
        return current_path.split("?", 1)[0], params

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


def _is_complete_collection(
    payload: Any,
    items: list[dict[str, Any]],
    *,
    limit: int | None = None,
    followed_link: bool = False,
    last_page_count: int | None = None,
) -> bool:
    """Fail closed. Missing pagination metadata is not a complete snapshot.

    Complete only when ``has_more`` is explicitly false and a numeric total
    equals the rows in hand, or every linked page was followed and the last
    page is shorter than the requested limit. Empty bodies, JSON null, bare
    lists, error objects, and single resources are never complete. A page that
    fills ``limit`` (5000 for transactions) is incomplete unless the explicit
    total matches.
    """
    page_count = len(items) if last_page_count is None else last_page_count
    found_total, total = _parse_total(payload)
    has_more = _explicit_has_more(payload)
    nxt = _next_token(payload)

    if found_total and total is None:
        return False
    if has_more is True or nxt:
        return False

    explicit_zero = (
        isinstance(payload, dict)
        and found_total
        and total == 0
        and len(items) == 0
        and has_more is not True
        and not nxt
        and not _is_single_resource(payload)
    )
    if explicit_zero:
        if _is_collection_envelope(payload):
            return True
        extra = set(payload) - {
            "total",
            "count",
            "total_count",
            "totalCount",
            "has_more",
            "hasMore",
        }
        if not extra:
            return True

    if not isinstance(payload, dict) or not _is_collection_envelope(payload):
        return False

    full_page = limit is not None and page_count >= limit
    total_matches = bool(found_total and total == len(items))
    if has_more is False and total_matches:
        return True
    if full_page:
        return False
    if (
        followed_link
        and not nxt
        and has_more is not True
        and (limit is None or page_count < limit)
        and (not found_total or total == len(items))
        and (len(items) > 0 or total == 0)
    ):
        return True
    return False


def _is_collection_envelope(payload: dict[str, Any]) -> bool:
    return any(isinstance(payload.get(key), list) for key in _COLLECTION_KEYS)


def _is_single_resource(payload: dict[str, Any]) -> bool:
    if _is_collection_envelope(payload):
        return False
    return payload.get("_id") is not None or payload.get("id") is not None


def _explicit_has_more(payload: Any) -> bool | None:
    """True/False when has_more is explicit, else None.

    Accepts bool and the string/int forms ``true`` / ``1`` / ``false`` / ``0``.
    """
    if not isinstance(payload, dict):
        return None
    if "has_more" in payload:
        raw = payload.get("has_more")
    elif "hasMore" in payload:
        raw = payload.get("hasMore")
    else:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int):
        if raw == 1:
            return True
        if raw == 0:
            return False
        return None
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text in {"true", "1", "yes"}:
            return True
        if text in {"false", "0", "no"}:
            return False
    return None


def _parse_total(payload: Any) -> tuple[bool, int | None]:
    """Return (key_present, value). Value is None when the key is garbage."""
    if not isinstance(payload, dict):
        return False, None
    for key in ("total", "count", "total_count", "totalCount"):
        if key not in payload:
            continue
        raw = payload.get(key)
        if isinstance(raw, bool):
            return True, None
        if isinstance(raw, int):
            return (True, raw) if raw >= 0 else (True, None)
        if isinstance(raw, float) and raw.is_integer() and raw >= 0:
            return True, int(raw)
        if isinstance(raw, str):
            text = raw.strip()
            if text.isdigit():
                return True, int(text)
            return True, None
        return True, None
    return False, None


def _next_token(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("next", "next_url", "next_cursor", "nextCursor", "next_page", "nextPage"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        if (
            key in {"next_page", "nextPage"}
            and isinstance(raw, int)
            and not isinstance(raw, bool)
            and raw > 0
        ):
            return str(raw)
    return None


def _wants_another_page(payload: Any) -> bool:
    if _explicit_has_more(payload) is True:
        return True
    return _next_token(payload) is not None


def _parse_next_url(raw: str, base_url: str) -> tuple[str, dict[str, Any]] | None:
    """Accept a relative path or a URL on this client's host. Reject other hosts."""
    base = base_url.rstrip("/")
    if raw.startswith(base):
        raw = raw[len(base) :] or "/"
    if raw.startswith("http://") or raw.startswith("https://"):
        return None
    if not raw.startswith("/"):
        return None
    parsed = urlparse(raw)
    params = {
        key: value
        for key, value in parse_qsl(parsed.query, keep_blank_values=False)
        if key != "key"
    }
    path = parsed.path or "/"
    return path, params


def _request_identity(path: str, params: dict[str, Any] | None) -> str:
    query = [
        f"{key}={value}"
        for key, value in sorted((params or {}).items())
        if key != "key"
    ]
    return path.split("?", 1)[0] + "?" + "&".join(query)
