"""Simulatie om *persoon volgen* te testen zonder camera (vereist opencv-python + numpy).

Start PTZ Control met één nagebootste camera "Sim" die echt draait op de pan-commando's van de tracker,
een getekend podium met drie personen in verschillende kleding (rood loopt heen en weer, blauw kruist,
groen staat stil) en een eenvoudige detector die de (getekende) hoofden vindt. Het uiterlijk (kleur van het
bovenlijf) gaat wel door de echte code van tracker.py, net als het kader en de pan-regelaar.

    python docs/tools/volg_sim.py /tmp/ptz-volg            # bediening op http://127.0.0.1:8080
    curl http://127.0.0.1:8091/state                        # toestand van de simulatie (JSON)

Aannames: beeldhoek = 1 eenheid wereld, 1 snelheidsstap = 4°/s bij 60° beeldhoek (zoals de eerdere simulaties).
"""
import json
import math
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ptz-volg"
os.makedirs(DATA, exist_ok=True)
os.environ["PTZ_DATA"] = DATA
for k in ("PTZ_CAMSTATUS", "PTZ_UPDATE", "PTZ_AUTOBK"):
    os.environ.setdefault(k, "0")
os.environ.setdefault("PTZ_YT_BASE", "http://127.0.0.1:9")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
cfg_path = os.path.join(DATA, "cameras.json")
if not os.path.exists(cfg_path):
    json.dump({"cameras": [{"id": "sim", "name": "Sim", "host": "127.0.0.1:9", "protocol": "http",
                            "presets": {"1": "Podium"}, "track_speed": 8}],
               "preset_count": 8}, open(cfg_path, "w"), indent=2)
VISCA_PORT = int(os.environ.get("PTZ_SIM_VISCA", "8092"))
_c = json.load(open(cfg_path))
for _cam in _c["cameras"]:
    if _cam["id"] == "sim":
        _cam["visca_port"] = VISCA_PORT          # panpositie (grenzen) via de nagebootste VISCA-poort
json.dump(_c, open(cfg_path, "w"), indent=2)

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import tracker  # noqa: E402
import ptz_control  # noqa: E402

W, H = 640, 360
SKIN = (140, 170, 225)          # BGR; de detector zoekt precies deze kleur
STEP = 4.0 / 60.0               # wereld-eenheden per seconde per snelheidsstap


class Sim:
    def __init__(self):
        self.pan = 0.0
        self.vel = 0.0
        self.t0 = time.time()
        self.last = self.t0
        self.lock = threading.Lock()
        self.cmds = 0
        # (naam, shirtkleur BGR, functie t -> wereld-x, diepte: hoger = vooraan)
        self.people = [
            ("rood", (40, 40, 200), lambda t: 0.9 * math.sin(t * 2 * math.pi / 24), 1),
            ("blauw", (200, 90, 30), lambda t: 0.25 + 0.7 * math.sin(t * 2 * math.pi / 17 + 2.0), 2),
            ("groen", (60, 170, 60), lambda t: -0.75, 0),
        ]

    def step(self):
        with self.lock:
            now = time.time()
            self.pan += self.vel * (now - self.last)
            self.last = now
            return now - self.t0, self.pan

    def command(self, action, args):
        self.step()
        with self.lock:
            self.cmds += 1
            if action == "move":
                d = args.get("dir", "")
                s = float(args.get("pan") or 1)
                self.vel = STEP * s * (1 if "right" in d else -1 if "left" in d else 0)
            elif action == "stop":
                self.vel = 0.0
        return "ok"

    def positions(self):
        t, pan = self.step()
        return t, pan, [(n, col, f(t) - pan + 0.5, z) for n, col, f, z in self.people]

    def render(self):
        t, pan, ps = self.positions()
        img = np.zeros((H, W, 3), np.uint8)
        img[:] = (70, 60, 55)
        img[int(H * 0.72):] = (40, 55, 80)                 # podiumvloer
        for k in range(-30, 30):                            # wandpanelen: bewegen mee met pan
            x = int(((k * 0.25 - pan) + 0.5) * W)
            if -10 < x < W + 10:
                cv2.line(img, (x, 0), (x, int(H * 0.72)), (95, 85, 80), 3)
        for n, col, u, z in sorted(ps, key=lambda p: p[3]):
            cx = int(u * W)
            bw, top = int(0.09 * W), int(0.24 * H)
            cv2.rectangle(img, (cx - bw // 2, top + 30), (cx + bw // 2, int(H * 0.62)), col, -1)       # bovenlijf
            cv2.rectangle(img, (cx - bw // 2 + 4, int(H * 0.62)), (cx + bw // 2 - 4, int(H * 0.94)), (45, 40, 40), -1)
            cv2.circle(img, (cx, top + 14), 14, SKIN, -1)                                              # hoofd
        return img

    def state(self):
        t, pan, ps = self.positions()
        return {"t": round(t, 2), "pan": round(pan, 3), "vel": round(self.vel, 3), "cmds": self.cmds,
                "people": {n: round(u, 3) for n, col, u, z in ps}}


SIM = Sim()


class SimGrabber(threading.Thread):
    def __init__(self, url):
        super().__init__(daemon=True)
        self.frame, self.ts, self.error, self.running = None, 0.0, "", True
        self.lock = threading.Lock()

    def run(self):
        while self.running:
            f = SIM.render()
            with self.lock:
                self.frame, self.ts = f, time.time()
            time.sleep(1 / 12)

    def latest(self):
        with self.lock:
            return self.frame, self.ts

    def stop(self):
        self.running = False


class HeadDetector:
    """Vindt de getekende hoofden; box = hoofd tot voeten, zoals een echte detector een persoon geeft."""
    kind = "sim"
    note = ""

    def detect(self, frame):
        h, w = frame.shape[:2]
        mask = cv2.inRange(frame, np.array(SKIN) - 3, np.array(SKIN) + 3)
        n, _, stats, cent = cv2.connectedComponentsWithStats(mask)
        out = []
        for i in range(1, n):
            x, y, bw, bh, area = stats[i]
            if area < 60:
                continue
            cx = (x + bw / 2) / w
            top = y / h
            out.append((cx, (top + 0.94) / 2, 0.09, 0.94 - top, 0.9))
        return out


ptz_control.run_command = lambda cam, action, args: SIM.command(action, args)
_orig_http_get = ptz_control.http_get


def _http_get(cam, path, binary=False):
    if path == "/snapshot.jpg":
        return cv2.imencode(".jpg", SIM.render())[1].tobytes(), "image/jpeg"
    return _orig_http_get(cam, path, binary)


ptz_control.http_get = _http_get
tracker.FrameGrabber = SimGrabber
tracker.Detector = lambda dirs=None: HeadDetector()


def visca_server():
    """Alleen de positie-inquiry 81 09 06 12 FF: pan = wereld-x × 1000 (rechts = hoger), tilt 0."""
    import socket
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", VISCA_PORT))
    srv.listen(8)

    def client(c):
        buf = b""
        with c:
            while True:
                d = c.recv(256)
                if not d:
                    return
                buf += d
                while b"\xff" in buf:
                    msg, buf = buf.split(b"\xff", 1)
                    if msg == bytes.fromhex("81090612"):
                        p = round(SIM.step()[1] * 1000) & 0xFFFF
                        nib = bytes((p >> s) & 0xF for s in (12, 8, 4, 0))
                        c.sendall(b"\x90\x50" + nib + b"\x00\x00\x00\x00\xff")
                    else:
                        c.sendall(bytes.fromhex("906002ff"))

    while True:
        conn, _ = srv.accept()
        threading.Thread(target=client, args=(conn,), daemon=True).start()


threading.Thread(target=visca_server, daemon=True).start()


class StateHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(SIM.state()).encode()
        if self.path.startswith("/pan?"):          # testbediening: camera naar wereld-x
            with SIM.lock:
                SIM.pan, SIM.vel = float(self.path.split("=", 1)[1]), 0.0
            body = b'{"ok": true}'
        if self.path.startswith("/reset"):
            with SIM.lock:
                SIM.pan, SIM.vel, SIM.t0, SIM.last = 0.0, 0.0, time.time(), time.time()
            body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("PTZ_SIM_PORT", "8091"))), StateHandler).serve_forever, daemon=True).start()
sys.argv = [sys.argv[0], "--no-open", "--no-tray", "--port", os.environ.get("PTZ_PORT", "8080")]
ptz_control.main()
