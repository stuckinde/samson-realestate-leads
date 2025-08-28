# app.py — Samson Properties (PG County) one-file MVP
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlmodel import SQLModel, Field, Session, create_engine, select
from typing import Optional
from datetime import datetime
import json

app = FastAPI(title="Samson Properties Lead-Gen", version="1.0")

# ---------- Database ----------
engine = create_engine("sqlite:///app.db", echo=False)

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
    tags: Optional[str] = None
    stage: str = "New"              # pipeline stage
    consent_sms: bool = False
    consent_email: bool = False
    score: int = 0

class Lead(LeadBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

def init_db():
    SQLModel.metadata.create_all(engine)

@app.on_event("startup")
def _on_start():
    init_db()

# ---------- Valuation (PG County PPSF) ----------
PG_PPSF = {
    "20706":255, "20707":260, "20708":245, "20710":240, "20712":270,
    "20715":250, "20716":255, "20720":265, "20721":270, "20722":245,
    "20735":250, "20737":255, "20740":275, "20742":280, "20743":235,
    "20744":245, "20745":250, "20746":240, "20747":238, "20748":242,
    "20769":260, "20770":265, "20771":270, "20772":255, "20774":268,
    "20781":248, "20782":252, "20783":258, "20784":245, "20785":240
}
DEFAULT_PPSF = 220.0

def estimate_value(zip_code: Optional[str], beds: Optional[int], baths: Optional[float], sqft: Optional[int]):
    ppsf = PG_PPSF.get(zip_code or "", DEFAULT_PPSF)
    sqft = max(600, (sqft or 1800))
    adj = 1.0
    if beds:
        adj += 0.08 if beds >= 5 else 0.05 if beds == 4 else 0.02 if beds == 3 else 0
    if baths:
        adj += 0.05 if baths >= 3 else 0.03 if baths >= 2 else 0
    est = round(ppsf * sqft * adj)
    return {"estimate": est, "low": int(est*0.93), "high": int(est*1.07), "ppsf_used": ppsf, "sqft_used": sqft, "adjustment": adj}

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

# ---------- API ----------
@app.get("/api/health")
def health():
    return {"ok": True, "service": "samson-leads", "version": "1.0"}

@app.post("/api/valuation")
def api_valuation(payload: dict):
    return {"ok": True, "valuation": estimate_value(payload.get("zip_code"), payload.get("beds"), payload.get("baths"), payload.get("sqft"))}

@app.post("/api/leads")
def create_lead(payload: dict):
    with Session(engine) as s:
        lead = Lead(**payload)
        lead.updated_at = datetime.utcnow()
        lead.score = compute_score(lead)
        s.add(lead); s.commit(); s.refresh(lead)
        return {"ok": True, "lead": lead.dict()}

@app.get("/api/leads")
def list_leads(q: Optional[str] = None, role: Optional[str] = None, stage: Optional[str] = None):
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
def update_lead(lead_id: int, payload: dict):
    with Session(engine) as s:
        ld = s.get(Lead, lead_id)
        if not ld: raise HTTPException(404, "Lead not found")
        for k,v in payload.items():
            if hasattr(ld,k): setattr(ld,k,v)
        ld.updated_at = datetime.utcnow()
        ld.score = compute_score(ld)
        s.add(ld); s.commit(); s.refresh(ld)
        return {"ok": True, "lead": ld.dict()}

# ---------- UI ----------
HTML = """<!doctype html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Samson Properties • Leads</title>
<style>
:root{--bg:#0b0f1a;--card:#111827;--muted:#9aa4b2;--primary:#c9a227;--accent:#7fb4ff;--text:#f4f7ff}
*{box-sizing:border-box} body{margin:0;font-family:Inter,system-ui,Arial;background:linear-gradient(180deg,#0b0f1a,#0d1330);color:var(--text)}
.container{max-width:1100px;margin:0 auto;padding:24px}
header .brand{display:flex;align-items:center;gap:12px}
.logo{width:42px;height:42px;border-radius:8px;background:linear-gradient(135deg,var(--primary),#f6e8b1);display:flex;align-items:center;justify-content:center;color:#0b0f1a;font-weight:800}
h1{margin:0;font-size:26px} .tag{color:var(--muted)}
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
</style>
</head>
<body>
<div class="container">
<header>
  <div class="brand"><div class="logo">SP</div>
    <div><h1>Samson Properties</h1><div class="tag">Seller-first capture • Buyer search • Simple CRM</div></div>
  </div>
</header>

<nav class="tabs">
  <button class="tab active" data-tab="sell">Sell</button>
  <button class="tab" data-tab="buy">Buy</button>
  <button class="tab" data-tab="dash">Dashboard</button>
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
    <div><label>Beds</label><input id="b_beds" type="number" min="0" step="1"></div>
    <div><label>Baths</label><input id="b_baths" type="number" min="0" step="0.5"></div>
    <div><label>Price Min</label><input id="b_min" type="number" step="1000"></div>
    <div><label>Price Max</label><input id="b_max" type="number" step="1000"></div>
    <div><label>First Name</label><input id="b_fn"></div>
    <div><label>Last Name</label><input id="b_ln"></div>
    <div><label>Email</label><input id="b_email" type="email"></div>
    <div><label>Phone</label><input id="b_phone"></div>
    <div><label>Timeline</label>
      <select id="b_tl"><option value="">Select…</option><option>0-3</option><option>3-6</option><option>6-12</option><option>12+</option></select>
    </div>
    <div class="full"><label><input type="checkbox" id="b_sms"> I agree to receive SMS</label> ·
      <label><input type="checkbox" id="b_em" checked> I agree to receive email</label>
    </div>
    <div class="full"><button onclick="submitBuyer()">Create Saved Search</button></div>
  </div>
</section>

<section id="dash" class="tab-pane">
  <h2>Leads Dashboard</h2>
  <div class="card">
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
      <input id="q" placeholder="Search…"/>
      <select id="r"><option value="">All</option><option>seller</option><option>buyer</option></select>
      <select id="st"><option value="">All Stages</option><option>New</option><option>Contacted</option><option>Qualified</option><option>Appointment</option><option>Agreement</option><option>Closed/Lost</option></select>
      <button onclick="loadLeads()">Refresh</button>
    </div>
    <div id="tbl"></div>
  </div>
</section>

<footer class="card" style="text-align:center">
  © Samson Properties • Equal Housing Opportunity • Demo only
</footer>
</div>

<script>
// tabs
document.querySelectorAll('.tab').forEach(b=>{
  b.addEventListener('click',()=>{
    document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(x=>x.classList.remove('active'));
    b.classList.add('active'); document.getElementById(b.dataset.tab).classList.add('active');
    if (b.dataset.tab==='dash') loadLeads();
  });
});

async function post(url,data){ const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}); if(!r.ok) throw new Error('req failed'); return r.json(); }
async function patch(url,data){ const r=await fetch(url,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}); if(!r.ok) throw new Error('req failed'); return r.json(); }

async function doVal(){
  const payload={zip_code:val('s_zip'), beds:num('s_beds'), baths:num('s_baths'), sqft:num('s_sqft')};
  const out=document.getElementById('val_out'); out.textContent='...';
  try{ const resp=await post('/api/valuation',payload); const v=resp.valuation;
    out.innerHTML = `<b>Estimated:</b> $${v.estimate.toLocaleString()} <span class="badge">range: $${v.low.toLocaleString()} – $${v.high.toLocaleString()}</span><br>
    <span class="tag">Using $${v.ppsf_used}/sqft × ${v.sqft_used} sqft (adj ${v.adjustment}).</span>`;
  }catch(e){ out.textContent='Could not compute estimate'; }
}
function val(id){return document.getElementById(id).value}
function num(id){const v=val(id); return v?Number(v):null}

async function submitSeller(){
  const payload={
    role:'seller', first_name:val('s_fn'), last_name:val('s_ln'),
    email:val('s_email'), phone:val('s_phone'), address:val('s_addr'), zip_code:val('s_zip'),
    beds:num('s_beds'), baths:num('s_baths'), sqft:num('s_sqft'),
    timeline:val('s_tl'), consent_sms:document.getElementById('s_sms').checked, consent_email:document.getElementById('s_em').checked
  };
  try{ await post('/api/leads',payload); alert('Thanks! We will contact you shortly.'); } catch(e){ alert('Could not submit.'); }
}

async function submitBuyer(){
  const payload={
    role:'buyer', first_name:val('b_fn'), last_name:val('b_ln'),
    email:val('b_email'), phone:val('b_phone'), zip_code:val('b_zip'),
    beds:num('b_beds'), baths:num('b_baths'), price_min:num('b_min'), price_max:num('b_max'),
    timeline:val('b_tl'), consent_sms:document.getElementById('b_sms').checked, consent_email:document.getElementById('b_em').checked
  };
  try{ await post('/api/leads',payload); alert('Saved! We’ll send property alerts (demo).'); } catch(e){ alert('Could not submit.'); }
}

async function loadLeads(){
  const p=new URLSearchParams(); if(val('q')) p.set('q',val('q')); if(val('r')) p.set('role',val('r')); if(val('st')) p.set('stage',val('st'));
  const r=await fetch('/api/leads?'+p.toString()); const d=await r.json(); const T=document.getElementById('tbl');
  const rows = d.items.map(ld=>`
    <tr>
      <td><span class="badge">${ld.role}</span></td>
      <td><b>${(ld.first_name||'')+' '+(ld.last_name||'')}</b><br><span class="tag">${(ld.email||'')+' · '+(ld.phone||'')}</span></td>
      <td>${(ld.address||'')+' '+(ld.zip_code||'')}</td>
      <td>${ld.timeline||'—'}</td>
      <td>${ld.score||0}</td>
      <td>
        <select data-id="${ld.id}" class="stPick">
          ${['New','Contacted','Qualified','Appointment','Agreement','Closed/Lost'].map(s=>`<option ${s===ld.stage?'selected':''}>${s}</option>`).join('')}
        </select>
      </td>
    </tr>`).join('');
  T.innerHTML = `<table class="table"><thead><tr><th>Role</th><th>Lead</th><th>Location</th><th>Timeline</th><th>Score</th><th>Stage</th></tr></thead><tbody>${rows||'<tr><td colspan=6>No leads yet.</td></tr>'}</tbody></table>`;
  document.querySelectorAll('.stPick').forEach(sel=>{
    sel.addEventListener('change', async e=>{ await patch('/api/leads/'+e.target.getAttribute('data-id'), {stage:e.target.value}); loadLeads(); });
  });
}
</script>
</body></html>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    return HTML
