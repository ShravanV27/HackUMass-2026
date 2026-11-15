"""Google Gemini client for natural-language explanations.

Deterministic Python calculations remain the source of truth. Gemini only
narrates precomputed metrics / alerts and must not invent balances.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from google import genai
from google.genai import types

from app.config import Settings

_CENT = Decimal("0.01")
# Dollar amounts, or bare money written with cents. Bare integers are ignored
# in the model reply so "14 days" is not treated as $14.
_CITED_AMOUNT_RE = re.compile(
    r"\$\s*-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|\$\s*-?\d+(?:\.\d+)?"
    r"|(?<![\w.])-?\d+\.\d{2}(?![\w.])"
)
_EVIDENCE_NUMBER_RE = re.compile(
    r"(?<![\w.])-?\d{1,3}(?:,\d{3})*(?:\.\d+)?(?![\w.])"
    r"|(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])"
)


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
            "'true'. If scenario_purchase_applied is 'false', say the purchase is outside "
            "this horizon and was not applied; do not call it affordable or unaffordable. "
            "Never describe a 20% cut unless scenario_name is cut_discretionary_20. "
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
        cleaned = str(text).strip()
        _reject_ungrounded_amounts(cleaned, prompt)
        return cleaned


def _evidence_section(prompt: str) -> str:
    """Numbers the model is allowed to cite live after the metrics payload."""
    for marker in ("JSON:", "Metrics:"):
        index = prompt.rfind(marker)
        if index != -1:
            return prompt[index:]
    return prompt


def _quantize_token(raw: str) -> Decimal | None:
    token = raw.replace("$", "").replace(",", "").strip()
    if not token:
        return None
    try:
        return Decimal(token).quantize(_CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def _reject_ungrounded_amounts(text: str, prompt: str) -> None:
    """Refuse narration that cites a dollar amount absent from the evidence.

    Callers already fall back to deterministic copy when this raises.
    The error text deliberately omits the invented figure.
    """
    evidence = _evidence_section(prompt)
    allowed: set[Decimal] = set()
    for match in _EVIDENCE_NUMBER_RE.finditer(evidence):
        amount = _quantize_token(match.group(0))
        if amount is not None:
            allowed.add(amount)
    for match in _CITED_AMOUNT_RE.finditer(text):
        amount = _quantize_token(match.group(0))
        if amount is None:
            continue
        if amount not in allowed:
            raise GeminiClientError(
                "Gemini cited an amount that is not in the grounded metrics"
            )
