"""Google Gemini client for natural-language explanations.

Deterministic Python calculations remain the source of truth. Gemini only
narrates precomputed metrics / alerts and must not invent balances.
"""

from __future__ import annotations

import json
from typing import Any

from google import genai
from google.genai import types

from app.config import Settings


class GeminiClientError(RuntimeError):
    pass


_GROUNDING_RULES = (
    "Use ONLY the numbers and facts in the provided JSON. "
    "Do not invent balances, income, bills, accounts, transactions, or percentages. "
    "Do not suggest or execute transfers. "
    "Do not give investment advice. "
    "If a number is missing from the JSON, say you do not have that metric."
)


class GeminiClient:
    def __init__(
        self,
        settings: Settings,
        *,
        client: genai.Client | None = None,
    ) -> None:
        if client is None and not settings.gemini_api_key:
            raise GeminiClientError("GEMINI_API_KEY is not configured")
        self._model = settings.gemini_model
        self._client = client or genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=15000),
        )

    @property
    def model_name(self) -> str:
        return self._model

    def explain_dashboard(self, customer_id: str, summary_text: str) -> str:
        prompt = (
            "You are a concise personal-finance assistant. "
            "Explain the following precomputed metrics in 2 short sentences. "
            f"{_GROUNDING_RULES} "
            f"Customer id: {customer_id}. "
            f"Metrics: {summary_text}"
        )
        return self._generate(prompt)

    def explain_alerts(
        self,
        customer_id: str,
        alerts: list[dict[str, Any]],
        metrics: dict[str, Any],
    ) -> str:
        payload = {"customer_id": customer_id, "alerts": alerts, "metrics": metrics}
        prompt = (
            "You are a personal-finance coach. In 3 short sentences, explain the "
            "deterministic alerts using only the JSON evidence. Prioritize urgent "
            "items, then watch, then info. "
            f"{_GROUNDING_RULES} "
            f"JSON: {json.dumps(payload, default=str)}"
        )
        return self._generate(prompt)

    def coach_from_behavior(
        self,
        customer_id: str,
        *,
        segment: str,
        tips: list[str],
        metrics: dict[str, Any],
    ) -> str:
        payload = {
            "customer_id": customer_id,
            "segment": segment,
            "deterministic_tips": tips,
            "metrics": metrics,
        }
        prompt = (
            "Personalize the deterministic tips into 2-3 friendly coaching sentences. "
            "Keep every dollar amount identical to the JSON. "
            f"{_GROUNDING_RULES} "
            f"JSON: {json.dumps(payload, default=str)}"
        )
        return self._generate(prompt)

    def answer_copilot(
        self,
        customer_id: str,
        question: str,
        grounded_metrics: dict[str, Any],
    ) -> str:
        payload = {
            "customer_id": customer_id,
            "question": question,
            "grounded_metrics": grounded_metrics,
        }
        prompt = (
            "You are a grounded finance copilot. Answer the user question using only "
            "grounded_metrics. If the question requires a number not present, refuse "
            "politely and list which metrics you do have. Keep the answer under 120 words. "
            "If needs_clarification is true, ask the clarification question — do not invent "
            "a scenario. If scenario_name is present, compare baseline_* vs scenario_* "
            "(safe-to-spend, min/ending balance, overdraft risk) and cite those deltas. "
            "Never claim a purchase is affordable unless scenario_affordable is exactly "
            "'true'. Never describe a 20% cut unless scenario_name is cut_discretionary_20. "
            f"{_GROUNDING_RULES} "
            f"JSON: {json.dumps(payload, default=str)}"
        )
        return self._generate(prompt)

    def _generate(self, prompt: str) -> str:
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
            )
        except Exception as exc:  # noqa: BLE001 — surface provider failures cleanly
            raise GeminiClientError(f"Gemini request failed: {exc}") from exc

        text = getattr(response, "text", None)
        if not text or not str(text).strip():
            raise GeminiClientError("Gemini returned an empty explanation")
        return str(text).strip()
