from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse, JSONResponse
from sqlmodel import SQLModel, Field, Session, create_engine, select
from typing import Optional, List, Dict
from datetime import datetime
import os

app = FastAPI(title="Samson Properties Lead-Gen", version="1.3")

# ---- Config ----
ADMIN_KEY = os.getenv("ADMIN_KEY")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL")

DB_PATH = "sqlite:////data/app.db" if os.path.isdir("/data") else "sqlite:///app.db"
engine = create_engine(DB_PATH, echo=False)

# ---- Models ----
class LeadBase(SQLModel):
    role: str  # "seller" or "buyer"
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    zip_code: Optional[str] = None
    county: Optional[str] = None
    beds: Optional[int] = None
    baths: Optional[float] = None
    sqft: Optional[int] = None
    price_min: Optional[int] = None
    price_max: Optional[int] = None
    timeline: Optional[str] = None
    condition: Optional[str] = None
    tags: Optional[str] = None
    stage: str = "New"
    consent_sms: bool = False
    consent_email: bool = False
    score: int = 0

class Lead(LeadBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class ZipPPSF(SQLModel, table=True):
    zip: str = Field(primary_key=True)
    ppsf: float

def init_db():
    SQLModel.metadata.create_all(engine)

@app.on_event("startup")
def _on_start():
    init_db()

# ---- Valuation config ----
PG_PPSF_BASE: Dict[str, float] = {
    "20706":255, "20707":260, "20708":245, "20710":240, "20712":270,
    "20715":250, "20716":255, "20720":265, "20721":270, "20722":245,
    "20735":250, "20737":255, "20740":275, "20742":280, "20743":235,
    "20744":245, "20745":250, "20746":240, "20747":238, "20748":242,
    "20769":260, "20770":265, "20771":270, "20772":255, "20774":268,
    "20781":248, "20782":252, "20783":258, "20784":245, "20785":240
}
DEFAULT_PPSF = 220.0
COUNTY_MULT = {
    "Prince George’s": 1.00,
    "Calvert": 0.95,
    "St. Mary's": 0.92,
    "Charles": 0.95,
    "Anne Arundel": 1.15,
    "Montgomery": 1.45,
    "Howard": 1.30,
}
CONDITION_ADJ = {
    "Needs work": -0.10,
    "Average": 0.0,
    "Updated": 0.05,
    "Renovated": 0.10
}

def current_ppsf_map() -> Dict[str, float]:
    with Session(engine) as s:
        overrides = {row.zip: row.ppsf for row in s.exec(select(ZipPPSF)).all()}
    merged = PG_PPSF_BASE.copy()
    merged.update(overrides)
    return merged

def estimate_value(zip_code: Optional[str], beds: Optional[int], baths: Optional[float],
                   sqft: Optional[int], condition: Optional[str], county: Optional[str]) -> Dict[str, float]:
    ppsf_map = current_ppsf_map()
    zip_clean = (zip_code or "").strip()
    county_mult = COUNTY_MULT.get((county or "").strip(), 1.0)
    base_ppsf = ppsf_map.get(zip_clean, DEFAULT_PPSF * county_mult)

    sqft = max(600, (sqft or 1800))
    adj = 1.0
    if beds:  adj += 0.08 if beds >= 5 else 0.05 if beds == 4 else 0.02 if beds == 3 else 0
    if baths: adj += 0.05 if (baths or 0) >= 3 else 0.03 if (baths or 0) >= 2 else 0
    if condition and condition in CONDITION_ADJ: adj += CONDITION_ADJ[condition]

    est = round(base_ppsf * sqft * adj)
    band = 0.05 if condition else 0.08
    return {"estimate": est, "low": int(est*(1-band)), "high": int(est*(1+band)),
            "ppsf_used": float(base_ppsf), "sqft_used": int(sqft), "adjustment": round(adj,3), "band": band}

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

# ---- API ----
@app.get("/api/health")
def health_api():
    return {"ok": True, "service": "samson-leads", "version": app.version}

# optional alias so /health also works
@app.get("/health", response_class=JSONResponse)
def health_alias():
    return {"ok": True, "service": "samson-leads", "version": app.version}

@app.post("/api/valuation")
def api_valuation(payload: dict):
    return {"ok": True, "valuation": estimate_value(
        payload.get("zip_code"), payload.get("beds"), payload.get("baths"),
        payload.get("sqft"), payload.get("condition"), payload.get("county")
    )}

@app.post("/api/leads")
def create_lead(payload: dict):
    with Session(engine) as s:
        lead = Lead(**payload)
        lead.updated_at = datetime.utcnow()
        lead.score = compute_score(lead)
        s.add(lead); s.commit(); s.refresh(lead)
        notify_email(
            subject=f"[New {lead.role.title()} Lead] {lead.first_name or ''} {lead.last_name or ''}",
            html=(f"<h3>New {lead.role} lead</h3>"
                  f"<p><b>Name:</b> {lead.first_name or ''} {lead.last_name or ''}<br>"
                  f"<b>Email:</b> {lead.email or '—'} — <b>Phone:</b> {lead.phone or '—'}<br>"
                  f"<b>ZIP:</b> {lead.zip_code or '—'} — <b>County:</b> {lead.county or '—'}<br>"
                  f"<b>Timeline:</b> {lead.timeline or '—'} — <b>Score:</b> {lead.score}</p>")
        )
        return {"ok": True, "lead": lead.dict()}

def _require_admin(h: Optional[str]):
    if ADMIN_KEY and h != ADMIN_KEY:
        raise HTTPException(401, "Unauthorized")

@app.get("/api/leads")
def list_leads(q: Optional[str] = None, role: Optional[str] = None, stage: Optional[str] = None,
               x_admin_key: Optional[str] = Header(None)):
    _require_admin(x_admin_key)
    with Session(engine) as s:
        rows = s.exec(select(Lead)).all()
        out = []
        for ld in rows:
            if role and ld.role != role: continue
            if stage and ld.stage != stage: continue
            if q:
                blob = " ".join([str(getattr(ld, k, "") or "") for k in
                                 ("first_name","last_name","email","phone","address","zip_code","county","tags","role","stage")]).lower()
                if q.lower() not in blob: continue
            out.append(ld.dict())
        out.sort(key=lambda x: (x.get("score",0), x.get("created_at","")), reverse=True)
        return {"ok": True, "items": out}

@app.patch("/api/leads/{lead_id}")
def update_lead(lead_id: int, payload: dict, x_admin_key: Optional[str] = Header(None)):
    _require_admin(x_admin_key)
    with Session(engine) as s:
        ld = s.get(Lead, lead_id)
        if not ld: raise HTTPException(404, "Lead not found")
        for k,v in payload.items():
            if hasattr(ld, k): setattr(ld, k, v)
        ld.updated_at = datetime.utcnow(); ld.score = compute_score(ld)
        s.add(ld); s.commit(); s.refresh(ld)
        return {"ok": True, "lead": ld.dict()}

@app.get("/api/admin/ppsf")
def admin_get_ppsf(x_admin_key: Optional[str] = Header(None)):
    _require_admin(x_admin_key)
    data = current_ppsf_map()
    with Session(engine) as s:
        overrides = {row.zip: row.ppsf for row in s.exec(select(ZipPPSF)).all()}
    return {"ok": True, "ppsf": data, "overrides": overrides}

@app.post("/api/admin/ppsf")
def admin_set_ppsf(item: dict, x_admin_key: Optional[str] = Header(None)):
    _require_admin(x_admin_key)
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
    _require_admin(x_admin_key)
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

# ---- UI ----
HTML = """<!doctype html>
<html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Samson Properties • MD Home Values & Leads</title>
<style>
body{font-family:Inter,system-ui,Arial;margin:0;background:#0b1020;color:#f5f7ff}
.container{max-width:960px;margin:0 auto;padding:20px}
.card{background:#111a3a;border:1px solid #283469;border-radius:14px;padding:14px;margin:12px 0}
input,select,button{width:100%;padding:10px;border-radius:10px;border:1px solid #283469;background:#0f1733;color:#fff}
button{cursor:pointer;background:linear-gradient(90deg,#c9a227,#7fb4ff);color:#0b1020;font-weight:600;border:none}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.tab{border:1px solid #283469;background:#0f1733;border-radius:10px;padding:8px 12px;color:#fff;cursor:pointer}
.tab.active,.tab:hover{background:#1a2550}
.tabs{display:flex;gap:8px;margin:10px 0}
.hidden{display:none}
.table{width:100%;border-collapse:collapse}
.table th,.table td{border-bottom:1px solid #283469;padding:8px;text-align:left}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;background:#273165}
</style></head>
<body><div class="container">
<h2>Samson Properties — Southern Maryland</h2>
<div class="card">
  <h3>How much is my home worth?</h3>
  <div style="display:flex; gap:8px; flex-wrap:wrap">
    <input id="hero_addr" placeholder="Enter your address" style="flex:1; min-width:220px">
    <input id="hero_zip" placeholder="ZIP" style="width:120px">
    <select id="hero_county" style="width:220px">
      <option value="">County…</option>
      <option>Prince George’s</option><option>Calvert</option><option>St. Mary's</option>
      <option>Charles</option><option>Anne Arundel</option><option>Montgomery</option><option>Howard</option>
    </select>
    <button onclick="heroStart()">Get started</button>
  </div>
  <div id="hero_result" style="margin-top:8px;color:#a9b4c9"></div>
</div>

<div class="tabs">
  <button class="tab active" data-tab="sell">Sell</button>
  <button class="tab" data-tab="buy">Buy</button>
  <button class="tab admin-tab hidden" data-tab="dash">Dashboard</button>
  <button class="tab admin-tab hidden" data-tab="admin">Admin</button>
</div>

<section id="sell" class="">
  <div class="card">
    <div class="grid">
      <div><input id="s_addr" placeholder="Address"></div>
      <div><input id="s_zip" placeholder="ZIP"></div>
      <div>
        <select id="s_county">
          <option value="">County…</option>
          <option>Prince George’s</option><option>Calvert</option><option>St. Mary's</option>
          <option>Charles</option><option>Anne Arundel</option><option>Montgomery</option><option>Howard</option>
        </select>
      </div>
      <div><input id="s_beds" type="number" placeholder="Beds"></div>
      <div><input id="s_baths" type="number" step="0.5" placeholder="Baths"></div>
      <div><input id="s_sqft" type="number" placeholder="Sq Ft"></div>
      <div>
        <select id="s_cond"><option value="">Condition…</option><option>Needs work</option><option>Average</option><option>Updated</option><option>Renovated</option></select>
      </div>
      <div class="grid" style="grid-column:1/-1"><button onclick="doVal()">Get Instant Estimate</button></div>
    </div>
    <div id="val_out" style="margin-top:8px;color:#a9b4c9"></div>
  </div>

  <div class="card grid">
    <input type="hidden" id="s_role" value="seller"/>
    <div><input id="s_fn" placeholder="First name"></div>
    <div><input id="s_ln" placeholder="Last name"></div>
    <div><input id="s_email" type="email" placeholder="Email"></div>
    <div><input id="s_phone" placeholder="Phone"></div>
    <div>
      <select id="s_tl"><option value="">Timeline…</option><option>0-3</option><option>3-6</option><option>6-12</option><option>12+</option></select>
    </div>
    <div style="grid-column:1/-1">
      <label><input type="checkbox" id="s_sms"> SMS OK</label> ·
      <label><input type="checkbox" id="s_em" checked> Email OK</label>
    </div>
    <div style="grid-column:1/-1"><button onclick="submitSeller()">Request In-Person Valuation</button></div>
  </div>
</section>

<section id="buy" class="hidden">
  <div class="card grid">
    <input type="hidden" id="b_role" value="buyer"/>
    <div><input id="b_zip" placeholder="ZIP"></div>
    <div>
      <select id="b_county">
        <option value="">County…</option>
        <option>Prince George’s</option><option>Calvert</option><option>St. Mary's</option>
        <option>Charles</option><option>Anne Arundel</option><option>Montgomery</option><option>Howard</option>
      </select>
    </div>
    <div><input id="b_beds" type="number" placeholder="Beds"></div>
    <div><input id="b_baths" type="number" step="0.5" placeholder="Baths"></div>
    <div><input id="b_min" type="number" placeholder="Price Min"></div>
    <div><input id="b_max" type="number" placeholder="Price Max"></div>
    <div><input id="b_fn" placeholder="First name"></div>
    <div><input id="b_ln" placeholder="Last name"></div>
    <div><input id="b_email" type="email" placeholder="Email"></div>
    <div><input id="b_phone" placeholder="Phone"></div>
    <div>
      <select id="b_tl"><option value="">Timeline…</option><option>0-3</option><option>3-6</option><option>6-12</option><option>12+</option></select>
    </div>
    <div style="grid-column:1/-1">
      <label><input type="checkbox" id="b_sms"> SMS OK</label> ·
      <label><input type="checkbox" id="b_em" checked> Email OK</label>
    </div>
    <div style="grid-column:1/-1"><button onclick="submitBuyer()">Create Saved Search</button></div>
  </div>
</section>

<section id="dash" class="hidden">
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

<section id="admin" class="hidden">
  <div class="card">
    <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px">
      <input id="azip" placeholder="ZIP (5 digits)">
      <input id="apps" type="number" step="1" placeholder="$/sqft">
      <button onclick="savePPSF()">Save ZIP</button>
      <div style="grid-column:1/-1">
        <textarea id="bulk" rows="6" style="width:100%;background:#0f1733;color:#fff;border:1px solid #283469;border-radius:10px" placeholder='[{"zip":"20774","ppsf":270}]'></textarea>
        <button onclick="bulkPPSF()">Import Bulk</button>
      </div>
      <div id="ppsf_table" style="grid-column:1/-1"></div>
    </div>
  </div>
</section>

<script>
const urlParams = new URLSearchParams(window.location.search);
const ADMIN = urlParams.get('admin');
if (ADMIN){ document.querySelectorAll('.admin-tab').forEach(e=>e.classList.remove('hidden')); }

document.querySelectorAll('.tab').forEach(b=>{
  b.addEventListener('click',()=>{
    document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
    document.querySelectorAll('section').forEach(x=>x.classList.add('hidden'));
    b.classList.add('active'); document.getElementById(b.dataset.tab).classList.remove('hidden');
    if (b.dataset.tab==='dash') loadLeads();
    if (b.dataset.tab==='admin') loadPPSF();
  });
});

function val(id){ const n=document.getElementById(id); return n ? n.value : ""; }
function num(id){ const v=val(id); return v?Number(v):null; }

async function heroStart(){
  document.getElementById('s_addr').value = val('hero_addr');
  document.getElementById('s_zip').value  = val('hero_zip');
  const sc = document.getElementById('s_county'); if (sc) sc.value = val('hero_county');
  document.querySelector('.tab[data-tab="sell"]').click();
  const out=document.getElementById('hero_result'); out.textContent='...';
  try{
    const payload = { zip_code: val('hero_zip'), beds: null, baths: null, sqft: null, condition: null, county: val('hero_county') || null };
    const r = await fetch('/api/valuation',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const j = await r.json(); const v = j.valuation;
    out.innerHTML = `Estimated: $${v.estimate.toLocaleString()} (range $${v.low.toLocaleString()} – $${v.high.toLocaleString()})`;
    document.getElementById('val_out').innerHTML = out.innerHTML;
  }catch(e){ out.textContent='Could not compute estimate'; }
}

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

async function doVal(){
  const payload={zip_code:val('s_zip'), beds:num('s_beds'), baths:num('s_baths'), sqft:num('s_sqft'),
                 condition:val('s_cond')||null, county:val('s_county')||null};
  const out=document.getElementById('val_out'); out.textContent='...';
  try{
    const resp=await post('/api/valuation',payload);
    const v=resp.valuation;
    out.innerHTML = `Estimated: $${v.estimate.toLocaleString()} (range $${v.low.toLocaleString()} – $${v.high.toLocaleString()})`;
  }catch(e){ out.textContent='Could not compute estimate'; }
}

async function submitSeller(){
  const payload={
    role:'seller', first_name:val('s_fn'), last_name:val('s_ln'), email:val('s_email'), phone:val('s_phone'),
    address:val('s_addr'), zip_code:val('s_zip'), county:val('s_county')||null, beds:num('s_beds'), baths:num('s_baths'),
    sqft:num('s_sqft'), condition:val('s_cond')||null, timeline:val('s_tl'),
    consent_sms:document.getElementById('s_sms').checked, consent_email:document.getElementById('s_em').checked
  };
  try{ await post('/api/leads',payload); alert('Thanks! We will contact you shortly.'); } catch(e){ alert('Could not submit.'); }
}

async function submitBuyer(){
  const payload={
    role:'buyer', first_name:val('b_fn'), last_name:val('b_ln'), email:val('b_email'), phone:val('b_phone'),
    zip_code:val('b_zip'), county:val('b_county')||null, beds:num('b_beds'), baths:num('b_baths'),
    price_min:num('b_min'), price_max:num('b_max'),
    timeline:val('b_tl'), consent_sms:document.getElementById('b_sms').checked, consent_email:document.getElementById('b_em').checked
  };
  try{ await post('/api/leads',payload); alert('Saved! We’ll send property alerts (demo).'); } catch(e){ alert('Could not submit.'); }
}

async function loadLeads(){
  const p=new URLSearchParams(); if(val('q')) p.set('q',val('q')); if(val('r')) p.set('role',val('r')); if(val('st')) p.set('stage',val('st'));
  try{
    const d=await getJSON('/api/leads?'+p.toString(), true);
    const T=document.getElementById('tbl');
    const rows = d.items.map(ld=>`
      <tr>
        <td><span class="badge">${ld.role}</span></td>
        <td><b>${(ld.first_name||'')+' '+(ld.last_name||'')}</b><br><span style="color:#a9b4c9">${(ld.email||'')} · ${(ld.phone||'')}</span></td>
        <td>${(ld.address||'')+' '+(ld.zip_code||'')}<br><span style="color:#a9b4c9">${ld.county||''}</span></td>
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
  }catch(e){
    document.getElementById('tbl').innerHTML = '(Admin key required in URL ?admin=YOUR_KEY)';
  }
}

async function loadPPSF(){
  try{
    const d=await getJSON('/api/admin/ppsf', true);
    const entries = Object.entries(d.ppsf).sort();
    const rows = entries.map(([z,p])=>`<tr><td>${z}</td><td>$${Number(p).toFixed(0)}</td></tr>`).join('');
    document.getElementById('ppsf_table').innerHTML = `<table class="table"><thead><tr><th>ZIP</th><th>$/sqft</th></tr></thead><tbody>${rows}</tbody></table>`;
  }catch(e){
    document.getElementById('ppsf_table').innerHTML='(Admin key required)';
  }
}
async function savePPSF(){
  try{
    const payload = {zip: val('azip'), ppsf: Number(val('apps'))};
    const h={'Content-Type':'application/json'}; if(ADMIN) h['X-Admin-Key']=ADMIN;
    const r=await fetch('/api/admin/ppsf',{method:'POST',headers:h,body:JSON.stringify(payload)});
    if(!r.ok) throw new Error(await r.text()); await loadPPSF(); alert('Saved.');
  }catch(e){ alert('Save failed.'); }
}
async function bulkPPSF(){
  try{
    const items = JSON.parse(document.getElementById('bulk').value || "[]");
    const h={'Content-Type':'application/json'}; if(ADMIN) h['X-Admin-Key']=ADMIN;
    const r=await fetch('/api/admin/ppsf/bulk',{method:'POST',headers:h,body:JSON.stringify(items)});
    if(!r.ok) throw new Error(await r.text()); await loadPPSF(); alert('Bulk imported.');
  }catch(e){ alert('Import failed.'); }
}
</script>
</div></body></html>"""

@app.get("/", response_class=HTMLResponse)
def index():
    return HTML
