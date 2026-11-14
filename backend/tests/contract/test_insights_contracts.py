"""Contract tests for alerts / insights / copilot endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_alerts_contract(client: TestClient, demo_customer_id: str) -> None:
    response = client.get(f"/customers/{demo_customer_id}/alerts")
    assert response.status_code == 200
    body = response.json()
    assert body["customer_id"] == demo_customer_id
    assert body["mode"] == "mock"
    assert isinstance(body["alerts"], list)
    assert body["analytics_source"] in {"memory", "sql", "continuous_aggregate"}
    for alert in body["alerts"]:
        assert alert["kind"] in {
            "overdraft_risk",
            "spending_drift",
            "category_spike",
            "bill_cluster",
        }
        assert alert["severity"] in {"info", "watch", "urgent"}
        assert "evidence" in alert


def test_insights_contract(client: TestClient, demo_customer_id: str) -> None:
    response = client.get(f"/customers/{demo_customer_id}/insights")
    assert response.status_code == 200
    body = response.json()
    assert body["ai_generated"] is False
    assert body["model"] is None
    assert "alert_explanation" in body
    assert "coaching" in body
    assert "safe_to_spend" in body["metric_glossary"]


def test_copilot_contract(client: TestClient, demo_customer_id: str) -> None:
    response = client.post(
        f"/customers/{demo_customer_id}/copilot",
        json={
            "question": "Why is my safe to spend this amount?",
            "include_scenario": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["customer_id"] == demo_customer_id
    assert body["answer"]
    assert body["ai_generated"] is False
    assert "safe_to_spend" in body["grounded_metrics"]
    assert body["refused"] is False


def test_copilot_purchase_scenario_contract(
    client: TestClient, demo_customer_id: str
) -> None:
    response = client.post(
        f"/customers/{demo_customer_id}/copilot",
        json={
            "question": "Can I afford a $500 purchase?",
            "include_scenario": True,
            "scenario_name": "cut_discretionary_20",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["grounded_metrics"]["scenario_name"] == "one_time_purchase"
    assert body["grounded_metrics"]["scenario_amount"] == "500.00"
    assert "scenario_affordable" in body["grounded_metrics"]


def test_insights_forbid_foreign_customer(client: TestClient) -> None:
    assert client.get("/customers/other/alerts").status_code == 403
    assert (
        client.post(
            "/customers/other/copilot",
            json={"question": "hello"},
        ).status_code
        == 403
    )
