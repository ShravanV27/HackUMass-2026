# Live integration tests

Opt-in suite under `backend/tests/live/`. Ordinary `pytest` **deselects** these tests so the offline suite stays credential-free.

Demo sandbox resets belong **only** in:

```bash
python scripts/reset_demo.py --reset
```

Live tests never call `DELETE /data`, start simulations, or wipe `DATABASE_URL`.

## Commands

### A. Offline suite (default — no external APIs)

```bash
source .venv/bin/activate
cd backend
pytest
```

### B. Live integration (`--run-live`)

Requires `.env` credentials from a prior demo setup (`NESSIE_*`, and optionally `GEMINI_*`).

TigerData live tests also require an **isolated** database:

```bash
export LIVE_TEST_DATABASE_URL='postgresql://…'   # must differ from DATABASE_URL
```

```bash
source .venv/bin/activate
cd backend
pytest --run-live
```

This runs the full live suite. Gemini tests run automatically when `GEMINI_API_KEY` is set; otherwise they skip with a clear message.

## Isolation rules

| Resource | Policy |
|----------|--------|
| TigerData | Requires `LIVE_TEST_DATABASE_URL` ≠ `DATABASE_URL`. Rows use customer `live-test-customer-001` and are deleted after tests. Never truncates demo tables. |
| Nessie | Read-only GETs against the configured sandbox customer. Bounded retries on HTTP 429. |
| Gemini | Included with `--run-live` when `GEMINI_API_KEY` is present. |
| Demo reset | Only `scripts/reset_demo.py --reset` (or `--resume`). |

## Markers

- `live` — external read/sync/API contracts (`--run-live`)
- `live_gemini` — Gemini network calls (auto-included with `--run-live` when configured)
