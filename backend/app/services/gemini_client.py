"""Google Gemini client for natural-language explanations.

Deterministic Python calculations remain the source of truth. Gemini only
narrates precomputed summary text and must not invent balances.
"""

from __future__ import annotations

from google import genai
from google.genai import types

from app.config import Settings


class GeminiClientError(RuntimeError):
    pass


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

    def explain_dashboard(self, customer_id: str, summary_text: str) -> str:
        prompt = (
            "You are a concise personal-finance assistant. "
            "Explain the following precomputed metrics in 2 short sentences. "
            "Use only the numbers provided — do not invent balances, income, "
            "bills, accounts, or transactions. "
            "Do not suggest or execute transfers or payments. "
            "Do not give investment advice. "
            f"Customer id: {customer_id}. "
            f"Metrics: {summary_text}"
        )
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
