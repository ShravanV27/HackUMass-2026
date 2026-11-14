"""Shared pytest fixtures and live/destructive safety gates."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# Ensure mock mode and fixed demo customer for all offline API tests.
# Live tests override settings via their own fixtures — they never rely on
# these defaults for external calls.
os.environ.setdefault("USE_MOCK_DATA", "true")
os.environ.setdefault("DEMO_CUSTOMER_ID", "demo-customer-001")

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services.providers.factory import get_provider  # noqa: E402
from app.services.providers.mock import clear_fixture_cache  # noqa: E402


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Run tests marked @pytest.mark.live (external credentials required).",
    )
    parser.addoption(
        "--run-destructive",
        action="store_true",
        default=False,
        help=(
            "Run tests marked @pytest.mark.destructive "
            "(also requires CONFIRM_NESSIE_DESTRUCTIVE=I_UNDERSTAND)."
        ),
    )


def _has_marker(item: pytest.Item, name: str) -> bool:
    # Use iter_markers — path segments like tests/live/ are also keywords.
    return any(marker.name == name for marker in item.iter_markers())


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Deselect live/destructive tests during ordinary pytest (not merely skip)."""
    run_live = bool(config.getoption("--run-live"))
    run_destructive = bool(config.getoption("--run-destructive"))
    kept: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        is_destructive = _has_marker(item, "destructive")
        is_live = _has_marker(item, "live")
        if is_destructive and not run_destructive:
            deselected.append(item)
        elif is_live and not is_destructive and not run_live:
            deselected.append(item)
        else:
            kept.append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = kept


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    get_provider.cache_clear()
    clear_fixture_cache()
    yield
    get_settings.cache_clear()
    get_provider.cache_clear()
    clear_fixture_cache()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def demo_customer_id() -> str:
    return "demo-customer-001"
