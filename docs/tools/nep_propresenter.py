"""Nagebootste ProPresenter-API (alleen de GET's die PTZ Control gebruikt), om liederen.py zonder ProPresenter te testen.

    python docs/tools/nep_propresenter.py [poort] [--oud]

- ``--oud``: gedraagt zich als ProPresenter 7.9 (alleen ``/v1/presentation/current`` en ``/v1/playlist/current``);
  zonder: nieuwere versies (``/active``, met ``presentation.playlist`` en ``item``).
- Bediening voor tests (geen echte ProPresenter-endpoints):
  ``GET /test/trigger?i=<n>`` = item n (0-based, telt headers mee) uit de playlist op het scherm,
  ``GET /test/clear`` = niets op het scherm, ``GET /test/playlist?name=<naam>`` = andere playlist actief,
  ``GET /test/library?name=<naam>`` = presentatie buiten de playlist op het scherm.
Demodata: verzonnen liederen, geen echte dienst.
"""
import json
import sys
import uuid as _uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse, unquote

OUD = "--oud" in sys.argv
PORT = next((int(a) for a in sys.argv[1:] if a.isdigit()), 50001)


def _u(name):
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL, name)).upper()


PLAYLISTS = {
    "Zondag 10:00": [("Welkom", "header"), ("Welkom en mededelingen", "presentation"), ("Zingen", "header"),
                     ("Opwekking 733 - Heer, U bent mijn leven", "presentation"), ("Psalm 42:1 en 3", "presentation"),
                     ("10.000 redenen", "presentation"), ("Preek", "header"), ("Preek: Ruth 1", "presentation"),
                     ("Opwekking 710 - Tienduizend redenen", "presentation"), ("Zegen", "presentation")],
    "Avonddienst": [("Psalm 134", "presentation"), ("Opwekking 1 - Hoe groot zijt Gij", "presentation")],
}
STATE = {"playlist": "Zondag 10:00", "item": None, "library": None}


def ident(name, index):
    return {"uuid": _u(name) if name else "", "name": name, "index": index}


def current_pres():
    if STATE["library"]:
        return {"id": ident(STATE["library"], 0), "groups": [], "has_timeline": False, "presentation_path": "", "destination": "presentation"}
    if STATE["item"] is None:
        return None
    name, _ = PLAYLISTS[STATE["playlist"]][STATE["item"]]
    return {"id": ident(name, 0), "groups": [{"name": "Couplet 1", "color": None, "slides": [{"enabled": True, "notes": "", "text": "…", "label": ""}]}],
            "has_timeline": False, "presentation_path": "", "destination": "presentation"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        names = list(PLAYLISTS)
        if p == "/version":
            return self.send(200, {"name": "Nep-ProPresenter", "platform": "mac", "os_version": "15.1",
                                   "host_description": "ProPresenter 7.9" if OUD else "ProPresenter 21.3", "api_version": "v1"})
        if p == ("/v1/presentation/current" if OUD else "/v1/presentation/active"):
            return self.send(200, {"presentation": current_pres()})
        if p == ("/v1/playlist/current" if OUD else "/v1/playlist/active"):
            pl = ident(STATE["playlist"], names.index(STATE["playlist"]))
            if OUD:
                return self.send(200, pl)
            item = None
            if STATE["item"] is not None and not STATE["library"]:
                n, t = PLAYLISTS[STATE["playlist"]][STATE["item"]]
                item = {"id": ident(n, STATE["item"]), "type": t, "is_hidden": False, "is_pco": False}
            return self.send(200, {"presentation": {"playlist": pl, "item": item}, "announcements": None})
        if p.startswith("/v1/playlist/") and p.count("/") == 3:
            key = unquote(p.rsplit("/", 1)[1])
            name = next((n for n in names if key in (n, _u(n), str(names.index(n)))), None)
            if not name:
                return self.send(404, {"error": "not found"})
            return self.send(200, {"id": ident(name, names.index(name)),
                                   "items": [{"id": ident(n, i) if t == "presentation" else {"uuid": "", "name": n, "index": i},
                                              "type": t, "is_hidden": False} for i, (n, t) in enumerate(PLAYLISTS[name])]})
        if p == "/test/trigger":
            STATE.update(item=int(q.get("i", ["0"])[0]), library=None)
            return self.send(200, {"ok": True})
        if p == "/test/clear":
            STATE.update(item=None, library=None)
            return self.send(200, {"ok": True})
        if p == "/test/playlist":
            STATE.update(playlist=q.get("name", [names[0]])[0], item=None, library=None)
            return self.send(200, {"ok": True})
        if p == "/test/library":
            STATE.update(library=q.get("name", ["Losse presentatie"])[0])
            return self.send(200, {"ok": True})
        self.send(404, {"error": "not found"})


if __name__ == "__main__":
    print(f"Nep-ProPresenter op poort {PORT}" + (" (7.9: /current)" if OUD else " (/active)"))
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
