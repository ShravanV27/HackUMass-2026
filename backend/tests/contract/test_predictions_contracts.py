"""Contract tests for M5 prediction endpoints."""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient


def test_cashflow_prediction_contract(
    client: TestClient, demo_customer_id: str
) -> None:
    response = client.get(
        f"/customers/{demo_customer_id}/predictions/cashflow",
        params={"horizon_days": 14, "lookback_days": 30},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["customer_id"] == demo_customer_id
    assert body["horizon_days"] == 14
    assert len(body["points"]) == 15
    assert "scheduled_bills_total" in body
    assert "estimated_spend_total" in body
    assert "daily_estimated_spend" in body
    assert isinstance(body["scheduled_bills_in_horizon"], list)
    point = body["points"][0]
    assert "scheduled_bill_total" in point
    assert "estimated_spend" in point
    assert "projected_balance" in point


def test_safe_to_spend_contract(client: TestClient, demo_customer_id: str) -> None:
    response = client.get(
        f"/customers/{demo_customer_id}/predictions/safe-to-spend",
        params={"minimum_buffer": "250.00"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["customer_id"] == demo_customer_id
    assert Decimal(body["minimum_buffer"]) == Decimal("250.00")
    assert "safe_to_spend" in body
    assert "scheduled_bills_total" in body
    assert "estimated_spend_total" in body


def test_drift_contract(client: TestClient, demo_customer_id: str) -> None:
    response = client.get(
        f"/customers/{demo_customer_id}/predictions/drift",
        params={"recent_days": 7, "baseline_days": 28},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["customer_id"] == demo_customer_id
    assert body["direction"] in {"up", "down", "flat"}
    assert "recent_spend" in body
    assert "baseline_spend" in body
    assert "drift_pct" in body


def test_what_if_contract(client: TestClient, demo_customer_id: str) -> None:
    response = client.post(
        f"/customers/{demo_customer_id}/predictions/what-if",
        json={
            "amount": "25.00",
            "description": "Coffee maker",
            "minimum_buffer": "250.00",
            "horizon_days": 14,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["customer_id"] == demo_customer_id
    assert Decimal(body["purchase_amount"]) == Decimal("25.00")
    assert isinstance(body["affordable"], bool)
    assert "safe_to_spend_before" in body
    assert "safe_to_spend_after" in body
    assert "projected_min_balance_after" in body


def test_predictions_forbid_foreign_customer(client: TestClient) -> None:
    assert client.get("/customers/other/predictions/cashflow").status_code == 403
    assert (
        client.post(
            "/customers/other/predictions/what-if",
            json={"amount": "10.00"},
        ).status_code
        == 403
    )


def test_legacy_forecast_still_available(
    client: TestClient, demo_customer_id: str
) -> None:
    """Preserve existing linear forecast contract used by dashboard."""
    response = client.get(f"/customers/{demo_customer_id}/forecast")
    assert response.status_code == 200
    body = response.json()
    assert body["horizon_days"] == 14
    assert len(body["points"]) == 15
    assert "daily_net_rate" in body
