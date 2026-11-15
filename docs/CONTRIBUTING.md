# Contributing

Thanks for helping improve Centsible. Keep changes small, accurate, and demo-safe.

## Before you start

1. Read the [README](../README.md) and [setup](setup.md).
2. Run the offline suite: `cd backend && pytest`.
3. Prefer **mock mode** (`USE_MOCK_DATA=true`) for everyday development.

## What to change where

| Kind of work | Where |
|--------------|--------|
| Money math, alerts, scenarios | `backend/app/domain/` |
| HTTP routes / schemas | `backend/app/api/`, `backend/app/models/` |
| Nessie / TigerData / Gemini clients | `backend/app/services/` |
| iOS UX | `ios/FinanceApp/FinanceApp/Features/` |
| Docs only | `README.md`, `docs/` |

Do not commit `.env`, API keys, or real credentials. Use `.env.example` for new variable names.

## Safety rules

- **Demo Mode** may write to Nessie only when `ALLOW_NESSIE_WRITES=true`. Never add DELETE/purge paths to the FastAPI demo routes.
- Sandbox wipes belong exclusively in `scripts/reset_demo.py`.
- Live tests must use `LIVE_TEST_DATABASE_URL` ≠ `DATABASE_URL` and must not wipe the demo database.
- Gemini must not invent balances or execute transfers — pass precomputed metrics only.
- Document limitations honestly; do not claim real Capital One account access.

## Pull requests

- Keep PRs focused (one concern when possible).
- Include or update tests for backend behavior changes.
- Update docs when you change setup, env vars, API paths, or user-visible behavior.
- Note mock vs live impact in the PR description.

## Style

- Match existing naming and file layout.
- Prefer clear names over abbreviations in new public APIs.
- Customer-facing copy uses **Centsible** and **Demo Mode**; backend module names may still say “Demo Lab.”
