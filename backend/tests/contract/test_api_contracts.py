"""API contract tests against the mock-backed FastAPI app."""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient


def test_health_contract(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["mode"] == "mock"


def test_accounts_contract_includes_ids(
    client: TestClient, demo_customer_id: str
) -> None:
    response = client.get(f"/customers/{demo_customer_id}/accounts")
    assert response.status_code == 200
    body = response.json()
    assert body["customer_id"] == demo_customer_id
    assert len(body["accounts"]) == 2
    for account in body["accounts"]:
        assert "account_id" in account
        assert account["customer_id"] == demo_customer_id
        assert "balance" in account


def test_transactions_contract_includes_ids(
    client: TestClient, demo_customer_id: str
) -> None:
    response = client.get(f"/customers/{demo_customer_id}/transactions")
    assert response.status_code == 200
    body = response.json()
    assert body["customer_id"] == demo_customer_id
    assert body["account_id"] is None
    assert len(body["transactions"]) >= 1
    txn = body["transactions"][0]
    assert "transaction_id" in txn
    assert "account_id" in txn
    assert txn["customer_id"] == demo_customer_id
    assert txn["kind"] in {"income", "expense", "transfer"}
    assert txn["status"] in {"posted", "pending"}


def test_transactions_filter_by_account(
    client: TestClient, demo_customer_id: str
) -> None:
    account_id = "acct-checking-001"
    response = client.get(
        f"/customers/{demo_customer_id}/transactions",
        params={"account_id": account_id},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["account_id"] == account_id
    assert all(t["account_id"] == account_id for t in body["transactions"])


def test_unknown_account_returns_404(
    client: TestClient, demo_customer_id: str
) -> None:
    response = client.get(
        f"/customers/{demo_customer_id}/transactions",
        params={"account_id": "does-not-exist"},
    )
    assert response.status_code == 404


def test_foreign_customer_forbidden(client: TestClient) -> None:
    response = client.get("/customers/someone-else/accounts")
    assert response.status_code == 403


def test_dashboard_contract(client: TestClient, demo_customer_id: str) -> None:
    response = client.get(f"/customers/{demo_customer_id}/dashboard")
    assert response.status_code == 200
    body = response.json()
    assert body["customer_id"] == demo_customer_id
    assert body["mode"] == "mock"
    assert "accounts" in body
    assert "money_flow" in body
    assert "forecast" in body
    assert isinstance(body["insight"], str) and body["insight"]
    money_flow = body["money_flow"]
    assert Decimal(money_flow["income_posted"]) == Decimal("3200.00")
    assert Decimal(money_flow["transfer_volume"]) == Decimal("300.00")
    assert money_flow["customer_id"] == demo_customer_id
    forecast = body["forecast"]
    assert forecast["customer_id"] == demo_customer_id
    assert forecast["horizon_days"] == 14
    assert len(forecast["points"]) == 15
    assert "projected_balance" in forecast["points"][0]


def test_money_flow_and_forecast_routes(
    client: TestClient, demo_customer_id: str
) -> None:
    flow = client.get(f"/customers/{demo_customer_id}/money-flow")
    assert flow.status_code == 200
    assert flow.json()["customer_id"] == demo_customer_id

    forecast = client.get(
        f"/customers/{demo_customer_id}/forecast",
        params={"horizon_days": 7, "lookback_days": 30},
    )
    assert forecast.status_code == 200
    body = forecast.json()
    assert body["horizon_days"] == 7
    assert len(body["points"]) == 8


def test_spending_analytics_mock_contract(
    client: TestClient, demo_customer_id: str
) -> None:
    response = client.get(
        f"/customers/{demo_customer_id}/analytics/spending",
        params={"lookback_days": 365},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["customer_id"] == demo_customer_id
    assert body["source"] == "memory"
    assert "categories" in body
    assert "expenses_posted" in body
    assert "net_posted" in body
