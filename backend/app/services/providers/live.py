"""Live provider: Nessie → TigerData reads + Gemini explanations."""

from __future__ import annotations

from typing import Literal

from app.config import Settings
from app.db.connection import get_connection
from app.db.repository import TigerRepository
from app.models.schemas import Account, Transaction
from app.services.gemini_client import GeminiClient, GeminiClientError
from app.services.nessie_client import NessieClient
from app.services.nessie_sync import NessieSyncService
from app.services.providers.base import DataProvider


class LiveDataProvider(DataProvider):
    """Syncs from Nessie into TigerData on read, then serves persisted rows.

    Gemini narrates precomputed dashboard metrics; calculations stay in Python.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        nessie: NessieClient | None = None,
        gemini: GeminiClient | None = None,
        sync_on_read: bool = True,
    ) -> None:
        self._settings = settings
        self._nessie = nessie
        self._gemini = gemini
        self._sync_on_read = sync_on_read
        self._synced_customers: set[str] = set()

    @property
    def mode(self) -> Literal["mock", "live"]:
        return "live"

    def list_accounts(self, customer_id: str) -> list[Account]:
        with get_connection(self._settings) as conn:
            repo = TigerRepository(conn)
            if self._sync_on_read:
                self._sync(customer_id, repo)
            return repo.list_accounts(customer_id)

    def list_transactions(
        self,
        customer_id: str,
        account_id: str | None = None,
    ) -> list[Transaction]:
        with get_connection(self._settings) as conn:
            repo = TigerRepository(conn)
            if self._sync_on_read:
                self._sync(customer_id, repo)
            return repo.list_transactions(customer_id, account_id=account_id)

    def explain_dashboard(self, customer_id: str, summary_text: str) -> str:
        client = self._gemini or GeminiClient(self._settings)
        try:
            return client.explain_dashboard(customer_id, summary_text)
        except GeminiClientError as exc:
            # Keep the dashboard usable if Gemini is unavailable.
            return (
                f"Live metrics for {customer_id}: {summary_text} "
                f"(Gemini unavailable: {exc})"
            )

    def _sync(self, customer_id: str, repo: TigerRepository) -> None:
        if customer_id in self._synced_customers:
            return
        nessie = self._nessie or NessieClient(self._settings)
        NessieSyncService(self._settings, nessie, repo).sync_customer(customer_id)
        self._synced_customers.add(customer_id)
