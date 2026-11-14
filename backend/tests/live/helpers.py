"""Helpers for opt-in live integration tests.

Safety:
- Never log API keys, account numbers, or full banking payloads.
- Bound retries for HTTP 429 only.
- TigerData isolation via LIVE_TEST_DATABASE_URL (must differ from DATABASE_URL).
- Live tests are read-mostly: never call DELETE /data, POST /simulations, or
  reset_demo. Cleanup deletes only rows owned by the live-test customer id.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from typing import Any, TypeVar
from urllib.parse import urlparse

import pytest

from app.config import Settings
from app.services.nessie_client import NessieClientError

T = TypeVar("T")

LIVE_TEST_CUSTOMER_ID = "live-test-customer-001"
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0)

_SECRET_KEYS = re.compile(
    r"(api[_-]?key|authorization|password|secret|token|bearer)",
    re.IGNORECASE,
)
_ID_KEYS = frozenset(
    {
        "account_id",
        "accountId",
        "_id",
        "id",
        "customer_id",
        "customerId",
        "nessie_customer_id",
    }
)


def env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def redact_value(key: str, value: Any) -> Any:
    """Return a log-safe representation of a single field."""
    if value is None:
        return None
    if _SECRET_KEYS.search(key):
        return "***REDACTED***"
    if key in _ID_KEYS or key.endswith("_id") or key.endswith("Id"):
        text = str(value)
        if len(text) <= 6:
            return "***"
        return f"{text[:3]}…{text[-2:]}({len(text)} chars)"
    if isinstance(value, str) and len(value) > 80:
        return f"{value[:24]}…({len(value)} chars)"
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, dict):
        return redact_mapping(value)
    if isinstance(value, list):
        return f"list[{len(value)}]"
    return type(value).__name__


def redact_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    return {str(k): redact_value(str(k), v) for k, v in payload.items()}


def summarize_rows(rows: list[dict[str, Any]], *, label: str) -> str:
    """One-line safe summary for assertions/debug (never dumps payloads)."""
    return f"{label}: count={len(rows)}"


def with_rate_limit_retries(fn: Callable[[], T], *, label: str = "request") -> T:
    """Retry only on HTTP 429 with bounded backoff."""
    last_exc: Exception | None = None
    for attempt, delay in enumerate(RETRY_BACKOFF_SECONDS[:MAX_RETRIES], start=1):
        try:
            return fn()
        except NessieClientError as exc:
            last_exc = exc
            if exc.status_code != 429 or attempt >= MAX_RETRIES:
                raise
            time.sleep(delay)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            raise
    assert last_exc is not None
    raise last_exc


def _normalize_db_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path or ""
    return f"{parsed.scheme}://{host}{port}{path}"


def require_isolated_database_url() -> str:
    """Return LIVE_TEST_DATABASE_URL; must differ from DATABASE_URL."""
    live_url = (os.getenv("LIVE_TEST_DATABASE_URL") or "").strip()
    shared_url = (os.getenv("DATABASE_URL") or "").strip()
    if not live_url:
        pytest.skip(
            "LIVE_TEST_DATABASE_URL is required for TigerData live tests "
            "(isolates writes from the HackUMass demo DATABASE_URL). "
            "Demo resets belong in: python scripts/reset_demo.py --reset"
        )
    if shared_url and _normalize_db_url(live_url) == _normalize_db_url(shared_url):
        pytest.fail(
            "Refusing live TigerData tests against shared DATABASE_URL. "
            "Point LIVE_TEST_DATABASE_URL at an isolated database. "
            "Demo data resets belong only in scripts/reset_demo.py --reset."
        )
    return live_url


def require_nessie_read_settings() -> Settings:
    api_key = (os.getenv("NESSIE_API_KEY") or "").strip()
    if not api_key:
        pytest.skip("NESSIE_API_KEY not configured")
    customer = (os.getenv("NESSIE_CUSTOMER_ID") or "").strip()
    if not customer:
        pytest.skip(
            "NESSIE_CUSTOMER_ID not configured "
            "(run: python scripts/reset_demo.py --reset)"
        )
    return Settings(
        use_mock_data=False,
        demo_customer_id=LIVE_TEST_CUSTOMER_ID,
        nessie_api_key=api_key,
        nessie_base_url=(
            os.getenv("NESSIE_BASE_URL") or "https://v2-prod-api.nessieisreal.com"
        ).rstrip("/"),
        nessie_customer_id=customer,
        database_url=None,
        gemini_api_key=None,
        gemini_model=os.getenv("GEMINI_MODEL") or "gemini-3.5-flash-lite",
    )


def require_live_sync_settings() -> Settings:
    nessie = require_nessie_read_settings()
    db_url = require_isolated_database_url()
    return Settings(
        use_mock_data=False,
        demo_customer_id=LIVE_TEST_CUSTOMER_ID,
        nessie_api_key=nessie.nessie_api_key,
        nessie_base_url=nessie.nessie_base_url,
        nessie_customer_id=nessie.nessie_customer_id,
        database_url=db_url,
        gemini_api_key=None,
        gemini_model=nessie.gemini_model,
    )


def require_gemini_settings() -> Settings:
    """Gemini live tests run with --run-live when GEMINI_API_KEY is set."""
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        pytest.skip("GEMINI_API_KEY not configured — skipping Gemini live tests")
    return Settings(
        use_mock_data=False,
        demo_customer_id=LIVE_TEST_CUSTOMER_ID,
        nessie_api_key=None,
        gemini_api_key=api_key,
        gemini_model=os.getenv("GEMINI_MODEL") or "gemini-3.5-flash-lite",
    )


def assert_money_quantized(value: Any) -> None:
    from decimal import Decimal

    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    assert amount == amount.quantize(Decimal("0.01"))
