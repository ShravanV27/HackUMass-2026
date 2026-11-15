"""Pass 4 regressions: horizon purchases, category pause, payment attribution."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.api.prediction_context import _tiger_discretionary_history
from app.config import Settings
from app.db.repository import _DISCRETIONARY_DAILY_SQL
from app.domain.alerts import category_spend_totals
from app.domain.predictive_engine import (
    DailySpendObservation,
    classify_behavior,
    simulate_scenarios,
    simulate_what_if_with_engine,
)
from app.models.schemas import (
    Account,
    CopilotRequest,
    ScenarioRequest,
    ScenarioSpec,
    ScheduledBill,
    Transaction,
    TransactionKind,
    TransactionStatus,
)
from app.services.insights_service import answer_copilot
from app.services.providers.mock import MockDataProvider

AS_OF = date(2026, 11, 14)


def _cash(balance: str = "1000.00") -> Account:
    return Account(
        account_id="checking",
        customer_id="c1",
        name="Checking",
        account_type="Checking",
        balance=Decimal(balance),
    )


def _card() -> Account:
    return Account(
        account_id="cc",
        customer_id="c1",
        name="Card",
        account_type="Credit Card",
        balance=Decimal("-100.00"),
    )


def _txn(
    txn_id: str,
    account_id: str,
    amount: str,
    *,
    day: int,
    category: str,
    description: str,
    counterparty: str | None = None,
) -> Transaction:
    return Transaction(
        transaction_id=txn_id,
        customer_id="c1",
        account_id=account_id,
        amount=Decimal(amount),
        kind=TransactionKind.EXPENSE,
        status=TransactionStatus.POSTED,
        description=description,
        category=category,
        posted_at=datetime(2026, 11, day, tzinfo=timezone.utc),
        counterparty_account_id=counterparty,
    )


def test_purchase_past_the_horizon_does_not_change_safe_to_spend() -> None:
    accounts = [_cash("1000.00")]
    common = dict(
        customer_id="c1",
        accounts=accounts,
        transactions=[],
        scheduled_bills=[],
        as_of=AS_OF,
        horizon_days=14,
        lookback_days=30,
        minimum_buffer=Decimal("0.00"),
    )
    inside = simulate_what_if_with_engine(
        **common,
        purchase_amount=Decimal("200.00"),
        description="Coat",
        purchase_date=date(2026, 11, 20),
    )
    outside = simulate_what_if_with_engine(
        **common,
        purchase_amount=Decimal("200.00"),
        description="Coat",
        purchase_date=date(2026, 12, 20),
    )
    assert inside.purchase_applied is True
    assert inside.safe_to_spend_after == inside.safe_to_spend_before - Decimal("200.00")
    assert outside.purchase_applied is False
    assert outside.safe_to_spend_before == Decimal("1000.00")
    assert outside.safe_to_spend_after == Decimal("1000.00")
    assert outside.projected_min_balance_after == Decimal("1000.00")
    assert "beyond the forecast horizon" in outside.note
    assert outside.affordable is True

    scenarios = simulate_scenarios(
        customer_id="c1",
        accounts=accounts,
        transactions=[],
        scheduled_bills=[],
        as_of=AS_OF,
        request=ScenarioRequest(
            horizon_days=14,
            lookback_days=30,
            minimum_buffer=Decimal("0.00"),
            scenarios=[
                ScenarioSpec(name="baseline"),
                ScenarioSpec(
                    name="one_time_purchase",
                    amount=Decimal("200.00"),
                    on_date=date(2026, 12, 20),
                ),
            ],
        ),
    )
    by_name = {row.name: row for row in scenarios.scenarios}
    assert by_name["one_time_purchase"].safe_to_spend == by_name["baseline"].safe_to_spend
    assert by_name["one_time_purchase"].min_balance == by_name["baseline"].min_balance
    assert by_name["one_time_purchase"].delta_min_balance_vs_baseline == Decimal("0.00")
    assert "outside this horizon" in by_name["one_time_purchase"].note


def test_copilot_does_not_call_an_outside_purchase_affordable() -> None:
    result = answer_copilot(
        customer_id="demo-customer-001",
        provider=MockDataProvider(),
        body=CopilotRequest(
            question="Can I afford a $500 purchase on 2026-12-20?",
            include_scenario=True,
            horizon_days=14,
            minimum_buffer=Decimal("50.00"),
        ),
        settings=Settings(use_mock_data=True, demo_customer_id="demo-customer-001"),
        as_of=AS_OF,
    )
    metrics = result.grounded_metrics
    assert metrics["scenario_amount"] == "500.00"
    assert metrics["scenario_on_date"] == "2026-12-20"
    assert metrics["scenario_purchase_applied"] == "false"
    assert metrics["scenario_affordable"] == "not_in_horizon"
    assert metrics["scenario_safe_to_spend"] == metrics["baseline_safe_to_spend"]
    assert "outside this forecast horizon" in result.answer
    assert "not applied" in result.answer
    assert "is affordable" not in result.answer
    assert "not affordable" not in result.answer


def test_pause_category_matches_case_and_ignores_preaggregated_history() -> None:
    txns = [
        _txn(
            "food",
            "checking",
            "-300.00",
            day=10,
            category="Food",
            description="lunch",
        )
    ]
    history = [
        DailySpendObservation(day=date(2026, 11, 10), total_spent=Decimal("300.00"))
    ]
    result = simulate_scenarios(
        customer_id="c1",
        accounts=[_cash("2000.00")],
        transactions=txns,
        scheduled_bills=[],
        as_of=AS_OF,
        daily_spend_history=history,
        request=ScenarioRequest(
            horizon_days=14,
            lookback_days=30,
            minimum_buffer=Decimal("0.00"),
            scenarios=[
                ScenarioSpec(name="baseline"),
                ScenarioSpec(name="pause_category", category="food"),
            ],
        ),
    )
    by_name = {row.name: row for row in result.scenarios}
    assert by_name["baseline"].estimated_spend_total > Decimal("0.00")
    assert by_name["pause_category"].estimated_spend_total == Decimal("0.00")
    assert by_name["pause_category"].safe_to_spend > by_name["baseline"].safe_to_spend


def test_bill_category_case_does_not_count_as_discretionary_behavior() -> None:
    behavior = classify_behavior(
        customer_id="c1",
        accounts=[_cash("1000.00")],
        transactions=[
            _txn(
                "groc",
                "checking",
                "-80.00",
                day=8,
                category="Groceries",
                description="market",
            )
        ],
        scheduled_bills=[
            ScheduledBill(
                bill_id="groceries",
                customer_id="c1",
                account_id="checking",
                name="Groceries plan",
                amount=Decimal("80.00"),
                category="groceries",
                next_due_date=date(2026, 11, 20),
                cadence="monthly",
            )
        ],
        as_of=AS_OF,
        lookback_days=28,
    )
    assert behavior.evidence.weekend_spend_share == Decimal("0.00")
    assert behavior.evidence.weekday_spend_share == Decimal("0.00")


def test_unmatched_card_payments_keep_their_own_categories() -> None:
    accounts = [_cash(), _card()]
    totals = category_spend_totals(
        [
            _txn(
                "visa",
                "checking",
                "-40.00",
                day=10,
                category="Visa",
                description="Visa card payment",
                counterparty="cc",
            ),
            _txn(
                "mc",
                "checking",
                "-60.00",
                day=12,
                category="Mastercard",
                description="Mastercard payment",
                counterparty="cc",
            ),
        ],
        customer_id="c1",
        start=date(2026, 11, 1),
        end=AS_OF,
        accounts=accounts,
    )
    assert totals == {"Visa": Decimal("40.00"), "Mastercard": Decimal("60.00")}


def test_partial_card_match_consumes_the_earlier_payment_first() -> None:
    accounts = [_cash(), _card()]
    totals = category_spend_totals(
        [
            _txn(
                "swipe",
                "cc",
                "-50.00",
                day=4,
                category="Shopping",
                description="store",
            ),
            _txn(
                "later",
                "checking",
                "-60.00",
                day=12,
                category="Mastercard",
                description="Mastercard payment",
                counterparty="cc",
            ),
            _txn(
                "earlier",
                "checking",
                "-40.00",
                day=10,
                category="Visa",
                description="Visa card payment",
                counterparty="cc",
            ),
        ],
        customer_id="c1",
        start=date(2026, 11, 1),
        end=AS_OF,
        accounts=accounts,
    )
    assert totals["Shopping"] == Decimal("50.00")
    assert totals["Mastercard"] == Decimal("50.00")
    assert "Visa" not in totals


def test_discretionary_sql_drops_bill_names_and_category_case() -> None:
    assert "lower(t.category)" in _DISCRETIONARY_DAILY_SQL
    assert "exclude_description_tokens" in _DISCRETIONARY_DAILY_SQL
    assert "position(token.value in lower(COALESCE(t.description, '')))" in (
        _DISCRETIONARY_DAILY_SQL
    )


def test_tiger_history_passes_bill_names(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Repo:
        def __init__(self, _conn: object) -> None:
            pass

        def discretionary_daily_spending(self, customer_id, **kwargs):
            captured.update(kwargs)
            return []

    class _Conn:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: object) -> bool:
            return False

    monkeypatch.setattr(
        "app.api.prediction_context.get_connection", lambda _settings: _Conn()
    )
    monkeypatch.setattr("app.api.prediction_context.TigerRepository", _Repo)
    history = _tiger_discretionary_history(
        "c1",
        Settings(
            use_mock_data=False,
            demo_customer_id="c1",
            database_url="postgresql://unused/db",
        ),
        as_of=AS_OF,
        lookback_days=30,
        bills=[
            ScheduledBill(
                bill_id="phone",
                customer_id="c1",
                account_id="checking",
                name="Phone",
                amount=Decimal("40.00"),
                category="Utilities",
                next_due_date=date(2026, 11, 20),
                cadence="monthly",
            )
        ],
    )
    assert history == []
    assert captured["exclude_description_tokens"] == ["Phone"]
