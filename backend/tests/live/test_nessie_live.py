"""Live Nessie v2 read-only integration tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.config import Settings
from app.services.nessie_client import NessieClient, NessieClientError, _as_list
from app.services.nessie_mapper import (
    collect_from_transaction_feed,
    collect_scheduled_bills,
    map_account,
)
from tests.live.helpers import (
    LIVE_TEST_CUSTOMER_ID,
    assert_money_quantized,
    redact_mapping,
    summarize_rows,
    with_rate_limit_retries,
)

pytestmark = pytest.mark.live


def test_nessie_auth_and_customer_accounts(
    nessie_client: NessieClient, nessie_settings: Settings
) -> None:
    customer_id = nessie_settings.nessie_customer_id
    assert customer_id

    rows = with_rate_limit_retries(
        lambda: nessie_client.list_accounts_for_customer(customer_id),
        label="list_accounts",
    )
    # Safe summary only — never dump balances/ids to stdout.
    assert summarize_rows(rows, label="accounts")
    assert isinstance(rows, list)

    if not rows:
        pytest.skip("Nessie customer has no accounts in sandbox")

    sample = rows[0]
    assert isinstance(sample, dict)
    # Envelope must already be unwrapped.
    assert "items" not in sample
    mapped = map_account(sample, app_customer_id=LIVE_TEST_CUSTOMER_ID)
    assert mapped.customer_id == LIVE_TEST_CUSTOMER_ID
    assert mapped.account_id
    assert_money_quantized(mapped.balance)
    safe = redact_mapping({**sample, "api_key": "should-hide"})
    assert safe["api_key"] == "***REDACTED***"
    assert "should-hide" not in str(safe)


def test_nessie_transactions_feed_and_mapping(
    nessie_client: NessieClient, nessie_settings: Settings
) -> None:
    customer_id = nessie_settings.nessie_customer_id
    assert customer_id
    accounts = with_rate_limit_retries(
        lambda: nessie_client.list_accounts_for_customer(customer_id),
        label="list_accounts",
    )
    if not accounts:
        pytest.skip("No accounts for transaction feed test")

    account = map_account(accounts[0], app_customer_id=LIVE_TEST_CUSTOMER_ID)
    feed = with_rate_limit_retries(
        lambda: nessie_client.list_account_transactions(account.account_id),
        label="list_transactions",
    )
    assert isinstance(feed, list)
    if not feed:
        pytest.skip("Account has no transactions")

    txns = collect_from_transaction_feed(
        app_customer_id=LIVE_TEST_CUSTOMER_ID,
        account_id=account.account_id,
        feed=feed[:25],  # bound request processing
    )
    assert len(txns) == min(len(feed), 25)
    for txn in txns:
        assert txn.customer_id == LIVE_TEST_CUSTOMER_ID
        assert txn.transaction_id
        assert_money_quantized(txn.amount)
        assert txn.kind.value in {"income", "expense", "transfer"}
        assert txn.status.value in {"posted", "pending"}


def test_nessie_cents_fields_map_to_dollars_when_present(
    nessie_client: NessieClient, nessie_settings: Settings
) -> None:
    customer_id = nessie_settings.nessie_customer_id
    assert customer_id
    accounts = with_rate_limit_retries(
        lambda: nessie_client.list_accounts_for_customer(customer_id),
        label="list_accounts",
    )
    if not accounts:
        pytest.skip("No accounts")

    cents_accounts = [
        row
        for row in accounts
        if any(
            k in row and row[k] is not None
            for k in ("balance_cents", "current_balance_cents", "available_balance_cents")
        )
    ]
    if not cents_accounts:
        pytest.skip("Sandbox accounts do not expose *_cents balance fields")

    mapped = map_account(cents_accounts[0], app_customer_id=LIVE_TEST_CUSTOMER_ID)
    assert_money_quantized(mapped.balance)
    # Sanity: cents/100 should match mapper output when dollar field absent.
    raw = cents_accounts[0]
    if "balance" not in raw or raw.get("balance") is None:
        cents = Decimal(
            str(
                raw.get("balance_cents")
                or raw.get("current_balance_cents")
                or raw.get("available_balance_cents")
            )
        )
        expected = (cents / Decimal("100")).quantize(Decimal("0.01"))
        # Credit cards may be negated by mapper.
        assert abs(mapped.balance) == expected or mapped.balance == expected


def test_nessie_bills_retrieval_and_mapping(
    nessie_client: NessieClient, nessie_settings: Settings
) -> None:
    customer_id = nessie_settings.nessie_customer_id
    assert customer_id
    raw = with_rate_limit_retries(
        lambda: nessie_client.list_bills_for_customer(customer_id),
        label="list_bills",
    )
    assert isinstance(raw, list)
    bills = collect_scheduled_bills(
        raw, app_customer_id=LIVE_TEST_CUSTOMER_ID
    )
    for bill in bills:
        assert bill.customer_id == LIVE_TEST_CUSTOMER_ID
        assert bill.bill_id.startswith("bill:")
        assert bill.amount > 0
        assert_money_quantized(bill.amount)


def test_nessie_items_envelope_and_pagination_metadata(
    nessie_client: NessieClient, nessie_settings: Settings
) -> None:
    """Verify items unwrap; tolerate pagination metadata without following writes."""
    customer_id = nessie_settings.nessie_customer_id
    assert customer_id

    # Hit the low-level GET once to inspect envelope shape safely.
    payload = with_rate_limit_retries(
        lambda: nessie_client._get(f"/customers/{customer_id}/accounts"),  # noqa: SLF001
        label="accounts_raw",
    )
    rows = _as_list(payload)
    assert isinstance(rows, list)

    if isinstance(payload, dict):
        # Common Nessie pagination / collection metadata keys — presence is OK.
        meta_keys = {"items", "total", "count", "next", "offset", "limit", "page"}
        present = meta_keys.intersection(payload.keys())
        # If paginated, we at least unwrap items without treating envelope as a row.
        if "items" in present:
            assert all("items" not in row for row in rows if isinstance(row, dict))
        # Bound: do not auto-walk next pages (limits API volume).
        if payload.get("next"):
            assert True  # acknowledged; intentionally not followed


def test_nessie_invalid_path_surfaces_error(nessie_settings: Settings) -> None:
    client = NessieClient(nessie_settings, timeout=10.0)
    with pytest.raises(NessieClientError) as exc_info:
        client._get("/customers/definitely-not-a-real-id-000/accounts")  # noqa: SLF001
    assert exc_info.value.status_code is not None
    assert exc_info.value.status_code >= 400
    # Error message must not echo the API key.
    assert nessie_settings.nessie_api_key not in str(exc_info.value)
