"""Per-customer payment-cycle settings (memory + optional TigerData)."""

from __future__ import annotations

import json
import threading
from typing import Any, Literal

import psycopg
from psycopg.rows import dict_row

from app.config import Settings, get_settings
from app.models.schemas import PaymentCycleConfig

_LOCK = threading.Lock()
_MEMORY: dict[str, PaymentCycleConfig] = {}


def clear_settings_memory() -> None:
    """Test helper — wipe in-process overrides."""
    with _LOCK:
        _MEMORY.clear()


def default_payment_cycle() -> PaymentCycleConfig:
    return PaymentCycleConfig()


def get_payment_cycle(
    customer_id: str,
    *,
    settings: Settings | None = None,
) -> tuple[PaymentCycleConfig, Literal["default", "memory", "database"]]:
    with _LOCK:
        if customer_id in _MEMORY:
            return _MEMORY[customer_id].model_copy(deep=True), "memory"

    cfg = settings or get_settings()
    if not cfg.use_mock_data and cfg.database_url:
        loaded = _load_from_db(customer_id, cfg.database_url)
        if loaded is not None:
            with _LOCK:
                _MEMORY[customer_id] = loaded
            return loaded.model_copy(deep=True), "database"
    return default_payment_cycle(), "default"


def put_payment_cycle(
    customer_id: str,
    payment_cycle: PaymentCycleConfig,
    *,
    settings: Settings | None = None,
) -> tuple[PaymentCycleConfig, Literal["memory", "database"]]:
    stored = payment_cycle.model_copy(deep=True)
    # Validate override days.
    for bill_id, day in stored.bill_day_overrides.items():
        if day < 1 or day > 31:
            raise ValueError(f"bill_day_overrides[{bill_id}] must be 1-31")

    cfg = settings or get_settings()
    source: Literal["memory", "database"] = "memory"
    if not cfg.use_mock_data and cfg.database_url:
        _save_to_db(customer_id, stored, cfg.database_url)
        source = "database"

    with _LOCK:
        _MEMORY[customer_id] = stored
    return stored.model_copy(deep=True), source


def _load_from_db(customer_id: str, database_url: str) -> PaymentCycleConfig | None:
    try:
        with psycopg.connect(database_url) as conn:
            _ensure_settings_table(conn)
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT payload
                    FROM customer_settings
                    WHERE customer_id = %s
                    """,
                    (customer_id,),
                )
                row = cur.fetchone()
            if not row:
                return None
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            return PaymentCycleConfig.model_validate(payload)
    except (psycopg.Error, ValueError, TypeError, json.JSONDecodeError):
        return None


def _save_to_db(
    customer_id: str, payment_cycle: PaymentCycleConfig, database_url: str
) -> None:
    payload: dict[str, Any] = json.loads(payment_cycle.model_dump_json())
    with psycopg.connect(database_url) as conn:
        _ensure_settings_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO customer_settings (customer_id, payload, updated_at)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (customer_id) DO UPDATE SET
                    payload = EXCLUDED.payload,
                    updated_at = NOW()
                """,
                (customer_id, json.dumps(payload)),
            )
        conn.commit()


def _ensure_settings_table(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS customer_settings (
                customer_id TEXT PRIMARY KEY,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    conn.commit()
