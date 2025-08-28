# admin_gui.py — Samson Admin GUI for Leads & ZIP $/sqft
# Works against your existing FastAPI service.
# Tabs: Connect • Leads • ZIP $/sqft • Valuation Tester • Bulk Import

import os, json, requests, math
import streamlit as st

st.set_page_config(page_title="Samson Admin GUI", page_icon="🏠", layout="wide")

# ---------- Settings (env or UI) ----------
DEFAULT_BASE_URL = os.getenv("BASE_URL", "").rstrip("/")
DEFAULT_ADMIN_KEY = os.getenv("ADMIN_KEY", "")

if "base_url" not in st.session_state:
    st.session_state.base_url = DEFAULT_BASE_URL
if "admin_key" not in st.session_state:
    st.session_state.admin_key = DEFAULT_ADMIN_KEY

def api_headers(admin=False):
    h = {}
    if admin and st.session_state.admin_key:
        h["X-Admin-Key"] = st.session_state.admin_key
    return h

def get_json(path, admin=False, params=None):
    url = st.session_state.base_url + path
    r = requests.get(url, headers=api_headers(admin), params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def post_json(path, data, admin=False):
    url = st.session_state.base_url + path
    r = requests.post(url, headers={**api_headers(admin), "Content-Type":"application/json"}, data=json.dumps(data), timeout=20)
    r.raise_for_status()
    return r.json()

def patch_json(path, data, admin=False):
    url = st.session_state.base_url + path
    r = requests.patch(url, headers={**api_headers(admin), "Content-Type":"application/json"}, data=json.dumps(data), timeout=20)
    r.raise_for_status()
    return r.json()

# ---------- Sidebar / Connection ----------
st.sidebar.header("Connection")
st.sidebar.write("Point this GUI at your deployed API (the site running `app.py`).")
st.session_state.base_url = st.sidebar.text_input("API Base URL", st.session_state.base_url, placeholder="https://your-render-url")
st.session_state.admin_key = st.sidebar.text_input("Admin Key (X-Admin-Key)", st.session_state.admin_key, type="password")

colA, colB = st.sidebar.columns(2)
if colA.button("Check Health"):
    try:
        j = get_json("/api/health")
        st.sidebar.success(f"OK: {j}")
    except Exception as e:
        st.sidebar.error(f"Health check failed: {e}")

# ---------- Tabs ----------
tab_connect, tab_leads, tab_ppsf, tab_val, tab_bulk = st.tabs(["🔌 Connect", "👥 Leads", "📍 ZIP $/sqft", "🧮 Valuation Tester", "📦 Bulk Import"])

with tab_connect:
    st.subheader("How to use")
    st.markdown("""
1) Put your **API Base URL** and **Admin Key** in the left sidebar.  
2) Click **Check Health** — should return OK.  
3) Use **Leads**, **ZIP $/sqft**, **Valuation Tester**, and **Bulk Import** tabs.
""")
    st.info("Tip: You can deploy this GUI to Render as a second service. Set env vars `BASE_URL` and `ADMIN_KEY` there.")

with tab_leads:
    st.subheader("Leads Dashboard (Admin)")
    if not st.session_state.base_url or not st.session_state.admin_key:
        st.warning("Enter Base URL and Admin Key in the sidebar.")
    else:
        c1, c2, c3, c4 = st.columns([2,1,1,1])
        q = c1.text_input("Search", "")
        role = c2.selectbox("Role", ["", "seller", "buyer"], index=0)
        stage = c3.selectbox("Stage", ["", "New","Contacted","Qualified","Appointment","Agreement","Closed/Lost"], index=0)
        if c4.button("Refresh"):
            st.experimental_rerun()

        try:
            params = {}
            if q: params["q"] = q
            if role: params["role"] = role
            if stage: params["stage"] = stage
            data = get_json("/api/leads", admin=True, params=params)
            items = data.get("items", [])
            st.caption(f"{len(items)} lead(s)")

            if items:
                # Table-like editor
                for ld in items:
                    with st.expander(f"{ld.get('role','?').upper()} • {ld.get('first_name','')} {ld.get('last_name','')} • Score {ld.get('score',0)}", expanded=False):
                        cA, cB, cC, cD = st.columns([2,2,1,1])
                        cA.text(f"{ld.get('email','')}  |  {ld.get('phone','')}")
                        cB.text(f"{ld.get('address','')}  {ld.get('zip_code','')}  ({ld.get('county','')})")
                        timeline = cC.selectbox("Timeline", ["","0-3","3-6","6-12","12+"], index=["","0-3","3-6","6-12","12+"].index(ld.get("timeline","") or ""), key=f"tl{ld['id']}")
                        stage_new = cD.selectbox("Stage", ["New","Contacted","Qualified","Appointment","Agreement","Closed/Lost"],
                                                 index=["New","Contacted","Qualified","Appointment","Agreement","Closed/Lost"].index(ld.get("stage","New")), key=f"st{ld['id']}")
                        if st.button("Save", key=f"save{ld['id']}"):
                            try:
                                patch_json(f"/api/leads/{ld['id']}", {"timeline": timeline or None, "stage": stage_new}, admin=True)
                                st.success("Saved")
                            except Exception as e:
                                st.error(f"Save failed: {e}")
            else:
                st.info("No leads found.")
        except Exception as e:
            st.error(f"Failed to load leads: {e}")

with tab_ppsf:
    st.subheader("ZIP Overrides ($/sqft)")
    if not st.session_state.base_url or not st.session_state.admin_key:
        st.warning("Enter Base URL and Admin Key in the sidebar.")
    else:
        col1, col2, col3 = st.columns([1,1,1])
        z = col1.text_input("ZIP (5 digits)")
        p = col2.number_input("$/sqft", min_value=0.0, step=1.0, value=0.0)
        if col3.button("Save ZIP $/sqft"):
            try:
                post_json("/api/admin/ppsf", {"zip": z, "ppsf": p}, admin=True)
                st.success(f"Saved {z} = ${p:.0f}/sqft")
            except Exception as e:
                st.error(f"Save failed: {e}")

        st.markdown("---")
        if st.button("Refresh ZIP Table"):
            st.experimental_rerun()

        try:
            d = get_json("/api/admin/ppsf", admin=True)
            ppsf = d.get("ppsf", {})
            if ppsf:
                # show as table
                rows = sorted(ppsf.items(), key=lambda x: x[0])
                st.dataframe({"ZIP": [r[0] for r in rows], "$/sqft": [r[1] for r in rows]}, use_container_width=True)
            else:
                st.info("No data.")
        except Exception as e:
            st.error(f"Failed to load table: {e}")

with tab_val:
    st.subheader("Valuation Tester")
    if not st.session_state.base_url:
        st.warning("Enter Base URL in the sidebar.")
    else:
        c1, c2, c3 = st.columns(3)
        zip_code = c1.text_input("ZIP", "")
        county = c2.selectbox("County", ["", "Prince George’s","Calvert","St. Mary's","Charles","Anne Arundel","Montgomery","Howard"], index=0)
        condition = c3.selectbox("Condition", ["", "Needs work", "Average", "Updated", "Renovated"], index=0)

        c4, c5, c6 = st.columns(3)
        beds = c4.number_input("Beds", min_value=0, step=1, value=0)
        baths = c5.number_input("Baths", min_value=0.0, step=0.5, value=0.0)
        sqft  = c6.number_input("Sq Ft", min_value=0, step=10, value=0)

        if st.button("Compute Estimate"):
            try:
                payload = {
                    "zip_code": zip_code or None,
                    "county": county or None,
                    "beds": int(beds) or None,
                    "baths": float(baths) or None,
                    "sqft": int(sqft) or None,
                    "condition": condition or None
                }
                j = post_json("/api/valuation", payload)
                v = j.get("valuation", {})
                st.success(f"Estimate: ${v.get('estimate',0):,} (range ${v.get('low',0):,} – ${v.get('high',0):,})")
                st.caption(f"Using ${v.get('ppsf_used',0):.0f}/sqft × {v.get('sqft_used',0)} sqft (adj {v.get('adjustment',1.0)}).")
            except Exception as e:
                st.error(f"Failed: {e}")

with tab_bulk:
    st.subheader("Bulk Import ZIP $/sqft (JSON)")
    st.caption('Paste JSON like: [{"zip":"20774","ppsf":275}, ...]')
    text = st.text_area("JSON", height=200)
    c1, c2 = st.columns([1,1])
    if c1.button("Validate JSON"):
        try:
            items = json.loads(text or "[]")
            assert isinstance(items, list)
            ok = 0
            for it in items:
                z = str(it.get("zip","")).strip()
                p = float(it.get("ppsf", 0))
                if len(z)==5 and z.isdigit() and p>0:
                    ok += 1
            st.success(f"Looks good. {ok} valid rows.")
        except Exception as e:
            st.error(f"Invalid JSON: {e}")
    if c2.button("Import to API"):
        try:
            items = json.loads(text or "[]")
            j = post_json("/api/admin/ppsf/bulk", items, admin=True)
            st.success(f"Imported {j.get('count',0)} row(s).")
        except Exception as e:
            st.error(f"Import failed: {e}")
