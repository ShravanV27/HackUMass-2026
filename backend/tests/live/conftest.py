"""Fixtures for opt-in live integration tests."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.db.connection import get_connection
from app.db.repository import TigerRepository
from app.services.nessie_client import NessieClient
from app.services.nessie_sync import NessieSyncService
from tests.live.helpers import (
    LIVE_TEST_CUSTOMER_ID,
    require_isolated_database_url,
    require_live_sync_settings,
    require_nessie_read_settings,
    with_rate_limit_retries,
)


@pytest.fixture
def nessie_settings() -> Settings:
    return require_nessie_read_settings()


@pytest.fixture
def nessie_client(nessie_settings: Settings) -> NessieClient:
    return NessieClient(nessie_settings, timeout=20.0)


@pytest.fixture
def live_sync_settings() -> Settings:
    return require_live_sync_settings()


@pytest.fixture
def live_test_customer_id() -> str:
    return LIVE_TEST_CUSTOMER_ID


@pytest.fixture
def isolated_db_url() -> str:
    return require_isolated_database_url()


@pytest.fixture
def synced_live_customer(live_sync_settings: Settings, live_test_customer_id: str):
    """Sync Nessie → isolated TigerData for the live-test customer only.

    Cleans only rows owned by ``live-test-customer-001`` before and after.
    Never truncates shared tables.
    """
    with get_connection(live_sync_settings) as conn:
        repo = TigerRepository(conn)
        repo.ensure_schema(require_hypertable=True)
        _delete_customer_rows(conn, live_test_customer_id)

        nessie = NessieClient(live_sync_settings, timeout=20.0)
        result = with_rate_limit_retries(
            lambda: NessieSyncService(
                live_sync_settings, nessie, repo
            ).sync_customer(live_test_customer_id),
            label="nessie_sync",
        )

    yield {
        "settings": live_sync_settings,
        "customer_id": live_test_customer_id,
        "sync_result": result,
    }

    with get_connection(live_sync_settings) as conn:
        _delete_customer_rows(conn, live_test_customer_id)


def _delete_customer_rows(conn, customer_id: str) -> None:
    """Scoped cleanup — only the live-test customer namespace."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM transactions WHERE customer_id = %s",
            (customer_id,),
        )
        cur.execute(
            "DELETE FROM accounts WHERE customer_id = %s",
            (customer_id,),
        )
    conn.commit()
