"""Application settings loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

# Load repo-root .env without requiring callers to set cwd.
_REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_REPO_ROOT / ".env")


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Runtime configuration.

    Secrets are read from the environment only; never hard-code credentials.
    """

    use_mock_data: bool = True
    demo_customer_id: str = "demo-customer-001"
    nessie_api_key: str | None = None
    nessie_base_url: str = "https://v2-prod-api.nessieisreal.com"
    # Optional Nessie sandbox customer mapped to demo_customer_id in live mode.
    nessie_customer_id: str | None = None
    database_url: str | None = None
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    # Demo Lab Nessie writes (purchase/deposit/bill). Default off — never destructive.
    allow_nessie_writes: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings(
        use_mock_data=_as_bool(os.getenv("USE_MOCK_DATA"), default=True),
        demo_customer_id=os.getenv("DEMO_CUSTOMER_ID") or "demo-customer-001",
        nessie_api_key=os.getenv("NESSIE_API_KEY") or None,
        nessie_base_url=os.getenv("NESSIE_BASE_URL")
        or "https://v2-prod-api.nessieisreal.com",
        nessie_customer_id=os.getenv("NESSIE_CUSTOMER_ID") or None,
        database_url=os.getenv("DATABASE_URL") or None,
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        gemini_model=os.getenv("GEMINI_MODEL") or "gemini-3.5-flash-lite",
        allow_nessie_writes=_as_bool(os.getenv("ALLOW_NESSIE_WRITES"), default=False),
    )
