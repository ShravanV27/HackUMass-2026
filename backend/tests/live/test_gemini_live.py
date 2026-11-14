"""Gemini live tests — included with ``pytest --run-live`` when keyed.

Skips cleanly when ``GEMINI_API_KEY`` is absent. Few calls; metrics-only prompts.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.gemini_client import GeminiClient, GeminiClientError
from app.services.providers.live import LiveDataProvider
from tests.live.helpers import require_gemini_settings

pytestmark = [pytest.mark.live, pytest.mark.live_gemini]


def test_gemini_connectivity_and_metrics_only_prompt() -> None:
    settings = require_gemini_settings()
    client = GeminiClient(settings)
    # Single bounded call — precomputed metrics only.
    metrics = (
        "Posted net $12.34; including pending $10.00; "
        "projected daily rate $-1.50."
    )
    text = client.explain_dashboard("live-test-customer-001", metrics)
    assert isinstance(text, str)
    assert text.strip()
    # Model must not invent a wildly different fabricated balance string that
    # clearly was not in the metrics (heuristic — soft check).
    lowered = text.lower()
    assert "wire transfer completed" not in lowered
    assert "i executed" not in lowered


def test_gemini_failure_handling_does_not_invent_balances() -> None:
    settings = require_gemini_settings()
    broken = MagicMock()
    broken.models.generate_content.side_effect = RuntimeError("upstream down")
    client = GeminiClient(settings, client=broken)
    with pytest.raises(GeminiClientError):
        client.explain_dashboard("live-test-customer-001", "Posted net $1.00")

    provider = LiveDataProvider(settings, gemini=client, sync_on_read=False)
    insight = provider.explain_dashboard(
        "live-test-customer-001", "Posted net $1.00"
    )
    assert "Posted net $1.00" in insight
    assert "Gemini unavailable" in insight
