"""Regression tests for Copilot scenario intent parsing + grounding."""

from __future__ import annotations

from decimal import Decimal

from app.config import Settings
from app.domain.scenario_intent import parse_scenario_intent
from app.models.schemas import CopilotRequest
from app.services.insights_service import answer_copilot
from app.services.providers.mock import MockDataProvider


def test_parse_purchase_amount_from_question() -> None:
    intent = parse_scenario_intent(
        "Can I afford a $500 purchase?",
        include_scenario=True,
        explicit_name="cut_discretionary_20",  # must NOT win
    )
    assert intent.ready
    assert intent.name == "one_time_purchase"
    assert intent.amount == Decimal("500.00")


def test_parse_spending_reduction() -> None:
    intent = parse_scenario_intent("What if I cut discretionary spending 20%?")
    assert intent.ready
    assert intent.name == "cut_discretionary_20"


def test_parse_aggressive_save() -> None:
    intent = parse_scenario_intent("What if I aggressively save and cut 40%?")
    assert intent.ready
    assert intent.name == "aggressive_save"


def test_parse_extra_income() -> None:
    intent = parse_scenario_intent("What if I get an $800 bonus paycheck?")
    assert intent.ready
    assert intent.name == "extra_paycheck"
    assert intent.amount == Decimal("800.00")


def test_ambiguous_include_scenario_asks_clarification() -> None:
    intent = parse_scenario_intent(
        "Why is my safe to spend this amount?",
        include_scenario=True,
        explicit_name="cut_discretionary_20",
    )
    assert intent.status == "clarify"
    assert intent.clarification


def test_copilot_purchase_runs_one_time_purchase_not_cut() -> None:
    provider = MockDataProvider()
    settings = Settings(use_mock_data=True, demo_customer_id="demo-customer-001")
    result = answer_copilot(
        customer_id="demo-customer-001",
        provider=provider,
        body=CopilotRequest(
            question="Can I afford a $500 purchase?",
            include_scenario=True,
            # Legacy client default that previously hijacked purchase asks.
            scenario_name="cut_discretionary_20",
            minimum_buffer=Decimal("100.00"),
        ),
        settings=settings,
    )
    metrics = result.grounded_metrics
    assert metrics["scenario_name"] == "one_time_purchase"
    assert metrics["scenario_amount"] == "500.00"
    assert metrics["purchase_amount"] == "500.00"
    assert metrics["scenario_affordable"] in {"true", "false"}
    assert "scenario_safe_to_spend" in metrics
    assert "baseline_safe_to_spend" in metrics
    assert "scenario_overdraft_risk" in metrics
    assert "scenario_delta_min_vs_baseline" in metrics
    assert "cut_discretionary" not in result.answer.lower()
    assert "500" in result.answer
    assert "afford" in result.answer.lower()


def test_copilot_never_claims_affordable_without_purchase_scenario() -> None:
    provider = MockDataProvider()
    settings = Settings(use_mock_data=True, demo_customer_id="demo-customer-001")
    result = answer_copilot(
        customer_id="demo-customer-001",
        provider=provider,
        body=CopilotRequest(
            question="Can I afford a purchase?",
            include_scenario=True,
        ),
        settings=settings,
    )
    assert "scenario_name" not in result.grounded_metrics
    assert result.grounded_metrics.get("needs_clarification") == "true"
    assert "amount" in result.answer.lower()
    assert "affordable" not in result.answer.lower() or "need" in result.answer.lower()


def test_copilot_spending_reduction_scenario() -> None:
    provider = MockDataProvider()
    settings = Settings(use_mock_data=True, demo_customer_id="demo-customer-001")
    result = answer_copilot(
        customer_id="demo-customer-001",
        provider=provider,
        body=CopilotRequest(
            question="What if I cut discretionary spending 20%?",
            include_scenario=True,
            minimum_buffer=Decimal("100.00"),
        ),
        settings=settings,
    )
    assert result.grounded_metrics["scenario_name"] == "cut_discretionary_20"
    assert Decimal(result.grounded_metrics["scenario_min_balance"]) >= Decimal(
        result.grounded_metrics["baseline_min_balance"]
    )
    assert "scenario_delta_safe_to_spend" in result.grounded_metrics


def test_copilot_income_scenario() -> None:
    provider = MockDataProvider()
    settings = Settings(use_mock_data=True, demo_customer_id="demo-customer-001")
    result = answer_copilot(
        customer_id="demo-customer-001",
        provider=provider,
        body=CopilotRequest(
            question="What if I receive a $1,200 paycheck bonus?",
            include_scenario=True,
            minimum_buffer=Decimal("100.00"),
        ),
        settings=settings,
    )
    assert result.grounded_metrics["scenario_name"] == "extra_paycheck"
    assert result.grounded_metrics["scenario_amount"] == "1200.00"
    assert Decimal(result.grounded_metrics["scenario_min_balance"]) >= Decimal(
        result.grounded_metrics["baseline_min_balance"]
    )


def test_copilot_explicit_purchase_amount_hint() -> None:
    provider = MockDataProvider()
    settings = Settings(use_mock_data=True, demo_customer_id="demo-customer-001")
    result = answer_copilot(
        customer_id="demo-customer-001",
        provider=provider,
        body=CopilotRequest(
            question="Simulate this one-time purchase for me",
            include_scenario=True,
            scenario_name="one_time_purchase",
            scenario_amount=Decimal("75.00"),
        ),
        settings=settings,
    )
    assert result.grounded_metrics["scenario_name"] == "one_time_purchase"
    assert result.grounded_metrics["scenario_amount"] == "75.00"
