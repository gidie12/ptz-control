"""Nagebootste PTZOptics-camera's (HTTP-CGI + VISCA over TCP), alleen stdlib (Pillow optioneel).

Gebruikt door de 🧑 **oefenstand** in de app (camera's in de kerk bewegen niet) en door de testtool
``docs/tools/nep_camera.py``. Elke camera onthoudt pan/tilt/zoom/focus en presets, beweegt geleidelijk naar een
preset of absolute positie, en geeft een JPEG op ``/snapshot.jpg`` met de naam en positie erin.
Servers luisteren alleen op 127.0.0.1 en zijn te stoppen met ``Cam.stop()``.
"""
import io
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from PIL import Image, ImageDraw
except ImportError:  # zonder Pillow een vaste mini-JPEG
    Image = None

TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720"
    "222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101011100ffc4001f0000010501010101010100000000000000000102030405"
    "060708090a0bffc400b5100002010303020403050504040000017d01020300041105122131410613516107227114328191a1082342b1c11552d1f02433627282"
    "090a161718191a25262728292a3435363738393a434445464748494a535455565758595a636465666768696a737475767778797a838485868788898a92939495"
    "969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda00"
    "08010100003f00fbd3ffd9")

MOVES = {"left": (-1, 0), "right": (1, 0), "up": (0, 1), "down": (0, -1), "leftup": (-1, 1), "rightup": (1, 1),
         "leftdown": (-1, -1), "rightdown": (1, -1)}


class Cam:
    def __init__(self, name, label=""):
        self.name = name
        self.label = label                    # bijv. "OEFENEN" in het beeld
        self.pos = {"pan": 0, "tilt": 0, "zoom": 0, "focus": 1000}
        self.target = dict(self.pos)
        self.presets = {}
        self.vel = (0, 0, 0)                  # pan/tilt/zoom per tik bij handbediening
        self.lock = threading.Lock()
        self.log = []
        self.http_port = self.visca_port = None
        self._stop = threading.Event()
        self._servers = []
        threading.Thread(target=self._mover, daemon=True).start()

    def _mover(self):
        while not self._stop.is_set():
            time.sleep(0.05)
            with self.lock:
                vp, vt, vz = self.vel
                if vp or vt or vz:
                    self.target["pan"] += vp
                    self.target["tilt"] = max(-1200, min(1200, self.target["tilt"] + vt))
                    self.target["zoom"] = max(0, min(16384, self.target["zoom"] + vz))
                for k, step in (("pan", 60), ("tilt", 30), ("zoom", 400), ("focus", 200)):
                    d = self.target[k] - self.pos[k]
                    self.pos[k] += max(-step, min(step, d))

    def call(self, n):
        with self.lock:
            self.vel = (0, 0, 0)
            if n in self.presets:
                self.target = dict(self.presets[n])

    def store(self, n):
        with self.lock:
            self.presets[n] = dict(self.target)

    def reset(self, n):
        with self.lock:
            self.presets.pop(n, None)

    def jog(self, dpan=0, dtilt=0):
        with self.lock:
            self.target["pan"] += dpan
            self.target["tilt"] += dtilt

    def move(self, d, speed=5):
        dp, dt = MOVES.get(d, (0, 0))
        with self.lock:
            self.vel = (dp * 6 * speed, dt * 3 * speed, self.vel[2])

    def zoom(self, d, speed=3):
        with self.lock:
            self.vel = (self.vel[0], self.vel[1], {"in": 60, "out": -60}.get(d, 0) * speed)

    def halt(self):
        with self.lock:
            self.vel = (0, 0, 0)
            self.target = dict(self.pos)

    def home(self):
        with self.lock:
            self.vel = (0, 0, 0)
            self.target.update(pan=0, tilt=0)

    def jpeg(self):
        if Image is None:
            return TINY_JPEG
        with self.lock:
            p = dict(self.pos)
        hue = (p["pan"] // 8) % 360
        im = Image.new("RGB", (320, 180), (40 + hue % 120, 55 + (p["tilt"] // 20) % 60, 95))
        d = ImageDraw.Draw(im)
        # simpele "zaal": horizon schuift mee met tilt, kruisje met pan
        hy = 110 + p["tilt"] // 20
        d.rectangle([0, hy, 320, 180], fill=(60, 45, 35))
        cx = 160 - (p["pan"] // 10) % 320
        for x in (cx, cx + 320):
            d.rectangle([x - 3, hy - 60, x + 3, hy], fill=(220, 200, 160))
            d.rectangle([x - 18, hy - 46, x + 18, hy - 40], fill=(220, 200, 160))
        txt = f"{self.name}\npan {p['pan']}  tilt {p['tilt']}\nzoom {p['zoom']}"
        if self.label:
            txt = self.label + "\n" + txt
        d.text((10, 8), txt, fill=(255, 255, 255))
        b = io.BytesIO()
        im.save(b, "JPEG")
        return b.getvalue()

    def stop(self):
        self._stop.set()
        for s in self._servers:
            try:
                s()
            except Exception:  # noqa: BLE001
                pass


def nib(v, n):
    v &= (1 << (4 * n)) - 1
    return bytes((v >> (4 * (n - 1 - i))) & 0xF for i in range(n))


def unnib(b):
    v = 0
    for x in b:
        v = (v << 4) | (x & 0xF)
    return v


def signed(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def visca_server(cam, port=0):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(16)
    cam.visca_port = srv.getsockname()[1]

    def client(c):
        buf = b""
        with c:
            while not cam._stop.is_set():
                try:
                    data = c.recv(256)
                except OSError:
                    return
                if not data:
                    return
                buf += data
                while b"\xff" in buf:
                    msg, buf = buf.split(b"\xff", 1)
                    try:
                        c.sendall(handle(msg))
                    except OSError:
                        return

    def handle(m):
        cam.log.append(m.hex(" "))
        ack = b"\x90\x41\xff\x90\x51\xff"
        if m[:4] == b"\x81\x01\x04\x3f" and len(m) >= 6:          # preset reset/set/call
            {0: cam.reset, 1: cam.store, 2: cam.call}.get(m[4], lambda n: None)(m[5])
            return ack
        if m[:4] == b"\x81\x01\x06\x01" and len(m) >= 8:          # pan/tilt drive
            dirs = {(3, 1): "up", (3, 2): "down", (1, 3): "left", (2, 3): "right", (1, 1): "leftup",
                    (2, 1): "rightup", (1, 2): "leftdown", (2, 2): "rightdown"}
            d = dirs.get((m[6], m[7]))
            cam.move(d, max(1, m[4] // 4)) if d else cam.halt()
            return ack
        if m[:4] == b"\x81\x01\x06\x04":                            # home
            cam.home()
            return ack
        if m[:4] == b"\x81\x01\x04\x07" and len(m) >= 5:          # zoom
            z = m[4] >> 4
            cam.zoom({2: "in", 3: "out"}.get(z, ""), max(1, m[4] & 0xF))
            return ack
        if m[:4] == b"\x81\x01\x06\x02" and len(m) >= 14:         # absoluut pan/tilt
            with cam.lock:
                cam.target["pan"] = signed(unnib(m[6:10]), 16)
                cam.target["tilt"] = signed(unnib(m[10:14]), 16)
            return ack
        if m[:4] == b"\x81\x01\x04\x47" and len(m) >= 8:          # zoom direct
            with cam.lock:
                cam.target["zoom"] = unnib(m[4:8])
            return ack
        if m[:4] == b"\x81\x01\x04\x48" and len(m) >= 8:          # focus direct
            with cam.lock:
                cam.target["focus"] = unnib(m[4:8])
            return ack
        with cam.lock:
            p = dict(cam.pos)
        if m == b"\x81\x09\x06\x12":
            return b"\x90\x50" + nib(p["pan"], 4) + nib(p["tilt"], 4) + b"\xff"
        if m == b"\x81\x09\x04\x47":
            return b"\x90\x50" + nib(p["zoom"], 4) + b"\xff"
        if m == b"\x81\x09\x04\x48":
            return b"\x90\x50" + nib(p["focus"], 4) + b"\xff"
        if m == b"\x81\x09\x04\x38":
            return b"\x90\x50\x02\xff"
        return ack

    def loop():
        while not cam._stop.is_set():
            try:
                c, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=client, args=(c,), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    cam._servers.append(srv.close)
    return cam.visca_port


def http_server(cam, port=0):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/snapshot.jpg"):
                body, ctype = cam.jpeg(), "image/jpeg"
            else:
                q = self.path.split("?", 1)[-1].split("&")
                cam.log.append(self.path)
                if len(q) >= 3 and q[0] == "ptzcmd" and q[1] in ("poscall", "posset"):
                    (cam.call if q[1] == "poscall" else cam.store)(int(q[2]))
                elif len(q) >= 2 and q[0] == "ptzcmd" and q[1] in MOVES:
                    speed = int(q[2]) if len(q) >= 3 and q[2].isdigit() else 5
                    cam.move(q[1], max(1, speed // 4))
                elif len(q) >= 2 and q[0] == "ptzcmd" and q[1] == "ptzstop":
                    cam.halt()
                elif len(q) >= 2 and q[0] == "ptzcmd" and q[1] == "home":
                    cam.home()
                elif len(q) >= 2 and q[0] == "ptzcmd" and q[1] in ("zoomin", "zoomout", "zoomstop"):
                    speed = int(q[2]) if len(q) >= 3 and q[2].isdigit() else 3
                    cam.zoom(q[1][4:], max(1, speed))
                body, ctype = b"OK", "text/plain"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    s = ThreadingHTTPServer(("127.0.0.1", port), H)
    s.daemon_threads = True
    cam.http_port = s.server_address[1]
    threading.Thread(target=s.serve_forever, daemon=True).start()

    def close():
        s.shutdown()
        s.server_close()
    cam._servers.append(close)
    return cam.http_port


def start_cam(name, http_port=0, visca_port=0, label=""):
    c = Cam(name, label)
    http_server(c, http_port)
    visca_server(c, visca_port)
    return c
