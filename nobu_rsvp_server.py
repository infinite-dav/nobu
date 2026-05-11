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
THANKYOU_URL = "https://mailchi.mp/noburestaurant/nobu-opening-dinner"

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
            # Contact doesn't exist — add them
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

# ── HTTP Server ──────────────────────────────────────────────────
class RSVPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        
        if parsed.path == "/health":
            self._json({"status": "ok", "service": "nobu-rsvp"})
            return
        
        if parsed.path == "/rsvp":
            params = urllib.parse.parse_qs(parsed.query)
            email = params.get("email", [None])[0]
            choice = params.get("choice", ["no"])[0]
            
            if not email:
                # No email provided — redirect to generic form
                self._redirect(THANKYOU_URL)
                return
            
            rsvp_value = "✅ Igen, ott leszek!" if choice == "yes" else "❌ Sajnos nem tudok jönni"
            
            # Update Mailchimp
            result = update_rsvp(email, rsvp_value)
            print(f"RSVP: {email} → {rsvp_value} → {result['status']}", flush=True)
            
            # Redirect to thank-you page
            self._redirect(THANKYOU_URL)
            return
        
        # Default: show info
        self._json({"service": "nobu-rsvp", "endpoints": ["/rsvp?email=&choice=yes|no", "/health"]})
    
    def _redirect(self, url):
        self.send_response(302)
        self.send_header("Location", url)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
    
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
    print(f"   RSVP: http://{HOST}:{PORT}/rsvp?email=test@example.com&choice=yes", flush=True)
    print(f"   Health: http://{HOST}:{PORT}/health", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.server_close()
