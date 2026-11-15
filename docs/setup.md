# Setup & developer onboarding

## Prerequisites

- Python 3.11+ recommended (3.x with venv support)
- Xcode 15+ with an iOS 17+ Simulator
- Optional for live mode: Nessie API key, HackUMass TigerData `DATABASE_URL`, Gemini API key

## Install

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r backend/requirements-dev.txt   # pytest + httpx
cp .env.example .env
```

Edit `.env` only when you need live mode or Demo Mode writes. **Do not commit `.env` or API keys.**

## Environment variables

| Variable | Purpose | Default / notes |
|----------|---------|-----------------|
| `USE_MOCK_DATA` | `true` = fixtures; `false` = Nessie + TigerData | `true` |
| `DEMO_CUSTOMER_ID` | Stable app customer id | `demo-customer-001` |
| `NESSIE_API_KEY` | Nessie v2 access | required for live |
| `NESSIE_BASE_URL` | Nessie host | `https://v2-prod-api.nessieisreal.com` |
| `NESSIE_CUSTOMER_ID` | Sandbox customer mapped to `DEMO_CUSTOMER_ID` | set by `reset_demo.py --reset` |
| `DATABASE_URL` | TigerData / Timescale connection | required for live |
| `GEMINI_API_KEY` | Optional coaching / Copilot | optional |
| `GEMINI_MODEL` | Gemini model id | `gemini-3.5-flash-lite` |
| `ALLOW_NESSIE_WRITES` | Enable Demo Mode purchase/deposit/bill | `false` |
| `LIVE_TEST_DATABASE_URL` | Isolated DB for `pytest --run-live` | must ≠ `DATABASE_URL` |

## Mock vs live

| Mode | `.env` | Notes |
|------|--------|--------|
| **Mock** | `USE_MOCK_DATA=true` | Simulator works with zero external credentials |
| **Live** | `USE_MOCK_DATA=false` | Needs `NESSIE_*` + `DATABASE_URL`; Gemini optional |

Keep `DEMO_CUSTOMER_ID=demo-customer-001`. After a sandbox reset, let `reset_demo.py` update `NESSIE_CUSTOMER_ID`.

Demo Mode writes require `ALLOW_NESSIE_WRITES=true`. They never DELETE or purge Nessie; full resets are only via `scripts/reset_demo.py`.

## Run the API

```bash
source .venv/bin/activate
cd backend
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

- Health: `GET http://127.0.0.1:8000/health`
- Demo customer: `demo-customer-001`

## Run the iOS app

1. Start the backend on `127.0.0.1:8000`.
2. Open `ios/FinanceApp/FinanceApp.xcodeproj` in Xcode.
3. Select an iPhone Simulator and Run.
4. Choose **Continue with sample data**, or bind a Nessie customer id for a live demo.

The app loads Home via prediction and alert endpoints under `/customers/demo-customer-001/...` (not the legacy `/dashboard` aggregate alone).

## Live demo sandbox reset

Preview (no writes):

```bash
source .venv/bin/activate
python scripts/reset_demo.py --dry-run
```

Reset designated Nessie sandbox + TigerData (`DATABASE_URL`), simulate history, sync, verify predictions:

```bash
python scripts/reset_demo.py --reset
```

If Nessie succeeds but TigerData sync fails:

```bash
python scripts/reset_demo.py --resume
```

Nessie historical simulation may produce irregular activity; predictions still run, but interpret drift carefully. See the summary printed by the script.

## Tests

### Offline (default)

```bash
source .venv/bin/activate
cd backend
pytest
```

No external APIs. Live tests under `backend/tests/live/` are deselected.

### Live integration

Requires credentials and an **isolated** TigerData URL:

```bash
export LIVE_TEST_DATABASE_URL='postgresql://…'   # ≠ DATABASE_URL
cd backend
pytest --run-live
```

Details and isolation rules: [`live_testing.md`](live_testing.md).

## Manual Demo Mode check (live)

With `USE_MOCK_DATA=false` and `ALLOW_NESSIE_WRITES=true`:

1. `POST /session/bind-customer` with an existing Nessie customer id (or use the iOS connect field).
2. `POST /customers/demo-customer-001/demo/deposit` → expect safer spend / sync.
3. `POST .../demo/purchase` → expect runway / risk to tighten.
4. `POST .../demo/bill` → expect scheduled bills / runway to update after sync.
5. Confirm `POST .../predictions/scenarios` still creates no Nessie resources.

## Next reading

- [`architecture.md`](architecture.md) — component roles and API map
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — contribution norms
