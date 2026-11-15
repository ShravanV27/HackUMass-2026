# Centsible

**A financial intelligence companion** that helps you see safe-to-spend, cash runway, and what-if outcomes *before* money moves.

Centsible is a HackUMass prototype of how a banking partner (for example Capital One) could offer predictive coaching on top of customer-authorized account data. Today it runs against **Nessie**, Capital One’s synthetic banking API — not real customer accounts.

---

## The problem

Most banking apps show balances and past transactions. They rarely answer the questions that matter day to day:

- How much can I safely spend this week?
- Will this bill cluster push me close to zero?
- What if I cut spending 20%, or buy something next Friday?

Centsible turns account history, bills, and simple preferences into **grounded forecasts and alerts**, then uses Gemini only to *explain* those numbers — never to invent balances or move money.

---

## What works today

| Area | What you get |
|------|----------------|
| **Home** | Safe-to-spend, cash runway, health status, 7/14/30-day cashflow, bills, accounts, recent activity, attention alerts |
| **Insights** | Spending behavior, drift, deterministic alerts, Gemini coaching + Copilot (when keyed in live mode) |
| **What If?** | Multi-scenario futures vs baseline — hypothetical only; never writes bank data |
| **Settings** | Payment cycle, safety buffer, **Demo Mode** (sandbox purchases/deposits/bills), developer details |

**Honest limits**

- Prototype / hackathon demo — single demo customer, no production auth.
- **Nessie = synthetic data.** We do **not** access real Capital One customer accounts or have an official Capital One product integration.
- Gemini narrates precomputed metrics; Python owns every dollar figure.
- Forecast backtest exists as an API (`GET .../predictions/backtest`) but is not yet exposed in the iOS UI.
- Mock mode is the default so the Simulator works with zero external credentials.

---

## App overview

Four tabs in the SwiftUI Simulator client:

1. **Home** — financial health at a glance  
2. **Insights** — alerts, coaching, Copilot Q&A  
3. **What If?** — compare alternative futures  
4. **Settings** — preferences + Demo Mode lab  

---

## How it fits together

```text
┌─────────────┐     sync      ┌──────────────┐
│   Nessie    │ ───────────►  │  TigerData   │
│ (sim bank)  │               │ (time-series)│
└─────────────┘               └──────┬───────┘
                                     │
                              ┌──────▼───────┐
                              │   FastAPI    │
                              │ domain math  │
                              └──────┬───────┘
                        ┌────────────┼────────────┐
                        ▼            ▼            ▼
                   Predictions    Alerts     Gemini narration
                        └────────────┬────────────┘
                                     ▼
                              SwiftUI (Centsible)
```

| Piece | Role |
|-------|------|
| **Nessie** | Simulated bank: accounts, transactions, bills (read; optional Demo Mode writes) |
| **TigerData** | Postgres/Timescale store for synced history and SQL spending analytics |
| **FastAPI** | API + predictive engine (source of truth for money math) |
| **Gemini** | Optional coaching/Copilot prose over already-computed metrics |
| **SwiftUI** | Customer-facing iOS Simulator app |

More detail: [`docs/architecture.md`](docs/architecture.md).

---

## Tech stack

- **iOS:** SwiftUI (iOS 17+), Xcode project `ios/FinanceApp/`
- **API:** FastAPI + Uvicorn, Pydantic
- **Data:** Nessie v2 API, TigerData (Postgres/Timescale via `psycopg`)
- **AI:** Google Gemini (`google-genai`, default model `gemini-3.5-flash-lite`)
- **Tests:** pytest (+ httpx for API tests)

---

## Quick start (mock mode)

No Nessie, TigerData, or Gemini keys required.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r backend/requirements-dev.txt
cp .env.example .env   # USE_MOCK_DATA=true by default

cd backend
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Then open `ios/FinanceApp/FinanceApp.xcodeproj` in Xcode, pick an iPhone Simulator, and Run. Choose **Continue with sample data** on the welcome screen.

Health check: `GET http://127.0.0.1:8000/health`  
Demo customer id: `demo-customer-001`

Full install, live sandbox, env vars, and tests: [`docs/setup.md`](docs/setup.md).

---

## Demo

<!-- Replace with your demo link -->
**Demo video / walkthrough:** `youtube.com/shorts/dLe3wYw77Ik?feature=share`

Live sandbox reset (optional):

```bash
python scripts/reset_demo.py --dry-run   # preview
python scripts/reset_demo.py --reset     # Nessie + TigerData
python scripts/reset_demo.py --resume    # TigerData only after a partial reset
```

---

## Documentation

| Doc | Contents |
|-----|----------|
| [`docs/architecture.md`](docs/architecture.md) | Data flow, component roles, API surface, limitations |
| [`docs/setup.md`](docs/setup.md) | Install, environment, mock vs live, testing |
| [`docs/live_testing.md`](docs/live_testing.md) | Opt-in live integration tests |
| [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) | How to contribute safely |

---

## Product vision

Centsible sketches a **customer-authorized financial intelligence layer**: a bank (or partner) could connect real accounts with consent, keep deterministic risk and cashflow math in a trusted backend, and use generative AI only for explanation and coaching.

This repo demonstrates that shape with synthetic Nessie data and a Simulator client — a prototype, not a production banking product.
