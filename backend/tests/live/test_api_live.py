"""FastAPI endpoint contracts exercised in live mode (isolated TigerData)."""

from __future__ import annotations

import os
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services.providers.factory import get_provider
from tests.live.helpers import LIVE_TEST_CUSTOMER_ID, require_live_sync_settings

pytestmark = pytest.mark.live


@pytest.fixture
def live_api_env(synced_live_customer, monkeypatch: pytest.MonkeyPatch):
    """Point the FastAPI process settings at the isolated live-test stack."""
    settings = synced_live_customer["settings"]
    monkeypatch.setenv("USE_MOCK_DATA", "false")
    monkeypatch.setenv("DEMO_CUSTOMER_ID", LIVE_TEST_CUSTOMER_ID)
    monkeypatch.setenv("DATABASE_URL", settings.database_url or "")
    monkeypatch.setenv("NESSIE_API_KEY", settings.nessie_api_key or "")
    monkeypatch.setenv("NESSIE_BASE_URL", settings.nessie_base_url)
    monkeypatch.setenv("NESSIE_CUSTOMER_ID", settings.nessie_customer_id or "")
    # Avoid accidental Gemini calls from dashboard during API contract tests.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    get_settings.cache_clear()
    get_provider.cache_clear()
    yield settings
    get_settings.cache_clear()
    get_provider.cache_clear()


@pytest.fixture
def live_client(live_api_env) -> TestClient:
    return TestClient(app)


def test_health_reports_live_mode(live_client: TestClient) -> None:
    response = live_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["mode"] == "live"


def test_accounts_and_transactions_contracts(
    live_client: TestClient, live_test_customer_id: str
) -> None:
    accounts = live_client.get(f"/customers/{live_test_customer_id}/accounts")
    assert accounts.status_code == 200
    body = accounts.json()
    assert body["customer_id"] == live_test_customer_id
    assert isinstance(body["accounts"], list)

    txns = live_client.get(f"/customers/{live_test_customer_id}/transactions")
    assert txns.status_code == 200
    tbody = txns.json()
    assert tbody["customer_id"] == live_test_customer_id
    assert isinstance(tbody["transactions"], list)


def test_dashboard_contract_live_mode(
    live_client: TestClient, live_test_customer_id: str
) -> None:
    response = live_client.get(f"/customers/{live_test_customer_id}/dashboard")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "live"
    assert body["customer_id"] == live_test_customer_id
    assert "money_flow" in body
    assert "forecast" in body
    assert "insight" in body
    # Insight may note Gemini unavailable — still must include metrics context.
    assert isinstance(body["insight"], str)


def test_spending_analytics_sql_source(
    live_client: TestClient, live_test_customer_id: str
) -> None:
    response = live_client.get(
        f"/customers/{live_test_customer_id}/analytics/spending",
        params={"lookback_days": 30},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source"] in {"sql", "continuous_aggregate"}
    assert "categories" in body
    assert "daily" in body


def test_predictions_contracts_live(
    live_client: TestClient, live_test_customer_id: str
) -> None:
    cashflow = live_client.get(
        f"/customers/{live_test_customer_id}/predictions/cashflow",
        params={"horizon_days": 14, "lookback_days": 30},
    )
    assert cashflow.status_code == 200
    cbody = cashflow.json()
    assert len(cbody["points"]) == 15
    assert "scheduled_bills_total" in cbody
    assert "estimated_spend_total" in cbody

    safe = live_client.get(
        f"/customers/{live_test_customer_id}/predictions/safe-to-spend",
        params={"minimum_buffer": "250.00"},
    )
    assert safe.status_code == 200
    assert Decimal(safe.json()["minimum_buffer"]) == Decimal("250.00")

    drift = live_client.get(
        f"/customers/{live_test_customer_id}/predictions/drift"
    )
    assert drift.status_code == 200
    assert drift.json()["direction"] in {"up", "down", "flat"}

    what_if = live_client.post(
        f"/customers/{live_test_customer_id}/predictions/what-if",
        json={
            "amount": "15.00",
            "description": "live contract what-if",
            "minimum_buffer": "250.00",
            "horizon_days": 14,
        },
    )
    assert what_if.status_code == 200
    wbody = what_if.json()
    assert Decimal(wbody["purchase_amount"]) == Decimal("15.00")
    assert isinstance(wbody["affordable"], bool)


def test_live_api_forbids_foreign_customer(live_client: TestClient) -> None:
    assert (
        live_client.get("/customers/demo-customer-001/dashboard").status_code
        == 403
    )


def test_require_live_sync_settings_isolation_guard() -> None:
    """Sanity: helper used by the suite enforces isolation policy."""
    settings = require_live_sync_settings()
    assert settings.use_mock_data is False
    assert settings.demo_customer_id == LIVE_TEST_CUSTOMER_ID
    assert settings.database_url
    assert os.getenv("LIVE_TEST_DATABASE_URL")
