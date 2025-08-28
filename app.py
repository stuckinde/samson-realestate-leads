# app.py — Samson Properties (PG County) one-file MVP — enhanced
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse, JSONResponse
from sqlmodel import SQLModel, Field, Session, create_engine, select
from typing import Optional, List, Dict
from datetime import datetime
import os, json

app = FastAPI(title="Samson Properties Lead-Gen", version="1.2")

# ---------- Config / Env ----------
ADMIN_KEY = os.getenv("ADMIN_KEY")  # set in Render to hide admin-only routes
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL")
GA_TAG = os.getenv("GA_TAG")              # e.g., G-XXXXXXX
FB_PIXEL = os.getenv("FB_PIXEL")          # e.g., 123456789

# DB path: if Render Disk mounted at /data, use it so leads persist
DB_PATH = "sqlite:////data/app.db" if os.path.isdir("/data") else "sqlite:///app.db"

# ---------- Database ----------
engine = create_engine(DB_PATH, echo=False)

class LeadBase(SQLModel):
    role: str                       # "seller" or "buyer"
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    zip_code: Optional[str] = None
    beds: Optional[int] = None
    baths: Optional[float] = None
    sqft: Optional[int] = None
    price_min: Optional[int] = None
    price_max: Optional[int] = None
    timeline: Optional[str] = None  # "0-3","3-6","6-12","12+"
    condition: Optional[str] = None # "Needs work"|"Average"|"Updated"|"Renovated"
    tags: Optional[str] = None
    stage: str = "New"              # pipeline stage
    consent_sms: bool = False
    consent_email: bool = False
    score: int = 0

class Lead(LeadBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

# Store ZIP overrides in DB (editable in Admin)
class ZipPPSF(SQLModel, table=True):
    zip: str = Field(primary_key=True)
    ppsf: float

def init_db():
    SQLModel.metadata.create_all(engine)

@app.on_event("startup")
def _on_start():
    init_db()

# ---------- Valuation ----------
# Base Prince George’s County PPSF (seed defaults). Admin overrides can replace these.
PG_PPSF_BASE: Dict[str, float] = {
    "20706":255, "20707":260, "20708":245, "20710":240, "20712":270,
    "20715":250, "20716":255, "20720":265, "20721":270, "20722":245,
    "20735":250, "20737":255, "20740":275, "20742":280, "20743":235,
    "20744":245, "20745":250, "20746":240, "20747":238, "20748":242,
    "20769":260, "20770":265, "20771":270, "20772":255, "20774":268,
    "20781":248, "20782":252, "20783":258, "20784":245, "20785":240
}
DEFAULT_PPSF = 220.0

# Cache built from base + DB overrides
def current_ppsf_map() -> Dict[str, float]:
    with Session(engine) as s:
        overrides = {row.zip: row.ppsf for row in s.exec(select(ZipPPSF)).all()}
    merged = PG_PPSF_BASE.copy()
    merged.update(overrides)
    return merged

CONDITION_ADJ = {
    "Needs work": -0.10,
    "Average": 0.0,
    "Updated": 0.05,
    "Renovated": 0.10
}

def estimate_value(zip_code: Optional[str], beds: Optional[int], baths: Optional[float], sqft: Optional[int], condition: Optional[str]):
    ppsf_map = current_ppsf_map()
    ppsf = ppsf_map.get((zip_code or "").strip(), DEFAULT_PPSF)
    sqft = max(600, (sqft or 1800))

    adj = 1.0
    if beds:
        adj += 0.08 if beds >= 5 else 0.05 if beds == 4 else 0.02 if beds == 3 else 0
    if baths:
        adj += 0.05 if (baths or 0) >= 3 else 0.03 if (baths or 0) >= 2 else 0
    if condition and condition in CONDITION_ADJ:
        adj += CONDITION_ADJ[condition]

    est = round(ppsf * sqft * adj)
    band = 0.05 if condition else 0.08  # tighter if condition provided
    return {
        "estimate": est,
        "low": int(est * (1 - band)),
        "high": int(est * (1 + band)),
        "ppsf_used": ppsf,
        "sqft_used": sqft,
        "adjustment": round(adj, 3),
        "band": band
    }

def compute_score(ld: Lead) -> int:
    s = 0
    if ld.phone: s += 15
    if ld.email: s += 10
    if ld.timeline in ("0-3","3-6"): s += 15
    if ld.role == "seller" and ld.address: s += 20
    if ld.consent_sms: s += 10
    if ld.consent_email: s += 5
    s += {"New":0,"Contacted":5,"Qualified":10,"Appointment":20,"Agreement":30,"Closed/Lost":0}.get(ld.stage,0)
    return int(s)

# ---------- Optional: Email notify via SendGrid ----------
def notify_email(subject: str, html: str):
    if not (SENDGRID_API_KEY and NOTIFY_EMAIL): 
        return
    try:
        import requests
        requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={"Authorization": f"Bearer {SENDGRID_API_KEY}", "Content-Type":"application/json"},
            json={
                "personalizations":[{"to":[{"email": NOTIFY_EMAIL}]}],
                "from":{"email":"no-reply@samsonprops-demo.com"},
                "subject":subject,
                "content":[{"type":"text/html","value":html}]
            },
            timeout=8
        )
    except Exception:
        pass

# ---------- API ----------
@app.get("/api/health")
def health():
    return {"ok": True, "service": "samson-leads", "version": app.version}

@app.post("/api/valuation")
def api_valuation(payload: dict):
    return {"ok": True, "valuation": estimate_value(
        payload.get("zip_code"), payload.get("beds"), payload.get("baths"), payload.get("sqft"), payload.get("condition")
    )}

@app.post("/api/leads")
def create_lead(payload: dict):
    with Session(engine) as s:
        lead = Lead(**payload)
        lead.updated_at = datetime.utcnow()
        lead.score = compute_score(lead)
        s.add(lead); s.commit(); s.refresh(lead)

        # notify (optional)
        notify_email(
            subject=f"[New {lead.role.title()} Lead] {lead.first_name or ''} {lead.last_name or ''}",
            html=f"""<h3>New {lead.role} lead</h3>
<p><b>Name:</b> {lead.first_name} {lead.last_name}<br>
<b>Email:</b> {lead.email} — <b>Phone:</b> {lead.phone}<br>
<b>ZIP:</b> {lead.zip_code} — <b>Timeline:</b> {lead.timeline or '—'}<br>
<b>Score:</b> {lead.score}</p>"""
        )
        return {"ok": True, "lead": lead.dict()}

@app.get("/api/leads")
def list_leads(q: Optional[str] = None, role: Optional[str] = None, stage: Optional[str] = None, x_admin_key: Optional[str] = Header(None)):
    if ADMIN_KEY and x_admin_key != ADMIN_KEY:
        raise HTTPException(401, "Unauthorized")
    with Session(engine) as s:
        rows = s.exec(select(Lead)).all()
        out = []
        for ld in rows:
            if role and ld.role != role: continue
            if stage and ld.stage != stage: continue
            if q:
                blob = " ".join([str(getattr(ld, k, "") or "") for k in ("first_name","last_name","email","phone","address","zip_code","tags","role","stage")]).lower()
                if q.lower() not in blob: continue
            out.append(ld.dict())
        out.sort(key=lambda x: (x.get("score",0), x.get("created_at","")), reverse=True)
        return {"ok": True, "items": out}

@app.patch("/api/leads/{lead_id}")
def update_lead(lead_id: int, payload: dict, x_admin_key: Optional[str] = Header(None)):
    if ADMIN_KEY and x_admin_key != ADMIN_KEY:
        raise HTTPException(401, "Unauthorized")
    with Session(engine) as s:
        ld = s.get(Lead, lead_id)
        if not ld: raise HTTPException(404, "Lead not found")
        for k,v in payload.items():
            if hasattr(ld,k): setattr(ld,k,v)
        ld.updated_at = datetime.utcnow()
        ld.score = compute_score(ld)
        s.add(ld); s.commit(); s.refresh(ld)
        return {"ok": True, "lead": ld.dict()}

# ----- Admin: PPSF overrides -----
def require_admin(x_admin_key: Optional[str]):
    if ADMIN_KEY and x_admin_key != ADMIN_KEY:
        raise HTTPException(401, "Unauthorized")

@app.get("/api/admin/ppsf")
def admin_get_ppsf(x_admin_key: Optional[str] = Header(None)):
    require_admin(x_admin_key)
    data = current_ppsf_map()
    with Session(engine) as s:
        overrides = {row.zip: row.ppsf for row in s.exec(select(ZipPPSF)).all()}
    return {"ok": True, "ppsf": data, "overrides": overrides}

@app.post("/api/admin/ppsf")
def admin_set_ppsf(item: dict, x_admin_key: Optional[str] = Header(None)):
    require_admin(x_admin_key)
    z = (item.get("zip") or "").strip()
    p = float(item.get("ppsf"))
    if not z or len(z) != 5:
        raise HTTPException(400, "zip required (5 digits)")
    with Session(engine) as s:
        row = s.get(ZipPPSF, z)
        if row: row.ppsf = p
        else: row = ZipPPSF(zip=z, ppsf=p); s.add(row)
        s.commit()
    return {"ok": True, "zip": z, "ppsf": p}

@app.post("/api/admin/ppsf/bulk")
def admin_set_ppsf_bulk(items: List[dict], x_admin_key: Optional[str] = Header(None)):
    require_admin(x_admin_key)
    with Session(engine) as s:
        for it in items:
            try:
                z = (str(it.get("zip"))).strip()
                p = float(it.get("ppsf"))
                if len(z) == 5:
                    row = s.get(ZipPPSF, z)
                    if row: row.ppsf = p
                    else: s.add(ZipPPSF(zip=z, ppsf=p))
            except Exception:
                continue
        s.commit()
    return {"ok": True, "count": len(items)}

# ---------- UI (Landing + App + Admin) ----------
def analytics_snippets() -> str:
    parts = []
    if GA_TAG:
        parts.append(f"""<script async src="https://www.googletagmanager.com/gtag/js?id={GA_TAG}"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments);}}gtag('js',new Date());gtag('config','{GA_TAG}');</script>""")
    if FB_PIXEL:
        parts.append(f"""<script>!function(f,b,e,v,n,t,s){{if(f.fbq)return;n=f.fbq=function(){{n.callMethod?
n.callMethod.apply(n,arguments):n.queue.push(arguments)}};if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';
n.queue=[];t=b.createElement(e);t.async=!0;t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}}
(window, document,'script','https://connect.facebook.net/en_US/fbevents.js');fbq('init','{FB_PIXEL}');fbq('track','PageView');</script>""")
    return "\n".join(parts)

HTML = """<!doctype html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Samson Properties • PG County Home Values & Leads</title>
<meta name="description" content="Instant home value estimates, buyer intake, and a simple CRM for Samson Properties (Prince George’s County).">
<style>
:root{--bg:#0b0f1a;--card:#0f1733;--muted:#9aa4b2;--primary:#c9a227;--accent:#7fb4ff;--text:#f4f7ff}
*{box-sizing:border-box} body{margin:0;font-family:Inter,system-ui,Arial;background:radial-gradient(1200px 600px at 20% -10%, #1a245a 0%, #090e20 60%), #090e20;color:var(--text)}
.container{max-width:1100px;margin:0 auto;padding:24px}
header .brand{display:flex;align-items:center;gap:12px}
.logo{width:42px;height:42px;border-radius:8px;background:linear-gradient(135deg,var(--primary),#f6e8b1);display:flex;align-items:center;justify-content:center;color:#0b0f1a;font-weight:800;box-shadow:0 8px 28px rgba(201,162,39,.35)}
h1{margin:0;font-size:28px} .tag{color:var(--muted)}
.hero{display:grid;grid-template-columns:1.2fr .8fr;gap:18px;align-items:center;margin-top:18px}
.hero-card{background:linear-gradient(180deg,#101a3f,#0b1330);border:1px solid #2a3565;border-radius:18px;padding:18px;box-shadow:0 10px 30px rgba(0,0,0,.35)}
.hero h2{font-size:30px;margin:0 0 8px}
.hero ul{margin:8px 0 0 18px}
.ctas{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
.cta{background:linear-gradient(90deg,var(--primary),var(--accent));color:#0b1020;padding:10px 16px;border-radius:12px;border:none;cursor:pointer;font-weight:700}
.card{background:var(--card);border:1px solid #2a3565;border-radius:16px;padding:16px;box-shadow:0 8px 28px rgba(0,0,0,.25);margin:12px 0}
.tabs{display:flex;gap:10px;margin:16px 0 20px}
.tab{background:transparent;border:1px solid #2a3565;color:var(--text);padding:8px 14px;border-radius:10px;cursor:pointer}
.tab.active,.tab:hover{background:#1b254b}
.tab-pane{display:none}.tab-pane.active{display:block}
.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.form-grid .full{grid-column:1/-1}
label{display:block;font-size:12px;color:var(--muted);margin-bottom:6px}
input,select,button{width:100%;padding:10px;border-radius:10px;border:1px solid #2a3565;background:#0f1733;color:var(--text)}
button{cursor:pointer;background:linear-gradient(90deg,var(--primary),var(--accent));color:#0b1020;font-weight:600;border:none}
.table{width:100%;border-collapse:collapse}
.table th,.table td{border-bottom:1px solid #2a3565;padding:8px;text-align:left;font-size:14px}
.badge{display:inline-block;padding:4px 8px;border-radius:999px;background:#273165;color:var(--text);font-size:12px}
footer{margin-top:20px;color:var(--muted);font-size:12px;text-align:center}
.hidden{display:none}
.testimonials{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.quote{background:#0d1330;border:1px solid #2a3565;border-radius:12px;padding:12px;font-size:14px}
.admin-only{display:none}
</style>
</head>
<body>
<div class="container">
<header>
  <div class="brand"><div class="logo">SP</div>
    <div><h1>Samson Properties — Prince George’s County</h1><div class="tag">Instant valuations • Buyer intake • Simple CRM</div></div>
  </div>
</header>

<section class="hero">
  <div class="hero-card">
    <h2>Curious what your PG County home is worth?</h2>
    <p>Get an instant estimate and a tailored pricing plan—no obligation.</p>
    <ul>
      <li>Local comps + condition adjustment</li>
      <li>PG County neighborhoods & ZIPs baked in</li>
      <li>Free expert walkthrough after you submit</li>
    </ul>
    <div class="ctas">
      <button class="cta" onclick="openTab('sell')">Get My Home Value</button>
      <button class="cta" onclick="openTab('buy')">Find Homes</button>
    </div>
  </div>
  <div>
    <div class="testimonials">
      <div class="quote">“Sold over asking in 6 days. Seamless.” — A. Johnson</div>
      <div class="quote">“Accurate estimate and a clear plan.” — D. Morgan</div>
      <div class="quote">“Professional and responsive—highly recommend.” — K. Lee</div>
    </div>
  </div>
</section>

<nav class="tabs">
  <button class="tab active" data-tab="sell">Sell</button>
  <button class="tab" data-tab="buy">Buy</button>
  <button class="tab admin-only" data-tab="dash">Dashboard</button>
  <button class="tab admin-only" data-tab="admin">Admin</button>
</nav>

<section id="sell" class="tab-pane active">
  <h2>What's My Home Worth?</h2>
  <div class="card">
    <div class="form-grid">
      <div><label>Address</label><input id="s_addr" placeholder="123 Main St"></div>
      <div><label>ZIP</label><input id="s_zip" placeholder="20774"></div>
      <div><label>Beds</label><input id="s_beds" type="number" min="0" step="1"></div>
      <div><label>Baths</label><input id="s_baths" type="number" min="0" step="0.5"></div>
      <div><label>Sq Ft</label><input id="s_sqft" type="number" min="300" step="10"></div>
      <div><label>Condition</label>
        <select id="s_cond">
          <option value="">Select…</option>
          <option>Needs work</option><option>Average</option><option>Updated</option><option>Renovated</option>
        </select>
      </div>
      <div class="full"><button onclick="doVal()">Get Instant Estimate</button></div>
    </div>
    <div id="val_out" class="tag" style="margin-top:8px"></div>
  </div>

  <h3>Contact Info</h3>
  <div class="card form-grid">
    <input type="hidden" id="s_role" value="seller"/>
    <div><label>First Name</label><input id="s_fn"></div>
    <div><label>Last Name</label><input id="s_ln"></div>
    <div><label>Email</label><input id="s_email" type="email"></div>
    <div><label>Phone</label><input id="s_phone"></div>
    <div><label>Timeline</label>
      <select id="s_tl"><option value="">Select…</option><option>0-3</option><option>3-6</option><option>6-12</option><option>12+</option></select>
    </div>
    <div class="full"><label><input type="checkbox" id="s_sms"> I agree to receive SMS</label> ·
      <label><input type="checkbox" id="s_em" checked> I agree to receive email</label>
    </div>
    <div class="full"><button onclick="submitSeller()">Request In-Person Valuation</button></div>
  </div>
</section>

<section id="buy" class="tab-pane">
  <h2>Find Your Next Home</h2>
  <div class="card form-grid">
    <input type="hidden" id="b_role" value="buyer"/>
    <div><label>ZIP</label><input id="b_zip" placeholder="20774"></div>
    <div><label>Beds</label><input id="b_beds" type="number" m_

</script>
</body></html>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    head = analytics_snippets()
    return HTML.replace("</head>", head + "\n</head>") if head else HTML

