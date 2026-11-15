"""Pydantic contracts shared by API routes and providers."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TransactionKind(str, Enum):
    INCOME = "income"
    EXPENSE = "expense"
    TRANSFER = "transfer"


class TransactionStatus(str, Enum):
    POSTED = "posted"
    PENDING = "pending"


class Account(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str
    customer_id: str
    name: str
    account_type: str
    balance: Decimal
    currency: str = "USD"


class Transaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str
    customer_id: str
    account_id: str
    amount: Decimal = Field(description="Signed amount: income +, expense -")
    kind: TransactionKind
    status: TransactionStatus
    description: str
    category: str
    posted_at: datetime
    counterparty_account_id: str | None = None


class AccountsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    accounts: list[Account]


class TransactionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    account_id: str | None = None
    transactions: list[Transaction]


class MoneyFlowSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    income_posted: Decimal
    expenses_posted: Decimal
    pending_expenses: Decimal
    pending_income: Decimal
    net_posted: Decimal
    net_including_pending: Decimal
    transfer_volume: Decimal


class ForecastPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    projected_balance: Decimal


class ForecastSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    starting_balance: Decimal
    daily_net_rate: Decimal
    horizon_days: int
    points: list[ForecastPoint]
    mode: Literal["mock", "live"]


class DashboardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    mode: Literal["mock", "live"]
    accounts: list[Account]
    money_flow: MoneyFlowSummary
    forecast: ForecastSummary
    insight: str


class CategorySpend(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    total_spent: Decimal
    txn_count: int


class DailySpendPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: date
    total_spent: Decimal
    txn_count: int


class SpendingWindowSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    start_at: datetime
    end_at: datetime
    source: Literal["sql", "memory", "continuous_aggregate"]
    categories: list[CategorySpend]
    daily: list[DailySpendPoint] = Field(default_factory=list)
    income_posted: Decimal
    expenses_posted: Decimal
    pending_income: Decimal
    pending_expenses: Decimal
    transfer_volume: Decimal
    net_posted: Decimal
    net_including_pending: Decimal


class ScheduledBill(BaseModel):
    """Known upcoming payment — never invented by the model."""

    model_config = ConfigDict(extra="forbid")

    bill_id: str
    customer_id: str
    account_id: str
    name: str
    amount: Decimal
    category: str
    next_due_date: date
    cadence: str = "monthly"


class CashflowDayPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    projected_balance: Decimal
    scheduled_bill_total: Decimal
    estimated_spend: Decimal
    scheduled_bill_names: list[str]
    projected_income: Decimal = Decimal("0.00")
    event_labels: list[str] = Field(default_factory=list)


class CashflowEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    kind: Literal["bill", "payday", "risk", "purchase"]
    label: str
    amount: Decimal = Decimal("0.00")


class CashflowForecastResponse(BaseModel):
    """Cash runway projection distinguishing scheduled bills vs estimated spend.

    Future income is included only when a payment-cycle config is set
    (``income_assumed=True``). Otherwise no income is invented.
    """

    model_config = ConfigDict(extra="forbid")

    customer_id: str
    as_of: date
    horizon_days: int
    starting_balance: Decimal
    daily_estimated_spend: Decimal
    scheduled_bills_in_horizon: list[ScheduledBill]
    estimated_spend_total: Decimal
    scheduled_bills_total: Decimal
    ending_balance: Decimal
    min_balance: Decimal
    points: list[CashflowDayPoint]
    # PredictiveEngine extensions (defaults preserve older clients).
    days_until_zero: int | None = None
    overdraft_risk: Literal["none", "watch", "likely"] = "none"
    horizon: Literal["week", "month", "custom"] = "custom"
    income_assumed: bool = False
    projected_income_total: Decimal = Decimal("0.00")
    next_events: list[CashflowEvent] = Field(default_factory=list)
    spend_model: Literal["flat", "weekday"] = "weekday"
    weekday_rates: dict[str, Decimal] = Field(default_factory=dict)
    # Phase 5 — surface uncertainty instead of presenting estimates as certainty.
    assumptions: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"


class SafeToSpendResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    as_of: date
    horizon_days: int
    available_balance: Decimal
    pending_expenses: Decimal
    pending_income: Decimal
    scheduled_bills_total: Decimal
    estimated_spend_total: Decimal
    minimum_buffer: Decimal
    safe_to_spend: Decimal


class SpendingDriftResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    as_of: date
    recent_days: int
    baseline_days: int
    recent_spend: Decimal
    baseline_spend: Decimal
    drift_amount: Decimal
    drift_pct: Decimal
    direction: Literal["up", "down", "flat"]


class WhatIfRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(gt=0, description="Hypothetical purchase amount")
    description: str = "Hypothetical purchase"
    minimum_buffer: Decimal = Field(default=Decimal("250.00"), ge=0)
    horizon_days: int = Field(default=14, ge=1, le=90)
    purchase_date: date | None = Field(
        default=None,
        description="Optional day the purchase hits; None = immediate.",
    )


class WhatIfResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    purchase_amount: Decimal
    description: str
    safe_to_spend_before: Decimal
    safe_to_spend_after: Decimal
    affordable: bool
    projected_min_balance_after: Decimal
    note: str
    purchase_date: date | None = None
    assumptions: list[str] = Field(default_factory=list)
    purchase_applied: bool = True


class PaymentCycleConfig(BaseModel):
    """Configurable payday / buffer settings for the PredictiveEngine."""

    model_config = ConfigDict(extra="forbid")

    cadence: Literal["none", "biweekly", "semimonthly", "monthly"] = "none"
    next_payday: date | None = None
    paycheck_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    minimum_buffer: Decimal = Field(default=Decimal("250.00"), ge=0)
    # Optional day-of-month overrides keyed by bill_id (1-31).
    bill_day_overrides: dict[str, int] = Field(default_factory=dict)


class PaymentCycleSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    payment_cycle: PaymentCycleConfig
    source: Literal["default", "memory", "database"]


class OverdraftRiskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    as_of: date
    horizon_days: int
    minimum_buffer: Decimal
    starting_balance: Decimal
    min_projected_balance: Decimal
    ending_balance: Decimal
    days_until_zero: int | None
    days_until_below_buffer: int | None
    overdraft_risk: Literal["none", "watch", "likely"]
    suggested_daily_cutback: Decimal
    income_assumed: bool
    note: str


class ScenarioSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal[
        "baseline",
        "cut_discretionary_20",
        "pause_category",
        "extra_paycheck",
        "one_time_purchase",
        "aggressive_save",
    ]
    # Used by pause_category / one_time_purchase / extra_paycheck variants.
    category: str | None = None
    amount: Decimal | None = Field(default=None, ge=0)
    # Optional calendar day for one_time_purchase (None = immediate / as_of).
    on_date: date | None = None


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    horizon_days: int = Field(default=14, ge=1, le=90)
    lookback_days: int = Field(default=30, ge=1, le=365)
    minimum_buffer: Decimal = Field(default=Decimal("250.00"), ge=0)
    scenarios: list[ScenarioSpec] = Field(
        default_factory=lambda: [
            ScenarioSpec(name="baseline"),
            ScenarioSpec(name="cut_discretionary_20"),
            ScenarioSpec(name="aggressive_save"),
        ]
    )


class ScenarioSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    ending_balance: Decimal
    min_balance: Decimal
    days_until_zero: int | None
    overdraft_risk: Literal["none", "watch", "likely"]
    estimated_spend_total: Decimal
    projected_income_total: Decimal
    safe_to_spend: Decimal
    delta_min_balance_vs_baseline: Decimal
    points: list[CashflowDayPoint]
    note: str
    assumptions: list[str] = Field(default_factory=list)


class ScenarioSimulationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    as_of: date
    horizon_days: int
    income_assumed: bool
    baseline_min_balance: Decimal
    scenarios: list[ScenarioSeries]
    assumptions: list[str] = Field(default_factory=list)


class BacktestWindowResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: date
    horizon_days: int
    forecast_spend: Decimal
    actual_spend: Decimal
    abs_error: Decimal


class BacktestResponse(BaseModel):
    """Historical spend-forecast accuracy where enough posted history exists."""

    model_config = ConfigDict(extra="forbid")

    customer_id: str
    as_of: date
    windows_evaluated: int
    horizon_days: int
    mean_abs_error: Decimal | None = None
    mean_abs_pct_error: Decimal | None = None
    sufficient_history: bool
    windows: list[BacktestWindowResult] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    note: str


class BehaviorEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekend_spend_share: Decimal
    weekday_spend_share: Decimal
    spend_volatility: Decimal
    drift_pct: Decimal
    bill_to_cash_ratio: Decimal
    top_categories: list[str] = Field(default_factory=list)


class BehaviorSegmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    as_of: date
    segment: Literal["steady", "spiky", "weekend_heavy", "bill_stressed"]
    confidence: Decimal
    evidence: BehaviorEvidence
    tips: list[str]
    loan_payment_suggestions: list[str] = Field(default_factory=list)


class SyncResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    mode: Literal["mock", "live"]
    status: Literal["ok", "skipped", "synced"]
    message: str
    accounts_upserted: int = 0
    transactions_upserted: int = 0


class PredictionSnapshot(BaseModel):
    """Compact prediction strip for Demo Lab before/after impact."""

    model_config = ConfigDict(extra="forbid")

    safe_to_spend: Decimal
    min_projected_balance: Decimal
    ending_balance: Decimal
    overdraft_risk: Literal["none", "watch", "likely"]
    days_until_zero: int | None = None
    scheduled_bills_total: Decimal = Decimal("0.00")
    estimated_spend_total: Decimal = Decimal("0.00")


class DemoPurchaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1)
    amount: Decimal = Field(gt=0)
    description: str = "Demo Lab purchase"
    status: Literal["pending", "posted", "completed"] = "pending"
    merchant_id: str | None = None
    horizon_days: int = Field(default=14, ge=1, le=90)
    lookback_days: int = Field(default=30, ge=1, le=365)
    minimum_buffer: Decimal = Field(default=Decimal("250.00"), ge=0)


class DemoDepositRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1)
    amount: Decimal = Field(gt=0)
    description: str = "Demo Lab deposit"
    status: Literal["pending", "posted", "completed"] = "pending"
    horizon_days: int = Field(default=14, ge=1, le=90)
    lookback_days: int = Field(default=30, ge=1, le=365)
    minimum_buffer: Decimal = Field(default=Decimal("250.00"), ge=0)


class DemoBillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1)
    payee: str = Field(min_length=1)
    amount: Decimal = Field(gt=0)
    nickname: str | None = None
    payment_date: date | None = None
    recurring_date: int | None = Field(default=None, ge=1, le=28)
    horizon_days: int = Field(default=14, ge=1, le=90)
    lookback_days: int = Field(default=30, ge=1, le=365)
    minimum_buffer: Decimal = Field(default=Decimal("250.00"), ge=0)


class DemoActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    mode: Literal["mock", "live"]
    action: Literal["purchase", "deposit", "bill"]
    resource_id: str
    account_id: str
    amount: Decimal
    description: str
    synced: bool
    before: PredictionSnapshot
    after: PredictionSnapshot
    delta_safe_to_spend: Decimal
    delta_min_balance: Decimal
    note: str


class DemoStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    mode: Literal["mock", "live"]
    writes_enabled: bool
    bound_nessie_customer_id: str | None
    env_nessie_customer_id: str | None
    message: str


class BindCustomerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nessie_customer_id: str = Field(min_length=1)


class SessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_customer_id: str
    mode: Literal["mock", "live"]
    bound_nessie_customer_id: str | None
    writes_enabled: bool
    message: str


class AlertItem(BaseModel):
    """Deterministic predictive alert — metrics computed in Python only."""

    model_config = ConfigDict(extra="forbid")

    alert_id: str
    kind: Literal[
        "overdraft_risk",
        "spending_drift",
        "category_spike",
        "bill_cluster",
    ]
    severity: Literal["info", "watch", "urgent"]
    title: str
    summary: str
    evidence: dict[str, str] = Field(default_factory=dict)
    related_metric: str | None = None


class AlertsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    as_of: date
    mode: Literal["mock", "live"]
    analytics_source: Literal["memory", "sql", "continuous_aggregate"] = "memory"
    alerts: list[AlertItem]


class InsightsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    as_of: date
    mode: Literal["mock", "live"]
    ai_generated: bool
    model: str | None = None
    alerts: list[AlertItem]
    alert_explanation: str
    behavior_segment: str | None = None
    coaching: str
    metric_glossary: dict[str, str] = Field(default_factory=dict)


class CopilotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500)
    include_scenario: bool = False
    # Optional hint only — question text is parsed and wins when it names a
    # concrete action (purchase / cut / income). Defaults to None so clients
    # do not accidentally force cut_discretionary_20 on unrelated asks.
    scenario_name: (
        Literal[
            "baseline",
            "cut_discretionary_20",
            "aggressive_save",
            "one_time_purchase",
            "extra_paycheck",
            "pause_category",
        ]
        | None
    ) = None
    scenario_amount: Decimal | None = Field(default=None, ge=0)
    scenario_category: str | None = Field(default=None, min_length=1, max_length=64)
    horizon_days: int = Field(default=14, ge=1, le=90)
    minimum_buffer: Decimal = Field(default=Decimal("250.00"), ge=0)


class CopilotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    question: str
    answer: str
    ai_generated: bool
    model: str | None = None
    grounded_metrics: dict[str, str] = Field(default_factory=dict)
    scenario_note: str | None = None
    refused: bool = False
