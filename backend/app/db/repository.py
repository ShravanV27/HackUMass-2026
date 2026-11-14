"""Idempotent TigerData repository + SQL analytics."""

from __future__ import annotations

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
"""

_SPENDING_BY_CATEGORY = """
SELECT
    category,
    COALESCE(SUM(ABS(amount)), 0) AS total_spent,
    COUNT(*) AS txn_count
FROM transactions
WHERE customer_id = %(customer_id)s
  AND kind = 'expense'
  AND status = 'posted'
  AND posted_at >= %(start_at)s
  AND posted_at < %(end_at)s
GROUP BY category
ORDER BY total_spent DESC
"""

_CASHFLOW_WINDOW = """
SELECT
    COALESCE(SUM(CASE WHEN kind = 'income' AND status = 'posted' THEN amount ELSE 0 END), 0)
        AS income_posted,
    COALESCE(SUM(CASE WHEN kind = 'expense' AND status = 'posted' THEN ABS(amount) ELSE 0 END), 0)
        AS expenses_posted,
    COALESCE(SUM(CASE WHEN kind = 'income' AND status = 'pending' THEN amount ELSE 0 END), 0)
        AS pending_income,
    COALESCE(SUM(CASE WHEN kind = 'expense' AND status = 'pending' THEN ABS(amount) ELSE 0 END), 0)
        AS pending_expenses,
    COALESCE(SUM(CASE WHEN kind = 'transfer' THEN ABS(amount) ELSE 0 END), 0)
        AS transfer_abs_sum,
    COUNT(*) FILTER (WHERE kind = 'transfer') AS transfer_legs
FROM transactions
WHERE customer_id = %(customer_id)s
  AND posted_at >= %(start_at)s
  AND posted_at < %(end_at)s
"""


class TigerRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def ensure_schema(self) -> None:
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        statements = _split_sql(sql)
        table_stmts = [s for s in statements if "create_hypertable" not in s.lower()]
        hyper_stmts = [s for s in statements if "create_hypertable" in s.lower()]
        with self._conn.cursor() as cur:
            for statement in table_stmts:
                cur.execute(statement)
        self._conn.commit()
        # Timescale hypertable is best-effort when the extension is available.
        for statement in hyper_stmts:
            try:
                with self._conn.cursor() as cur:
                    cur.execute(statement)
                self._conn.commit()
            except psycopg.Error:
                self._conn.rollback()

    def upsert_accounts(self, accounts: list[Account]) -> int:
        if not accounts:
            return 0
        with self._conn.cursor() as cur:
            for account in accounts:
                cur.execute(
                    _UPSERT_ACCOUNT,
                    {
                        "account_id": account.account_id,
                        "customer_id": account.customer_id,
                        "name": account.name,
                        "account_type": account.account_type,
                        "balance": account.balance,
                        "currency": account.currency,
                    },
                )
        self._conn.commit()
        return len(accounts)

    def upsert_transactions(self, transactions: list[Transaction]) -> int:
        if not transactions:
            return 0
        with self._conn.cursor() as cur:
            for txn in transactions:
                cur.execute(
                    _UPSERT_TRANSACTION,
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
                    },
                )
        self._conn.commit()
        return len(transactions)

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
