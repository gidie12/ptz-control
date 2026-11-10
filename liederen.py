"""Liederen: welke nummers er gezongen worden, uit ProPresenter en/of de Voordienst app → activiteitenlog.

🧑 Op verzoek (10 nov 2026): koppeling met API's, bijv. de playlist van nummers die gezongen worden.
Bronnen: ProPresenter (netwerk-API) en de Voordienst app (🧑 heette eerst "draaiboek"; niet meer zo noemen),
met 🧑 een vast API-token (``Authorization: Bearer <token>``; eerst OAuth gebouwd, op verzoek vervangen). Wat er gebeurt: elk nummer komt in het
activiteitenlog (soort ``lied``). Geen camera-acties: de app beweegt hierdoor niets.

ProPresenter (7.9+, officiële HTTP-API; in ProPresenter: Instellingen → Netwerk → *Netwerk inschakelen*):
- ``GET /version``                         → ``{name, host_description, api_version}``
- ``GET /v1/presentation/active``          → ``{"presentation": {"id": {uuid, name, index}, …} | null}``
  (oudere versies: ``/v1/presentation/current``, zelfde vorm)
- ``GET /v1/playlist/active``              → ``{"presentation": {"playlist": {uuid,name,index}, "item": {…}}, …}``
  (oudere versies: ``/v1/playlist/current`` → ``{uuid, name, index}``)
- ``GET /v1/playlist/{uuid}``              → ``{"id": {…}, "items": [{"id": {name,…}, "type": "presentation"|"header"}]}``
Alleen GET's: niets wijzigt in ProPresenter.

Voordienst app: een URL (http/https, met of zonder bearer token) of een bestand op deze computer (``.json`` / ``.txt``).
JSON: een lijst (tekst of objecten) of een object met ``liederen``/``nummers``/``songs``/``items``/``playlist``;
titel uit ``titel``/``title``/``naam``/``dienst``; optioneel huidig nummer in ``huidig``/``current`` (index vanaf 1 of naam).
Objecten met een ``type``/``soort``: alleen lied/song/zang/nummer tellen, als die er zijn. Tekst: één nummer per regel.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

POLL_PP = 2          # s, ProPresenter (huidig nummer)
POLL_PP_LIST = 30    # s, playlist-inhoud opnieuw ophalen
POLL_DB = 30         # s, Voordienst app
TIMEOUT = 3
MAX_ITEMS = 100

DEFAULTS = {"pp": {"enabled": False, "host": "", "port": 50001},
            "voordienst": {"enabled": False, "url": "", "token": ""}}
WHO = "Voordienst app"

LIST_KEYS = ("liederen", "nummers", "songs", "items", "playlist", "lijst", "lied")
TITLE_KEYS = ("titel", "title", "naam", "name", "dienst", "datum", "date")
NAME_KEYS = ("titel", "title", "naam", "name", "lied", "song", "nummer", "tekst", "text")
CUR_KEYS = ("huidig", "current", "actief", "active", "nu", "now")
SONG_TYPES = ("lied", "liederen", "song", "zang", "nummer", "worship", "aanbidding")


def norm_cfg(m):
    m = m if isinstance(m, dict) else {}
    pp = m.get("pp") if isinstance(m.get("pp"), dict) else {}
    db = m.get("voordienst") if isinstance(m.get("voordienst"), dict) else m.get("draaiboek")   # oude naam
    db = db if isinstance(db, dict) else {}
    host = str(pp.get("host") or "").strip()
    host = re.sub(r"^https?://", "", host).strip("/")[:120]
    if ":" in host and host.count(":") == 1:          # host:poort in één veld
        host, p = host.split(":", 1)
        if p.isdigit():
            pp = {**pp, "port": p}
    try:
        port = max(1, min(65535, int(pp.get("port") or DEFAULTS["pp"]["port"])))
    except (TypeError, ValueError):
        port = DEFAULTS["pp"]["port"]
    return {"pp": {"enabled": bool(pp.get("enabled")), "host": host, "port": port},
            "voordienst": {"enabled": bool(db.get("enabled")), "url": str(db.get("url") or "").strip()[:500],
                           "token": re.sub(r"^\s*bearer\s+", "", str(db.get("token") or ""), flags=re.I).strip()[:2000]}}


def public_cfg(cfg):
    """Config voor de UI (ook meekijkers): zonder het token zelf."""
    out = json.loads(json.dumps(norm_cfg(cfg)))
    out["voordienst"]["token_set"] = bool(out["voordienst"].pop("token"))
    return out


def _key(s):
    """Naam vergelijkbaar maken: kleine letters, alleen letters/cijfers."""
    return re.sub(r"[^0-9a-z]+", " ", str(s or "").lower()).strip()


def match(name, items):
    """Index (0-based) van ``name`` in ``items`` (namen), of None. Gelijk, of de een bevat de ander."""
    k = _key(name)
    if not k:
        return None
    keys = [_key(i) for i in items]
    for i, ik in enumerate(keys):
        if ik == k:
            return i
    for i, ik in enumerate(keys):
        # op woordgrenzen, zodat "psalm 1" niet in "psalm 134" past
        if len(ik) >= 4 and len(k) >= 4 and (f" {ik} " in f" {k} " or f" {k} " in f" {ik} "):
            return i
    return None


def reason(e):
    """Foutmelding in gewone taal."""
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code}" + {404: " (adres niet gevonden)", 401: " (token ontbreekt, is ongeldig of verlopen)",
                                   403: " (geen toegang voor dit account)"}.get(e.code, "")
    r = getattr(e, "reason", None) or e
    if isinstance(r, ConnectionRefusedError):
        return "verbinding geweigerd (programma niet gestart of verkeerde poort?)"
    if isinstance(r, (TimeoutError,)) or "timed out" in str(r):
        return "geen antwoord (klopt het adres?)"
    if isinstance(r, FileNotFoundError):
        return "bestand niet gevonden"
    if isinstance(r, PermissionError):
        return "geen toegang tot het bestand"
    return str(r)


# ---------------------------------------------------------------- Voordienst app
def _item_name(x):
    if isinstance(x, str):
        return x.strip()
    if isinstance(x, dict):
        for k in NAME_KEYS:
            if isinstance(x.get(k), (str, int)) and str(x.get(k)).strip():
                return str(x[k]).strip()
    return ""


def parse_voordienst(text):
    """→ {title, items: [namen], current: index|None}. Gooit ValueError als er niets bruikbaars in staat."""
    text = (text or "").lstrip("﻿")
    data = None
    if text.strip()[:1] in ("[", "{"):
        try:
            data = json.loads(text)
        except ValueError as e:
            raise ValueError(f"geen geldige JSON ({e})") from None
    title, cur, raw = "", None, None
    if data is None:
        raw = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "//")):
                continue
            line = re.sub(r"^(\d{1,3}[.)]|[-*•▶])\s*", "", line).strip()
            if line:
                raw.append(line)
    elif isinstance(data, list):
        raw = data
    elif isinstance(data, dict):
        for k in TITLE_KEYS:
            if isinstance(data.get(k), str) and not isinstance(data.get(k), (list, dict)):
                title = data[k].strip()
                break
        for k in LIST_KEYS:
            if isinstance(data.get(k), list):
                raw = data[k]
                break
        for k in CUR_KEYS:
            if k in data and data[k] not in (None, ""):
                cur = data[k]
                break
        if raw is None:
            raise ValueError("geen lijst met liederen gevonden (verwacht bijv. \"liederen\": [...])")
    else:
        raise ValueError("onbekend formaat")
    typed = [x for x in raw if isinstance(x, dict) and (x.get("type") or x.get("soort"))]
    if typed and any(str(x.get("type") or x.get("soort")).lower() in SONG_TYPES for x in typed):
        raw = [x for x in raw if not isinstance(x, dict) or str(x.get("type") or x.get("soort") or "lied").lower() in SONG_TYPES]
    items = [n[:120] for n in (_item_name(x) for x in raw) if n][:MAX_ITEMS]
    if not items:
        raise ValueError("geen liederen gevonden")
    idx = None
    if isinstance(cur, bool):
        cur = None
    if isinstance(cur, int) or (isinstance(cur, str) and cur.strip().isdigit()):
        n = int(cur)
        idx = n - 1 if 1 <= n <= len(items) else None
    elif isinstance(cur, (str, dict)):
        idx = match(_item_name(cur), items)
    return {"title": title[:120], "items": items, "current": idx}


def read_source(src, token=None):
    """Tekst van een URL (eventueel met bearer token) of een lokaal bestand (alleen .json/.txt)."""
    src = (src or "").strip()
    if not src:
        raise ValueError("geen adres ingesteld")
    if re.match(r"^https?://", src, re.I):
        headers = {"User-Agent": "PTZ-Control", "Cache-Control": "no-cache", "Accept": "application/json, text/plain"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(src, headers=headers)
        with urllib.request.urlopen(req, timeout=TIMEOUT + 2) as r:   # noqa: S310 (adres stelt de gebruiker zelf in)
            return r.read(2_000_000).decode("utf-8", errors="replace")
    if token:
        raise ValueError("met een token moet het adres met https:// of http:// beginnen")
    if src.lower().startswith("file://"):
        src = urllib.parse.unquote(urllib.parse.urlparse(src).path)
    path = os.path.expanduser(src)
    # ⚠️ alleen .json/.txt: de server leest het bestand, iedereen met bedienrecht kan het pad instellen
    if os.path.splitext(path)[1].lower() not in (".json", ".txt"):
        raise ValueError("alleen een .json- of .txt-bestand, of een http(s)-adres")
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read(2_000_000)


# ---------------------------------------------------------------- ProPresenter
class ProPresenter:
    def __init__(self, host, port):
        self.base = f"http://{host}:{port}"
        self.paths = {}          # welke variant werkt: active of current

    def get(self, path):
        req = urllib.request.Request(self.base + path, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:     # noqa: S310
            body = r.read(4_000_000)
        return json.loads(body.decode("utf-8", errors="replace") or "null")

    def get_any(self, kind, variants):
        """Eerst de variant die eerder werkte; bij 404 de volgende (ProPresenter 7.9 heet 'current', later 'active')."""
        order = [self.paths[kind]] + [v for v in variants if v != self.paths[kind]] if kind in self.paths else list(variants)
        last = None
        for p in order:
            try:
                out = self.get(p)
                self.paths[kind] = p
                return out
            except urllib.error.HTTPError as e:
                if e.code in (404, 405):
                    last = e
                    continue
                raise
        raise last or RuntimeError("niet gevonden")

    def version(self):
        v = self.get("/version")
        return (v or {}).get("host_description") or (v or {}).get("name") or "ProPresenter"

    def presentation(self):
        """→ {uuid, name} van de actieve presentatie, of None."""
        j = self.get_any("pres", ("/v1/presentation/active", "/v1/presentation/current"))
        p = (j or {}).get("presentation") if isinstance(j, dict) else None
        ident = (p or {}).get("id") if isinstance(p, dict) else None
        if not isinstance(ident, dict) or not ident.get("name"):
            return None
        return {"uuid": ident.get("uuid") or ident.get("name"), "name": str(ident["name"])[:160]}

    def playlist(self):
        """→ {uuid, name, item: index|None} van de actieve playlist, of None."""
        j = self.get_any("pl", ("/v1/playlist/active", "/v1/playlist/current"))
        if not isinstance(j, dict):
            return None
        if "presentation" in j or "announcements" in j:
            p = j.get("presentation") or {}
            pl, item = p.get("playlist"), p.get("item")
        else:
            pl, item = j, None
        if not isinstance(pl, dict) or not (pl.get("uuid") or pl.get("name")):
            return None
        idx = item.get("index") if isinstance(item, dict) else None
        if isinstance(item, dict) and isinstance(item.get("id"), dict):
            idx = item["id"].get("index", idx)
        return {"uuid": pl.get("uuid") or pl.get("name"), "name": str(pl.get("name") or "")[:120],
                "item": idx if isinstance(idx, int) else None}

    def playlist_items(self, uuid):
        j = self.get("/v1/playlist/" + urllib.parse.quote(str(uuid), safe=""))
        out = []
        for it in (j or {}).get("items") or []:
            ident = it.get("id") or {}
            name = str(ident.get("name") or "").strip()
            if not name:
                continue
            out.append({"name": name[:160], "header": it.get("type") == "header",
                        "index": ident.get("index"), "uuid": ident.get("uuid") or ""})
        return out[:MAX_ITEMS * 2]


# ---------------------------------------------------------------- watcher
class Watcher:
    """Leest ProPresenter (elke 2 s) en de Voordienst app (elke 30 s) en meldt nieuwe nummers.

    ``on_state(snapshot)`` bij elke wijziging (SSE ``liederen``), ``on_log(msg, kind, who)`` voor het activiteitenlog.
    """

    def __init__(self, get_cfg, on_state, on_log):
        self.get_cfg = get_cfg
        self.on_state = on_state
        self.on_log = on_log
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._cfg = None
        self._dirty = True
        self._pp = None
        self._pp_list_at = 0
        self._db_at = 0
        self._run = threading.Lock()
        self._logged = {"pl": None, "cur": None}   # laatst gelogde playlist/presentatie (ook over een verbindingsonderbreking)      # loop en "nu controleren" niet tegelijk
        self.state = self._empty()

    @staticmethod
    def _empty():
        return {"type": "liederen",
                "pp": {"enabled": False, "connected": False, "error": None, "version": None,
                       "current": None, "playlist": None, "items": [], "pos": None, "checked": None},
                "voordienst": {"enabled": False, "ok": False, "error": None, "title": "", "items": [],
                               "current": None, "playing": None, "checked": None}}

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="liederen").start()

    def refresh(self):
        """Na een wijziging van de instellingen of workspace: alles opnieuw (zonder dubbele logregels)."""
        self._dirty = True
        self._wake.set()

    def snapshot(self):
        with self._lock:
            return json.loads(json.dumps(self.state))

    def check_now(self):
        self._db_at = 0
        self._pp_list_at = 0
        self._once()
        return self.snapshot()

    def _once(self):
        with self._run:
            return self._round()

    def _loop(self):
        while True:
            self._wake.clear()
            try:
                wait = self._once()
            except Exception as e:  # noqa: BLE001  (mag de server nooit stoppen)
                print(f"Liederen: {e}")
                wait = 10
            self._wake.wait(wait)

    # ------------------------------------------------------------ één ronde
    def _round(self):
        try:
            cfg = norm_cfg(self.get_cfg())
        except Exception:  # noqa: BLE001
            cfg = norm_cfg({})
        if cfg != self._cfg or self._dirty:
            self._dirty = False
            old = self._cfg
            self._cfg = cfg
            self._pp = ProPresenter(cfg["pp"]["host"], cfg["pp"]["port"]) if cfg["pp"]["host"] else None
            self._pp_list_at = 0
            self._db_at = 0
            with self._lock:
                if old is None or old["pp"] != cfg["pp"]:
                    self.state["pp"] = self._empty()["pp"]
                    self._logged = {"pl": None, "cur": None}
                if old is None or old["voordienst"] != cfg["voordienst"]:
                    self.state["voordienst"] = self._empty()["voordienst"]
                self.state["pp"]["enabled"] = cfg["pp"]["enabled"]
                self.state["voordienst"]["enabled"] = cfg["voordienst"]["enabled"]
            self._emit()
        now = time.time()
        if cfg["voordienst"]["enabled"] and now - self._db_at >= POLL_DB:
            self._db_at = now
            self._check_db(cfg["voordienst"])
        if cfg["pp"]["enabled"]:
            self._check_pp(now)
            return POLL_PP
        return min(POLL_DB, 3600) if cfg["voordienst"]["enabled"] else 3600

    def _emit(self):
        self.on_state(self.snapshot())

    def _log(self, msg, kind, who):
        try:
            self.on_log(msg, kind, who)
        except Exception as e:  # noqa: BLE001
            print(f"Liederen-log: {e}")

    # ------------------------------------------------------------ Voordienst app
    def _check_db(self, vc):
        new = {"checked": time.strftime("%H:%M:%S")}
        quiet = not vc["url"]    # nog aan het instellen (geen adres): geen fout in het log
        try:
            d = parse_voordienst(read_source(vc["url"], vc["token"] or None))
            new.update(ok=True, error=None, **d)
        except ValueError as e:
            new.update(ok=False, error=str(e))
        except (urllib.error.URLError, OSError) as e:
            new.update(ok=False, error=reason(e))
        with self._lock:
            st = self.state["voordienst"]
            prev = dict(st)
            st.update(new)
            changed = {k: v for k, v in prev.items() if k != "checked"} != {k: v for k, v in st.items() if k != "checked"}
        if st["ok"]:
            if st["items"] != prev.get("items") or st["title"] != prev.get("title"):
                n = len(st["items"])
                self._log(f"Liederen geladen{': ' + st['title'] if st['title'] else ''} ({n} lied{'eren' if n != 1 else ''}): "
                          + "; ".join(st["items"]), "lied", WHO)
            if st["current"] is not None and st["current"] != prev.get("current"):
                i = st["current"]
                self._log(f"▶ {st['items'][i]} ({i + 1} van {len(st['items'])} in de Voordienst app)", "lied", WHO)
        elif st["error"] != prev.get("error") and not quiet:
            self._log(f"Voordienst app niet te lezen: {st['error']}", "fout", WHO)
        if changed:
            self._emit()

    # ------------------------------------------------------------ ProPresenter
    def _check_pp(self, now):
        pp = self._pp
        with self._lock:
            prev = json.loads(json.dumps(self.state["pp"]))
        new = {"checked": time.strftime("%H:%M:%S")}
        if pp is None:
            new.update(connected=False, error="geen adres ingesteld")
        else:
            try:
                if not prev["connected"]:
                    new["version"] = pp.version()
                cur = pp.presentation()
                pl = pp.playlist()
                items = prev["items"]
                if pl and (now - self._pp_list_at >= POLL_PP_LIST or not prev["playlist"] or prev["playlist"]["uuid"] != pl["uuid"]):
                    try:
                        items = pp.playlist_items(pl["uuid"])
                    except (urllib.error.URLError, OSError, ValueError):
                        items = items if prev["playlist"] and prev["playlist"]["uuid"] == pl["uuid"] else []
                    self._pp_list_at = now
                elif not pl:
                    items = []
                pos = None
                if cur:
                    songs = [i for i in items if not i["header"]]
                    pos = next((k for k, i in enumerate(songs) if i["uuid"] and i["uuid"] == cur["uuid"]), None)
                    if pos is None:
                        pos = match(cur["name"], [i["name"] for i in songs])
                new.update(connected=True, error=None, current=cur, playlist=pl, items=items, pos=pos)
            except (urllib.error.URLError, OSError, ValueError, RuntimeError) as e:
                new.update(connected=False, error=reason(e))
        with self._lock:
            st = self.state["pp"]
            st.update(new)
            db = self.state["voordienst"]
            db_items = db["items"] if db.get("ok") else []
            playing = match(st["current"]["name"], db_items) if (st["connected"] and st["current"] and db_items) else None
            db_changed = playing != db.get("playing")
            db["playing"] = playing
            snap = json.loads(json.dumps(st))
            changed = {k: v for k, v in prev.items() if k != "checked"} != {k: v for k, v in snap.items() if k != "checked"}
        # ---- logregels
        if snap["connected"] and not prev["connected"]:
            self._log(f"ProPresenter verbonden ({snap.get('version') or 'API'})", "systeem", "ProPresenter")
        elif not snap["connected"] and pp is not None and (prev["connected"] or (snap["error"] != prev["error"] and prev["checked"] is None)):
            self._log(f"ProPresenter niet bereikbaar: {snap['error'] or 'geen verbinding'}", "fout", "ProPresenter")
        if snap["connected"]:
            # 🧑 alleen loggen als het echt iets anders is dan wat het laatst in het log kwam: een leeg scherm tussen twee
            # dia's van hetzelfde lied of een korte verbindingsonderbreking geeft geen dubbele regels
            pl = snap["playlist"]
            songs = [i["name"] for i in snap["items"] if not i["header"]]
            key = (pl["uuid"], tuple(songs)) if pl else None
            if pl and key != self._logged["pl"] and (songs or not self._logged["pl"] or self._logged["pl"][0] != pl["uuid"]):
                self._logged["pl"] = key
                self._log(f"Playlist: {pl['name'] or '(zonder naam)'}"
                          + (f" ({len(songs)} items): " + "; ".join(songs) if songs else ""), "lied", "ProPresenter")
            cur = snap["current"]
            if cur and cur["uuid"] != self._logged["cur"]:
                self._logged["cur"] = cur["uuid"]
                where = []
                songs = [i for i in snap["items"] if not i["header"]]
                if snap["pos"] is not None and pl:
                    where.append(f"{snap['pos'] + 1} van {len(songs)} in {pl['name'] or 'de playlist'}")
                if playing is not None:
                    where.append(f"voordienst nr. {playing + 1}")
                self._log(f"▶ {cur['name']}" + (f" ({', '.join(where)})" if where else ""), "lied", "ProPresenter")
        if changed or db_changed:
            self._emit()
