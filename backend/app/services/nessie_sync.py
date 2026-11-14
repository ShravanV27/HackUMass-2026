"""Synchronize Nessie read-only data into TigerData."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.db.repository import TigerRepository
from app.services.nessie_client import AccountCollection, NessieClient
from app.services.nessie_mapper import (
    collect_account_transactions,
    collect_from_transaction_feed,
    map_account,
)


@dataclass(frozen=True)
class SyncResult:
    accounts_upserted: int
    transactions_upserted: int
    accounts_deleted: int
    transactions_deleted: int
    reconciled: bool
    nessie_customer_id: str
    app_customer_id: str


def resolve_nessie_customer_id(settings: Settings, app_customer_id: str) -> str:
    """Map app DEMO_CUSTOMER_ID → NESSIE_CUSTOMER_ID (fallback to demo id)."""
    if app_customer_id != settings.demo_customer_id:
        raise PermissionError(
            "Live sync is only enabled for the authenticated demo customer"
        )
    return settings.nessie_customer_id or app_customer_id


class NessieSyncService:
    """Pull accounts/transactions from Nessie (GET only) and upsert into TigerData."""

    def __init__(
        self,
        settings: Settings,
        nessie: NessieClient,
        repo: TigerRepository,
    ) -> None:
        self._settings = settings
        self._nessie = nessie
        self._repo = repo

    def resolve_nessie_customer_id(self, app_customer_id: str) -> str:
        return resolve_nessie_customer_id(self._settings, app_customer_id)

    def sync_customer(self, app_customer_id: str) -> SyncResult:
        self._repo.ensure_schema(require_hypertable=True)
        nessie_customer_id = self.resolve_nessie_customer_id(app_customer_id)

        # Fetch first — if this raises, TigerData is left unchanged (aside from schema).
        collection = self._fetch_accounts(nessie_customer_id)

        accounts = [
            map_account(row, app_customer_id=app_customer_id)
            for row in collection.items
        ]

        all_txns = []
        for account in accounts:
            all_txns.extend(
                self._transactions_for_account(
                    app_customer_id=app_customer_id,
                    account_id=account.account_id,
                )
            )

        write = self._repo.commit_customer_sync(
            customer_id=app_customer_id,
            accounts=accounts,
            transactions=all_txns,
            reconcile=collection.complete,
        )
        return SyncResult(
            accounts_upserted=write.accounts_upserted,
            transactions_upserted=write.transactions_upserted,
            accounts_deleted=write.accounts_deleted,
            transactions_deleted=write.transactions_deleted,
            reconciled=write.reconciled,
            nessie_customer_id=nessie_customer_id,
            app_customer_id=app_customer_id,
        )

    def _fetch_accounts(self, nessie_customer_id: str) -> AccountCollection:
        collection = self._nessie.fetch_accounts_for_customer(nessie_customer_id)
        if not collection.items and not self._settings.nessie_customer_id:
            # Fallback for sandboxes that only expose /accounts.
            return self._nessie.fetch_all_accounts()
        return collection

    def _transactions_for_account(
        self,
        *,
        app_customer_id: str,
        account_id: str,
    ):
        # Prefer v2 combined feed — classic /purchases etc. are POST-only (405) on v2.
        feed = self._nessie.list_account_transactions(account_id)
        if feed:
            return collect_from_transaction_feed(
                app_customer_id=app_customer_id,
                account_id=account_id,
                feed=feed,
            )

        purchases = self._nessie.list_purchases(account_id)
        deposits = self._nessie.list_deposits(account_id)
        withdrawals = self._nessie.list_withdrawals(account_id)
        transfers = self._nessie.list_transfers(account_id)
        return collect_account_transactions(
            app_customer_id=app_customer_id,
            account_id=account_id,
            purchases=purchases,
            deposits=deposits,
            withdrawals=withdrawals,
            transfers=transfers,
        )
