"""
YouTube-livestream van een kanaal herkennen (alleen kijken, niet starten/stoppen).

Werkt zonder API-sleutel: de server haalt https://www.youtube.com/<kanaal>/live op.
Is het kanaal live, dan is dat de kijkpagina van de livestream; daaruit halen we
video-id, titel en of hij nu live is (of alleen gepland).

Alleen standaardbibliotheek.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote

# Voor tests te vervangen door een nep-server (bv. http://127.0.0.1:9999)
BASE = os.environ.get("PTZ_YT_BASE", "https://www.youtube.com").rstrip("/")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
# Zonder deze cookies stuurt YouTube vanuit de EU een toestemmingspagina terug
COOKIES = "SOCS=CAI; CONSENT=YES+cb"
POLL_LIVE = 60       # s tussen controles als er een stream live is
POLL_IDLE = 60       # s tussen controles als er niets live is
POLL_ERROR = 120     # s na een fout


def channel_path(raw):
    """Maak van wat de gebruiker invult een kanaalpad: '/@naam' of '/channel/UC…'.

    Toegestaan: @naam, naam, UC-id, of een volledige YouTube-link (/@, /channel/, /c/, /user/)."""
    s = (raw or "").strip()
    if not s:
        return ""
    s = re.sub(r"^(https?://)?(www\.|m\.)?youtube\.com", "", s, flags=re.I)
    s = s.split("?")[0].split("#")[0].strip("/")
    m = re.match(r"^(channel/UC[\w-]{22}|c/[^/]+|user/[^/]+|@[^/]+)", s)
    if m:
        return "/" + m.group(1)
    if re.fullmatch(r"UC[\w-]{22}", s):
        return "/channel/" + s
    if re.fullmatch(r"[\w.\-]{3,100}", s):
        return "/@" + s
    raise ValueError("Onbekend kanaal. Vul bijv. @kerknaam, een kanaal-id (UC…) of de kanaallink in.")


def _fetch(url, timeout=10):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8", "Cookie": COOKIES})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace"), r.geturl()


def _json_after(html, marker):
    i = html.find(marker)
    if i < 0:
        return None
    j = html.find("{", i)
    try:
        obj, _ = json.JSONDecoder().raw_decode(html[j:])
        return obj
    except ValueError:
        return None


def parse_live_page(html, final_url=""):
    """Haal de status uit de /live-pagina. Geeft dict met live/upcoming/video_id/title/start."""
    out = {"live": False, "upcoming": False, "video_id": None, "title": None, "start": None}
    if "consent.youtube.com" in final_url or 'action="https://consent.youtube.com' in html:
        raise RuntimeError("YouTube vraagt om cookie-toestemming; kon de pagina niet lezen")
    pr = _json_after(html, "ytInitialPlayerResponse")
    vid = None
    if pr:
        vd = pr.get("videoDetails") or {}
        mf = ((pr.get("microformat") or {}).get("playerMicroformatRenderer") or {})
        lb = mf.get("liveBroadcastDetails") or {}
        vid = vd.get("videoId")
        out["title"] = vd.get("title") or (mf.get("title") or {}).get("simpleText")
        live_now = lb.get("isLiveNow")
        if live_now is None:
            live_now = bool(vd.get("isLive")) and not vd.get("isUpcoming")
        out["live"] = bool(live_now)
        out["upcoming"] = bool(vd.get("isUpcoming")) and not out["live"]
        out["start"] = lb.get("startTimestamp")
    if not vid:
        m = re.search(r'<link rel="canonical" href="[^"]*watch\?v=([\w-]{11})', html)
        vid = m.group(1) if m else None
        if vid and not pr:
            out["live"] = '"isLiveNow":true' in html
            out["upcoming"] = not out["live"] and '"isUpcoming":true' in html
    if not (out["live"] or out["upcoming"]):
        vid = None  # kanaalpagina of een afgelopen stream: niets live
    out["video_id"] = vid
    if not vid:
        out["title"] = None
        out["start"] = None
    return out


def check(raw_channel):
    path = channel_path(raw_channel)
    if not path:
        return {"live": False, "upcoming": False, "video_id": None, "title": None, "start": None}
    html, final = _fetch(BASE + quote(path, safe="/@") + "/live")
    return parse_live_page(html, final)


class Watcher:
    """Controleert op de achtergrond of het ingestelde kanaal live is en meldt veranderingen."""

    def __init__(self, get_channel, on_change):
        self.get_channel = get_channel
        self.on_change = on_change
        self.state = {"type": "youtube", "channel": "", "live": False, "upcoming": False,
                      "video_id": None, "title": None, "start": None, "checked": None, "error": None}
        self._wake = threading.Event()
        self._lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="youtube").start()

    def refresh(self):
        self._wake.set()

    def _loop(self):
        while True:
            self._wake.clear()
            wait = self._once()
            self._wake.wait(wait)

    def _once(self):
        try:
            chan = (self.get_channel() or "").strip()
        except Exception:  # noqa: BLE001
            chan = ""
        new = {"channel": chan, "error": None}
        wait = POLL_IDLE
        if not chan:
            new.update(live=False, upcoming=False, video_id=None, title=None, start=None, checked=None)
            wait = 3600
        else:
            try:
                new.update(check(chan))
                wait = POLL_LIVE if new["live"] else POLL_IDLE
            except ValueError as e:
                new.update(live=False, upcoming=False, video_id=None, title=None, start=None, error=str(e))
                wait = 3600
            except (urllib.error.URLError, OSError, RuntimeError) as e:
                # bij een netwerkfout de laatst bekende stream laten staan
                reason = getattr(e, "reason", None) or e
                new["error"] = f"YouTube niet bereikbaar: {reason}"
                wait = POLL_ERROR
            new["checked"] = time.strftime("%H:%M:%S")
        with self._lock:
            before = {k: v for k, v in self.state.items() if k != "checked"}
            self.state.update(new)
            after = {k: v for k, v in self.state.items() if k != "checked"}
            changed = before != after
            snap = dict(self.state)
        # altijd melden: de 'laatst gecontroleerd'-tijd verandert ook
        self.on_change(snap, changed)
        return wait

    def snapshot(self):
        with self._lock:
            return dict(self.state)
