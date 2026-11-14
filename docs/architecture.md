# Architecture (M0–M4)

## Layout

- `backend/` — FastAPI API, domain math, mock/live providers
- `ios/FinanceApp/` — SwiftUI Simulator client
- Root `test_*.py` — existing live smoke scripts (unchanged)

## Auth posture

The server resolves the current customer from server-side context (fixed `DEMO_CUSTOMER_ID` today). Path `customer_id` values are authorization checks only — they must match the resolved customer. Client-supplied IDs are never treated as authentication. Ready for future Sign in with Apple token validation in `get_current_customer_id()`.

## Providers

`USE_MOCK_DATA=true` (default) uses fixtures. Live mode (`USE_MOCK_DATA=false`):

1. **Nessie** (GET only) — accounts, purchases, deposits, withdrawals, transfers
2. **TigerData** — idempotent upsert into `accounts` + `transactions` hypertable
3. **Domain math** — still the source of truth for dashboard money-flow / forecast
4. **Gemini** — narrates precomputed metrics via `GEMINI_MODEL` (does not invent numbers)

Map Nessie sandbox customers with optional `NESSIE_CUSTOMER_ID` → app `DEMO_CUSTOMER_ID`.

## SQL analytics

`GET /customers/{id}/analytics/spending` uses TigerData SQL in live mode and in-memory aggregation in mock mode.

## CORS

Native SwiftUI does not need browser CORS. CORS middleware is not enabled.
