"""Idempotent TigerData repository + SQL analytics."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from app.models.schemas import (
    Account,
    Transaction,
    TransactionKind,
    TransactionStatus,
)

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_PROCESS_SCHEMA_LOCK = threading.Lock()
_PROCESS_SCHEMA_RESULT: SchemaEnsureResult | None = None

_UPSERT_ACCOUNT = """
INSERT INTO accounts (
    account_id, customer_id, name, account_type, balance, currency, updated_at
) VALUES (
    %(account_id)s, %(customer_id)s, %(name)s, %(account_type)s,
    %(balance)s, %(currency)s, NOW()
)
ON CONFLICT (account_id) DO UPDATE SET
    customer_id = EXCLUDED.customer_id,
    name = EXCLUDED.name,
    account_type = EXCLUDED.account_type,
    balance = EXCLUDED.balance,
    currency = EXCLUDED.currency,
    updated_at = NOW()
"""

_UPSERT_TRANSACTION = """
INSERT INTO transactions (
    transaction_id, customer_id, account_id, amount, kind, status,
    description, category, posted_at, counterparty_account_id, ingested_at
) VALUES (
    %(transaction_id)s, %(customer_id)s, %(account_id)s, %(amount)s, %(kind)s,
    %(status)s, %(description)s, %(category)s, %(posted_at)s,
    %(counterparty_account_id)s, NOW()
)
ON CONFLICT (transaction_id, posted_at) DO UPDATE SET
    customer_id = EXCLUDED.customer_id,
    account_id = EXCLUDED.account_id,
    amount = EXCLUDED.amount,
    kind = EXCLUDED.kind,
    status = EXCLUDED.status,
    description = EXCLUDED.description,
    category = EXCLUDED.category,
    counterparty_account_id = EXCLUDED.counterparty_account_id,
    ingested_at = NOW()
WHERE transactions.customer_id = EXCLUDED.customer_id
"""

_SPENDING_BY_CATEGORY = """
SELECT
    t.category,
    COALESCE(SUM(ABS(t.amount)), 0) AS total_spent,
    COUNT(*) AS txn_count
FROM transactions AS t
LEFT JOIN accounts AS a
  ON a.account_id = t.account_id
 AND a.customer_id = t.customer_id
WHERE t.customer_id = %(customer_id)s
  AND t.kind = 'expense'
  AND t.status = 'posted'
  AND t.posted_at >= %(start_at)s
  AND t.posted_at < %(end_at)s
  AND (
        a.account_id IS NULL
        OR position('credit' in lower(a.account_type)) = 0
      )
GROUP BY t.category
ORDER BY total_spent DESC
"""

_CASHFLOW_WINDOW = """
SELECT
    COALESCE(SUM(CASE WHEN kind = 'income' AND status = 'posted' THEN amount ELSE 0 END), 0)
        AS income_posted,
    COALESCE(SUM(CASE
        WHEN kind = 'expense' AND status = 'posted'
         AND NOT EXISTS (
             SELECT 1 FROM accounts AS a
             WHERE a.account_id = transactions.account_id
               AND a.customer_id = transactions.customer_id
               AND position('credit' in lower(a.account_type)) > 0
         )
        THEN ABS(amount) ELSE 0 END), 0)
        AS expenses_posted,
    COALESCE(SUM(CASE WHEN kind = 'income' AND status = 'pending' THEN amount ELSE 0 END), 0)
        AS pending_income,
    COALESCE(SUM(CASE
        WHEN kind = 'expense' AND status = 'pending'
         AND NOT EXISTS (
             SELECT 1 FROM accounts AS a
             WHERE a.account_id = transactions.account_id
               AND a.customer_id = transactions.customer_id
               AND position('credit' in lower(a.account_type)) > 0
         )
        THEN ABS(amount) ELSE 0 END), 0)
        AS pending_expenses,
    COALESCE(SUM(CASE WHEN kind = 'transfer' THEN ABS(amount) ELSE 0 END), 0)
        AS transfer_abs_sum,
    COUNT(*) FILTER (WHERE kind = 'transfer') AS transfer_legs
FROM transactions
WHERE customer_id = %(customer_id)s
  AND posted_at >= %(start_at)s
  AND posted_at < %(end_at)s
"""

_DAILY_SPENDING_SQL = """
SELECT
    date_trunc('day', t.posted_at AT TIME ZONE 'UTC')::date AS day,
    COALESCE(SUM(ABS(t.amount)), 0) AS total_spent,
    COUNT(*) AS txn_count
FROM transactions AS t
LEFT JOIN accounts AS a
  ON a.account_id = t.account_id
 AND a.customer_id = t.customer_id
WHERE t.customer_id = %(customer_id)s
  AND t.kind = 'expense'
  AND t.status = 'posted'
  AND t.posted_at >= %(start_at)s
  AND t.posted_at < %(end_at)s
  AND (
        a.account_id IS NULL
        OR position('credit' in lower(a.account_type)) = 0
      )
GROUP BY 1
ORDER BY 1
"""

_DAILY_SPENDING_CAGG_SQL = """
SELECT
    day::date AS day,
    COALESCE(SUM(total_spent), 0) AS total_spent,
    COALESCE(SUM(txn_count), 0)::bigint AS txn_count
FROM daily_spending
WHERE customer_id = %(customer_id)s
  AND day >= %(start_at)s
  AND day < %(end_at)s
GROUP BY 1
ORDER BY 1
"""

_CREATE_DAILY_SPENDING_CAGG = """
CREATE MATERIALIZED VIEW IF NOT EXISTS daily_spending
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', posted_at) AS day,
    customer_id,
    SUM(ABS(amount)) AS total_spent,
    COUNT(*)::bigint AS txn_count
FROM transactions
WHERE kind = 'expense'
  AND status = 'posted'
GROUP BY 1, 2
WITH NO DATA
"""

_CREATE_WEEKLY_SPENDING_CAGG = """
CREATE MATERIALIZED VIEW IF NOT EXISTS weekly_spending
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('7 days', posted_at) AS week_start,
    customer_id,
    SUM(ABS(amount)) AS total_spent,
    COUNT(*)::bigint AS txn_count
FROM transactions
WHERE kind = 'expense'
  AND status = 'posted'
GROUP BY 1, 2
WITH NO DATA
"""

_DISCRETIONARY_DAILY_SQL = """
SELECT
    date_trunc('day', t.posted_at AT TIME ZONE 'UTC')::date AS day,
    COALESCE(SUM(ABS(t.amount)), 0) AS total_spent,
    COUNT(*) AS txn_count
FROM transactions AS t
LEFT JOIN accounts AS a
  ON a.account_id = t.account_id
 AND a.customer_id = t.customer_id
WHERE t.customer_id = %(customer_id)s
  AND t.kind = 'expense'
  AND t.status = 'posted'
  AND t.posted_at >= %(start_at)s
  AND t.posted_at < %(end_at)s
  AND NOT (lower(t.category) = ANY(%(exclude_categories)s))
  AND NOT EXISTS (
        SELECT 1
        FROM unnest(%(exclude_description_tokens)s::text[]) AS token(value)
        WHERE token.value <> ''
          AND position(token.value in lower(COALESCE(t.description, ''))) > 0
      )
  AND (
        a.account_id IS NULL
        OR position('credit' in lower(a.account_type)) = 0
      )
GROUP BY 1
ORDER BY 1
"""

_WEEKLY_SPENDING_SQL = """
SELECT
    date_trunc('week', posted_at AT TIME ZONE 'UTC')::date AS week_start,
    COALESCE(SUM(ABS(amount)), 0) AS total_spent,
    COUNT(*) AS txn_count
FROM transactions
WHERE customer_id = %(customer_id)s
  AND kind = 'expense'
  AND status = 'posted'
  AND posted_at >= %(start_at)s
  AND posted_at < %(end_at)s
GROUP BY 1
ORDER BY 1
"""

_WEEKLY_SPENDING_CAGG_SQL = """
SELECT
    week_start::date AS week_start,
    COALESCE(SUM(total_spent), 0) AS total_spent,
    COALESCE(SUM(txn_count), 0)::bigint AS txn_count
FROM weekly_spending
WHERE customer_id = %(customer_id)s
  AND week_start >= %(start_at)s
  AND week_start < %(end_at)s
GROUP BY 1
ORDER BY 1
"""

_HYPERTABLE_CHECK = """
SELECT 1
FROM timescaledb_information.hypertables
WHERE hypertable_name = 'transactions'
LIMIT 1
"""


class TigerSchemaError(RuntimeError):
    """Raised when required TigerData/Timescale features are unavailable."""


@dataclass(frozen=True)
class SchemaEnsureResult:
    hypertable_enabled: bool
    continuous_aggregate_enabled: bool


@dataclass(frozen=True)
class CustomerSyncWriteResult:
    accounts_upserted: int
    transactions_upserted: int
    accounts_deleted: int
    transactions_deleted: int
    reconciled: bool


class TigerRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn
        self.last_schema_result: SchemaEnsureResult | None = None

    def ensure_schema(self, *, require_hypertable: bool = False) -> SchemaEnsureResult:
        global _PROCESS_SCHEMA_RESULT

        cached = _PROCESS_SCHEMA_RESULT
        if cached is not None:
            if require_hypertable and not cached.hypertable_enabled:
                raise TigerSchemaError(
                    "TigerData hypertable for transactions is required in live mode "
                    "but is not enabled."
                )
            self.last_schema_result = cached
            return cached

        with _PROCESS_SCHEMA_LOCK:
            cached = _PROCESS_SCHEMA_RESULT
            if cached is not None:
                if require_hypertable and not cached.hypertable_enabled:
                    raise TigerSchemaError(
                        "TigerData hypertable for transactions is required in live mode "
                        "but is not enabled."
                    )
                self.last_schema_result = cached
                return cached

            result = self._ensure_schema_uncached(require_hypertable=require_hypertable)
            _PROCESS_SCHEMA_RESULT = result
            self.last_schema_result = result
            return result

    def _ensure_schema_uncached(
        self, *, require_hypertable: bool
    ) -> SchemaEnsureResult:
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        statements = _split_sql(sql)
        table_stmts = [s for s in statements if "create_hypertable" not in s.lower()]
        hyper_stmts = [s for s in statements if "create_hypertable" in s.lower()]
        with self._conn.cursor() as cur:
            for statement in table_stmts:
                cur.execute(statement)
        self._conn.commit()

        hypertable_enabled = False
        hypertable_error: str | None = None
        for statement in hyper_stmts:
            try:
                with self._conn.cursor() as cur:
                    cur.execute(statement)
                self._conn.commit()
            except psycopg.Error as exc:
                self._conn.rollback()
                hypertable_error = str(exc)
        hypertable_enabled = self._transactions_is_hypertable()
        if require_hypertable and not hypertable_enabled:
            detail = hypertable_error or "create_hypertable did not register transactions"
            raise TigerSchemaError(
                "TigerData hypertable for transactions is required in live mode "
                f"but is not enabled ({detail}). "
                "Confirm TimescaleDB/TigerData is available on DATABASE_URL."
            )

        cagg_enabled = False
        if hypertable_enabled:
            daily_ok = self._ensure_daily_spending_cagg()
            weekly_ok = self._ensure_weekly_spending_cagg()
            cagg_enabled = daily_ok or weekly_ok

        return SchemaEnsureResult(
            hypertable_enabled=hypertable_enabled,
            continuous_aggregate_enabled=cagg_enabled,
        )

    def reset_app_tables(self) -> None:
        """Drop and recreate HackUMass app tables only (accounts/transactions/cagg).

        Does not touch unrelated schemas or databases. Safe for disposable
        hackathon demo databases pointed at by DATABASE_URL.
        """
        global _PROCESS_SCHEMA_RESULT
        with self._conn.cursor() as cur:
            cur.execute("DROP MATERIALIZED VIEW IF EXISTS weekly_spending CASCADE")
            cur.execute("DROP MATERIALIZED VIEW IF EXISTS daily_spending CASCADE")
            cur.execute("DROP TABLE IF EXISTS customer_settings CASCADE")
            cur.execute("DROP TABLE IF EXISTS transactions CASCADE")
            cur.execute("DROP TABLE IF EXISTS accounts CASCADE")
        self._conn.commit()
        with _PROCESS_SCHEMA_LOCK:
            _PROCESS_SCHEMA_RESULT = None
        self.ensure_schema(require_hypertable=True)

    def upsert_accounts(self, accounts: list[Account]) -> int:
        if not accounts:
            return 0
        with self._conn.cursor() as cur:
            self._upsert_accounts_cursor(cur, accounts)
        self._conn.commit()
        return len(accounts)

    def upsert_transactions(self, transactions: list[Transaction]) -> int:
        if not transactions:
            return 0
        with self._conn.cursor() as cur:
            self._upsert_transactions_cursor(cur, transactions)
        self._conn.commit()
        return len(transactions)

    def commit_customer_sync(
        self,
        *,
        customer_id: str,
        accounts: list[Account],
        transactions: list[Transaction],
        reconcile: bool,
        transactions_complete: bool = False,
    ) -> CustomerSyncWriteResult:
        """Atomically upsert the Nessie snapshot and optionally prune orphans.

        Orphan deletes are scoped to ``customer_id`` only and run only when
        ``reconcile`` is True (caller must certify the Nessie account fetch was
        complete) and ``transactions_complete`` is True. The default is False:
        a caller that forgets the flag must not snapshot-delete. An incomplete
        feed deletes nothing, including when the kept account set is empty.
        """
        keep_ids = {account.account_id for account in accounts}
        transactions = _dedupe_transactions(transactions)
        with self._conn.transaction():
            with self._conn.cursor() as cur:
                self._upsert_accounts_cursor(cur, accounts)
                self._upsert_transactions_cursor(cur, transactions)
                accounts_deleted = 0
                transactions_deleted = 0
                if reconcile:
                    transactions_deleted, accounts_deleted = (
                        self._delete_stale_for_customer_cursor(
                            cur,
                            customer_id=customer_id,
                            keep_account_ids=keep_ids,
                            transactions=transactions,
                            transactions_complete=transactions_complete,
                        )
                    )
        return CustomerSyncWriteResult(
            accounts_upserted=len(accounts),
            transactions_upserted=len(transactions),
            accounts_deleted=accounts_deleted,
            transactions_deleted=transactions_deleted,
            reconciled=reconcile,
        )

    def _upsert_accounts_cursor(self, cur: Any, accounts: list[Account]) -> None:
        if not accounts:
            return
        cur.executemany(
            _UPSERT_ACCOUNT,
            [
                {
                    "account_id": account.account_id,
                    "customer_id": account.customer_id,
                    "name": account.name,
                    "account_type": account.account_type,
                    "balance": account.balance,
                    "currency": account.currency,
                }
                for account in accounts
            ],
        )

    def _upsert_transactions_cursor(
        self, cur: Any, transactions: list[Transaction]
    ) -> None:
        if not transactions:
            return
        # Batch upsert — row-by-row over WAN TigerData was ~12s for ~500 txns
        # and caused iOS 15s timeouts under concurrent prediction requests.
        cur.executemany(
            _UPSERT_TRANSACTION,
            [
                {
                    "transaction_id": txn.transaction_id,
                    "customer_id": txn.customer_id,
                    "account_id": txn.account_id,
                    "amount": txn.amount,
                    "kind": txn.kind.value,
                    "status": txn.status.value,
                    "description": txn.description,
                    "category": txn.category,
                    "posted_at": txn.posted_at,
                    "counterparty_account_id": txn.counterparty_account_id,
                }
                for txn in transactions
            ],
        )

    def _delete_stale_for_customer_cursor(
        self,
        cur: Any,
        *,
        customer_id: str,
        keep_account_ids: set[str],
        transactions: list[Transaction] | None = None,
        transactions_complete: bool = False,
    ) -> tuple[int, int]:
        """Delete stale transactions then accounts for one customer only.

        When the transaction feed is complete, any row whose
        ``(transaction_id, posted_at)`` is absent from the snapshot is removed,
        including duplicates left by an unstable ``posted_at``. A partial feed
        deletes nothing: an empty keep-set must not wipe the customer, and a
        non-empty keep-set must not drop accounts whose transactions were not
        fully fetched.
        """
        if not transactions_complete:
            return 0, 0
        keep = list(keep_account_ids)
        snapshot = list(transactions or [])
        if snapshot:
            cur.execute(
                """
                DELETE FROM transactions AS t
                WHERE t.customer_id = %s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM unnest(%s::text[], %s::timestamptz[])
                          AS kept(transaction_id, posted_at)
                      WHERE kept.transaction_id = t.transaction_id
                            AND kept.posted_at = t.posted_at
                  )
                """,
                (
                    customer_id,
                    [txn.transaction_id for txn in snapshot],
                    [txn.posted_at for txn in snapshot],
                ),
            )
        else:
            cur.execute(
                "DELETE FROM transactions WHERE customer_id = %s",
                (customer_id,),
            )
        transactions_deleted = cur.rowcount or 0

        if keep:
            cur.execute(
                """
                DELETE FROM accounts
                WHERE customer_id = %s
                  AND account_id <> ALL(%s)
                """,
                (customer_id, keep),
            )
            accounts_deleted = cur.rowcount or 0
        else:
            cur.execute(
                "DELETE FROM accounts WHERE customer_id = %s",
                (customer_id,),
            )
            accounts_deleted = cur.rowcount or 0
        return transactions_deleted, accounts_deleted

    def list_accounts(self, customer_id: str) -> list[Account]:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT account_id, customer_id, name, account_type, balance, currency
                FROM accounts
                WHERE customer_id = %s
                ORDER BY account_id
                """,
                (customer_id,),
            )
            rows = cur.fetchall()
        return [Account.model_validate(row) for row in rows]

    def list_transactions(
        self,
        customer_id: str,
        account_id: str | None = None,
    ) -> list[Transaction]:
        sql = """
            SELECT transaction_id, customer_id, account_id, amount, kind, status,
                   description, category, posted_at, counterparty_account_id
            FROM transactions
            WHERE customer_id = %s
        """
        params: list[Any] = [customer_id]
        if account_id is not None:
            sql += " AND account_id = %s"
            params.append(account_id)
        sql += " ORDER BY posted_at DESC, transaction_id"
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [
            Transaction(
                transaction_id=row["transaction_id"],
                customer_id=row["customer_id"],
                account_id=row["account_id"],
                amount=Decimal(str(row["amount"])),
                kind=TransactionKind(row["kind"]),
                status=TransactionStatus(row["status"]),
                description=row["description"],
                category=row["category"],
                posted_at=row["posted_at"],
                counterparty_account_id=row["counterparty_account_id"],
            )
            for row in rows
        ]

    def spending_by_category(
        self,
        customer_id: str,
        *,
        start_at: datetime,
        end_at: datetime,
    ) -> list[dict[str, Any]]:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                _SPENDING_BY_CATEGORY,
                {
                    "customer_id": customer_id,
                    "start_at": start_at,
                    "end_at": end_at,
                },
            )
            rows = cur.fetchall()
        return [
            {
                "category": row["category"],
                "total_spent": Decimal(str(row["total_spent"])),
                "txn_count": int(row["txn_count"]),
            }
            for row in rows
        ]

    def cashflow_in_window(
        self,
        customer_id: str,
        *,
        start_at: datetime,
        end_at: datetime,
    ) -> dict[str, Decimal]:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                _CASHFLOW_WINDOW,
                {
                    "customer_id": customer_id,
                    "start_at": start_at,
                    "end_at": end_at,
                },
            )
            row = cur.fetchone() or {}
        income = Decimal(str(row.get("income_posted") or 0))
        expenses = Decimal(str(row.get("expenses_posted") or 0))
        pending_income = Decimal(str(row.get("pending_income") or 0))
        pending_expenses = Decimal(str(row.get("pending_expenses") or 0))
        transfer_abs = Decimal(str(row.get("transfer_abs_sum") or 0))
        transfer_legs = int(row.get("transfer_legs") or 0)
        if transfer_legs >= 2 and transfer_legs % 2 == 0:
            transfer_volume = (transfer_abs / 2).quantize(Decimal("0.01"))
        else:
            transfer_volume = transfer_abs.quantize(Decimal("0.01"))
        return {
            "income_posted": income.quantize(Decimal("0.01")),
            "expenses_posted": expenses.quantize(Decimal("0.01")),
            "pending_income": pending_income.quantize(Decimal("0.01")),
            "pending_expenses": pending_expenses.quantize(Decimal("0.01")),
            "transfer_volume": transfer_volume,
            "net_posted": (income - expenses).quantize(Decimal("0.01")),
            "net_including_pending": (
                (income + pending_income) - (expenses + pending_expenses)
            ).quantize(Decimal("0.01")),
        }

    def daily_spending(
        self,
        customer_id: str,
        *,
        start_at: datetime,
        end_at: datetime,
        prefer_continuous_aggregate: bool = True,
    ) -> list[dict[str, Any]]:
        """SQL time-window daily spending (cagg when available, else date_trunc)."""
        params = {
            "customer_id": customer_id,
            "start_at": start_at,
            "end_at": end_at,
        }
        if prefer_continuous_aggregate and self._daily_spending_cagg_exists():
            try:
                self._refresh_daily_spending(start_at=start_at, end_at=end_at)
                with self._conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(_DAILY_SPENDING_CAGG_SQL, params)
                    rows = cur.fetchall()
                return [
                    {
                        "day": row["day"],
                        "total_spent": Decimal(str(row["total_spent"])),
                        "txn_count": int(row["txn_count"]),
                        "source": "continuous_aggregate",
                    }
                    for row in rows
                ]
            except psycopg.Error:
                self._conn.rollback()

        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_DAILY_SPENDING_SQL, params)
            rows = cur.fetchall()
        return [
            {
                "day": row["day"],
                "total_spent": Decimal(str(row["total_spent"])),
                "txn_count": int(row["txn_count"]),
                "source": "sql",
            }
            for row in rows
        ]

    def _transactions_is_hypertable(self) -> bool:
        try:
            with self._conn.cursor() as cur:
                cur.execute(_HYPERTABLE_CHECK)
                found = cur.fetchone() is not None
            return found
        except psycopg.Error:
            self._conn.rollback()
            return False

    def _daily_spending_cagg_exists(self) -> bool:
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1
                    FROM timescaledb_information.continuous_aggregates
                    WHERE view_name = 'daily_spending'
                    LIMIT 1
                    """
                )
                found = cur.fetchone() is not None
            return found
        except psycopg.Error:
            self._conn.rollback()
            return False

    def _ensure_daily_spending_cagg(self) -> bool:
        if self._daily_spending_cagg_exists():
            return True
        try:
            with self._conn.cursor() as cur:
                cur.execute(_CREATE_DAILY_SPENDING_CAGG)
            self._conn.commit()
            return self._daily_spending_cagg_exists()
        except psycopg.Error:
            self._conn.rollback()
            return False

    def _weekly_spending_cagg_exists(self) -> bool:
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1
                    FROM timescaledb_information.continuous_aggregates
                    WHERE view_name = 'weekly_spending'
                    LIMIT 1
                    """
                )
                found = cur.fetchone() is not None
            return found
        except psycopg.Error:
            self._conn.rollback()
            return False

    def _ensure_weekly_spending_cagg(self) -> bool:
        if self._weekly_spending_cagg_exists():
            return True
        try:
            with self._conn.cursor() as cur:
                cur.execute(_CREATE_WEEKLY_SPENDING_CAGG)
            self._conn.commit()
            return self._weekly_spending_cagg_exists()
        except psycopg.Error:
            self._conn.rollback()
            return False

    def discretionary_daily_spending(
        self,
        customer_id: str,
        *,
        start_at: datetime,
        end_at: datetime,
        exclude_categories: list[str] | None = None,
        exclude_description_tokens: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Posted expense totals by day, excluding bill-like categories.

        Uses indexed SQL (not cagg) so category filters stay accurate for the
        PredictiveEngine weekday model without an extra full-table scan in Python
        when the caller prefers TigerData. Category and bill-name checks are
        case-insensitive, matching the in-memory discretionary filter.
        """
        params = {
            "customer_id": customer_id,
            "start_at": start_at,
            "end_at": end_at,
            "exclude_categories": [
                category.casefold() for category in (exclude_categories or []) if category
            ],
            "exclude_description_tokens": [
                token.strip().lower()
                for token in (exclude_description_tokens or [])
                if token and token.strip()
            ],
        }
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_DISCRETIONARY_DAILY_SQL, params)
            rows = cur.fetchall()
        return [
            {
                "day": row["day"],
                "total_spent": Decimal(str(row["total_spent"])),
                "txn_count": int(row["txn_count"]),
                "source": "sql",
            }
            for row in rows
        ]

    def weekly_spending(
        self,
        customer_id: str,
        *,
        start_at: datetime,
        end_at: datetime,
        prefer_continuous_aggregate: bool = True,
    ) -> list[dict[str, Any]]:
        """Week-bucketed spending (cagg when available)."""
        params = {
            "customer_id": customer_id,
            "start_at": start_at,
            "end_at": end_at,
        }
        if prefer_continuous_aggregate and self._weekly_spending_cagg_exists():
            try:
                self._refresh_weekly_spending(start_at=start_at, end_at=end_at)
                with self._conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(_WEEKLY_SPENDING_CAGG_SQL, params)
                    rows = cur.fetchall()
                return [
                    {
                        "week_start": row["week_start"],
                        "total_spent": Decimal(str(row["total_spent"])),
                        "txn_count": int(row["txn_count"]),
                        "source": "continuous_aggregate",
                    }
                    for row in rows
                ]
            except psycopg.Error:
                self._conn.rollback()

        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_WEEKLY_SPENDING_SQL, params)
            rows = cur.fetchall()
        return [
            {
                "week_start": row["week_start"],
                "total_spent": Decimal(str(row["total_spent"])),
                "txn_count": int(row["txn_count"]),
                "source": "sql",
            }
            for row in rows
        ]

    def _refresh_daily_spending(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                CALL refresh_continuous_aggregate(
                    'daily_spending',
                    %(start_at)s,
                    %(end_at)s
                )
                """,
                {"start_at": start_at, "end_at": end_at},
            )
        self._conn.commit()

    def _refresh_weekly_spending(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                CALL refresh_continuous_aggregate(
                    'weekly_spending',
                    %(start_at)s,
                    %(end_at)s
                )
                """,
                {"start_at": start_at, "end_at": end_at},
            )
        self._conn.commit()


def _dedupe_transactions(transactions: list[Transaction]) -> list[Transaction]:
    """Collapse duplicate Nessie ids. Last row wins so re-sync stays one PK."""
    by_id: dict[str, Transaction] = {}
    for txn in transactions:
        by_id[txn.transaction_id] = txn
    return list(by_id.values())


def _split_sql(sql: str) -> list[str]:
    parts: list[str] = []
    buffer: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            parts.append("\n".join(buffer).strip().rstrip(";"))
            buffer = []
    if buffer:
        parts.append("\n".join(buffer).strip().rstrip(";"))
    return [p for p in parts if p]
