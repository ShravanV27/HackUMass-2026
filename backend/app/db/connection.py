"""Database connection helpers for TigerData / Postgres."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from app.config import Settings


class DatabaseConfigError(RuntimeError):
    pass


@contextmanager
def get_connection(settings: Settings) -> Iterator[psycopg.Connection]:
    if not settings.database_url:
        raise DatabaseConfigError("DATABASE_URL is not configured")
    with psycopg.connect(settings.database_url) as conn:
        yield conn
