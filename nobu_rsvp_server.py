#!/usr/bin/env python3
"""
Nobu RSVP Middleware Server (Flask)
Receives email + RSVP choice → updates Mailchimp contact
Organizer subscription forms for szerda/csutortok → tags subscriber

Deploy to render.com:
  - Build Command: pip install flask
  - Start Command: python3 nobu_rsvp_server.py
  - Environment: PORT env var auto-set
"""
import os, json, base64, hashlib, urllib.request, urllib.error
from flask import Flask, request, render_template_string

app = Flask(__name__)

# ── Config ───────────────────────────────────────────────────────
API_KEY = os.environ.get("MAILCHIMP_API_KEY", "")
DC = "us9"
BASE = f"https://{DC}.api.mailchimp.com/3.0"
LIST_ID = "7b625dbadf"
AUTH = base64.b64encode(f"anystring:{API_KEY}".encode()).decode()

# ── Mailchimp API helper ─────────────────────────────────────────
def mc_api(method, path, data=None):
    url = f"{BASE}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Basic {AUTH}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def lookup_email_by_uid(unique_email_id):
    try:
        result = mc_api("GET", f"/lists/{LIST_ID}/members?unique_email_id={unique_email_id}&fields=members.email_address")
        members = result.get("members", [])
        if members:
            return members[0]["email_address"]
    except Exception as e:
        print(f"Lookup failed for {unique_email_id}: {e}", flush=True)
    return None

def update_rsvp(email, rsvp_value):
    subscriber_hash = hashlib.md5(email.lower().encode()).hexdigest()
    try:
        mc_api("PATCH", f"/lists/{LIST_ID}/members/{subscriber_hash}", {
            "merge_fields": {"RSVP": rsvp_value}
        })
        return {"status": "updated", "email": email}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            try:
                mc_api("POST", f"/lists/{LIST_ID}/members", {
                    "email_address": email,
                    "status": "subscribed",
                    "merge_fields": {"RSVP": rsvp_value}
                })
                return {"status": "created", "email": email}
            except Exception as ex:
                return {"status": "error", "msg": f"Failed to create: {ex}"}
        return {"status": "error", "msg": f"API error {e.code}"}
    except Exception as e:
        return {"status": "error", "msg": str(e)}

def subscribe_guest(name, email, day, pluszfo=None):
    """Subscribe a guest with NAP merge field and tag for Customer Journey trigger."""
    nap_value = "Szerda (május 27.)" if day == "szerda" else "Csütörtök (május 28.)"
    subscriber_hash = hashlib.md5(email.lower().encode()).hexdigest()
    merge_fields = {"NAP": nap_value}
    if name:
        merge_fields["MMERGE8"] = name
    # Additional guest count (PLUSZ / Plusz fo merge field)
    if pluszfo is not None:
        try:
            plusz_int = int(pluszfo)
            merge_fields["PLUSZ"] = max(0, min(plusz_int, 10))
        except (ValueError, TypeError):
            pass
    try:
        mc_api("PATCH", f"/lists/{LIST_ID}/members/{subscriber_hash}", {"merge_fields": merge_fields})
        return {"status": "updated", "email": email, "nap": nap_value}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            try:
                mc_api("POST", f"/lists/{LIST_ID}/members", {
                    "email_address": email,
                    "status": "subscribed",
                    "merge_fields": merge_fields,
                    "tags": [f"opening-{day}"]
                })
                return {"status": "created", "email": email, "nap": nap_value}
            except Exception as ex:
                return {"status": "error", "msg": f"Failed to create: {ex}"}
        return {"status": "error", "msg": f"API error {e.code}"}
    except Exception as e:
        return {"status": "error", "msg": str(e)}

# ── CSS ──────────────────────────────────────────────────────────
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
input, select { width:100%; padding:12px; background:#0a1628; border:1px solid #3a4a5a; color:#c8a960; font-family:Georgia,serif; font-size:15px; text-align:center; margin-bottom:12px; outline:0; appearance:none; -webkit-appearance:none; }
input:focus, select:focus { border-color:#c8a960; }
select { cursor:pointer; background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath fill='%23c8a960' d='M1.41 0L6 4.58 10.59 0 12 1.41l-6 6-6-6z'/%3E%3C/svg%3E"); background-repeat:no-repeat; background-position:right 15px center; padding-right:40px; }
select option { background:#181823; color:#c8a960; }
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

LOGO_HTML = '<img src="https://mcusercontent.com/99977b9e1589502e522f30db3/images/8ac32077-ab78-29d0-fc9d-213947a6e0cd.png" alt="NOBU Budapest" class="logo" />'

# ── Routes ───────────────────────────────────────────────────────
@app.route("/health")
def health():
    return {"status": "ok", "service": "nobu-rsvp", "version": "flask"}

@app.route("/szerda", methods=["GET", "POST"])
@app.route("/csutortok", methods=["GET", "POST"])
def subscribe():
    day = "szerda" if request.path.startswith("/szerda") else "csutortok"
    day_label = "Szerda • Május 27." if day == "szerda" else "Csütörtök • Május 28."
    day_name = "szerda" if day == "szerda" else "csütörtök"
    other_day = "csutortok" if day == "szerda" else "szerda"
    other_label = "Csütörtök • Május 28." if day == "szerda" else "Szerda • Május 27."

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        pluszfo = request.form.get("pluszfo", "0").strip()

        if not email or "@" not in email:
            return render_template_string(SUBSCRIBE_FORM_HTML,
                day=day, day_label=day_label, day_name=day_name,
                error='<p class="error">Kérjük, adjon meg egy érvényes email címet.</p>',
                style=BASE_STYLE, logo=LOGO_HTML)

        result = subscribe_guest(name, email, day, pluszfo=pluszfo)
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

# ── HTML Templates ───────────────────────────────────────────────
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
    <select name="pluszfo">
      <option value="0">Vendégek száma: csak a vendég</option>
      <option value="1">+1 fő</option>
      <option value="2">+2 fő</option>
      <option value="3">+3 fő</option>
      <option value="4">+4 fő</option>
      <option value="5">+5 fő</option>
      <option value="6">+6 fő</option>
      <option value="7">+7 fő</option>
      <option value="8">+8 fő</option>
      <option value="9">+9 fő</option>
      <option value="10">+10 fő</option>
    </select>
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

# ── Main ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"🦅 Nobu RSVP (Flask) running on http://{host}:{port}", flush=True)
    print(f"   Feliratás (szerda):    http://{host}:{port}/szerda", flush=True)
    print(f"   Feliratás (csütörtök): http://{host}:{port}/csutortok", flush=True)
    print(f"   RSVP:       http://{host}:{port}/rsvp", flush=True)
    print(f"   Health:     http://{host}:{port}/health", flush=True)
    app.run(host=host, port=port, debug=False)
