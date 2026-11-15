"""PredictiveEngine — deterministic cash-runway forecasting (Phase 1).

Rules:
- Python math is the source of truth; Gemini must never feed numbers here.
- Never invent bills. Recurring bills are rolled from known cadence only.
- Future income is included only when PaymentCycleConfig explicitly sets a
  paycheck amount and next payday (income_assumed=True).
- Discretionary spend uses weekday-aware rates from history (Tiger series or
  in-memory transactions). Flat average is retained as a summary field.
- Credit-card balances are excluded from spendable cash.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Literal

from app.domain.calculations import credit_account_ids, spendable_cash_balance
from app.models.schemas import (
    Account,
    BehaviorEvidence,
    BehaviorSegmentResponse,
    CashflowDayPoint,
    CashflowEvent,
    CashflowForecastResponse,
    OverdraftRiskResponse,
    PaymentCycleConfig,
    SafeToSpendResponse,
    ScenarioRequest,
    ScenarioSeries,
    ScenarioSimulationResponse,
    ScenarioSpec,
    ScheduledBill,
    Transaction,
    TransactionKind,
    TransactionStatus,
    WhatIfResponse,
)

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
# Same instant as nessie_mapper.UNDATED_POSTED_AT. Undated rows are not history.
_UNDATED_POSTED_AT = datetime(1970, 1, 1, tzinfo=timezone.utc)

WEEKDAY_NAMES = (
    "mon",
    "tue",
    "wed",
    "thu",
    "fri",
    "sat",
    "sun",
)

HorizonName = Literal["week", "month", "custom"]


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _as_utc_date(value: datetime | date) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.date()
        return value.astimezone(timezone.utc).date()
    return value


def _is_undated_posted_at(posted_at: datetime) -> bool:
    moment = posted_at if posted_at.tzinfo else posted_at.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc) == _UNDATED_POSTED_AT


def _card_purchases_in_lookback(
    transactions: list[Transaction],
    *,
    customer_id: str,
    card_ids: set[str],
    as_of: date,
    lookback_days: int,
) -> bool:
    if not card_ids:
        return False
    window_start = as_of - timedelta(days=lookback_days)
    for txn in transactions:
        if txn.customer_id != customer_id or txn.account_id not in card_ids:
            continue
        if txn.kind != TransactionKind.EXPENSE or txn.status != TransactionStatus.POSTED:
            continue
        if _is_undated_posted_at(txn.posted_at):
            continue
        day = _as_utc_date(txn.posted_at)
        if window_start < day <= as_of and abs(txn.amount) > ZERO:
            return True
    return False


def resolve_horizon_days(
    *,
    horizon: str | None = None,
    horizon_days: int | None = None,
) -> tuple[int, HorizonName]:
    """Map week/month aliases to day counts; custom uses horizon_days."""
    key = (horizon or "").strip().lower()
    if key in {"week", "7"}:
        return 7, "week"
    if key in {"month", "30"}:
        return 30, "month"
    days = horizon_days if horizon_days is not None else 14
    if days < 1:
        raise ValueError("horizon_days must be >= 1")
    if days == 7:
        return 7, "week"
    if days == 30:
        return 30, "month"
    return days, "custom"


@dataclass(frozen=True)
class DailySpendObservation:
    day: date
    total_spent: Decimal


def _count_weekdays(start: date, end: date) -> dict[int, int]:
    """Count occurrences of each weekday with start < day <= end."""
    counts = {i: 0 for i in range(7)}
    day = start + timedelta(days=1)
    while day <= end:
        counts[day.weekday()] += 1
        day += timedelta(days=1)
    return counts


def sql_bounds_for_half_open_days(
    *,
    start_exclusive: date,
    end_inclusive: date,
) -> tuple[datetime, datetime]:
    """UTC ``[start, end)`` matching in-memory ``start_exclusive < day <= end_inclusive``.

    SQL predicates are ``posted_at >= start AND posted_at < end``. Using midnight
    of ``start_exclusive`` would include that calendar day and shift category
    windows and discretionary history by one day versus the Python engine.
    """
    if end_inclusive < start_exclusive:
        raise ValueError("end_inclusive must be on or after start_exclusive")
    start_at = datetime.combine(
        start_exclusive + timedelta(days=1),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    end_at = datetime.combine(
        end_inclusive + timedelta(days=1),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    return start_at, end_at


def discretionary_daily_observations(
    transactions: list[Transaction],
    *,
    customer_id: str,
    as_of: date,
    lookback_days: int,
    exclude_categories: set[str],
    exclude_description_tokens: set[str] | None = None,
    exclude_account_ids: set[str] | None = None,
) -> list[DailySpendObservation]:
    """Posted discretionary cash outflow per day in the lookback window.

    ``exclude_account_ids`` drops credit-card purchases. Those swipes are not
    cash leaving the bank; the later card payment on a cash account is.
    """
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    window_start = as_of - timedelta(days=lookback_days)
    tokens = {t.lower() for t in (exclude_description_tokens or set()) if t}
    skipped_accounts = exclude_account_ids or set()
    by_day: dict[date, Decimal] = {}
    for txn in transactions:
        if txn.customer_id != customer_id:
            continue
        if txn.kind != TransactionKind.EXPENSE:
            continue
        if txn.status != TransactionStatus.POSTED:
            continue
        if txn.account_id in skipped_accounts:
            continue
        if txn.category in exclude_categories:
            continue
        if tokens and any(token in txn.description.lower() for token in tokens):
            continue
        day = _as_utc_date(txn.posted_at)
        if window_start < day <= as_of:
            by_day[day] = by_day.get(day, ZERO) + abs(txn.amount)
    return [
        DailySpendObservation(day=day, total_spent=_money(total))
        for day, total in sorted(by_day.items())
    ]


def weekday_spend_rates(
    observations: Iterable[DailySpendObservation],
    *,
    as_of: date,
    lookback_days: int,
) -> tuple[dict[int, Decimal], Decimal]:
    """Return per-weekday daily rates and the flat average daily spend.

    Weekday rate = sum(spend on that weekday) / count(that weekday in window),
    including zero-spend days. Missing weekdays fall back to the flat average.
    """
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    window_start = as_of - timedelta(days=lookback_days)
    counts = _count_weekdays(window_start, as_of)
    sums = {i: ZERO for i in range(7)}
    total = ZERO
    for obs in observations:
        if window_start < obs.day <= as_of:
            sums[obs.day.weekday()] += obs.total_spent
            total += obs.total_spent
    flat = _money(total / Decimal(lookback_days))
    rates: dict[int, Decimal] = {}
    for weekday in range(7):
        n = counts[weekday]
        if n > 0:
            # Include zero-spend days so quiet weekdays stay near zero.
            rates[weekday] = _money(sums[weekday] / Decimal(n))
        else:
            rates[weekday] = flat
    return rates, flat


def _advance_by_cadence(current: date, cadence: str) -> date:
    key = cadence.strip().lower()
    if key in {"weekly", "week"}:
        return current + timedelta(days=7)
    if key in {"biweekly", "bi-weekly", "fortnightly"}:
        return current + timedelta(days=14)
    # Default monthly (also covers semimonthly bill stubs by day-of-month).
    year, month = current.year, current.month
    if month == 12:
        year += 1
        month = 1
    else:
        month += 1
    day = min(current.day, monthrange(year, month)[1])
    return date(year, month, day)


def _apply_bill_day_override(bill: ScheduledBill, day_of_month: int, as_of: date) -> date:
    """Next occurrence of day_of_month strictly after as_of."""
    day = max(1, min(31, day_of_month))
    year, month = as_of.year, as_of.month
    for _ in range(0, 14):
        candidate_day = min(day, monthrange(year, month)[1])
        candidate = date(year, month, candidate_day)
        if candidate > as_of:
            return candidate
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return bill.next_due_date


def roll_bill_occurrences(
    bills: list[ScheduledBill],
    *,
    customer_id: str,
    as_of: date,
    horizon_days: int,
    bill_day_overrides: dict[str, int] | None = None,
) -> list[ScheduledBill]:
    """Expand known bills into every occurrence inside the horizon."""
    end = as_of + timedelta(days=horizon_days)
    overrides = bill_day_overrides or {}
    occurrences: list[ScheduledBill] = []
    for bill in bills:
        if bill.customer_id != customer_id:
            continue
        cadence = (bill.cadence or "monthly").strip().lower()
        if bill.bill_id in overrides:
            first = _apply_bill_day_override(bill, overrides[bill.bill_id], as_of)
        else:
            first = bill.next_due_date

        if cadence in {"one_time", "once", "single"}:
            if as_of < first <= end:
                occurrences.append(
                    ScheduledBill(
                        bill_id=f"{bill.bill_id}@{first.isoformat()}",
                        customer_id=bill.customer_id,
                        account_id=bill.account_id,
                        name=bill.name,
                        amount=bill.amount,
                        category=bill.category,
                        next_due_date=first,
                        cadence=bill.cadence,
                    )
                )
            continue

        current = first
        # If stored next_due is stale relative to as_of, advance. Weekly bills
        # can be years behind, so the cap is longer than a few dozen steps.
        # Never emit a date on or before as_of — those are not future obligations.
        guard = 0
        while current <= as_of and guard < 520:
            current = _advance_by_cadence(current, cadence)
            guard += 1
        while as_of < current <= end and guard < 1040:
            occurrences.append(
                ScheduledBill(
                    bill_id=f"{bill.bill_id}@{current.isoformat()}",
                    customer_id=bill.customer_id,
                    account_id=bill.account_id,
                    name=bill.name,
                    amount=bill.amount,
                    category=bill.category,
                    next_due_date=current,
                    cadence=bill.cadence,
                )
            )
            current = _advance_by_cadence(current, cadence)
            guard += 1
    occurrences.sort(key=lambda b: (b.next_due_date, b.name, b.bill_id))
    return occurrences


def payday_dates(
    config: PaymentCycleConfig | None,
    *,
    as_of: date,
    horizon_days: int,
) -> list[tuple[date, Decimal]]:
    """Explicit paycheck dates from config — empty when income is not configured."""
    if config is None:
        return []
    if config.cadence == "none" or config.paycheck_amount <= ZERO:
        return []
    if config.next_payday is None:
        return []
    end = as_of + timedelta(days=horizon_days)
    amount = _money(config.paycheck_amount)
    current = config.next_payday
    guard = 0
    while current <= as_of and guard < 36:
        if config.cadence == "biweekly":
            current = current + timedelta(days=14)
        elif config.cadence == "semimonthly":
            # 1st and 15th style: jump to next semi-monthly slot.
            if current.day < 15:
                current = date(current.year, current.month, 15)
            else:
                year, month = current.year, current.month
                if month == 12:
                    year += 1
                    month = 1
                else:
                    month += 1
                current = date(year, month, 1)
        else:  # monthly
            current = _advance_by_cadence(current, "monthly")
        guard += 1

    out: list[tuple[date, Decimal]] = []
    while current <= end and guard < 72:
        out.append((current, amount))
        if config.cadence == "biweekly":
            current = current + timedelta(days=14)
        elif config.cadence == "semimonthly":
            if current.day < 15:
                current = date(current.year, current.month, 15)
            else:
                year, month = current.year, current.month
                if month == 12:
                    year += 1
                    month = 1
                else:
                    month += 1
                current = date(year, month, 1)
        else:
            current = _advance_by_cadence(current, "monthly")
        guard += 1
    return out


def classify_overdraft_risk(
    *,
    min_balance: Decimal,
    minimum_buffer: Decimal,
) -> Literal["none", "watch", "likely"]:
    if min_balance < ZERO:
        return "likely"
    if min_balance < minimum_buffer:
        return "watch"
    return "none"


def days_until_threshold(
    points: list[CashflowDayPoint],
    *,
    as_of: date,
    threshold: Decimal,
) -> int | None:
    for point in points:
        if point.date <= as_of:
            continue
        if point.projected_balance <= threshold:
            return (point.date - as_of).days
    return None


def suggested_daily_cutback(
    *,
    min_balance: Decimal,
    minimum_buffer: Decimal,
    horizon_days: int,
) -> Decimal:
    if horizon_days < 1:
        return ZERO
    shortfall = minimum_buffer - min_balance
    if shortfall <= ZERO:
        return ZERO
    return _money(shortfall / Decimal(horizon_days))


def build_engine_forecast(
    *,
    customer_id: str,
    accounts: list[Account],
    transactions: list[Transaction],
    scheduled_bills: list[ScheduledBill],
    as_of: date,
    horizon_days: int = 14,
    lookback_days: int = 30,
    payment_cycle: PaymentCycleConfig | None = None,
    minimum_buffer: Decimal | None = None,
    daily_spend_history: list[DailySpendObservation] | None = None,
    discretionary_multiplier: Decimal = Decimal("1.00"),
    one_time_purchase: Decimal = ZERO,
    one_time_purchase_on: date | None = None,
    pause_categories: set[str] | None = None,
    extra_paycheck: Decimal = ZERO,
    spend_model: Literal["flat", "weekday"] = "weekday",
    horizon_name: HorizonName | None = None,
) -> CashflowForecastResponse:
    if horizon_days < 1:
        raise ValueError("horizon_days must be >= 1")
    if lookback_days < 1:
        raise ValueError("lookback_days must be >= 1")

    buffer = (
        _money(minimum_buffer)
        if minimum_buffer is not None
        else _money(
            payment_cycle.minimum_buffer
            if payment_cycle is not None
            else Decimal("250.00")
        )
    )
    _, resolved_horizon = resolve_horizon_days(
        horizon=horizon_name, horizon_days=horizon_days
    )
    if horizon_name is not None:
        resolved_horizon = horizon_name

    raw_starting = spendable_cash_balance(accounts)
    purchase = _money(one_time_purchase) if one_time_purchase > ZERO else ZERO
    purchase_day = one_time_purchase_on
    horizon_end = as_of + timedelta(days=horizon_days)
    immediate_purchase = ZERO
    deferred_purchase = ZERO
    purchase_skipped = False
    if purchase > ZERO:
        if purchase_day is None or purchase_day <= as_of:
            immediate_purchase = purchase
            purchase_day = as_of
        elif purchase_day <= horizon_end:
            deferred_purchase = purchase
        else:
            purchase_skipped = True
    starting = (
        _money(raw_starting - immediate_purchase)
        if immediate_purchase > ZERO
        else raw_starting
    )

    customer_bills = [b for b in scheduled_bills if b.customer_id == customer_id]
    exclude = {b.category for b in customer_bills} | {"Bills"}
    if pause_categories:
        exclude = exclude | set(pause_categories)
    bill_names = {b.name for b in customer_bills}
    card_ids = credit_account_ids(accounts, customer_id)
    card_purchases_omitted = _card_purchases_in_lookback(
        transactions,
        customer_id=customer_id,
        card_ids=card_ids,
        as_of=as_of,
        lookback_days=lookback_days,
    )

    observations = daily_spend_history
    if observations is None:
        observations = discretionary_daily_observations(
            transactions,
            customer_id=customer_id,
            as_of=as_of,
            lookback_days=lookback_days,
            exclude_categories=exclude,
            exclude_description_tokens=bill_names,
            exclude_account_ids=card_ids,
        )
    rates, flat_daily = weekday_spend_rates(
        observations, as_of=as_of, lookback_days=lookback_days
    )
    mult = discretionary_multiplier
    rates = {k: _money(v * mult) for k, v in rates.items()}
    flat_daily = _money(flat_daily * mult)

    overrides = payment_cycle.bill_day_overrides if payment_cycle else None
    due = roll_bill_occurrences(
        customer_bills,
        customer_id=customer_id,
        as_of=as_of,
        horizon_days=horizon_days,
        bill_day_overrides=overrides,
    )
    bills_by_day: dict[date, list[ScheduledBill]] = {}
    for bill in due:
        bills_by_day.setdefault(bill.next_due_date, []).append(bill)

    paydays = payday_dates(
        payment_cycle, as_of=as_of, horizon_days=horizon_days
    )
    if extra_paycheck > ZERO:
        # Inject on the first forward day as an explicit scenario boost.
        paydays = list(paydays) + [(as_of + timedelta(days=1), _money(extra_paycheck))]
    income_by_day: dict[date, Decimal] = {}
    for day, amount in paydays:
        income_by_day[day] = _money(income_by_day.get(day, ZERO) + amount)
    income_assumed = bool(income_by_day)

    points: list[CashflowDayPoint] = []
    balance = starting
    min_balance = starting
    scheduled_total = ZERO
    estimated_total = ZERO
    income_total = ZERO
    events: list[CashflowEvent] = []
    as_of_labels: list[str] = []
    if immediate_purchase > ZERO:
        as_of_labels.append("Purchase")
        events.append(
            CashflowEvent(
                date=as_of,
                kind="purchase",
                label="Hypothetical purchase",
                amount=immediate_purchase,
            )
        )

    points.append(
        CashflowDayPoint(
            date=as_of,
            projected_balance=balance,
            scheduled_bill_total=ZERO,
            estimated_spend=ZERO,
            scheduled_bill_names=[],
            projected_income=ZERO,
            event_labels=as_of_labels,
        )
    )

    for offset in range(1, horizon_days + 1):
        day = as_of + timedelta(days=offset)
        day_bills = bills_by_day.get(day, [])
        bill_total = _money(sum((b.amount for b in day_bills), ZERO))
        if spend_model == "flat":
            est = flat_daily
        else:
            est = rates[day.weekday()]
        income = income_by_day.get(day, ZERO)
        day_purchase = (
            deferred_purchase if deferred_purchase > ZERO and day == purchase_day else ZERO
        )
        balance = _money(balance + income - bill_total - est - day_purchase)
        scheduled_total += bill_total
        estimated_total += est
        income_total += income
        min_balance = min(min_balance, balance)
        labels = [b.name for b in day_bills]
        if income > ZERO:
            labels.append("Payday")
            events.append(
                CashflowEvent(
                    date=day, kind="payday", label="Payday", amount=income
                )
            )
        if day_purchase > ZERO:
            labels.append("Purchase")
            events.append(
                CashflowEvent(
                    date=day,
                    kind="purchase",
                    label="Hypothetical purchase",
                    amount=day_purchase,
                )
            )
        for bill in day_bills:
            events.append(
                CashflowEvent(
                    date=day,
                    kind="bill",
                    label=bill.name,
                    amount=bill.amount,
                )
            )
        points.append(
            CashflowDayPoint(
                date=day,
                projected_balance=balance,
                scheduled_bill_total=bill_total,
                estimated_spend=est,
                scheduled_bill_names=[b.name for b in day_bills],
                projected_income=income,
                event_labels=labels,
            )
        )

    min_balance = _money(min_balance)
    risk = classify_overdraft_risk(
        min_balance=min_balance, minimum_buffer=buffer
    )
    zero_days = days_until_threshold(points, as_of=as_of, threshold=ZERO)
    if risk != "none" and zero_days is not None:
        events.append(
            CashflowEvent(
                date=as_of + timedelta(days=zero_days),
                kind="risk",
                label="Projected zero balance",
                amount=ZERO,
            )
        )
    events.sort(key=lambda e: (e.date, e.kind, e.label))

    assumptions = _forecast_assumptions(
        lookback_days=lookback_days,
        income_assumed=income_assumed,
        spend_model=spend_model,
        observation_count=len(observations),
        immediate_purchase=immediate_purchase,
        deferred_purchase=deferred_purchase,
        purchase_day=purchase_day,
        purchase_skipped=purchase_skipped,
        bill_count=len(due),
        card_purchases_omitted=card_purchases_omitted,
    )
    confidence = _forecast_confidence(
        observation_count=len(observations), lookback_days=lookback_days
    )

    return CashflowForecastResponse(
        customer_id=customer_id,
        as_of=as_of,
        horizon_days=horizon_days,
        starting_balance=_money(raw_starting),
        daily_estimated_spend=flat_daily,
        scheduled_bills_in_horizon=due,
        estimated_spend_total=_money(estimated_total),
        scheduled_bills_total=_money(scheduled_total),
        ending_balance=_money(balance),
        min_balance=min_balance,
        points=points,
        days_until_zero=zero_days,
        overdraft_risk=risk,
        horizon=resolved_horizon,
        income_assumed=income_assumed,
        projected_income_total=_money(income_total),
        next_events=events[:12],
        spend_model=spend_model,
        weekday_rates={WEEKDAY_NAMES[i]: rates[i] for i in range(7)},
        assumptions=assumptions,
        confidence=confidence,
    )


def _forecast_assumptions(
    *,
    lookback_days: int,
    income_assumed: bool,
    spend_model: str,
    observation_count: int,
    immediate_purchase: Decimal,
    deferred_purchase: Decimal,
    purchase_day: date | None,
    purchase_skipped: bool,
    bill_count: int,
    card_purchases_omitted: bool = False,
) -> list[str]:
    notes = [
        (
            f"Discretionary spend is a {spend_model} estimate from ~{lookback_days} "
            "days of history — not a guarantee of future spending."
        ),
        (
            "Known bills are scheduled separately and excluded from estimated "
            "discretionary spend to avoid double-counting."
        ),
    ]
    if income_assumed:
        notes.append(
            "Future income is included only because a payment cycle is configured "
            "in Settings."
        )
    else:
        notes.append(
            "No future income is assumed. Configure a payment cycle to project paydays."
        )
    if bill_count == 0:
        notes.append("No known bills fall in this horizon.")
    if observation_count < max(7, lookback_days // 4):
        notes.append(
            "Limited spending history — treat this forecast as directional only."
        )
    if immediate_purchase > ZERO:
        notes.append(
            f"Hypothetical purchase of ${_money(immediate_purchase)} applied immediately "
            f"({purchase_day})."
        )
    if deferred_purchase > ZERO and purchase_day is not None:
        notes.append(
            f"Hypothetical purchase of ${_money(deferred_purchase)} applied on "
            f"{purchase_day.isoformat()}."
        )
    if purchase_skipped:
        notes.append(
            "Purchase date is beyond the forecast horizon, so it was not applied."
        )
    if card_purchases_omitted:
        notes.append(
            "Credit-card purchases are not immediate cash outflow. "
            "That spending counts once; cash leaves when the card is paid."
        )
    return notes


def _forecast_confidence(
    *, observation_count: int, lookback_days: int
) -> Literal["low", "medium", "high"]:
    if observation_count < 5:
        return "low"
    if observation_count < max(10, lookback_days // 3):
        return "medium"
    return "high"


def compute_engine_safe_to_spend(
    *,
    customer_id: str,
    accounts: list[Account],
    transactions: list[Transaction],
    scheduled_bills: list[ScheduledBill],
    as_of: date,
    horizon_days: int = 14,
    lookback_days: int = 30,
    minimum_buffer: Decimal = Decimal("250.00"),
    payment_cycle: PaymentCycleConfig | None = None,
    daily_spend_history: list[DailySpendObservation] | None = None,
    spend_model: Literal["flat", "weekday"] = "weekday",
    discretionary_multiplier: Decimal = Decimal("1.00"),
    pause_categories: set[str] | None = None,
    floor_at_zero: bool = True,
) -> SafeToSpendResponse:
    if minimum_buffer < 0:
        raise ValueError("minimum_buffer must be non-negative")

    available = spendable_cash_balance(accounts)
    pending_expenses = ZERO
    pending_income = ZERO
    for txn in transactions:
        if txn.customer_id != customer_id:
            continue
        if txn.status != TransactionStatus.PENDING:
            continue
        if txn.kind == TransactionKind.EXPENSE:
            pending_expenses += abs(txn.amount)
        elif txn.kind == TransactionKind.INCOME:
            pending_income += txn.amount

    forecast = build_engine_forecast(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=scheduled_bills,
        as_of=as_of,
        horizon_days=horizon_days,
        lookback_days=lookback_days,
        payment_cycle=payment_cycle,
        minimum_buffer=minimum_buffer,
        daily_spend_history=daily_spend_history,
        spend_model=spend_model,
        discretionary_multiplier=discretionary_multiplier,
        pause_categories=pause_categories,
    )
    # Known pending income may clear; configured paydays are already in the
    # forecast totals when income_assumed — do not double-count them here.
    # Safe-to-spend remains conservative: subtract bills + est spend + buffer,
    # add only *pending* income (not projected paydays). Floor at $0 so the
    # customer hero never shows a negative "spendable" amount. Callers that
    # report a signed shortfall (What If? / scenarios) pass floor_at_zero=False
    # so a purchase is subtracted from the real remainder, not from $0.
    safe = _money(
        available
        + pending_income
        - pending_expenses
        - forecast.scheduled_bills_total
        - forecast.estimated_spend_total
        - minimum_buffer
    )
    if floor_at_zero and safe < ZERO:
        safe = ZERO
    return SafeToSpendResponse(
        customer_id=customer_id,
        as_of=as_of,
        horizon_days=horizon_days,
        available_balance=available,
        pending_expenses=_money(pending_expenses),
        pending_income=_money(pending_income),
        scheduled_bills_total=forecast.scheduled_bills_total,
        estimated_spend_total=forecast.estimated_spend_total,
        minimum_buffer=_money(minimum_buffer),
        safe_to_spend=safe,
    )


def compute_overdraft_risk(
    *,
    customer_id: str,
    accounts: list[Account],
    transactions: list[Transaction],
    scheduled_bills: list[ScheduledBill],
    as_of: date,
    horizon_days: int = 14,
    lookback_days: int = 30,
    minimum_buffer: Decimal = Decimal("250.00"),
    payment_cycle: PaymentCycleConfig | None = None,
    daily_spend_history: list[DailySpendObservation] | None = None,
) -> OverdraftRiskResponse:
    forecast = build_engine_forecast(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=scheduled_bills,
        as_of=as_of,
        horizon_days=horizon_days,
        lookback_days=lookback_days,
        payment_cycle=payment_cycle,
        minimum_buffer=minimum_buffer,
        daily_spend_history=daily_spend_history,
    )
    cutback = suggested_daily_cutback(
        min_balance=forecast.min_balance,
        minimum_buffer=minimum_buffer,
        horizon_days=horizon_days,
    )
    below_buffer = days_until_threshold(
        forecast.points, as_of=as_of, threshold=minimum_buffer - CENT
    )
    if forecast.overdraft_risk == "likely":
        note = (
            "Projected balance crosses below zero within the horizon. "
            f"Cutting about {cutback}/day of discretionary spend keeps a "
            f"{_money(minimum_buffer)} buffer."
            if cutback > ZERO
            else "Projected balance crosses below zero within the horizon."
        )
    elif forecast.overdraft_risk == "watch":
        note = (
            f"Projected balance dips below the {_money(minimum_buffer)} buffer. "
            f"Suggested discretionary cutback: {cutback}/day."
        )
    else:
        note = (
            f"Projected min balance stays at or above the "
            f"{_money(minimum_buffer)} buffer."
        )
    return OverdraftRiskResponse(
        customer_id=customer_id,
        as_of=as_of,
        horizon_days=horizon_days,
        minimum_buffer=_money(minimum_buffer),
        starting_balance=forecast.starting_balance,
        min_projected_balance=forecast.min_balance,
        ending_balance=forecast.ending_balance,
        days_until_zero=forecast.days_until_zero,
        days_until_below_buffer=below_buffer,
        overdraft_risk=forecast.overdraft_risk,
        suggested_daily_cutback=cutback,
        income_assumed=forecast.income_assumed,
        note=note,
    )


def _scenario_label(spec: ScenarioSpec) -> str:
    return {
        "baseline": "Baseline",
        "cut_discretionary_20": "Cut discretionary 20%",
        "pause_category": f"Pause {(spec.category or 'category')}",
        "extra_paycheck": "Extra paycheck",
        "one_time_purchase": "One-time purchase",
        "aggressive_save": "Aggressive save (40% cut)",
    }.get(spec.name, spec.name)


def _scenario_note(spec: ScenarioSpec) -> str:
    return {
        "baseline": "Current bills, weekday spend pattern, and configured paydays.",
        "cut_discretionary_20": "Discretionary daily spend reduced by 20%.",
        "pause_category": (
            f"Category '{spec.category}' excluded from estimated spend."
            if spec.category
            else "No category provided; treated as baseline."
        ),
        "extra_paycheck": (
            f"Adds ${_money(spec.amount or ZERO)} on the first forecast day."
        ),
        "one_time_purchase": (
            f"Applies a ${_money(spec.amount or ZERO)} purchase"
            + (
                f" on {spec.on_date.isoformat()}."
                if spec.on_date is not None
                else " immediately."
            )
        ),
        "aggressive_save": "Discretionary daily spend reduced by 40%.",
    }.get(spec.name, "")


def simulate_scenarios(
    *,
    customer_id: str,
    accounts: list[Account],
    transactions: list[Transaction],
    scheduled_bills: list[ScheduledBill],
    as_of: date,
    request: ScenarioRequest,
    payment_cycle: PaymentCycleConfig | None = None,
    daily_spend_history: list[DailySpendObservation] | None = None,
) -> ScenarioSimulationResponse:
    series: list[ScenarioSeries] = []
    prepared: list[tuple[ScenarioSpec, CashflowForecastResponse, Decimal]] = []
    for spec in request.scenarios:
        multiplier = Decimal("1.00")
        pause: set[str] | None = None
        purchase = ZERO
        purchase_on: date | None = None
        extra = ZERO
        if spec.name == "cut_discretionary_20":
            multiplier = Decimal("0.80")
        elif spec.name == "aggressive_save":
            multiplier = Decimal("0.60")
        elif spec.name == "pause_category" and spec.category:
            pause = {spec.category}
        elif spec.name == "one_time_purchase":
            purchase = _money(spec.amount or ZERO)
            purchase_on = spec.on_date
        elif spec.name == "extra_paycheck":
            extra = _money(spec.amount or ZERO)

        forecast = build_engine_forecast(
            customer_id=customer_id,
            accounts=accounts,
            transactions=transactions,
            scheduled_bills=scheduled_bills,
            as_of=as_of,
            horizon_days=request.horizon_days,
            lookback_days=request.lookback_days,
            payment_cycle=payment_cycle,
            minimum_buffer=request.minimum_buffer,
            daily_spend_history=daily_spend_history,
            discretionary_multiplier=multiplier,
            one_time_purchase=purchase,
            one_time_purchase_on=purchase_on,
            pause_categories=pause,
            extra_paycheck=extra,
        )
        # Use the scenario's own estimated spend (cuts / pauses). Keep the
        # signed value so a purchase is not subtracted from a floored $0.
        # Projected paydays stay out of safe-to-spend (same conservative rule).
        safe = compute_engine_safe_to_spend(
            customer_id=customer_id,
            accounts=accounts,
            transactions=transactions,
            scheduled_bills=scheduled_bills,
            as_of=as_of,
            horizon_days=request.horizon_days,
            lookback_days=request.lookback_days,
            minimum_buffer=request.minimum_buffer,
            payment_cycle=payment_cycle,
            daily_spend_history=daily_spend_history,
            discretionary_multiplier=multiplier,
            pause_categories=pause,
            floor_at_zero=False,
        )
        safe_value = _money(safe.safe_to_spend - purchase)
        prepared.append((spec, forecast, safe_value))

    baseline_forecast = next(
        (forecast for spec, forecast, _safe in prepared if spec.name == "baseline"),
        None,
    )
    if baseline_forecast is None and prepared:
        baseline_forecast = prepared[0][1]
    baseline_min = baseline_forecast.min_balance if baseline_forecast else ZERO
    shared_assumptions = (
        list(baseline_forecast.assumptions) if baseline_forecast else []
    )
    for spec, forecast, safe_value in prepared:
        delta = _money(forecast.min_balance - baseline_min)
        series.append(
            ScenarioSeries(
                name=spec.name,
                label=_scenario_label(spec),
                ending_balance=forecast.ending_balance,
                min_balance=forecast.min_balance,
                days_until_zero=forecast.days_until_zero,
                overdraft_risk=forecast.overdraft_risk,
                estimated_spend_total=forecast.estimated_spend_total,
                projected_income_total=forecast.projected_income_total,
                safe_to_spend=safe_value,
                delta_min_balance_vs_baseline=delta,
                points=forecast.points,
                note=_scenario_note(spec),
                assumptions=list(forecast.assumptions),
            )
        )

    income_assumed = any(s.projected_income_total > ZERO for s in series)
    return ScenarioSimulationResponse(
        customer_id=customer_id,
        as_of=as_of,
        horizon_days=request.horizon_days,
        income_assumed=income_assumed,
        baseline_min_balance=_money(baseline_min),
        scenarios=series,
        assumptions=shared_assumptions,
    )


def classify_behavior(
    *,
    customer_id: str,
    accounts: list[Account],
    transactions: list[Transaction],
    scheduled_bills: list[ScheduledBill],
    as_of: date,
    lookback_days: int = 28,
    recent_days: int = 7,
    baseline_days: int = 28,
    payment_cycle: PaymentCycleConfig | None = None,
) -> BehaviorSegmentResponse:
    customer_bills = [b for b in scheduled_bills if b.customer_id == customer_id]
    exclude = {b.category for b in customer_bills} | {"Bills"}
    observations = discretionary_daily_observations(
        transactions,
        customer_id=customer_id,
        as_of=as_of,
        lookback_days=lookback_days,
        exclude_categories=exclude,
        exclude_description_tokens={b.name for b in customer_bills},
    )
    weekend = ZERO
    weekday = ZERO
    daily_totals = [obs.total_spent for obs in observations]
    for obs in observations:
        if obs.day.weekday() >= 5:
            weekend += obs.total_spent
        else:
            weekday += obs.total_spent
    total = weekend + weekday
    weekend_share = (
        _money((weekend / total) * Decimal("100")) if total > ZERO else ZERO
    )
    weekday_share = (
        _money((weekday / total) * Decimal("100")) if total > ZERO else ZERO
    )

    if daily_totals:
        mean = sum(daily_totals, ZERO) / Decimal(len(daily_totals))
        if mean > ZERO:
            variance = sum(((x - mean) ** 2 for x in daily_totals), ZERO) / Decimal(
                len(daily_totals)
            )
            # Coefficient of variation, capped for display.
            volatility = _money((variance.sqrt() / mean) * Decimal("100"))
        else:
            volatility = ZERO
    else:
        volatility = ZERO

    drift_pct = _drift_pct(
        transactions,
        customer_id=customer_id,
        as_of=as_of,
        recent_days=recent_days,
        baseline_days=baseline_days,
    )
    cash = spendable_cash_balance(accounts)
    horizon_bills = roll_bill_occurrences(
        customer_bills,
        customer_id=customer_id,
        as_of=as_of,
        horizon_days=30,
        bill_day_overrides=(
            payment_cycle.bill_day_overrides if payment_cycle else None
        ),
    )
    bills_30 = _money(sum((b.amount for b in horizon_bills), ZERO))
    bill_ratio = (
        _money((bills_30 / cash) * Decimal("100")) if cash > ZERO else Decimal("100.00")
    )

    category_totals: dict[str, Decimal] = {}
    for txn in transactions:
        if txn.customer_id != customer_id:
            continue
        if txn.kind != TransactionKind.EXPENSE:
            continue
        if txn.status != TransactionStatus.POSTED:
            continue
        day = _as_utc_date(txn.posted_at)
        if as_of - timedelta(days=lookback_days) < day <= as_of:
            category_totals[txn.category] = category_totals.get(
                txn.category, ZERO
            ) + abs(txn.amount)
    top_categories = [
        name
        for name, _ in sorted(
            category_totals.items(), key=lambda item: item[1], reverse=True
        )[:3]
    ]

    # Rule-based segmentation (deterministic; no Gemini).
    segment: Literal["steady", "spiky", "weekend_heavy", "bill_stressed"]
    confidence = Decimal("0.55")
    if bill_ratio >= Decimal("40.00"):
        segment = "bill_stressed"
        confidence = Decimal("0.80")
    elif weekend_share >= Decimal("40.00") and total > ZERO:
        segment = "weekend_heavy"
        confidence = Decimal("0.75")
    elif volatility >= Decimal("80.00") or abs(drift_pct) >= Decimal("35.00"):
        segment = "spiky"
        confidence = Decimal("0.70")
    else:
        segment = "steady"
        confidence = Decimal("0.65") if total > ZERO else Decimal("0.40")

    tips = _tips_for_segment(segment, top_categories)
    loan_tips = _loan_payment_suggestions(
        segment=segment,
        bill_ratio=bill_ratio,
        bills_30=bills_30,
        cash=cash,
    )
    return BehaviorSegmentResponse(
        customer_id=customer_id,
        as_of=as_of,
        segment=segment,
        confidence=confidence,
        evidence=BehaviorEvidence(
            weekend_spend_share=weekend_share,
            weekday_spend_share=weekday_share,
            spend_volatility=volatility,
            drift_pct=drift_pct,
            bill_to_cash_ratio=bill_ratio,
            top_categories=top_categories,
        ),
        tips=tips,
        loan_payment_suggestions=loan_tips,
    )


def _posted_expense_total(
    transactions: list[Transaction],
    customer_id: str,
    *,
    start: date,
    end: date,
) -> Decimal:
    total = ZERO
    for txn in transactions:
        if txn.customer_id != customer_id:
            continue
        if txn.kind != TransactionKind.EXPENSE:
            continue
        if txn.status != TransactionStatus.POSTED:
            continue
        day = _as_utc_date(txn.posted_at)
        if start < day <= end:
            total += abs(txn.amount)
    return _money(total)


def _drift_pct(
    transactions: list[Transaction],
    *,
    customer_id: str,
    as_of: date,
    recent_days: int,
    baseline_days: int,
) -> Decimal:
    recent_start = as_of - timedelta(days=recent_days)
    baseline_end = recent_start
    baseline_start = baseline_end - timedelta(days=baseline_days)
    recent = _posted_expense_total(
        transactions, customer_id, start=recent_start, end=as_of
    )
    baseline_raw = _posted_expense_total(
        transactions, customer_id, start=baseline_start, end=baseline_end
    )
    baseline_comparable = _money(
        (baseline_raw / Decimal(baseline_days)) * Decimal(recent_days)
    )
    drift_amount = _money(recent - baseline_comparable)
    if baseline_comparable == ZERO:
        return ZERO if drift_amount == ZERO else Decimal("100.00")
    return _money((drift_amount / baseline_comparable) * Decimal("100"))


def _tips_for_segment(
    segment: str, top_categories: list[str]
) -> list[str]:
    lead = top_categories[0] if top_categories else "discretionary"
    mapping = {
        "steady": [
            f"Your spend is relatively steady — lock in a weekly cap for {lead}.",
            "Keep the minimum buffer funded; small automatic transfers help.",
        ],
        "spiky": [
            f"Spending spikes detected — set a soft daily limit on {lead}.",
            "Review the last 7 days vs the prior month and pause one non-essential category.",
        ],
        "weekend_heavy": [
            "Weekends drive a large share of spend — pre-budget a weekend envelope.",
            f"Move {lead} purchases to a weekday plan when possible.",
        ],
        "bill_stressed": [
            "Known bills use a large share of cash — prioritize bill dates on the runway.",
            "Consider shifting discretionary spend down 20% until after the next large bill.",
        ],
    }
    return list(mapping.get(segment, mapping["steady"]))


def _loan_payment_suggestions(
    *,
    segment: str,
    bill_ratio: Decimal,
    bills_30: Decimal,
    cash: Decimal,
) -> list[str]:
    """Deterministic payment-plan style tips (no Nessie loan writes)."""
    suggestions: list[str] = []
    if bill_ratio >= Decimal("40.00"):
        suggestions.append(
            f"Schedule loan/bill payments right after payday; "
            f"~{_money(bills_30)} in obligations land in the next 30 days."
        )
        suggestions.append(
            "If a lender allows, split the largest obligation into two semi-monthly pulls "
            "to reduce single-day overdraft risk."
        )
    elif cash > ZERO and bills_30 > ZERO:
        suggestions.append(
            "Keep minimum payments automated and route any surplus to the highest-APR balance first."
        )
    if segment == "spiky":
        suggestions.append(
            "Avoid variable extra loan payments on high-spend weeks; batch extras after a steady week."
        )
    return suggestions


def simulate_what_if_with_engine(
    *,
    customer_id: str,
    accounts: list[Account],
    transactions: list[Transaction],
    scheduled_bills: list[ScheduledBill],
    purchase_amount: Decimal,
    description: str,
    as_of: date,
    horizon_days: int = 14,
    lookback_days: int = 30,
    minimum_buffer: Decimal = Decimal("250.00"),
    payment_cycle: PaymentCycleConfig | None = None,
    purchase_date: date | None = None,
) -> WhatIfResponse:
    if purchase_amount <= 0:
        raise ValueError("purchase_amount must be positive")
    before = compute_engine_safe_to_spend(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=scheduled_bills,
        as_of=as_of,
        horizon_days=horizon_days,
        lookback_days=lookback_days,
        minimum_buffer=minimum_buffer,
        payment_cycle=payment_cycle,
        floor_at_zero=False,
    )
    # Hero "before" stays non-negative. The shortfall uses the unfloored
    # remainder so an already-negative safe-to-spend is not collapsed to $0
    # before the purchase is subtracted.
    displayed_before = before.safe_to_spend if before.safe_to_spend >= ZERO else ZERO
    after_safe = _money(before.safe_to_spend - purchase_amount)
    # Deferred purchases still reduce safe capacity once committed, but runway
    # timing follows purchase_date in the forecast path.
    affordable = after_safe >= ZERO
    forecast = build_engine_forecast(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=scheduled_bills,
        as_of=as_of,
        horizon_days=horizon_days,
        lookback_days=lookback_days,
        payment_cycle=payment_cycle,
        minimum_buffer=minimum_buffer,
        one_time_purchase=purchase_amount,
        one_time_purchase_on=purchase_date,
    )
    when = (
        f" on {purchase_date.isoformat()}"
        if purchase_date is not None and purchase_date > as_of
        else " immediately"
    )
    note = (
        f"Affordable{when} within safe-to-spend after buffer, bills, and estimated spend."
        if affordable
        else f"Exceeds safe-to-spend{when}; would consume buffer or projected runway."
    )
    if description:
        note = f"{note} ({description})"
    return WhatIfResponse(
        customer_id=customer_id,
        purchase_amount=_money(purchase_amount),
        description=description,
        safe_to_spend_before=displayed_before,
        safe_to_spend_after=after_safe,
        affordable=affordable,
        projected_min_balance_after=forecast.min_balance,
        note=note,
        purchase_date=purchase_date or as_of,
        assumptions=list(forecast.assumptions),
    )


def backtest_spend_forecast(
    *,
    customer_id: str,
    accounts: list[Account],
    transactions: list[Transaction],
    scheduled_bills: list[ScheduledBill],
    as_of: date,
    horizon_days: int = 7,
    lookback_days: int = 30,
    windows: int = 4,
    payment_cycle: PaymentCycleConfig | None = None,
) -> "BacktestResponse":
    """Compare forecast discretionary spend to actual posted spend historically.

    Requires enough history: each window needs lookback before ``as_of`` and a
    full horizon of actual expenses after that day. Balance path reconstruction
    is intentionally avoided — spend totals are the most reliable signal.
    """
    from app.models.schemas import BacktestResponse, BacktestWindowResult

    min_history_days = lookback_days + horizon_days * windows
    earliest_needed = as_of - timedelta(days=min_history_days)
    customer_txns = [
        t
        for t in transactions
        if t.customer_id == customer_id
        and t.status == TransactionStatus.POSTED
        and t.kind == TransactionKind.EXPENSE
    ]
    if not customer_txns:
        return BacktestResponse(
            customer_id=customer_id,
            as_of=as_of,
            windows_evaluated=0,
            horizon_days=horizon_days,
            sufficient_history=False,
            assumptions=[
                "Backtesting needs posted discretionary expenses in history.",
            ],
            note="Not enough transaction history to backtest forecasts.",
        )

    dated_days = [
        _as_utc_date(t.posted_at)
        for t in customer_txns
        if not _is_undated_posted_at(t.posted_at)
    ]
    if not dated_days:
        return BacktestResponse(
            customer_id=customer_id,
            as_of=as_of,
            windows_evaluated=0,
            horizon_days=horizon_days,
            sufficient_history=False,
            assumptions=[
                "Undated transactions are not treated as spending history.",
            ],
            note="Not enough transaction history to backtest forecasts.",
        )
    oldest = min(dated_days)
    if oldest > earliest_needed:
        return BacktestResponse(
            customer_id=customer_id,
            as_of=as_of,
            windows_evaluated=0,
            horizon_days=horizon_days,
            sufficient_history=False,
            assumptions=[
                f"Need history back to {earliest_needed.isoformat()}; oldest posted "
                f"expense is {oldest.isoformat()}.",
            ],
            note="Insufficient history for a reliable backtest.",
        )

    customer_bills = [b for b in scheduled_bills if b.customer_id == customer_id]
    exclude = {b.category for b in customer_bills} | {"Bills"}
    bill_names = {b.name for b in customer_bills}
    card_ids = credit_account_ids(accounts, customer_id)
    results: list[BacktestWindowResult] = []
    abs_errors: list[Decimal] = []
    pct_errors: list[Decimal] = []

    for index in range(windows):
        window_as_of = as_of - timedelta(days=horizon_days * (index + 1))
        forecast = build_engine_forecast(
            customer_id=customer_id,
            accounts=accounts,
            transactions=transactions,
            scheduled_bills=scheduled_bills,
            as_of=window_as_of,
            horizon_days=horizon_days,
            lookback_days=lookback_days,
            payment_cycle=payment_cycle,
        )
        actual = ZERO
        end = window_as_of + timedelta(days=horizon_days)
        for txn in customer_txns:
            if txn.account_id in card_ids:
                continue
            day = _as_utc_date(txn.posted_at)
            if day <= window_as_of or day > end:
                continue
            if txn.category in exclude:
                continue
            if any(token.lower() in txn.description.lower() for token in bill_names):
                continue
            actual = _money(actual + abs(txn.amount))
        err = _money(abs(forecast.estimated_spend_total - actual))
        abs_errors.append(err)
        if actual > ZERO:
            pct_errors.append(_money((err / actual) * Decimal("100")))
        results.append(
            BacktestWindowResult(
                as_of=window_as_of,
                horizon_days=horizon_days,
                forecast_spend=forecast.estimated_spend_total,
                actual_spend=actual,
                abs_error=err,
            )
        )

    mean_abs = (
        _money(sum(abs_errors, ZERO) / Decimal(len(abs_errors))) if abs_errors else None
    )
    mean_pct = (
        _money(sum(pct_errors, ZERO) / Decimal(len(pct_errors))) if pct_errors else None
    )
    return BacktestResponse(
        customer_id=customer_id,
        as_of=as_of,
        windows_evaluated=len(results),
        horizon_days=horizon_days,
        mean_abs_error=mean_abs,
        mean_abs_pct_error=mean_pct,
        sufficient_history=True,
        windows=list(reversed(results)),
        assumptions=[
            "Compares forecast discretionary spend to actual posted expenses.",
            "Known bill categories are excluded from both sides when possible.",
            "Errors reflect estimate uncertainty — not a guarantee of future accuracy.",
        ],
        note=(
            f"Evaluated {len(results)} historical windows of {horizon_days} days."
            if results
            else "No comparable windows."
        ),
    )
