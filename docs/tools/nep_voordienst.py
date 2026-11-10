"""Nagebootste Voordienst app met een bearer token, om de koppeling zonder de echte app te testen.

    python docs/tools/nep_voordienst.py [poort] [--token <token>]

- ``GET /api/liederen``: JSON met de liederen van de dienst, alleen met ``Authorization: Bearer <token>``
  (standaard token ``test-token-123``); anders 401.
- Testbediening: ``/test/huidig?n=<k>`` = huidig nummer (vanaf 1, leeg = geen), ``/test/token?t=<nieuw>`` = ander geldig
  token (het oude werkt dan niet meer), ``/test/stats`` = aantallen verzoeken.
Demodata: verzonnen liederen, geen echte dienst.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = next((int(a) for a in sys.argv[1:] if a.isdigit()), 50020)
TOKEN = sys.argv[sys.argv.index("--token") + 1] if "--token" in sys.argv else "test-token-123"
STATS = {"api": 0, "api_401": 0, "laatste_auth": None}
SONGS = {"titel": "Zondag 10 november", "huidig": None, "items": [
    {"type": "mededeling", "titel": "Welkom"}, {"type": "lied", "titel": "Opwekking 733"},
    {"type": "lied", "titel": "Psalm 42:1 en 3"}, {"type": "lied", "titel": "10.000 redenen"},
    {"type": "preek", "titel": "Ruth 1"}, {"type": "lied", "titel": "Opwekking 710"}]}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, obj, headers=None):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):  # noqa: N802
        global TOKEN
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/api/liederen":
            STATS["api"] += 1
            auth = self.headers.get("Authorization") or ""
            STATS["laatste_auth"] = auth.split(" ")[0] if auth else None
            if auth != "Bearer " + TOKEN:
                STATS["api_401"] += 1
                return self.send(401, {"error": "invalid_token"}, {"WWW-Authenticate": 'Bearer error="invalid_token"'})
            return self.send(200, SONGS)
        if u.path == "/test/huidig":
            SONGS["huidig"] = int(q["n"]) if q.get("n") else None
            return self.send(200, {"ok": True})
        if u.path == "/test/token":
            TOKEN = q.get("t") or TOKEN
            return self.send(200, {"ok": True})
        if u.path == "/test/stats":
            return self.send(200, STATS)
        self.send(404, {"error": "not found"})


if __name__ == "__main__":
    print(f"Nep-Voordienst app op poort {PORT} (token {TOKEN})")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
