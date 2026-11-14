"""Deterministic predictive alerts (Phase 3).

All thresholds and comparisons are pure Python. Gemini may narrate these
alerts later but must never invent or alter the numeric evidence.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Literal

from app.domain.predictive_engine import (
    compute_overdraft_risk,
    roll_bill_occurrences,
)
from app.domain.predictions import compute_spending_drift
from app.models.schemas import (
    Account,
    AlertItem,
    ScheduledBill,
    Transaction,
    TransactionKind,
    TransactionStatus,
)

ZERO = Decimal("0.00")
CENT = Decimal("0.01")

DRIFT_PCT_THRESHOLD = Decimal("25.00")
CATEGORY_SPIKE_RATIO = Decimal("1.75")  # 75% above baseline daily rate
CATEGORY_SPIKE_MIN_RECENT = Decimal("40.00")
BILL_CLUSTER_MIN_COUNT = 2
BILL_CLUSTER_WINDOW_DAYS = 7


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _as_utc_date(value: datetime | date) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.date()
        return value.astimezone(timezone.utc).date()
    return value


def evaluate_alerts(
    *,
    customer_id: str,
    accounts: list[Account],
    transactions: list[Transaction],
    scheduled_bills: list[ScheduledBill],
    as_of: date,
    horizon_days: int = 14,
    lookback_days: int = 30,
    minimum_buffer: Decimal = Decimal("250.00"),
    category_totals_recent: dict[str, Decimal] | None = None,
    category_totals_baseline: dict[str, Decimal] | None = None,
    recent_days: int = 7,
    baseline_days: int = 28,
) -> list[AlertItem]:
    """Return fired alerts sorted by severity then kind."""
    alerts: list[AlertItem] = []

    overdraft = compute_overdraft_risk(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=scheduled_bills,
        as_of=as_of,
        horizon_days=horizon_days,
        lookback_days=lookback_days,
        minimum_buffer=minimum_buffer,
    )
    if overdraft.overdraft_risk != "none":
        severity: Literal["info", "watch", "urgent"] = (
            "urgent" if overdraft.overdraft_risk == "likely" else "watch"
        )
        evidence = {
            "overdraft_risk": overdraft.overdraft_risk,
            "min_projected_balance": str(overdraft.min_projected_balance),
            "minimum_buffer": str(overdraft.minimum_buffer),
            "suggested_daily_cutback": str(overdraft.suggested_daily_cutback),
        }
        if overdraft.days_until_zero is not None:
            evidence["days_until_zero"] = str(overdraft.days_until_zero)
        alerts.append(
            AlertItem(
                alert_id=f"overdraft-{overdraft.overdraft_risk}",
                kind="overdraft_risk",
                severity=severity,
                title=(
                    "Projected overdraft"
                    if overdraft.overdraft_risk == "likely"
                    else "Buffer at risk"
                ),
                summary=overdraft.note,
                evidence=evidence,
                related_metric="overdraft_risk",
            )
        )

    drift = compute_spending_drift(
        customer_id=customer_id,
        transactions=transactions,
        as_of=as_of,
        recent_days=recent_days,
        baseline_days=baseline_days,
    )
    if drift.direction == "up" and abs(drift.drift_pct) >= DRIFT_PCT_THRESHOLD:
        alerts.append(
            AlertItem(
                alert_id="drift-up",
                kind="spending_drift",
                severity="watch",
                title="Spending drift up",
                summary=(
                    f"Recent {drift.recent_days}-day spend "
                    f"${drift.recent_spend} is {drift.drift_pct}% above the "
                    f"comparable {drift.baseline_days}-day baseline "
                    f"(${drift.baseline_spend})."
                ),
                evidence={
                    "direction": drift.direction,
                    "drift_pct": str(drift.drift_pct),
                    "drift_amount": str(drift.drift_amount),
                    "recent_spend": str(drift.recent_spend),
                    "baseline_spend": str(drift.baseline_spend),
                },
                related_metric="spending_drift",
            )
        )

    recent_cats = category_totals_recent or category_spend_totals(
        transactions,
        customer_id=customer_id,
        start=as_of - timedelta(days=recent_days),
        end=as_of,
    )
    baseline_cats = category_totals_baseline or category_spend_totals(
        transactions,
        customer_id=customer_id,
        start=as_of - timedelta(days=recent_days + baseline_days),
        end=as_of - timedelta(days=recent_days),
    )
    for category, recent_total in sorted(
        recent_cats.items(), key=lambda item: item[1], reverse=True
    ):
        if recent_total < CATEGORY_SPIKE_MIN_RECENT:
            continue
        baseline_total = baseline_cats.get(category, ZERO)
        # Normalize baseline to recent_days length.
        baseline_comparable = (
            _money((baseline_total / Decimal(baseline_days)) * Decimal(recent_days))
            if baseline_days > 0
            else ZERO
        )
        if baseline_comparable <= ZERO:
            continue
        ratio = _money(recent_total / baseline_comparable)
        if ratio >= CATEGORY_SPIKE_RATIO:
            alerts.append(
                AlertItem(
                    alert_id=f"spike-{category.lower().replace(' ', '-')}",
                    kind="category_spike",
                    severity="watch",
                    title=f"{category} spending spike",
                    summary=(
                        f"{category} spend ${recent_total} over {recent_days} days "
                        f"is {ratio}× the comparable baseline (${baseline_comparable})."
                    ),
                    evidence={
                        "category": category,
                        "recent_total": str(recent_total),
                        "baseline_comparable": str(baseline_comparable),
                        "ratio": str(ratio),
                        "recent_days": str(recent_days),
                        "baseline_days": str(baseline_days),
                    },
                    related_metric="category_spend",
                )
            )

    cluster = _bill_cluster_alert(
        customer_id=customer_id,
        scheduled_bills=scheduled_bills,
        as_of=as_of,
    )
    if cluster is not None:
        alerts.append(cluster)

    order = {"urgent": 0, "watch": 1, "info": 2}
    alerts.sort(key=lambda a: (order[a.severity], a.kind, a.alert_id))
    return alerts


def metric_glossary() -> dict[str, str]:
    return {
        "safe_to_spend": (
            "Cash available after pending items, known bills in the horizon, "
            "estimated discretionary spend, and your minimum buffer. "
            "Credit-card balances are excluded. Future income is only included "
            "when a payday is configured."
        ),
        "overdraft_risk": (
            "none = projected min balance stays at/above buffer; "
            "watch = dips below buffer; likely = crosses below zero. "
            "Suggested daily cutback is the discretionary reduction that restores the buffer."
        ),
        "spending_drift": (
            "Recent posted expense total vs a length-normalized baseline window. "
            "Transfers and pending items are excluded."
        ),
        "estimated_spend": (
            "Weekday-aware average of historical discretionary expenses, "
            "excluding categories covered by known scheduled bills."
        ),
        "bill_cluster": (
            "Multiple known bills due within a short window — increases short-term "
            "cash pressure even if each bill alone is affordable."
        ),
    }


def category_spend_totals(
    transactions: Iterable[Transaction],
    *,
    customer_id: str,
    start: date,
    end: date,
) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for txn in transactions:
        if txn.customer_id != customer_id:
            continue
        if txn.kind != TransactionKind.EXPENSE:
            continue
        if txn.status != TransactionStatus.POSTED:
            continue
        day = _as_utc_date(txn.posted_at)
        if start < day <= end:
            totals[txn.category] += abs(txn.amount)
    return {k: _money(v) for k, v in totals.items()}


def _bill_cluster_alert(
    *,
    customer_id: str,
    scheduled_bills: list[ScheduledBill],
    as_of: date,
) -> AlertItem | None:
    occurrences = roll_bill_occurrences(
        scheduled_bills,
        customer_id=customer_id,
        as_of=as_of,
        horizon_days=BILL_CLUSTER_WINDOW_DAYS,
    )
    if len(occurrences) < BILL_CLUSTER_MIN_COUNT:
        return None
    total = _money(sum((b.amount for b in occurrences), ZERO))
    names = ", ".join(b.name for b in occurrences[:4])
    if len(occurrences) > 4:
        names += ", …"
    return AlertItem(
        alert_id="bill-cluster",
        kind="bill_cluster",
        severity="info",
        title="Bills clustering soon",
        summary=(
            f"{len(occurrences)} bills totaling ${total} are due within "
            f"{BILL_CLUSTER_WINDOW_DAYS} days ({names})."
        ),
        evidence={
            "bill_count": str(len(occurrences)),
            "bills_total": str(total),
            "window_days": str(BILL_CLUSTER_WINDOW_DAYS),
            "names": names,
        },
        related_metric="bill_cluster",
    )
