#!/usr/bin/env python3
"""
NOBU Budapest — RSVP Server + Dashboard
========================================
- Guest subscription forms (szerda/csutortok)
- RSVP handling (email link → Mailchimp)
- Dashboard: per-day guest status (jön / nem jön / megnyitotta / nem nyitotta / visszapattant)
- Auto-refresh from Mailchimp API every 30 min (background thread + on-demand)

Deploy to render.com:
  Build Command: pip install flask
  Start Command: python3 nobu_dashboard_server.py
  Environment variables:
    MAILCHIMP_API_KEY  — Mailchimp API key (required)
    DASHBOARD_TOKEN    — simple auth token for /dashboard (optional, no auth if unset)
    REFRESH_MINUTES    — refresh interval in minutes (default 30)
"""
import os, sys, json, base64, hashlib, time, threading
import urllib.request, urllib.error
from flask import Flask, request, render_template_string, jsonify, make_response

app = Flask(__name__)

# ── Config ───────────────────────────────────────────────────────
API_KEY = os.getenv("MAILCHIMP_API_KEY", "")
DC = "us9"
BASE = f"https://{DC}.api.mailchimp.com/3.0"
LIST_ID = "7b625dbadf"
AUTH = base64.b64encode(f"anystring:{API_KEY}".encode()).decode()
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "")
REFRESH_MINUTES = int(os.getenv("REFRESH_MINUTES", "30"))

# ── Cache ────────────────────────────────────────────────────────
_cache = {"data": None, "ts": 0, "error": None}
_cache_lock = threading.Lock()

# ══════════════════════════════════════════════════════════════════
#  Mailchimp API helpers
# ══════════════════════════════════════════════════════════════════

def mc_api(method, path, data=None, timeout=30):
    """Call Mailchimp API v3. Returns parsed JSON or None on error."""
    url = f"{BASE}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Basic {AUTH}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode()[:300]
        except Exception:
            pass
        print(f"[MC] {method} {path} → HTTP {e.code}: {err_body}", flush=True)
        return None
    except Exception as e:
        print(f"[MC] {method} {path} → {e}", flush=True)
        return None


def mc_paginate(method, path_template, key, max_items=2000):
    """Paginate through a Mailchimp collection endpoint.
    
    Args:
        path_template: str with {offset} and {count} placeholders, e.g.
            "/lists/X/members?offset={offset}&count={count}&fields=..."
        key: the JSON key containing items (e.g. "members", "campaigns")
        max_items: stop after this many items
    
    Returns list of all items.
    """
    all_items = []
    offset = 0
    count = min(200, max_items)
    
    while True:
        path = path_template.format(offset=offset, count=count)
        result = mc_api("GET", path)
        if not result:
            break
        
        items = result.get(key, [])
        all_items.extend(items)
        
        total = result.get("total_items", 0)
        if offset + count >= total or len(all_items) >= max_items:
            break
        offset += count
    
    return all_items


def lookup_email_by_uid(unique_email_id):
    try:
        result = mc_api("GET", f"/lists/{LIST_ID}/members?unique_email_id={unique_email_id}&fields=members.email_address")
        if result:
            members = result.get("members", [])
            if members:
                return members[0]["email_address"]
    except Exception as e:
        print(f"Lookup failed for {unique_email_id}: {e}", flush=True)
    return None


def update_rsvp(email, rsvp_value):
    subscriber_hash = hashlib.md5(email.lower().encode()).hexdigest()
    # PUT = add-or-update: works for new, existing, and archived members
    result = mc_api("PUT", f"/lists/{LIST_ID}/members/{subscriber_hash}", {
        "email_address": email,
        "status": "subscribed",
        "merge_fields": {"RSVP": rsvp_value}
    })
    if result is not None:
        return {"status": "updated", "email": email}
    return {"status": "error", "msg": "mc_api returned None for PUT"}


def subscribe_guest(name, email, day):
    nap_value = "Szerda (május 27.)" if day == "szerda" else "Csütörtök (május 28.)"
    subscriber_hash = hashlib.md5(email.lower().encode()).hexdigest()
    merge_fields = {"NAP": nap_value}
    if name:
        merge_fields["MMERGE8"] = name
    # PUT = add-or-update: creates new, updates existing, AND reactivates archived members
    result = mc_api("PUT", f"/lists/{LIST_ID}/members/{subscriber_hash}", {
        "email_address": email,
        "status": "subscribed",
        "merge_fields": merge_fields,
        "tags": [f"opening-{day}"]
    })
    if result is not None:
        return {"status": "updated", "email": email, "nap": nap_value}
    return {"status": "error", "msg": "mc_api returned None for PUT"}


# ══════════════════════════════════════════════════════════════════
#  Dashboard data collection
# ══════════════════════════════════════════════════════════════════

def find_nobu_campaign_ids():
    """Find campaign IDs for NOBU-related emails (automations + regular campaigns).
    
    Returns list of campaign IDs that are NOBU Opening Dinner emails.
    """
    campaign_ids = []
    
    # 1. Try automations (Customer Journey emails)
    print("[DASH] Looking for NOBU automations...", flush=True)
    autos = mc_api("GET", "/automations?count=50")
    if autos:
        for auto in autos.get("automations", []):
            settings = auto.get("settings", {})
            title = settings.get("title", "")
            if "nobu" in title.lower() or "opening" in title.lower():
                print(f"[DASH]   Found automation: {title} (id={auto['id']})", flush=True)
                emails = mc_api("GET", f"/automations/{auto['id']}/emails?count=20")
                if emails:
                    for em in emails.get("emails", []):
                        em_settings = em.get("settings", {})
                        em_title = em_settings.get("title", "")
                        cid = em.get("id", "")
                        if cid:
                            campaign_ids.append(cid)
                            print(f"[DASH]     Email: {em_title} → campaign {cid}", flush=True)
    
    # 2. Also check regular campaigns — only Opening Dinner ones
    print("[DASH] Looking for NOBU campaigns...", flush=True)
    camps = mc_api("GET", f"/campaigns?list_id={LIST_ID}&status=sent&sort_field=send_time&sort_dir=DESC&count=20")
    if camps:
        for camp in camps.get("campaigns", []):
            settings = camp.get("settings", {})
            title = settings.get("title", "")
            subject = settings.get("subject_line", "")
            # Only match Opening Dinner campaigns (filter out old newsletters)
            if ("opening dinner" in title.lower() or "opening dinner" in subject.lower()):
                cid = camp.get("id", "")
                if cid and cid not in campaign_ids:
                    campaign_ids.append(cid)
                    print(f"[DASH]   Campaign: {title} (id={cid})", flush=True)
    
    print(f"[DASH] Total NOBU campaign IDs: {len(campaign_ids)}", flush=True)
    return campaign_ids


def get_campaign_openers(campaign_id):
    """Get set of email addresses that opened a specific campaign."""
    openers = set()
    offset = 0
    count = 200
    while True:
        result = mc_api("GET",
            f"/reports/{campaign_id}/email-activity?offset={offset}&count={count}")
        if not result:
            break
        # Response key is 'emails', each has nested 'activity' array
        for email_item in result.get("emails", []):
            email = (email_item.get("email_address") or "").lower().strip()
            for act in email_item.get("activity", []):
                if act.get("action") == "open":
                    if email:
                        openers.add(email)
        total = result.get("total_items", 0)
        if offset + count >= total:
            break
        offset += count
    return openers


def get_campaign_sent_set(campaign_id):
    """Get set of email addresses that were SENT a specific campaign (not bounced)."""
    sent = set()
    offset = 0
    count = 200
    while True:
        result = mc_api("GET",
            f"/reports/{campaign_id}/email-activity?offset={offset}&count={count}")
        if not result:
            break
        for email_item in result.get("emails", []):
            email = (email_item.get("email_address") or "").lower().strip()
            if email:
                actions = [a.get("action", "") for a in email_item.get("activity", [])]
                has_sent = any(a in ("sent", "open", "click") for a in actions)
                if has_sent or not email_item.get("activity"):
                    # If activity is empty, the email was sent but not opened yet
                    sent.add(email)
        total = result.get("total_items", 0)
        if offset + count >= total:
            break
        offset += count
    return sent


def get_campaign_bounces(campaign_id):
    """Get set of email addresses that bounced for a specific campaign."""
    bounces = set()
    offset = 0
    count = 200
    while True:
        result = mc_api("GET",
            f"/reports/{campaign_id}/email-activity?offset={offset}&count={count}")
        if not result:
            break
        for email_item in result.get("emails", []):
            email = (email_item.get("email_address") or "").lower().strip()
            for act in email_item.get("activity", []):
                if act.get("action") == "bounce":
                    if email:
                        bounces.add(email)
        total = result.get("total_items", 0)
        if offset + count >= total:
            break
        offset += count
    return bounces


def collect_dashboard_data():
    """Main data collection: fetches list members, campaign activity, categorizes by day.
    
    Returns dict:
    {
        "ts": <unix timestamp>,
        "szerda": {"jon": [...], "nem_jon": [...], "megnyitotta": [...], "nem_nyitotta": [...], "visszapattant": [...]},
        "csutortok": { ... same ... },
        "osszesen": {"jon": N, "nem_jon": N, "megnyitotta": N, "nem_nyitotta": N, "visszapattant": N},
        "total_members": N,
        "errors": [...]
    }
    """
    errors = []
    start = time.time()
    print("[DASH] === Starting data collection ===", flush=True)
    
    # ── Step 1: Get all list members ──
    # NOTE: Do NOT use fields= filter — it strips total_items and breaks pagination
    print("[DASH] Fetching list members...", flush=True)
    members = mc_paginate(
        "GET",
        f"/lists/{LIST_ID}/members?offset={{offset}}&count={{count}}",
        "members"
    )
    print(f"[DASH]   Got {len(members)} members", flush=True)
    
    if not members:
        errors.append("Nem sikerült lekérni a listatagokat a Mailchimp-ből.")
        return _empty_result(errors)
    
    # ── Step 2: Find NOBU campaign IDs ──
    campaign_ids = find_nobu_campaign_ids()
    if not campaign_ids:
        errors.append("Nem található NOBU kampány a Mailchimp-ben. Lehet, hogy még nem lett kiküldve email.")
    
    # ── Step 3: Collect openers, sent, bounces across all NOBU campaigns ──
    all_openers = set()
    all_sent = set()
    all_bounces = set()
    
    for cid in campaign_ids:
        print(f"[DASH]   Fetching activity for campaign {cid}...", flush=True)
        all_openers |= get_campaign_openers(cid)
        all_sent |= get_campaign_sent_set(cid)
        all_bounces |= get_campaign_bounces(cid)
    
    print(f"[DASH]   Openers: {len(all_openers)}, Sent: {len(all_sent)}, Bounces: {len(all_bounces)}", flush=True)
    
    # ── Step 4: Categorize members ──
    days = {
        "szerda": {"jon": [], "nem_jon": [], "megnyitotta": [], "nem_nyitotta": [], "visszapattant": []},
        "csutortok": {"jon": [], "nem_jon": [], "megnyitotta": [], "nem_nyitotta": [], "visszapattant": []},
        "egyeb": []  # no day tag, but in the list
    }
    
    for m in members:
        email = (m.get("email_address") or "").lower().strip()
        name = (m.get("full_name") or "").strip()
        merge = m.get("merge_fields", {})
        rsvp = (merge.get("RSVP") or "").strip()
        status = m.get("status", "")
        tags = [t.get("name", "") for t in m.get("tags", [])]
        nap_field = (merge.get("NAP") or "").strip()
        
        # Determine day
        day = None
        if "opening-szerda" in tags:
            day = "szerda"
        elif "opening-csutortok" in tags:
            day = "csutortok"
        elif "szerda" in nap_field.lower():
            day = "szerda"
        elif "csütörtök" in nap_field.lower():
            day = "csutortok"
        
        if not day:
            days["egyeb"].append({"name": name, "email": email, "status": "no_tag"})
            continue
        
        # Determine category
        guest = {"name": name or email, "email": email, "rsvp": rsvp}
        
        # Bounced?
        is_bounced = (status == "cleaned") or (email in all_bounces)
        
        if is_bounced:
            days[day]["visszapattant"].append(guest)
        elif rsvp.startswith("✅") or "igen" in rsvp.lower() or "ott leszek" in rsvp.lower():
            days[day]["jon"].append(guest)
        elif rsvp.startswith("❌") or "nem" in rsvp.lower() or "sajnos" in rsvp.lower():
            days[day]["nem_jon"].append(guest)
        elif email in all_openers:
            days[day]["megnyitotta"].append(guest)
        elif campaign_ids and email in all_sent:
            days[day]["nem_nyitotta"].append(guest)
        else:
            # No campaign data yet, or member added after campaign sent
            days[day]["nem_nyitotta"].append(guest)
    
    elapsed = time.time() - start
    
    # Build summary
    totals = {}
    for k in ["jon", "nem_jon", "megnyitotta", "nem_nyitotta", "visszapattant"]:
        totals[k] = len(days["szerda"][k]) + len(days["csutortok"][k])
    
    result = {
        "ts": int(time.time()),
        "elapsed_sec": round(elapsed, 1),
        "szerda": days["szerda"],
        "csutortok": days["csutortok"],
        "egyeb": days["egyeb"],
        "osszesen": totals,
        "total_members": len(members),
        "total_guests": len(days["szerda"]["jon"]) + len(days["szerda"]["nem_jon"]) +
                        len(days["szerda"]["megnyitotta"]) + len(days["szerda"]["nem_nyitotta"]) +
                        len(days["szerda"]["visszapattant"]) +
                        len(days["csutortok"]["jon"]) + len(days["csutortok"]["nem_jon"]) +
                        len(days["csutortok"]["megnyitotta"]) + len(days["csutortok"]["nem_nyitotta"]) +
                        len(days["csutortok"]["visszapattant"]),
        "campaign_ids_found": len(campaign_ids),
        "errors": errors
    }
    
    print(f"[DASH] Collection done in {elapsed:.1f}s: "
          f"S={totals['jon']+totals['nem_jon']+totals['megnyitotta']+totals['nem_nyitotta']+totals['visszapattant']} guests, "
          f"J={totals['jon']} N={totals['nem_jon']} M={totals['megnyitotta']} "
          f"NY={totals['nem_nyitotta']} V={totals['visszapattant']}", flush=True)
    
    return result


def _empty_result(errors=None):
    return {
        "ts": int(time.time()),
        "elapsed_sec": 0,
        "szerda": {"jon": [], "nem_jon": [], "megnyitotta": [], "nem_nyitotta": [], "visszapattant": []},
        "csutortok": {"jon": [], "nem_jon": [], "megnyitotta": [], "nem_nyitotta": [], "visszapattant": []},
        "egyeb": [],
        "osszesen": {"jon": 0, "nem_jon": 0, "megnyitotta": 0, "nem_nyitotta": 0, "visszapattant": 0},
        "total_members": 0,
        "total_guests": 0,
        "campaign_ids_found": 0,
        "errors": errors or []
    }


def refresh_cache():
    """Refresh the dashboard data cache."""
    global _cache
    try:
        data = collect_dashboard_data()
        with _cache_lock:
            _cache = {"data": data, "ts": time.time(), "error": None}
        print(f"[CACHE] Refreshed at {time.strftime('%H:%M:%S')}", flush=True)
    except Exception as e:
        print(f"[CACHE] Refresh failed: {e}", flush=True)
        with _cache_lock:
            _cache["error"] = str(e)


def get_cached_data(max_age_minutes=None):
    """Get dashboard data from cache, refreshing if needed."""
    if max_age_minutes is None:
        max_age_minutes = REFRESH_MINUTES
    
    with _cache_lock:
        age = (time.time() - _cache["ts"]) / 60 if _cache["ts"] else 999
        has_data = _cache["data"] is not None
    
    if not has_data or age > max_age_minutes:
        print(f"[CACHE] Cache age={age:.0f}m, refreshing...", flush=True)
        refresh_cache()
    
    with _cache_lock:
        return _cache["data"], _cache["error"]


def background_refresh_loop():
    """Background thread: refresh cache every REFRESH_MINUTES."""
    while True:
        time.sleep(REFRESH_MINUTES * 60)
        try:
            print(f"[BG] Background refresh triggered", flush=True)
            refresh_cache()
        except Exception as e:
            print(f"[BG] Background refresh error: {e}", flush=True)


# ══════════════════════════════════════════════════════════════════
#  CSS
# ══════════════════════════════════════════════════════════════════

BASE_STYLE = """
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#181823; color:#c8a960; font-family:Georgia,'Times New Roman',serif; display:flex; align-items:center; justify-content:center; min-height:100vh; padding:20px; }
.card { max-width:520px; width:100%; border:2px solid #c8a960; padding:50px 35px; text-align:center; background:#181823; }
img.logo { max-width:220px; height:auto; margin-bottom:10px; }
h2 { font-size:11px; letter-spacing:5px; text-transform:uppercase; color:#a08950; margin-bottom:30px; }
p.title { font-size:18px; font-weight:bold; margin-bottom:20px; }
p.msg { font-size:15px; line-height:1.8; margin-bottom:30px; }
p.note { font-size:12px; line-height:1.8; color:#7a7a8a; padding-top:25px; border-top:1px solid #2a2a3a; }
p.day { font-size:18px; font-weight:bold; margin-bottom:30px; }
p.info { font-size:13px; color:#7a7a8a; margin-bottom:25px; line-height:1.6; }
input { width:100%; padding:12px; background:#0a1628; border:1px solid #3a4a5a; color:#c8a960; font-family:Georgia,serif; font-size:15px; text-align:center; margin-bottom:12px; outline:0; }
input:focus { border-color:#c8a960; }
.btn { font-family:Georgia,serif; font-size:15px; font-weight:bold; padding:14px 40px; border:0; cursor:pointer; text-transform:uppercase; letter-spacing:2px; background:#c8a960; color:#181823; width:100%; margin-top:10px; text-decoration:none; display:inline-block; }
.btn:hover { background:#d4b870; }
.btn-sm { font-family:Georgia,serif; font-size:13px; font-weight:bold; padding:12px 24px; border:1px solid #c8a960; cursor:pointer; text-transform:uppercase; letter-spacing:1px; text-decoration:none; display:inline-block; }
.btn-gold { background:#c8a960; color:#181823; border-color:#c8a960; }
.btn-outline { background:transparent; color:#c8a960; }
.btn-outline:hover { background:#2a2a3a; }
.btn-yes { background:#c8a960; color:#181823; }
.btn-no { background:#2a2a3a; color:#c8a960; }
.buttons { display:flex; gap:10px; justify-content:center; flex-wrap:wrap; margin-top:25px; }
.error { color:#c86060; font-size:13px; margin-bottom:15px; }
"""

DASHBOARD_STYLE = """
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#181823; color:#c8a960; font-family:Georgia,'Times New Roman',serif; padding:20px; }
.container { max-width:1100px; margin:0 auto; }
.header { text-align:center; padding:30px 0; border-bottom:1px solid #2a2a3a; margin-bottom:30px; }
.header img { max-width:180px; height:auto; margin-bottom:10px; }
.header h1 { font-size:12px; letter-spacing:5px; text-transform:uppercase; color:#a08950; margin-bottom:5px; }
.header .ts { font-size:11px; color:#5a5a7a; margin-top:8px; }
.header .refresh { font-size:11px; color:#5a5a7a; }
.header a { color:#a08950; text-decoration:underline; }
.summary { display:flex; gap:15px; justify-content:center; flex-wrap:wrap; margin-bottom:30px; }
.summary-box { background:#0d1f3c; border:1px solid #2a2a3a; border-radius:6px; padding:15px 25px; text-align:center; min-width:110px; }
.summary-box .label { font-size:10px; letter-spacing:2px; text-transform:uppercase; color:#7a7a8a; }
.summary-box .count { font-size:28px; font-weight:bold; margin-top:5px; }
.jon .count { color:#4caf50; }
.nemjon .count { color:#e53935; }
.megnyitotta .count { color:#ff9800; }
.nemnyitotta .count { color:#607d8b; }
.visszapattant .count { color:#880e4f; }
.day-section { margin-bottom:40px; }
.day-title { font-size:18px; font-weight:bold; text-align:center; padding:15px; background:#0d1f3c; border:1px solid #2a2a3a; border-radius:6px; margin-bottom:10px; }
.day-title .date { font-size:13px; color:#a08950; }
table { width:100%; border-collapse:collapse; font-size:13px; }
th { text-align:left; padding:10px 12px; background:#1a1a2e; color:#a08950; font-size:10px; letter-spacing:2px; text-transform:uppercase; border-bottom:1px solid #2a2a3a; }
td { padding:10px 12px; border-bottom:1px solid #1a1a2e; vertical-align:middle; }
tr:hover { background:#1a1a2e; }
.badge { display:inline-block; padding:3px 10px; border-radius:4px; font-size:11px; font-weight:bold; letter-spacing:1px; }
.badge-jon { background:#1b5e20; color:#a5d6a7; }
.badge-nemjon { background:#b71c1c; color:#ef9a9a; }
.badge-megnyitotta { background:#e65100; color:#ffcc80; }
.badge-nemnyitotta { background:#37474f; color:#b0bec5; }
.badge-visszapattant { background:#880e4f; color:#f48fb1; }
.badge-no-tag { background:#333; color:#999; }
.no-data { text-align:center; padding:30px; color:#5a5a7a; font-size:13px; }
.errors { max-width:1100px; margin:20px auto; padding:15px; background:#2a1515; border:1px solid #5a2020; border-radius:6px; font-size:12px; color:#e57373; }
.errors .err-title { font-weight:bold; margin-bottom:8px; }
.legend { display:flex; gap:20px; justify-content:center; flex-wrap:wrap; margin:10px 0 25px; font-size:11px; color:#7a7a8a; }
.legend span { display:flex; align-items:center; gap:5px; }
.card { max-width:520px; width:100%; border:2px solid #c8a960; padding:50px 35px; background:#181823; margin:0 auto; }
.tabs { display:flex; justify-content:center; gap:4px; margin-bottom:25px; }
.tab-btn { font-family:Georgia,serif; font-size:14px; font-weight:bold; padding:12px 30px; background:#0d1f3c; border:1px solid #2a2a3a; color:#7a7a8a; cursor:pointer; text-transform:uppercase; letter-spacing:2px; transition:all 0.2s; }
.tab-btn:first-child { border-radius:6px 0 0 6px; }
.tab-btn:last-child { border-radius:0 6px 6px 0; }
.tab-btn:hover { background:#1a1a2e; color:#c8a960; }
.tab-btn.active { background:#c8a960; color:#181823; border-color:#c8a960; }
.tab-panel { display:none; }
.tab-panel.active { display:block; }
/* Toggle switch */
.switch { position:relative; display:inline-block; width:48px; height:24px; }
.switch input { opacity:0; width:0; height:0; }
.slider { position:absolute; cursor:pointer; top:0; left:0; right:0; bottom:0; background-color:#37474f; transition:0.3s; border-radius:24px; }
.slider:before { position:absolute; content:""; height:18px; width:18px; left:3px; bottom:3px; background-color:white; transition:0.3s; border-radius:50%; }
input:checked + .slider { background-color:#4caf50; }
input:checked + .slider:before { transform:translateX(24px); }
input:disabled + .slider { opacity:0.4; cursor:not-allowed; }
.toggle-feedback { display:inline-block; margin-left:8px; font-size:11px; vertical-align:middle; }
.toggle-feedback.ok { color:#4caf50; }
.toggle-feedback.err { color:#e53935; }
"""

LOGO_HTML = '<img src="https://mcusercontent.com/99977b9e1589502e522f30db3/images/8ac32077-ab78-29d0-fc9d-213947a6e0cd.png" alt="NOBU Budapest" class="logo" />'


# ══════════════════════════════════════════════════════════════════
#  Routes: Existing (subscribe, RSVP, health)
# ══════════════════════════════════════════════════════════════════

@app.route("/health")
def health():
    with _cache_lock:
        has_data = _cache["data"] is not None
        ts = _cache["ts"]
    return {
        "status": "ok",
        "service": "nobu-rsvp-dashboard",
        "version": "2.0-flask",
        "cache": {"ready": has_data, "last_refresh": ts}
    }


@app.route("/szerda", methods=["GET", "POST"])
@app.route("/csutortok", methods=["GET", "POST"])
@app.route("/subscribe/szerda", methods=["GET", "POST"])
@app.route("/subscribe/csutortok", methods=["GET", "POST"])
def subscribe():
    path = request.path
    if "szerda" in path:
        day = "szerda"
    else:
        day = "csutortok"
    
    day_label = "Szerda • Május 27." if day == "szerda" else "Csütörtök • Május 28."
    day_name = "szerda" if day == "szerda" else "csütörtök"
    other_day = "csutortok" if day == "szerda" else "szerda"
    other_label = "Csütörtök • Május 28." if day == "szerda" else "Szerda • Május 27."
    
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        
        if not email or "@" not in email:
            return render_template_string(SUBSCRIBE_FORM_HTML,
                day=day, day_label=day_label, day_name=day_name,
                error='<p class="error">Kérjük, adjon meg egy érvényes email címet.</p>',
                style=BASE_STYLE, logo=LOGO_HTML)
        
        result = subscribe_guest(name, email, day)
        print(f"SUBSCRIBE: {name} <{email}> → {result['status']} (NAP={result.get('nap','')})", flush=True)
        
        display_name = name if name else email
        if result["status"] in ("created", "updated"):
            title = "Meghívó elküldve!"
            message = f"<strong>{display_name}</strong> felkerült a vendéglistára.<br>A(z) <strong>{day_label}</strong> napra szóló meghívót a Mailchimp automation azonnal elküldi a(z) <strong>{email}</strong> címre."
            note = "A vendég a meghívóban található gombokkal jelezheti, hogy részt tud-e venni az eseményen. A visszajelzés a vendéglistán is megjelenik."
        else:
            title = "Hiba történt"
            message = f"Nem sikerült felíratni a vendéget. Hiba: {result.get('msg', 'Ismeretlen hiba')}"
            note = "Kérjük, próbálja újra később, vagy vegye fel a kapcsolatot a rendszergazdával."
        
        return render_template_string(SUBSCRIBE_DONE_HTML,
            title=title, message=message, note=note,
            day=day, day_label=day_label, other_day=other_day, other_label=other_label,
            style=BASE_STYLE, logo=LOGO_HTML)
    
    # GET: show form
    error = request.args.get("error", "")
    error_html = '<p class="error">Kérjük, adjon meg egy érvényes email címet.</p>' if error else ""
    
    return render_template_string(SUBSCRIBE_FORM_HTML,
        day=day, day_label=day_label, day_name=day_name,
        error=error_html, style=BASE_STYLE, logo=LOGO_HTML)


@app.route("/rsvp", methods=["GET", "POST"])
def rsvp():
    if request.method == "POST":
        email = request.form.get("email", "")
        choice = request.form.get("choice", "yes")
        if email and "@" in email:
            rsvp_value = "✅ Igen, ott leszek!" if choice == "yes" else "❌ Sajnos nem tudok jönni"
            result = update_rsvp(email, rsvp_value)
            print(f"RSVP (form): {email} → {rsvp_value} → {result['status']}", flush=True)
            return render_template_string(RSVP_CONFIRM_HTML, choice=choice, style=BASE_STYLE, logo=LOGO_HTML)
        return render_template_string(FALLBACK_FORM_HTML, choice=choice,
            error='<p style="color:#c86060;font-size:13px;margin-bottom:15px;">Kérjük, adjon meg egy érvényes email címet.</p>',
            style=BASE_STYLE, logo=LOGO_HTML)
    
    # GET
    email = request.args.get("email")
    uid = request.args.get("uid")
    choice = request.args.get("choice", "no")
    
    if uid and not email:
        email = lookup_email_by_uid(uid)
        if not email:
            return render_template_string(RSVP_CONFIRM_HTML, choice=choice, style=BASE_STYLE, logo=LOGO_HTML)
    
    if email and ("*|" in email or "URLENCODE" in email):
        return render_template_string(FALLBACK_FORM_HTML, choice=choice,
            error="", style=BASE_STYLE, logo=LOGO_HTML)
    
    if not email:
        return render_template_string(FALLBACK_FORM_HTML, choice=choice,
            error="", style=BASE_STYLE, logo=LOGO_HTML)
    
    rsvp_value = "✅ Igen, ott leszek!" if choice == "yes" else "❌ Sajnos nem tudok jönni"
    result = update_rsvp(email, rsvp_value)
    print(f"RSVP: {email} → {rsvp_value} → {result['status']}", flush=True)
    return render_template_string(RSVP_CONFIRM_HTML, choice=choice, style=BASE_STYLE, logo=LOGO_HTML)


# ══════════════════════════════════════════════════════════════════
#  Routes: Dashboard
# ══════════════════════════════════════════════════════════════════

@app.route("/refresh")
def force_refresh():
    """Force a cache refresh (can be called by external cron)."""
    token = request.args.get("token", "")
    if DASHBOARD_TOKEN and token != DASHBOARD_TOKEN:
        return jsonify({"status": "unauthorized"}), 401
    
    refresh_cache()
    return jsonify({"status": "refreshed", "ts": int(time.time())})


@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    """Dashboard: password-protected guest list with per-day tabs."""
    # Check cookie for existing auth
    authed = request.cookies.get("nobu_auth", "") == DASHBOARD_TOKEN if DASHBOARD_TOKEN else True
    
    if request.method == "POST":
        pw = request.form.get("password", "")
        if DASHBOARD_TOKEN and pw == DASHBOARD_TOKEN:
            authed = True
        else:
            return render_template_string(LOGIN_FORM_HTML,
                error='<p class="error">Hibás jelszó. Próbáld újra.</p>',
                style=BASE_STYLE,
                logo=LOGO_HTML), 401
    
    if not authed:
        return render_template_string(LOGIN_FORM_HTML,
            error="",
            style=BASE_STYLE,
            logo=LOGO_HTML), 401
    
    data, error = get_cached_data()
    if not data:
        return render_template_string(ERROR_HTML,
            error=error or "Nincs adat. Lehet, hogy még nem futott le az első adatgyűjtés.",
            style=DASHBOARD_STYLE,
            logo=LOGO_HTML)
    
    resp_body = render_template_string(DASHBOARD_HTML,
        data=data,
        style=DASHBOARD_STYLE,
        logo=LOGO_HTML,
        token=DASHBOARD_TOKEN)
    
    from flask import make_response
    resp = make_response(resp_body)
    if DASHBOARD_TOKEN:
        resp.set_cookie("nobu_auth", DASHBOARD_TOKEN, max_age=86400, httponly=True, samesite="Lax")
    return resp


@app.route("/dashboard.json")
def dashboard_json():
    """JSON endpoint: raw dashboard data."""
    token = request.args.get("token", "")
    if DASHBOARD_TOKEN and token != DASHBOARD_TOKEN:
        return jsonify({"status": "unauthorized"}), 401
    
    data, error = get_cached_data()
    if not data:
        return jsonify({"status": "error", "error": error or "No data"}), 500
    
    return jsonify({"status": "ok", "data": data})


# ══════════════════════════════════════════════════════════════════
#  HTML Templates
# ══════════════════════════════════════════════════════════════════

@app.route("/toggle-rsvp", methods=["POST"])
def toggle_rsvp():
    """Toggle RSVP status for a guest directly from the dashboard.
    
    Accepts JSON: { "email": "...", "value": "jon"|"nem" }
    Updates Mailchimp merge_fields.RSVP and invalidates cache.
    """
    token = request.args.get("token", "")
    if DASHBOARD_TOKEN and token != DASHBOARD_TOKEN:
        return jsonify({"status": "unauthorized"}), 401
    
    data = request.get_json(silent=True) or {}
    email = data.get("email", "").strip()
    value = data.get("value", "nem")
    
    if not email or "@" not in email:
        return jsonify({"status": "error", "msg": "Valid email required"}), 400
    
    rsvp_value = "✅ Igen, ott leszek!" if value == "jon" else "❌ Sajnos nem tudok jönni"
    result = update_rsvp(email, rsvp_value)
    print(f"TOGGLE-RSVP: {email} → {rsvp_value} → {result['status']}", flush=True)
    
    if result["status"] in ("updated", "created"):
        with _cache_lock:
            _cache["ts"] = 0
        return jsonify({"status": "ok", "email": email, "rsvp": rsvp_value})
    
    return jsonify({"status": "error", "msg": result.get("msg", "Unknown error")}), 500


SUBSCRIBE_FORM_HTML = """<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NOBU Budapest – Vendég feliratása ({{ day_name }})</title>
<style>{{ style|safe }}</style>
</head>
<body>
<div class="card">
  {{ logo|safe }}
  <h2>VENDÉG FELIRATÁSA</h2>
  <p class="day">{{ day_label }}</p>
  <p class="info">Adja meg a vendég nevét és email címét.<br>A vendég azonnal megkapja a meghívót emailben.</p>
  {{ error|safe }}
  <form method="POST" action="/{{ day }}">
    <input type="text" name="name" placeholder="Vendég teljes neve" />
    <input type="email" name="email" placeholder="Vendég email címe" required />
    <button type="submit" class="btn">Küldés</button>
  </form>
</div>
</body>
</html>"""

SUBSCRIBE_DONE_HTML = """<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NOBU Budapest – Feliratás kész</title>
<style>{{ style|safe }}</style>
</head>
<body>
<div class="card">
  {{ logo|safe }}
  <h2>VENDÉG FELIRATÁSA</h2>
  <p class="title">{{ title|safe }}</p>
  <p class="msg">{{ message|safe }}</p>
  <p class="note">{{ note|safe }}</p>
  <div class="buttons">
    <a href="/{{ day }}" class="btn-sm btn-gold">+ Újabb vendég ({{ day_label }})</a>
    <a href="/{{ other_day }}" class="btn-sm btn-outline">{{ other_label }}</a>
  </div>
</div>
</body>
</html>"""

FALLBACK_FORM_HTML = """<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NOBU Budapest - RSVP</title>
<style>{{ style|safe }}</style>
</head>
<body>
<div class="card">
  {{ logo|safe }}
  <p class="info">Kérjük, adja meg az email címét a visszajelzéshez.<br>Köszönjük!</p>
  {{ error|safe }}
  <form method="POST" action="/rsvp">
    <input type="email" name="email" placeholder="email@pelda.hu" required />
    <div class="buttons">
      <button type="submit" name="choice" value="yes" class="btn btn-yes">&check; Ott leszek</button>
      <button type="submit" name="choice" value="no" class="btn btn-no">&cross; Nem tudok j&ouml;nni</button>
    </div>
  </form>
</div>
</body>
</html>"""

RSVP_CONFIRM_HTML = """<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NOBU Budapest</title>
<style>{{ style|safe }}</style>
</head>
<body>
<div class="card">
  {{ logo|safe }}
  {% if choice == "yes" %}
  <p class="title">Örülünk, hogy részt tud venni az eseményen!</p>
  <p class="msg">Várjuk Önt a megújult Nobu Budapestben!</p>
  <p class="note">Ha mégis változna a helyzet, a kapott emailben a &bdquo;Nem tudok jönni&rdquo; gombot bármikor megnyomva módosíthatja a visszajelzését.</p>
  {% else %}
  <p class="title">Sajnáljuk, hogy nem tud részt venni!</p>
  <p class="msg">Reméljük, legközelebb tudunk találkozni!</p>
  <p class="note">Ha mégis úgy alakulna, hogy tud jönni, a kapott emailben az &bdquo;Ott leszek&rdquo; gombot bármikor megnyomva módosíthatja a visszajelzését.</p>
  {% endif %}
</div>
</body>
</html>"""

LOGIN_FORM_HTML = """<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NOBU Budapest – Vendéglista</title>
<style>{{ style|safe }}</style>
</head>
<body>
<div class="card">
  {{ logo|safe }}
  <h2>VENDÉGLISTA</h2>
  <p style="font-size:13px;color:#7a7a8a;margin-bottom:25px;line-height:1.6;">A megtekintéshez add meg a jelszót.</p>
  {{ error|safe }}
  <form method="POST" action="/dashboard">
    <input type="password" name="password" placeholder="Jelszó" autofocus required />
    <button type="submit" class="btn">BELÉPÉS</button>
  </form>
</div>
</body>
</html>"""

ERROR_HTML = """<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NOBU Dashboard – Hiba</title>
<style>{{ style|safe }}</style>
</head>
<body>
<div class="container">
  <div class="header">
    {{ logo|safe }}
    <h1>VENDÉGLISTA DASHBOARD</h1>
    <p style="color:#c86060; margin-top:20px;">{{ error }}</p>
  </div>
</div>
</body>
</html>"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NOBU Opening Dinner – Vendéglista Dashboard</title>
<style>{{ style|safe }}</style>
</head>
<body>
<div class="container">

  <!-- Header -->
  <div class="header">
    {{ logo|safe }}
    <h1>VENDÉGLISTA DASHBOARD</h1>
    <p class="ts">
      Utolsó frissítés: 
      <span id="refreshTime">...</span>
      &nbsp;|&nbsp; 
      Összes vendég: <strong>{{ data.total_guests }}</strong>
      &nbsp;|&nbsp;
      {{ data.campaign_ids_found }} NOBU kampány követve
    </p>
    <p class="refresh">
      <a href="/dashboard">🔄 Frissítés most</a>
      &nbsp;|&nbsp; Auto-frissítés 5 percenként
    </p>
  </div>

  <!-- Tabs -->
  <div class="tabs">
    <button class="tab-btn active" onclick="switchTab('osszesen')">📋 Összesen</button>
    <button class="tab-btn" onclick="switchTab('szerda')">📅 Szerda • Május 27.</button>
    <button class="tab-btn" onclick="switchTab('csutortok')">📅 Csütörtök • Május 28.</button>
  </div>

  <!-- Tab: ÖSSZESEN -->
  <div id="tab-osszesen" class="tab-panel active">
    <div class="summary">
      <div class="summary-box jon"><div class="label">✅ Jön</div><div class="count">{{ data.szerda.jon|length + data.csutortok.jon|length }}</div></div>
      <div class="summary-box nemjon"><div class="label">❌ Nem jön</div><div class="count">{{ data.szerda.nem_jon|length + data.csutortok.nem_jon|length }}</div></div>
      <div class="summary-box megnyitotta"><div class="label">👁 Megnyitotta</div><div class="count">{{ data.szerda.jon|length + data.csutortok.jon|length + data.szerda.nem_jon|length + data.csutortok.nem_jon|length + data.szerda.megnyitotta|length + data.csutortok.megnyitotta|length }}</div></div>
      <div class="summary-box nemnyitotta"><div class="label">⬜ Nem nyitotta</div><div class="count">{{ data.szerda.nem_nyitotta|length + data.csutortok.nem_nyitotta|length }}</div></div>
      <div class="summary-box visszapattant"><div class="label">↩️ Visszapattant</div><div class="count">{{ data.szerda.visszapattant|length + data.csutortok.visszapattant|length }}</div></div>
    </div>
  </div>

  <!-- Tab: SZERDA -->
  <div id="tab-szerda" class="tab-panel">
    <div class="summary">
      <div class="summary-box jon"><div class="label">✅ Jön</div><div class="count">{{ data.szerda.jon|length }}</div></div>
      <div class="summary-box nemjon"><div class="label">❌ Nem jön</div><div class="count">{{ data.szerda.nem_jon|length }}</div></div>
      <div class="summary-box megnyitotta"><div class="label">👁 Megnyitotta</div><div class="count">{{ data.szerda.jon|length + data.szerda.nem_jon|length + data.szerda.megnyitotta|length }}</div></div>
      <div class="summary-box nemnyitotta"><div class="label">⬜ Nem nyitotta</div><div class="count">{{ data.szerda.nem_nyitotta|length }}</div></div>
      <div class="summary-box visszapattant"><div class="label">↩️ Visszapattant</div><div class="count">{{ data.szerda.visszapattant|length }}</div></div>
    </div>
    {% set szerda_total = data.szerda.jon|length + data.szerda.nem_jon|length + data.szerda.megnyitotta|length + data.szerda.nem_nyitotta|length + data.szerda.visszapattant|length %}
    {% if szerda_total == 0 %}
    <div class="no-data">Még nincs vendég ezen a napon.</div>
    {% else %}
    <table>
      <thead><tr><th>Név / Email</th><th>Státusz</th><th>Kapcsoló</th></tr></thead>
      <tbody>
        {% for g in data.szerda.jon %}<tr><td>{{ g.name }}</td><td><span class="badge badge-jon">✅ JÖN</span></td><td><label class="switch"><input type="checkbox" class="rsvp-toggle" data-email="{{ g.email }}" {% if g.rsvp and ('✅' in g.rsvp or 'igen' in g.rsvp.lower()) %}checked{% endif %}><span class="slider"></span></label></td></tr>{% endfor %}
        {% for g in data.szerda.nem_jon %}<tr><td>{{ g.name }}</td><td><span class="badge badge-nemjon">❌ NEM JÖN</span></td><td><label class="switch"><input type="checkbox" class="rsvp-toggle" data-email="{{ g.email }}" {% if g.rsvp and ('✅' in g.rsvp or 'igen' in g.rsvp.lower()) %}checked{% endif %}><span class="slider"></span></label></td></tr>{% endfor %}
        {% for g in data.szerda.megnyitotta %}<tr><td>{{ g.name }}</td><td><span class="badge badge-megnyitotta">👁 MEGNYITOTTA</span></td><td><label class="switch"><input type="checkbox" class="rsvp-toggle" data-email="{{ g.email }}" {% if g.rsvp and ('✅' in g.rsvp or 'igen' in g.rsvp.lower()) %}checked{% endif %}><span class="slider"></span></label></td></tr>{% endfor %}
        {% for g in data.szerda.nem_nyitotta %}<tr><td>{{ g.name }}</td><td><span class="badge badge-nemnyitotta">⬜ NEM NYITOTTA</span></td><td><label class="switch"><input type="checkbox" class="rsvp-toggle" data-email="{{ g.email }}" {% if g.rsvp and ('✅' in g.rsvp or 'igen' in g.rsvp.lower()) %}checked{% endif %}><span class="slider"></span></label></td></tr>{% endfor %}
        {% for g in data.szerda.visszapattant %}<tr><td>{{ g.name }}</td><td><span class="badge badge-visszapattant">↩️ VISSZAPATTANT</span></td><td><label class="switch"><input type="checkbox" class="rsvp-toggle" data-email="{{ g.email }}" {% if g.rsvp and ('✅' in g.rsvp or 'igen' in g.rsvp.lower()) %}checked{% endif %}><span class="slider"></span></label></td></tr>{% endfor %}
      </tbody>
    </table>
    {% endif %}
  </div>

  <!-- Tab: CSÜTÖRTÖK -->
  <div id="tab-csutortok" class="tab-panel">
    <div class="summary">
      <div class="summary-box jon"><div class="label">✅ Jön</div><div class="count">{{ data.csutortok.jon|length }}</div></div>
      <div class="summary-box nemjon"><div class="label">❌ Nem jön</div><div class="count">{{ data.csutortok.nem_jon|length }}</div></div>
      <div class="summary-box megnyitotta"><div class="label">👁 Megnyitotta</div><div class="count">{{ data.csutortok.jon|length + data.csutortok.nem_jon|length + data.csutortok.megnyitotta|length }}</div></div>
      <div class="summary-box nemnyitotta"><div class="label">⬜ Nem nyitotta</div><div class="count">{{ data.csutortok.nem_nyitotta|length }}</div></div>
      <div class="summary-box visszapattant"><div class="label">↩️ Visszapattant</div><div class="count">{{ data.csutortok.visszapattant|length }}</div></div>
    </div>
    {% set csutortok_total = data.csutortok.jon|length + data.csutortok.nem_jon|length + data.csutortok.megnyitotta|length + data.csutortok.nem_nyitotta|length + data.csutortok.visszapattant|length %}
    {% if csutortok_total == 0 %}
    <div class="no-data">Még nincs vendég ezen a napon.</div>
    {% else %}
    <table>
      <thead><tr><th>Név / Email</th><th>Státusz</th><th>Kapcsoló</th></tr></thead>
      <tbody>
        {% for g in data.csutortok.jon %}<tr><td>{{ g.name }}</td><td><span class="badge badge-jon">✅ JÖN</span></td><td><label class="switch"><input type="checkbox" class="rsvp-toggle" data-email="{{ g.email }}" {% if g.rsvp and ('✅' in g.rsvp or 'igen' in g.rsvp.lower()) %}checked{% endif %}><span class="slider"></span></label></td></tr>{% endfor %}
        {% for g in data.csutortok.nem_jon %}<tr><td>{{ g.name }}</td><td><span class="badge badge-nemjon">❌ NEM JÖN</span></td><td><label class="switch"><input type="checkbox" class="rsvp-toggle" data-email="{{ g.email }}" {% if g.rsvp and ('✅' in g.rsvp or 'igen' in g.rsvp.lower()) %}checked{% endif %}><span class="slider"></span></label></td></tr>{% endfor %}
        {% for g in data.csutortok.megnyitotta %}<tr><td>{{ g.name }}</td><td><span class="badge badge-megnyitotta">👁 MEGNYITOTTA</span></td><td><label class="switch"><input type="checkbox" class="rsvp-toggle" data-email="{{ g.email }}" {% if g.rsvp and ('✅' in g.rsvp or 'igen' in g.rsvp.lower()) %}checked{% endif %}><span class="slider"></span></label></td></tr>{% endfor %}
        {% for g in data.csutortok.nem_nyitotta %}<tr><td>{{ g.name }}</td><td><span class="badge badge-nemnyitotta">⬜ NEM NYITOTTA</span></td><td><label class="switch"><input type="checkbox" class="rsvp-toggle" data-email="{{ g.email }}" {% if g.rsvp and ('✅' in g.rsvp or 'igen' in g.rsvp.lower()) %}checked{% endif %}><span class="slider"></span></label></td></tr>{% endfor %}
        {% for g in data.csutortok.visszapattant %}<tr><td>{{ g.name }}</td><td><span class="badge badge-visszapattant">↩️ VISSZAPATTANT</span></td><td><label class="switch"><input type="checkbox" class="rsvp-toggle" data-email="{{ g.email }}" {% if g.rsvp and ('✅' in g.rsvp or 'igen' in g.rsvp.lower()) %}checked{% endif %}><span class="slider"></span></label></td></tr>{% endfor %}
      </tbody>
    </table>
    {% endif %}
  </div>

  <!-- Legend -->
  <div class="legend">
    <span><span class="badge badge-jon">JÖN</span> Visszajelzett: ott lesz</span>
    <span><span class="badge badge-nemjon">NEM JÖN</span> Visszajelzett: nem tud jönni</span>
    <span><span class="badge badge-megnyitotta">MEGNYITOTTA</span> Összes megnyitás (jön + nem jön + csak megnyitotta)</span>
    <span><span class="badge badge-nemnyitotta">NEM NYITOTTA</span> Még nem nyitotta meg</span>
    <span><span class="badge badge-visszapattant">VISSZAPATTANT</span> Nem kézbesíthető</span>
  </div>

  <!-- Errors -->
  {% if data.errors %}
  <div class="errors">
    <div class="err-title">⚠️ Figyelmeztetések:</div>
    {% for e in data.errors %}<div>• {{ e }}</div>{% endfor %}
  </div>
  {% endif %}

  <!-- Footer -->
  <div style="text-align:center;padding:30px 0;color:#3a3a5a;font-size:10px;">
    nobu-rsvp-dashboard v2.1 &nbsp;|&nbsp; Frissítés: 30 percenként
  </div>

</div>

<script>
var DASHBOARD_TOKEN = "{{ token|safe }}";
// Format timestamp
(function() {
  var ts = {{ data.ts }};
  var d = new Date(ts * 1000);
  var diffMin = Math.floor((Date.now() - d) / 60000);
  var timeStr = d.toLocaleString('hu-HU', {timeZone: 'Europe/Budapest'});
  var agoStr = diffMin < 1 ? 'most' : (diffMin + ' perce');
  document.getElementById('refreshTime').textContent = timeStr + ' (' + agoStr + ')';
})();

// Tab switching
function switchTab(tab) {
  document.querySelectorAll('.tab-btn').forEach(function(b) { b.classList.remove('active'); });
  document.querySelectorAll('.tab-panel').forEach(function(p) { p.classList.remove('active'); });
  event.target.classList.add('active');
  document.getElementById('tab-' + tab).classList.add('active');
}

// RSVP toggle handler — sends toggle changes to server → Mailchimp
document.querySelectorAll('.rsvp-toggle').forEach(function(toggle) {
  toggle.addEventListener('change', function() {
    var email = this.dataset.email;
    var value = this.checked ? 'jon' : 'nem';
    var toggleEl = this;
    var row = toggleEl.closest('tr');
    var badge = row ? row.querySelector('.badge') : null;
    
    toggleEl.disabled = true;
    
    fetch('/toggle-rsvp?token=' + encodeURIComponent(DASHBOARD_TOKEN), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email, value: value })
    }).then(function(resp) {
      return resp.json();
    }).then(function(data) {
      if (data.status === 'ok') {
        if (badge) {
          if (value === 'jon') {
            badge.className = 'badge badge-jon';
            badge.textContent = '✅ JÖN';
          } else {
            badge.className = 'badge badge-nemjon';
            badge.textContent = '❌ NEM JÖN';
          }
        }
      } else {
        toggleEl.checked = !toggleEl.checked;
        alert('Hiba: ' + (data.msg || 'Ismeretlen hiba'));
      }
    }).catch(function(err) {
      toggleEl.checked = !toggleEl.checked;
      alert('Hálózati hiba történt. Próbáld újra!');
    }).finally(function() {
      toggleEl.disabled = false;
    });
  });
});

// Auto-refresh every 5 minutes
setTimeout(function() { location.reload(); }, 300000);
</script>

</body>
</html>"""



# ══════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    host = os.environ.get("HOST", "0.0.0.0")
    
    # Start background refresh thread
    if REFRESH_MINUTES > 0:
        bg_thread = threading.Thread(target=background_refresh_loop, daemon=True)
        bg_thread.start()
        print(f"[BG] Background refresh started (every {REFRESH_MINUTES} min)", flush=True)
    
    # Initial cache warm-up (async, won't block startup)
    def warmup():
        time.sleep(2)  # Let Flask start first
        refresh_cache()
    threading.Thread(target=warmup, daemon=True).start()
    
    print(f"🦅 Nobu RSVP + Dashboard (Flask) running on http://{host}:{port}", flush=True)
    print(f"   Feliratás (szerda):    http://{host}:{port}/szerda", flush=True)
    print(f"   Feliratás (csütörtök): http://{host}:{port}/csutortok", flush=True)
    print(f"   RSVP:                   http://{host}:{port}/rsvp", flush=True)
    print(f"   Dashboard:              http://{host}:{port}/dashboard", flush=True)
    print(f"   Refresh (force):        http://{host}:{port}/refresh", flush=True)
    print(f"   Health:                 http://{host}:{port}/health", flush=True)
    
    app.run(host=host, port=port, debug=False)
