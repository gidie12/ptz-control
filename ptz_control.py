#!/usr/bin/env python3
"""
PTZ Control — eigen webbediening voor PTZOptics-camera's.

Start:   python3 ptz_control.py            (poort 8080)
         python3 ptz_control.py --port 9000
Open:    http://localhost:8080  (of http://<ip-van-deze-pc>:8080 op een tablet)

Geen externe packages nodig (alleen Python 3.8+ standaardbibliotheek).
Camera's en preset-namen worden opgeslagen in cameras.json naast dit script.

Protocollen per camera:
  http  -> HTTP-CGI  (http://<ip>/cgi-bin/ptzctrl.cgi?ptzcmd&...)
  visca -> VISCA over IP via TCP (standaard poort 5678)
Autofocus/one-push-AF gaan altijd via VISCA (TCP 5678), ook bij http.
"""
import argparse
import sys
import base64
import datetime
import hashlib
import hmac
import json
import os
import queue
import re
import secrets
import shutil
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import backup as _backup  # noqa: E402
import youtube as _youtube  # noqa: E402
import updater as _updater  # noqa: E402
import midi as _midi  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = bool(getattr(sys, "frozen", False))   # losse app (PyInstaller): .exe / .app


def _data_dir():
    """Waar cameras.json en thumbs/ staan.

    Als script: naast dit bestand (zoals altijd). Als losse app: een vaste, schrijfbare map per gebruiker,
    want de app zelf is alleen-lezen (macOS) of wordt bij elke start uitgepakt (Windows).
    PTZ_DATA kan dit overschrijven; staat er een cameras.json naast de .exe/.app, dan die map (draagbaar)."""
    if os.environ.get("PTZ_DATA"):
        return os.environ["PTZ_DATA"]
    if not FROZEN:
        return HERE
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    if sys.platform == "darwin" and ".app/Contents/MacOS" in exe_dir:
        exe_dir = os.path.abspath(os.path.join(exe_dir, "..", "..", ".."))   # map waar de .app in staat
    if os.path.isfile(os.path.join(exe_dir, "cameras.json")):
        return exe_dir
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "PTZ Control")


RES_DIR = getattr(sys, "_MEIPASS", HERE)       # meegeleverde bestanden (index.html)
DATA_DIR = _data_dir()
os.makedirs(DATA_DIR, exist_ok=True)
INDEX_PATH = os.path.join(RES_DIR, "index.html")

# ---------------------------------------------------------------- workspaces
# 🧑 Elke workspace is een eigen set instellingen (camera's, presets, foto's, YouTube, sneltoetsen, …),
# alsof het een aparte installatie is. "standaard" gebruikt de oude plek (cameras.json + thumbs/ in
# DATA_DIR), zodat bestaande installaties niets hoeven te verhuizen; andere staan in workspaces/<id>/.
WS_FILE = os.path.join(DATA_DIR, "workspaces.json")
WS_DIR = os.path.join(DATA_DIR, "workspaces")
WS_DEFAULT = "standaard"


def ws_paths(ws_id):
    base = DATA_DIR if ws_id == WS_DEFAULT else os.path.join(WS_DIR, ws_id)
    return os.path.join(base, "cameras.json"), os.path.join(base, "thumbs")


def ws_load():
    try:
        with open(WS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    lst = [w for w in data.get("list", []) if isinstance(w, dict) and re.fullmatch(r"[a-z0-9-]{1,40}", str(w.get("id", "")))]
    if not any(w["id"] == WS_DEFAULT for w in lst):
        lst.insert(0, {"id": WS_DEFAULT, "name": "Standaard"})
    active = data.get("active") if any(w["id"] == data.get("active") for w in lst) else WS_DEFAULT
    return {"active": active, "list": lst}


def ws_save(data):
    tmp = WS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, WS_FILE)


WS = ws_load()
CONFIG_PATH, THUMB_DIR = ws_paths(WS["active"])
LOCK = threading.Lock()
TIMEOUT = 2.5

DEFAULT_CONFIG = {
    "cameras": [
        {
            "id": "cam1",
            "name": "Camera 1",
            "host": "192.168.100.88",
            "protocol": "http",
            "visca_port": 5678,
            "user": "",
            "password": "",
            "presets": {},
        }
    ]
}

MOVE_DIRS = {"up", "down", "left", "right", "leftup", "rightup", "leftdown", "rightdown"}


# ---------------------------------------------------------------- config
def load_config():
    with LOCK:
        if not os.path.exists(CONFIG_PATH):
            _write(DEFAULT_CONFIG)
            return json.loads(json.dumps(DEFAULT_CONFIG))
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)


def _write(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_PATH)


def save_config(cfg):
    with LOCK:
        _write(cfg)


def find_camera(ref):
    """Zoek camera op id, volgnummer (1-based) of IP-adres/host."""
    cams = load_config().get("cameras", [])
    ref = str(ref or "").strip()
    for cam in cams:
        if cam.get("id") == ref:
            return cam
    if ref.isdigit() and 1 <= int(ref) <= len(cams):
        return cams[int(ref) - 1]
    for cam in cams:
        if cam.get("host", "") == ref:
            return cam
    host = ref.split(":")[0]
    for cam in cams:
        if cam.get("host", "").split(":")[0] == host:
            return cam
    raise KeyError(f"Onbekende camera: {ref}")


# ---------------------------------------------------------------- live events (SSE)
STATE = {"active": None, "last_preset": {}}
SUBSCRIBERS = []
SUB_LOCK = threading.Lock()


def broadcast(event):
    if event.get("type") == "select":
        STATE["active"] = event["cam"]
    elif event.get("type") == "preset":
        STATE["last_preset"][event["cam"]] = event["n"]
    with SUB_LOCK:
        for q in list(SUBSCRIBERS):
            q.put(event)


# ---------------------------------------------------------------- YouTube (alleen kijken)
def _yt_channel():
    return (load_config().get("youtube") or {}).get("channel", "")


YT = _youtube.Watcher(_yt_channel, lambda st, changed: broadcast(st))


# ---------------------------------------------------------------- videomixer (Roland V-1HD via MIDI)
def _midi_cfg():
    return load_config().get("midi") or {}


def _midi_cams():
    return [{"id": c.get("id"), "name": c.get("name")} for c in load_config().get("cameras", [])]


MIDI = _midi.Watcher(_midi_cfg, _midi_cams, broadcast)


# ---------------------------------------------------------------- updates
def _restart():
    """Server opnieuw starten na bijwerken (zelfde Terminal-venster; browser niet opnieuw openen)."""
    args = [a for a in sys.argv[1:] if a != "--open"]
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, os.path.abspath(__file__), *args])


UPD = _updater.Updater(HERE, FROZEN, RES_DIR, DATA_DIR, broadcast, _restart)


# ---------------------------------------------------------------- helpers
def clamp(v, lo, hi):
    try:
        v = int(v)
    except (TypeError, ValueError):
        v = lo
    return max(lo, min(hi, v))


def http_get(cam, path, binary=False):
    url = f"http://{cam['host']}{path}"
    if cam.get("user"):
        mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        mgr.add_password(None, url, cam["user"], cam.get("password", ""))
        opener = urllib.request.build_opener(
            urllib.request.HTTPBasicAuthHandler(mgr),
            urllib.request.HTTPDigestAuthHandler(mgr),
        )
    else:
        opener = urllib.request.build_opener()
    with opener.open(url, timeout=TIMEOUT) as r:
        data = r.read()
        return (data, r.headers.get("Content-Type", "image/jpeg")) if binary else data.decode(errors="replace")


def visca_send(cam, hexstr):
    payload = bytes.fromhex(hexstr.replace(" ", ""))
    port = int(cam.get("visca_port") or 5678)
    with socket.create_connection((cam["host"].split(":")[0], port), timeout=TIMEOUT) as s:
        s.sendall(payload)
        s.settimeout(0.4)
        try:
            return s.recv(64).hex(" ")
        except socket.timeout:
            return ""


# ---------------------------------------------------------------- commands
def http_command(cam, action, a):
    if action == "move":
        d = a.get("dir")
        if d not in MOVE_DIRS:
            raise ValueError("ongeldige richting")
        q = f"ptzcmd&{d}&{clamp(a.get('pan'), 1, 24)}&{clamp(a.get('tilt'), 1, 20)}"
    elif action == "stop":
        q = "ptzcmd&ptzstop&0&0"
    elif action == "home":
        q = "ptzcmd&home"
    elif action == "zoom":
        d = a.get("dir")
        q = {"in": f"ptzcmd&zoomin&{clamp(a.get('speed'), 1, 7)}",
             "out": f"ptzcmd&zoomout&{clamp(a.get('speed'), 1, 7)}",
             "stop": "ptzcmd&zoomstop&0"}[d]
    elif action == "focus":
        d = a.get("dir")
        q = {"far": f"ptzcmd&focusin&{clamp(a.get('speed'), 1, 7)}",
             "near": f"ptzcmd&focusout&{clamp(a.get('speed'), 1, 7)}",
             "stop": "ptzcmd&focusstop&0"}[d]
    elif action == "preset_call":
        q = f"ptzcmd&poscall&{clamp(a.get('n'), 0, 254)}"
        if a.get("speed"):
            q += f"&{clamp(a.get('speed'), 1, 24)}"
    elif action == "preset_set":
        q = f"ptzcmd&posset&{clamp(a.get('n'), 0, 254)}"
    else:
        return None
    return http_get(cam, f"/cgi-bin/ptzctrl.cgi?{q}")


VISCA_DIR = {
    "up": "03 01", "down": "03 02", "left": "01 03", "right": "02 03",
    "leftup": "01 01", "rightup": "02 01", "leftdown": "01 02", "rightdown": "02 02",
}


def visca_command(cam, action, a):
    if action == "move":
        d = a.get("dir")
        if d not in VISCA_DIR:
            raise ValueError("ongeldige richting")
        p, t = clamp(a.get("pan"), 1, 24), clamp(a.get("tilt"), 1, 20)
        cmd = f"81 01 06 01 {p:02x} {t:02x} {VISCA_DIR[d]} FF"
    elif action == "stop":
        cmd = "81 01 06 01 01 01 03 03 FF"
    elif action == "home":
        cmd = "81 01 06 04 FF"
    elif action == "zoom":
        s = clamp(a.get("speed"), 1, 7)
        cmd = {"in": f"81 01 04 07 2{s} FF", "out": f"81 01 04 07 3{s} FF",
               "stop": "81 01 04 07 00 FF"}[a.get("dir")]
    elif action == "focus":
        s = clamp(a.get("speed"), 1, 7)
        cmd = {"far": f"81 01 04 08 2{s} FF", "near": f"81 01 04 08 3{s} FF",
               "stop": "81 01 04 08 00 FF"}[a.get("dir")]
    elif action == "preset_call":
        cmd = f"81 01 04 3F 02 {clamp(a.get('n'), 0, 254):02x} FF"
    elif action == "preset_set":
        cmd = f"81 01 04 3F 01 {clamp(a.get('n'), 0, 254):02x} FF"
    elif action == "autofocus":
        cmd = "81 01 04 38 02 FF" if a.get("on") else "81 01 04 38 03 FF"
    elif action == "onepush_af":
        cmd = "81 01 04 18 01 FF"
    else:
        raise ValueError(f"onbekende actie: {action}")
    return visca_send(cam, cmd)


def run_command(cam, action, args):
    if cam.get("protocol") == "http":
        result = http_command(cam, action, args)
        if result is not None:
            return result
    return visca_command(cam, action, args)


def thumb_path(cam, n, tdir=None):
    safe = "".join(ch for ch in cam["id"] if ch.isalnum() or ch in "-_")
    return os.path.join(tdir or THUMB_DIR, f"{safe}_{int(n)}.jpg")


def snapshot_thumb(cam, n, tries=2):
    """Sla een snapshot op als thumbnail voor preset n. Geeft "" bij succes, anders de reden."""
    err = ""
    for attempt in range(tries):
        if attempt:
            time.sleep(0.5)
        try:
            data, _ = http_get(cam, "/snapshot.jpg", binary=True)
        except Exception as e:  # noqa: BLE001
            err = f"geen snapshot van de camera ({e})"
            continue
        if not data.startswith(b"\xff\xd8"):  # geen JPEG, bv. een inlogpagina
            err = "camera gaf geen JPEG-beeld terug (inloggegevens?)"
            continue
        _store_thumb(cam, n, data)
        return ""
    return err


def capture_thumb(cam, n):
    """Zoals snapshot_thumb, maar geeft True/False."""
    return not snapshot_thumb(cam, n)


def _store_thumb(cam, n, data):
    os.makedirs(THUMB_DIR, exist_ok=True)
    tmp = thumb_path(cam, n) + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, thumb_path(cam, n))
    broadcast({"type": "thumb", "cam": cam["id"], "n": int(n)})


def do_action(cam, action, args, info=None):
    """Voer een commando uit en houd alle schermen (en thumbnails) bij.
    Bij preset_set komt in info["thumb_error"] waarom de thumbnail niet lukte ("" = gelukt)."""
    if action in ("preset_call", "preset_set"):
        n = clamp(args.get("n"), 0, 254)
        if n in _backup.RESERVED:
            raise ValueError(f"Preset {n} is gereserveerd door PTZOptics (90 = home, 95 = OSD-menu, 91–99 vast). "
                             "Gebruik 1–89 of 100–254.")
    if action in STOPS_TRACKING and (cam["id"] in TRACKERS or cam["id"] in FOLLOW_TIMERS):
        stop_tracking(cam["id"], halt=False)
    if action in ("move", "stop", "home", "preset_call", "preset_set"):
        moving_cancel(cam["id"])          # een nieuw commando beëindigt de "onderweg"-markering
    old = undo_snapshot(cam, clamp(args.get("n"), 0, 254)) if action == "preset_set" else None
    try:
        res = run_command(cam, action, args)
    except (urllib.error.URLError, OSError) as e:
        camstatus_set(cam["id"], False, str(e))
        raise
    camstatus_set(cam["id"], True)
    if action == "preset_call":
        n = clamp(args.get("n"), 0, 254)
        broadcast({"type": "preset", "cam": cam["id"], "n": n})
        moving_watch(cam, n)
        if n in (cam.get("track_presets") or []) and not tracking_error():
            follow_after_preset(cam)
    elif action == "preset_set":
        n = clamp(args.get("n"), 0, 254)
        err = snapshot_thumb(cam, n)  # nieuwe positie vastgezet: meteen een thumbnail
        if info is not None:
            info["thumb_error"] = err
            info["undo"] = undo_register(cam, n, old)
        broadcast({"type": "preset", "cam": cam["id"], "n": n})
        remember_position(cam, n)
    return res


# ---------------------------------------------------------------- onderweg naar een preset
# 🧑 Na het oproepen van een preset krijgt de tegel "onderweg" tot de camera stilstaat (VISCA-positie
# twee keer gelijk). Lukt uitlezen niet, dan een vaste tijd. Elk nieuw commando op die camera stopt het.
MOVING = {}            # cam_id -> token
MOVING_FALLBACK = 3.0  # s, als de positie niet uit te lezen is


def moving_cancel(cam_id):
    if MOVING.pop(cam_id, None) is not None:
        broadcast({"type": "moving", "cam": cam_id, "moving": False})


def moving_watch(cam, n):
    if JOB.get("running"):
        return                                  # back-up/test gebruikt VISCA al; niet storen
    token = object()
    cid = cam["id"]
    MOVING[cid] = token
    broadcast({"type": "moving", "cam": cid, "n": n, "moving": True})

    def run():
        exact = False
        start = time.time()
        try:
            with _backup.visca_session(cam) as sock:
                last = None
                while MOVING.get(cid) is token and time.time() - start < 15:
                    time.sleep(0.35)
                    try:
                        p = _backup.read_position(sock)
                    except _backup.ViscaError:
                        continue
                    key = (p["pan"], p["tilt"], p["zoom"], p.get("focus"))
                    if key == last and time.time() - start >= 0.8:
                        exact = True
                        break
                    last = key
        except Exception:  # noqa: BLE001  (geen VISCA: vaste tijd)
            while MOVING.get(cid) is token and time.time() - start < MOVING_FALLBACK:
                time.sleep(0.1)
        if MOVING.get(cid) is token:
            MOVING.pop(cid, None)
            broadcast({"type": "moving", "cam": cid, "n": n, "moving": False, "exact": exact})

    threading.Thread(target=run, daemon=True, name=f"onderweg-{cid}").start()


# ---------------------------------------------------------------- bereikbaarheid per camera
# 🧑 Groen/rood bolletje per camera. Elke 10 s een TCP-verbinding naar de camera (HTTP-poort of VISCA-poort),
# en direct bijwerken als een commando lukt of mislukt.
CAMSTATUS = {}
CAMSTATUS_LOCK = threading.Lock()
CAMSTATUS_INTERVAL = float(os.environ.get("PTZ_CAMSTATUS_INTERVAL", 10))


def camstatus_set(cam_id, ok, err=""):
    with CAMSTATUS_LOCK:
        old = CAMSTATUS.get(cam_id)
        CAMSTATUS[cam_id] = {"ok": bool(ok), "error": "" if ok else str(err)[:200], "checked": time.strftime("%H:%M:%S")}
        changed = not old or old["ok"] != bool(ok)
        snap = dict(CAMSTATUS)
    if changed:
        broadcast({"type": "camstatus", "cams": snap})


def _cam_port(cam):
    if cam.get("protocol") == "visca":
        return cam["host"].split(":")[0], int(cam.get("visca_port") or 5678)
    host, _, port = cam["host"].partition(":")
    return host, int(port or 80)


def camstatus_probe(cam):
    try:
        with socket.create_connection(_cam_port(cam), timeout=1.5):
            pass
        camstatus_set(cam["id"], True)
    except Exception as e:  # noqa: BLE001
        camstatus_set(cam["id"], False, f"niet bereikbaar ({e})")


def camstatus_loop():
    if os.environ.get("PTZ_CAMSTATUS", "").lower() in ("0", "uit", "off"):
        return                       # bv. screenshots met demodata (camera's bestaan niet)
    while True:
        try:
            cams = load_config().get("cameras", [])
            ids = {c["id"] for c in cams}
            with CAMSTATUS_LOCK:
                for k in list(CAMSTATUS):
                    if k not in ids:
                        CAMSTATUS.pop(k)
            ts = [threading.Thread(target=camstatus_probe, args=(c,), daemon=True) for c in cams if c.get("host")]
            for t in ts:
                t.start()
            for t in ts:
                t.join(3)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(CAMSTATUS_INTERVAL)


# ---------------------------------------------------------------- ongedaan maken na overschrijven
# 🧑 Bij preset_set onthouden we de oude positie (saved_positions) en foto. Binnen UNDO_TTL kan de app de camera
# terugzetten naar de oude positie en de preset opnieuw opslaan. Na elk opslaan wordt de nieuwe positie uitgelezen,
# zodat de volgende keer overschrijven ook ongedaan te maken is.
UNDO = {}
UNDO_TTL = 60


def _saved_pos(cam_id, n):
    for c in load_config().get("cameras", []):
        if c.get("id") == cam_id:
            return (c.get("saved_positions") or {}).get(str(n))
    return None


def undo_snapshot(cam, n):
    tp = thumb_path(cam, n)
    try:
        with open(tp, "rb") as f:
            thumb = f.read()
    except OSError:
        thumb = None
    return {"pos": _saved_pos(cam["id"], n), "thumb": thumb}


def undo_register(cam, n, old):
    if not old or not old.get("pos"):
        return False
    UNDO[(cam["id"], n)] = {"old": old, "t": time.time()}
    return True


def _write_saved_pos(cam_id, n, pos):
    with LOCK:
        cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
        for c in cfg.get("cameras", []):
            if c.get("id") == cam_id:
                sp = c.setdefault("saved_positions", {})
                if pos:
                    sp[str(n)] = pos
                else:
                    sp.pop(str(n), None)
        _write(cfg)


def remember_position(cam, n):
    """Positie van de zojuist opgeslagen preset uitlezen en bewaren (op de achtergrond)."""
    if JOB.get("running"):
        return

    def run():
        try:
            with _backup.visca_session(cam) as sock:
                pos = _backup.read_position(sock)
            pos["_captured"] = time.strftime("%Y-%m-%d %H:%M")
            _write_saved_pos(cam["id"], n, pos)
        except Exception:  # noqa: BLE001  (geen VISCA: dan geen ongedaan maken voor deze preset)
            pass

    threading.Thread(target=run, daemon=True, name="positie-onthouden").start()


def undo_preset(cam, n):
    e = UNDO.get((cam["id"], n))
    if not e or time.time() - e["t"] > UNDO_TTL:
        raise ValueError("Ongedaan maken kan niet meer (te lang geleden of oude positie onbekend).")
    if JOB.get("running"):
        raise ValueError("Er loopt een taak; probeer het daarna.")
    UNDO.pop((cam["id"], n), None)
    if cam["id"] in TRACKERS or cam["id"] in FOLLOW_TIMERS:
        stop_tracking(cam["id"], halt=True)
    moving_cancel(cam["id"])
    old = e["old"]
    with _backup.visca_session(cam) as sock:
        _backup.goto_position(sock, old["pos"])
        _backup.wait_still(sock)
    run_command(cam, "preset_set", {"n": n})
    if old["thumb"]:
        _store_thumb(cam, n, old["thumb"])
    else:
        try:
            os.remove(thumb_path(cam, n))
        except OSError:
            pass
        broadcast({"type": "thumb", "cam": cam["id"], "n": int(n)})
    _write_saved_pos(cam["id"], n, old["pos"])
    broadcast({"type": "preset", "cam": cam["id"], "n": n})


# ---------------------------------------------------------------- hulp bij instellen (wizard)
def camtest(c):
    """Test een (nog niet opgeslagen) camera: bereikbaar, VISCA-poort, snapshot."""
    cam = {"id": "test", "host": str(c.get("host") or "").strip(), "protocol": c.get("protocol") or "http",
           "visca_port": c.get("visca_port") or 5678, "user": c.get("user") or "", "password": c.get("password") or ""}
    if not cam["host"]:
        raise ValueError("Vul een IP-adres in")
    res = {"http": False, "visca": False, "snapshot": False, "error": ""}
    host, _, port = cam["host"].partition(":")
    for key, prt in (("http", int(port or 80)), ("visca", int(cam["visca_port"]))):
        try:
            with socket.create_connection((host, prt), timeout=1.5):
                res[key] = True
        except OSError as e:
            res["error"] = res["error"] or str(e)
    if res["http"]:
        try:
            data, _ = http_get(cam, "/snapshot.jpg", binary=True)
            res["snapshot"] = data.startswith(b"\xff\xd8")
            if not res["snapshot"]:
                res["error"] = "camera gaf geen beeld (inloggegevens?)"
        except Exception as e:  # noqa: BLE001
            res["error"] = f"geen beeld: {e}"
    return res


def scan_network(port=5678, timeout=0.4):
    """⚠️ Zoek camera's in het eigen /24-netwerk: apparaten met de VISCA-poort (PTZOptics: 5678) open."""
    ip = local_ip()
    if ip.startswith("127."):
        return {"network": "", "found": []}
    base = ip.rsplit(".", 1)[0]
    found, lock = [], threading.Lock()

    def probe(i):
        h = f"{base}.{i}"
        if h == ip:
            return
        try:
            with socket.create_connection((h, port), timeout=timeout):
                with lock:
                    found.append(h)
        except OSError:
            pass

    ts = [threading.Thread(target=probe, args=(i,), daemon=True) for i in range(1, 255)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout + 1)
    return {"network": base + ".0/24", "found": sorted(found, key=lambda h: int(h.rsplit(".", 1)[1]))}


def first_run(cfg):
    """Nog niet ingesteld: geen camera's, of alleen de voorbeeldcamera uit DEFAULT_CONFIG."""
    cams = cfg.get("cameras") or []
    if not cams:
        return True
    d = DEFAULT_CONFIG["cameras"][0]
    return len(cams) == 1 and cams[0].get("host") == d["host"] and cams[0].get("name") == d["name"] \
        and not cams[0].get("presets")




# ---------------------------------------------------------------- volgen (alleen pan)
try:
    import tracker as _tracker
except Exception as _e:  # noqa: BLE001
    _tracker = None
TRACKERS = {}
TRACK_STATUS = {}
TRACK_LOCK = threading.Lock()
_DETECTOR = None
FOLLOW_TIMERS = {}
STOPS_TRACKING = {"move", "stop", "home", "preset_call", "preset_set"}


def tracking_error():
    if _tracker is None:
        return "tracker.py niet gevonden"
    return "" if _tracker.available() else _tracker.CV_ERROR


def stream_url(cam):
    return cam.get("stream_url") or f"rtsp://{cam['host'].split(':')[0]}:554/2"


def _track_status(cam_id, **kw):
    st = TRACK_STATUS.setdefault(cam_id, {"type": "track", "cam": cam_id, "on": False, "status": ""})
    st.update(kw)
    broadcast(dict(st))


def start_tracking(cam):
    global _DETECTOR
    err = tracking_error()
    if err:
        raise RuntimeError(err)
    stop_tracking(cam["id"], halt=True, quiet=True)
    with TRACK_LOCK:
        if _DETECTOR is None:
            _DETECTOR = _tracker.Detector()
        settings = {"max_speed": clamp(cam.get("track_speed") or 6, 1, 24),
                    "invert": bool(cam.get("track_invert")),
                    "deadzone": 0.10}
        t = _tracker.CameraTracker(
            cam, stream_url(cam), _DETECTOR,
            send=lambda a, args, c=cam: run_command(c, a, args),
            on_status=lambda st, cid=cam["id"]: _track_status(cid, **st),
            settings=settings)
        TRACKERS[cam["id"]] = t
        t.start()
    _track_status(cam["id"], on=True, status="starten…", detector=_DETECTOR.kind)


def stop_tracking(cam_id, halt=True, quiet=False):
    timer = FOLLOW_TIMERS.pop(cam_id, None)
    if timer:
        timer.cancel()
    with TRACK_LOCK:
        t = TRACKERS.pop(cam_id, None)
    if t:
        t.stop(halt=halt)
        t.join(timeout=1.0)  # wacht tot de lopende detectie klaar is, zodat er geen pan meer achteraan komt
    if not quiet and (t or TRACK_STATUS.get(cam_id, {}).get("on")):
        _track_status(cam_id, on=False, status="uit")


def follow_after_preset(cam, delay=2.5):
    """Na een preset met 'volgen' eerst de camera laten aankomen, dan pas volgen."""
    def go():
        FOLLOW_TIMERS.pop(cam["id"], None)
        try:
            start_tracking(cam)
        except Exception as e:  # noqa: BLE001
            _track_status(cam["id"], on=False, status=str(e))
    t = threading.Timer(delay, go)
    t.daemon = True
    FOLLOW_TIMERS[cam["id"]] = t
    _track_status(cam["id"], on=True, status="wacht op preset…")
    t.start()


# ---------------------------------------------------------------- back-up en herstel

def ws_public():
    return {"active": WS["active"], "list": WS["list"]}


def _ws_slug(name, taken):
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "workspace"
    slug, i = base, 2
    while slug in taken:
        slug, i = f"{base}-{i}", i + 1
    return slug


def ws_select(ws_id):
    """Andere workspace actief maken: alles wat met de vorige bezig is stoppen, paden omzetten."""
    global CONFIG_PATH, THUMB_DIR
    if not any(w["id"] == ws_id for w in WS["list"]):
        raise ValueError("Onbekende workspace")
    if ws_id == WS["active"]:
        return
    if JOB.get("running"):
        raise ValueError("Er loopt nog een taak (uitlezen/terugzetten/laden). Wacht tot die klaar is.")
    for cam_id in list(TRACKERS) + list(FOLLOW_TIMERS):
        try:
            stop_tracking(cam_id, halt=True, quiet=True)
        except Exception:  # noqa: BLE001
            pass
    with LOCK:
        WS["active"] = ws_id
        CONFIG_PATH, THUMB_DIR = ws_paths(ws_id)
        ws_save(WS)
    STATE.update(active=None, last_preset={})
    JOB.update(running=False, kind="", cams={})
    with YT._lock:   # livestream van de vorige workspace niet laten hangen
        YT.state.update(channel="", live=False, upcoming=False, video_id=None, title=None, start=None, checked=None, error=None)
    load_config()          # maakt cameras.json aan als de workspace nog leeg is
    broadcast({"type": "workspace", "active": ws_id})
    YT.refresh()
    MIDI.refresh()
    with CAMSTATUS_LOCK:          # andere camera's: status, onderweg en ongedaan maken opnieuw
        CAMSTATUS.clear()
    MOVING.clear()
    UNDO.clear()


def ws_action(body):
    act = body.get("action")
    taken = {w["id"] for w in WS["list"]}
    if act == "select":
        ws_select(str(body.get("id", "")))
    elif act == "create":
        name = str(body.get("name") or "").strip()[:60]
        if not name:
            raise ValueError("Geef de workspace een naam")
        if any(w["name"].lower() == name.lower() for w in WS["list"]):
            raise ValueError("Er is al een workspace met die naam")
        new_id = _ws_slug(name, taken)
        cfg_path, thumb_dir = ws_paths(new_id)
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        src = body.get("copy_from")
        if src:
            if src not in taken:
                raise ValueError("Onbekende workspace om te kopiëren")
            s_cfg, s_thumbs = ws_paths(src)
            if os.path.exists(s_cfg):
                shutil.copyfile(s_cfg, cfg_path)
            if os.path.isdir(s_thumbs):
                shutil.copytree(s_thumbs, thumb_dir, dirs_exist_ok=True)
        else:
            empty = json.loads(json.dumps(DEFAULT_CONFIG))
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(empty, f, indent=2, ensure_ascii=False)
        WS["list"].append({"id": new_id, "name": name})
        ws_save(WS)
        broadcast({"type": "workspaces"})
        if body.get("select"):
            ws_select(new_id)
        return {**ws_public(), "created": new_id}
    elif act == "rename":
        w = next((w for w in WS["list"] if w["id"] == body.get("id")), None)
        name = str(body.get("name") or "").strip()[:60]
        if not w or not name:
            raise ValueError("Onbekende workspace of lege naam")
        w["name"] = name
        ws_save(WS)
        broadcast({"type": "workspaces"})
    elif act == "delete":
        ws_id = body.get("id")
        if ws_id == WS_DEFAULT:
            raise ValueError("De standaard-workspace kan niet weg")
        if ws_id == WS["active"]:
            raise ValueError("Kies eerst een andere workspace")
        if ws_id not in taken:
            raise ValueError("Onbekende workspace")
        WS["list"] = [w for w in WS["list"] if w["id"] != ws_id]
        ws_save(WS)
        shutil.rmtree(os.path.join(WS_DIR, ws_id), ignore_errors=True)
        broadcast({"type": "workspaces"})
    else:
        raise ValueError("Onbekende actie")
    return ws_public()


JOB = {"type": "job", "running": False, "kind": "", "cams": {}}
JOB_LOCK = threading.Lock()


def _safe_id(cam_id):
    return "".join(ch for ch in cam_id if ch.isalnum() or ch in "-_")


def list_thumbs(cam, tdir=None):
    prefix = _safe_id(cam["id"]) + "_"
    try:
        files = os.listdir(tdir or THUMB_DIR)
    except OSError:
        return []
    return sorted(int(f[len(prefix):-4]) for f in files
                  if f.startswith(prefix) and f.endswith(".jpg") and f[len(prefix):-4].isdigit())


def now_stamp():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _ws_name(ws_id):
    return next((w["name"] for w in WS["list"] if w["id"] == ws_id), ws_id)


def make_backup(with_passwords=True, ws_id=None):
    """Back-up van één workspace (standaard de actieve)."""
    ws_id = ws_id or WS["active"]
    if ws_id == WS["active"]:
        cfg, tdir = load_config(), THUMB_DIR
    else:
        cfg_path, tdir = ws_paths(ws_id)
        try:
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if not with_passwords:
        for cam in cfg.get("cameras", []):
            cam.pop("password", None)
    thumbs = {}
    for cam in cfg.get("cameras", []):
        for n in list_thumbs(cam, tdir):
            with open(thumb_path(cam, n, tdir), "rb") as f:
                thumbs[f"{cam['id']}/{n}"] = base64.b64encode(f.read()).decode()
    return {"type": "ptz-control-backup", "version": 1, "created": now_stamp(),
            "workspace": {"id": ws_id, "name": _ws_name(ws_id)},
            "with_passwords": with_passwords, "config": cfg, "thumbs": thumbs}


def make_backup_all(with_passwords=True):
    """Alle workspaces in één bestand."""
    return {"type": "ptz-control-backup-all", "version": 1, "created": now_stamp(),
            "with_passwords": with_passwords, "active": WS["active"],
            "workspaces": [make_backup(with_passwords, w["id"]) for w in WS["list"]]}


# ---------------------------------------------------------------- automatische back-up
# 🧑 Elke dag en altijd vóór een update een back-up van alle workspaces (met wachtwoorden, want lokaal) in
# DATA_DIR/backups/; de laatste AUTOBK_KEEP blijven bewaard.
AUTOBK_DIR = os.path.join(DATA_DIR, "backups")
AUTOBK_KEEP = 10
AUTOBK_EVERY = float(os.environ.get("PTZ_AUTOBK_EVERY", 24 * 3600))
AUTOBK_LOCK = threading.Lock()


def autobackup_list():
    try:
        names = sorted((n for n in os.listdir(AUTOBK_DIR) if n.startswith("auto-") and n.endswith(".json")), reverse=True)
    except OSError:
        return []
    out = []
    for n in names:
        m = re.match(r"auto-(\d{8})-(\d{6})-(.+)\.json$", n)
        try:
            size = os.path.getsize(os.path.join(AUTOBK_DIR, n))
        except OSError:
            continue
        created = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]} {m.group(2)[:2]}:{m.group(2)[2:4]}" if m else ""
        out.append({"name": n, "created": created, "reason": (m.group(3) if m else "").replace("-", " "), "size": size})
    return out


def autobackup(reason="handmatig"):
    with AUTOBK_LOCK:
        os.makedirs(AUTOBK_DIR, exist_ok=True)
        data = make_backup_all(True)
        data["auto_reason"] = reason
        slug = re.sub(r"[^a-z0-9]+", "-", reason.lower()).strip("-") or "auto"
        name = f"auto-{datetime.datetime.now():%Y%m%d-%H%M%S}-{slug}.json"
        tmp = os.path.join(AUTOBK_DIR, name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(AUTOBK_DIR, name))
        for old in autobackup_list()[AUTOBK_KEEP:]:
            try:
                os.remove(os.path.join(AUTOBK_DIR, old["name"]))
            except OSError:
                pass
    broadcast({"type": "autobackup"})
    return name


def autobackup_file(name):
    if not re.fullmatch(r"auto-[0-9]{8}-[0-9]{6}-[a-z0-9-]+\.json", name or ""):
        raise KeyError("onbekende back-up")
    path = os.path.join(AUTOBK_DIR, name)
    if not os.path.isfile(path):
        raise KeyError("onbekende back-up")
    return path


def autobackup_loop():
    time.sleep(float(os.environ.get("PTZ_AUTOBK_FIRST", 60)))
    while True:
        try:
            last = autobackup_list()
            age = time.time() - os.path.getmtime(os.path.join(AUTOBK_DIR, last[0]["name"])) if last else None
            if age is None or age >= AUTOBK_EVERY:
                autobackup("dagelijks")
        except Exception as e:  # noqa: BLE001
            print(f"Automatische back-up mislukt: {e}")
        time.sleep(min(3600, AUTOBK_EVERY))


# ---------------------------------------------------------------- pincode / alleen kijken
# 🧑 Met een pincode kan iedereen op het netwerk meekijken, maar alleen wie de pincode heeft ingevoerd kan bedienen.
# Deze computer zelf (localhost en het eigen LAN-adres) heeft nooit een pincode nodig, zodat je jezelf niet buitensluit.
ACCESS_FILE = os.path.join(DATA_DIR, "toegang.json")
ACCESS_LOCK = threading.Lock()
TOKEN_DAYS = 180
FAILS = {}            # ip -> [tijdstippen van foute pogingen]
OPEN_POSTS = {"/api/access/unlock", "/api/access/lock"}


def _access():
    try:
        with open(ACCESS_FILE, encoding="utf-8") as f:
            a = json.load(f)
    except (OSError, ValueError):
        a = {}
    a.setdefault("tokens", {})
    return a


def _access_save(a):
    tmp = ACCESS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(a, f)
    os.replace(tmp, ACCESS_FILE)
    try:
        os.chmod(ACCESS_FILE, 0o600)
    except OSError:
        pass


def _pin_hash(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 120_000).hex()


def pin_set():
    return bool(_access().get("pin_hash"))


def is_local(ip):
    if os.environ.get("PTZ_TEST_NOLOCAL"):     # tests: doen alsof elk verzoek van een ander apparaat komt
        return False
    return ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1") or ip == local_ip()


def token_ok(token):
    if not token:
        return False
    with ACCESS_LOCK:
        a = _access()
        exp = a["tokens"].get(token)
        return bool(exp and exp > time.time())


def new_token():
    tok = secrets.token_urlsafe(24)
    with ACCESS_LOCK:
        a = _access()
        now = time.time()
        a["tokens"] = {t: e for t, e in a["tokens"].items() if e > now}
        a["tokens"][tok] = now + TOKEN_DAYS * 86400
        _access_save(a)
    return tok


def check_pin(ip, pin):
    now = time.time()
    FAILS[ip] = [t for t in FAILS.get(ip, []) if now - t < 60]
    if len(FAILS[ip]) >= 5:
        raise PermissionError("Te veel foute pogingen. Wacht een minuut.")
    a = _access()
    if not a.get("pin_hash"):
        return True
    ok = hmac.compare_digest(_pin_hash(str(pin), a["salt"]), a["pin_hash"])
    if not ok:
        FAILS[ip].append(now)
    return ok


def set_pin(pin):
    pin = str(pin or "").strip()
    with ACCESS_LOCK:
        a = _access()
        if not pin:
            a.pop("pin_hash", None)
            a.pop("salt", None)
        else:
            if not re.fullmatch(r"[0-9]{4,8}", pin):
                raise ValueError("Pincode: 4 tot 8 cijfers")
            a["salt"] = secrets.token_hex(16)
            a["pin_hash"] = _pin_hash(pin, a["salt"])
        a["tokens"] = {}           # nieuwe of geen pincode: alle apparaten opnieuw laten inloggen
        _access_save(a)


def restore_backup(data, ws_id=None):
    """Back-up van één workspace terugzetten in ws_id (standaard de actieve)."""
    if not isinstance(data, dict) or data.get("type") != "ptz-control-backup":
        raise ValueError("Dit is geen PTZ Control-back-upbestand")
    new = data.get("config") or {}
    if not isinstance(new.get("cameras"), list):
        raise ValueError("Back-up bevat geen camera's")
    ws_id = ws_id or WS["active"]
    active = ws_id == WS["active"]
    cfg_path, tdir = ws_paths(ws_id)
    try:
        with open(cfg_path, encoding="utf-8") as f:
            current = {c["id"]: c for c in json.load(f).get("cameras", [])}
    except (OSError, ValueError):
        current = {}
    for cam in new["cameras"]:
        cam.pop("thumbs", None)
        old = current.get(cam.get("id"), {})
        if "password" not in cam and old.get("password"):   # back-up zonder wachtwoorden: huidige houden
            cam["password"] = old["password"]
    os.makedirs(tdir, exist_ok=True)
    with LOCK:
        tmp = cfg_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(new, f, indent=2, ensure_ascii=False)
        os.replace(tmp, cfg_path)
    count = 0
    cams = {c["id"]: c for c in new["cameras"]}
    for key, b64 in (data.get("thumbs") or {}).items():
        cam_id, _, n = key.rpartition("/")
        if cam_id in cams and n.isdigit():
            with open(thumb_path(cams[cam_id], int(n), tdir), "wb") as f:
                f.write(base64.b64decode(b64))
            count += 1
    if active:
        broadcast({"type": "config"})
        YT.refresh()
    return {"cameras": len(new["cameras"]), "thumbs": count, "workspace": ws_id}


def restore_any(body):
    """Terugzetten: één workspace (in de huidige of als nieuwe) of een back-up van alle workspaces."""
    if body.get("type") == "ptz-control-backup-all":
        done = []
        for item in body.get("workspaces") or []:
            src = item.get("workspace") or {}
            wid, name = str(src.get("id") or ""), str(src.get("name") or "").strip() or "Workspace"
            if not any(w["id"] == wid for w in WS["list"]):
                taken = {w["id"] for w in WS["list"]}
                wid = wid if re.fullmatch(r"[a-z0-9-]{1,40}", wid) and wid not in taken else _ws_slug(name, taken)
                WS["list"].append({"id": wid, "name": name})
                ws_save(WS)
            r = restore_backup(item, wid)
            done.append({"id": wid, "name": _ws_name(wid), "cameras": r["cameras"], "thumbs": r["thumbs"]})
        broadcast({"type": "workspaces"})
        return {"workspaces": done}
    target = body.pop("_target", "current")
    if target == "new":
        name = str(body.pop("_name", "") or (body.get("workspace") or {}).get("name") or "Teruggezet").strip()[:60]
        if any(w["name"].lower() == name.lower() for w in WS["list"]):
            name = f"{name} ({datetime.date.today().isoformat()})"
        created = ws_action({"action": "create", "name": name})["created"]
        r = restore_backup(body, created)
        return {**r, "created": created, "name": name}
    return restore_backup(body)


def _job_update(cam_id, **kw):
    with JOB_LOCK:
        JOB["cams"].setdefault(cam_id, {}).update(kw)
        snapshot = json.loads(json.dumps(JOB))
    broadcast(snapshot)


def start_job(kind, cam_ids, opts):
    """kind = 'capture' (uit camera lezen), 'apply' (terugzetten), 'load' (presets laden) of 'captest' (presetaantal testen)."""
    with JOB_LOCK:
        if JOB["running"]:
            raise RuntimeError("Er loopt al een back-uptaak")
        cfg = load_config()
        cams = [c for c in cfg.get("cameras", []) if c["id"] in cam_ids]
        if not cams:
            raise ValueError("Geen camera's gekozen")
        JOB.update(running=True, kind=kind, cams={c["id"]: {"status": "wachten", "msg": "", "errors": []} for c in cams})
    preset_count = int(cfg.get("preset_count") or 12)
    stamp = now_stamp()
    results = {}

    def work(cam):
        cid = cam["id"]
        stop_tracking(cid, halt=True)
        try:
            prog = lambda m: _job_update(cid, status="bezig", msg=m)  # noqa: E731
            if kind == "captest":
                r = _backup.capacity_test(cam, prog)
                r["_tested"] = stamp
                results[cid] = r
                errs = [f"preset {t['n']}: {t['note']}" for t in r["tested"]
                        if "LET OP" in t["note"] or (not t["ok"] and t["n"] < r["max"])]
                left = [t["n"] for t in r["tested"] if t.get("left_test")]
                msg = (f"hoogste werkende preset: {r['max']} ({r['total']} bruikbaar)" if r["max"] else "geen enkele preset werkte") + \
                      (f" · testpositie staat nu in: {', '.join(map(str, left))}" if left else "")
                back = STATE["last_preset"].get(cid)
                if back:
                    try:
                        run_command(cam, "preset_call", {"n": back})
                    except Exception:  # noqa: BLE001
                        pass
                _job_update(cid, capacity=r)
            elif kind == "load":
                upto = clamp(opts.get("upto") or preset_count, 1, 254)
                presets = [n for n in range(1, upto + 1) if _backup.usable(n)]
                photos = bool(opts.get("thumbs"))
                shot = (lambda n, _pos, c=cam: capture_thumb(c, n)) if photos else None
                pos, _img, errs = _backup.capture_camera(cam, presets, True, False, prog, stamp, on_preset=shot)
                found = sorted(int(n) for n, p in pos.items() if p.get("moved"))
                empty = sorted(int(n) for n, p in pos.items() if not p.get("moved"))
                for n in empty:  # foto van een lege preset weggooien
                    try:
                        os.remove(thumb_path(cam, n))
                        broadcast({"type": "thumb", "cam": cid, "n": n})
                    except OSError:
                        pass
                results[cid] = ({str(n): pos[str(n)] for n in found}, None)
                back = STATE["last_preset"].get(cid)
                if back:
                    try:
                        run_command(cam, "preset_call", {"n": back})
                    except Exception:  # noqa: BLE001
                        pass
                msg = (f"{len(found)} presets gevonden: {', '.join(map(str, found))}" if found else "geen presets gevonden") + \
                      (f" · leeg: {', '.join(map(str, empty))}" if empty else "")
                _job_update(cid, found=found, empty=empty)
            elif kind == "capture":
                cam["thumbs"] = list_thumbs(cam)
                presets = _backup.preset_list(cam, preset_count, opts.get("presets", "used"))
                pos, img, errs = _backup.capture_camera(cam, presets, opts.get("positions"), opts.get("image"), prog, stamp)
                results[cid] = (pos, img)
                # camera terug naar de preset die LIVE stond
                back = STATE["last_preset"].get(cid)
                if back and opts.get("positions"):
                    try:
                        run_command(cam, "preset_call", {"n": back})
                    except Exception:  # noqa: BLE001
                        pass
                summary = []
                if opts.get("positions"):
                    summary.append(f"{len(pos)} presetposities")
                if img:
                    summary.append("beeldinstellingen")
                msg = "gelezen: " + ", ".join(summary) if summary else "niets gelezen"
            else:
                errs = _backup.restore_camera(cam, opts.get("positions"), opts.get("image"), prog)
                n = len(cam.get("saved_positions") or {}) if opts.get("positions") else 0
                msg = "teruggezet" + (f": {n} presets" if n else "") + (" + beeld" if opts.get("image") and cam.get("saved_image") else "")
            _job_update(cid, status="klaar" if not errs else "klaar met meldingen", msg=msg, errors=errs)
        except OSError as e:
            _job_update(cid, status="fout", msg=f"Camera niet bereikbaar via VISCA (poort {cam.get('visca_port') or 5678}): {e}")
        except Exception as e:  # noqa: BLE001
            _job_update(cid, status="fout", msg=str(e))

    def run_all():
        threads = [threading.Thread(target=work, args=(c,), daemon=True) for c in cams]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if kind == "captest" and results:
            with LOCK:
                fresh = json.load(open(CONFIG_PATH, encoding="utf-8"))
                for cam in fresh.get("cameras", []):
                    if cam["id"] in results:
                        cam["preset_capacity"] = results[cam["id"]]
                _write(fresh)
            broadcast({"type": "config"})
        if kind in ("capture", "load") and results:
            with LOCK:
                fresh = json.load(open(CONFIG_PATH, encoding="utf-8"))
                top = 0
                for cam in fresh.get("cameras", []):
                    if cam["id"] in results:
                        pos, img = results[cam["id"]]
                        if pos:
                            cam.setdefault("saved_positions", {}).update(pos)
                            top = max([top] + [int(n) for n in pos])
                        if img:
                            cam["saved_image"] = img
                need = _backup.count_upto(top) if top else 0
                if kind == "load" and need > int(fresh.get("preset_count") or 12):
                    fresh["preset_count"] = min(244, need)   # genoeg tegels om de gevonden presets te tonen
                _write(fresh)
            broadcast({"type": "config"})
        with JOB_LOCK:
            JOB["running"] = False
            snapshot = json.loads(json.dumps(JOB))
        broadcast(snapshot)

    threading.Thread(target=run_all, daemon=True).start()


# ---------------------------------------------------------------- web server
class Handler(BaseHTTPRequestHandler):
    server_version = "PTZControl/1.0"

    def log_message(self, fmt, *args):  # rustigere console
        if "/api/snapshot" not in (str(args[0]) if args else ""):
            super().log_message(fmt, *args)

    def _send(self, code, body, ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "ptz_auth":
                return v
        return ""

    def _authorized(self):
        """Mag dit apparaat bedienen? Zonder pincode altijd; anders deze computer zelf of een geldige sessie."""
        return not pin_set() or is_local(self.client_address[0]) or token_ok(self._token())

    def _deny(self):
        return self._send(403, {"ok": False, "need_pin": True,
                                "error": "Alleen kijken: voer de pincode in om te bedienen."})

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            with open(INDEX_PATH, "rb") as f:
                html = f.read()
            # workspace meegeven vóór er iets geladen wordt (per-apparaat-instellingen zijn per workspace)
            meta = f'<meta name="ptz-workspace" content="{WS["active"]}">'.encode()
            return self._send(200, html.replace(b"<head>", b"<head>" + meta, 1), "text/html; charset=utf-8")
        if u.path == "/api/workspaces":
            return self._send(200, ws_public())
        if u.path == "/api/config":
            cfg = load_config()
            for cam in cfg.get("cameras", []):
                cam["thumbs"] = list_thumbs(cam)
            cfg["tracking_error"] = tracking_error()
            cfg["first_run"] = first_run(cfg)
            return self._send(200, cfg)
        if u.path == "/api/access":
            return self._send(200, {"pin_set": pin_set(), "unlocked": self._authorized(), "local": is_local(self.client_address[0])})
        if u.path == "/api/autobackup":
            return self._send(200, {"list": autobackup_list(), "keep": AUTOBK_KEEP})
        if u.path == "/api/autobackup/file":
            if not self._authorized():
                return self._deny()
            try:
                path = autobackup_file(parse_qs(u.query).get("name", [""])[0])
            except KeyError as e:
                return self._send(404, {"error": e.args[0]})
            with open(path, "rb") as f:
                body = f.read()
            dl = parse_qs(u.query).get("download", ["0"])[0] == "1"
            return self._send(200, body, "application/json; charset=utf-8",
                              {"Content-Disposition": f'attachment; filename="ptz-control-{os.path.basename(path)}"'} if dl else None)
        if u.path == "/api/backup":
            if not self._authorized():
                return self._deny()
            q = parse_qs(u.query)
            pw = q.get("passwords", ["1"])[0] == "1"
            if q.get("all", ["0"])[0] == "1":
                data, label = make_backup_all(pw), "alle-workspaces"
            else:
                data = make_backup(pw)
                label = re.sub(r"[^a-z0-9]+", "-", _ws_name(WS["active"]).lower()).strip("-") or "workspace"
            body = json.dumps(data, ensure_ascii=False, indent=1).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition",
                             f'attachment; filename="ptz-control-backup-{label}-{datetime.date.today().isoformat()}.json"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return None
        if u.path == "/api/job":
            return self._send(200, JOB)
        if u.path == "/api/state":
            return self._send(200, STATE)
        if u.path == "/api/youtube":
            return self._send(200, YT.snapshot())
        if u.path == "/api/update":
            return self._send(200, UPD.snapshot())
        if u.path == "/api/camstatus":
            with CAMSTATUS_LOCK:
                return self._send(200, {"type": "camstatus", "cams": dict(CAMSTATUS)})
        if u.path == "/api/midi":
            return self._send(200, {**MIDI.snapshot(), "config": _midi.norm_cfg(_midi_cfg())})
        if u.path == "/api/midi/log":
            seq = int(parse_qs(u.query).get("since", ["0"])[0] or 0)
            return self._send(200, MIDI.log_since(seq))
        if u.path == "/api/midi/log.txt":
            body = MIDI.log_text().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition",
                             f'attachment; filename="v1hd-verkenning-{datetime.datetime.now():%Y%m%d-%H%M}.txt"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return None
        if u.path == "/api/events":
            return self._sse()
        if u.path == "/api/thumb":
            q = parse_qs(u.query)
            try:
                path = thumb_path(find_camera(q.get("cam", [""])[0]), clamp(q.get("n", ["0"])[0], 0, 254))
                with open(path, "rb") as f:
                    return self._send(200, f.read(), "image/jpeg")
            except (KeyError, OSError):
                return self._send(404, {"error": "geen thumbnail"})
        if u.path == "/api/snapshot":
            cam_id = parse_qs(u.query).get("cam", [""])[0]
            try:
                data, ctype = http_get(find_camera(cam_id), "/snapshot.jpg", binary=True)
                return self._send(200, data, ctype)
            except Exception as e:  # noqa: BLE001
                return self._send(502, {"error": str(e)})
        self._send(404, {"error": "niet gevonden"})

    # --- Server-Sent Events: browsers houden de camerakeuze onderling gelijk
    def _sse(self):
        q = queue.Queue()
        with SUB_LOCK:
            SUBSCRIBERS.append(q)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(f"data: {json.dumps({'type': 'state', **STATE})}\n\n".encode())
            if JOB["cams"]:
                self.wfile.write(f"data: {json.dumps(JOB)}\n\n".encode())
            for st in list(TRACK_STATUS.values()):
                self.wfile.write(f"data: {json.dumps(st)}\n\n".encode())
            self.wfile.write(f"data: {json.dumps(YT.snapshot())}\n\n".encode())
            self.wfile.write(f"data: {json.dumps(UPD.snapshot())}\n\n".encode())
            self.wfile.write(f"data: {json.dumps(MIDI.snapshot())}\n\n".encode())
            with CAMSTATUS_LOCK:
                self.wfile.write(f"data: {json.dumps({'type': 'camstatus', 'cams': dict(CAMSTATUS)})}\n\n".encode())
            for cid in list(MOVING):
                self.wfile.write(f"data: {json.dumps({'type': 'moving', 'cam': cid, 'moving': True})}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with SUB_LOCK:
                SUBSCRIBERS.remove(q)


    def do_HEAD(self):
        """Alleen bestaan-check van thumbnails (gebruikt door de interface)."""
        u = urlparse(self.path)
        code = 404
        if u.path == "/api/thumb":
            q = parse_qs(u.query)
            try:
                path = thumb_path(find_camera(q.get("cam", [""])[0]), clamp(q.get("n", ["0"])[0], 0, 254))
                code = 200 if os.path.exists(path) else 404
            except KeyError:
                code = 404
        elif u.path in ("/", "/index.html"):
            code = 200
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_POST(self):
        u = urlparse(self.path)
        try:
            body = self._body()
        except json.JSONDecodeError:
            return self._send(400, {"error": "ongeldige JSON"})
        if u.path not in OPEN_POSTS and not self._authorized():
            return self._deny()

        ip = self.client_address[0]
        if u.path == "/api/access/unlock":
            try:
                if not check_pin(ip, body.get("pin", "")):
                    return self._send(403, {"ok": False, "error": "Pincode klopt niet"})
            except PermissionError as e:
                return self._send(429, {"ok": False, "error": str(e)})
            tok = new_token()
            return self._send(200, {"ok": True}, headers={
                "Set-Cookie": f"ptz_auth={tok}; Path=/; Max-Age={TOKEN_DAYS * 86400}; HttpOnly; SameSite=Strict"})
        if u.path == "/api/access/lock":
            tok = self._token()
            with ACCESS_LOCK:
                a = _access()
                if a["tokens"].pop(tok, None) is not None:
                    _access_save(a)
            return self._send(200, {"ok": True}, headers={"Set-Cookie": "ptz_auth=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"})
        if u.path == "/api/access/set":
            try:
                set_pin(body.get("pin", ""))
            except ValueError as e:
                return self._send(400, {"ok": False, "error": str(e)})
            broadcast({"type": "access"})
            hdr = None
            if body.get("pin") and not is_local(ip):    # dit apparaat blijft ingelogd
                hdr = {"Set-Cookie": f"ptz_auth={new_token()}; Path=/; Max-Age={TOKEN_DAYS * 86400}; HttpOnly; SameSite=Strict"}
            return self._send(200, {"ok": True, "pin_set": pin_set()}, headers=hdr)
        if u.path == "/api/access/logout_all":
            with ACCESS_LOCK:
                a = _access()
                a["tokens"] = {}
                _access_save(a)
            broadcast({"type": "access"})
            return self._send(200, {"ok": True})
        if u.path == "/api/autobackup/now":
            try:
                return self._send(200, {"ok": True, "name": autobackup("handmatig")})
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"ok": False, "error": str(e)})

        if u.path == "/api/config":
            if not isinstance(body.get("cameras"), list):
                return self._send(400, {"error": "cameras ontbreekt"})
            body.pop("tracking_error", None)
            body.pop("first_run", None)
            body.pop("obs", None)  # OBS-koppeling (stream starten/stoppen) is verwijderd
            body.pop("scenes", None)  # scènes zijn verwijderd
            old_cfg = load_config()
            old_yt = (old_cfg.get("youtube") or {}).get("channel", "")
            yt = body.get("youtube") if isinstance(body.get("youtube"), dict) else old_cfg.get("youtube")
            if yt:
                chan = str(yt.get("channel") or "").strip()[:200]
                if chan:
                    try:
                        _youtube.channel_path(chan)
                    except ValueError as e:
                        return self._send(400, {"error": str(e)})
                body["youtube"] = {"channel": chan}
            else:
                body.pop("youtube", None)
            # videomixer-instellingen alleen via /api/midi/config (een verouderd scherm mag ze niet wissen)
            body.pop("midi", None)
            if old_cfg.get("midi"):
                body["midi"] = old_cfg["midi"]
            existing = {c["id"]: c for c in old_cfg.get("cameras", [])}
            for cam in body["cameras"]:
                cam.pop("thumbs", None)
                # uitgelezen posities/beeld alleen via de back-uptaken wijzigen, nooit via een (mogelijk verouderd) scherm
                for key in ("saved_positions", "saved_image", "preset_capacity"):
                    cam.pop(key, None)
                    if key in existing.get(cam.get("id"), {}):
                        cam[key] = existing[cam["id"]][key]
            save_config(body)
            broadcast({"type": "config"})
            if (body.get("youtube") or {}).get("channel", "") != old_yt:
                YT.refresh()
            return self._send(200, {"ok": True})

        if u.path == "/api/workspaces":
            try:
                return self._send(200, ws_action(body))
            except ValueError as e:
                return self._send(400, {"error": str(e)})

        if u.path == "/api/quit":
            # 🧑 app afsluiten vanuit het menu (de losse Mac-app heeft geen venster om te sluiten)
            self._send(200, {"ok": True})
            threading.Timer(0.3, lambda: os._exit(0)).start()
            return None

        if u.path == "/api/update/check":
            return self._send(200, UPD.check())
        if u.path == "/api/update/install":
            if JOB.get("running"):
                return self._send(409, {"error": "Er loopt nog een taak (back-up/laden/test); probeer het daarna."})
            if UPD.snapshot().get("available"):
                try:
                    autobackup("voor update")      # 🧑 altijd eerst een back-up
                except Exception as e:  # noqa: BLE001
                    print(f"Back-up vóór update mislukt: {e}")
            try:
                UPD.install()
            except RuntimeError as e:
                return self._send(400, {"error": str(e)})
            return self._send(200, {"ok": True})
        if u.path == "/api/youtube/check":
            YT.refresh()
            return self._send(200, {"ok": True})
        if u.path == "/api/preset/undo":
            try:
                undo_preset(find_camera(body.get("cam")), clamp(body.get("n"), 0, 254))
                return self._send(200, {"ok": True})
            except KeyError as e:
                return self._send(404, {"ok": False, "error": e.args[0]})
            except Exception as e:  # noqa: BLE001
                return self._send(400, {"ok": False, "error": str(e)})
        if u.path == "/api/camtest":
            try:
                return self._send(200, {"ok": True, **camtest(body)})
            except Exception as e:  # noqa: BLE001
                return self._send(400, {"ok": False, "error": str(e)})
        if u.path == "/api/scan":
            return self._send(200, {"ok": True, **scan_network()})
        if u.path == "/api/midi/config":
            with LOCK:
                cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
                old_midi = cfg.get("midi") or {}
                cfg["midi"] = _midi.norm_cfg({**old_midi, **body})
                _write(cfg)
            MIDI.config_changed(old_midi, cfg["midi"])
            broadcast({"type": "config"})
            return self._send(200, {"ok": True, "config": cfg["midi"]})
        if u.path == "/api/midi/fake_press" and _midi.FAKE:   # alleen met PTZ_MIDI_FAKE (tests)
            _midi.FAKE.press("B" if body.get("bus") == "B" else "A", clamp(body.get("n", 1), 1, 4))
            return self._send(200, {"ok": True})
        if u.path == "/api/midi/record":
            MIDI.set_recording(bool(body.get("on")))
            return self._send(200, {"ok": True})

        if u.path == "/api/backup/restore":
            try:
                return self._send(200, {"ok": True, **restore_any(body)})
            except Exception as e:  # noqa: BLE001
                return self._send(400, {"ok": False, "error": str(e)})

        if u.path in ("/api/backup/capture", "/api/backup/apply", "/api/presets/load", "/api/presets/captest"):
            try:
                kind = {"load": "load", "captest": "captest", "capture": "capture", "apply": "apply"}[u.path.rsplit("/", 1)[1]]
                start_job(kind, body.get("cams") or [],
                          {"positions": bool(body.get("positions")), "image": bool(body.get("image")),
                           "presets": body.get("presets", "used"), "upto": body.get("upto"),
                           "thumbs": body.get("thumbs", True)})
                return self._send(200, {"ok": True})
            except Exception as e:  # noqa: BLE001
                return self._send(409, {"ok": False, "error": str(e)})

        if u.path == "/api/backup/forget":
            with LOCK:
                cfg = json.load(open(CONFIG_PATH, encoding="utf-8"))
                for cam in cfg.get("cameras", []):
                    if cam["id"] == body.get("cam"):
                        cam.pop("saved_positions", None)
                        cam.pop("saved_image", None)
                _write(cfg)
            broadcast({"type": "config"})
            return self._send(200, {"ok": True})

        if u.path == "/api/track":
            try:
                cam = find_camera(body.get("cam"))
                on = body.get("on")
                if on == "toggle":
                    on = not (cam["id"] in TRACKERS or cam["id"] in FOLLOW_TIMERS)
                start_tracking(cam) if on else stop_tracking(cam["id"])
                return self._send(200, {"ok": True, "on": bool(on)})
            except KeyError as e:
                return self._send(404, {"ok": False, "error": e.args[0]})
            except Exception as e:  # noqa: BLE001
                return self._send(400, {"ok": False, "error": str(e)})

        if u.path == "/api/thumb":
            try:
                cam = find_camera(body.get("cam"))
            except KeyError as e:
                return self._send(404, {"ok": False, "error": e.args[0]})
            n = clamp(body.get("n"), 0, 254)
            if body.get("delete"):
                try:
                    os.remove(thumb_path(cam, n))
                except OSError:
                    pass
                broadcast({"type": "thumb", "cam": cam["id"], "n": n})
                return self._send(200, {"ok": True})
            err = snapshot_thumb(cam, n)
            return self._send(502 if err else 200, {"ok": not err, "error": ("Thumbnail mislukt: " + err) if err else None})

        if u.path == "/api/select":
            try:
                cam = find_camera(body.get("cam"))
            except KeyError as e:
                return self._send(404, {"ok": False, "error": e.args[0]})
            broadcast({"type": "select", "cam": cam["id"], "origin": body.get("origin")})
            return self._send(200, {"ok": True})

        if u.path == "/api/cmd":
            try:
                cam = find_camera(body.get("cam"))
                args = body.get("args") or {}
                info = {}
                res = do_action(cam, body.get("action"), args, info)
                return self._send(200, {"ok": True, "response": res, **info})
            except KeyError as e:
                return self._send(404, {"ok": False, "error": e.args[0]})
            except (urllib.error.URLError, OSError) as e:
                return self._send(502, {"ok": False, "error": f"Camera niet bereikbaar: {e}"})
            except Exception as e:  # noqa: BLE001
                return self._send(400, {"ok": False, "error": str(e)})

        self._send(404, {"error": "niet gevonden"})


def local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _melding(tekst):
    """Foutmelding tonen, ook als de losse app geen consolevenster heeft."""
    print(tekst)
    if FROZEN and sys.platform == "win32":
        try:
            import ctypes  # noqa: PLC0415
            ctypes.windll.user32.MessageBoxW(None, tekst, "PTZ Control", 0x30)
        except Exception:  # noqa: BLE001
            pass
    elif FROZEN and sys.platform == "darwin":
        try:
            import subprocess  # noqa: PLC0415
            subprocess.run(["osascript", "-e", f'display alert "PTZ Control" message {json.dumps(tekst)}'], timeout=60)
        except Exception:  # noqa: BLE001
            pass


def _log_naar_bestand():
    """Losse app zonder console: uitvoer naar ptz_control.log in de instellingenmap."""
    if FROZEN and (sys.stdout is None or sys.stderr is None or not sys.stdout.isatty()):
        try:
            f = open(os.path.join(DATA_DIR, "ptz_control.log"), "a", buffering=1, encoding="utf-8")  # noqa: SIM115
            f.write(f"\n--- start {datetime.datetime.now():%Y-%m-%d %H:%M:%S} ---\n")
            sys.stdout = sys.stderr = f
        except OSError:
            pass


def _tray(url, lan_url):
    """Icoon in de menubalk (Mac) / het systeemvak (Windows): openen, map, afsluiten. Blokkeert.

    Geeft False terug als pystray/Pillow ontbreekt; dan draait de server gewoon zonder icoon."""
    try:
        import pystray  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return False
    import webbrowser  # noqa: PLC0415

    def open_map():
        import subprocess  # noqa: PLC0415
        if sys.platform == "win32":
            os.startfile(DATA_DIR)  # noqa: S606
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", DATA_DIR])

    def stoppen(icon, _item=None):
        icon.stop()
        os._exit(0)

    img = Image.open(os.path.join(RES_DIR, "icon.png"))
    menu = pystray.Menu(
        pystray.MenuItem("Bediening openen", lambda *_: webbrowser.open(url), default=True),
        pystray.MenuItem(f"Andere schermen: {lan_url}", lambda *_: webbrowser.open(lan_url)),
        pystray.MenuItem("Map met instellingen openen", lambda *_: open_map()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("PTZ Control afsluiten", stoppen),
    )
    pystray.Icon("PTZ Control", img, "PTZ Control", menu).run()
    return True


def main():
    ap = argparse.ArgumentParser(description="Webbediening voor PTZOptics-camera's")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--open", action="store_true", help="open de bediening in de standaardbrowser")
    ap.add_argument("--no-open", action="store_true", help="losse app: browser níet openen")
    ap.add_argument("--no-tray", action="store_true", help="losse app: geen icoon in menubalk/systeemvak")
    ap.add_argument("--workspace", help="starten in deze workspace (naam of id)")
    args = ap.parse_args()
    _log_naar_bestand()
    if FROZEN and not args.no_open:
        args.open = True   # dubbelklik op de app = bediening openen
    url = f"http://localhost:{args.port}"
    try:
        srv = ThreadingHTTPServer((args.bind, args.port), Handler)
    except OSError:
        # poort bezet: draait PTZ Control al? Dan alleen de bediening openen.
        try:
            urllib.request.urlopen(url + "/api/state", timeout=2).read()
            print(f"PTZ Control draait al op {url}")
            if args.open:
                import webbrowser
                webbrowser.open(url)
            return
        except Exception:  # noqa: BLE001
            _melding(f"Poort {args.port} is bezet door een ander programma. PTZ Control kan niet starten.")
            raise SystemExit(1)
    if args.workspace:
        want = args.workspace.strip().lower()
        w = next((w for w in WS["list"] if w["id"] == want or w["name"].lower() == want), None)
        if w:
            ws_select(w["id"])
        else:
            print(f"Workspace '{args.workspace}' bestaat niet; gestart in '{WS['active']}'.")
    load_config()
    lan_url = f"http://{local_ip()}:{args.port}"
    STATE["server"] = f"{local_ip()}:{args.port}"
    STATE["app"] = {"frozen": FROZEN, "data_dir": DATA_DIR}
    YT.start()
    UPD.start()
    MIDI.start()
    threading.Thread(target=camstatus_loop, daemon=True, name="camstatus").start()
    if os.environ.get("PTZ_AUTOBK", "").lower() not in ("0", "uit", "off"):
        threading.Thread(target=autobackup_loop, daemon=True, name="autobackup").start()
    srv.daemon_threads = True
    print(f"PTZ Control draait op {url}  en  {lan_url}")
    print(f"Instellingen en foto's: {DATA_DIR}  (workspace: {WS['active']})")
    print("Stoppen met Ctrl+C (of dit venster sluiten)")
    if args.open:
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    if FROZEN and not args.no_tray:
        threading.Thread(target=srv.serve_forever, daemon=True, name="http").start()
        if _tray(url, lan_url):
            os._exit(0)
        threading.Event().wait()   # geen icoon mogelijk: blijven draaien (afsluiten via het menu in de bediening)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nGestopt.")


if __name__ == "__main__":
    main()
