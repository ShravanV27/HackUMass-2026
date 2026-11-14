"""Live Nessie → TigerData sync + hypertable / continuous aggregate checks."""

from __future__ import annotations

import pytest

from app.db.connection import get_connection
from app.db.repository import TigerRepository
from app.services.nessie_client import NessieClient
from app.services.nessie_sync import NessieSyncService
from tests.live.helpers import LIVE_TEST_CUSTOMER_ID, with_rate_limit_retries

pytestmark = pytest.mark.live


def test_hypertable_required_and_verified(live_sync_settings) -> None:
    with get_connection(live_sync_settings) as conn:
        repo = TigerRepository(conn)
        result = repo.ensure_schema(require_hypertable=True)
    assert result.hypertable_enabled is True
    # Continuous aggregate is best-effort when Timescale supports it.
    assert isinstance(result.continuous_aggregate_enabled, bool)


def test_sync_maps_customer_and_is_idempotent(synced_live_customer) -> None:
    settings = synced_live_customer["settings"]
    customer_id = synced_live_customer["customer_id"]
    first = synced_live_customer["sync_result"]

    assert first.app_customer_id == LIVE_TEST_CUSTOMER_ID
    assert first.nessie_customer_id == settings.nessie_customer_id
    assert first.accounts_upserted >= 0

    with get_connection(settings) as conn:
        repo = TigerRepository(conn)
        accounts = repo.list_accounts(customer_id)
        txns = repo.list_transactions(customer_id)

        # All persisted rows must use the app customer id, not the Nessie id.
        assert all(a.customer_id == customer_id for a in accounts)
        assert all(t.customer_id == customer_id for t in txns)

        account_count = len(accounts)
        txn_count = len(txns)

        # Second sync must not create duplicates (upsert by primary key).
        nessie = NessieClient(settings, timeout=20.0)
        second = with_rate_limit_retries(
            lambda: NessieSyncService(settings, nessie, repo).sync_customer(
                customer_id
            ),
            label="nessie_sync_repeat",
        )
        accounts_after = repo.list_accounts(customer_id)
        txns_after = repo.list_transactions(customer_id)

    assert len(accounts_after) == account_count
    assert len(txns_after) == txn_count
    assert second.accounts_upserted == first.accounts_upserted
    # Transaction upsert count may equal the source size each run; row count stays flat.
    assert len({t.transaction_id for t in txns_after}) == len(txns_after)


def test_continuous_aggregate_or_sql_daily_spending(synced_live_customer) -> None:
    settings = synced_live_customer["settings"]
    customer_id = synced_live_customer["customer_id"]

    from datetime import datetime, timedelta, timezone

    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=90)

    with get_connection(settings) as conn:
        repo = TigerRepository(conn)
        schema = repo.ensure_schema(require_hypertable=True)
        daily = repo.daily_spending(
            customer_id, start_at=start, end_at=end
        )

    assert isinstance(daily, list)
    for row in daily:
        assert "day" in row
        assert "total_spent" in row
        assert "source" in row
        assert row["source"] in {"sql", "continuous_aggregate"}

    if schema.continuous_aggregate_enabled:
        # When cagg exists, prefer it (may still fall back if refresh fails).
        assert schema.hypertable_enabled is True
    else:
        # Isolation DB may lack cagg support — SQL path must still work.
        assert all(row["source"] == "sql" for row in daily) or daily == []
