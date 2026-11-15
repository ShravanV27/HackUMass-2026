"""SQL lookback bounds must match the in-memory (start, end] day filter."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.config import Settings
from app.domain.alerts import category_spend_totals
from app.models.schemas import Transaction, TransactionKind, TransactionStatus
from app.services.insights_service import _category_windows
from app.services.providers.mock import MockDataProvider


class _LiveStub(MockDataProvider):
    @property
    def mode(self):  # type: ignore[override]
        return "live"


def test_live_category_sql_bounds_match_memory_windows(monkeypatch) -> None:
    captured: list[tuple[datetime, datetime]] = []

    class _Repo:
        def __init__(self, _conn: object) -> None:
            pass

        def spending_by_category(self, customer_id: str, *, start_at, end_at):
            captured.append((start_at, end_at))
            return []

    class _Conn:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: object) -> bool:
            return False

    monkeypatch.setattr(
        "app.services.insights_service.get_connection", lambda _settings: _Conn()
    )
    monkeypatch.setattr("app.services.insights_service.TigerRepository", _Repo)

    as_of = date(2026, 11, 14)
    settings = Settings(
        use_mock_data=False,
        demo_customer_id="demo-customer-001",
        database_url="postgresql://unused/db",
    )
    _recent, _baseline, source = _category_windows(
        settings,
        _LiveStub(),
        "demo-customer-001",
        as_of=as_of,
        recent_days=7,
        baseline_days=28,
    )
    assert source == "sql"
    assert captured[0] == (
        datetime(2026, 11, 8, tzinfo=timezone.utc),
        datetime(2026, 11, 15, tzinfo=timezone.utc),
    )
    assert captured[1] == (
        datetime(2026, 10, 11, tzinfo=timezone.utc),
        datetime(2026, 11, 8, tzinfo=timezone.utc),
    )

    # A transaction on the exclusive boundary day is outside the memory window
    # and must also be outside the SQL window (previously included).
    boundary = Transaction(
        transaction_id="boundary",
        customer_id="demo-customer-001",
        account_id="a1",
        amount=Decimal("-20.00"),
        kind=TransactionKind.EXPENSE,
        status=TransactionStatus.POSTED,
        description="boundary",
        category="Food",
        posted_at=datetime(2026, 11, 7, 12, tzinfo=timezone.utc),
    )
    inside = Transaction(
        transaction_id="inside",
        customer_id="demo-customer-001",
        account_id="a1",
        amount=Decimal("-9.00"),
        kind=TransactionKind.EXPENSE,
        status=TransactionStatus.POSTED,
        description="inside",
        category="Food",
        posted_at=datetime(2026, 11, 8, 12, tzinfo=timezone.utc),
    )
    totals = category_spend_totals(
        [boundary, inside],
        customer_id="demo-customer-001",
        start=as_of - timedelta(days=7),
        end=as_of,
    )
    assert totals == {"Food": Decimal("9.00")}
    sql_start, sql_end = captured[0]
    assert not (sql_start <= boundary.posted_at < sql_end)
    assert sql_start <= inside.posted_at < sql_end


def test_tiger_history_bounds_skip_lookback_start_day(monkeypatch) -> None:
    captured: dict[str, datetime] = {}

    class _Repo:
        def __init__(self, _conn: object) -> None:
            pass

        def discretionary_daily_spending(
            self,
            customer_id,
            *,
            start_at,
            end_at,
            exclude_categories,
            exclude_description_tokens=None,
        ):
            captured["start_at"] = start_at
            captured["end_at"] = end_at
            captured["exclude_description_tokens"] = list(exclude_description_tokens or [])
            return []

    class _Conn:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: object) -> bool:
            return False

    monkeypatch.setattr(
        "app.api.prediction_context.get_connection", lambda _settings: _Conn()
    )
    monkeypatch.setattr("app.api.prediction_context.TigerRepository", _Repo)

    from app.api.prediction_context import _tiger_discretionary_history

    settings = Settings(
        use_mock_data=False,
        demo_customer_id="demo-customer-001",
        database_url="postgresql://unused/db",
    )
    history = _tiger_discretionary_history(
        "demo-customer-001",
        settings,
        as_of=date(2026, 11, 14),
        lookback_days=30,
        bills=[],
    )
    assert history == []
    # In-memory window is Oct 15 < day <= Nov 14, so SQL starts Oct 16.
    assert captured["start_at"] == datetime(2026, 10, 16, tzinfo=timezone.utc)
    assert captured["end_at"] == datetime(2026, 11, 15, tzinfo=timezone.utc)
