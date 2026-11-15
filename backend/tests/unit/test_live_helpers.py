"""Offline unit tests for live-suite safety helpers (no external calls)."""

from __future__ import annotations

import pytest

from tests.live.helpers import (
    redact_mapping,
    redact_value,
    require_isolated_database_url,
)


def test_redact_hides_secrets_and_shortens_ids() -> None:
    payload = {
        "api_key": "super-secret",
        "Authorization": "Bearer abc",
        "account_id": "acct_01m7djs39b2adt8vfg8ztzpw0f",
        "balance": 12.5,
        "items": [{"_id": "x"}],
    }
    safe = redact_mapping(payload)
    assert safe["api_key"] == "***REDACTED***"
    assert safe["Authorization"] == "***REDACTED***"
    assert "super-secret" not in str(safe)
    assert "acct_01m7djs39b2adt8vfg8ztzpw0f" not in str(safe["account_id"])
    assert safe["balance"] == 12.5
    assert safe["items"] == "list[1]"
    assert redact_value("password", "x") == "***REDACTED***"


def test_isolated_db_skips_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIVE_TEST_DATABASE_URL", raising=False)
    with pytest.raises(pytest.skip.Exception):
        require_isolated_database_url()


def test_live_cleanup_deletes_only_the_test_customer() -> None:
    from unittest.mock import MagicMock

    from tests.live.conftest import _delete_customer_rows
    from tests.live.helpers import LIVE_TEST_CUSTOMER_ID

    refused = MagicMock()
    with pytest.raises(RuntimeError, match=LIVE_TEST_CUSTOMER_ID):
        _delete_customer_rows(refused, "demo-customer-001")
    refused.cursor.assert_not_called()

    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    _delete_customer_rows(conn, LIVE_TEST_CUSTOMER_ID)
    statements = [call.args for call in cursor.execute.call_args_list]
    assert statements == [
        ("DELETE FROM transactions WHERE customer_id = %s", (LIVE_TEST_CUSTOMER_ID,)),
        ("DELETE FROM accounts WHERE customer_id = %s", (LIVE_TEST_CUSTOMER_ID,)),
    ]
    assert "TRUNCATE" not in str(statements).upper()
    conn.commit.assert_called_once()


def test_isolated_db_fails_when_same_as_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "postgresql://localhost:5432/finance"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("LIVE_TEST_DATABASE_URL", url)
    with pytest.raises(pytest.fail.Exception, match="shared DATABASE_URL"):
        require_isolated_database_url()
