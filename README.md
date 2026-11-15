# HackUMass-2026

**Centsible** — predictive personal finance (SwiftUI + FastAPI) using Nessie v2 as the simulated bank, TigerData analytics, and Gemini coaching. Mock data is the default so the iOS Simulator works without live credentials.

## Layout

- `backend/` — FastAPI app, domain math, mock/live providers
- `ios/FinanceApp/` — SwiftUI Simulator client
- `scripts/reset_demo.py` — one-command live demo setup
- `.env` — local secrets (gitignored); see `.env.example`

## 1. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r backend/requirements-dev.txt   # pytest + httpx
cp .env.example .env   # then fill in keys for live mode
```

## 2. One-command demo setup (live sandbox)

Preview (no writes):

```bash
source .venv/bin/activate
python scripts/reset_demo.py --dry-run
```

Reset designated Nessie sandbox + HackUMass TigerData (`DATABASE_URL`), simulate ~75 days of history, sync, and verify predictions:

```bash
python scripts/reset_demo.py --reset
```

If Nessie succeeds but TigerData sync fails:

```bash
python scripts/reset_demo.py --resume
```

`--reset` / `--resume` are sufficient confirmation for the disposable hackathon environments. No extra `CONFIRM_*` variables.

## 3. Start FastAPI

Mock mode (default):

```bash
source .venv/bin/activate
cd backend
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Health: `GET http://127.0.0.1:8000/health`  
Demo customer: `demo-customer-001`

## 4. Run the SwiftUI app

1. Start the backend on `127.0.0.1:8000`.
2. Open `ios/FinanceApp/FinanceApp.xcodeproj` in Xcode.
3. Select an iPhone Simulator and Run.

The app calls `GET /customers/demo-customer-001/dashboard` and M5 prediction endpoints under `/predictions/*`.

## 5. Automated tests

```bash
source .venv/bin/activate
cd backend

# Offline suite (default — no external APIs)
pytest

# Live suite (read-only Nessie + isolated TigerData; Gemini when keyed)
# Requires LIVE_TEST_DATABASE_URL ≠ DATABASE_URL
pytest --run-live
```

Details: [`docs/live_testing.md`](docs/live_testing.md).

## 6. Mock vs live mode

| Mode | `.env` | Notes |
|------|--------|--------|
| Mock | `USE_MOCK_DATA=true` | Fixture data; no Nessie/TigerData/Gemini required |
| Live | `USE_MOCK_DATA=false` | Needs `NESSIE_*`, `DATABASE_URL`, optional `GEMINI_*` |

Live mode maps `NESSIE_CUSTOMER_ID` → `DEMO_CUSTOMER_ID`. After a reset, keep `DEMO_CUSTOMER_ID=demo-customer-001` and let `reset_demo.py` update `NESSIE_CUSTOMER_ID`.

### Hackathon demo

- Judge walkthrough: [`docs/demo_script.md`](docs/demo_script.md)
- Architecture / sponsor roles: [`docs/architecture.md`](docs/architecture.md)

### Centsible iOS tabs (Phase 4)

- **Home** — health, safe-to-spend, runway, one cashflow forecast (7/14/30), bills, activity
- **Insights** — behavior, drift, alerts, Gemini coaching + Copilot
- **What If?** — baseline vs simulated futures (`/predictions/scenarios`); never writes bank data
- **Settings** — payment cycle, safety buffer, **Demo Mode** (purchases/deposits/bills), Developer details

### Insights + Copilot (Phase 3)

- `GET /alerts` — deterministic overdraft / drift / category-spike / bill-cluster alerts
- `GET /insights` — alert briefing + coaching (`ai_generated` when Gemini is used in live mode)
- `POST /copilot` — grounded Q&A over prediction-engine metrics (optional scenario math)

### Demo Mode (Phase 2 lab, relocated in Phase 4)

Settings → **Demo Mode** + `POST /customers/{id}/demo/{purchase|deposit|bill}` create real sandbox writes (or mock in-memory mutations), force-sync TigerData, and return before/after impact. Hypothetical `/predictions/scenarios` stays on **What If?**.

Enable writes explicitly:

```bash
ALLOW_NESSIE_WRITES=true
```

Never deletes or resets Nessie data. Bind an existing sandbox customer via the onboarding screen or `POST /session/bind-customer`.

### Environment variables

| Variable | Purpose |
|----------|---------|
| `USE_MOCK_DATA` | Provider mode |
| `DEMO_CUSTOMER_ID` | App customer id (stable) |
| `NESSIE_CUSTOMER_ID` | Nessie sandbox customer |
| `NESSIE_API_KEY` / `NESSIE_BASE_URL` | Nessie v2 access |
| `DATABASE_URL` | HackUMass TigerData database |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | Optional narration |

Test-only: `LIVE_TEST_DATABASE_URL` (isolated DB for `pytest --run-live`).

## Notes

- Do not commit `.env` or API keys.
- Native SwiftUI does not need browser CORS.
- Nessie historical simulation may produce irregular (fraud-oriented) activity rather than household spending; predictions still run, but interpret drift carefully. See the summary printed by `reset_demo.py`.
- Architecture overview: [`docs/architecture.md`](docs/architecture.md)
