"""Offline unit tests for live-suite safety helpers (no external calls)."""

from __future__ import annotations

import pytest

from tests.live.helpers import (
    CONFIRM_DESTRUCTIVE,
    redact_mapping,
    redact_value,
    require_destructive_nessie_key,
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


def test_isolated_db_fails_when_same_as_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "postgresql://localhost:5432/finance"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("LIVE_TEST_DATABASE_URL", url)
    monkeypatch.delenv("LIVE_TEST_ALLOW_SHARED_DB", raising=False)
    with pytest.raises(pytest.fail.Exception, match="shared DATABASE_URL"):
        require_isolated_database_url()


def test_isolated_db_allows_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "postgresql://localhost:5432/finance"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("LIVE_TEST_DATABASE_URL", url)
    monkeypatch.setenv("LIVE_TEST_ALLOW_SHARED_DB", "confirm")
    assert require_isolated_database_url() == url


def test_destructive_key_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONFIRM_NESSIE_DESTRUCTIVE", raising=False)
    monkeypatch.delenv("NESSIE_DESTRUCTIVE_API_KEY", raising=False)
    with pytest.raises(pytest.fail.Exception, match="CONFIRM_NESSIE_DESTRUCTIVE"):
        require_destructive_nessie_key()

    monkeypatch.setenv("CONFIRM_NESSIE_DESTRUCTIVE", CONFIRM_DESTRUCTIVE)
    with pytest.raises(pytest.fail.Exception, match="NESSIE_DESTRUCTIVE_API_KEY"):
        require_destructive_nessie_key()

    monkeypatch.setenv("NESSIE_API_KEY", "daily-key")
    monkeypatch.setenv("NESSIE_DESTRUCTIVE_API_KEY", "daily-key")
    with pytest.raises(pytest.fail.Exception, match="must differ"):
        require_destructive_nessie_key()

    monkeypatch.setenv("NESSIE_DESTRUCTIVE_API_KEY", "dedicated-key")
    assert require_destructive_nessie_key() == "dedicated-key"
