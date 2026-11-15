"""Parse Copilot questions into deterministic ScenarioSpec inputs.

Gemini never invents the scenario — Python extracts amounts / kinds from the
user question (and optional explicit API fields) before the engine runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal

ScenarioKind = Literal[
    "baseline",
    "cut_discretionary_20",
    "pause_category",
    "extra_paycheck",
    "one_time_purchase",
    "aggressive_save",
]

IntentStatus = Literal["ready", "clarify", "none"]

_MONEY_RE = re.compile(
    r"(?<!\w)(?:\$\s*)?(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)\s*"
    r"(?:dollars?)?",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(r"(\d{1,2})\s*%")
_CATEGORY_RE = re.compile(
    r"pause\s+(?:my\s+|the\s+)?([a-z][a-z0-9_ -]{1,32})(?:\s+category|\s+spending)?",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_IN_DAYS_RE = re.compile(r"\bin\s+(\d{1,2})\s+days?\b", re.IGNORECASE)


@dataclass(frozen=True)
class ScenarioIntent:
    status: IntentStatus
    name: ScenarioKind | None = None
    amount: Decimal | None = None
    category: str | None = None
    on_date: date | None = None
    clarification: str | None = None
    source: str = "none"

    @property
    def ready(self) -> bool:
        return self.status == "ready" and self.name is not None


def parse_scenario_intent(
    question: str,
    *,
    include_scenario: bool = False,
    explicit_name: ScenarioKind | None = None,
    explicit_amount: Decimal | None = None,
    explicit_category: str | None = None,
    as_of: date | None = None,
) -> ScenarioIntent:
    """Resolve which scenario (if any) the Copilot should evaluate.

    Question text wins over API defaults when it names a concrete action
    (purchase, cutback, income). Ambiguous ``include_scenario`` requests ask
    for clarification instead of running an unrelated preset.
    """
    q = " ".join(question.lower().split())
    amounts = _extract_amounts(question)
    percents = [int(m) for m in _PERCENT_RE.findall(q)]
    today = as_of or date.today()
    on_date = _extract_on_date(q, today=today)

    purchase_hit = _looks_like_purchase(q)
    income_hit = _looks_like_income(q)
    cut_hit = _looks_like_cut(q)
    pause_hit = "pause" in q
    what_if_hit = any(
        token in q for token in ("what if", "scenario", "simulate", "compare")
    )

    if purchase_hit:
        amount = _pick_amount(amounts, explicit_amount)
        if amount is None or amount <= 0:
            # Fall through to explicit_name handling when API provided amount.
            if not (
                explicit_name == "one_time_purchase"
                and explicit_amount
                and explicit_amount > 0
            ):
                return ScenarioIntent(
                    status="clarify",
                    clarification=(
                        "I can check affordability, but I need a purchase amount "
                        "(for example: 'Can I afford a $500 purchase on 2026-11-20?')."
                    ),
                    source="question_purchase",
                )
        else:
            return ScenarioIntent(
                status="ready",
                name="one_time_purchase",
                amount=amount,
                on_date=on_date,
                source="question_purchase",
            )

    if income_hit:
        amount = _pick_amount(amounts, explicit_amount)
        if amount is None or amount <= 0:
            if not (
                explicit_name == "extra_paycheck"
                and explicit_amount
                and explicit_amount > 0
            ):
                return ScenarioIntent(
                    status="clarify",
                    clarification=(
                        "I can model extra income, but I need an amount "
                        "(for example: 'What if I get a $800 bonus?')."
                    ),
                    source="question_income",
                )
        else:
            return ScenarioIntent(
                status="ready",
                name="extra_paycheck",
                amount=amount,
                source="question_income",
            )

    if pause_hit:
        category = explicit_category or _extract_category(q)
        if not category:
            return ScenarioIntent(
                status="clarify",
                clarification=(
                    "Which spending category should I pause "
                    "(for example: dining, groceries, travel)?"
                ),
                source="question_pause",
            )
        return ScenarioIntent(
            status="ready",
            name="pause_category",
            category=category,
            source="question_pause",
        )

    if cut_hit or (percents and any(token in q for token in ("cut", "reduce", "save"))):
        pct = percents[0] if percents else 20
        if pct in {20, 19, 21} or (not percents and cut_hit):
            return ScenarioIntent(
                status="ready",
                name="cut_discretionary_20",
                source="question_cut",
            )
        if pct in {40, 39, 41} or "aggressive" in q:
            return ScenarioIntent(
                status="ready",
                name="aggressive_save",
                source="question_cut",
            )
        return ScenarioIntent(
            status="clarify",
            clarification=(
                f"I can model a 20% discretionary cut or an aggressive 40% save, "
                f"but not a custom {pct}% cut yet. Which should I run?"
            ),
            source="question_cut",
        )

    # Explicit client scenario when the question does not contradict it.
    if explicit_name and explicit_name != "baseline":
        if explicit_name == "one_time_purchase":
            amount = _pick_amount(amounts, explicit_amount)
            if amount is None or amount <= 0:
                return ScenarioIntent(
                    status="clarify",
                    clarification="Provide a purchase amount to evaluate affordability.",
                    source="explicit",
                )
            return ScenarioIntent(
                status="ready",
                name="one_time_purchase",
                amount=amount,
                on_date=on_date,
                source="explicit",
            )
        if explicit_name == "extra_paycheck":
            amount = _pick_amount(amounts, explicit_amount)
            if amount is None or amount <= 0:
                return ScenarioIntent(
                    status="clarify",
                    clarification="Provide an income amount for the extra-paycheck scenario.",
                    source="explicit",
                )
            return ScenarioIntent(
                status="ready",
                name="extra_paycheck",
                amount=amount,
                source="explicit",
            )
        if explicit_name == "pause_category":
            category = explicit_category or _extract_category(q)
            if not category:
                return ScenarioIntent(
                    status="clarify",
                    clarification="Provide a category to pause.",
                    source="explicit",
                )
            return ScenarioIntent(
                status="ready",
                name="pause_category",
                category=category,
                source="explicit",
            )
        # Only honor cut presets when the question asks for a scenario / cut,
        # or include_scenario is on AND the question is scenario-shaped.
        if explicit_name in {"cut_discretionary_20", "aggressive_save"}:
            if cut_hit or what_if_hit or (
                include_scenario and _scenario_shaped(q)
            ):
                return ScenarioIntent(
                    status="ready",
                    name=explicit_name,
                    source="explicit",
                )

    if include_scenario or what_if_hit:
        return ScenarioIntent(
            status="clarify",
            clarification=(
                "Tell me which decision to simulate — for example a $500 purchase, "
                "cutting discretionary spending 20%, or receiving $800 extra income."
            ),
            source="ambiguous",
        )

    return ScenarioIntent(status="none", source="none")


def _scenario_shaped(question: str) -> bool:
    return any(
        token in question
        for token in (
            "what if",
            "scenario",
            "simulate",
            "compare",
            "cut",
            "reduce",
            "afford",
            "purchase",
            "buy",
            "income",
            "paycheck",
            "bonus",
            "pause",
            "save more",
        )
    )


def _looks_like_purchase(q: str) -> bool:
    if _looks_like_income(q):
        return False
    if any(
        token in q
        for token in ("spend less", "spending less", "cut spend", "reduce spend")
    ):
        return False
    return any(
        token in q
        for token in (
            "afford",
            "purchase",
            "buy ",
            "buying",
            "bought",
            "spend $",
            "one-time",
            "one time",
        )
    )


def _looks_like_income(q: str) -> bool:
    return any(
        token in q
        for token in (
            "extra income",
            "paycheck",
            "bonus",
            "deposit",
            "get paid",
            "receive $",
            "received $",
            "side hustle",
        )
    )


def _looks_like_cut(q: str) -> bool:
    return any(
        token in q
        for token in (
            "cut",
            "reduce",
            "trim",
            "save more",
            "spend less",
            "discretionary",
            "20%",
            "40%",
        )
    )


def _extract_amounts(text: str) -> list[Decimal]:
    found: list[Decimal] = []
    date_spans = [match.span() for match in _ISO_DATE_RE.finditer(text)]
    for match in _MONEY_RE.finditer(text):
        if any(start <= match.start() and match.end() <= end for start, end in date_spans):
            continue
        raw = match.group(1).replace(",", "")
        # Skip bare percents captured without $ when followed by %
        end = match.end()
        if end < len(text) and text[end : end + 1] == "%":
            continue
        # Skip year-like / tiny integers that are clearly not money when no $
        span = match.group(0)
        try:
            value = Decimal(raw)
        except InvalidOperation:
            continue
        if "$" not in span and value == int(value) and value < 10:
            continue
        if value <= 0:
            continue
        found.append(value.quantize(Decimal("0.01")))
    return found


def _pick_amount(
    amounts: list[Decimal], explicit: Decimal | None
) -> Decimal | None:
    if explicit is not None and explicit > 0:
        return Decimal(explicit).quantize(Decimal("0.01"))
    if amounts:
        # Prefer the largest mentioned dollar amount (purchase / income size).
        return max(amounts)
    return None


def _extract_on_date(q: str, *, today: date) -> date | None:
    iso = _ISO_DATE_RE.search(q)
    if iso:
        try:
            return date.fromisoformat(iso.group(1))
        except ValueError:
            pass
    in_days = _IN_DAYS_RE.search(q)
    if in_days:
        return today + timedelta(days=int(in_days.group(1)))
    if "tomorrow" in q:
        return today + timedelta(days=1)
    if "next week" in q:
        return today + timedelta(days=7)
    return None


def _extract_category(q: str) -> str | None:
    match = _CATEGORY_RE.search(q)
    if not match:
        # Common short forms: "pause dining"
        for known in (
            "dining",
            "groceries",
            "travel",
            "entertainment",
            "shopping",
            "transport",
            "food",
        ):
            if known in q:
                return known
        return None
    return match.group(1).strip().lower().replace(" ", "_")
