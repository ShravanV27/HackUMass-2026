"""Regression: Nessie v2 write payloads must use OpenAPI create DTOs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.config import Settings
from app.services.nessie_client import NessieClientError
from app.services.nessie_write_client import (
    NessieWriteClient,
    build_bill_body,
    build_deposit_body,
    build_purchase_body,
    dollars_to_cents,
)


def test_dollars_to_cents() -> None:
    assert dollars_to_cents(Decimal("50.00")) == 5000
    assert dollars_to_cents(Decimal("0.01")) == 1
    assert dollars_to_cents(Decimal("12.345")) == 1235


def test_build_deposit_body_is_v2_dto() -> None:
    body = build_deposit_body(
        amount=Decimal("50.00"),
        description="Demo Lab deposit",
    )
    assert body == {
        "amount_cents": 5000,
        "medium": "balance",
        "description": "Demo Lab deposit",
    }
    # Classic fields that caused live 422 must not appear.
    assert "amount" not in body
    assert "status" not in body
    assert "transaction_date" not in body


def test_build_purchase_body_is_v2_dto() -> None:
    body = build_purchase_body(
        amount=Decimal("19.99"),
        description="Gear",
        merchant_id="merch_abc",
    )
    assert body["amount_cents"] == 1999
    assert body["merchant_id"] == "merch_abc"
    assert "amount" not in body
    assert "status" not in body
    assert "purchase_date" not in body


def test_build_bill_body_maps_posted_status() -> None:
    body = build_bill_body(
        payee="Streaming",
        amount=Decimal("15.00"),
        status="posted",
        payment_date=date(2026, 11, 25),
    )
    assert body["status"] == "completed"
    assert body["payment_amount"] == 15.0
    assert body["payment_date"] == "2026-11-25"


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.content = b"{}" if payload is not None or text else b""

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _RecordingSession:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.posts.append({"url": url, "json": kwargs.get("json"), "params": kwargs.get("params")})
        return _FakeResponse(
            201,
            {"objectCreated": {"_id": "dep_1", "amount_cents": kwargs["json"]["amount_cents"]}},
        )

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        if url.endswith("/merchants"):
            return _FakeResponse(200, {"items": [{"_id": "merch_1"}]})
        return _FakeResponse(200, {})


def test_create_deposit_posts_amount_cents_only() -> None:
    session = _RecordingSession()
    client = NessieWriteClient(
        Settings(nessie_api_key="k", nessie_base_url="https://example.test"),
        session=session,  # type: ignore[arg-type]
    )
    created = client.create_deposit(
        "acct_1",
        amount=Decimal("50.00"),
        description="Demo Lab deposit",
        status="posted",  # must be ignored for v2
        transaction_date=date(2026, 11, 14),
    )
    assert created["_id"] == "dep_1"
    assert len(session.posts) == 1
    posted = session.posts[0]["json"]
    assert posted == {
        "amount_cents": 5000,
        "medium": "balance",
        "description": "Demo Lab deposit",
    }
    assert session.posts[0]["url"].endswith("/accounts/acct_1/deposits")


def test_create_purchase_posts_amount_cents() -> None:
    session = _RecordingSession()
    client = NessieWriteClient(
        Settings(nessie_api_key="k", nessie_base_url="https://example.test"),
        session=session,  # type: ignore[arg-type]
    )
    client.create_purchase(
        "acct_1",
        amount=Decimal("10.00"),
        description="Coffee",
        status="posted",
        purchase_date=date(2026, 11, 14),
    )
    posted = session.posts[0]["json"]
    assert posted["amount_cents"] == 1000
    assert posted["merchant_id"] == "merch_1"
    assert "status" not in posted
    assert "purchase_date" not in posted


def test_post_error_includes_upstream_body() -> None:
    class _ErrSession:
        def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse(
                422,
                text='{"detail":"validation failed","errors":[{"message":"expected required property amount_cents"}]}',
            )

        def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse(200, {"items": []})

    client = NessieWriteClient(
        Settings(nessie_api_key="k", nessie_base_url="https://example.test"),
        session=_ErrSession(),  # type: ignore[arg-type]
    )
    with pytest.raises(NessieClientError) as exc:
        client.create_deposit(
            "acct_1",
            amount=Decimal("1.00"),
            description="x",
        )
    assert exc.value.status_code == 422
    assert "amount_cents" in str(exc.value)
