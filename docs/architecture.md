# Architecture (M0–M5)

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

Live demo data is created only by `scripts/reset_demo.py` (historical `POST /simulations`, then TigerData sync).

## SQL analytics

`GET /customers/{id}/analytics/spending` uses TigerData SQL in live mode (category rollups + daily series; continuous aggregate `daily_spending` when Timescale supports it) and in-memory aggregation in mock mode.

## M5 predictions

Routes under `/customers/{id}/predictions/*` (existing `/forecast` unchanged):

- `GET .../cashflow` — 14-day runway with **scheduled bills** vs **estimated spend**
- `GET .../safe-to-spend` — buffer-aware discretionary capacity (cash accounts only)
- `GET .../drift` — recent vs baseline spend
- `POST .../what-if` — affordability simulation

Scheduled bills: mock fixtures in mock mode; live mode maps Nessie bills (`upcoming_payment_date` / `recurring_date`). Empty bills/history are valid and never invented. Credit-card balances are excluded from spendable cash. Transfers and pending items are excluded from estimated spend / drift.

## CORS

Native SwiftUI does not need browser CORS. CORS middleware is not enabled.
