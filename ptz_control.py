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
import hashlib
import json
import os
import queue
import socket
import struct
import threading
import time
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



# ---------------------------------------------------------------- OBS (YouTube-stream)
class OBSClient:
    """Minimale OBS WebSocket v5-client (OBS 28+), alleen standaardbibliotheek.
    Houdt verbinding open, herverbindt automatisch, en meldt stream/opname-status aan alle schermen."""

    def __init__(self):
        self.sock = None
        self.leftover = {}
        self.lock = threading.Lock()
        self.pending = {}
        self.req_id = 0
        self.status = {"type": "obs", "configured": False, "connected": False, "streaming": False,
                       "recording": False, "timecode": "", "error": ""}
        self.wake = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    # -- websocket framing
    def _send_frame(self, text):
        data = text.encode()
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x81])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        with self.lock:
            self.sock.sendall(head + mask + masked)

    def _recv_exact(self, n, sock):
        buf = self.leftover.pop(sock, b"")
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("OBS verbinding gesloten")
            buf += chunk
        if len(buf) > n:
            self.leftover[sock] = buf[n:]
        return buf[:n]

    def _recv_message(self, sock=None):
        sock = sock or self.sock
        parts = b""
        while True:
            b1, b2 = self._recv_exact(2, sock)
            op, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2, sock))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8, sock))[0]
            mask = self._recv_exact(4, sock) if b2 & 0x80 else None
            payload = self._recv_exact(n, sock)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if op == 8:
                raise ConnectionError("OBS sloot de verbinding")
            if op == 9:  # ping -> pong
                with self.lock:
                    m = os.urandom(4)
                    sock.sendall(bytes([0x8A, 0x80 | len(payload)]) + m +
                                      bytes(b ^ m[i % 4] for i, b in enumerate(payload)))
                continue
            if op in (1, 0):
                parts += payload
                if b1 & 0x80:
                    return json.loads(parts)

    # -- protocol
    def _connect(self, cfg):
        self.leftover = {}
        host, port, pw = cfg.get("host") or "127.0.0.1", int(cfg.get("port") or 4455), cfg.get("password") or ""
        self.sock = socket.create_connection((host, port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                           f"Sec-WebSocket-Protocol: obswebsocket.json\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise ConnectionError("Geen antwoord van OBS")
            resp += chunk
        if b" 101 " not in resp.split(b"\r\n")[0]:
            raise ConnectionError("OBS WebSocket weigert verbinding")
        rest = resp.split(b"\r\n\r\n", 1)[1]
        if rest:
            self.leftover[self.sock] = rest
        hello = self._recv_message()
        ident = {"rpcVersion": 1, "eventSubscriptions": 64}  # 64 = Outputs-events
        auth = hello.get("d", {}).get("authentication")
        if auth:
            if not pw:
                raise ConnectionError("OBS vraagt een wachtwoord")
            secret = base64.b64encode(hashlib.sha256((pw + auth["salt"]).encode()).digest()).decode()
            ident["authentication"] = base64.b64encode(
                hashlib.sha256((secret + auth["challenge"]).encode()).digest()).decode()
        self._send_frame(json.dumps({"op": 1, "d": ident}))
        msg = self._recv_message()
        if msg.get("op") != 2:
            raise ConnectionError("OBS-login mislukt (wachtwoord?)")
        self.sock.settimeout(None)

    def request(self, rtype, data=None, timeout=5):
        if not self.status["connected"]:
            raise ConnectionError("Niet verbonden met OBS")
        with self.lock:
            self.req_id += 1
            rid = str(self.req_id)
        ev = threading.Event()
        self.pending[rid] = [ev, None]
        self._send_frame(json.dumps({"op": 6, "d": {"requestType": rtype, "requestId": rid,
                                                    "requestData": data or {}}}))
        if not ev.wait(timeout):
            self.pending.pop(rid, None)
            raise TimeoutError("OBS reageert niet")
        res = self.pending.pop(rid)[1]
        st = res.get("requestStatus", {})
        if not st.get("result"):
            raise RuntimeError(st.get("comment") or f"OBS-fout {st.get('code')}")
        return res.get("responseData") or {}

    def _set(self, **kw):
        changed = any(self.status.get(k) != v for k, v in kw.items())
        self.status.update(kw)
        if changed:
            broadcast(dict(self.status))

    def _reader(self, sock):
        try:
            while True:
                msg = self._recv_message(sock)
                d = msg.get("d", {})
                if msg.get("op") == 7 and d.get("requestId") in self.pending:
                    self.pending[d["requestId"]][1] = d
                    self.pending[d["requestId"]][0].set()
                elif msg.get("op") == 5:
                    et, ed = d.get("eventType"), d.get("eventData", {})
                    if et == "StreamStateChanged":
                        self._set(streaming=bool(ed.get("outputActive")))
                    elif et == "RecordStateChanged":
                        self._set(recording=bool(ed.get("outputActive")))
        except Exception as e:  # noqa: BLE001
            if sock is not self.sock:  # oude verbinding na reconnect: negeren
                return
            self._set(connected=False, streaming=False, recording=False, timecode="", error=str(e))
            try:
                self.sock.close()
            except OSError:
                pass

    def _loop(self):
        while True:
            cfg = load_config().get("obs") or {}
            if not cfg.get("enabled"):
                self._set(configured=False, connected=False, error="")
                self.wake.wait(5)
                self.wake.clear()
                continue
            self.status["configured"] = True
            if not self.status["connected"]:
                try:
                    self._connect(cfg)
                    self._set(configured=True, connected=True, error="")
                    threading.Thread(target=self._reader, args=(self.sock,), daemon=True).start()
                except Exception as e:  # noqa: BLE001
                    self._set(configured=True, connected=False, error=str(e))
                    try:
                        self.sock and self.sock.close()
                    except OSError:
                        pass
                    self.wake.wait(5)
                    self.wake.clear()
                    continue
            # status/timecode bijwerken
            try:
                st = self.request("GetStreamStatus")
                rec = self.request("GetRecordStatus")
                self._set(streaming=bool(st.get("outputActive")), recording=bool(rec.get("outputActive")),
                          timecode=(st.get("outputTimecode") or "")[:8] if st.get("outputActive") else "")
            except Exception:  # noqa: BLE001
                pass
            self.wake.wait(2)
            self.wake.clear()

    def reconnect(self):
        try:
            self.sock and self.sock.close()
        except OSError:
            pass
        self.status["connected"] = False
        self.wake.set()

    def action(self, name):
        req = {"start_stream": "StartStream", "stop_stream": "StopStream", "toggle_stream": "ToggleStream",
               "start_record": "StartRecord", "stop_record": "StopRecord", "toggle_record": "ToggleRecord"}.get(name)
        if not req:
            raise ValueError(f"onbekende OBS-actie: {name}")
        self.request(req)
        self.wake.set()


OBS = None

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
                prefix = "".join(ch for ch in cam["id"] if ch.isalnum() or ch in "-_") + "_"
                try:
                    files = os.listdir(THUMB_DIR)
                except OSError:
                    files = []
                cam["thumbs"] = sorted(int(f[len(prefix):-4]) for f in files
                                       if f.startswith(prefix) and f.endswith(".jpg") and f[len(prefix):-4].isdigit())
            cfg["tracking_error"] = tracking_error()
            return self._send(200, cfg)
        if u.path == "/api/state":
            return self._send(200, {**STATE, "obs": OBS.status})
        if u.path == "/api/events":
            return self._sse()
        if u.path.startswith("/api/joy/"):
            return self._joy(u.path[len("/api/joy/"):], parse_qs(u.query))
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

    # --- Server-Sent Events: browsers volgen joystick/andere tablets live
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
            self.wfile.write(f"data: {json.dumps(OBS.status)}\n\n".encode())
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

    # --- Endpoints voor PT-JOY-G4 / SuperJoy custom buttons & automatic triggers (HTTP GET)
    def _joy(self, what, qs):
        g = lambda k, d="": qs.get(k, [d])[0]  # noqa: E731
        try:
            if what == "select":
                cam = find_camera(g("cam"))
                broadcast({"type": "select", "cam": cam["id"]})
                return self._send(200, {"ok": True, "cam": cam["name"]})
            if what == "preset":
                cam = find_camera(g("cam") or STATE["active"])
                n = clamp(g("n"), 0, 254)
                do_action(cam, "preset_call", {"n": n, "speed": g("speed") or None})
                return self._send(200, {"ok": True})
            if what == "scene":
                cfg = load_config()
                scenes = cfg.get("scenes", [])
                ref = g("n") or g("name")
                scene = next((s for i, s in enumerate(scenes, 1)
                              if str(i) == ref or s.get("name", "").lower() == ref.lower()), None)
                if not scene:
                    raise KeyError(f"Onbekende scène: {ref}")
                errors = run_scene(scene)
                return self._send(200 if not errors else 207, {"ok": not errors, "errors": errors})
            if what == "track":
                cam = find_camera(g("cam") or STATE["active"])
                on = g("on", "toggle")
                if on == "toggle":
                    on = not (cam["id"] in TRACKERS or cam["id"] in FOLLOW_TIMERS)
                else:
                    on = on in ("1", "true", "on")
                start_tracking(cam) if on else stop_tracking(cam["id"])
                return self._send(200, {"ok": True, "on": on})
            if what == "obs":
                OBS.action(g("action"))
                return self._send(200, {"ok": True})
            if what == "cmd":
                cam = find_camera(g("cam") or STATE["active"])
                args = {k: v[0] for k, v in qs.items() if k not in ("cam", "action")}
                if "on" in args:
                    args["on"] = args["on"] in ("1", "true", "on")
                do_action(cam, g("action"), args)
                return self._send(200, {"ok": True})
        except KeyError as e:
            return self._send(404, {"ok": False, "error": e.args[0]})
        except (urllib.error.URLError, OSError) as e:
            return self._send(502, {"ok": False, "error": f"Camera niet bereikbaar: {e}"})
        except Exception as e:  # noqa: BLE001
            return self._send(400, {"ok": False, "error": str(e)})
        self._send(404, {"error": "onbekend joystick-endpoint"})

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
            for cam in body["cameras"]:
                cam.pop("thumbs", None)
            save_config(body)
            broadcast({"type": "config"})
            OBS.reconnect()
            return self._send(200, {"ok": True})

        if u.path == "/api/scene":
            try:
                scenes = load_config().get("scenes", [])
                idx = clamp(body.get("n"), 1, max(1, len(scenes))) - 1
                errors = run_scene(scenes[idx])
                return self._send(200, {"ok": not errors, "errors": errors})
            except IndexError:
                return self._send(404, {"ok": False, "error": "Onbekende scène"})

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

        if u.path == "/api/obs":
            try:
                if body.get("action") == "reconnect":
                    OBS.reconnect()
                else:
                    OBS.action(body.get("action"))
                return self._send(200, {"ok": True})
            except Exception as e:  # noqa: BLE001
                return self._send(502, {"ok": False, "error": str(e)})

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
    global OBS
    STATE["server"] = f"{local_ip()}:{args.port}"
    OBS = OBSClient()
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
