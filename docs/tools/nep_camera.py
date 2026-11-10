"""Nagebootste PTZOptics-camera's voor tests (HTTP-CGI + VISCA over TCP), zonder echte hardware.

Elke camera onthoudt pan/tilt/zoom/focus en presets, beweegt geleidelijk (ca. 1,5 s) naar een preset
of absolute positie, en geeft een JPEG op /snapshot.jpg met de huidige positie erin getekend.

    python docs/tools/nep_camera.py 3 9101        # 3 camera's: HTTP op 9101.., VISCA op 9201..
    → host "127.0.0.1:9101", visca_port 9201 (enz.)
"""
import io
import socket
import sys
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


class Cam:
    def __init__(self, name):
        self.name = name
        self.pos = {"pan": 0, "tilt": 0, "zoom": 0, "focus": 1000}
        self.target = dict(self.pos)
        self.presets = {}
        self.lock = threading.Lock()
        self.log = []
        threading.Thread(target=self._mover, daemon=True).start()

    def _mover(self):
        while True:
            time.sleep(0.05)
            with self.lock:
                for k, step in (("pan", 60), ("tilt", 30), ("zoom", 400), ("focus", 200)):
                    d = self.target[k] - self.pos[k]
                    self.pos[k] += max(-step, min(step, d))

    def call(self, n):
        with self.lock:
            if n in self.presets:
                self.target = dict(self.presets[n])

    def store(self, n):
        with self.lock:
            self.presets[n] = dict(self.target)

    def jog(self, dpan=0, dtilt=0):
        with self.lock:
            self.target["pan"] += dpan
            self.target["tilt"] += dtilt

    def jpeg(self):
        if Image is None:
            return TINY_JPEG
        with self.lock:
            p = dict(self.pos)
        im = Image.new("RGB", (320, 180), (30 + (p["pan"] // 10) % 200, 60, 90))
        d = ImageDraw.Draw(im)
        d.text((10, 10), f"{self.name}\npan {p['pan']}\ntilt {p['tilt']}\nzoom {p['zoom']}", fill=(255, 255, 255))
        b = io.BytesIO()
        im.save(b, "JPEG")
        return b.getvalue()


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


def visca_server(cam, port):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(16)

    def client(c):
        buf = b""
        with c:
            while True:
                try:
                    data = c.recv(256)
                except OSError:
                    return
                if not data:
                    return
                buf += data
                while b"\xff" in buf:
                    msg, buf = buf.split(b"\xff", 1)
                    c.sendall(handle(msg))

    def handle(m):
        cam.log.append(m.hex(" "))
        ack = b"\x90\x41\xff\x90\x51\xff"
        if m[:4] == b"\x81\x01\x04\x3f" and len(m) >= 6:          # preset reset/set/call
            {1: cam.store, 2: cam.call}.get(m[4], lambda n: None)(m[5])
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
        while True:
            c, _ = srv.accept()
            threading.Thread(target=client, args=(c,), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()


def http_server(cam, port):
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
                elif len(q) >= 2 and q[0] == "ptzcmd" and q[1] in ("left", "right", "up", "down"):
                    cam.jog(dpan={"left": -200, "right": 200}.get(q[1], 0), dtilt={"up": 100, "down": -100}.get(q[1], 0))
                body, ctype = b"OK", "text/plain"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    s = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()


def start(n=3, base=9101):
    cams = []
    for i in range(n):
        c = Cam(f"Camera {i + 1}")
        http_server(c, base + i)
        visca_server(c, base + 100 + i)
        cams.append(c)
    return cams


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    base = int(sys.argv[2]) if len(sys.argv) > 2 else 9101
    start(n, base)
    print(f"{n} nep-camera's: HTTP 127.0.0.1:{base}…, VISCA {base + 100}…")
    threading.Event().wait()
