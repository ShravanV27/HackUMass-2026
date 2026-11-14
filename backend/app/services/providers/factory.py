"""Select mock or live provider from settings."""

from __future__ import annotations

from functools import lru_cache

from app.config import Settings, get_settings
from app.services.providers.base import DataProvider
from app.services.providers.live import LiveDataProvider
from app.services.providers.mock import MockDataProvider


def build_provider(settings: Settings) -> DataProvider:
    if settings.use_mock_data:
        return MockDataProvider()
    return LiveDataProvider(settings)


@lru_cache
def get_provider() -> DataProvider:
    return build_provider(get_settings())
