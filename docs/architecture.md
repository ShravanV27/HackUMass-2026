# Architecture

Centsible is a **mock-first** predictive finance stack: SwiftUI talks to FastAPI; FastAPI owns money math; Nessie and TigerData supply live sandbox history when enabled; Gemini only narrates.

## Repository layout

| Path | Role |
|------|------|
| `backend/` | FastAPI app, domain math, mock/live providers |
| `ios/FinanceApp/` | SwiftUI Simulator client (display name **Centsible**) |
| `scripts/reset_demo.py` | Live demo sandbox reset (Nessie + TigerData) |
| `test_nessie.py`, `test_gemini.py`, `test_tigerdata.py` | Optional root smoke scripts (main suite is `backend/tests/`) |

## Data flow

```text
Customer (iOS)
    │  HTTPS-local → 127.0.0.1:8000
    ▼
FastAPI routes
    │
    ├─ MockDataProvider  ← fixtures + in-memory Demo Mode overlays
    │
    └─ LiveDataProvider
           │
           ├─ Nessie GET (accounts, transactions, bills)
           ├─ sync → TigerData upsert (accounts + transactions hypertable)
           ├─ PredictiveEngine / alerts / analytics (Python)
           └─ Gemini (optional) ← precomputed metrics only
```

**Rules of the road**

- Path `customer_id` must match the server’s resolved customer (`DEMO_CUSTOMER_ID`, default `demo-customer-001`). It is an authorization check, not authentication.
- Session bind (`POST /session/bind-customer`) maps that app id → a Nessie sandbox customer **in process** (lost on restart; does not rewrite `.env`).
- Credit-card balances are excluded from spendable cash. Transfers and pending items are excluded from estimated spend / drift.
- Empty bills or history are valid; the API does not invent them.
- Future income enters forecasts **only** when payment-cycle settings define a paycheck.
- Native SwiftUI does not need browser CORS; CORS middleware is not enabled.

## Component roles

### Nessie (simulated bank)

Capital One’s **Nessie v2** API provides synthetic customers, accounts, transactions, and bills.

- Live reads: accounts, `/accounts/{id}/transactions`, bills.
- Demo Mode writes (when `ALLOW_NESSIE_WRITES=true`): purchase, deposit, bill — then force-sync to TigerData.
- Destructive sandbox resets happen **only** via `scripts/reset_demo.py`, never via the FastAPI Demo Mode routes.
- Nessie is infrastructure, not the product brand.

**Not in scope today:** real Capital One customer accounts or an official Capital One integration.

### TigerData (time-series analytics)

Postgres/Timescale database pointed at by `DATABASE_URL`.

- Idempotent sync from Nessie into `accounts` + `transactions` (hypertable required in live sync).
- SQL spending rollups and category windows for analytics / alerts.
- Continuous aggregate `daily_spending` when Timescale supports it (best-effort).
- Live discretionary spend history may feed the predictive engine.

### FastAPI + domain math

Source of truth for every dollar figure.

| Concern | Module area |
|---------|-------------|
| Predictive engine | `backend/app/domain/predictive_engine.py` |
| Alerts | `backend/app/domain/alerts.py` |
| Money-flow helpers | `backend/app/domain/calculations.py`, `predictions.py` |
| Insights / Copilot orchestration | `backend/app/services/insights_service.py` |
| Sync | `backend/app/services/nessie_sync.py` |

Gemini is never used for numerical forecasts.

### Gemini (narration)

Optional in live mode when `GEMINI_API_KEY` is set (default model `gemini-3.5-flash-lite`).

- Alert briefing, behavior coaching, Copilot answers.
- Responses are labeled (`ai_generated`, `model`).
- Mock mode returns deterministic prose (`ai_generated=false`).
- Copilot stays grounded in prediction-engine metrics and refuses out-of-scope asks.

### SwiftUI (Centsible)

| Tab | Purpose |
|-----|---------|
| **Home** | Health, safe-to-spend, runway, cashflow (7/14/30), bills, cash accounts, activity, alert strip |
| **Insights** | Behavior, drift, alerts, coaching + Copilot |
| **What If?** | `POST .../predictions/scenarios` — baseline vs simulated; no bank writes |
| **Settings** | Payment cycle, safety buffer, Demo Mode, developer details |

Onboarding: sample data, or optional bind to a Nessie sandbox customer id for live demos.

## API surface (working)

Prefix: `/customers/{customer_id}` unless noted.

| Method | Path | Notes |
|--------|------|-------|
| GET | `/health` | Liveness |
| GET | `.../accounts` | Accounts |
| GET | `.../transactions` | Transactions |
| GET | `.../money-flow` | Aggregated flow |
| GET | `.../forecast` | Legacy forecast |
| GET | `.../dashboard` | Money-flow + insight string (API available; iOS Home uses granular prediction routes instead) |
| GET | `.../analytics/spending` | Category / daily series |
| GET | `.../predictions/cashflow` | Runway + scheduled bills vs estimated spend |
| GET | `.../predictions/safe-to-spend` | Buffer-aware discretionary capacity |
| GET | `.../predictions/drift` | Recent vs baseline spend |
| POST | `.../predictions/what-if` | Single purchase affordability |
| POST | `.../predictions/scenarios` | Multi-future overlays |
| GET | `.../predictions/overdraft-risk` | Min balance, days-to-zero, cutback hint |
| GET | `.../predictions/behavior` | Rule-based segment + tips |
| GET | `.../predictions/backtest` | Historical forecast vs actual (**API only**; not in iOS UI) |
| GET/PUT | `.../settings/payment-cycles` | Payday / paycheck / bill-day overrides |
| POST | `.../sync` | Force Nessie → TigerData refresh |
| GET | `.../alerts` | Deterministic alerts |
| GET | `.../insights` | Alerts + coaching |
| POST | `.../copilot` | Grounded Q&A |
| GET | `.../demo/status` | Demo Mode status |
| POST | `.../demo/{purchase\|deposit\|bill}` | Sandbox writes (gated) |
| GET | `/session` | Mode + bind status |
| POST | `/session/bind-customer` | Map Nessie id for this process |

Cashflow / scenarios / what-if responses include `assumptions[]` and `confidence` so the UI does not present estimates as guarantees. Dated hypothetical purchases are supported via scenario / what-if request fields and Copilot date parsing.

## Mock vs live

| Mode | `USE_MOCK_DATA` | Behavior |
|------|-----------------|----------|
| Mock (default) | `true` | Fixture customer; in-memory Demo Mode; no external APIs |
| Live | `false` | Nessie + TigerData required; Gemini optional |

## Limitations (prototype)

- Single demo customer; no Sign in with Apple / OAuth yet (hook reserved in `get_current_customer_id()`).
- Session bind is ephemeral.
- Nessie simulation history can look irregular (fraud-oriented patterns); interpret drift carefully after `reset_demo.py`.
- No official Capital One production integration.
