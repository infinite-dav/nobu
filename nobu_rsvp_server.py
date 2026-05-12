#!/usr/bin/env python3
"""
Nobu RSVP Middleware Server
Receives email + RSVP choice → updates Mailchimp contact → redirects to landing page

Usage:
  python3 nobu_rsvp_server.py [--port 8080] [--host 0.0.0.0]

Deploy to render.com:
  - Build Command: pip install flask
  - Start Command: python3 nobu_rsvp_server.py
  - Environment: PORT env var auto-set
"""
import os, sys, json, base64, hashlib, urllib.request, urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── Config ───────────────────────────────────────────────────────
API_KEY = os.environ.get("MAILCHIMP_API_KEY", "")
DC = "us9"
BASE = f"https://{DC}.api.mailchimp.com/3.0"
LIST_ID = "7b625dbadf"
AUTH = base64.b64encode(f"anystring:{API_KEY}".encode()).decode()

# Landing page (published thank-you page)
THANKYOU_YES_URL = "https://mailchi.mp/noburestaurant/nobu-opening-dinner-ott-leszek"
THANKYOU_NO_URL = "https://mailchi.mp/noburestaurant/nobu-opening-dinner-nem-tudok-jonni"

PORT = int(os.environ.get("PORT", 8080))
HOST = os.environ.get("HOST", "0.0.0.0")

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
    """Look up subscriber email by unique_email_id."""
    try:
        result = mc_api("GET", f"/lists/{LIST_ID}/members?unique_email_id={unique_email_id}&fields=members.email_address")
        members = result.get("members", [])
        if members:
            return members[0]["email_address"]
    except Exception as e:
        print(f"Lookup failed for {unique_email_id}: {e}", flush=True)
    return None

def update_rsvp(email, rsvp_value):
    """Update the RSVP merge field for a contact. Creates contact if not exists."""
    subscriber_hash = hashlib.md5(email.lower().encode()).hexdigest()
    try:
        # Try updating existing contact
        mc_api("PATCH", f"/lists/{LIST_ID}/members/{subscriber_hash}", {
            "merge_fields": {"RSVP": rsvp_value}
        })
        return {"status": "updated", "email": email}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # Contact doesn't exist - add them
            try:
                mc_api("POST", f"/lists/{LIST_ID}/members", {
                    "email_address": email,
                    "status": "subscribed",
                    "merge_fields": {"RSVP": rsvp_value}
                })
                return {"status": "created", "email": email}
            except Exception as ex:
                return {"status": "error", "msg": f"Failed to create: {ex}"}
        return {"status": "error", "msg": f"API error {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return {"status": "error", "msg": str(e)}

def subscribe_guest(name, email, day):
    """Subscribe a guest to the list with the correct NAP merge field.
    The tag triggers a Mailchimp automation that sends the invitation email.
    day = 'szerda' or 'csutortok'"""
    nap_value = "Szerda (május 27.)" if day == "szerda" else "Csütörtök (május 28.)"
    subscriber_hash = hashlib.md5(email.lower().encode()).hexdigest()

    merge_fields = {"NAP": nap_value}
    if name:
        # Mailchimp auto-splits Full Name (MMERGE8) into FNAME/LNAME
        merge_fields["MMERGE8"] = name

    try:
        # Try updating existing contact first
        mc_api("PATCH", f"/lists/{LIST_ID}/members/{subscriber_hash}", {
            "merge_fields": merge_fields
        })
        return {"status": "updated", "email": email, "nap": nap_value}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # New subscriber — add with tags to trigger automation
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
        return {"status": "error", "msg": f"API error {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return {"status": "error", "msg": str(e)}

# ── HTTP Server ──────────────────────────────────────────────────
class RSVPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/health":
            self._json({"status": "ok", "service": "nobu-rsvp"})
            return

        # ── Organizer subscription forms ─────────────────────────
        if parsed.path in ("/subscribe/szerda", "/subscribe/csutortok"):
            day = "szerda" if parsed.path.endswith("szerda") else "csutortok"
            params = urllib.parse.parse_qs(parsed.query)
            error = params.get("error", [None])[0]
            self._show_subscribe_form(day, error)
            return

        if parsed.path == "/rsvp":
            params = urllib.parse.parse_qs(parsed.query)
            email = params.get("email", [None])[0]
            uid = params.get("uid", [None])[0]
            choice = params.get("choice", ["no"])[0]

            # If we have a Unique ID but no email, look up the email
            if uid and not email:
                email = lookup_email_by_uid(uid)
                if not email:
                    print(f"Could not find email for uid={uid}", flush=True)
                    self._show_confirmation(choice)  # Show page anyway
                    return

            # If email looks like an unreplaced merge tag → show fallback form
            if email and ("*|" in email or "URLENCODE" in email):
                print(f"Unreplaced merge tag: {email}", flush=True)
                self._show_fallback_form(choice)
                return

            if not email:
                self._show_fallback_form(choice)
                return

            rsvp_value = "✅ Igen, ott leszek!" if choice == "yes" else "❌ Sajnos nem tudok jönni"
            redirect_url = THANKYOU_YES_URL if choice == "yes" else THANKYOU_NO_URL

            # Update Mailchimp
            result = update_rsvp(email, rsvp_value)
            print(f"RSVP: {email} → {rsvp_value} → {result['status']}", flush=True)

            # Show confirmation page directly (no redirect needed)
            self._show_confirmation(choice)
            return

        # Catch-all: show the RSVP form for any other path
        # (handles old emails where merge tags weren't replaced)
        self._show_fallback_form(choice="yes")
        return

    def do_POST(self):
        """Handle form submissions."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode()
        params = urllib.parse.parse_qs(body)
        parsed_path = urllib.parse.urlparse(self.path).path

        # ── Organizer subscription form submit ───────────────────
        if parsed_path in ("/subscribe/szerda", "/subscribe/csutortok"):
            day = "szerda" if parsed_path.endswith("szerda") else "csutortok"
            name = params.get("name", [""])[0].strip()
            email = params.get("email", [""])[0].strip()

            if not email or "@" not in email:
                self._redirect(f"/subscribe/{day}?error=ervenytelen_email")
                return

            result = subscribe_guest(name, email, day)
            print(f"SUBSCRIBE: {name} <{email}> → {result['status']} (NAP={result.get('nap','')})", flush=True)
            self._show_subscribe_done(name, email, day, result)
            return

        # ── RSVP form submit ─────────────────────────────────────
        email = params.get("email", [None])[0]
        choice = params.get("choice", ["yes"])[0]

        if email and "@" in email:
            rsvp_value = "✅ Igen, ott leszek!" if choice == "yes" else "❌ Sajnos nem tudok jönni"
            result = update_rsvp(email, rsvp_value)
            print(f"RSVP (form): {email} → {rsvp_value} → {result['status']}", flush=True)
            self._show_confirmation(choice)
        else:
            self._show_fallback_form(choice, error="Kérjük, adjon meg egy érvényes email címet.")

    def _show_fallback_form(self, choice, error=None):
        """Show a simple email+RSVP form for old emails where merge tags failed."""
        choice_label = "Ott leszek" if choice == "yes" else "Nem tudok jönni"
        error_html = f'<p style="color:#c86060;font-size:13px;margin-bottom:15px;">{error}</p>' if error else ""
        html = f"""<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NOBU Budapest - RSVP</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#181823; color:#c8a960; font-family:Georgia,'Times New Roman',serif; display:flex; align-items:center; justify-content:center; min-height:100vh; padding:20px; }}
.card {{ max-width:460px; width:100%; border:2px solid #c8a960; padding:40px 30px; text-align:center; background:#181823; }}
img.logo {{ max-width:240px; height:auto; margin-bottom:30px; }}
p.info {{ font-size:13px; color:#7a7a8a; margin-bottom:20px; line-height:1.6; }}
input[type="email"] {{ width:100%; padding:12px; background:#0a1628; border:1px solid #3a4a5a; color:#c8a960; font-family:Georgia,serif; font-size:15px; text-align:center; margin-bottom:20px; outline:0; }}
input[type="email"]:focus {{ border-color:#c8a960; }}
.buttons {{ display:flex; gap:10px; justify-content:center; flex-wrap:wrap; }}
.btn {{ font-family:Georgia,serif; font-size:15px; font-weight:bold; padding:12px 28px; border:0; cursor:pointer; text-transform:uppercase; letter-spacing:1px; text-decoration:none; }}
.btn-yes {{ background:#c8a960; color:#181823; }}
.btn-no {{ background:#2a2a3a; color:#c8a960; }}
</style>
</head>
<body>
<div class="card">
  <img src="https://mcusercontent.com/99977b9e1589502e522f30db3/images/8ac32077-ab78-29d0-fc9d-213947a6e0cd.png" alt="NOBU Budapest" class="logo" />
  <p class="info">Kérjük, adja meg az email címét a visszajelzéshez.<br>Köszönjük!</p>
  {error_html}
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
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(html.encode())

    def _redirect(self, url):
        self.send_response(302)
        self.send_header("Location", url)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _show_confirmation(self, choice):
        if choice == "yes":
            title = "Örülünk, hogy részt tud venni az eseményen!"
            message = "Várjuk Önt a megújult Nobu Budapestben!"
            details = ""
            note = "Ha mégis változna a helyzet, a kapott emailben a &bdquo;Nem tudok jönni&rdquo; gombot bármikor megnyomva módosíthatja a visszajelzését."
        else:
            title = "Sajnáljuk, hogy nem tud részt venni!"
            message = "Reméljük, legközelebb tudunk találkozni!"
            details = ""
            note = "Ha mégis úgy alakulna, hogy tud jönni, a kapott emailben az &bdquo;Ott leszek&rdquo; gombot bármikor megnyomva módosíthatja a visszajelzését."

        html = f"""<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NOBU Budapest</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#181823; color:#c8a960; font-family:Georgia,'Times New Roman',serif; display:flex; align-items:center; justify-content:center; min-height:100vh; padding:20px; }}
.card {{ max-width:520px; width:100%; border:2px solid #c8a960; padding:50px 35px; text-align:center; background:#181823; }}
h1 {{ font-size:24px; letter-spacing:3px; text-transform:uppercase; margin-bottom:5px; }}
h2 {{ font-size:12px; letter-spacing:5px; text-transform:uppercase; margin-bottom:35px; color:#a08950; }}
p.title {{ font-size:18px; font-weight:bold; margin-bottom:20px; }}
p.msg {{ font-size:15px; line-height:1.8; margin-bottom:30px; }}
p.note {{ font-size:12px; line-height:1.8; color:#7a7a8a; padding-top:25px; border-top:1px solid #2a2a3a; }}
.details {{ margin:10px 0 20px 0; padding:20px 0; border-top:1px solid #2a2a3a; }}
.details p {{ font-size:13px; line-height:2; color:#b09860; margin:0; }}
</style>
</head>
<body>
<div class="card">
  <img src="https://mcusercontent.com/99977b9e1589502e522f30db3/images/8ac32077-ab78-29d0-fc9d-213947a6e0cd.png" alt="NOBU Budapest" style="max-width:280px;height:auto;margin-bottom:35px;" />
  <p class="title">{title}</p>
  <p class="msg">{message}</p>
  {details}
  <p class="note">{note}</p>
</div>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(html.encode())

    # ── Organizer subscription pages ────────────────────────────
    def _show_subscribe_form(self, day, error=None):
        day_label = "Szerda • Május 27." if day == "szerda" else "Csütörtök • Május 28."
        day_name = "szerda" if day == "szerda" else "csütörtök"
        error_html = ""
        if error == "ervenytelen_email":
            error_html = '<p style="color:#c86060;font-size:13px;margin-bottom:15px;">Kérjük, adjon meg egy érvényes email címet.</p>'

        html = f"""<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NOBU Budapest – Vendég feliratása ({day_name})</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#181823; color:#c8a960; font-family:Georgia,'Times New Roman',serif; display:flex; align-items:center; justify-content:center; min-height:100vh; padding:20px; }}
.card {{ max-width:480px; width:100%; border:2px solid #c8a960; padding:45px 35px; text-align:center; background:#181823; }}
img.logo {{ max-width:220px; height:auto; margin-bottom:15px; }}
h2 {{ font-size:11px; letter-spacing:5px; text-transform:uppercase; color:#a08950; margin-bottom:25px; }}
p.label {{ font-size:12px; color:#7a7a8a; text-transform:uppercase; letter-spacing:2px; margin-bottom:5px; }}
p.day {{ font-size:18px; font-weight:bold; margin-bottom:30px; }}
p.info {{ font-size:13px; color:#7a7a8a; margin-bottom:25px; line-height:1.6; }}
input {{ width:100%; padding:12px; background:#0a1628; border:1px solid #3a4a5a; color:#c8a960; font-family:Georgia,serif; font-size:15px; text-align:center; margin-bottom:12px; outline:0; }}
input:focus {{ border-color:#c8a960; }}
.btn {{ font-family:Georgia,serif; font-size:15px; font-weight:bold; padding:14px 40px; border:0; cursor:pointer; text-transform:uppercase; letter-spacing:2px; background:#c8a960; color:#181823; width:100%; margin-top:10px; }}
.btn:hover {{ background:#d4b870; }}
.back {{ display:block; margin-top:25px; font-size:12px; color:#5a5a7a; }}
.back:hover {{ color:#c8a960; }}
</style>
</head>
<body>
<div class="card">
  <img src="https://mcusercontent.com/99977b9e1589502e522f30db3/images/8ac32077-ab78-29d0-fc9d-213947a6e0cd.png" alt="NOBU Budapest" class="logo" />
  <h2>VENDÉG FELIRATÁSA</h2>
  <p class="day">{day_label}</p>
  <p class="info">Adja meg a vendég nevét és email címét.<br>A vendég azonnal megkapja a meghívót emailben.</p>
  {error_html}
  <form method="POST" action="/subscribe/{day}">
    <input type="text" name="name" placeholder="Vendég teljes neve" />
    <input type="email" name="email" placeholder="Vendég email címe" required />
    <button type="submit" class="btn">Küldés</button>
  </form>
</div>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(html.encode())

    def _show_subscribe_done(self, name, email, day, result):
        day_label = "Szerda • Május 27." if day == "szerda" else "Csütörtök • Május 28."
        display_name = name if name else email
        status_ok = result["status"] in ("created", "updated")

        if status_ok:
            title = f"Meghívó elküldve!"
            message = f"<strong>{display_name}</strong> felkerült a vendéglistára.<br>A(z) <strong>{day_label}</strong> napra szóló meghívót a Mailchimp automation azonnal elküldi a(z) <strong>{email}</strong> címre."
            note = "A vendég a meghívóban található gombokkal jelezheti, hogy részt tud-e venni az eseményen. A visszajelzés a vendéglistán is megjelenik."
            icon = "&#10003;"
        else:
            title = "Hiba történt"
            message = f"Nem sikerült felíratni a vendéget. Hiba: {result.get('msg', 'Ismeretlen hiba')}"
            note = "Kérjük, próbálja újra később, vagy vegye fel a kapcsolatot a rendszergazdával."
            icon = "&#10007;"

        other_day = "csutortok" if day == "szerda" else "szerda"
        other_label = "Csütörtök • Május 28." if day == "szerda" else "Szerda • Május 27."

        html = f"""<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NOBU Budapest – Feliratás kész</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#181823; color:#c8a960; font-family:Georgia,'Times New Roman',serif; display:flex; align-items:center; justify-content:center; min-height:100vh; padding:20px; }}
.card {{ max-width:520px; width:100%; border:2px solid #c8a960; padding:50px 35px; text-align:center; background:#181823; }}
img.logo {{ max-width:220px; height:auto; margin-bottom:10px; }}
h2 {{ font-size:11px; letter-spacing:5px; text-transform:uppercase; color:#a08950; margin-bottom:30px; }}
p.title {{ font-size:18px; font-weight:bold; margin-bottom:20px; }}
p.msg {{ font-size:15px; line-height:1.8; margin-bottom:30px; }}
p.note {{ font-size:12px; line-height:1.8; color:#7a7a8a; padding-top:25px; border-top:1px solid #2a2a3a; }}
.actions {{ display:flex; gap:12px; justify-content:center; flex-wrap:wrap; margin-top:25px; }}
.btn {{ font-family:Georgia,serif; font-size:13px; font-weight:bold; padding:12px 24px; border:1px solid #c8a960; cursor:pointer; text-transform:uppercase; letter-spacing:1px; text-decoration:none; display:inline-block; }}
.btn-gold {{ background:#c8a960; color:#181823; border-color:#c8a960; }}
.btn-outline {{ background:transparent; color:#c8a960; }}
.btn-outline:hover {{ background:#2a2a3a; }}
</style>
</head>
<body>
<div class="card">
  <img src="https://mcusercontent.com/99977b9e1589502e522f30db3/images/8ac32077-ab78-29d0-fc9d-213947a6e0cd.png" alt="NOBU Budapest" class="logo" />
  <h2>VENDÉG FELIRATÁSA</h2>
  <p class="title">{title}</p>
  <p class="msg">{message}</p>
  <p class="note">{note}</p>
  <div class="actions">
    <a href="/subscribe/{day}" class="btn btn-gold">+ Újabb vendég ({day_label})</a>
    <a href="/subscribe/{other_day}" class="btn btn-outline">{other_label}</a>
  </div>
</div>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(html.encode())

    def _json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {args[0]}", flush=True)

# ── Main ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    # CLI overrides
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--port" and i+1 < len(args):
            PORT = int(args[i+1]); i += 2
        elif args[i] == "--host" and i+1 < len(args):
            HOST = args[i+1]; i += 2
        else:
            i += 1

    server = HTTPServer((HOST, PORT), RSVPHandler)
    print(f"🦅 Nobu RSVP Middleware running on http://{HOST}:{PORT}", flush=True)
    print(f"   RSVP:       http://{HOST}:{PORT}/rsvp", flush=True)
    print(f"   Feliratás (szerda):    http://{HOST}:{PORT}/subscribe/szerda", flush=True)
    print(f"   Feliratás (csütörtök): http://{HOST}:{PORT}/subscribe/csutortok", flush=True)
    print(f"   Health:     http://{HOST}:{PORT}/health", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.server_close()
