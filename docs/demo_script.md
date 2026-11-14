# PulseGuard — Hackathon Demo Script (~4–5 minutes)

**Prep (before judges):** API running (`USE_MOCK_DATA=false` for live, or mock for safety). iOS Simulator open. Demo Mode writes only if `ALLOW_NESSIE_WRITES=true` **and** you intend to mutate the sandbox — otherwise stay on sample data / What If? (no bank writes).

---

### 1. Connect a simulated customer (20s)
- Launch PulseGuard → **Continue with sample data** (or optional bank link for live).
- Land on **Home**. Say: *“PulseGuard is a personal finance coach — Nessie is just the simulated bank behind the scenes.”*

### 2. Financial health & spending patterns (45s)
- Point to **Safe to spend**, **Cash runway**, and health chip (On track / Watch / At risk).
- Flip **7 / 14 / 30** day horizon — one chart, bill (orange) and payday (teal) markers.
- Scroll **Upcoming bills** and **Recent activity**.
- Open **Insights** → spending behavior + trend. Note AI labels when coaching is AI-generated.

### 3. Predictive warning (30s)
- On Home or Insights, highlight an alert (*Needs attention*).
- Explain: *“Alerts are computed in Python from balances, bills, and history — AI only explains them.”*

### 4. Ask Gemini if a purchase is affordable (45s)
- Insights → Copilot: *“Can I afford a $500 purchase?”* with scenario compare on.
- Show answer cites **safe-to-spend before/after**, min balance, overdraft — not a generic 20% cut.
- Optional: *“…in 5 days”* or an ISO date to show dated purchases.

### 5. Compare alternative futures (45s)
- **What If?** → Cut spending 20% vs Extra income vs Big purchase.
- Show **baseline (dashed) vs simulated (solid)** on the same chart.
- Read the **Recommendation** — actionable, tied to runway deltas.
- Emphasize: *“Hypothetical only — your bank is unchanged.”*

### 6. Simulate paycheck or purchase (40s)
- **Settings → Demo Mode** → *Received paycheck* or *Bought groceries*.
- Show **before/after** safe-to-spend and health.
- Return to Home — numbers refresh via shared update notification.

### 7. Closing (20s)
- *“Nessie supplies the bank truth, TigerData powers historical analytics, Gemini narrates grounded metrics — Python owns every dollar.”*

---

**If Demo Mode writes are off:** skip step 6 bank mutations; use What If? purchase + Insights Copilot only.
