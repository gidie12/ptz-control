"""
Volgmodule voor PTZ Control: volgt een persoon op het podium en stuurt ALLEEN pan bij.
Tilt en zoom worden nooit aangeraakt.

Vereist:  pip install opencv-python numpy
Detectors, in deze volgorde:
  1. YOLOv8n via `ultralytics` (als geïnstalleerd; gebruikt de Apple-chip/GPU).
  2. YOLOX-tiny als ONNX via OpenCV (`cv2.dnn`, geen torch nodig): zo werkt de losse app.
     Het modelbestand `yolox_tiny.onnx` (Apache 2.0) haal je met `packaging/haal_model.py`.
  3. OpenCV HOG: herkent alleen HELE personen (van top tot teen). Een half silhouet
     (ingezoomd) ziet hij nooit, daarom alleen als laatste terugval.
"""
import os
import threading
import time

try:
    import cv2
    import numpy as np
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
YOLOX_FILE = "yolox_tiny.onnx"
YOLOX_SIZE = 416        # invoermaat van yolox_tiny / yolox_nano
MIN_SCORE = 0.35


def find_model(dirs):
    """Zoek het YOLOX-modelbestand: PTZ_PERSON_MODEL, daarna <map>/models/ en <map>/ voor elke map."""
    env = os.environ.get("PTZ_PERSON_MODEL")
    cands = [env] if env else []
    for d in dirs or []:
        if d:
            cands += [os.path.join(d, "models", YOLOX_FILE), os.path.join(d, YOLOX_FILE)]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


class Yolox:
    """YOLOX (Megvii, Apache 2.0) als ONNX via cv2.dnn. Herkent ook halve silhouetten (bovenlichaam)."""

    def __init__(self, path, size=YOLOX_SIZE):
        self.net = cv2.dnn.readNetFromONNX(path)
        self.size = size
        grids, strides = [], []
        for st in (8, 16, 32):
            n = size // st
            yv, xv = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
            grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
            strides.append(np.full((n * n, 1), st))
        self.grid = np.concatenate(grids).astype(np.float32)
        self.stride = np.concatenate(strides).astype(np.float32)

    def detect(self, frame, min_score=MIN_SCORE):
        h, w = frame.shape[:2]
        size = self.size
        r = min(size / h, size / w)
        nw, nh = max(1, int(w * r)), max(1, int(h * r))
        pad = np.full((size, size, 3), 114, np.uint8)       # zoals YOLOX traint: links-boven, grijze rand
        pad[:nh, :nw] = cv2.resize(frame, (nw, nh))
        self.net.setInput(cv2.dnn.blobFromImage(pad, 1.0, (size, size), swapRB=False))
        out = self.net.forward()[0]                         # (N, 85): cx, cy, w, h, obj, 80 klassen
        score = out[:, 4] * out[:, 5]                       # klasse 0 = persoon
        keep = score >= min_score
        if not keep.any():
            return []
        out, score = out[keep], score[keep]
        xy = (out[:, :2] + self.grid[keep]) * self.stride[keep] / r
        wh = np.exp(out[:, 2:4]) * self.stride[keep] / r
        boxes = [[float(x - bw / 2), float(y - bh / 2), float(bw), float(bh)] for (x, y), (bw, bh) in zip(xy, wh)]
        idx = cv2.dnn.NMSBoxes(boxes, score.tolist(), min_score, 0.45)
        res = []
        for i in np.array(idx).flatten():
            x, y, bw, bh = boxes[int(i)]
            x2, y2 = min(w, x + bw), min(h, y + bh)          # binnen het beeld houden
            x, y = max(0.0, x), max(0.0, y)
            res.append(((x + x2) / 2 / w, (y + y2) / 2 / h, (x2 - x) / w, (y2 - y) / h, float(score[int(i)])))
        return res


class Detector:
    """Geeft personen terug als (cx, cy, w, h, score), genormaliseerd 0..1.
    model_dirs = mappen waarin naar yolox_tiny.onnx gezocht wordt."""

    def __init__(self, model_dirs=None):
        self.lock = threading.Lock()
        self.kind = "hog"
        self.model = None
        self.device = "cpu"
        self.note = ""
        try:
            from ultralytics import YOLO  # type: ignore
            import torch  # type: ignore
            self.device = "cuda" if torch.cuda.is_available() else \
                "mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "cpu"
            self.model = YOLO("yolov8n.pt")
            self.kind = "yolo"
            return
        except Exception:  # noqa: BLE001
            pass
        path = find_model(model_dirs)
        if path:
            try:
                self.model = Yolox(path)
                self.kind = "yolox"
                return
            except Exception as e:  # noqa: BLE001
                self.note = f"YOLOX-model niet te laden ({e})"
        else:
            self.note = "geen YOLOX-model gevonden: alleen hele personen worden herkend"
        if not hasattr(cv2, "HOGDescriptor"):
            raise RuntimeError("Geen persoonsdetector: installeer 'ultralytics' of opencv-python 4.x "
                               "(pip install -r requirements.txt)")
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def detect(self, frame):
        h, w = frame.shape[:2]
        with self.lock:
            if self.kind == "yolox":
                return self.model.detect(frame)
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


# ---------------------------------------------------------------- uiterlijk (om één persoon vast te houden)
def appearance(frame, box):
    """Kleurhistogram (HSV, tint x verzadiging) van het bovenlijf in box = (cx, cy, w, h, …) genormaliseerd.
    Zo blijft de tracker bij dezelfde persoon als iemand anders kruist of dichtbij komt."""
    if frame is None or cv2 is None:
        return None
    fh, fw = frame.shape[:2]
    cx, cy, bw, bh = box[:4]
    top = cy - bh / 2
    x1, x2 = int((cx - bw * 0.3) * fw), int((cx + bw * 0.3) * fw)
    y1, y2 = int((top + bh * 0.15) * fh), int((top + bh * 0.6) * fh)   # schouders/borst, ook bij een half silhouet
    x1, x2 = max(0, x1), min(fw, x2)
    y1, y2 = max(0, y1), min(fh, y2)
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    hsv = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256])
    cv2.normalize(hist, hist, 1, 0, cv2.NORM_L1)
    return hist


def similarity(a, b):
    """1 = zelfde kleuren, 0 = totaal anders. Onbekend uiterlijk telt als 'misschien' (0.5)."""
    if a is None or b is None:
        return 0.5
    return max(0.0, 1.0 - float(cv2.compareHist(a, b, cv2.HISTCMP_BHATTACHARYYA)))


# ---------------------------------------------------------------- volgkader
DEFAULT_FRAME = {"l": 0.3, "r": 0.7, "x": 0.5}


def norm_frame(f):
    """Volgkader {l, r, x} als deel van de beeldbreedte: de persoon moet tussen l en r blijven;
    komt hij erbuiten, dan draait de camera tot hij weer op x staat. Ongeldig → standaard."""
    try:
        l, r = float(f["l"]), float(f["r"])
        x = float(f.get("x", (l + r) / 2))
    except (TypeError, KeyError, ValueError):
        return dict(DEFAULT_FRAME)
    l, r = max(0.0, min(0.9, l)), max(0.1, min(1.0, r))
    if r - l < 0.1:
        return dict(DEFAULT_FRAME)
    x = max(l + 0.02, min(r - 0.02, x))
    return {"l": round(l, 3), "r": round(r, 3), "x": round(x, 3)}


def norm_limits(v):
    """Pan-grenzen {left, right} als ruwe VISCA-panwaarden van de camera (beide nodig), anders None."""
    try:
        left, right = int(v["left"]), int(v["right"])
    except (TypeError, KeyError, ValueError):
        return None
    return {"left": left, "right": right} if left != right else None


class PanGuard(threading.Thread):
    """Leest tijdens het volgen een paar keer per seconde de panpositie (VISCA) en stopt de camera
    vóór een grens. read_pan() -> int (gooit een fout als het niet lukt).
    Welke kant 'rechts' is volgt uit de twee ingestelde waarden (rechts > links of andersom),
    dus het maakt niet uit hoe de camera telt of of hij ondersteboven hangt."""
    POLL = 0.15
    REACT = 0.35   # s: zo ver vooruit kijken (reactietijd + uitlopen)

    def __init__(self, read_pan, limits, on_block):
        super().__init__(daemon=True)
        self.read_pan = read_pan
        self.limits = limits
        self.on_block = on_block      # on_block(direction): direct stoppen als we die kant op draaien
        self.running = True
        self.pan = None
        self.vel = 0.0
        self.error = ""
        self.lock = threading.Lock()

    def set_limits(self, limits):
        with self.lock:
            self.limits = limits

    def blocked(self, direction):
        """True als de camera die kant ('left'/'right') niet verder mag."""
        with self.lock:
            lim, pan, vel = self.limits, self.pan, self.vel
        if not lim or pan is None or direction not in ("left", "right"):
            return False
        sign = 1 if lim["right"] > lim["left"] else -1
        span = abs(lim["right"] - lim["left"])
        margin = span * 0.01 + abs(vel) * self.REACT
        if direction == "right":
            return (lim["right"] - pan) * sign <= margin
        return (pan - lim["left"]) * sign <= margin

    def run(self):
        last_t, last_p, fails = None, None, 0
        while self.running:
            try:
                p = self.read_pan()
                t = time.time()
                with self.lock:
                    if last_p is not None and t > last_t:
                        v = (p - last_p) / (t - last_t)
                        self.vel = 0.5 * v + 0.5 * self.vel
                    self.pan = p
                last_t, last_p, fails, self.error = t, p, 0, ""
                for d in ("left", "right"):
                    if self.blocked(d):
                        self.on_block(d)
            except Exception as e:  # noqa: BLE001
                fails += 1
                with self.lock:
                    self.pan = None if fails >= 3 else self.pan
                self.error = f"panpositie niet te lezen ({e})"
                time.sleep(min(2.0, 0.3 * fails))
            time.sleep(self.POLL)
        close = getattr(self.read_pan, "close", None)
        if close:
            close()

    def stop(self):
        self.running = False


# ---------------------------------------------------------------- pan-regelaar
class PanController:
    """Zet de afwijking van de doelpositie om in pan links/rechts/stop met evenredige snelheid.
    Hysterese: begint pas te draaien als `start` waar is (persoon buiten het kader),
    stopt pas als de persoon weer (bijna) op de doelpositie staat."""

    STOP_AT = 0.03

    def __init__(self, max_speed=6, min_speed=1, invert=False):
        self.max_speed = max_speed
        self.min_speed = min_speed
        self.invert = invert
        self.state = None  # None | ("left"|"right", speed)
        self.boost = 0.0   # extra snelheid zolang de persoon buiten het kader blijft (inhalen bij doorlopen)
        self.t_last = time.time()

    def update(self, err, start, urgent=False):
        """err = persoon_x - doel_x (positief = staat rechts van het doel). start = persoon buiten het kader.
        urgent = persoon raakt de beeldrand: meteen topsnelheid. Geeft nieuw commando of None (geen wijziging)."""
        if self.invert:
            err = -err
        a = abs(err)
        now = time.time()
        dt, self.t_last = min(0.3, now - self.t_last), now
        if self.state is None and not start:
            return None
        if self.state is not None and a <= self.STOP_AT:
            self.state, self.boost = None, 0.0
            return ("stop", 0)
        direction = "right" if err > 0 else "left"
        if self.state is not None and self.state[0] != direction:
            self.boost = 0.0
        # blijft hij buiten het kader terwijl we al draaien, dan loopt hij sneller dan wij: snelheid laten oplopen
        # per seconde, zodat het niet afhangt van het aantal detecties per seconde
        self.boost = min(float(self.max_speed), self.boost + 12.0 * dt) if start and self.state is not None \
            else max(0.0, self.boost - 12.0 * dt)
        frac = min(1.0, a / 0.35)
        speed = self.max_speed if urgent else round(self.min_speed + frac * (self.max_speed - self.min_speed) + self.boost)
        new = (direction, max(self.min_speed, min(self.max_speed, speed)))
        if new == self.state:
            return None
        self.state = new
        return new

    def halt(self):
        was = self.state
        self.state, self.boost = None, 0.0
        return ("stop", 0) if was else None


# ---------------------------------------------------------------- tracker per camera
class CameraTracker(threading.Thread):
    LOST_STOP = 0.4      # s zonder doel -> pan stoppen
    LOST_AUTO = 6.0      # s zonder (automatisch gekozen) doel -> nieuw doel mogen kiezen; een aangeklikt doel blijft
    MAX_JUMP = 0.22      # max sprong (beeldbreedte) tussen twee beelden om nog hetzelfde doel te zijn
    MIN_HEIGHT = 0.10    # kleinere personen (ver weg in een totaalshot) negeren
    MIN_SIM = 0.45       # uiterlijk dat minstens moet lijken om hetzelfde doel te zijn (dichtbij)
    REACQ_SIM = 0.62     # na kwijtraken: alleen terugvinden als het uiterlijk duidelijk lijkt
    PICK_WAIT = 20.0     # s zoeken naar een aangeklikte persoon voor we het opgeven
    # max. detecties per seconde (per camera): genoeg om een spreker te volgen, scheelt CPU/GPU t.o.v. elk beeld (25/s)
    MAX_FPS = max(1.0, float(os.environ.get("PTZ_TRACK_FPS") or 8))

    def __init__(self, cam, url, detector, send, on_status, settings):
        super().__init__(daemon=True)
        self.cam = cam
        self.detector = detector
        self.send = send                # send(action, args) -> camera
        self.on_status = on_status      # on_status(dict)
        self.grabber = FrameGrabber(url)
        self.ctrl = PanController(max_speed=settings.get("max_speed", 6),
                                  invert=settings.get("invert", False))
        self.frame_cfg = norm_frame(settings.get("frame") or DEFAULT_FRAME)
        self.lock = threading.Lock()
        self.running = True
        self.halt_on_exit = True
        self.target = None        # gladgestreken x van het doel
        self.box = None           # laatste box van het doel (cx, cy, w, h)
        self.model = None         # kleurhistogram van het doel
        self.picked = False       # doel is aangeklikt (blijft, ook als hij even weg is)
        self.pending = None       # (cx, hist, sinds) aangeklikte persoon die we nog moeten vinden
        self.last_seen = 0.0
        self.status = ""
        self.fps = 0.0
        self.sent = None          # laatst verzonden richting (left/right) of None
        self.at_limit = None      # 'left'/'right' als we tegen een grens staan
        self.guard = None
        if settings.get("read_pan"):
            self.guard = PanGuard(settings["read_pan"], norm_limits(settings.get("limits")), self._guard_block)
        pick = settings.get("pick")
        if pick:
            self.pick(*pick)

    # --- bediening vanuit de server
    def set_frame(self, frame):
        with self.lock:
            self.frame_cfg = norm_frame(frame)

    def set_limits(self, limits):
        if self.guard:
            self.guard.set_limits(norm_limits(limits))

    def _guard_block(self, direction):
        """Vanuit de PanGuard: draaien we die kant op, dan nu stoppen."""
        if self.sent == direction:
            self.at_limit = direction
            self.ctrl.state, self.ctrl.boost = None, 0.0
            self._cmd(("stop", 0))

    def pick(self, cx, hist):
        """Volg deze persoon (aangeklikt in een beeld van zojuist)."""
        with self.lock:
            self.pending = (float(cx), hist, time.time())
            self.picked = True
        self._status("zoekt gekozen persoon", force=True)

    def release(self):
        """Terug naar automatisch: de grootste persoon in beeld."""
        with self.lock:
            self.pending = None
            self.picked = False
            self.target = None
            self.model = None
        self._status("zoekt persoon", force=True)

    def target_box(self):
        """Box van het huidige doel als het net gezien is, anders None."""
        return self.box if self.box and time.time() - self.last_seen < 1.0 else None

    def _status(self, status, force=False):
        if status != self.status or force:
            self.status = status
            self.on_status({"status": status, "fps": round(self.fps, 1), "detector": self.detector.kind,
                            "note": getattr(self.detector, "note", ""), "picked": self.picked,
                            "frame": self.frame_cfg})

    def _cmd(self, c):
        if c is None:
            return
        direction, speed = c
        if direction != "stop" and not self.running:
            return  # gestopt terwijl we detecteerden: geen nieuwe beweging meer sturen
        if direction != "stop" and self.guard and self.guard.blocked(direction):
            # grens: niet verder die kant op (de persoon mag dan uit het kader lopen)
            self.at_limit = direction
            self.ctrl.state, self.ctrl.boost = None, 0.0
            if self.sent is None:
                return
            direction = "stop"
        elif direction != "stop":
            self.at_limit = None
        try:
            if direction == "stop":
                self.sent = None
                self.send("stop", {})
            else:
                self.sent = direction
                self.send("move", {"dir": direction, "pan": speed, "tilt": 1})
        except Exception as e:  # noqa: BLE001
            self._status(f"camera-fout: {e}")

    def _learn(self, hist, sim):
        """Uiterlijk langzaam bijwerken (licht verandert), alleen als we zeker zijn van de match."""
        if hist is None:
            return
        if self.model is None:
            self.model = hist.copy()
        elif sim >= 0.7:
            self.model = cv2.addWeighted(self.model, 0.92, hist, 0.08, 0)

    def _pick(self, people, frame):
        people = [p for p in people if p[3] > self.MIN_HEIGHT]
        if not people:
            return None
        now = time.time()
        hists = [appearance(frame, p) for p in people]
        with self.lock:
            pending, fc = self.pending, self.frame_cfg
        if pending:
            cx, hist, since = pending
            # beeld van het aanklikken is van zojuist: plek telt mee, uiterlijk het zwaarst
            scored = [(similarity(hist, h) - abs(p[0] - cx) * 1.5, i) for i, (p, h) in enumerate(zip(people, hists))]
            score, i = max(scored)
            p, h = people[i], hists[i]
            ok = (abs(p[0] - cx) <= 0.35 and similarity(hist, h) >= self.MIN_SIM) or similarity(hist, h) >= self.REACQ_SIM \
                or (hist is None and abs(p[0] - cx) <= 0.2)
            if ok:
                with self.lock:
                    self.pending = None
                self.model = hist.copy() if hist is not None else None
                self._learn(h, 1.0)
                self.target = None          # nieuw doel: niet gladstrijken met het oude
                return p
            if now - since > self.PICK_WAIT:
                with self.lock:
                    self.pending, self.picked = None, False
            return None
        if self.target is not None:
            lost = now - self.last_seen
            best, best_cost, best_sim = None, 9.0, 0.0
            for p, h in zip(people, hists):
                sim = similarity(self.model, h)
                d = abs(p[0] - self.target)
                if lost < self.LOST_STOP:
                    # gewoon volgen: dichtbij én gelijkend
                    if d > self.MAX_JUMP or sim < self.MIN_SIM:
                        continue
                    cost = d / self.MAX_JUMP + (1 - sim)
                else:
                    # kwijt geweest: overal zoeken, maar alleen op duidelijk gelijkend uiterlijk
                    if sim < self.REACQ_SIM and not (d <= self.MAX_JUMP and sim >= self.MIN_SIM + 0.1):
                        continue
                    cost = (1 - sim) + d * 0.3
                if cost < best_cost:
                    best, best_cost, best_sim = (p, h), cost, sim
            if best:
                self._learn(best[1], best_sim)
                return best[0]
            if not self.picked and lost > self.LOST_AUTO:
                self.target, self.model = None, None     # automatisch doel opgeven: hierna nieuw kiezen
            return None
        # nieuw doel: de grootste persoon (meestal de spreker vooraan), liefst binnen het kader,
        # bij gelijkspel het dichtst bij de doelpositie
        i = max(range(len(people)), key=lambda k: (fc["l"] <= people[k][0] <= fc["r"], round(people[k][3], 1),
                                                    -abs(people[k][0] - fc["x"])))
        self.model = None
        self._learn(hists[i], 1.0)
        return people[i]

    def _steer(self, hit):
        """Persoon binnen het kader houden: pas draaien als hij er (met zijn hele box) uit loopt,
        dan terug naar de doelpositie."""
        with self.lock:
            fc = self.frame_cfg
        l, r, tx = fc["l"], fc["r"], fc["x"]
        bw = hit[2]
        x1, x2 = self.target - bw / 2, self.target + bw / 2
        if bw >= (r - l) * 0.9:
            # persoon breder dan het kader (ver ingezoomd): dan op het midden van de persoon letten
            outside = abs(self.target - tx) > max(0.05, (r - l) * 0.25)
        else:
            outside = x1 < l or x2 > r
        urgent = (x1 <= 0.02 and self.target < tx) or (x2 >= 0.98 and self.target > tx)
        return self.ctrl.update(self.target - tx, outside, urgent)

    def run(self):
        self.grabber.start()
        if self.guard:
            self.guard.start()
        self._status("verbinden…")
        last_ts, frames, t0, last_det = 0.0, 0, time.time(), 0.0
        while self.running:
            wait = last_det + 1.0 / self.MAX_FPS - time.time()
            if wait > 0:
                time.sleep(min(wait, 0.2))
                continue
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
            last_det = time.time()
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

            hit = self._pick(people, frame)
            now = time.time()
            if hit:
                x = hit[0]
                self.target = x if self.target is None or now - self.last_seen > self.LOST_STOP \
                    else 0.6 * x + 0.4 * self.target
                self.last_seen = now
                self.box = tuple(float(v) for v in hit[:4])
                self._cmd(self._steer(hit))
                base = "volgt gekozen persoon" if self.picked else "volgt"
                lim = self.at_limit if self.at_limit and self.guard and self.guard.blocked(self.at_limit) else None
                self._status(base + (f" · grens {'links' if lim == 'left' else 'rechts'} bereikt" if lim else ""))
            else:
                if now - self.last_seen > self.LOST_STOP:
                    self._cmd(self.ctrl.halt())
                    with self.lock:
                        waiting = self.pending is not None
                    self._status("zoekt gekozen persoon" if waiting
                                 else "gekozen persoon kwijt" if self.picked and self.target is not None
                                 else "zoekt persoon")
        if self.halt_on_exit:
            self._cmd(self.ctrl.halt())
        self.grabber.stop()
        if self.guard:
            self.guard.stop()

    def stop(self, halt=True):
        """halt=False als er direct een ander commando (preset/handbediening) volgt."""
        self.halt_on_exit = halt
        self.running = False
