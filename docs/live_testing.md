# Live integration tests

Opt-in suite under `backend/tests/live/`. Ordinary `pytest` **deselects** these tests so the offline suite stays credential-free.

Sandbox resets belong **only** in:

```bash
python scripts/reset_demo.py --reset
```

Live tests never call `DELETE /data`, start simulations, or wipe `DATABASE_URL`.

## Commands

### Offline (default)

```bash
source .venv/bin/activate
cd backend
pytest
```

### Live (`--run-live`)

Requires `.env` credentials (`NESSIE_*`, optional `GEMINI_*`) and an **isolated** TigerData database:

```bash
export LIVE_TEST_DATABASE_URL='postgresql://…'   # must differ from DATABASE_URL
source .venv/bin/activate
cd backend
pytest --run-live
```

Gemini cases run when `GEMINI_API_KEY` is set; otherwise they skip with a clear message.

## Isolation rules

| Resource | Policy |
|----------|--------|
| TigerData | `LIVE_TEST_DATABASE_URL` ≠ `DATABASE_URL`. Rows use `live-test-customer-001` and are deleted afterward. Never truncates demo tables. |
| Nessie | Read-only GETs against the configured sandbox customer. Bounded retries on HTTP 429. |
| Gemini | Included with `--run-live` when `GEMINI_API_KEY` is present. |
| Demo reset | Only `scripts/reset_demo.py --reset` or `--resume`. |

## Markers

- `live` — external read/sync/API contracts (`--run-live`)
- `live_gemini` — Gemini network calls (auto-included with `--run-live` when configured)

## Manual Demo Mode verification

Offline tests cover mock writes + mocked Nessie. For a real sandbox check (does **not** DELETE/reset):

1. `USE_MOCK_DATA=false`, `ALLOW_NESSIE_WRITES=true`, valid `NESSIE_*` + `DATABASE_URL`.
2. `POST /session/bind-customer` with an existing Nessie customer id (or iOS connect field).
3. `POST /customers/demo-customer-001/demo/deposit` → `delta_safe_to_spend > 0`, `synced: true`.
4. `POST .../demo/purchase` → safe-to-spend / runway drop; overdraft risk may worsen.
5. `POST .../demo/bill` → scheduled bills / runway update after sync.
6. Confirm `/predictions/scenarios` still creates no Nessie resources.

General setup: [`setup.md`](setup.md).
