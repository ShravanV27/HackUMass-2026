"""Unit tests for Phase 1 PredictiveEngine."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.domain.predictive_engine import (
    build_engine_forecast,
    classify_behavior,
    compute_overdraft_risk,
    payday_dates,
    resolve_horizon_days,
    roll_bill_occurrences,
    simulate_scenarios,
    simulate_what_if_with_engine,
    sql_bounds_for_half_open_days,
    suggested_daily_cutback,
    weekday_spend_rates,
    DailySpendObservation,
)
from app.models.schemas import (
    Account,
    PaymentCycleConfig,
    ScenarioRequest,
    ScenarioSpec,
    ScheduledBill,
    Transaction,
    TransactionKind,
    TransactionStatus,
)


def _account(balance: str = "1000.00") -> Account:
    return Account(
        account_id="a1",
        customer_id="c1",
        name="Checking",
        account_type="Checking",
        balance=Decimal(balance),
    )


def _expense(day: int, amount: str, category: str = "Food") -> Transaction:
    return Transaction(
        transaction_id=f"e-{day}-{amount}-{category}",
        customer_id="c1",
        account_id="a1",
        amount=-Decimal(amount),
        kind=TransactionKind.EXPENSE,
        status=TransactionStatus.POSTED,
        description="x",
        category=category,
        posted_at=datetime(2026, 11, day, tzinfo=timezone.utc),
    )


def test_resolve_horizon_week_month() -> None:
    assert resolve_horizon_days(horizon="week") == (7, "week")
    assert resolve_horizon_days(horizon="month") == (30, "month")
    assert resolve_horizon_days(horizon_days=14) == (14, "custom")
    assert resolve_horizon_days(horizon_days=7) == (7, "week")


def test_roll_monthly_bills_across_month_horizon() -> None:
    bills = [
        ScheduledBill(
            bill_id="phone",
            customer_id="c1",
            account_id="a1",
            name="Phone",
            amount=Decimal("40.00"),
            category="Utilities",
            next_due_date=date(2026, 11, 16),
            cadence="monthly",
        )
    ]
    rolled = roll_bill_occurrences(
        bills,
        customer_id="c1",
        as_of=date(2026, 11, 14),
        horizon_days=40,
    )
    dates = [b.next_due_date for b in rolled]
    assert date(2026, 11, 16) in dates
    assert date(2026, 12, 16) in dates
    assert len(rolled) == 2


def test_bill_day_override() -> None:
    bills = [
        ScheduledBill(
            bill_id="rent",
            customer_id="c1",
            account_id="a1",
            name="Rent",
            amount=Decimal("980.00"),
            category="Housing",
            next_due_date=date(2026, 12, 5),
            cadence="monthly",
        )
    ]
    rolled = roll_bill_occurrences(
        bills,
        customer_id="c1",
        as_of=date(2026, 11, 14),
        horizon_days=30,
        bill_day_overrides={"rent": 20},
    )
    assert [b.next_due_date for b in rolled] == [date(2026, 11, 20)]


def test_payday_injection_biweekly() -> None:
    cfg = PaymentCycleConfig(
        cadence="biweekly",
        next_payday=date(2026, 11, 21),
        paycheck_amount=Decimal("2000.00"),
    )
    days = payday_dates(cfg, as_of=date(2026, 11, 14), horizon_days=30)
    assert days[0] == (date(2026, 11, 21), Decimal("2000.00"))
    assert days[1][0] == date(2026, 12, 5)


def test_weekday_rates_from_observations() -> None:
    # One Sunday spend in a 7-day window ending Nov 14 (Sat).
    obs = [DailySpendObservation(day=date(2026, 11, 8), total_spent=Decimal("70.00"))]
    rates, flat = weekday_spend_rates(
        obs, as_of=date(2026, 11, 14), lookback_days=7
    )
    assert flat == Decimal("10.00")
    assert rates[6] == Decimal("70.00")  # Sunday
    assert rates[0] == Decimal("0.00")  # Monday quiet


def test_forecast_includes_configured_income() -> None:
    cfg = PaymentCycleConfig(
        cadence="monthly",
        next_payday=date(2026, 11, 20),
        paycheck_amount=Decimal("1000.00"),
        minimum_buffer=Decimal("100.00"),
    )
    forecast = build_engine_forecast(
        customer_id="c1",
        accounts=[_account("200.00")],
        transactions=[],
        scheduled_bills=[],
        as_of=date(2026, 11, 14),
        horizon_days=14,
        lookback_days=30,
        payment_cycle=cfg,
        minimum_buffer=Decimal("100.00"),
    )
    assert forecast.income_assumed is True
    assert forecast.projected_income_total == Decimal("1000.00")
    payday_point = next(p for p in forecast.points if p.date == date(2026, 11, 20))
    assert payday_point.projected_income == Decimal("1000.00")


def test_overdraft_risk_and_cutback() -> None:
    bills = [
        ScheduledBill(
            bill_id="rent",
            customer_id="c1",
            account_id="a1",
            name="Rent",
            amount=Decimal("900.00"),
            category="Housing",
            next_due_date=date(2026, 11, 16),
            cadence="one_time",
        )
    ]
    risk = compute_overdraft_risk(
        customer_id="c1",
        accounts=[_account("500.00")],
        transactions=[_expense(1, "300.00")],
        scheduled_bills=bills,
        as_of=date(2026, 11, 14),
        horizon_days=7,
        lookback_days=30,
        minimum_buffer=Decimal("250.00"),
    )
    assert risk.overdraft_risk == "likely"
    assert risk.days_until_zero is not None
    assert risk.suggested_daily_cutback > Decimal("0.00")
    assert suggested_daily_cutback(
        min_balance=Decimal("-100.00"),
        minimum_buffer=Decimal("250.00"),
        horizon_days=10,
    ) == Decimal("35.00")


def test_scenarios_cut_improves_min_balance() -> None:
    txns = [_expense(1, "300.00"), _expense(8, "300.00")]
    result = simulate_scenarios(
        customer_id="c1",
        accounts=[_account("800.00")],
        transactions=txns,
        scheduled_bills=[],
        as_of=date(2026, 11, 14),
        request=ScenarioRequest(
            horizon_days=14,
            lookback_days=30,
            minimum_buffer=Decimal("100.00"),
            scenarios=[
                ScenarioSpec(name="baseline"),
                ScenarioSpec(name="cut_discretionary_20"),
                ScenarioSpec(name="aggressive_save"),
                ScenarioSpec(
                    name="one_time_purchase", amount=Decimal("50.00")
                ),
            ],
        ),
    )
    by_name = {s.name: s for s in result.scenarios}
    assert (
        by_name["cut_discretionary_20"].min_balance
        >= by_name["baseline"].min_balance
    )
    assert (
        by_name["aggressive_save"].min_balance
        >= by_name["cut_discretionary_20"].min_balance
    )
    assert (
        by_name["one_time_purchase"].min_balance
        <= by_name["baseline"].min_balance
    )


def test_behavior_bill_stressed() -> None:
    bills = [
        ScheduledBill(
            bill_id="rent",
            customer_id="c1",
            account_id="a1",
            name="Rent",
            amount=Decimal("900.00"),
            category="Housing",
            next_due_date=date(2026, 11, 20),
            cadence="monthly",
        )
    ]
    behavior = classify_behavior(
        customer_id="c1",
        accounts=[_account("1000.00")],
        transactions=[_expense(3, "20.00")],
        scheduled_bills=bills,
        as_of=date(2026, 11, 14),
    )
    assert behavior.segment == "bill_stressed"
    assert behavior.tips
    assert behavior.loan_payment_suggestions


def test_sql_bounds_exclude_the_lookback_start_day() -> None:
    start, end = sql_bounds_for_half_open_days(
        start_exclusive=date(2026, 11, 7),
        end_inclusive=date(2026, 11, 14),
    )
    assert start == datetime(2026, 11, 8, tzinfo=timezone.utc)
    assert end == datetime(2026, 11, 15, tzinfo=timezone.utc)


def test_stale_weekly_bill_does_not_emit_past_dates() -> None:
    bills = [
        ScheduledBill(
            bill_id="gym",
            customer_id="c1",
            account_id="a1",
            name="Gym",
            amount=Decimal("15.00"),
            category="Health",
            next_due_date=date(2024, 1, 1),
            cadence="weekly",
        )
    ]
    as_of = date(2026, 11, 14)
    rolled = roll_bill_occurrences(
        bills, customer_id="c1", as_of=as_of, horizon_days=14
    )
    assert rolled
    assert all(as_of < bill.next_due_date <= date(2026, 11, 28) for bill in rolled)


def test_cut_and_pause_raise_scenario_safe_to_spend() -> None:
    result = simulate_scenarios(
        customer_id="c1",
        accounts=[_account("2000.00")],
        transactions=[_expense(1, "300.00", "Food"), _expense(8, "300.00", "Food")],
        scheduled_bills=[],
        as_of=date(2026, 11, 14),
        request=ScenarioRequest(
            horizon_days=14,
            lookback_days=30,
            minimum_buffer=Decimal("50.00"),
            scenarios=[
                ScenarioSpec(name="baseline"),
                ScenarioSpec(name="cut_discretionary_20"),
                ScenarioSpec(name="pause_category", category="Food"),
            ],
        ),
    )
    by_name = {s.name: s for s in result.scenarios}
    assert by_name["cut_discretionary_20"].estimated_spend_total < (
        by_name["baseline"].estimated_spend_total
    )
    assert (
        by_name["cut_discretionary_20"].safe_to_spend
        > by_name["baseline"].safe_to_spend
    )
    assert by_name["pause_category"].estimated_spend_total == Decimal("0.00")
    assert by_name["pause_category"].safe_to_spend > by_name["baseline"].safe_to_spend


def test_scenario_delta_uses_baseline_even_when_it_is_not_first() -> None:
    result = simulate_scenarios(
        customer_id="c1",
        accounts=[_account("1000.00")],
        transactions=[],
        scheduled_bills=[],
        as_of=date(2026, 11, 14),
        request=ScenarioRequest(
            horizon_days=7,
            lookback_days=30,
            minimum_buffer=Decimal("0.00"),
            scenarios=[
                ScenarioSpec(name="one_time_purchase", amount=Decimal("100.00")),
                ScenarioSpec(name="baseline"),
            ],
        ),
    )
    by_name = {s.name: s for s in result.scenarios}
    assert by_name["baseline"].delta_min_balance_vs_baseline == Decimal("0.00")
    assert by_name["one_time_purchase"].delta_min_balance_vs_baseline == Decimal(
        "-100.00"
    )


def test_what_if_shortfall_is_not_measured_from_floored_zero() -> None:
    result = simulate_what_if_with_engine(
        customer_id="c1",
        accounts=[_account("100.00")],
        transactions=[],
        scheduled_bills=[
            ScheduledBill(
                bill_id="rent",
                customer_id="c1",
                account_id="a1",
                name="Rent",
                amount=Decimal("900.00"),
                category="Housing",
                next_due_date=date(2026, 11, 20),
                cadence="one_time",
            )
        ],
        purchase_amount=Decimal("50.00"),
        description="Shoes",
        as_of=date(2026, 11, 14),
        horizon_days=14,
        lookback_days=30,
        minimum_buffer=Decimal("250.00"),
    )
    # 100 cash - 900 bill - 250 buffer = -1050, then -50 purchase.
    assert result.safe_to_spend_before == Decimal("0.00")
    assert result.safe_to_spend_after == Decimal("-1100.00")
    assert result.affordable is False
