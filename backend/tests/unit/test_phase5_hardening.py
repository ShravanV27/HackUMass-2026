"""Phase 5 — dated purchases, assumptions, backtest regressions."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.config import Settings
from app.domain.predictive_engine import (
    backtest_spend_forecast,
    build_engine_forecast,
    simulate_scenarios,
    simulate_what_if_with_engine,
)
from app.domain.scenario_intent import parse_scenario_intent
from app.models.schemas import (
    Account,
    CopilotRequest,
    ScenarioRequest,
    ScenarioSpec,
    ScheduledBill,
    Transaction,
    TransactionKind,
    TransactionStatus,
)
from app.services.insights_service import answer_copilot
from app.services.providers.mock import MockDataProvider


AS_OF = date(2026, 11, 14)


def _cash_account(balance: str = "1000.00") -> Account:
    return Account(
        account_id="acc-1",
        customer_id="demo-customer-001",
        name="Checking",
        account_type="Checking",
        balance=Decimal(balance),
        currency="USD",
    )


def test_deferred_purchase_hits_chosen_day_not_day_zero() -> None:
    purchase_day = AS_OF + timedelta(days=5)
    forecast = build_engine_forecast(
        customer_id="demo-customer-001",
        accounts=[_cash_account()],
        transactions=[],
        scheduled_bills=[],
        as_of=AS_OF,
        horizon_days=14,
        one_time_purchase=Decimal("200.00"),
        one_time_purchase_on=purchase_day,
    )
    day0 = forecast.points[0]
    assert day0.projected_balance == Decimal("1000.00")
    assert "Purchase" not in day0.event_labels

    purchase_point = next(p for p in forecast.points if p.date == purchase_day)
    assert "Purchase" in purchase_point.event_labels
    assert any(a for a in forecast.assumptions if "2026-11-19" in a)
    assert forecast.confidence in {"low", "medium", "high"}


def test_immediate_purchase_still_reduces_starting_path() -> None:
    forecast = build_engine_forecast(
        customer_id="demo-customer-001",
        accounts=[_cash_account()],
        transactions=[],
        scheduled_bills=[],
        as_of=AS_OF,
        horizon_days=7,
        one_time_purchase=Decimal("100.00"),
    )
    assert forecast.points[0].projected_balance == Decimal("900.00")
    assert forecast.starting_balance == Decimal("1000.00")  # raw cash, not reduced


def test_scenario_on_date_propagates() -> None:
    on_date = AS_OF + timedelta(days=3)
    result = simulate_scenarios(
        customer_id="demo-customer-001",
        accounts=[_cash_account("800.00")],
        transactions=[],
        scheduled_bills=[],
        as_of=AS_OF,
        request=ScenarioRequest(
            horizon_days=10,
            scenarios=[
                ScenarioSpec(name="baseline"),
                ScenarioSpec(
                    name="one_time_purchase",
                    amount=Decimal("50.00"),
                    on_date=on_date,
                ),
            ],
        ),
    )
    purchase = next(s for s in result.scenarios if s.name == "one_time_purchase")
    assert on_date.isoformat() in purchase.note
    assert result.assumptions


def test_what_if_purchase_date_in_response() -> None:
    on_date = AS_OF + timedelta(days=2)
    result = simulate_what_if_with_engine(
        customer_id="demo-customer-001",
        accounts=[_cash_account("600.00")],
        transactions=[],
        scheduled_bills=[],
        purchase_amount=Decimal("40.00"),
        description="Concert",
        as_of=AS_OF,
        purchase_date=on_date,
    )
    assert result.purchase_date == on_date
    assert result.assumptions
    assert "Concert" in result.note


def test_intent_parses_relative_purchase_date() -> None:
    intent = parse_scenario_intent(
        "Can I afford a $120 purchase in 3 days?",
        include_scenario=True,
        as_of=AS_OF,
    )
    assert intent.ready
    assert intent.name == "one_time_purchase"
    assert intent.amount == Decimal("120.00")
    assert intent.on_date == AS_OF + timedelta(days=3)


def test_copilot_grounds_dated_purchase() -> None:
    provider = MockDataProvider()
    settings = Settings(use_mock_data=True, demo_customer_id="demo-customer-001")
    result = answer_copilot(
        customer_id="demo-customer-001",
        provider=provider,
        body=CopilotRequest(
            question="Can I afford a $90 purchase in 4 days?",
            include_scenario=True,
            minimum_buffer=Decimal("50.00"),
        ),
        settings=settings,
        as_of=AS_OF,
    )
    assert result.grounded_metrics["scenario_name"] == "one_time_purchase"
    assert result.grounded_metrics["scenario_on_date"] == (AS_OF + timedelta(days=4)).isoformat()
    assert "scenario_affordable" in result.grounded_metrics


def test_backtest_reports_insufficient_history_clearly() -> None:
    result = backtest_spend_forecast(
        customer_id="demo-customer-001",
        accounts=[_cash_account()],
        transactions=[],
        scheduled_bills=[],
        as_of=AS_OF,
        horizon_days=7,
        windows=3,
    )
    assert result.sufficient_history is False
    assert result.windows_evaluated == 0
    assert result.assumptions


def test_backtest_evaluates_windows_with_synthetic_history() -> None:
    txns: list[Transaction] = []
    # Build ~90 days of small daily expenses so backtest has coverage.
    for offset in range(90, 0, -1):
        day = AS_OF - timedelta(days=offset)
        txns.append(
            Transaction(
                transaction_id=f"t-{offset}",
                customer_id="demo-customer-001",
                account_id="acc-1",
                amount=Decimal("-25.00"),
                kind=TransactionKind.EXPENSE,
                status=TransactionStatus.POSTED,
                description="Coffee",
                category="dining",
                posted_at=datetime(
                    day.year, day.month, day.day, 12, 0, tzinfo=timezone.utc
                ),
            )
        )
    result = backtest_spend_forecast(
        customer_id="demo-customer-001",
        accounts=[_cash_account("2000.00")],
        transactions=txns,
        scheduled_bills=[
            ScheduledBill(
                bill_id="b1",
                customer_id="demo-customer-001",
                account_id="acc-1",
                name="Rent",
                amount=Decimal("1000.00"),
                category="housing",
                next_due_date=AS_OF + timedelta(days=20),
                cadence="monthly",
            )
        ],
        as_of=AS_OF,
        horizon_days=7,
        lookback_days=28,
        windows=3,
    )
    assert result.sufficient_history is True
    assert result.windows_evaluated == 3
    assert result.mean_abs_error is not None
