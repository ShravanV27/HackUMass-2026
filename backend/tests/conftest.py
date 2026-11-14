"""Shared pytest fixtures."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# Ensure mock mode and fixed demo customer for all API tests.
os.environ.setdefault("USE_MOCK_DATA", "true")
os.environ.setdefault("DEMO_CUSTOMER_ID", "demo-customer-001")

from app.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def demo_customer_id() -> str:
    return "demo-customer-001"
