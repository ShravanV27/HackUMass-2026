"""Synchronize Nessie read-only data into TigerData."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.db.repository import TigerRepository
from app.services.nessie_client import AccountCollection, NessieClient, TransactionFeed
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
    """Map app DEMO_CUSTOMER_ID → bound / env NESSIE customer (fallback to demo id)."""
    if app_customer_id != settings.demo_customer_id:
        raise PermissionError(
            "Live sync is only enabled for the authenticated demo customer"
        )
    from app.services.session_bindings import get_bound_nessie_customer_id

    bound = get_bound_nessie_customer_id(app_customer_id)
    if bound:
        return bound
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
        if not accounts:
            # An empty body is not "this customer has zero accounts". Only an
            # explicit total of 0 may snapshot-delete the stored customer.
            feeds_complete = (
                collection.reported_total == 0 and collection.complete
            )
        else:
            feeds_complete = True
            for account in accounts:
                account_txns, feed_complete = self._transactions_for_account(
                    app_customer_id=app_customer_id,
                    account_id=account.account_id,
                )
                all_txns.extend(account_txns)
                feeds_complete = feeds_complete and feed_complete

        write = self._repo.commit_customer_sync(
            customer_id=app_customer_id,
            accounts=accounts,
            transactions=all_txns,
            reconcile=collection.complete,
            transactions_complete=feeds_complete,
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
    ) -> tuple[list, bool]:
        # Prefer v2 combined feed — classic /purchases etc. are POST-only (405) on v2.
        feed, complete = self._read_transaction_feed(account_id)
        if feed or not complete:
            mapped = (
                collect_from_transaction_feed(
                    app_customer_id=app_customer_id,
                    account_id=account_id,
                    feed=feed,
                )
                if feed
                else []
            )
            return mapped, complete

        purchases = self._nessie.list_purchases(account_id)
        deposits = self._nessie.list_deposits(account_id)
        withdrawals = self._nessie.list_withdrawals(account_id)
        transfers = self._nessie.list_transfers(account_id)
        # Classic /purchases|deposits|withdrawals|transfers GETs are POST-only
        # on v2 (405 → []). That empty fallback is not a complete snapshot.
        return (
            collect_account_transactions(
                app_customer_id=app_customer_id,
                account_id=account_id,
                purchases=purchases,
                deposits=deposits,
                withdrawals=withdrawals,
                transfers=transfers,
            ),
            False,
        )

    def _read_transaction_feed(self, account_id: str) -> tuple[list[dict], bool]:
        """Read the v2 feed. Bare lists (test doubles) are treated as complete."""
        fetcher = getattr(self._nessie, "fetch_account_transactions", None)
        if fetcher is not None:
            fetched = fetcher(account_id)
            if isinstance(fetched, TransactionFeed):
                return list(fetched.items), fetched.complete
            if isinstance(fetched, list):
                return fetched, True
        listed = self._nessie.list_account_transactions(account_id)
        if isinstance(listed, list):
            return listed, True
        return [], False
