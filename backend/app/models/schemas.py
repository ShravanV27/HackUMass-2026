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


class SpendingWindowSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    start_at: datetime
    end_at: datetime
    source: Literal["sql", "memory"]
    categories: list[CategorySpend]
    income_posted: Decimal
    expenses_posted: Decimal
    pending_income: Decimal
    pending_expenses: Decimal
    transfer_volume: Decimal
    net_posted: Decimal
    net_including_pending: Decimal
