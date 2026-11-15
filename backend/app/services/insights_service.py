"""Phase 3 insights: deterministic alerts + optional Gemini narration/copilot."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from app.config import Settings, get_settings
from app.db.connection import get_connection
from app.db.repository import TigerRepository
from app.domain.alerts import evaluate_alerts, metric_glossary
from app.domain.predictive_engine import (
    build_engine_forecast,
    classify_behavior,
    compute_engine_safe_to_spend,
    compute_overdraft_risk,
    purchase_applies_in_horizon,
    simulate_scenarios,
    sql_bounds_for_half_open_days,
)
from app.domain.scenario_intent import ScenarioIntent, parse_scenario_intent
from app.models.schemas import (
    AlertItem,
    AlertsResponse,
    CopilotRequest,
    CopilotResponse,
    InsightsResponse,
    ScenarioRequest,
    ScenarioSeries,
    ScenarioSpec,
)
from app.services.customer_settings import get_payment_cycle
from app.services.gemini_client import GeminiClient, GeminiClientError
from app.services.providers.base import DataProvider

ZERO = Decimal("0.00")


def build_alerts_response(
    *,
    customer_id: str,
    provider: DataProvider,
    settings: Settings | None = None,
    as_of: date | None = None,
    horizon_days: int = 14,
    minimum_buffer: Decimal = Decimal("250.00"),
) -> AlertsResponse:
    cfg = settings or get_settings()
    day = as_of or date.today()
    accounts = provider.list_accounts(customer_id)
    transactions = provider.list_transactions(customer_id)
    bills = provider.list_scheduled_bills(customer_id)
    payment_cycle, _ = get_payment_cycle(customer_id, settings=cfg)
    buffer = minimum_buffer if minimum_buffer is not None else payment_cycle.minimum_buffer

    recent_cats, baseline_cats, source = _category_windows(
        cfg, provider, customer_id, as_of=day
    )
    alerts = evaluate_alerts(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=bills,
        as_of=day,
        horizon_days=horizon_days,
        minimum_buffer=buffer,
        category_totals_recent=recent_cats,
        category_totals_baseline=baseline_cats,
    )
    return AlertsResponse(
        customer_id=customer_id,
        as_of=day,
        mode=provider.mode,
        analytics_source=source,
        alerts=alerts,
    )


def build_insights_response(
    *,
    customer_id: str,
    provider: DataProvider,
    settings: Settings | None = None,
    as_of: date | None = None,
    horizon_days: int = 14,
    minimum_buffer: Decimal | None = None,
) -> InsightsResponse:
    cfg = settings or get_settings()
    day = as_of or date.today()
    accounts = provider.list_accounts(customer_id)
    transactions = provider.list_transactions(customer_id)
    bills = provider.list_scheduled_bills(customer_id)
    payment_cycle, _ = get_payment_cycle(customer_id, settings=cfg)
    buffer = (
        minimum_buffer
        if minimum_buffer is not None
        else payment_cycle.minimum_buffer
    )
    alerts_resp = build_alerts_response(
        customer_id=customer_id,
        provider=provider,
        settings=cfg,
        as_of=day,
        horizon_days=horizon_days,
        minimum_buffer=buffer,
    )

    safe = compute_engine_safe_to_spend(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=bills,
        as_of=day,
        horizon_days=horizon_days,
        payment_cycle=payment_cycle,
        minimum_buffer=buffer,
    )
    forecast = build_engine_forecast(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=bills,
        as_of=day,
        horizon_days=horizon_days,
        payment_cycle=payment_cycle,
        minimum_buffer=buffer,
    )
    overdraft = compute_overdraft_risk(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=bills,
        as_of=day,
        horizon_days=horizon_days,
        payment_cycle=payment_cycle,
        minimum_buffer=buffer,
    )
    behavior = classify_behavior(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=bills,
        as_of=day,
        payment_cycle=payment_cycle,
    )

    metrics = {
        "safe_to_spend": str(safe.safe_to_spend),
        "min_projected_balance": str(forecast.min_balance),
        "ending_balance": str(forecast.ending_balance),
        "overdraft_risk": overdraft.overdraft_risk,
        "suggested_daily_cutback": str(overdraft.suggested_daily_cutback),
        "scheduled_bills_total": str(forecast.scheduled_bills_total),
        "estimated_spend_total": str(forecast.estimated_spend_total),
        "behavior_segment": behavior.segment,
        "alert_count": str(len(alerts_resp.alerts)),
    }
    alert_dicts = [a.model_dump(mode="json") for a in alerts_resp.alerts]

    ai_generated = False
    model_name: str | None = None
    alert_explanation = _deterministic_alert_explanation(alerts_resp.alerts, metrics)
    coaching = _deterministic_coaching(behavior.segment, behavior.tips, metrics)

    if provider.mode == "live" and cfg.gemini_api_key:
        try:
            client = GeminiClient(cfg)
            model_name = client.model_name
            alert_explanation = client.explain_alerts(
                customer_id, alert_dicts, metrics
            )
            coaching = client.coach_from_behavior(
                customer_id,
                segment=behavior.segment,
                tips=behavior.tips,
                metrics=metrics,
            )
            ai_generated = True
        except GeminiClientError as exc:
            alert_explanation = (
                f"{alert_explanation} (Gemini unavailable: {_safe(exc, cfg)})"
            )
            coaching = f"{coaching} (Gemini unavailable: {_safe(exc, cfg)})"

    return InsightsResponse(
        customer_id=customer_id,
        as_of=day,
        mode=provider.mode,
        ai_generated=ai_generated,
        model=model_name if ai_generated else None,
        alerts=alerts_resp.alerts,
        alert_explanation=alert_explanation,
        behavior_segment=behavior.segment,
        coaching=coaching,
        metric_glossary=metric_glossary(),
    )


def answer_copilot(
    *,
    customer_id: str,
    provider: DataProvider,
    body: CopilotRequest,
    settings: Settings | None = None,
    as_of: date | None = None,
) -> CopilotResponse:
    cfg = settings or get_settings()
    day = as_of or date.today()
    accounts = provider.list_accounts(customer_id)
    transactions = provider.list_transactions(customer_id)
    bills = provider.list_scheduled_bills(customer_id)
    payment_cycle, _ = get_payment_cycle(customer_id, settings=cfg)
    buffer = body.minimum_buffer

    safe = compute_engine_safe_to_spend(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=bills,
        as_of=day,
        horizon_days=body.horizon_days,
        payment_cycle=payment_cycle,
        minimum_buffer=buffer,
    )
    forecast = build_engine_forecast(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=bills,
        as_of=day,
        horizon_days=body.horizon_days,
        payment_cycle=payment_cycle,
        minimum_buffer=buffer,
    )
    overdraft = compute_overdraft_risk(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=bills,
        as_of=day,
        horizon_days=body.horizon_days,
        payment_cycle=payment_cycle,
        minimum_buffer=buffer,
    )
    grounded: dict[str, Any] = {
        "safe_to_spend": str(safe.safe_to_spend),
        "min_projected_balance": str(forecast.min_balance),
        "ending_balance": str(forecast.ending_balance),
        "overdraft_risk": overdraft.overdraft_risk,
        "suggested_daily_cutback": str(overdraft.suggested_daily_cutback),
        "scheduled_bills_total": str(forecast.scheduled_bills_total),
        "estimated_spend_total": str(forecast.estimated_spend_total),
        "days_until_zero": (
            str(forecast.days_until_zero)
            if forecast.days_until_zero is not None
            else "null"
        ),
        "horizon_days": str(body.horizon_days),
        "baseline_safe_to_spend": str(safe.safe_to_spend),
        "baseline_min_balance": str(forecast.min_balance),
        "baseline_ending_balance": str(forecast.ending_balance),
        "baseline_overdraft_risk": overdraft.overdraft_risk,
    }

    intent = parse_scenario_intent(
        body.question,
        include_scenario=body.include_scenario,
        explicit_name=body.scenario_name,
        explicit_amount=body.scenario_amount,
        explicit_category=body.scenario_category,
        as_of=day,
    )
    scenario_note: str | None = None
    if intent.status == "clarify":
        grounded["needs_clarification"] = "true"
        grounded["clarification"] = intent.clarification or (
            "Please specify the scenario to evaluate."
        )
        scenario_note = grounded["clarification"]
    elif intent.ready:
        scenario_note = _apply_scenario_grounding(
            grounded=grounded,
            intent=intent,
            customer_id=customer_id,
            accounts=accounts,
            transactions=transactions,
            bills=bills,
            as_of=day,
            horizon_days=body.horizon_days,
            buffer=buffer,
            payment_cycle=payment_cycle,
        )

    alerts = evaluate_alerts(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=bills,
        as_of=day,
        horizon_days=body.horizon_days,
        minimum_buffer=buffer,
    )
    grounded["alert_count"] = str(len(alerts))
    grounded["top_alert"] = alerts[0].title if alerts else "none"

    # Refuse path when question asks for unavailable metrics (simple heuristic).
    refused = False
    if _asks_for_unavailable(body.question, grounded):
        answer = (
            "I only have the grounded metrics provided by the prediction engine "
            f"({', '.join(sorted(grounded.keys()))}). I do not have that figure."
        )
        refused = True
        return CopilotResponse(
            customer_id=customer_id,
            question=body.question,
            answer=answer,
            ai_generated=False,
            model=None,
            grounded_metrics={k: str(v) for k, v in grounded.items()},
            scenario_note=scenario_note,
            refused=True,
        )

    if intent.status == "clarify":
        answer = intent.clarification or (
            "Please specify the purchase, cutback, or income scenario to evaluate."
        )
        return CopilotResponse(
            customer_id=customer_id,
            question=body.question,
            answer=answer,
            ai_generated=False,
            model=None,
            grounded_metrics={k: str(v) for k, v in grounded.items()},
            scenario_note=scenario_note,
            refused=False,
        )

    answer = _deterministic_copilot_answer(body.question, grounded)
    ai_generated = False
    model_name: str | None = None

    if provider.mode == "live" and cfg.gemini_api_key:
        try:
            client = GeminiClient(cfg)
            model_name = client.model_name
            answer = client.answer_copilot(customer_id, body.question, grounded)
            ai_generated = True
        except GeminiClientError as exc:
            answer = f"{answer} (Gemini unavailable: {_safe(exc, cfg)})"

    return CopilotResponse(
        customer_id=customer_id,
        question=body.question,
        answer=answer,
        ai_generated=ai_generated,
        model=model_name if ai_generated else None,
        grounded_metrics={k: str(v) for k, v in grounded.items()},
        scenario_note=scenario_note,
        refused=refused,
    )


def _apply_scenario_grounding(
    *,
    grounded: dict[str, Any],
    intent: ScenarioIntent,
    customer_id: str,
    accounts: list[Any],
    transactions: list[Any],
    bills: list[Any],
    as_of: date,
    horizon_days: int,
    buffer: Decimal,
    payment_cycle: Any,
) -> str:
    assert intent.name is not None
    specs = [
        ScenarioSpec(name="baseline"),
        ScenarioSpec(
            name=intent.name,
            amount=intent.amount,
            category=intent.category,
            on_date=intent.on_date,
        ),
    ]
    sim = simulate_scenarios(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=bills,
        as_of=as_of,
        request=ScenarioRequest(
            horizon_days=horizon_days,
            minimum_buffer=buffer,
            scenarios=specs,
        ),
        payment_cycle=payment_cycle,
    )
    by_name = {s.name: s for s in sim.scenarios}
    baseline = by_name.get("baseline") or sim.scenarios[0]
    chosen = by_name.get(intent.name) or sim.scenarios[-1]
    _attach_scenario_metrics(
        grounded,
        baseline=baseline,
        chosen=chosen,
        intent=intent,
        as_of=as_of,
        horizon_days=horizon_days,
    )
    return chosen.note


def _attach_scenario_metrics(
    grounded: dict[str, Any],
    *,
    baseline: ScenarioSeries,
    chosen: ScenarioSeries,
    intent: ScenarioIntent,
    as_of: date,
    horizon_days: int,
) -> None:
    grounded["scenario_name"] = chosen.name
    grounded["scenario_label"] = chosen.label
    grounded["scenario_source"] = intent.source
    if intent.amount is not None:
        grounded["scenario_amount"] = str(intent.amount)
    if intent.category:
        grounded["scenario_category"] = intent.category
    if intent.on_date is not None:
        grounded["scenario_on_date"] = intent.on_date.isoformat()

    grounded["baseline_safe_to_spend"] = str(baseline.safe_to_spend)
    grounded["baseline_min_balance"] = str(baseline.min_balance)
    grounded["baseline_ending_balance"] = str(baseline.ending_balance)
    grounded["baseline_overdraft_risk"] = baseline.overdraft_risk

    grounded["scenario_safe_to_spend"] = str(chosen.safe_to_spend)
    grounded["scenario_min_balance"] = str(chosen.min_balance)
    grounded["scenario_ending_balance"] = str(chosen.ending_balance)
    grounded["scenario_overdraft_risk"] = chosen.overdraft_risk
    grounded["scenario_delta_min_vs_baseline"] = str(
        chosen.delta_min_balance_vs_baseline
    )
    grounded["scenario_delta_safe_to_spend"] = str(
        chosen.safe_to_spend - baseline.safe_to_spend
    )
    grounded["scenario_delta_ending_balance"] = str(
        chosen.ending_balance - baseline.ending_balance
    )

    if chosen.name == "one_time_purchase":
        applied = purchase_applies_in_horizon(
            intent.amount or ZERO,
            intent.on_date,
            as_of=as_of,
            horizon_days=horizon_days,
        )
        grounded["scenario_purchase_applied"] = "true" if applied else "false"
        if applied:
            affordable = chosen.safe_to_spend >= ZERO
            grounded["scenario_affordable"] = "true" if affordable else "false"
        else:
            grounded["scenario_affordable"] = "not_in_horizon"
        grounded["purchase_amount"] = grounded.get(
            "scenario_amount", str(intent.amount or ZERO)
        )


def _category_windows(
    settings: Settings,
    provider: DataProvider,
    customer_id: str,
    *,
    as_of: date,
    recent_days: int = 7,
    baseline_days: int = 28,
) -> tuple[
    dict[str, Decimal],
    dict[str, Decimal],
    Literal["memory", "sql", "continuous_aggregate"],
]:
    """Prefer TigerData category SQL in live mode; else in-memory totals."""
    if provider.mode == "live" and settings.database_url:
        try:
            # Warm sync once via provider.
            provider.list_transactions(customer_id)
            recent_start_day = as_of - timedelta(days=recent_days)
            baseline_end_day = recent_start_day
            baseline_start_day = baseline_end_day - timedelta(days=baseline_days)
            # Same half-open days as category_spend_totals: (start, end].
            recent_start, recent_end = sql_bounds_for_half_open_days(
                start_exclusive=recent_start_day,
                end_inclusive=as_of,
            )
            baseline_start, baseline_end = sql_bounds_for_half_open_days(
                start_exclusive=baseline_start_day,
                end_inclusive=baseline_end_day,
            )
            with get_connection(settings) as conn:
                repo = TigerRepository(conn)
                recent_rows = repo.spending_by_category(
                    customer_id, start_at=recent_start, end_at=recent_end
                )
                baseline_rows = repo.spending_by_category(
                    customer_id, start_at=baseline_start, end_at=baseline_end
                )
            recent = {
                row["category"]: Decimal(str(row["total_spent"])) for row in recent_rows
            }
            baseline = {
                row["category"]: Decimal(str(row["total_spent"]))
                for row in baseline_rows
            }
            return recent, baseline, "sql"
        except Exception:
            pass

    from app.domain.alerts import category_spend_totals

    txns = provider.list_transactions(customer_id)
    accounts = provider.list_accounts(customer_id)
    recent = category_spend_totals(
        txns,
        customer_id=customer_id,
        start=as_of - timedelta(days=recent_days),
        end=as_of,
        accounts=accounts,
    )
    baseline = category_spend_totals(
        txns,
        customer_id=customer_id,
        start=as_of - timedelta(days=recent_days + baseline_days),
        end=as_of - timedelta(days=recent_days),
        accounts=accounts,
    )
    return recent, baseline, "memory"


def _deterministic_alert_explanation(
    alerts: list[AlertItem], metrics: dict[str, str]
) -> str:
    if not alerts:
        return (
            f"No predictive alerts right now. Safe to spend is "
            f"${metrics.get('safe_to_spend', '0.00')} with overdraft risk "
            f"{metrics.get('overdraft_risk', 'none')}."
        )
    parts = [f"{a.severity.upper()}: {a.summary}" for a in alerts[:3]]
    return " ".join(parts)


def _deterministic_coaching(
    segment: str, tips: list[str], metrics: dict[str, str]
) -> str:
    tip = tips[0] if tips else "Keep your buffer funded."
    return (
        f"Behavior segment {segment}. {tip} "
        f"Current safe to spend ${metrics.get('safe_to_spend', '0.00')}."
    )


def _deterministic_copilot_answer(question: str, grounded: dict[str, Any]) -> str:
    q = question.lower()
    if grounded.get("needs_clarification") == "true":
        return str(grounded.get("clarification") or "Please clarify the scenario.")

    if "scenario_name" in grounded:
        name = grounded["scenario_name"]
        baseline_safe = grounded.get("baseline_safe_to_spend", grounded["safe_to_spend"])
        scenario_safe = grounded["scenario_safe_to_spend"]
        baseline_min = grounded.get(
            "baseline_min_balance", grounded["min_projected_balance"]
        )
        scenario_min = grounded["scenario_min_balance"]
        risk = grounded["scenario_overdraft_risk"]
        if name == "one_time_purchase":
            amount = grounded.get("purchase_amount") or grounded.get(
                "scenario_amount", "?"
            )
            if grounded.get("scenario_purchase_applied") == "false":
                when = grounded.get("scenario_on_date", "that date")
                return (
                    f"A ${amount} purchase on {when} is outside this forecast "
                    "horizon, so it was not applied and safe-to-spend stays "
                    f"${baseline_safe}."
                )
            affordable = grounded.get("scenario_affordable") == "true"
            verdict = "affordable" if affordable else "not affordable"
            return (
                f"A ${amount} purchase is {verdict}. Safe to spend moves from "
                f"${baseline_safe} to ${scenario_safe}; lowest projected balance "
                f"from ${baseline_min} to ${scenario_min}; overdraft risk becomes "
                f"{risk}."
            )
        return (
            f"Under {grounded.get('scenario_label', name)}, safe to spend moves from "
            f"${baseline_safe} to ${scenario_safe}; lowest balance from "
            f"${baseline_min} to ${scenario_min} "
            f"(delta {grounded['scenario_delta_min_vs_baseline']}); "
            f"overdraft risk becomes {risk}."
        )

    if "safe" in q and "afford" not in q:
        return (
            f"Safe to spend is ${grounded['safe_to_spend']} after bills, "
            f"estimated spend (${grounded['estimated_spend_total']}), and buffer."
        )
    if "overdraft" in q or "zero" in q or "runway" in q:
        return (
            f"Overdraft risk is {grounded['overdraft_risk']}; min projected balance "
            f"${grounded['min_projected_balance']}; suggested cutback "
            f"${grounded['suggested_daily_cutback']}/day."
        )
    return (
        f"Using grounded metrics: safe to spend ${grounded['safe_to_spend']}, "
        f"min runway ${grounded['min_projected_balance']}, "
        f"overdraft {grounded['overdraft_risk']}."
    )


def _asks_for_unavailable(question: str, grounded: dict[str, Any]) -> bool:
    q = question.lower()
    # Soft refuse for clearly out-of-scope asks.
    if any(token in q for token in ("stock", "crypto", "wire transfer", "ssn", "password")):
        return True
    if "credit score" in q:
        return True
    return False


def _safe(exc: BaseException, settings: Settings) -> str:
    text = str(exc).strip() or "unavailable"
    key = settings.gemini_api_key
    if key and key in text:
        text = text.replace(key, "***REDACTED***")
    return text[:160]
