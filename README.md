# HackUMass-2026

Predictive personal finance prototype (SwiftUI + FastAPI). Mock data is the default so the iOS Simulator demo works without Nessie or Gemini.

## Layout

- `backend/` — FastAPI app, domain math, mock/live providers
- `ios/FinanceApp/` — SwiftUI Simulator client
- Root `test_*.py` — existing live smoke scripts (unchanged)
- `.env` — local secrets (gitignored); see `.env.example`

## Backend (mock mode)

```bash
source .venv/bin/activate
pip install -r backend/requirements-dev.txt   # pytest + httpx for tests only
cd backend
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Health check: `GET http://127.0.0.1:8000/health`

Demo customer id: `demo-customer-001` (server-resolved; path must match).

## Tests

```bash
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
cd backend
pytest
```

## iOS Simulator

1. Start the backend on `127.0.0.1:8000` (mock mode).
2. Open `ios/FinanceApp/FinanceApp.xcodeproj` in Xcode.
3. Select an iPhone Simulator and Run.

The app calls `GET /customers/demo-customer-001/dashboard` and M5 prediction endpoints under `/predictions/*`.

## Live mode (M4 — optional)

Keep `USE_MOCK_DATA=true` for day-to-day Simulator work. To enable live integrations locally:

1. Set `USE_MOCK_DATA=false` in `.env` (not committed).
2. Provide `NESSIE_API_KEY`, `NESSIE_BASE_URL`, optional `NESSIE_CUSTOMER_ID`.
3. Provide `DATABASE_URL` (TigerData / TimescaleDB).
4. Provide `GEMINI_API_KEY` and `GEMINI_MODEL` for dashboard narration.

The API only performs Nessie **GET** reads, upserts into TigerData, and asks Gemini to explain **precomputed** metrics. Root `test_*.py` smoke scripts are unchanged.

## Notes

- `USE_MOCK_DATA=true` by default (see `.env.example`).
- No browser CORS middleware — native SwiftUI does not need it.
- Do not commit `.env` or API keys. Keys stay server-side only.
