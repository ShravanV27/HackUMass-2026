"""Shared provider interface for mock and live data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from app.models.schemas import Account, ScheduledBill, Transaction


class DataProvider(ABC):
    """Contract implemented by mock and live providers."""

    @property
    @abstractmethod
    def mode(self) -> Literal["mock", "live"]:
        raise NotImplementedError

    @abstractmethod
    def list_accounts(self, customer_id: str) -> list[Account]:
        raise NotImplementedError

    @abstractmethod
    def list_transactions(
        self,
        customer_id: str,
        account_id: str | None = None,
    ) -> list[Transaction]:
        raise NotImplementedError

    def list_scheduled_bills(self, customer_id: str) -> list[ScheduledBill]:
        """Known upcoming bills. Default empty — never invent obligations."""
        return []

    @abstractmethod
    def explain_dashboard(self, customer_id: str, summary_text: str) -> str:
        """Return a short natural-language insight for the dashboard."""
        raise NotImplementedError
