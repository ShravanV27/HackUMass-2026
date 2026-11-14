"""Data provider factory."""

from app.services.providers.base import DataProvider
from app.services.providers.factory import get_provider

__all__ = ["DataProvider", "get_provider"]
