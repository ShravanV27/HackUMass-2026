"""FastAPI entrypoint for the personal finance prototype."""

from __future__ import annotations

from fastapi import FastAPI

from app.api.routes import (
    accounts,
    analytics,
    demo,
    health,
    insights,
    predictions,
    settings,
    sync,
    transactions,
)

app = FastAPI(
    title="HackUMass Predictive Finance API",
    version="0.1.0",
    description=(
        "Mock-first personal finance API. Native SwiftUI clients do not need "
        "browser CORS; CORS is intentionally omitted until a browser client exists."
    ),
)

app.include_router(health.router)
app.include_router(accounts.router)
app.include_router(transactions.router)
app.include_router(analytics.router)
app.include_router(predictions.router)
app.include_router(settings.router)
app.include_router(sync.router)
app.include_router(demo.router)
app.include_router(insights.router)
