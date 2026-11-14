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
    assert body["spend_model"] == "weekday"
    assert "next_events" in body
    point = body["points"][0]
    assert "scheduled_bill_total" in point
    assert "estimated_spend" in point
    assert "projected_balance" in point
    assert "projected_income" in point


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


def test_cashflow_week_month_aliases(
    client: TestClient, demo_customer_id: str
) -> None:
    week = client.get(
        f"/customers/{demo_customer_id}/predictions/cashflow",
        params={"horizon": "week"},
    )
    assert week.status_code == 200
    week_body = week.json()
    assert week_body["horizon_days"] == 7
    assert week_body["horizon"] == "week"
    assert len(week_body["points"]) == 8
    assert "days_until_zero" in week_body
    assert week_body["overdraft_risk"] in {"none", "watch", "likely"}
    assert "weekday_rates" in week_body

    month = client.get(
        f"/customers/{demo_customer_id}/predictions/cashflow",
        params={"horizon": "month"},
    )
    assert month.status_code == 200
    month_body = month.json()
    assert month_body["horizon_days"] == 30
    assert month_body["horizon"] == "month"
    assert len(month_body["points"]) == 31


def test_overdraft_behavior_scenarios_contracts(
    client: TestClient, demo_customer_id: str
) -> None:
    od = client.get(f"/customers/{demo_customer_id}/predictions/overdraft-risk")
    assert od.status_code == 200
    od_body = od.json()
    assert od_body["overdraft_risk"] in {"none", "watch", "likely"}
    assert "suggested_daily_cutback" in od_body

    behavior = client.get(f"/customers/{demo_customer_id}/predictions/behavior")
    assert behavior.status_code == 200
    b = behavior.json()
    assert b["segment"] in {"steady", "spiky", "weekend_heavy", "bill_stressed"}
    assert isinstance(b["tips"], list)
    assert b["tips"]

    scenarios = client.post(
        f"/customers/{demo_customer_id}/predictions/scenarios",
        json={
            "horizon_days": 14,
            "lookback_days": 30,
            "minimum_buffer": "250.00",
            "scenarios": [
                {"name": "baseline"},
                {"name": "cut_discretionary_20"},
                {"name": "aggressive_save"},
            ],
        },
    )
    assert scenarios.status_code == 200
    s = scenarios.json()
    assert len(s["scenarios"]) == 3
    assert "baseline_min_balance" in s


def test_payment_cycle_settings_and_sync(
    client: TestClient, demo_customer_id: str
) -> None:
    got = client.get(
        f"/customers/{demo_customer_id}/settings/payment-cycles"
    )
    assert got.status_code == 200
    assert got.json()["payment_cycle"]["cadence"] == "none"

    put = client.put(
        f"/customers/{demo_customer_id}/settings/payment-cycles",
        json={
            "cadence": "biweekly",
            "next_payday": "2026-11-21",
            "paycheck_amount": "3200.00",
            "minimum_buffer": "300.00",
            "bill_day_overrides": {"bill-rent-001": 5},
        },
    )
    assert put.status_code == 200
    body = put.json()
    assert body["payment_cycle"]["cadence"] == "biweekly"
    assert body["source"] == "memory"

    again = client.get(
        f"/customers/{demo_customer_id}/settings/payment-cycles"
    )
    assert again.json()["payment_cycle"]["paycheck_amount"] == "3200.00"

    cashflow = client.get(
        f"/customers/{demo_customer_id}/predictions/cashflow",
        params={"horizon_days": 30},
    )
    assert cashflow.status_code == 200
    assert cashflow.json()["income_assumed"] is True
    assert float(cashflow.json()["projected_income_total"]) > 0

    sync = client.post(f"/customers/{demo_customer_id}/sync")
    assert sync.status_code == 200
    assert sync.json()["status"] == "skipped"
    assert sync.json()["mode"] == "mock"
