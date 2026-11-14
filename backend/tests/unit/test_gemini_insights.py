"""Unit tests for Gemini insight/copilot helpers (mocked network)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.models.schemas import CopilotRequest
from app.services.gemini_client import GeminiClient, GeminiClientError
from app.services.insights_service import answer_copilot, build_insights_response
from app.services.providers.mock import MockDataProvider


def test_explain_alerts_prompt_is_grounded() -> None:
    settings = Settings(gemini_api_key="test-key", gemini_model="gemini-3.5-flash-lite")
    fake = MagicMock()
    fake.models.generate_content.return_value = MagicMock(
        text="Watch: spending rose using only provided metrics."
    )
    client = GeminiClient(settings, client=fake)
    text = client.explain_alerts(
        "c1",
        alerts=[{"kind": "spending_drift", "evidence": {"drift_pct": "40.00"}}],
        metrics={"safe_to_spend": "100.00"},
    )
    assert "provided metrics" in text.lower() or text
    prompt = fake.models.generate_content.call_args.kwargs["contents"]
    assert "40.00" in prompt
    assert "100.00" in prompt
    assert "ONLY the numbers" in prompt or "do not invent" in prompt.lower()


def test_copilot_refuses_out_of_scope() -> None:
    provider = MockDataProvider()
    settings = Settings(use_mock_data=True, demo_customer_id="demo-customer-001")
    result = answer_copilot(
        customer_id="demo-customer-001",
        provider=provider,
        body=CopilotRequest(question="What is my credit score?"),
        settings=settings,
    )
    assert result.refused is True
    assert result.ai_generated is False
    assert "grounded" in result.answer.lower() or "do not have" in result.answer.lower()


def test_mock_insights_are_deterministic() -> None:
    provider = MockDataProvider()
    settings = Settings(use_mock_data=True, demo_customer_id="demo-customer-001")
    insights = build_insights_response(
        customer_id="demo-customer-001",
        provider=provider,
        settings=settings,
    )
    assert insights.mode == "mock"
    assert insights.ai_generated is False
    assert insights.model is None
    assert "safe_to_spend" in insights.metric_glossary
    assert isinstance(insights.alerts, list)
    assert insights.coaching
    assert insights.alert_explanation


def test_answer_copilot_includes_scenario_grounding() -> None:
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
    assert "scenario_min_balance" in result.grounded_metrics
    assert "baseline_safe_to_spend" in result.grounded_metrics
    assert result.scenario_note
    assert result.refused is False


def test_gemini_empty_response_raises() -> None:
    settings = Settings(gemini_api_key="test-key")
    fake = MagicMock()
    fake.models.generate_content.return_value = MagicMock(text="  ")
    client = GeminiClient(settings, client=fake)
    with pytest.raises(GeminiClientError):
        client.explain_dashboard("c1", "Posted net $1.00")
