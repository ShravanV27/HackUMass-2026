"""Optional Postgres round-trip for the customer_id conflict guard.

Skipped unless CENTSABLE_TEST_DATABASE_URL points at a throwaway database.
Never set that variable to a real TigerData / Nessie database.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.db.repository import TigerRepository
from app.models.schemas import Transaction, TransactionKind, TransactionStatus

pytestmark = pytest.mark.skipif(
    not os.environ.get("CENTSABLE_TEST_DATABASE_URL"),
    reason="Optional throwaway Postgres; skipped in the default offline suite.",
)


def test_same_transaction_key_does_not_move_between_customers() -> None:
    import psycopg

    url = os.environ["CENTSABLE_TEST_DATABASE_URL"]
    posted = datetime(2026, 11, 1, tzinfo=timezone.utc)
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS transactions")
            cur.execute(
                """
                CREATE TABLE transactions (
                    transaction_id TEXT NOT NULL,
                    customer_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    amount NUMERIC(18, 2) NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    description TEXT NOT NULL,
                    category TEXT NOT NULL,
                    posted_at TIMESTAMPTZ NOT NULL,
                    counterparty_account_id TEXT NULL,
                    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (transaction_id, posted_at)
                )
                """
            )
        conn.commit()
        repo = TigerRepository(conn)
        row = Transaction(
            transaction_id="purchase:shared",
            customer_id="customer-a",
            account_id="acc-a",
            amount=Decimal("-5.00"),
            kind=TransactionKind.EXPENSE,
            status=TransactionStatus.POSTED,
            description="a",
            category="Purchase",
            posted_at=posted,
        )
        repo.commit_customer_sync(
            customer_id="customer-a",
            accounts=[],
            transactions=[row],
            reconcile=False,
        )
        repo.commit_customer_sync(
            customer_id="customer-b",
            accounts=[],
            transactions=[
                row.model_copy(
                    update={
                        "customer_id": "customer-b",
                        "account_id": "acc-b",
                        "description": "stolen",
                    }
                )
            ],
            reconcile=False,
        )
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT customer_id, account_id, description
                FROM transactions
                WHERE transaction_id = %s AND posted_at = %s
                """,
                ("purchase:shared", posted),
            )
            stored = cur.fetchone()
        assert stored == ("customer-a", "acc-a", "a")
