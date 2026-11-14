"""In-process Nessie customer bindings for Demo Lab session entry.

Does not rewrite .env and never deletes Nessie data. Cleared between tests.
"""

from __future__ import annotations

import threading

_LOCK = threading.Lock()
# app_customer_id → nessie sandbox customer id
_BINDINGS: dict[str, str] = {}


def clear_session_bindings() -> None:
    with _LOCK:
        _BINDINGS.clear()


def get_bound_nessie_customer_id(app_customer_id: str) -> str | None:
    with _LOCK:
        value = _BINDINGS.get(app_customer_id)
        return value.strip() if value else None


def bind_nessie_customer(app_customer_id: str, nessie_customer_id: str) -> str:
    cleaned = nessie_customer_id.strip()
    if not cleaned:
        raise ValueError("nessie_customer_id must be non-empty")
    with _LOCK:
        _BINDINGS[app_customer_id] = cleaned
    return cleaned


def unbind_nessie_customer(app_customer_id: str) -> None:
    with _LOCK:
        _BINDINGS.pop(app_customer_id, None)
