"""Unit tests for Gemini insight/copilot helpers (mocked network)."""

from __future__ import annotations

from datetime import date
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


def test_gemini_rejects_amounts_missing_from_grounded_metrics() -> None:
    settings = Settings(gemini_api_key="test-key", gemini_model="gemini-3.5-flash-lite")
    fake = MagicMock()
    fake.models.generate_content.return_value = MagicMock(
        text="Your balance is $99999.99 and safe to spend is $100.00."
    )
    client = GeminiClient(settings, client=fake)
    with pytest.raises(GeminiClientError, match="grounded metrics"):
        client.explain_alerts(
            "c1",
            alerts=[],
            metrics={"safe_to_spend": "100.00"},
        )


def test_gemini_accepts_negative_amount_before_sentence_period() -> None:
    """$-1.50. is grounded; the period is punctuation, not a second decimal."""
    settings = Settings(gemini_api_key="test-key", gemini_model="gemini-3.5-flash-lite")
    fake = MagicMock()
    fake.models.generate_content.return_value = MagicMock(
        text="Your projected daily rate is $-1.50."
    )
    client = GeminiClient(settings, client=fake)
    text = client.explain_dashboard(
        "c1",
        "Posted net $12.34; including pending $10.00; projected daily rate $-1.50.",
    )
    assert text == "Your projected daily rate is $-1.50."


def test_gemini_rejects_sign_flip_and_sentence_final_invention() -> None:
    settings = Settings(gemini_api_key="test-key", gemini_model="gemini-3.5-flash-lite")
    fake = MagicMock()
    fake.models.generate_content.return_value = MagicMock(text="The daily rate is $1.50.")
    client = GeminiClient(settings, client=fake)
    with pytest.raises(GeminiClientError, match="grounded metrics"):
        client.explain_dashboard("c1", "projected daily rate $-1.50.")

    fake.models.generate_content.return_value = MagicMock(text="You are short 99999.99.")
    with pytest.raises(GeminiClientError, match="grounded metrics"):
        client.explain_dashboard("c1", "Posted net $12.34.")


def test_gemini_accepts_amounts_copied_from_evidence() -> None:
    settings = Settings(gemini_api_key="test-key", gemini_model="gemini-3.5-flash-lite")
    fake = MagicMock()
    fake.models.generate_content.return_value = MagicMock(
        text="Safe to spend is $100.00."
    )
    client = GeminiClient(settings, client=fake)
    text = client.explain_alerts(
        "c1",
        alerts=[],
        metrics={"safe_to_spend": "100.00"},
    )
    assert text == "Safe to spend is $100.00."


def test_live_insights_drop_hallucinated_gemini_amounts() -> None:
    class _LiveStub(MockDataProvider):
        @property
        def mode(self):  # type: ignore[override]
            return "live"

    fake = MagicMock()
    fake.models.generate_content.return_value = MagicMock(
        text="You secretly have $99999.99 available."
    )

    def _client(settings: Settings) -> GeminiClient:
        return GeminiClient(settings, client=fake)

    settings = Settings(
        use_mock_data=False,
        demo_customer_id="demo-customer-001",
        gemini_api_key="test-key",
        gemini_model="gemini-3.5-flash-lite",
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("app.services.insights_service.GeminiClient", _client)
        insights = build_insights_response(
            customer_id="demo-customer-001",
            provider=_LiveStub(),
            settings=settings,
            as_of=date(2026, 11, 14),
        )
    assert insights.ai_generated is False
    assert "99999" not in insights.alert_explanation
    assert "99999" not in insights.coaching
    assert "Gemini unavailable" in insights.alert_explanation


def test_gemini_empty_response_raises() -> None:
    settings = Settings(gemini_api_key="test-key")
    fake = MagicMock()
    fake.models.generate_content.return_value = MagicMock(text="  ")
    client = GeminiClient(settings, client=fake)
    with pytest.raises(GeminiClientError):
        client.explain_dashboard("c1", "Posted net $1.00")
