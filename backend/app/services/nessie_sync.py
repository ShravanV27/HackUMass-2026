"""Synchronize Nessie read-only data into TigerData."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.db.repository import TigerRepository
from app.services.nessie_client import NessieClient
from app.services.nessie_mapper import collect_account_transactions, map_account


@dataclass(frozen=True)
class SyncResult:
    accounts_upserted: int
    transactions_upserted: int
    nessie_customer_id: str
    app_customer_id: str


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
        if app_customer_id != self._settings.demo_customer_id:
            raise PermissionError(
                "Live sync is only enabled for the authenticated demo customer"
            )
        return self._settings.nessie_customer_id or app_customer_id

    def sync_customer(self, app_customer_id: str) -> SyncResult:
        self._repo.ensure_schema()
        nessie_customer_id = self.resolve_nessie_customer_id(app_customer_id)

        raw_accounts = self._nessie.list_accounts_for_customer(nessie_customer_id)
        if not raw_accounts and not self._settings.nessie_customer_id:
            # Fallback for sandboxes that only expose /accounts.
            raw_accounts = self._nessie.list_all_accounts()

        accounts = [
            map_account(row, app_customer_id=app_customer_id) for row in raw_accounts
        ]
        accounts_upserted = self._repo.upsert_accounts(accounts)

        all_txns = []
        for account in accounts:
            purchases = self._nessie.list_purchases(account.account_id)
            deposits = self._nessie.list_deposits(account.account_id)
            withdrawals = self._nessie.list_withdrawals(account.account_id)
            transfers = self._nessie.list_transfers(account.account_id)
            all_txns.extend(
                collect_account_transactions(
                    app_customer_id=app_customer_id,
                    account_id=account.account_id,
                    purchases=purchases,
                    deposits=deposits,
                    withdrawals=withdrawals,
                    transfers=transfers,
                )
            )
        transactions_upserted = self._repo.upsert_transactions(all_txns)
        return SyncResult(
            accounts_upserted=accounts_upserted,
            transactions_upserted=transactions_upserted,
            nessie_customer_id=nessie_customer_id,
            app_customer_id=app_customer_id,
        )
