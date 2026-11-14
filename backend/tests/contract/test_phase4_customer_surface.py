"""Phase 4 customer-surface contracts — endpoints Home / What If? / Settings use."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_home_surface_endpoints(
    client: TestClient, demo_customer_id: str
) -> None:
    for path in (
        f"/customers/{demo_customer_id}/accounts",
        f"/customers/{demo_customer_id}/transactions",
        f"/customers/{demo_customer_id}/predictions/cashflow?horizon_days=7",
        f"/customers/{demo_customer_id}/predictions/cashflow?horizon_days=14",
        f"/customers/{demo_customer_id}/predictions/cashflow?horizon_days=30",
        f"/customers/{demo_customer_id}/predictions/safe-to-spend?horizon_days=14&minimum_buffer=250.00",
        f"/customers/{demo_customer_id}/predictions/overdraft-risk?horizon_days=14",
        f"/customers/{demo_customer_id}/alerts",
        f"/customers/{demo_customer_id}/insights",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        body = response.json()
        assert "customer_id" in body or "accounts" in body or "transactions" in body


def test_what_if_scenarios_remain_hypothetical(
    client: TestClient, demo_customer_id: str
) -> None:
    before = client.get(f"/customers/{demo_customer_id}/accounts").json()
    response = client.post(
        f"/customers/{demo_customer_id}/predictions/scenarios",
        json={
            "horizon_days": 14,
            "minimum_buffer": "250.00",
            "scenarios": [
                {"name": "baseline"},
                {"name": "cut_discretionary_20"},
                {"name": "one_time_purchase", "amount": "80.00"},
                {"name": "extra_paycheck", "amount": "500.00"},
                {"name": "pause_category", "category": "dining"},
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["scenarios"]) >= 2
    names = {row["name"] for row in body["scenarios"]}
    assert "baseline" in names
    assert "resource_id" not in body
    assert "synced" not in body

    after = client.get(f"/customers/{demo_customer_id}/accounts").json()
    assert before["accounts"] == after["accounts"]


def test_payment_cycle_settings_round_trip(
    client: TestClient, demo_customer_id: str
) -> None:
    put = client.put(
        f"/customers/{demo_customer_id}/settings/payment-cycles",
        json={
            "cadence": "biweekly",
            "next_payday": "2026-11-21",
            "paycheck_amount": "1800.00",
            "minimum_buffer": "300.00",
            "bill_day_overrides": {},
        },
    )
    assert put.status_code == 200
    body = put.json()
    assert body["payment_cycle"]["cadence"] == "biweekly"
    assert body["payment_cycle"]["minimum_buffer"] in ("300.00", "300.0", 300.0, "300")

    get = client.get(f"/customers/{demo_customer_id}/settings/payment-cycles")
    assert get.status_code == 200
    assert get.json()["payment_cycle"]["cadence"] == "biweekly"

    # Restore none so later tests stay income-free by default.
    reset = client.put(
        f"/customers/{demo_customer_id}/settings/payment-cycles",
        json={
            "cadence": "none",
            "next_payday": None,
            "paycheck_amount": "0.00",
            "minimum_buffer": "250.00",
            "bill_day_overrides": {},
        },
    )
    assert reset.status_code == 200


def test_backtest_and_dated_what_if_contracts(
    client: TestClient, demo_customer_id: str
) -> None:
    backtest = client.get(
        f"/customers/{demo_customer_id}/predictions/backtest?horizon_days=7&windows=2"
    )
    assert backtest.status_code == 200
    body = backtest.json()
    assert "sufficient_history" in body
    assert "assumptions" in body

    what_if = client.post(
        f"/customers/{demo_customer_id}/predictions/what-if",
        json={
            "amount": "35.00",
            "description": "Dated purchase",
            "minimum_buffer": "100.00",
            "horizon_days": 14,
            "purchase_date": "2026-11-20",
        },
    )
    assert what_if.status_code == 200
    payload = what_if.json()
    assert payload["purchase_date"] == "2026-11-20"
    assert isinstance(payload.get("assumptions"), list)

    cashflow = client.get(
        f"/customers/{demo_customer_id}/predictions/cashflow?horizon_days=14"
    )
    assert cashflow.status_code == 200
    cf = cashflow.json()
    assert "assumptions" in cf
    assert cf["confidence"] in {"low", "medium", "high"}


def test_behavior_and_what_if_purchase(
    client: TestClient, demo_customer_id: str
) -> None:
    behavior = client.get(f"/customers/{demo_customer_id}/predictions/behavior")
    assert behavior.status_code == 200
    assert behavior.json()["segment"] in {
        "steady",
        "spiky",
        "weekend_heavy",
        "bill_stressed",
    }

    what_if = client.post(
        f"/customers/{demo_customer_id}/predictions/what-if",
        json={
            "amount": "40.00",
            "description": "Phase 4 purchase check",
            "minimum_buffer": "250.00",
            "horizon_days": 14,
        },
    )
    assert what_if.status_code == 200
    payload = what_if.json()
    assert "safe_to_spend_before" in payload
    assert "affordable" in payload
