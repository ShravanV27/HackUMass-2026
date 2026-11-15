# Architecture (M0–M5 + Phase 1–4)

## Layout

- `backend/` — FastAPI API, domain math, mock/live providers
- `ios/FinanceApp/` — SwiftUI Simulator client
- Root `test_*.py` — existing live smoke scripts (unchanged)

## Auth posture

The server resolves the current customer from server-side context (fixed `DEMO_CUSTOMER_ID` today). Path `customer_id` values are authorization checks only — they must match the resolved customer. Client-supplied IDs are never treated as authentication. Ready for future Sign in with Apple token validation in `get_current_customer_id()`.

## Providers

`USE_MOCK_DATA=true` (default) uses fixtures. Live mode (`USE_MOCK_DATA=false`):

1. **Nessie** (GET only) — accounts + `/accounts/{id}/transactions` (v2) + bills (per-account / fallback)
2. **TigerData** — idempotent upsert into `accounts` + `transactions` hypertable (required; no silent table fallback)
3. **Domain math** — source of truth for dashboard money-flow / M5 forecasts
4. **Gemini** — narrates precomputed metrics via `GEMINI_MODEL` (does not invent numbers or execute transfers)

Map Nessie sandbox customers with optional `NESSIE_CUSTOMER_ID` → app `DEMO_CUSTOMER_ID`.

Live demo data is created by `scripts/reset_demo.py` (historical `POST /simulations`, then TigerData sync). **Demo Lab** (`/demo/*`, gated by `ALLOW_NESSIE_WRITES`) can also POST purchases/deposits/bills to an existing sandbox customer, then force-sync TigerData — it never DELETE/purges Nessie. Hypothetical `/predictions/scenarios` stays separate from Demo Lab writes. Session bind: `POST /session/bind-customer` maps a Nessie customer id in-process (no `.env` rewrite).

## SQL analytics

`GET /customers/{id}/analytics/spending` uses TigerData SQL in live mode (category rollups + daily series; continuous aggregate `daily_spending` when Timescale supports it) and in-memory aggregation in mock mode.

## M5 + Phase 1 PredictiveEngine

Routes under `/customers/{id}/predictions/*` (existing `/forecast` unchanged):

- `GET .../cashflow` — runway with **scheduled bills** vs **weekday-aware estimated spend**; supports `horizon=week|month` (7/30) or `horizon_days`
- `GET .../safe-to-spend` — buffer-aware discretionary capacity (cash accounts only)
- `GET .../drift` — recent vs baseline spend
- `POST .../what-if` — affordability simulation
- `GET .../overdraft-risk` — min balance, days-to-zero, suggested daily cutback
- `GET .../behavior` — rule-based segment (`steady|spiky|weekend_heavy|bill_stressed`) + tips
- `POST .../scenarios` — multi-future overlays (cut 20%, aggressive save, pause category, etc.)
- `GET/PUT .../settings/payment-cycles` — payday cadence / paycheck / bill-day overrides
- `POST .../sync` — force Nessie→TigerData refresh (no Nessie writes/deletes)

Core math lives in `domain/predictive_engine.py`. Recurring bills are rolled across the horizon by cadence. Future income is included **only** when payment-cycle settings define a paycheck (otherwise `income_assumed=false`). Live mode may read discretionary daily spend from TigerData SQL; weekly/daily continuous aggregates remain best-effort. Gemini is never used for numerical forecasts.

## Phase 3 — Alerts + Gemini Copilot

- `GET /customers/{id}/alerts` — deterministic overdraft, spending drift, category spikes (Tiger SQL category windows in live / memory in mock), bill clusters (`domain/alerts.py`)
- `GET /customers/{id}/insights` — alerts + optional Gemini alert briefing / behavior coaching (`ai_generated` + `model` labeled)
- `POST /customers/{id}/copilot` — grounded Q&A over prediction-engine metrics; can attach scenario deltas; refuses out-of-scope asks
- Mock mode returns deterministic prose (`ai_generated=false`). Live uses `GEMINI_MODEL` (default `gemini-3.5-flash-lite`) with fallbacks on failure.
- SwiftUI **Insights** tab shows alerts, AI labels, Copilot, and metric info buttons; Predictions shows an alerts banner + info affordances.

## Phase 4 — Centsible customer UX

Customer-facing SwiftUI app branded **Centsible** (not a Nessie developer console):

| Tab | Purpose |
|-----|---------|
| **Home** | Financial health, safe-to-spend (≥ $0), cash runway, single cashflow forecast (7/14/30), bills, accounts, recent activity, needs-attention alerts |
| **Insights** | Behavior, spending drift, alerts, Gemini coaching + Copilot (AI-labeled) — coaching lives here only |
| **What If?** | Multi-scenario simulator (`POST /predictions/scenarios`) with baseline vs simulated chart; purchase checks via `/what-if`; never writes bank data |
| **Settings** | Payment cycle + safety buffer; **Demo Mode** (former Demo Lab) for realistic purchases/deposits/bills; Developer details collapsed |

Demo Mode still uses `/demo/*` + sync; technical bank-link ids stay behind Developer Details / optional onboarding. All money math remains Python; Gemini only narrates.

## Phase 5 — Hardening & demo prep

- **Dated purchases:** `ScenarioSpec.on_date` / `WhatIfRequest.purchase_date` apply hypothetical spends on a chosen day (immediate if omitted). Copilot parses ISO dates, “in N days”, “tomorrow”, “next week”.
- **Assumptions & confidence:** cashflow / scenarios / what-if return `assumptions[]` + `confidence` so the UI never presents estimates as guarantees.
- **Backtest:** `GET /predictions/backtest` compares historical discretionary-spend forecasts to actual posted spend when enough history exists.
- **Demo artifacts:** [`docs/demo_script.md`](demo_script.md) (judge walkthrough) and sponsor roles below.

### Sponsor technology roles

| Sponsor | Role in Centsible |
|---------|--------------------|
| **Nessie** | Simulated customer bank — accounts, transactions, bills (read + gated Demo Mode writes). Not the product brand. |
| **TigerData** | Time-series store + SQL analytics for live spending windows, category spikes, and sync from Nessie. |
| **Gemini** | Narrates precomputed alerts, coaching, and Copilot answers. Never invents balances or executes transfers. |

Scheduled bills: mock fixtures in mock mode; live mode maps Nessie bills (`upcoming_payment_date` / `recurring_date`). Empty bills/history are valid and never invented. Credit-card balances are excluded from spendable cash. Transfers and pending items are excluded from estimated spend / drift.

## CORS

Native SwiftUI does not need browser CORS. CORS middleware is not enabled.
