"""
Volgmodule voor PTZ Control: volgt een persoon op het podium en stuurt ALLEEN pan bij.
Tilt en zoom worden nooit aangeraakt.

Vereist:  pip install opencv-python numpy
Beter (optioneel, nauwkeuriger, liefst met GPU):  pip install ultralytics
  -> dan wordt automatisch YOLOv8n gebruikt (model wordt de eerste keer gedownload).
  Zonder ultralytics valt hij terug op de ingebouwde OpenCV-persoonsdetector (HOG, alleen CPU).
"""
import threading
import time

try:
    import cv2
    import numpy as np  # noqa: F401  (vereist door cv2)
    CV_ERROR = ""
except Exception as e:  # noqa: BLE001
    cv2 = None
    CV_ERROR = f"OpenCV ontbreekt ({e}). Installeer met: pip install opencv-python numpy"


def available():
    return cv2 is not None


# ---------------------------------------------------------------- videobron
class FrameGrabber(threading.Thread):
    """Leest de stream continu en bewaart alleen het nieuwste beeld (geen opgebouwde vertraging)."""

    def __init__(self, url):
        super().__init__(daemon=True)
        self.url = url
        self.frame = None
        self.ts = 0.0
        self.running = True
        self.error = ""
        self.lock = threading.Lock()

    def run(self):
        while self.running:
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG) if self.url.startswith(("rtsp", "http")) \
                else cv2.VideoCapture(self.url)
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:  # noqa: BLE001
                pass
            if not cap.isOpened():
                self.error = f"Kan stream niet openen: {self.url}"
                time.sleep(2)
                continue
            self.error = ""
            fails = 0
            is_file = not self.url.startswith(("rtsp", "http", "udp", "srt"))
            delay = 1.0 / (cap.get(cv2.CAP_PROP_FPS) or 25) if is_file else 0
            while self.running:
                ok, frame = cap.read()
                if not ok and is_file:  # testbestand: opnieuw afspelen
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                if delay:
                    time.sleep(delay)
                if not ok:
                    fails += 1
                    if fails > 25:
                        self.error = "Stream onderbroken"
                        break
                    time.sleep(0.02)
                    continue
                fails = 0
                with self.lock:
                    self.frame, self.ts = frame, time.time()
            cap.release()
            if self.running:
                time.sleep(1)

    def latest(self):
        with self.lock:
            return self.frame, self.ts

    def stop(self):
        self.running = False


# ---------------------------------------------------------------- persoonsdetectie
class Detector:
    """Geeft personen terug als (cx, cy, w, h, score), genormaliseerd 0..1."""

    def __init__(self):
        self.lock = threading.Lock()
        self.kind = "hog"
        self.model = None
        self.device = "cpu"
        try:
            from ultralytics import YOLO  # type: ignore
            import torch  # type: ignore
            self.device = "cuda" if torch.cuda.is_available() else \
                "mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "cpu"
            self.model = YOLO("yolov8n.pt")
            self.kind = "yolo"
        except Exception:  # noqa: BLE001
            if not hasattr(cv2, "HOGDescriptor"):
                raise RuntimeError("Geen persoonsdetector: installeer 'ultralytics' of opencv-python 4.x "
                                   "(pip install -r requirements.txt)")
            self.hog = cv2.HOGDescriptor()
            self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def detect(self, frame):
        h, w = frame.shape[:2]
        with self.lock:
            if self.kind == "yolo":
                res = self.model(frame, classes=[0], conf=0.35, imgsz=640, device=self.device, verbose=False)[0]
                res = res.cpu()
                out = []
                for (x1, y1, x2, y2), s in zip(res.boxes.xyxy.tolist(), res.boxes.conf.tolist()):
                    out.append(((x1 + x2) / 2 / w, (y1 + y2) / 2 / h, (x2 - x1) / w, (y2 - y1) / h, float(s)))
                return out
            scale = 480.0 / w
            small = cv2.resize(frame, (480, int(h * scale))) if w > 480 else frame
            sw = small.shape[1]
            sh = small.shape[0]
            rects, weights = self.hog.detectMultiScale(small, winStride=(8, 8), padding=(8, 8), scale=1.05)
            out = []
            for (x, y, rw, rh), s in zip(rects, weights):
                s = float(s[0] if hasattr(s, "__len__") else s)
                if s < 0.4:
                    continue
                out.append(((x + rw / 2) / sw, (y + rh / 2) / sh, rw / sw, rh / sh, s))
            return out


# ---------------------------------------------------------------- pan-regelaar
class PanController:
    """Zet de horizontale afwijking om in pan links/rechts/stop met evenredige snelheid.
    Hysterese: begint te draaien buiten de dode zone, stopt pas als het doel weer dicht bij het midden is."""

    def __init__(self, deadzone=0.10, max_speed=6, min_speed=1, invert=False):
        self.deadzone = deadzone
        self.max_speed = max_speed
        self.min_speed = min_speed
        self.invert = invert
        self.state = None  # None | ("left"|"right", speed)

    def update(self, err):
        """err = doel_x - 0.5 (positief = doel staat rechts). Geeft nieuw commando of None (geen wijziging)."""
        if self.invert:
            err = -err
        a = abs(err)
        stop_at = self.deadzone * 0.5
        if self.state is None and a <= self.deadzone:
            return None
        if self.state is not None and a <= stop_at:
            self.state = None
            return ("stop", 0)
        if a <= self.deadzone and self.state is not None:
            # binnen de dode zone maar nog niet in het midden: langzaam uitlopen
            direction, speed = self.state[0], self.min_speed
        else:
            direction = "right" if err > 0 else "left"
            frac = min(1.0, (a - self.deadzone) / max(0.01, 0.5 - self.deadzone))
            speed = round(self.min_speed + frac * (self.max_speed - self.min_speed))
        new = (direction, max(self.min_speed, min(self.max_speed, speed)))
        if new == self.state:
            return None
        self.state = new
        return new

    def halt(self):
        was = self.state
        self.state = None
        return ("stop", 0) if was else None


# ---------------------------------------------------------------- tracker per camera
class CameraTracker(threading.Thread):
    LOST_STOP = 0.4      # s zonder doel -> pan stoppen
    LOST_RESET = 2.5     # s zonder doel -> nieuw doel mogen kiezen
    MAX_JUMP = 0.22      # max sprong (beeldbreedte) om nog hetzelfde doel te zijn

    def __init__(self, cam, url, detector, send, on_status, settings):
        super().__init__(daemon=True)
        self.cam = cam
        self.detector = detector
        self.send = send                # send(action, args) -> camera
        self.on_status = on_status      # on_status(dict)
        self.grabber = FrameGrabber(url)
        self.ctrl = PanController(deadzone=settings.get("deadzone", 0.10),
                                  max_speed=settings.get("max_speed", 6),
                                  invert=settings.get("invert", False))
        self.zone = settings.get("zone", (0.0, 1.0))  # alleen personen in dit horizontale gebied
        self.running = True
        self.halt_on_exit = True
        self.target = None        # gladgestreken x van het doel
        self.last_seen = 0.0
        self.status = ""
        self.fps = 0.0

    def _status(self, status, force=False):
        if status != self.status or force:
            self.status = status
            self.on_status({"status": status, "fps": round(self.fps, 1), "detector": self.detector.kind})

    def _cmd(self, c):
        if c is None:
            return
        direction, speed = c
        if direction != "stop" and not self.running:
            return  # gestopt terwijl we detecteerden: geen nieuwe beweging meer sturen
        try:
            if direction == "stop":
                self.send("stop", {})
            else:
                self.send("move", {"dir": direction, "pan": speed, "tilt": 1})
        except Exception as e:  # noqa: BLE001
            self._status(f"camera-fout: {e}")

    def _pick(self, people):
        lo, hi = self.zone
        people = [p for p in people if lo <= p[0] <= hi and p[3] > 0.15]
        if not people:
            return None
        now = time.time()
        if self.target is not None and now - self.last_seen < self.LOST_RESET:
            best = min(people, key=lambda p: abs(p[0] - self.target))
            return best if abs(best[0] - self.target) <= self.MAX_JUMP else None
        # nieuw doel: grootste persoon (meestal de spreker vooraan), bij gelijkspel het dichtst bij het midden
        return max(people, key=lambda p: (round(p[3], 1), -abs(p[0] - 0.5)))

    def run(self):
        self.grabber.start()
        self._status("verbinden…")
        last_ts, frames, t0 = 0.0, 0, time.time()
        while self.running:
            frame, ts = self.grabber.latest()
            if frame is None or time.time() - ts > 1.5:
                self._cmd(self.ctrl.halt())
                self._status(self.grabber.error or "geen beeld")
                time.sleep(0.2)
                continue
            if ts == last_ts:
                time.sleep(0.01)
                continue
            last_ts = ts
            try:
                people = self.detector.detect(frame)
            except Exception as e:  # noqa: BLE001
                self._status(f"detectiefout: {e}")
                time.sleep(0.5)
                continue
            if not self.running:
                break
            frames += 1
            if time.time() - t0 >= 2:
                self.fps = frames / (time.time() - t0)
                frames, t0 = 0, time.time()
                self._status(self.status, force=True)

            hit = self._pick(people)
            now = time.time()
            if hit:
                x = hit[0]
                self.target = x if self.target is None or now - self.last_seen > self.LOST_RESET \
                    else 0.6 * x + 0.4 * self.target
                self.last_seen = now
                self._cmd(self.ctrl.update(self.target - 0.5))
                self._status("volgt")
            else:
                if now - self.last_seen > self.LOST_STOP:
                    self._cmd(self.ctrl.halt())
                    self._status("zoekt persoon")
        if self.halt_on_exit:
            self._cmd(self.ctrl.halt())
        self.grabber.stop()

    def stop(self, halt=True):
        """halt=False als er direct een ander commando (preset/handbediening) volgt."""
        self.halt_on_exit = halt
        self.running = False
