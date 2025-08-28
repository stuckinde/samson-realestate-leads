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
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Samson Properties • Leads</title>
</head>
<body>
  <h2>Samson Properties — Lead Capture (PG County)</h2>

  <h3>Seller</h3>
  <form onsubmit="submitSeller(); return false;">
    <input id="s_addr" placeholder="Address" required>
    <input id="s_zip" placeholder="ZIP" required>
    <input id="s_beds" type="number" placeholder="Beds">
    <input id="s_baths" type="number" step="0.5" placeholder="Baths">
    <input id="s_sqft" type="number" placeholder="Sq Ft">
    <select id="s_cond">
      <option value="">Condition…</option>
      <option>Needs work</option><option>Average</option><option>Updated</option><option>Renovated</option>
    </select>
    <br>
    <input id="s_fn" placeholder="First name"><input id="s_ln" placeholder="Last name">
    <input id="s_email" type="email" placeholder="Email"><input id="s_phone" placeholder="Phone">
    <select id="s_tl">
      <option value="">Timeline…</option><option>0-3</option><option>3-6</option><option>6-12</option><option>12+</option>
    </select>
    <label><input type="checkbox" id="s_sms"> SMS OK</label>
    <label><input type="checkbox" id="s_em" checked> Email OK</label>
    <button type="button" onclick="doVal()">Get Instant Estimate</button>
    <div id="val_out"></div>
    <button type="submit">Request In-Person Valuation</button>
  </form>

  <h3>Buyer</h3>
  <form onsubmit="submitBuyer(); return false;">
    <input id="b_zip" placeholder="ZIP">
    <input id="b_beds" type="number" placeholder="Beds">
    <input id="b_baths" type="number" step="0.5" placeholder="Baths">
    <input id="b_min" type="number" placeholder="Price Min">
    <input id="b_max" type="number" placeholder="Price Max">
    <input id="b_fn" placeholder="First name"><input id="b_ln" placeholder="Last name">
    <input id="b_email" type="email" placeholder="Email"><input id="b_phone" placeholder="Phone">
    <select id="b_tl">
      <option value="">Timeline…</option><option>0-3</option><option>3-6</option><option>6-12</option><option>12+</option>
    </select>
    <label><input type="checkbox" id="b_sms"> SMS OK</label>
    <label><input type="checkbox" id="b_em" checked> Email OK</label>
    <button type="submit">Create Saved Search</button>
  </form>

  <h3>Dashboard (admin only)</h3>
  <div>
    <input id="q" placeholder="Search…">
    <select id="r"><option value="">All</option><option>seller</option><option>buyer</option></select>
    <select id="st"><option value="">All Stages</option><option>New</option><option>Contacted</option><option>Qualified</option><option>Appointment</option><option>Agreement</option><option>Closed/Lost</option></select>
    <button onclick="loadLeads()">Refresh</button>
  </div>
  <div id="tbl"></div>

<script>
async function post(url,data,admin=false){
  const h={'Content-Type':'application/json'};
  if (admin && ADMIN) h['X-Admin-Key']=ADMIN;
  const r=await fetch(url,{method:'POST',headers:h,body:JSON.stringify(data)});
  if(!r.ok) throw new Error(await r.text()); return r.json();
}
async function patch(url,data){
  const h={'Content-Type':'application/json'}; if(ADMIN) h['X-Admin-Key']=ADMIN;
  const r=await fetch(url,{method:'PATCH',headers:h,body:JSON.stringify(data)});
  if(!r.ok) throw new Error(await r.text()); return r.json();
}
async function getJSON(url,admin=false){
  const h={}; if(admin && ADMIN) h['X-Admin-Key']=ADMIN;
  const r=await fetch(url,{headers:h}); if(!r.ok) throw new Error(await r.text()); return r.json();
}
function val(id){return document.getElementById(id).value}
function num(id){const v=val(id); return v?Number(v):null}

const urlParams = new URLSearchParams(window.location.search);
const ADMIN = urlParams.get('admin');

async function doVal(){
  const payload={zip_code:val('s_zip'), beds:num('s_beds'), baths:num('s_baths'), sqft:num('s_sqft'), condition:val('s_cond')||null};
  const out=document.getElementById('val_out'); out.textContent='...';
  try{ const resp=await post('/api/valuation',payload); const v=resp.valuation;
    out.innerHTML = `Estimated: $${v.estimate.toLocaleString()} (range $${v.low.toLocaleString()}–$${v.high.toLocaleString()})`;
  }catch(e){ out.textContent='Could not compute estimate'; }
}
async function submitSeller(){
  const payload={
    role:'seller', first_name:val('s_fn'), last_name:val('s_ln'), email:val('s_email'), phone:val('s_phone'),
    address:val('s_addr'), zip_code:val('s_zip'), beds:num('s_beds'), baths:num('s_baths'), sqft:num('s_sqft'),
    condition:val('s_cond')||null, timeline:val('s_tl'),
    consent_sms:document.getElementById('s_sms').checked, consent_email:document.getElementById('s_em').checked
  };
  try{ await post('/api/leads',payload); alert('Thanks! We will contact you shortly.'); } catch(e){ alert('Could not submit.'); }
}
async function submitBuyer(){
  const payload={
    role:'buyer', first_name:val('b_fn'), last_name:val('b_ln'), email:val('b_email'), phone:val('b_phone'),
    zip_code:val('b_zip'), beds:num('b_beds'), baths:num('b_baths'),
    price_min:num('b_min'), price_max:num('b_max'),
    timeline:val('b_tl'), consent_sms:document.getElementById('b_sms').checked, consent_email:document.getElementById('b_em').checked
  };
  try{ await post('/api/leads',payload); alert('Saved! We’ll send property alerts (demo).'); } catch(e){ alert('Could not submit.'); }
}
async function loadLeads(){
  const p=new URLSearchParams(); if(val('q')) p.set('q',val('q')); if(val('r')) p.set('role',val('r')); if(val('st')) p.set('stage',val('st'));
  const d=await getJSON('/api/leads?'+p.toString(), true);
  const T=document.getElementById('tbl');
  const rows = d.items.map(ld=>`
    <tr>
      <td>${ld.role}</td>
      <td><b>${(ld.first_name||'')+' '+(ld.last_name||'')}</b> — ${(ld.email||'')} ${(ld.phone?' · '+ld.phone:'')}</td>
      <td>${(ld.address||'')+' '+(ld.zip_code||'')}</td>
      <td>${ld.timeline||'—'}</td>
      <td>${ld.score||0}</td>
      <td>
        <select data-id="${ld.id}" class="stPick">
          ${['New','Contacted','Qualified','Appointment','Agreement','Closed/Lost'].map(s=>`<option ${s===ld.stage?'selected':''}>${s}</option>`).join('')}
        </select>
      </td>
    </tr>`).join('');
  T.innerHTML = `<table border="1" cellpadding="6"><thead><tr><th>Role</th><th>Lead</th><th>Location</th><th>Timeline</th><th>Score</th><th>Stage</th></tr></thead><tbody>${rows||'<tr><td colspan=6>No leads yet.</td></tr>'}</tbody></table>`;
  document.querySelectorAll('.stPick').forEach(sel=>{
    sel.addEventListener('change', async e=>{ await patch('/api/leads/'+e.target.getAttribute('data-id'), {stage:e.target.value}); loadLeads(); });
  });
}
</script>
</body></html>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    head = analytics_snippets()
    return HTML.replace("</head>", head + "\n</head>") if head else HTML
