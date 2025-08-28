
# Real Estate Lead-Gen MVP — Samson Properties (PG County)

A self-contained prototype you can run locally. It captures seller/buyer leads, computes a **mock home valuation** with PG County ZIP‑specific PPSF, scores leads, and shows a **dashboard**.

## Quick Start (Windows & Mac)

1) **Install Python 3.10+**.

2) In a terminal, go to the project root (the folder that contains `server/` and `web/`):
```
cd realestate-mvp
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r server/requirements.txt
python -m uvicorn server.app:app --reload --port 8000
```

3) Open your browser to: **http://localhost:8000**

### What you get
- **Sell** tab: "What's my home worth?" → instant **estimate** + CTA to request in-person valuation (creates a Seller lead).
- **Buy** tab: capture buyer criteria (creates a Buyer lead).
- **Dashboard** tab: list/search leads, update stages, view scores, and events.

### Notes
- This is an MVP with a mock valuation model using ZIP-based price-per-sqft heuristics (see `server/comps.csv`). If a ZIP isn't found, it falls back to a national default.
- Email/SMS integrations are stubbed (log-only). You can wire Twilio/SendGrid where indicated.
- Data lives in a local **SQLite** file (`server/app.db`).

### Next steps / Customization
- Connect MLS/IDX to replace the mock comps and power saved searches.
- Plug in Twilio/SendGrid for real messaging.
- Deploy the backend (Render/Fly/Heroku/AWS) and front-end (Vercel/Netlify) when you're ready.
- Add ad platform webhooks (FB/IG Lead Ads, Google Lead Forms) to post leads directly to `/api/leads`.

— Updated on 2025-08-28
