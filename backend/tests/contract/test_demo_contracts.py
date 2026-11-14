"""Contract tests for Demo Lab + session bind endpoints."""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


@pytest.fixture
def writes_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_NESSIE_WRITES", "true")
    monkeypatch.setenv("USE_MOCK_DATA", "true")
    get_settings.cache_clear()


@pytest.fixture
def writes_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_NESSIE_WRITES", "false")
    monkeypatch.setenv("USE_MOCK_DATA", "true")
    get_settings.cache_clear()


def test_demo_writes_forbidden_when_flag_off(
    client: TestClient, demo_customer_id: str, writes_off: None
) -> None:
    response = client.post(
        f"/customers/{demo_customer_id}/demo/purchase",
        json={
            "account_id": "acct-checking-001",
            "amount": "25.00",
            "description": "blocked",
        },
    )
    assert response.status_code == 403


def test_demo_purchase_deposit_bill_contracts(
    client: TestClient, demo_customer_id: str, writes_on: None
) -> None:
    status = client.get(f"/customers/{demo_customer_id}/demo/status")
    assert status.status_code == 200
    assert status.json()["writes_enabled"] is True

    purchase = client.post(
        f"/customers/{demo_customer_id}/demo/purchase",
        json={
            "account_id": "acct-checking-001",
            "amount": "40.00",
            "description": "Headphones",
            "status": "posted",
            "minimum_buffer": "100.00",
        },
    )
    assert purchase.status_code == 200
    body = purchase.json()
    assert body["action"] == "purchase"
    assert "before" in body and "after" in body
    assert "delta_safe_to_spend" in body
    assert body["before"]["overdraft_risk"] in {"none", "watch", "likely"}
    assert body["after"]["overdraft_risk"] in {"none", "watch", "likely"}

    deposit = client.post(
        f"/customers/{demo_customer_id}/demo/deposit",
        json={
            "account_id": "acct-checking-001",
            "amount": "100.00",
            "status": "posted",
            "minimum_buffer": "100.00",
        },
    )
    assert deposit.status_code == 200
    assert Decimal(deposit.json()["delta_safe_to_spend"]) > 0

    bill = client.post(
        f"/customers/{demo_customer_id}/demo/bill",
        json={
            "account_id": "acct-checking-001",
            "payee": "Streaming Co",
            "amount": "15.00",
            "payment_date": "2026-11-25",
            "minimum_buffer": "100.00",
        },
    )
    assert bill.status_code == 200
    assert bill.json()["action"] == "bill"


def test_session_bind_contract(
    client: TestClient, demo_customer_id: str, writes_on: None
) -> None:
    session = client.get("/session")
    assert session.status_code == 200
    assert session.json()["app_customer_id"] == demo_customer_id

    bound = client.post(
        "/session/bind-customer",
        json={"nessie_customer_id": "sandbox-customer-xyz"},
    )
    assert bound.status_code == 200
    assert bound.json()["bound_nessie_customer_id"] == "sandbox-customer-xyz"

    again = client.get("/session")
    assert again.json()["bound_nessie_customer_id"] == "sandbox-customer-xyz"


def test_demo_forbids_foreign_customer(
    client: TestClient, writes_on: None
) -> None:
    assert (
        client.post(
            "/customers/other/demo/purchase",
            json={"account_id": "a", "amount": "1.00"},
        ).status_code
        == 403
    )


def test_scenarios_remain_separate_from_demo_writes(
    client: TestClient, demo_customer_id: str, writes_on: None
) -> None:
    """Hypothetical scenarios must not require ALLOW_NESSIE_WRITES semantics beyond math."""
    # Even with writes on, scenarios endpoint stays hypothetical (no resource_id/synced).
    response = client.post(
        f"/customers/{demo_customer_id}/predictions/scenarios",
        json={
            "horizon_days": 14,
            "scenarios": [{"name": "baseline"}, {"name": "one_time_purchase", "amount": "25.00"}],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert "scenarios" in body
    assert "synced" not in body
    assert "resource_id" not in body
