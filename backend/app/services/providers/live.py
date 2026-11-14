"""Live provider: Nessie → TigerData reads + Gemini explanations."""

from __future__ import annotations

import threading
from datetime import date
from decimal import Decimal
from typing import Literal

from app.config import Settings
from app.db.connection import get_connection
from app.db.repository import TigerRepository
from app.models.schemas import (
    Account,
    ScheduledBill,
    Transaction,
    TransactionKind,
    TransactionStatus,
)
from app.services.gemini_client import GeminiClient, GeminiClientError
from app.services.nessie_client import NessieClient, NessieClientError
from app.services.nessie_mapper import collect_scheduled_bills
from app.services.nessie_sync import NessieSyncService, resolve_nessie_customer_id
from app.services.providers.base import DataProvider


class LiveDataProvider(DataProvider):
    """Syncs from Nessie into TigerData on read, then serves persisted rows.

    Gemini narrates precomputed dashboard metrics; calculations stay in Python.
    Scheduled bills are fetched from Nessie (GET only) and are not invented.
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
        self._sync_lock = threading.Lock()
        self._bills_cache: dict[str, list[ScheduledBill]] = {}
        self._bills_lock = threading.Lock()

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

    def list_scheduled_bills(self, customer_id: str) -> list[ScheduledBill]:
        """Known Nessie bills for the mapped sandbox customer.

        Returns [] when Nessie has no bills or the bills endpoint is unavailable.
        Never invents obligations.
        """
        with self._bills_lock:
            if customer_id in self._bills_cache:
                return list(self._bills_cache[customer_id])

        # Ensure accounts/transactions are warm so forecasts see live balances.
        if self._sync_on_read:
            with get_connection(self._settings) as conn:
                repo = TigerRepository(conn)
                self._sync(customer_id, repo)

        try:
            nessie_customer_id = resolve_nessie_customer_id(
                self._settings, customer_id
            )
        except PermissionError:
            with self._bills_lock:
                self._bills_cache[customer_id] = []
            return []

        nessie = self._nessie or NessieClient(self._settings)
        try:
            raw = nessie.list_bills_for_customer(nessie_customer_id)
        except NessieClientError:
            with self._bills_lock:
                self._bills_cache[customer_id] = []
            return []

        bills = collect_scheduled_bills(
            raw,
            app_customer_id=customer_id,
            as_of=date.today(),
        )
        with self._bills_lock:
            self._bills_cache[customer_id] = bills
            return list(bills)

    def explain_dashboard(self, customer_id: str, summary_text: str) -> str:
        """Narrate precomputed metrics via Gemini when configured.

        Gemini is optional: missing credentials or API failures fall back to the
        deterministic summary_text. Never invents balances or exposes secrets.
        """
        try:
            client = self._gemini or GeminiClient(self._settings)
            return client.explain_dashboard(customer_id, summary_text)
        except GeminiClientError as exc:
            return _deterministic_insight(
                customer_id,
                summary_text,
                reason=_safe_gemini_error(exc, self._settings),
            )

    def invalidate_sync(self, customer_id: str) -> None:
        """Drop sync/bill caches so the next read refreshes from Nessie."""
        with self._sync_lock:
            self._synced_customers.discard(customer_id)
            with self._bills_lock:
                self._bills_cache.pop(customer_id, None)

    def invalidate_bills_cache(self, customer_id: str) -> None:
        with self._bills_lock:
            self._bills_cache.pop(customer_id, None)

    def force_sync(self, customer_id: str) -> dict[str, int]:
        """Clear sync/bill caches and pull a fresh Nessie → TigerData snapshot.

        Does not write, delete, or reseed Nessie data — GET + upsert only.
        """
        with self._sync_lock:
            self._synced_customers.discard(customer_id)
            with self._bills_lock:
                self._bills_cache.pop(customer_id, None)
            with get_connection(self._settings) as conn:
                repo = TigerRepository(conn)
                nessie = self._nessie or NessieClient(self._settings)
                result = NessieSyncService(
                    self._settings, nessie, repo
                ).sync_customer(customer_id)
                self._synced_customers.add(customer_id)
            return {
                "accounts_upserted": result.accounts_upserted,
                "transactions_upserted": result.transactions_upserted,
                "accounts_deleted": result.accounts_deleted,
                "transactions_deleted": result.transactions_deleted,
            }

    def apply_local_balance_delta(
        self,
        customer_id: str,
        account_id: str,
        delta: Decimal,
    ) -> bool:
        """Patch TigerData balance when Nessie ``balance_cents`` lags a Demo write.

        Also promotes a matching pending txn to posted so safe-to-spend does not
        double-count (available + pending) for the same cash movement.
        """
        if delta == 0:
            return False
        with get_connection(self._settings) as conn:
            repo = TigerRepository(conn)
            accounts = repo.list_accounts(customer_id)
            target: Account | None = None
            for account in accounts:
                if account.account_id == account_id:
                    target = account
                    break
            if target is None:
                return False
            patched = target.model_copy(
                update={"balance": (target.balance + delta).quantize(Decimal("0.01"))}
            )
            repo.upsert_accounts([patched])

            # Avoid STS double-count when the feed still marks the write pending.
            txns = repo.list_transactions(customer_id, account_id=account_id)
            abs_delta = abs(delta).quantize(Decimal("0.01"))
            want_kind = (
                TransactionKind.INCOME if delta > 0 else TransactionKind.EXPENSE
            )
            for txn in txns:
                if txn.status != TransactionStatus.PENDING:
                    continue
                if txn.kind != want_kind:
                    continue
                if abs(txn.amount).quantize(Decimal("0.01")) != abs_delta:
                    continue
                repo.upsert_transactions(
                    [
                        txn.model_copy(
                            update={"status": TransactionStatus.POSTED}
                        )
                    ]
                )
                break
        return True

    def _sync(self, customer_id: str, repo: TigerRepository) -> None:
        # Single-flight: PredictionsView fires cashflow/safe/drift in parallel;
        # without a lock each request ran a full Nessie→TigerData sync (~12s+)
        # and iOS timed out at 15s.
        if customer_id in self._synced_customers:
            return
        with self._sync_lock:
            if customer_id in self._synced_customers:
                return
            nessie = self._nessie or NessieClient(self._settings)
            NessieSyncService(self._settings, nessie, repo).sync_customer(
                customer_id
            )
            self._synced_customers.add(customer_id)


def _deterministic_insight(
    customer_id: str, summary_text: str, *, reason: str
) -> str:
    return (
        f"Live metrics for {customer_id}: {summary_text} "
        f"(Gemini unavailable: {reason})"
    )


def _safe_gemini_error(exc: BaseException, settings: Settings) -> str:
    """Short reason string with any configured secret fragments redacted."""
    text = str(exc).strip() or "unavailable"
    key = settings.gemini_api_key
    if key and key in text:
        text = text.replace(key, "***REDACTED***")
    return text[:160]
