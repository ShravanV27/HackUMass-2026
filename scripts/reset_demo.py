#!/usr/bin/env python3
"""One-command HackUMass demo environment reset.

Workflow
--------
1. Purge the designated Nessie sandbox (DELETE /data).
2. Run a deterministic historical simulation (POST /simulations).
3. Select a customer with usable accounts + transaction history.
4. Add upcoming bills via Nessie v2 bill endpoints when needed.
5. Recreate HackUMass TigerData app tables and sync from Nessie.
6. Update NESSIE_CUSTOMER_ID in .env (DEMO_CUSTOMER_ID unchanged).
7. Verify dashboard / prediction math against the synced dataset.

Commands
--------
  python scripts/reset_demo.py --dry-run   # preview only (default)
  python scripts/reset_demo.py --reset     # full Nessie + TigerData reset
  python scripts/reset_demo.py --resume    # skip Nessie; redo TigerData sync

Confirmation: ``--reset`` / ``--resume`` alone are enough for the disposable
hackathon sandbox + DATABASE_URL. No CONFIRM_* env vars.

Secrets are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

load_dotenv(_REPO_ROOT / ".env")

# Deterministic historical simulation (OpenAPI SimulationCreateDTO).
SIM_SEED = 20261114
SIM_CUSTOMERS = 1
SIM_TRANSACTIONS = 400
SIM_DURATION_SECONDS = 75 * 24 * 60 * 60  # 6_480_000 → 75 days
SIM_ANOMALY_RATE = 0.0
SIM_MODE = "historical"
SIM_POLL_INTERVAL_SEC = 2.0
SIM_TIMEOUT_SEC = 180.0

MIN_TXNS_FOR_DEMO = 50
ALLOWED_NESSIE_HOST_SUFFIX = "nessieisreal.com"
ALLOWED_TIGERDATA_HOST_MARKERS = ("timescale.com", "tsdb.cloud", "localhost", "127.0.0.1")

STATE_PATH = _REPO_ROOT / ".demo_reset_state.json"
ENV_PATH = _REPO_ROOT / ".env"

RATE_LIMIT_BACKOFF = (1.0, 2.0, 4.0)


@dataclass(frozen=True)
class DemoConfig:
    nessie_api_key: str
    nessie_base_url: str
    database_url: str
    demo_customer_id: str
    nessie_customer_id: str | None
    gemini_api_key: str | None
    gemini_model: str


@dataclass(frozen=True)
class SelectedCustomer:
    customer_id: str
    accounts: list[dict[str, Any]]
    checking_id: str | None
    savings_id: str | None
    transaction_count: int
    source: str  # simulation | seed_fallback


class ResetError(RuntimeError):
    """Hard failure — do not continue silently."""


def _money(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _redact_id(value: str | None) -> str:
    if not value:
        return "(none)"
    if len(value) <= 6:
        return "***"
    return f"{value[:4]}…{value[-3:]}(len={len(value)})"


def _redact_db_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or "?"
    port = f":{parsed.port}" if parsed.port else ""
    db = (parsed.path or "/").lstrip("/") or "?"
    return f"{parsed.scheme}://***@{host}{port}/{db}"


def _redact_secrets(text: str, *, api_key: str = "") -> str:
    redacted = text
    if api_key:
        redacted = redacted.replace(api_key, "[REDACTED_API_KEY]")
    redacted = re.sub(r"\b\d{12,19}\b", "[REDACTED_ACCOUNT]", redacted)
    redacted = re.sub(r"\bnsk_[A-Za-z0-9]+\b", "[REDACTED_API_KEY]", redacted)
    redacted = re.sub(
        r"(?i)(api[_-]?key|authorization|bearer|password|secret|token)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        redacted,
    )
    return redacted[:2000]


def _as_list(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in (
            "items",
            "results",
            "data",
            "accounts",
            "transactions",
            "bills",
            "customers",
        ):
            inner = payload.get(key)
            if isinstance(inner, list):
                return [row for row in inner if isinstance(row, dict)]
        if payload.get("id") is not None or payload.get("_id") is not None:
            return [payload]
    return []


def _resource_id(raw: dict[str, Any]) -> str:
    return str(raw.get("id") or raw.get("_id") or "")


def _account_type(raw: dict[str, Any]) -> str:
    return str(raw.get("type") or raw.get("account_type") or "")


def _account_balance(raw: dict[str, Any]) -> Decimal:
    if raw.get("balance") is not None:
        return _money(raw["balance"])
    for key in ("balance_cents", "current_balance_cents", "available_cents"):
        if raw.get(key) is not None:
            return _money(Decimal(str(raw[key])) / Decimal("100"))
    return _money(0)


class NessieAdminClient:
    """Write-capable Nessie helper for demo reset only (not used by FastAPI)."""

    def __init__(self, *, base_url: str, api_key: str, timeout: float = 30.0) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._session = requests.Session()
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self._base}{path}"
        for attempt, delay in enumerate(RATE_LIMIT_BACKOFF, start=1):
            try:
                response = self._session.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=json_body,
                    timeout=self._timeout,
                )
            except requests.RequestException as exc:
                raise ResetError(
                    f"Nessie {method} {path} failed: {type(exc).__name__}"
                ) from exc

            if response.status_code == 429 and attempt < len(RATE_LIMIT_BACKOFF):
                time.sleep(delay)
                continue
            if response.status_code >= 400:
                detail = _redact_secrets(response.text or "", api_key=self._key)
                raise ResetError(
                    f"Nessie HTTP {response.status_code} for {method} {path}"
                    + (f": {detail}" if detail else "")
                )
            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError as exc:
                raise ResetError(f"Nessie returned invalid JSON for {path}") from exc
        raise ResetError(f"Nessie {method} {path} exhausted rate-limit retries")

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self.request("POST", path, json_body=body)

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path)


def load_demo_config() -> DemoConfig:
    api_key = (os.getenv("NESSIE_API_KEY") or "").strip()
    base_url = (
        os.getenv("NESSIE_BASE_URL") or "https://v2-prod-api.nessieisreal.com"
    ).rstrip("/")
    database_url = (os.getenv("DATABASE_URL") or "").strip()
    demo_customer_id = (os.getenv("DEMO_CUSTOMER_ID") or "demo-customer-001").strip()
    nessie_customer_id = (os.getenv("NESSIE_CUSTOMER_ID") or "").strip() or None
    gemini_api_key = (os.getenv("GEMINI_API_KEY") or "").strip() or None
    gemini_model = (os.getenv("GEMINI_MODEL") or "gemini-3.5-flash-lite").strip()

    if not api_key:
        raise ResetError("NESSIE_API_KEY is required in .env")
    if not database_url:
        raise ResetError("DATABASE_URL is required in .env")
    return DemoConfig(
        nessie_api_key=api_key,
        nessie_base_url=base_url,
        database_url=database_url,
        demo_customer_id=demo_customer_id,
        nessie_customer_id=nessie_customer_id,
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
    )


def verify_environment(config: DemoConfig) -> None:
    """Refuse to mutate environments that cannot be identified as our demo targets."""
    host = (urlparse(config.nessie_base_url).hostname or "").lower()
    if not host.endswith(ALLOWED_NESSIE_HOST_SUFFIX):
        raise ResetError(
            f"Refusing Nessie reset: base URL host {host!r} is not "
            f"*.{ALLOWED_NESSIE_HOST_SUFFIX}"
        )

    db_host = (urlparse(config.database_url).hostname or "").lower()
    if not any(marker in db_host for marker in ALLOWED_TIGERDATA_HOST_MARKERS):
        raise ResetError(
            f"Refusing TigerData reset: DATABASE_URL host {db_host!r} is not a "
            "recognized HackUMass TigerData/Timescale development host. "
            "Update ALLOWED_TIGERDATA_HOST_MARKERS only if intentional."
        )

    live_test_url = (os.getenv("LIVE_TEST_DATABASE_URL") or "").strip()
    if live_test_url:
        demo_norm = _normalize_db(config.database_url)
        live_norm = _normalize_db(live_test_url)
        if demo_norm == live_norm:
            print(
                "NOTE: LIVE_TEST_DATABASE_URL matches DATABASE_URL. "
                "reset_demo only uses DATABASE_URL; live pytest should use a "
                "separate isolated URL when possible."
            )


def _normalize_db(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path or ""
    return f"{parsed.scheme}://{host}{port}{path}"


def simulation_body() -> dict[str, Any]:
    return {
        "mode": SIM_MODE,
        "seed": SIM_SEED,
        "customers": SIM_CUSTOMERS,
        "transactions": SIM_TRANSACTIONS,
        "duration_seconds": SIM_DURATION_SECONDS,
        "anomaly_rate": SIM_ANOMALY_RATE,
    }


def purge_nessie(client: NessieAdminClient) -> int:
    payload = client.delete("/data")
    deleted = int(payload.get("deleted") or 0) if isinstance(payload, dict) else 0
    return deleted


def start_simulation(client: NessieAdminClient) -> dict[str, Any]:
    payload = client.post("/simulations", simulation_body())
    if not isinstance(payload, dict) or not payload.get("id"):
        raise ResetError("Simulation create response missing id")
    return payload


def poll_simulation(
    client: NessieAdminClient,
    simulation_id: str,
    *,
    timeout_sec: float = SIM_TIMEOUT_SEC,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        payload = client.get(f"/simulations/{simulation_id}")
        if not isinstance(payload, dict):
            raise ResetError("Simulation poll returned non-object JSON")
        last = payload
        status = str(payload.get("status") or "")
        generated = payload.get("generated")
        print(
            f"  simulation status={status} generated={generated} "
            f"id={_redact_id(simulation_id)}"
        )
        if status == "finished":
            return payload
        if status == "stopped":
            raise ResetError(
                "Simulation stopped before finishing. "
                f"generated={generated} anomalies={payload.get('anomalies')}"
            )
        time.sleep(SIM_POLL_INTERVAL_SEC)
    raise ResetError(
        f"Simulation timed out after {timeout_sec:.0f}s "
        f"(last status={last.get('status')!r}, generated={last.get('generated')})"
    )


def _score_customer(
    client: NessieAdminClient,
    customer_id: str,
) -> SelectedCustomer | None:
    accounts = _as_list(client.get(f"/customers/{customer_id}/accounts"))
    if not accounts:
        return None

    checking_id: str | None = None
    savings_id: str | None = None
    txn_count = 0
    for account in accounts:
        aid = _resource_id(account)
        atype = _account_type(account).lower()
        if not aid:
            continue
        if "checking" in atype and checking_id is None:
            checking_id = aid
        if "saving" in atype and savings_id is None:
            savings_id = aid
        try:
            txns = _as_list(client.get(f"/accounts/{aid}/transactions"))
        except ResetError:
            txns = []
        txn_count += len(txns)

    # Prefer checking+savings; allow checking-only if enough history.
    if checking_id is None and savings_id is None:
        return None
    return SelectedCustomer(
        customer_id=customer_id,
        accounts=accounts,
        checking_id=checking_id,
        savings_id=savings_id,
        transaction_count=txn_count,
        source="simulation",
    )


def select_customer(
    client: NessieAdminClient,
    simulation: dict[str, Any],
) -> SelectedCustomer:
    customer_ids = [
        str(cid) for cid in (simulation.get("customer_ids") or []) if cid
    ]
    if not customer_ids:
        raise ResetError("Simulation finished without customer_ids")

    candidates: list[SelectedCustomer] = []
    for cid in customer_ids:
        scored = _score_customer(client, cid)
        if scored is not None:
            candidates.append(scored)

    if not candidates:
        raise ResetError(
            "Simulation customers have no usable Checking/Savings accounts"
        )

    # Prefer checking+savings, then highest transaction count.
    candidates.sort(
        key=lambda c: (
            1 if c.checking_id and c.savings_id else 0,
            1 if c.checking_id else 0,
            c.transaction_count,
        ),
        reverse=True,
    )
    best = candidates[0]
    if best.transaction_count < MIN_TXNS_FOR_DEMO:
        print(
            f"WARNING: selected customer has only {best.transaction_count} "
            f"transactions (want ≥{MIN_TXNS_FOR_DEMO})."
        )
    if not best.checking_id:
        print(
            "WARNING: selected customer has no Checking account; "
            "using best available cash account."
        )
    return best


def seed_fallback(client: NessieAdminClient) -> SelectedCustomer:
    """Documented fallback when historical simulation is unsuitable.

    Uses POST /data/seed (idempotent when data already exists). Does not invent
    synthetic transaction rows locally.
    """
    print(
        "FALLBACK: historical simulation did not yield a usable demo customer. "
        "Calling documented POST /data/seed (small demo bank; limited history)."
    )
    client.post("/data/seed")
    customers = _as_list(client.get("/customers"))
    if not customers:
        raise ResetError("Seed fallback produced no customers")

    candidates: list[SelectedCustomer] = []
    for row in customers:
        cid = _resource_id(row)
        if not cid:
            continue
        scored = _score_customer(client, cid)
        if scored is None:
            continue
        candidates.append(
            SelectedCustomer(
                customer_id=scored.customer_id,
                accounts=scored.accounts,
                checking_id=scored.checking_id,
                savings_id=scored.savings_id,
                transaction_count=scored.transaction_count,
                source="seed_fallback",
            )
        )
    if not candidates:
        raise ResetError("Seed fallback customers lack Checking/Savings accounts")
    candidates.sort(
        key=lambda c: (
            1 if c.checking_id and c.savings_id else 0,
            c.transaction_count,
        ),
        reverse=True,
    )
    return candidates[0]


def ensure_upcoming_bills(
    client: NessieAdminClient,
    selected: SelectedCustomer,
) -> int:
    account_id = selected.checking_id or selected.savings_id
    if not account_id:
        print("Skipping bills: no cash account available")
        return 0

    existing = _as_list(client.get(f"/accounts/{account_id}/bills"))
    upcoming = [
        b
        for b in existing
        if str(b.get("status") or "").lower() in {"pending", "recurring"}
    ]
    if len(upcoming) >= 2:
        print(f"Bills already present on account: {len(upcoming)}")
        return len(upcoming)

    as_of = date.today()
    planned = [
        ("Verizon", "Phone plan", 45.00, as_of + timedelta(days=6), 6),
        ("Comcast", "Internet", 70.00, as_of + timedelta(days=11), 11),
        ("Landlord", "Rent", 980.00, as_of + timedelta(days=21), 21),
    ]
    created = 0
    for payee, nick, amount, due, recurring_day in planned:
        body = {
            "status": "recurring",
            "payee": payee,
            "nickname": nick,
            "payment_amount": float(amount),
            "payment_date": due.isoformat(),
            "recurring_date": min(recurring_day, 28),
        }
        client.post(f"/accounts/{account_id}/bills", body)
        created += 1
    print(f"Created {created} upcoming bills on {_redact_id(account_id)}")
    return created + len(upcoming)


def update_env_nessie_customer_id(customer_id: str) -> None:
    """Targeted NESSIE_CUSTOMER_ID edit — never rewrites other secrets."""
    if not ENV_PATH.exists():
        raise ResetError(f".env not found at {ENV_PATH}")
    text = ENV_PATH.read_text(encoding="utf-8")
    pattern = re.compile(r"^NESSIE_CUSTOMER_ID=.*$", re.MULTILINE)
    replacement = f"NESSIE_CUSTOMER_ID={customer_id}"
    if pattern.search(text):
        new_text = pattern.sub(replacement, text, count=1)
    else:
        if not text.endswith("\n"):
            text += "\n"
        new_text = text + replacement + "\n"
    if new_text == text:
        print("NESSIE_CUSTOMER_ID already up to date")
        return
    ENV_PATH.write_text(new_text, encoding="utf-8")
    print(f"Updated .env NESSIE_CUSTOMER_ID → {_redact_id(customer_id)}")


def write_state(payload: dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_state() -> dict[str, Any] | None:
    if not STATE_PATH.exists():
        return None
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def sync_tigerdata(config: DemoConfig, nessie_customer_id: str) -> dict[str, Any]:
    from app.config import Settings
    from app.db.connection import get_connection
    from app.db.repository import TigerRepository
    from app.services.nessie_client import NessieClient
    from app.services.nessie_sync import NessieSyncService

    settings = Settings(
        use_mock_data=False,
        demo_customer_id=config.demo_customer_id,
        nessie_api_key=config.nessie_api_key,
        nessie_base_url=config.nessie_base_url,
        nessie_customer_id=nessie_customer_id,
        database_url=config.database_url,
        gemini_api_key=config.gemini_api_key,
        gemini_model=config.gemini_model,
    )

    with get_connection(settings) as conn:
        repo = TigerRepository(conn)
        print("Recreating TigerData app tables (accounts/transactions/cagg)…")
        repo.reset_app_tables()
        nessie = NessieClient(settings, timeout=30.0)
        result = NessieSyncService(settings, nessie, repo).sync_customer(
            config.demo_customer_id
        )
        schema = repo.last_schema_result
        accounts = repo.list_accounts(config.demo_customer_id)
        transactions = repo.list_transactions(config.demo_customer_id)

    return {
        "sync": result,
        "schema": schema,
        "accounts": accounts,
        "transactions": transactions,
        "settings": settings,
    }


def verify_predictions(
    config: DemoConfig,
    *,
    settings: Any,
    accounts: list[Any],
    transactions: list[Any],
) -> dict[str, Any]:
    from datetime import date as date_cls
    from decimal import Decimal as Dec

    from app.domain.calculations import summarize_money_flow
    from app.domain.predictions import (
        build_cashflow_forecast,
        compute_safe_to_spend,
        compute_spending_drift,
        simulate_what_if_purchase,
    )
    from app.services.nessie_client import NessieClient
    from app.services.nessie_mapper import collect_scheduled_bills
    from app.services.nessie_sync import resolve_nessie_customer_id

    customer_id = config.demo_customer_id
    nessie_id = resolve_nessie_customer_id(settings, customer_id)
    nessie = NessieClient(settings, timeout=30.0)
    raw_bills = nessie.list_bills_for_customer(nessie_id)
    bills = collect_scheduled_bills(
        raw_bills, app_customer_id=customer_id, as_of=date_cls.today()
    )

    money = summarize_money_flow(customer_id, transactions)
    cashflow = build_cashflow_forecast(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=bills,
        as_of=date_cls.today(),
        horizon_days=14,
        lookback_days=30,
    )
    safe = compute_safe_to_spend(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=bills,
        as_of=date_cls.today(),
        horizon_days=14,
        lookback_days=30,
        minimum_buffer=Dec("250.00"),
    )
    drift = compute_spending_drift(
        customer_id=customer_id,
        transactions=transactions,
        accounts=accounts,
        as_of=date_cls.today(),
        recent_days=7,
        baseline_days=28,
    )
    what_if = simulate_what_if_purchase(
        customer_id=customer_id,
        accounts=accounts,
        transactions=transactions,
        scheduled_bills=bills,
        purchase_amount=Dec("75.00"),
        description="Demo verification purchase",
        as_of=date_cls.today(),
        horizon_days=14,
        lookback_days=30,
        minimum_buffer=Dec("250.00"),
    )

    # Validity gates — empty history is allowed but must return structured results.
    if not accounts:
        raise ResetError("Verification failed: no accounts after sync")
    if cashflow.horizon_days != 14:
        raise ResetError("Verification failed: cashflow forecast incomplete")
    if safe.safe_to_spend is None:
        raise ResetError("Verification failed: safe-to-spend incomplete")
    if drift.direction not in {"up", "down", "flat"}:
        raise ResetError("Verification failed: drift incomplete")
    if what_if.affordable is None:
        raise ResetError("Verification failed: what-if incomplete")

    return {
        "accounts": len(accounts),
        "transactions": len(transactions),
        "bills": len(bills),
        "money_flow": money,
        "cashflow_ending": str(cashflow.ending_balance),
        "cashflow_days": len(cashflow.points),
        "safe_to_spend": str(safe.safe_to_spend),
        "drift_direction": drift.direction,
        "drift_pct": str(drift.drift_pct),
        "what_if_affordable": what_if.affordable,
        "limitations": _dataset_limitations(len(transactions), bills),
    }


def _dataset_limitations(txn_count: int, bills: list[Any]) -> list[str]:
    notes: list[str] = []
    notes.append(
        "Nessie historical simulation generates synthetic bank activity that may "
        "look irregular / fraud-oriented rather than household spending patterns."
    )
    if txn_count < MIN_TXNS_FOR_DEMO:
        notes.append(
            f"Transaction history is thin ({txn_count} < {MIN_TXNS_FOR_DEMO}); "
            "drift and spend estimates may be weak."
        )
    if not bills:
        notes.append(
            "No scheduled bills were found; cash-flow / safe-to-spend will rely "
            "on estimated discretionary spend only."
        )
    notes.append(
        "Fixed simulation seed repeats behavior, but Nessie resource IDs change "
        "across resets — always re-sync TigerData after reset."
    )
    return notes


def print_summary(
    *,
    selected: SelectedCustomer,
    verification: dict[str, Any],
    sync_meta: dict[str, Any] | None = None,
) -> None:
    print("\n=== DEMO SETUP SUMMARY ===")
    print(f"source: {selected.source}")
    print(f"nessie_customer: {_redact_id(selected.customer_id)}")
    print(f"demo_customer: {os.getenv('DEMO_CUSTOMER_ID') or 'demo-customer-001'}")
    print("accounts:")
    for account in selected.accounts:
        print(
            f"  {_redact_id(_resource_id(account))} "
            f"type={_account_type(account)} balance={_account_balance(account)}"
        )
    print(
        f"synced: accounts={verification['accounts']} "
        f"transactions={verification['transactions']} "
        f"bills={verification['bills']}"
    )
    if sync_meta and sync_meta.get("sync") is not None:
        sync = sync_meta["sync"]
        print(
            f"reconcile: accounts_deleted={sync.accounts_deleted} "
            f"transactions_deleted={sync.transactions_deleted} "
            f"reconciled={sync.reconciled}"
        )
        schema = sync_meta.get("schema")
        if schema is not None:
            print(
                f"tigerdata: hypertable={schema.hypertable_enabled} "
                f"cagg={schema.continuous_aggregate_enabled}"
            )
    print(
        f"predictions: cashflow_ending={verification['cashflow_ending']} "
        f"safe_to_spend={verification['safe_to_spend']} "
        f"drift={verification['drift_direction']} "
        f"what_if_affordable={verification['what_if_affordable']}"
    )
    print("limitations:")
    for note in verification["limitations"]:
        print(f"  - {note}")


def run_dry_run(config: DemoConfig) -> int:
    print("MODE: dry-run (no Nessie writes, no TigerData changes, no .env edits)")
    verify_environment(config)
    print(f"nessie_base: {config.nessie_base_url}")
    print(f"database: {_redact_db_url(config.database_url)}")
    print(f"demo_customer_id: {config.demo_customer_id}")
    print(f"current_nessie_customer_id: {_redact_id(config.nessie_customer_id)}")
    print("\nPlanned steps:")
    print("  1. DELETE /data  (purge sandbox under NESSIE_API_KEY)")
    print("  2. POST /simulations with:")
    print(f"       {json.dumps(simulation_body())}")
    print(f"  3. Poll GET /simulations/{{id}} until finished (timeout {SIM_TIMEOUT_SEC:.0f}s)")
    print("  4. Select customer with Checking/Savings + history")
    print("  5. POST upcoming bills on checking if fewer than 2 exist")
    print("  6. DROP/recreate TigerData accounts + transactions (+ cagg)")
    print("  7. Update .env NESSIE_CUSTOMER_ID only")
    print("  8. Sync Nessie → TigerData for DEMO_CUSTOMER_ID")
    print("  9. Verify cashflow / safe-to-spend / drift / what-if")
    print("\nFallback if simulation unsuitable: POST /data/seed (documented).")
    print("Resume after partial Nessie success: python scripts/reset_demo.py --resume")
    print("\nDry-run complete. Pass --reset to execute.")
    return 0


def run_reset(config: DemoConfig) -> int:
    print("MODE: reset (will mutate designated Nessie sandbox + DATABASE_URL)")
    verify_environment(config)
    client = NessieAdminClient(
        base_url=config.nessie_base_url, api_key=config.nessie_api_key
    )

    print("1/9 Purging Nessie sandbox…")
    deleted = purge_nessie(client)
    print(f"  deleted_rows={deleted}")

    print("2/9 Starting historical simulation…")
    created = start_simulation(client)
    sim_id = str(created["id"])
    print(f"  simulation_id={_redact_id(sim_id)}")

    print("3/9 Waiting for simulation…")
    finished = poll_simulation(client, sim_id)
    print(
        f"  finished generated={finished.get('generated')} "
        f"anomalies={finished.get('anomalies')} "
        f"customers={len(finished.get('customer_ids') or [])}"
    )

    print("4/9 Selecting demo customer…")
    try:
        selected = select_customer(client, finished)
        if selected.transaction_count < MIN_TXNS_FOR_DEMO and not (
            selected.checking_id and selected.savings_id
        ):
            raise ResetError("Simulation quality below demo threshold")
    except ResetError as exc:
        print(f"  selection failed: {exc}")
        # Purge again so seed does not no-op on leftover simulation data.
        purge_nessie(client)
        selected = seed_fallback(client)

    print(
        f"  selected={_redact_id(selected.customer_id)} "
        f"txns={selected.transaction_count} "
        f"checking={_redact_id(selected.checking_id)} "
        f"savings={_redact_id(selected.savings_id)} "
        f"source={selected.source}"
    )

    print("5/9 Ensuring upcoming bills…")
    bill_count = ensure_upcoming_bills(client, selected)

    write_state(
        {
            "nessie_customer_id": selected.customer_id,
            "source": selected.source,
            "transaction_count": selected.transaction_count,
            "bill_count": bill_count,
            "simulation_seed": SIM_SEED,
            "phase": "nessie_ready",
        }
    )
    print(
        "  Nessie phase complete. If TigerData fails next, resume with:\n"
        "    python scripts/reset_demo.py --resume"
    )

    print("6/9 Updating .env NESSIE_CUSTOMER_ID…")
    update_env_nessie_customer_id(selected.customer_id)
    # Reload for subsequent Settings consumers.
    os.environ["NESSIE_CUSTOMER_ID"] = selected.customer_id
    config = DemoConfig(
        nessie_api_key=config.nessie_api_key,
        nessie_base_url=config.nessie_base_url,
        database_url=config.database_url,
        demo_customer_id=config.demo_customer_id,
        nessie_customer_id=selected.customer_id,
        gemini_api_key=config.gemini_api_key,
        gemini_model=config.gemini_model,
    )

    print("7/9 Syncing TigerData…")
    try:
        sync_meta = sync_tigerdata(config, selected.customer_id)
    except Exception as exc:  # noqa: BLE001
        write_state(
            {
                "nessie_customer_id": selected.customer_id,
                "source": selected.source,
                "phase": "nessie_ready_tigerdata_failed",
                "error": _redact_secrets(str(exc), api_key=config.nessie_api_key),
            }
        )
        raise ResetError(
            f"TigerData sync failed after Nessie succeeded: {exc}\n"
            "Recovery: python scripts/reset_demo.py --resume"
        ) from exc

    print("8/9 Verifying predictions…")
    verification = verify_predictions(
        config,
        settings=sync_meta["settings"],
        accounts=sync_meta["accounts"],
        transactions=sync_meta["transactions"],
    )

    write_state(
        {
            "nessie_customer_id": selected.customer_id,
            "source": selected.source,
            "phase": "complete",
            "transaction_count": verification["transactions"],
            "bill_count": verification["bills"],
        }
    )
    print_summary(selected=selected, verification=verification, sync_meta=sync_meta)
    print("\nReset complete.")
    return 0


def run_resume(config: DemoConfig) -> int:
    print("MODE: resume (skip Nessie purge/simulation; redo TigerData sync)")
    verify_environment(config)
    state = read_state() or {}
    customer_id = (
        (state.get("nessie_customer_id") if isinstance(state, dict) else None)
        or config.nessie_customer_id
    )
    if not customer_id:
        raise ResetError(
            "No NESSIE_CUSTOMER_ID available for resume. "
            "Run --reset first, or set NESSIE_CUSTOMER_ID in .env."
        )

    print(f"Using nessie_customer={_redact_id(str(customer_id))}")
    update_env_nessie_customer_id(str(customer_id))
    os.environ["NESSIE_CUSTOMER_ID"] = str(customer_id)
    config = DemoConfig(
        nessie_api_key=config.nessie_api_key,
        nessie_base_url=config.nessie_base_url,
        database_url=config.database_url,
        demo_customer_id=config.demo_customer_id,
        nessie_customer_id=str(customer_id),
        gemini_api_key=config.gemini_api_key,
        gemini_model=config.gemini_model,
    )

    # Re-read accounts for summary via admin client (GET only).
    client = NessieAdminClient(
        base_url=config.nessie_base_url, api_key=config.nessie_api_key
    )
    selected = _score_customer(client, str(customer_id))
    if selected is None:
        raise ResetError(
            "Resume customer has no usable accounts. Run --reset instead."
        )

    sync_meta = sync_tigerdata(config, str(customer_id))
    verification = verify_predictions(
        config,
        settings=sync_meta["settings"],
        accounts=sync_meta["accounts"],
        transactions=sync_meta["transactions"],
    )
    write_state(
        {
            "nessie_customer_id": str(customer_id),
            "source": selected.source,
            "phase": "complete",
            "transaction_count": verification["transactions"],
            "bill_count": verification["bills"],
        }
    )
    print_summary(selected=selected, verification=verification, sync_meta=sync_meta)
    print("\nResume complete.")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reset the HackUMass Nessie + TigerData demo environment."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview workflow without mutating anything (default).",
    )
    mode.add_argument(
        "--reset",
        action="store_true",
        help="Purge Nessie sandbox, simulate history, recreate TigerData, sync.",
    )
    mode.add_argument(
        "--resume",
        action="store_true",
        help="Skip Nessie; recreate TigerData and sync using existing customer.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_demo_config()
        if args.reset:
            return run_reset(config)
        if args.resume:
            return run_resume(config)
        return run_dry_run(config)
    except ResetError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
