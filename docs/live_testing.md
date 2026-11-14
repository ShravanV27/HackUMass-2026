# Live & destructive integration tests

Opt-in suite under `backend/tests/live/`. Ordinary `pytest` **deselects** these tests so the offline suite stays credential-free.

## Commands

### A. Offline suite (default — no external APIs)

```bash
source .venv/bin/activate
cd backend
pytest
# equivalent:
pytest -m "not live and not destructive"
```

### B. Safe live integration (read-only Nessie + isolated TigerData)

```bash
source .venv/bin/activate
cd backend

export LIVE_TEST_DATABASE_URL='postgresql://…'   # must differ from DATABASE_URL
# optional if you accept shared-DB risk (not recommended):
# export LIVE_TEST_ALLOW_SHARED_DB=confirm

# Nessie read credentials (from env / shell — do not commit):
# NESSIE_API_KEY, NESSIE_BASE_URL, NESSIE_CUSTOMER_ID

pytest --run-live -m "live and not destructive and not live_gemini"
```

Gemini (extra opt-in, few calls):

```bash
LIVE_TEST_GEMINI=1 pytest --run-live -m "live_gemini"
```

### C. Explicitly approved sandbox reset/reseed (destructive)

```bash
export NESSIE_DESTRUCTIVE_API_KEY='…'          # dedicated key ≠ NESSIE_API_KEY
export CONFIRM_NESSIE_DESTRUCTIVE=I_UNDERSTAND
export CONFIRM_NESSIE_SEED=SEED                # required to actually POST /data/seed

cd backend
pytest --run-destructive -m destructive
```

## Isolation rules

| Resource | Policy |
|----------|--------|
| TigerData | Requires `LIVE_TEST_DATABASE_URL`. Fails if it matches `DATABASE_URL` unless `LIVE_TEST_ALLOW_SHARED_DB=confirm`. Rows use customer `live-test-customer-001` and are deleted after tests. |
| Nessie reads | `NESSIE_API_KEY` GET only; bounded retries on HTTP 429. |
| Nessie writes | Dedicated `NESSIE_DESTRUCTIVE_API_KEY` + confirmations. Never auto-wipe shared sandbox. |
| Gemini | `LIVE_TEST_GEMINI=1` + `@pytest.mark.live_gemini`. |
| Logs | Helpers redact keys and truncate identifiers; no full banking payloads. |

## Markers

- `live` — external read/sync/API contracts (`--run-live`)
- `live_gemini` — Gemini network calls
- `destructive` — sandbox mutation (`--run-destructive`)
