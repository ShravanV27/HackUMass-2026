"""Fixture-backed provider for Simulator and offline development."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from app.models.schemas import Account, ScheduledBill, Transaction
from app.services import mock_demo_state
from app.services.providers.base import DataProvider

_FIXTURE_PATH = Path(__file__).with_name("fixtures") / "demo_customer.json"


@lru_cache
def _load_fixture() -> dict[str, Any]:
    with _FIXTURE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def clear_fixture_cache() -> None:
    _load_fixture.cache_clear()


class MockDataProvider(DataProvider):
    @property
    def mode(self) -> Literal["mock", "live"]:
        return "mock"

    def list_accounts(self, customer_id: str) -> list[Account]:
        overlay = mock_demo_state.list_accounts(customer_id)
        if overlay is not None:
            return overlay
        data = _load_fixture()
        if data["customer_id"] != customer_id:
            return []
        return [Account.model_validate(row) for row in data["accounts"]]

    def list_transactions(
        self,
        customer_id: str,
        account_id: str | None = None,
    ) -> list[Transaction]:
        overlay = mock_demo_state.list_transactions(customer_id)
        if overlay is not None:
            txns = overlay
        else:
            data = _load_fixture()
            if data["customer_id"] != customer_id:
                return []
            txns = [Transaction.model_validate(row) for row in data["transactions"]]
        if account_id is not None:
            txns = [t for t in txns if t.account_id == account_id]
        return txns

    def list_scheduled_bills(self, customer_id: str) -> list[ScheduledBill]:
        overlay = mock_demo_state.list_scheduled_bills(customer_id)
        if overlay is not None:
            return overlay
        data = _load_fixture()
        if data["customer_id"] != customer_id:
            return []
        return [
            ScheduledBill.model_validate(row)
            for row in data.get("scheduled_bills", [])
        ]

    def explain_dashboard(self, customer_id: str, summary_text: str) -> str:
        return (
            f"Demo insight for {customer_id}: {summary_text} "
            "Internal transfers are excluded from spend totals; "
            "pending items are included in the short-term forecast."
        )
