"""Unit tests for Gemini insight/copilot helpers (mocked network)."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any
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


def test_gemini_accepts_thousand_scale_amounts_and_rejects_prefixes() -> None:
    """$7,085.19 is the same grounded figure as 7085.19, not $708 plus a suffix."""
    settings = Settings(gemini_api_key="test-key", gemini_model="gemini-3.5-flash-lite")
    fake = MagicMock()
    fake.models.generate_content.return_value = MagicMock(
        text="Safe to spend is $7,085.19 after the $250.00 buffer."
    )
    client = GeminiClient(settings, client=fake)
    text = client.explain_alerts(
        "c1",
        alerts=[],
        metrics={"safe_to_spend": "7085.19", "minimum_buffer": "250.00"},
    )
    assert text == "Safe to spend is $7,085.19 after the $250.00 buffer."

    fake.models.generate_content.return_value = MagicMock(text="That is only $708.")
    with pytest.raises(GeminiClientError, match="grounded metrics"):
        client.explain_alerts(
            "c1",
            alerts=[],
            metrics={"safe_to_spend": "7085.19"},
        )


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


def test_why_safe_to_spend_includes_python_breakdown() -> None:
    provider = MockDataProvider()
    settings = Settings(use_mock_data=True, demo_customer_id="demo-customer-001")
    result = answer_copilot(
        customer_id="demo-customer-001",
        provider=provider,
        body=CopilotRequest(question="Why is my safe to spend this amount?"),
        settings=settings,
        as_of=date(2026, 11, 14),
    )
    metrics = result.grounded_metrics
    for key in (
        "available_balance",
        "pending_income",
        "pending_expenses",
        "scheduled_bills_total",
        "estimated_spend_total",
        "minimum_buffer",
        "safe_to_spend_before_floor",
        "safe_to_spend_formula",
        "projected_income_total",
    ):
        assert key in metrics
    assert metrics["income_included_in_safe_to_spend"] == "false"
    before = (
        Decimal(metrics["available_balance"])
        + Decimal(metrics["pending_income"])
        - Decimal(metrics["pending_expenses"])
        - Decimal(metrics["scheduled_bills_total"])
        - Decimal(metrics["estimated_spend_total"])
        - Decimal(metrics["minimum_buffer"])
    )
    assert before == Decimal(metrics["safe_to_spend_before_floor"])
    displayed = Decimal(metrics["safe_to_spend"])
    assert displayed == max(before, Decimal("0.00"))
    answer = result.answer.lower()
    assert "i do not have" not in answer
    assert "buffer" in answer
    assert "bill" in answer


def test_copilot_explanation_keeps_verified_amounts_and_drops_inventions() -> None:
    class _LiveStub(MockDataProvider):
        @property
        def mode(self):  # type: ignore[override]
            return "live"

    settings = Settings(
        use_mock_data=False,
        demo_customer_id="demo-customer-001",
        gemini_api_key="test-key",
        gemini_model="gemini-3.5-flash-lite",
    )
    captured: dict[str, Any] = {}

    def _client(cfg: Settings) -> GeminiClient:
        fake = MagicMock()

        def _generate(**kwargs: Any) -> MagicMock:
            captured["prompt"] = kwargs["contents"]
            payload = json.loads(kwargs["contents"].split("JSON:", 1)[1])
            metrics = payload["grounded_metrics"]
            assert "available_balance" in metrics
            assert "minimum_buffer" in metrics
            assert "Do not say the calculation steps are missing" in kwargs["contents"]
            return MagicMock(
                text=(
                    f"Safe to spend is ${metrics['safe_to_spend']} because spendable "
                    f"cash ${metrics['available_balance']} plus pending income "
                    f"${metrics['pending_income']}, minus pending expenses "
                    f"${metrics['pending_expenses']}, bills "
                    f"${metrics['scheduled_bills_total']}, typical spend "
                    f"${metrics['estimated_spend_total']}, and the "
                    f"${metrics['minimum_buffer']} buffer. "
                    "Projected paychecks are not included. "
                    f"The display floor is ${metrics['display_floor']}."
                )
            )

        fake.models.generate_content.side_effect = _generate
        return GeminiClient(cfg, client=fake)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("app.services.insights_service.GeminiClient", _client)
        explained = answer_copilot(
            customer_id="demo-customer-001",
            provider=_LiveStub(),
            body=CopilotRequest(question="Why is my safe to spend this amount?"),
            settings=settings,
            as_of=date(2026, 11, 14),
        )
    assert explained.ai_generated is True
    assert "i do not have" not in explained.answer.lower()
    assert "calculation steps are missing" in captured["prompt"]

    def _inventing_client(cfg: Settings) -> GeminiClient:
        fake = MagicMock()
        fake.models.generate_content.return_value = MagicMock(
            text="You actually have $99999.99 safe to spend."
        )
        return GeminiClient(cfg, client=fake)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("app.services.insights_service.GeminiClient", _inventing_client)
        rejected = answer_copilot(
            customer_id="demo-customer-001",
            provider=_LiveStub(),
            body=CopilotRequest(question="Why is my safe to spend this amount?"),
            settings=settings,
            as_of=date(2026, 11, 14),
        )
    assert rejected.ai_generated is False
    assert "99999" not in rejected.answer
    assert "buffer" in rejected.answer.lower()
    assert "Gemini unavailable" in rejected.answer


def test_gemini_empty_response_raises() -> None:
    settings = Settings(gemini_api_key="test-key")
    fake = MagicMock()
    fake.models.generate_content.return_value = MagicMock(text="  ")
    client = GeminiClient(settings, client=fake)
    with pytest.raises(GeminiClientError):
        client.explain_dashboard("c1", "Posted net $1.00")
