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
import base64
import datetime
import json
import os
import queue
import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "cameras.json")
INDEX_PATH = os.path.join(HERE, "index.html")
THUMB_DIR = os.path.join(HERE, "thumbs")
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


def thumb_path(cam, n):
    safe = "".join(ch for ch in cam["id"] if ch.isalnum() or ch in "-_")
    return os.path.join(THUMB_DIR, f"{safe}_{int(n)}.jpg")


def capture_thumb(cam, n):
    """Sla een snapshot op als thumbnail voor preset n. Geeft True bij succes."""
    try:
        data, _ = http_get(cam, "/snapshot.jpg", binary=True)
    except Exception:  # noqa: BLE001
        return False
    os.makedirs(THUMB_DIR, exist_ok=True)
    tmp = thumb_path(cam, n) + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, thumb_path(cam, n))
    broadcast({"type": "thumb", "cam": cam["id"], "n": int(n)})
    return True


def do_action(cam, action, args):
    """Voer een commando uit en houd alle schermen (en thumbnails) bij."""
    if action in STOPS_TRACKING and (cam["id"] in TRACKERS or cam["id"] in FOLLOW_TIMERS):
        stop_tracking(cam["id"], halt=False)
    res = run_command(cam, action, args)
    if action == "preset_call":
        n = clamp(args.get("n"), 0, 254)
        broadcast({"type": "preset", "cam": cam["id"], "n": n})
        if n in (cam.get("track_presets") or []) and not tracking_error():
            follow_after_preset(cam)
    elif action == "preset_set":
        n = clamp(args.get("n"), 0, 254)
        capture_thumb(cam, n)
        broadcast({"type": "preset", "cam": cam["id"], "n": n})
    return res


def run_scene(scene):
    """Scène = meerdere camera's tegelijk naar een preset (zoals SuperPresets)."""
    errors = []
    for step in scene.get("steps", []):
        try:
            cam = find_camera(step["cam"])
            do_action(cam, "preset_call", {"n": step["n"]})
        except Exception as e:  # noqa: BLE001
            errors.append(f"{step.get('cam')}: {e}")
    return errors



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
import backup as _backup  # noqa: E402

JOB = {"type": "job", "running": False, "kind": "", "cams": {}}
JOB_LOCK = threading.Lock()


def _safe_id(cam_id):
    return "".join(ch for ch in cam_id if ch.isalnum() or ch in "-_")


def list_thumbs(cam):
    prefix = _safe_id(cam["id"]) + "_"
    try:
        files = os.listdir(THUMB_DIR)
    except OSError:
        return []
    return sorted(int(f[len(prefix):-4]) for f in files
                  if f.startswith(prefix) and f.endswith(".jpg") and f[len(prefix):-4].isdigit())


def now_stamp():
    return datetime.datetime.now().isoformat(timespec="seconds")


def make_backup(with_passwords=True):
    cfg = load_config()
    if not with_passwords:
        for cam in cfg.get("cameras", []):
            cam.pop("password", None)
    thumbs = {}
    for cam in cfg.get("cameras", []):
        for n in list_thumbs(cam):
            with open(thumb_path(cam, n), "rb") as f:
                thumbs[f"{cam['id']}/{n}"] = base64.b64encode(f.read()).decode()
    return {"type": "ptz-control-backup", "version": 1, "created": now_stamp(),
            "with_passwords": with_passwords, "config": cfg, "thumbs": thumbs}


def restore_backup(data):
    if not isinstance(data, dict) or data.get("type") != "ptz-control-backup":
        raise ValueError("Dit is geen PTZ Control-back-upbestand")
    new = data.get("config") or {}
    if not isinstance(new.get("cameras"), list):
        raise ValueError("Back-up bevat geen camera's")
    current = {c["id"]: c for c in load_config().get("cameras", [])}
    for cam in new["cameras"]:
        cam.pop("thumbs", None)
        old = current.get(cam.get("id"), {})
        if "password" not in cam and old.get("password"):   # back-up zonder wachtwoorden: huidige houden
            cam["password"] = old["password"]
    save_config(new)
    os.makedirs(THUMB_DIR, exist_ok=True)
    count = 0
    cams = {c["id"]: c for c in new["cameras"]}
    for key, b64 in (data.get("thumbs") or {}).items():
        cam_id, _, n = key.rpartition("/")
        if cam_id in cams and n.isdigit():
            with open(thumb_path(cams[cam_id], int(n)), "wb") as f:
                f.write(base64.b64decode(b64))
            count += 1
    broadcast({"type": "config"})
    return {"cameras": len(new["cameras"]), "thumbs": count, "scenes": len(new.get("scenes") or [])}


def _job_update(cam_id, **kw):
    with JOB_LOCK:
        JOB["cams"].setdefault(cam_id, {}).update(kw)
        snapshot = json.loads(json.dumps(JOB))
    broadcast(snapshot)


def start_job(kind, cam_ids, opts):
    """kind = 'capture' (uit camera lezen) of 'apply' (naar camera terugzetten)."""
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
            if kind == "capture":
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
        if kind == "capture" and results:
            with LOCK:
                fresh = json.load(open(CONFIG_PATH, encoding="utf-8"))
                for cam in fresh.get("cameras", []):
                    if cam["id"] in results:
                        pos, img = results[cam["id"]]
                        if pos:
                            cam.setdefault("saved_positions", {}).update(pos)
                        if img:
                            cam["saved_image"] = img
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
        if "/api/snapshot" not in (args[0] if args else ""):
            super().log_message(fmt, *args)

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            with open(INDEX_PATH, "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if u.path == "/api/config":
            cfg = load_config()
            for cam in cfg.get("cameras", []):
                cam["thumbs"] = list_thumbs(cam)
            cfg["tracking_error"] = tracking_error()
            return self._send(200, cfg)
        if u.path == "/api/backup":
            q = parse_qs(u.query)
            data = make_backup(with_passwords=q.get("passwords", ["1"])[0] == "1")
            body = json.dumps(data, ensure_ascii=False, indent=1).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition",
                             f'attachment; filename="ptz-control-backup-{datetime.date.today().isoformat()}.json"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return None
        if u.path == "/api/job":
            return self._send(200, JOB)
        if u.path == "/api/state":
            return self._send(200, STATE)
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


    def do_POST(self):
        u = urlparse(self.path)
        try:
            body = self._body()
        except json.JSONDecodeError:
            return self._send(400, {"error": "ongeldige JSON"})

        if u.path == "/api/config":
            if not isinstance(body.get("cameras"), list):
                return self._send(400, {"error": "cameras ontbreekt"})
            body.pop("tracking_error", None)
            body.pop("obs", None)  # YouTube/OBS-koppeling is verwijderd
            existing = {c["id"]: c for c in load_config().get("cameras", [])}
            for cam in body["cameras"]:
                cam.pop("thumbs", None)
                # uitgelezen posities/beeld alleen via de back-uptaken wijzigen, nooit via een (mogelijk verouderd) scherm
                for key in ("saved_positions", "saved_image"):
                    cam.pop(key, None)
                    if key in existing.get(cam.get("id"), {}):
                        cam[key] = existing[cam["id"]][key]
            save_config(body)
            broadcast({"type": "config"})
            return self._send(200, {"ok": True})

        if u.path == "/api/scene":
            try:
                scenes = load_config().get("scenes", [])
                idx = clamp(body.get("n"), 1, max(1, len(scenes))) - 1
                errors = run_scene(scenes[idx])
                return self._send(200, {"ok": not errors, "errors": errors})
            except IndexError:
                return self._send(404, {"ok": False, "error": "Onbekende scène"})

        if u.path == "/api/backup/restore":
            try:
                return self._send(200, {"ok": True, **restore_backup(body)})
            except Exception as e:  # noqa: BLE001
                return self._send(400, {"ok": False, "error": str(e)})

        if u.path in ("/api/backup/capture", "/api/backup/apply"):
            try:
                start_job("capture" if u.path.endswith("capture") else "apply", body.get("cams") or [],
                          {"positions": bool(body.get("positions")), "image": bool(body.get("image")),
                           "presets": body.get("presets", "used")})
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
            ok = capture_thumb(cam, n)
            return self._send(200 if ok else 502, {"ok": ok, "error": None if ok else "Geen snapshot van camera"})

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
                res = do_action(cam, body.get("action"), args)
                return self._send(200, {"ok": True, "response": res})
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


def main():
    ap = argparse.ArgumentParser(description="Webbediening voor PTZOptics-camera's")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--open", action="store_true", help="open de bediening in de standaardbrowser")
    args = ap.parse_args()
    load_config()
    STATE["server"] = f"{local_ip()}:{args.port}"
    srv = ThreadingHTTPServer((args.bind, args.port), Handler)
    srv.daemon_threads = True
    print(f"PTZ Control draait op http://localhost:{args.port}  en  http://{local_ip()}:{args.port}")
    print("Stoppen met Ctrl+C (of dit venster sluiten)")
    if args.open:
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nGestopt.")


if __name__ == "__main__":
    main()
