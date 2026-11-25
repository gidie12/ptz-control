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

De pan-regelaar (`PanFollower`), het vasthouden van één persoon (`Identity`, galerij van zijn uiterlijk) en het
meetlog (`TrackLog`) zijn overgezet van Churchfront Video (Tracker.swift, Identity.swift),
Copyright 2026 Churchfront LLC, Apache License 2.0: https://github.com/Churchfront/churchfront-video
Gewijzigd voor deze app: alleen pan; VISCA-snelheidsstappen (1–24) i.p.v. graden per seconde; kleurhistogram
i.p.v. Apple Vision; het volgkader als dode zone; vooruitrekenen over de beeldvertraging en die vertraging meten;
terugval zonder positie van de camera. Zie NOTICE.
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
        self.error = self.error_detail = ""
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
                self.error = "geen verbinding met de camerastream"
                self.error_detail = f"Niet te openen: {self.url} — staat RTSP aan op de camera en klopt het adres?"
                time.sleep(2)
                continue
            self.error = self.error_detail = ""
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
                    self.frame, self.ts = frame, clock()
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


def distance(a, b):
    """Hoe verschillend twee uiterlijken zijn: 0 = zelfde kleuren, 1 = totaal anders, None = niet te zeggen.
    `b` mag ook een galerij (lijst) zijn: dan de kleinste afstand tot een van de beelden."""
    if a is None or b is None:
        return None
    if isinstance(b, (list, tuple)):
        ds = [d for d in (distance(a, g) for g in b) if d is not None]
        return min(ds) if ds else None
    return max(0.0, min(1.0, float(cv2.compareHist(a, b, cv2.HISTCMP_BHATTACHARYYA))))


class Identity:
    """Welke persoon in een nieuw beeld is degene die we volgen? Uit waar iedereen staat en hoe ze eruitzien.
    Overgezet van `IdentityMatch` in Churchfront Video (Identity.swift). Zonder beelden of camera's,
    zodat het los te testen is.

    Een kandidaat = (positie, uiterlijk): afstand tot de laatste plek van de gevolgde persoon (deel van het
    beeld) en hoe verschillend hij eruitziet (0..1, None = niet te zeggen)."""

    @staticmethod
    def limit(own, others):
        """Hoe verschillend iemand er hooguit uit mag zien om nog de gevolgde persoon te zijn als er niemand
        is om mee te vergelijken. Halverwege 'hij' en 'de anderen' zodra beide bekend zijn; daarvoor ruim,
        want de goede persoon ten onrechte weigeren stopt het volgen helemaal."""
        if own is None:
            return None
        if others is not None and others > own:
            return max(0.45, own + (others - own) * 0.5)
        return max(0.6, own * 3)

    @staticmethod
    def choose(people, reach, gone, own=None, others=None, unsure=0.0):
        """reach = hoe ver hij sinds de laatste keer gekomen kan zijn; gone = s sinds hij gezien is;
        own/others = geleerd verschil van hemzelf en van de mensen om hem heen; unsure = s sinds zijn
        uiterlijk het voor het laatst bevestigde. Geeft (index, zeker) of None = shot vasthouden en zoeken."""
        near = [i for i, p in enumerate(people) if p[0] < reach]
        if not near:
            return None
        nearest = min(near, key=lambda i: people[i][0])
        known = sorted((i for i in near if people[i][1] is not None), key=lambda i: people[i][1])
        continuous = gone < 0.7     # net nog gezien: de plek telt zwaar; na een gat niet meer
        lim = Identity.limit(own, others)
        if len(known) >= 2:
            # twee of meer die het kunnen zijn (gekruist, of naast elkaar): wie het meest lijkt, als dat
            # duidelijk is. Geen vast getal: één moet duidelijk beter passen dan de volgende.
            best, nxt = people[known[0]][1], people[known[1]][1]
            if best < nxt * 0.8 and (lim is None or best < lim * 1.3):
                return known[0], True
            return (nearest, False) if continuous else None
        if len(known) == 1 and len(near) == 1 and lim is not None:
            # één persoon. Was hij even weg, dan moet het uiterlijk kloppen, anders pakt de camera wie er
            # toevallig in het gat loopt. Afwijking van Churchfront: ook zonder gat volgen we iemand die er
            # duidelijk anders uitziet hooguit 1,5 s op zijn plek (anders blijft de camera bij degene die
            # voor de gevolgde persoon langs liep).
            if people[known[0]][1] <= lim:
                return nearest, True
            return (nearest, False) if continuous and unsure < 1.5 else None
        return nearest, False       # niets om ze mee uit elkaar te houden: de dichtstbijzijnde


# ---------------------------------------------------------------- volgkader
DEFAULT_FRAME = {"l": 0.3, "r": 0.7, "x": 0.5}


def norm_frame(f):
    """Volgkader {l, r, x} als deel van de beeldbreedte: staat de persoon stil, dan mag hij tussen l en r
    staan zonder dat de camera beweegt; komt hij erbuiten of loopt hij, dan houdt de camera hem op x.
    Ongeldig → standaard."""
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


# ---------------------------------------------------------------- afstelling van de pan-regelaar (per camera)
clock = time.monotonic   # alle tijden in deze module: loopt altijd vooruit, ook als Windows de klok gelijkzet


def _envf(name, default):
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return float(default)


# grenzen per instelling: (min, max)
TUNE_RANGE = {"speed": (0.5, 2.0), "lead": (0.1, 1.5), "walk": (0.05, 0.4), "fps": (1.0, 25.0)}
DEFAULT_TUNE = {"speed": 1.0, "lead": 0.45, "walk": 0.12, "fps": 8.0}   # + "curve" (zie norm_curve)
for _k, _e in (("speed", "PTZ_TRACK_SPEED"), ("lead", "PTZ_TRACK_LEAD"), ("walk", "PTZ_TRACK_WALK"),
               ("fps", "PTZ_TRACK_FPS")):
    _lo, _hi = TUNE_RANGE[_k]
    DEFAULT_TUNE[_k] = max(_lo, min(_hi, _envf(_e, DEFAULT_TUNE[_k])))


# 🧑 curve van het terughalen: afstand tot de doelpositie (0 … CURVE_SPAN beeldbreedte) → correctie (deel van de
# rechte lijn kp·afstand bij CURVE_SPAN). Drie ankerpunten (x oplopend, y niet terug) plus (0,0) en (1,1).
# 'standaard' = recht evenredig (precies de regelaar van vóór de curve), 'rustig' = dichtbij het doel zachter
# bijsturen, 'krachtig' = eerder en harder terughalen, 'eigen' = punten van de gebruiker.
CURVE_SPAN = 0.5
CURVE_PRESETS = {"standaard": [[0.25, 0.25], [0.5, 0.5], [0.75, 0.75]],
                 "rustig": [[0.25, 0.08], [0.5, 0.3], [0.75, 0.6]],
                 "krachtig": [[0.25, 0.45], [0.5, 0.72], [0.75, 0.9]]}
DEFAULT_CURVE = {"mode": "standaard", "pts": [p[:] for p in CURVE_PRESETS["standaard"]]}


def norm_curve(v, base=None):
    """Curve {mode, pts}: een voorkeuze geeft altijd zijn eigen punten; 'eigen' = drie punten, x oplopend
    (0,02–0,98, minstens 0,05 uit elkaar), y 0–1 en niet teruglopend. Ongeldig → `base` (of standaard)."""
    out = {"mode": base["mode"], "pts": [p[:] for p in base["pts"]]} if isinstance(base, dict) and "pts" in base \
        else {"mode": DEFAULT_CURVE["mode"], "pts": [p[:] for p in DEFAULT_CURVE["pts"]]}
    if not isinstance(v, dict):
        return out
    mode = v.get("mode")
    if mode in CURVE_PRESETS:
        return {"mode": mode, "pts": [p[:] for p in CURVE_PRESETS[mode]]}
    if mode != "eigen":
        return out
    try:
        pts = [[float(p[0]), float(p[1])] for p in v["pts"]]
    except (KeyError, TypeError, ValueError, IndexError):
        return out
    if len(pts) != 3 or any(a != a or b != b for a, b in pts):
        return out
    lo_x, lo_y = 0.0, 0.0
    for i, p in enumerate(pts):
        p[0] = max(lo_x + (0.02 if i == 0 else 0.05), min(0.98 - 0.05 * (2 - i), p[0]))
        p[1] = max(lo_y, min(1.0, p[1]))
        lo_x, lo_y = p[0], p[1]
        p[0], p[1] = round(p[0], 3), round(p[1], 3)
    return {"mode": "eigen", "pts": pts}


def curve_at(curve, v):
    """Curve op v = afstand ÷ CURVE_SPAN (≥ 0) → 0 … 1; voorbij 1 recht door (zelfde helling als de lijn ernaartoe
    vanaf de oorsprong), zodat een persoon helemaal aan de rand nog steeds harder teruggehaald wordt."""
    if v >= 1:
        return v
    P = [[0.0, 0.0]] + curve["pts"] + [[1.0, 1.0]]
    for (x0, y0), (x1, y1) in zip(P, P[1:]):
        if v <= x1:
            return y0 + (y1 - y0) * (v - x0) / max(1e-6, x1 - x0)
    return 1.0


def norm_tune(v, base=None):
    """Afstelling {speed, lead, walk, fps, curve}: volgsnelheid (×), vertraging van het beeld (s), loopdrempel
    (beeldbreedte/s: daarboven telt het als lopen), detecties per seconde, curve van het terughalen (norm_curve). Ontbrekend/ongeldig → waarde uit
    `base` (al genormaliseerd), anders de standaard (of omgeving). Onbekende sleutels (de afstelling van de
    regelaar van vóór v1.0.40: scale0, settle) vallen weg."""
    out = dict(base) if base else dict(DEFAULT_TUNE)
    if isinstance(v, dict):
        for k, (lo, hi) in TUNE_RANGE.items():
            try:
                x = float(v[k])
            except (KeyError, TypeError, ValueError):
                continue
            if x == x:   # geen NaN
                out[k] = round(max(lo, min(hi, x)), 2)
    out["curve"] = norm_curve(v.get("curve") if isinstance(v, dict) else None, out.get("curve"))
    return out


def norm_limits(v):
    """Pan-grenzen {left, right} als ruwe VISCA-panwaarden van de camera (beide nodig), anders None."""
    try:
        left, right = int(v["left"]), int(v["right"])
    except (TypeError, KeyError, ValueError):
        return None
    return {"left": left, "right": right} if left != right else None


# ---------------------------------------------------------------- beeldhoek en eenheden van de camera
# ⚠️ Aannames (zoals `VISCAUnits.ptzOptics` in Churchfront Video; het cameramodel is niet bekend):
WIDE_FOV = max(5.0, _envf("PTZ_TRACK_FOV", 58.0))          # horizontale beeldhoek helemaal uitgezoomd (graden)
ZOOM_RATIO = max(1.01, _envf("PTZ_TRACK_ZOOMX", 20.0))      # optische zoom (20x); de beeldhoek loopt gelijkmatig terug
COUNTS_PER_DEG = max(0.1, _envf("PTZ_TRACK_CPD", 14.4))    # VISCA-pantellen per graad (0x0990 tellen = 170 graden)
DEG_PER_STEP = max(0.1, _envf("PTZ_TRACK_DEGSTEP", 4.0))   # graden/s per pan-snelheidsstap, als beginwaarde: wordt gemeten


def fov_of(zoom, zoom_max=16384):
    """Horizontale beeldhoek (graden) bij een VISCA-zoomwaarde. Schatting voor een 20x-lens; op een 12x valt hij
    te smal uit, dat maakt het volgen rustiger, niet schokkeriger."""
    try:
        z = max(0.0, min(1.0, float(zoom) / float(zoom_max or 16384)))
    except (TypeError, ValueError):
        return WIDE_FOV
    return WIDE_FOV * (1.0 / ZOOM_RATIO) ** z


class CameraPoller(threading.Thread):
    """Leest tijdens het volgen een paar keer per seconde de panpositie (en elke seconde de zoom) via VISCA.
    Daaruit komt hoe snel de camera echt draait en hoe breed het beeld is (voor de regelaar), en hij stopt de
    camera vóór een pan-grens. read_pan() -> int (gooit een fout als het niet lukt); read_zoom() -> int.
    Welke kant 'rechts' is bij de grenzen volgt uit de twee ingestelde waarden (rechts > links of andersom),
    dus het maakt niet uit hoe de camera telt of of hij ondersteboven hangt."""
    POLL = 0.15
    REACT = 0.35     # s: zo ver vooruit kijken bij een grens (reactietijd + uitlopen)
    ZOOM_EVERY = 1.0
    KEEP = 40        # metingen bewaren (6 s: genoeg om de beeldvertraging aan te meten)

    def __init__(self, read_pan, limits, on_block, read_zoom=None):
        super().__init__(daemon=True)
        self.read_pan = read_pan
        self.read_zoom = read_zoom
        self.limits = limits
        self.on_block = on_block      # on_block(direction): direct stoppen als we die kant op draaien
        self.running = True
        self.pan = None
        self.zoom = None
        self.vel = 0.0
        self.polls = []               # [(tijd, pan)]
        self.error = ""
        self.zoom_error = ""          # zoom niet te lezen: de beeldhoek is dan onbekend
        self.lock = threading.Lock()

    def set_limits(self, limits):
        with self.lock:
            self.limits = limits

    def recent(self):
        """Laatste metingen [(tijd, pan)] en de zoom (None = onbekend)."""
        with self.lock:
            return list(self.polls), self.zoom

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
        last_t, last_p, fails, zoom_t = None, None, 0, 0.0
        while self.running:
            try:
                p = self.read_pan()
                t = clock()
                with self.lock:
                    if last_p is not None and t > last_t:
                        v = (p - last_p) / (t - last_t)
                        self.vel = 0.5 * v + 0.5 * self.vel
                    self.pan = p
                    self.polls.append((t, p))
                    del self.polls[:-self.KEEP]
                last_t, last_p, fails, self.error = t, p, 0, ""
                for d in ("left", "right"):
                    if self.blocked(d):
                        self.on_block(d)
                if self.read_zoom and t - zoom_t >= self.ZOOM_EVERY:
                    zoom_t = t
                    try:
                        z = self.read_zoom()
                        with self.lock:
                            self.zoom = z
                        self.zoom_error = ""
                    except Exception as e:  # noqa: BLE001
                        self.zoom_error = f"zoom niet te lezen ({e})"   # de regelaar houdt de laatste beeldhoek aan
            except Exception as e:  # noqa: BLE001
                fails += 1
                with self.lock:
                    if fails >= 3:
                        self.pan = None
                        self.polls = []
                self.error = f"panpositie niet te lezen ({e})"
                time.sleep(min(10.0, 0.3 * fails))   # geen VISCA: niet blijven hameren
            time.sleep(self.POLL)
        close = getattr(self.read_pan, "close", None)
        if close:
            close()

    def stop(self):
        self.running = False


# ---------------------------------------------------------------- pan-regelaar
GAINS = {}   # cam_id -> gemeten (echte snelheid ÷ gevraagde snelheid), onthouden tot de app stopt


class PanFollower:
    """Houdt de persoon op de doelpositie door de pan-SNELHEID te regelen: zijn eigen loopsnelheid plus een
    correctie richting de doelpositie. Overgezet van `AutoTracker.steer`/`poll` in Churchfront Video
    (Tracker.swift), alleen de pan-as: tilt en zoom doet deze app nooit.

    - Rekent in echte hoeken: de beeldhoek komt uit de zoom, de echte draaisnelheid uit de panpositie
      (`observe`). Zo weet hij hoe snel de PERSOON loopt (zijn verschuiving in het beeld plus hoe snel de
      camera draaide toen dat beeld gemaakt werd) en hoe snel de camera echt draait per snelheidsstap (`gain`).
    - Alleen duidelijk, gelijkmatig bewegen gedurende een halve seconde telt als lopen; kleiner is gewicht
      verplaatsen of meetruis, en dan houdt de camera gewoon het kader vast.
    - Dode zone = het volgkader: staat hij stil binnen het kader, dan beweegt de camera niet. Begint pas
      als hij eruit komt, loopt of de beeldrand nadert; komt tot rust als hij stilstaat op de doelpositie.
    - Optrekken is begrensd (rustig begin), afremmen gaat sneller; bij de beeldrand harder.
    - Geen positie via VISCA (`sign` blijft 0)? Dan schat hij de camerasnelheid uit wat hij zelf stuurde en
      meet de echte snelheid aan het beeld (hoeveel de persoon verschuift door een beweging).

    Alles in 'commando-ruimte': positief = commando rechts. Tijd komt van de aanroeper (los te testen)."""

    CORRECTION = 1.2   # trek naar de doelpositie (per seconde), × volgsnelheid
    REACT = 0.15       # s: zo laat reageert de camera op een commando
    STOP_AT = 0.03     # dichter dan dit bij de doelpositie = erop
    MIN_DEAD = 0.07    # kleinste dode zone (deel van het beeld): zo ver mag hij altijd wiegen zonder dat de camera jaagt
    CAREFUL = 2        # hoogste snelheidsstap zolang de echte snelheid nog niet gemeten is

    def __init__(self, max_speed=6, tune=None, gain=None):
        self.max_speed = max(1, int(max_speed))
        self.gain = float(gain) if gain else 1.0   # echte snelheid ÷ gevraagde (DEG_PER_STEP per stap)
        self.gain_known = bool(gain)
        self.sign = 0          # telt de camera op (+1) of af (−1) bij commando rechts? 0 = (nog) onbekend
        self._votes = 0
        self.fov = WIDE_FOV
        self.fov_known = False # is de zoom (dus de beeldhoek) ooit gelezen? Zo niet: positie niet bruikbaar
        self.sent = 0          # laatst gegeven snelheidsstap, met teken
        self.sent_log = [(0.0, 0)]
        self.rate = 0.0        # gevraagde camerasnelheid (beeldbreedte/s), vóór afronden op een stap
        self.panning = False
        self.eye = None        # gladgestreken plek van de persoon
        self.last_x = None     # (x, tijd) van het vorige beeld
        self.img_v = 0.0       # zijn snelheid in het beeld (beeldbreedte/s)
        self.move = 0.0        # zijn echte snelheid (beeldbreedte/s, rechts positief)
        self.walk_dir, self.walk_since = 0, 0.0
        self.last_pos = None   # laatst verwerkte positiemeting
        self._stuck = None     # sinds wanneer hij wil draaien maar onder de kleinste stap blijft
        self._lead = None
        self.obs = []          # (tijd, x) van de laatste 2,5 s, voor het meten van de beeldvertraging
        self._lat_at = 0.0
        self.dbg = {}
        self.set_tune(tune)

    def set_tune(self, tune):
        t = norm_tune(tune)
        self.k, self.walk_min, self.curve = t["speed"], t["walk"], t["curve"]
        if t["lead"] != self._lead:      # alleen als de beginwaarde zelf verandert: anders blijft de meting staan
            self._lead = self.lat = t["lead"]   # zoveel loopt het beeld achter (s); wordt daarna gemeten

    def uses_position(self):
        """Rekent de regelaar met de gemeten camerapositie? Alleen als de telrichting bekend is én de zoom
        gelezen is: zonder beeldhoek zeggen pantellen niets over het beeld."""
        return bool(self.sign) and self.fov_known

    # --- wat de camera echt doet
    def _asked(self, t0, t1):
        """Hoeveel stap·seconden er tussen t0 en t1 gevraagd is (met teken)."""
        total, log = 0.0, self.sent_log
        for i, (at, step) in enumerate(log):
            start = max(t0, at)
            end = min(t1, log[i + 1][0] if i + 1 < len(log) else t1)
            if end > start:
                total += step * (end - start)
        return total

    def observe(self, polls, zoom, zoom_max=16384):
        """Nieuwe positiemetingen [(tijd, pan)]: beeldhoek, telrichting en echte draaisnelheid leren."""
        if zoom is not None:
            self.fov, self.fov_known = fov_of(zoom, zoom_max), True
        if not self.fov_known:
            return          # geen beeldhoek: volgen op schatting (alles wordt dan aan het beeld gemeten)
        for t, pan in polls:
            last = self.last_pos
            if last is None or t - last[0] > 1.5:
                self.last_pos = (t, pan)
                continue
            dt = t - last[0]
            if dt <= 0.28:                # over minstens twee metingen: het precieze moment telt dan minder
                continue
            # graden die gevraagd zijn in die tijd (de camera reageert ~0,15 s later), tegen wat hij echt
            # draaide. Alleen leren van duidelijke bewegingen één kant op.
            asked = self._asked(last[0] - self.REACT, t - self.REACT) * DEG_PER_STEP
            moved = (pan - last[1]) / COUNTS_PER_DEG
            self.last_pos = (t, pan)
            if abs(asked) <= 0.3 or abs(moved) <= 0.1:
                continue
            self._votes = max(-4, min(4, self._votes + (1 if moved * asked > 0 else -1)))
            if abs(self._votes) >= 2:
                self.sign = 1 if self._votes > 0 else -1
            if self.sign and moved * asked * self.sign > 0:
                seen = abs(moved) / abs(asked)
                self.gain += (max(0.1, min(3.0, seen)) - self.gain) * 0.25
                self.gain_known = True

    def _cam_rate(self, now, polls):
        """Hoe snel de camera draaide (beeldbreedte/s) toen het beeld van nu gemaakt werd."""
        ta = now - self.lat + 0.05
        if self.uses_position() and polls:
            before = [p for p in polls if p[0] <= ta]
            if len(before) >= 2 and ta - before[-1][0] < 0.6:
                # over de laatste drie metingen, zodat starten en stoppen van de camera zelf er niet uitzien
                # als lopen van de persoon
                a, b = before[max(0, len(before) - 3)], before[-1]
                dt = b[0] - a[0]
                if 0.05 < dt < 1:
                    return self.sign * (b[1] - a[1]) / dt / (COUNTS_PER_DEG * self.fov)
        step = 0
        for at, s in self.sent_log:       # geen meting: wat we zelf vroegen
            if at <= ta - self.REACT:
                step = s
        return step * DEG_PER_STEP * self.gain / self.fov

    def _cam_shift(self, now, polls):
        """Hoeveel de camera gedraaid is (beeldbreedtes) sinds het beeld van nu gemaakt werd. Het beeld loopt
        achter: zonder dit stuurt de regelaar op waar de persoon WAS en schiet hij voorbij (slingeren)."""
        ta = now - self.lat
        per_step = DEG_PER_STEP * self.gain / self.fov
        if self.uses_position() and polls and now - polls[-1][0] < 0.5 and polls[0][0] <= ta:
            pan_then = polls[0][1]
            for (t0, p0), (t1, p1) in zip(polls, polls[1:]):
                if t0 <= ta <= t1:
                    pan_then = p0 + (p1 - p0) * ((ta - t0) / (t1 - t0) if t1 > t0 else 0)
                    break
            else:
                pan_then = polls[-1][1]
            turned = self.sign * (polls[-1][1] - pan_then) / (COUNTS_PER_DEG * self.fov)
            # na de laatste meting: wat we zelf vroegen
            return turned + self._asked(polls[-1][0] - self.REACT, now - self.REACT) * per_step
        return self._asked(ta - self.REACT, now - self.REACT) * per_step

    def _measure(self, now, x, polls):
        """Eens per seconde, over de laatste 2,5 s: hoeveel loopt het beeld achter, en (zonder camerapositie) hoe
        snel draait de camera echt? De plek van de persoon in het beeld plus de stand van de camera op het moment
        dat het beeld gemaakt werd is zijn echte plek, en die verloopt vloeiend. Bij de juiste vertraging (en
        draaisnelheid) valt het draaien van de camera precies weg; anders blijft elke start en stop van de camera
        als knik zichtbaar. Alleen als de camera in die tijd duidelijk van snelheid veranderde en één uitkomst
        duidelijk het best past.
        - Met positie: de camerastand is gemeten, alleen de vertraging wordt gezocht.
        - Zonder positie: de camerastand is wat we zelf vroegen (in stappen); de draaisnelheid per stap (`gain`,
          dan met de onbekende zoom erin) wordt per vertraging uitgerekend als de waarde die het best past."""
        self.obs.append((now, x))
        while self.obs and now - self.obs[0][0] > 2.5:
            self.obs.pop(0)
        if now - self._lat_at < 1.0 or len(self.obs) < 10 or now - self.obs[0][0] < 1.8:
            return
        self._lat_at = now
        t0 = self.obs[0][0] - 1.5
        measured = self.uses_position() and polls and polls[0][0] <= t0
        if measured:
            k = self.sign / (COUNTS_PER_DEG * self.fov)

            def pos(t):
                for (ta, pa), (tb, pb) in zip(polls, polls[1:]):
                    if ta <= t <= tb:
                        return k * (pa + (pb - pa) * ((t - ta) / (tb - ta) if tb > ta else 0))
                return k * (polls[-1][1] if t > polls[-1][0] else polls[0][1])
        elif self.uses_position() or self.sent_log[0][0] > t0 - self.REACT:
            return          # nog te weinig metingen of geschiedenis
        else:
            unit = DEG_PER_STEP / self.fov           # beeldbreedte per stap·seconde, zonder gain

            def pos(t):
                return self._asked(t0 - self.REACT, t - self.REACT) * unit

        ts = [t - self.obs[0][0] for t, _ in self.obs]
        n, mt = len(ts), sum(ts) / len(ts)
        var = sum((t - mt) ** 2 for t in ts)

        def rest(vals):       # wat overblijft na een rechte lijn (constante snelheid)
            mv = sum(vals) / n
            b = sum((t - mt) * (v - mv) for t, v in zip(ts, vals)) / var
            return [v - mv - b * (t - mt) for t, v in zip(ts, vals)]

        xr = rest([xx for _, xx in self.obs])
        cands = [0.05 * i for i in range(31)]          # 0 … 1,5 s
        res, gains = [], []
        for c in cands:
            cr = rest([pos(t - c) for t, _ in self.obs])
            cc = sum(v * v for v in cr) / n
            if measured:
                g = 1.0
            else:
                g = -sum(a * b for a, b in zip(xr, cr)) / (cc * n) if cc > 1e-9 else 0.0
            gains.append(g)
            res.append(sum((a + g * b) ** 2 for a, b in zip(xr, cr)) / n if cc >= 0.0004 * (1 if measured else 0.05)
                       else float("inf"))
        low = min(res)
        if low == float("inf"):
            return          # camera stond stil of draaide gelijkmatig: niets te meten
        # de KLEINSTE vertraging die (bijna) het best past: bij heen en weer draaien past een hele slag later
        # even goed, en een te hoog geschatte vertraging laat de regelaar zelf slingeren
        best = next(i for i, r in enumerate(res) if r <= low * 1.25 + 1e-9)
        if best >= len(res) - 1:
            return
        # tegen de huidige schatting afgezet: omhoog alleen bij duidelijk bewijs (een te hoge vertraging laat de
        # regelaar zelf heen en weer draaien, en dat heen en weer draaien lijkt dan weer op een hoge vertraging)
        ci = min(range(len(cands)), key=lambda i: abs(cands[i] - self.lat))
        cur = res[ci]
        lat_ok = best == ci or cur == float("inf") or res[best] <= cur * (0.4 if cands[best] > self.lat else 0.85)
        if not lat_ok:
            if measured or cur == float("inf"):
                return
            best = ci       # de vertraging blijft staan; de draaisnelheid is bij de huidige schatting wel te meten
        finite = sorted(r for r in res if r != float("inf"))
        if res[best] >= 0.5 * finite[len(finite) // 2]:
            return          # geen duidelijke winnaar
        if measured:
            self._lat_to(cands[best])
            return
        g = gains[best]
        moved = sum(v * v for v in xr) / n
        if not 0.05 <= g <= 25.0 or moved < 0.0001 or res[best] > 0.3 * moved:
            return          # het draaien van de camera verklaart het beeld niet (de persoon bewoog zelf)
        self.gain += (g - self.gain) * (0.5 if not self.gain_known else 0.3)
        self.gain_known = True
        if lat_ok:
            self._lat_to(cands[best])

    def _lat_to(self, seen):
        """Omlaag snel, omhoog voorzichtig: te laag geschat geeft wat achterlopen, te hoog geschat slingeren."""
        self.lat += (seen - self.lat) * (0.5 if seen < self.lat else 0.2)

    # --- sturen
    @staticmethod
    def _blend(per_frame, dt):
        """Gladstrijken per beeld bij Churchfront (~15 beelden/s) omgerekend naar de tijd tussen onze beelden,
        zodat het bij 8 detecties per seconde even snel reageert."""
        return 1.0 - (1.0 - per_frame) ** (dt / 0.07)

    def hold(self, now):
        """Stilstand (persoon kwijt, geen beeld, grens, van buitenaf). Geeft True als de camera draaide."""
        was = self.sent != 0
        self._set(now, 0)
        self.rate, self.panning = 0.0, False
        self.walk_dir = 0
        return was

    def forget(self):
        """Nieuwe persoon of een gat in het beeld: niets van de vorige beweging meenemen."""
        self.eye, self.last_x = None, None
        self.img_v = self.move = 0.0
        self.walk_dir = 0
        self.obs = []

    def _set(self, now, step):
        if step != self.sent:
            self.sent = step
            self.sent_log.append((now, step))
            del self.sent_log[:-40]

    def steer(self, now, x, x1, x2, frame, polls=None):
        """x = midden van de persoon in dit beeld, x1/x2 = linker- en rechterrand van zijn box, frame = volgkader
        {l, r, x}; alles als deel van de beeldbreedte. Geeft de snelheidsstap met teken (0 = stilstaan)."""
        l, r, tx = frame["l"], frame["r"], frame["x"]
        if self.last_x is None or now - self.last_x[1] > 0.6:
            self.eye, self.img_v, self.move = x, 0.0, 0.0
            dt_frame = 0.12
        else:
            dt = now - self.last_x[1]
            dt_frame = min(0.3, max(0.03, dt))
            self.eye += (x - self.eye) * self._blend(0.5, dt_frame)
            if dt > 0.02:
                self.img_v += ((x - self.last_x[0]) / dt - self.img_v) * self._blend(0.35, dt_frame)
        self.last_x = (x, now)
        self._measure(now, x, polls)

        # hoe snel hij echt loopt: zijn verschuiving in het beeld plus hoe snel de camera toen draaide
        cam = self._cam_rate(now, polls)
        self.move += ((self.img_v + cam) - self.move) * self._blend(0.2, dt_frame)
        d = 1 if self.move > self.walk_min else -1 if self.move < -self.walk_min else 0
        if d != self.walk_dir:
            self.walk_dir, self.walk_since = d, now
        walk = self.move if d != 0 and now - self.walk_since > 0.5 else 0.0

        # waar hij NU staat: waar het (achterlopende) beeld hem toont, min wat de camera sindsdien draaide,
        # plus wat hij zelf liep
        own = self.move * self.lat if (self.panning or walk != 0) else 0.0   # stilstaan en wiegen telt niet
        shift = max(-0.5, min(0.5, own - self._cam_shift(now, polls)))
        here = self.eye + shift
        x1, x2 = x1 + shift, x2 + shift
        ex = here - tx                     # rechts van de doelpositie: naar rechts draaien

        # randalarm: hoe dicht hij bij de beeldrand is waar hij heen gaat, 0 (ruim) tot 1 (raakt hem)
        w = x2 - x1
        gap = ((1 - x2) if ex > 0 else x1) if w < 0.5 else ((1 - here) if ex > 0 else here) - 0.1
        urgency = min(1.0, max(0.0, (0.2 - gap) / 0.2))

        # dode zone = het kader: pas bewegen als hij er (met zijn hele box) uit komt, loopt of de rand nadert
        if w >= (r - l) * 0.9:             # breder dan het kader (ver ingezoomd): op zijn midden letten
            dead = max(self.MIN_DEAD, (r - l) * 0.25)
            outside = abs(ex) > dead
        else:
            e1, e2 = here - w / 2, here + w / 2
            dead = max(self.MIN_DEAD, (tx - l - w / 2) if ex < 0 else (r - tx - w / 2))
            outside = (e1 < l or e2 > r) and abs(ex) > self.MIN_DEAD
        if outside or walk != 0 or urgency > 0:
            self.panning = True
        elif abs(ex) < max(self.STOP_AT, dead * 0.5) and walk == 0:
            self.panning = False

        # gevraagde camerasnelheid (beeldbreedte/s): zijn eigen snelheid plus een zachte trek naar de
        # doelpositie (harder bij de rand). Optrekken begrensd, zodat een beweging rustig begint.
        k = self.k
        per_step = DEG_PER_STEP * max(0.05, self.gain) / self.fov      # beeldbreedte/s per snelheidsstap
        top = self.max_speed if self.gain_known else min(self.max_speed, self.CAREFUL)
        if self.panning:
            kp = min(2.5, min(1.6, self.CORRECTION * k) * (1 + 1.5 * urgency))
            # trek naar de doelpositie volgens de curve (standaard = recht evenredig: kp · ex)
            pull = kp * CURVE_SPAN * curve_at(self.curve, abs(ex) / CURVE_SPAN)
            want = walk + (pull if ex > 0 else -pull)
            accel = (16.0 if urgency > 0.3 else 8.0 * max(1.0, k)) * dt_frame
            decel = 16.0 * max(1.0, k) * dt_frame
            prev = self.rate
            lim = decel if abs(want) < abs(prev) or want * prev < 0 else accel
            self.rate = prev + max(-lim, min(lim, want - prev))
            self.rate = max(-top * per_step, min(top * per_step, self.rate))
        else:
            want = self.rate = 0.0
        raw = self.rate / per_step
        a = abs(raw)
        keep = self.sent != 0 and (self.sent > 0) == (raw > 0)     # loopt al die kant op: niet meteen stoppen
        if a < (0.25 if keep else 0.5):
            n = 0
        elif keep and abs(a - abs(self.sent)) < 0.7:
            n = min(top, abs(self.sent))   # niet bij elk beeld een stapje op en neer: scheelt commando's
        else:
            n = max(1, min(top, int(round(a))))
        step = n if raw > 0 else -n
        if self.panning and n == 0 and outside and not self.uses_position():
            # wil draaien maar blijft onder de kleinste stap: de draaisnelheid is te hoog geschat
            if self._stuck is None:
                self._stuck = now
            elif now - self._stuck > 1.5:
                self.gain, self._stuck = max(0.1, self.gain * 0.8), now
        else:
            self._stuck = None
        self._set(now, step)
        self.dbg = {"x": here, "err": ex, "img_v": self.img_v, "move": self.move, "cam": cam, "want": want,
                    "fov": self.fov, "gain": self.gain, "lat": self.lat, "step": step,
                    "pos": self.uses_position(), "urgency": urgency,
                    "panning": int(self.panning), "x1": x1, "x2": x2}
        return step


class TrackLog:
    """Meetlog van het volgen, één regel per bekeken beeld, om af te stellen: wat de tracker zag en deed.
    Zoals `TrackLog` in Churchfront Video. Puntkomma's en decimale komma's (met BOM), zodat het bestand in een
    Nederlandstalige Excel direct in kolommen opent. Gaat door na een herstart van de app; na MAX_LINES regels
    (ruim 2,5 uur volgen met twee camera's) schuift het bestand door naar `<naam>.1.csv` en begint een nieuw."""
    COLS = ("tijd;klok;camera;x;afwijking;beeld_v;persoon_v;camera_v;gewenst_v;beeldhoek;gain;vertraging;stap;rand;"
            "draait;box_links;box_rechts;positie")
    HEAD = "\ufeff" + COLS + "\n"
    MAX_LINES = 150000
    RETRY = 30.0         # s wachten na een schrijffout (bestand open in Excel op Windows) voor we het opnieuw proberen

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.f = None
        self.lines = 0
        self.failed_at = None

    @staticmethod
    def _num(v, digits):
        return ("%.*f" % (digits, v)).replace(".", ",")

    def _open(self):
        """Bestaand log van deze opzet voortzetten; een ander (ouder) formaat of een vol bestand schuift door."""
        lines, keep = 0, False
        try:
            with open(self.path, "r", encoding="utf-8-sig") as f:
                keep = f.readline().strip() == self.COLS
                lines = sum(1 for _ in f) if keep else 0
        except OSError:
            pass
        try:
            empty = os.path.getsize(self.path) == 0
        except OSError:
            empty = True
        if not empty and (not keep or lines >= self.MAX_LINES):
            old = os.path.splitext(self.path)[0] + ".1.csv"
            try:
                os.replace(self.path, old)
                keep, lines = False, 0
            except OSError:
                # vorige log staat open (Excel): niets weggooien, in het huidige bestand doorschrijven
                if not keep:
                    raise
                lines = 0         # pas na nog een vol bestand opnieuw proberen
        elif empty:
            keep, lines = False, 0
        self.f = open(self.path, "a" if keep else "w", encoding="utf-8", newline="")
        if not keep:
            self.f.write(self.HEAD)
        self.lines = lines

    def write(self, cam, d):
        if not self.path or not d:
            return
        n = self._num
        row = ";".join([n(time.time(), 3), time.strftime("%H:%M:%S"), str(cam).replace(";", " "), n(d["x"], 3),
                        n(d["err"], 3), n(d["img_v"], 3), n(d["move"], 3), n(d["cam"], 3), n(d["want"], 3),
                        n(d["fov"], 1), n(d["gain"], 2), n(d["lat"], 2), str(int(d["step"])), n(d["urgency"], 2),
                        str(int(d["panning"])), n(d["x1"], 3), n(d["x2"], 3), str(int(d["pos"]))]) + "\n"
        with self.lock:
            if self.failed_at is not None and clock() - self.failed_at < self.RETRY:
                return
            try:
                if self.f is None or self.lines >= self.MAX_LINES:
                    if self.f:
                        self.f.close()
                        self.f = None
                    self._open()
                self.f.write(row)
                self.f.flush()
                self.lines += 1
                self.failed_at = None
            except OSError:
                # niet schrijfbaar (bv. open in Excel): het volgen gaat gewoon door, straks opnieuw proberen
                try:
                    if self.f:
                        self.f.close()
                except OSError:
                    pass
                self.f, self.failed_at = None, clock()


_LOGS = {}


def track_log(path):
    """Eén meetlog per bestand (alle camera's schrijven in hetzelfde)."""
    if not path:
        return None
    if path not in _LOGS:
        _LOGS[path] = TrackLog(path)
    return _LOGS[path]


# ---------------------------------------------------------------- tracker per camera
class CameraTracker(threading.Thread):
    """Volgt één persoon met één camera: beeld → personen → de juiste vasthouden (`Identity`) → pan sturen
    (`PanFollower`). Elke handbediening, preset of home op die camera stopt het volgen (dat doet de server)."""
    STALE = 0.6          # s zonder nieuw beeld -> pan stoppen (nooit doordraaien op oude beelden)
    LOST_STOP = 0.6      # s zonder doel -> pan stoppen
    LOST_AUTO = 6.0      # s zonder (automatisch gekozen) doel -> nieuw doel mogen kiezen; een aangeklikt doel blijft
    MIN_HEIGHT = 0.10    # kleinere personen (ver weg in een totaalshot) negeren
    PICK_NEAR = 0.55     # aangeklikt: zo verschillend mag hij er hooguit uitzien als hij nog op die plek staat
    PICK_ANY = 0.38      # … en zo verschillend als hij ergens anders in het beeld staat
    PICK_WAIT = 20.0     # s zoeken naar een aangeklikte persoon voor we het opgeven
    GALLERY = 6          # beelden van het uiterlijk van de gevolgde persoon
    # max. detecties per seconde (per camera): standaard 8, genoeg om een spreker te volgen en scheelt CPU/GPU
    # t.o.v. elk beeld (25/s); instelbaar per camera (tune.fps) of met PTZ_TRACK_FPS
    MAX_FPS = DEFAULT_TUNE["fps"]

    def __init__(self, cam, url, detector, send, on_status, settings):
        super().__init__(daemon=True)
        self.cam = cam
        self.cam_id = (cam or {}).get("id")
        self.detector = detector
        self.send = send                # send(action, args) -> camera
        self.on_status = on_status      # on_status(dict)
        self.grabber = FrameGrabber(url)
        tune = norm_tune(settings.get("tune"))
        self.invert = bool(settings.get("invert", False))
        self.zoom_max = settings.get("zoom_max") or 16384
        self.ctrl = PanFollower(max_speed=settings.get("max_speed", 6), tune=tune, gain=GAINS.get(self.cam_id))
        self.MAX_FPS = tune["fps"]
        self.frame_cfg = norm_frame(settings.get("frame") or DEFAULT_FRAME)
        self.log = track_log(settings.get("log"))
        self.lock = threading.Lock()
        self.ctrl_lock = threading.RLock()   # de regelaar wordt ook vanuit de CameraPoller-thread stilgezet (grens)
        self.running = True
        self.halt_on_exit = True
        self.retry_at = 0.0       # een mislukt commando niet vaker dan eens per seconde opnieuw sturen
        self.target = None        # x van het doel in het laatste beeld waarin hij gezien is
        self.box = None           # laatste box van het doel (cx, cy, w, h)
        self.gallery = []         # hoe hij eruitziet: de eerste `anchors` blijven, de rest zijn de nieuwste
        self.anchors = 1
        self.gallery_at = 0.0
        self.own = None           # hoeveel zijn eigen uiterlijk van beeld tot beeld verschilt
        self.others = None        # hoe verschillend de mensen om hem heen eruitzien
        self.alone_since = None   # sinds wanneer hij de enige in de buurt is (dan is leren veilig)
        self.sure_seen = 0.0      # wanneer zijn uiterlijk het voor het laatst bevestigde
        self.picked = False       # doel is aangeklikt (blijft, ook als hij even weg is)
        self.pending = None       # (cx, hist, sinds) aangeklikte persoon die we nog moeten vinden
        self.last_seen = 0.0
        self.status = ""
        self.problem = False      # staat de huidige status voor een fout (rood in de UI)?
        self.fps = 0.0
        self.sent = None          # laatst verzonden (richting, snelheid) of None = staat stil
        self.at_limit = None      # 'left'/'right' als we tegen een grens staan
        self.poller = None
        if settings.get("read_pan"):
            rp = settings["read_pan"]
            self.poller = CameraPoller(rp, norm_limits(settings.get("limits")), self._guard_block,
                                       read_zoom=getattr(rp, "zoom", None))
        pick = settings.get("pick")
        if pick:
            self.pick(*pick)

    # --- bediening vanuit de server
    def set_frame(self, frame):
        with self.lock:
            self.frame_cfg = norm_frame(frame)

    def set_tune(self, tune):
        tune = norm_tune(tune)
        with self.ctrl_lock:
            self.ctrl.set_tune(tune)
        with self.lock:
            self.MAX_FPS = tune["fps"]

    def set_limits(self, limits):
        if self.poller:
            self.poller.set_limits(norm_limits(limits))

    def pan(self):
        """Laatst gelezen panpositie van de camera, of None."""
        return self.poller.pan if self.poller else None

    def _guard_block(self, direction):
        """Vanuit de CameraPoller: draaien we die kant op, dan nu stoppen."""
        if self.running and self.sent and self.sent != "?" and self.sent[0] == direction:
            self.at_limit = direction
            with self.ctrl_lock:
                self.ctrl.hold(clock())
                self._cmd(0)

    def pick(self, cx, hist):
        """Volg deze persoon (aangeklikt in een beeld van zojuist)."""
        with self.lock:
            self.pending = (float(cx), hist, clock())
            self.picked = True
        self._status("zoekt gekozen persoon", force=True)

    def release(self):
        """Terug naar automatisch: de grootste persoon in beeld."""
        with self.lock:
            self.pending = None
            self.picked = False
            self._drop()
        self._status("zoekt persoon", force=True)

    def _drop(self):
        self.target, self.box = None, None
        self.gallery, self.own, self.others, self.alone_since = [], None, None, None

    def target_box(self):
        """Box van het huidige doel als het net gezien is, anders None."""
        return self.box if self.box and clock() - self.last_seen < 1.0 else None

    def _status(self, status, force=False, problem=False, detail=""):
        """problem=True → de UI toont dit als fout (rood), niet als gewone voortgang."""
        if self.sent == "?" and not problem:
            if not force:
                return            # besturing mislukt: de fout blijft staan tot een commando weer aankomt
            status, problem = self.status, self.problem
        if status != self.status or problem != self.problem or force:
            self.status, self.problem = status, problem
            c = self.ctrl
            # waarom de regelaar niet met de camerapositie rekent (dan volgt hij op schatting: minder strak)
            guess = ""
            if not self.poller:
                guess = "camerapositie lezen staat uit"
            elif self.poller.error:
                guess = "camerapositie niet te lezen via VISCA"
            elif not c.fov_known and self.poller.zoom_error:
                guess = "zoom van de camera niet te lezen via VISCA"
            self.on_status({"status": status, "problem": problem, "detail": detail,
                            "fps": round(self.fps, 1), "detector": self.detector.kind,
                            "note": getattr(self.detector, "note", ""), "picked": self.picked,
                            "frame": self.frame_cfg, "guess": guess,
                            "meas": {"lat": round(c.lat, 2), "gain": round(c.gain, 2), "fov": round(c.fov, 1),
                                     "pos": c.uses_position() and not guess, "known": c.gain_known,
                                     # afwijking van de doelpositie nu (beeldbreedte), voor de stip in de curve
                                     "err": round(float(c.dbg.get("err", 0.0)), 3),
                                     "panning": bool(c.dbg.get("panning"))}})

    def _cmd(self, step):
        """Snelheidsstap met teken (commando-ruimte: positief = rechts) naar de camera; alleen bij verandering."""
        direction = "stop" if not step else "right" if step > 0 else "left"
        new = None if direction == "stop" else (direction, abs(int(step)))
        if direction != "stop" and not self.running:
            return  # gestopt terwijl we detecteerden: geen nieuwe beweging meer sturen
        if direction != "stop" and self.poller and self.poller.blocked(direction):
            # grens: niet verder die kant op (de persoon mag dan uit het kader lopen)
            self.at_limit = direction
            with self.ctrl_lock:
                self.ctrl.hold(clock())
            new = None
        elif direction != "stop":
            self.at_limit = None
        if new == self.sent:
            return
        if self.sent == "?" and clock() < self.retry_at:
            return                # vorige commando mislukte: niet bij elk beeld opnieuw proberen
        self.sent = new
        try:
            if new is None:
                self.send("stop", {})
            else:
                self.send("move", {"dir": new[0], "pan": new[1], "tilt": 1})
        except Exception as e:  # noqa: BLE001
            # niet aangekomen: we weten niet wat de camera doet, dus het volgende commando (ook een stop)
            # gaat hoe dan ook opnieuw de deur uit
            self.sent, self.retry_at = "?", clock() + 1.0
            self._status(f"camera-fout: {e}", problem=True)

    def _learn(self, look, cands, i, sure, reach, gone, now):
        """Leert hoe de gevolgde persoon eruitziet, alleen van beelden waarin zeker is wie hij is: zijn
        uiterlijk bevestigde het, of hij is al een seconde de enige in de buurt. Twee mensen die elkaar
        kruisen leren hem dus nooit de verkeerde aan."""
        alone = sum(1 for c in cands if c[0] < reach * 1.5) == 1
        if not alone:
            self.alone_since = None
        elif self.alone_since is None:
            self.alone_since = now
        settled = gone < 0.7 and self.alone_since is not None and now - self.alone_since > 1
        if not (sure or settled):
            return
        self.sure_seen = now
        if look is None:
            return
        d = cands[i][1]
        if d is not None:
            self.own = d if self.own is None else self.own + (d - self.own) * 0.2
        if sure:
            for j, c in enumerate(cands):
                if j != i and c[0] < reach * 1.5 and c[1] is not None:
                    self.others = c[1] if self.others is None else self.others + (c[1] - self.others) * 0.2
        if now - self.gallery_at <= 0.7:
            return
        self.gallery_at = now
        if len(self.gallery) >= self.GALLERY and len(self.gallery) > self.anchors:
            del self.gallery[self.anchors]
        if len(self.gallery) < self.GALLERY:
            self.gallery.append(look)

    def _begin(self, p, look, now, anchor=None):
        """Nieuw doel: niets van het vorige meenemen."""
        self._drop()
        self.gallery = [g for g in (anchor, look) if g is not None]
        self.anchors = 1
        self.gallery_at = self.sure_seen = now
        self.ctrl.forget()
        return p

    def _pick(self, people, frame, now):
        people = [p for p in people if p[3] > self.MIN_HEIGHT]
        if not people:
            if self.target is not None and not self.picked and now - self.last_seen > self.LOST_AUTO:
                self._drop()
            return None
        looks = [appearance(frame, p) for p in people]
        with self.lock:
            pending, fc = self.pending, self.frame_cfg
        if pending:
            cx, hist, since = pending
            # beeld van het aanklikken is van zojuist: plek telt mee, uiterlijk het zwaarst
            ds = [distance(hist, h) for h in looks]
            i = min(range(len(people)), key=lambda k: (0.5 if ds[k] is None else ds[k]) + abs(people[k][0] - cx) * 1.5)
            p, d = people[i], ds[i]
            ok = (d is None and abs(p[0] - cx) <= 0.2) if d is None or hist is None else \
                (abs(p[0] - cx) <= 0.35 and d <= self.PICK_NEAR) or d <= self.PICK_ANY
            if ok:
                with self.lock:
                    self.pending = None
                return self._begin(p, looks[i], now, anchor=hist)
            if now - since > self.PICK_WAIT:
                with self.lock:
                    self.pending, self.picked = None, False
            return None
        if self.target is not None and self.box is not None:
            # dezelfde persoon vasthouden: op waar hij was en op hoe hij eruitziet
            last, gone = self.box, now - self.last_seen
            reach = min(0.5, 0.2 + max(last[2], last[3]) * 0.6 + gone * 0.15)
            cands = [(((p[0] - last[0]) ** 2 + (p[1] - last[1]) ** 2) ** 0.5,
                      distance(h, self.gallery) if self.gallery else None) for p, h in zip(people, looks)]
            choice = Identity.choose(cands, reach, gone, self.own, self.others, now - self.sure_seen)
            if choice:
                i, sure = choice
                self._learn(looks[i], cands, i, sure, reach, gone, now)
                return people[i]
            if not self.picked and gone > self.LOST_AUTO:
                self._drop()                 # automatisch doel opgeven: hierna nieuw kiezen
            return None
        # nieuw doel: de grootste persoon (meestal de spreker vooraan), liefst binnen het kader,
        # bij gelijkspel het dichtst bij de doelpositie
        i = max(range(len(people)), key=lambda k: (fc["l"] <= people[k][0] <= fc["r"], round(people[k][3], 1),
                                                    -abs(people[k][0] - fc["x"])))
        return self._begin(people[i], looks[i], now)

    def _steer(self, hit, now):
        """Persoon → snelheidsstap. Hangt de camera ondersteboven (`invert`), dan spiegelen we het beeld,
        zodat de regelaar altijd in commando's rekent (positief = commando rechts)."""
        with self.lock:
            fc = self.frame_cfg
        x, w = hit[0], hit[2]
        x1, x2 = x - w / 2, x + w / 2
        if self.invert:
            x, x1, x2 = 1 - x, 1 - x2, 1 - x1
            fc = {"l": 1 - fc["r"], "r": 1 - fc["l"], "x": 1 - fc["x"]}
        polls = None
        with self.ctrl_lock:
            if self.poller:
                polls, zoom = self.poller.recent()
                self.ctrl.observe(polls, zoom, self.zoom_max)
            step = self.ctrl.steer(now, x, x1, x2, fc, polls)
            dbg = self.ctrl.dbg
            if self.ctrl.gain_known and self.ctrl.uses_position() and self.cam_id:
                GAINS[self.cam_id] = self.ctrl.gain  # alleen de echt gemeten waarde (zonder positie zit de zoom erin)
        if self.log:
            self.log.write((self.cam or {}).get("name") or self.cam_id or "?", dbg)
        return step

    def _hold(self):
        with self.ctrl_lock:
            self.ctrl.hold(clock())
            self._cmd(0)

    def run(self):
        """De volglus, met opruimen wat er ook gebeurt: stopt de lus door een onverwachte fout, dan staat de
        camera stil, zijn beeld- en positielezer gestopt en toont de UI de fout (niet eeuwig *volgt*)."""
        crashed = None
        try:
            self._loop()
        except Exception as e:  # noqa: BLE001
            crashed = e
        finally:
            if crashed is not None or self.halt_on_exit:
                if crashed is not None and self.sent is not None:
                    self.sent = "?"                           # hoe dan ook een stop sturen
                self.retry_at = 0.0       # de laatste stop wacht niet op de pauze na een mislukt commando
                try:
                    self._hold()
                except Exception:  # noqa: BLE001
                    pass
            self.running = False
            self.grabber.stop()
            if self.poller:
                self.poller.stop()
            if crashed is not None:
                try:
                    self._status(f"volgen gestopt door een fout: {crashed}", force=True, problem=True,
                                 detail=f"{type(crashed).__name__}: {crashed}")
                except Exception:  # noqa: BLE001
                    pass

    def _loop(self):
        self.grabber.start()
        if self.poller:
            self.poller.start()
        self._status("verbinden…")
        last_ts, frames, t0, last_det = 0.0, 0, clock(), 0.0
        started = clock()
        while self.running:
            wait = last_det + 1.0 / self.MAX_FPS - clock()
            if wait > 0:
                time.sleep(min(wait, 0.2))
                continue
            frame, ts = self.grabber.latest()
            age = clock() - ts
            if frame is None or age > self.STALE:
                self._hold()                 # beeld valt weg: meteen stilstaan
                # fout pas tonen (en loggen) als het echt mis is: niet bij het verbinden of een hapering
                if self.grabber.error or (age > 1.5 if frame is not None else clock() - started > 4.0):
                    self._status(self.grabber.error or "geen beeld van de camera",
                                 problem=True, detail=self.grabber.error_detail)
                time.sleep(0.1)
                continue
            if ts == last_ts:
                time.sleep(0.01)
                continue
            last_ts = ts
            last_det = clock()
            try:
                people = self.detector.detect(frame)
            except Exception as e:  # noqa: BLE001
                self._hold()
                self._status(f"detectiefout: {e}", problem=True)
                time.sleep(0.5)
                continue
            if not self.running:
                break
            frames += 1
            if clock() - t0 >= 2:
                self.fps = frames / (clock() - t0)
                frames, t0 = 0, clock()
                self._status(self.status, force=True)

            now = clock()
            hit = self._pick(people, frame, now)
            if hit:
                if now - self.last_seen > self.LOST_STOP:
                    self.ctrl.forget()       # even weg geweest: niet doorrekenen met de oude beweging
                self.target = hit[0]
                self.last_seen = now
                self.box = tuple(float(v) for v in hit[:4])
                step = self._steer(hit, now)
                with self.ctrl_lock:
                    self._cmd(step)
                base = "volgt gekozen persoon" if self.picked else "volgt"
                lim = self.at_limit if self.at_limit and self.poller and self.poller.blocked(self.at_limit) else None
                self._status(base + (f" · grens {'links' if lim == 'left' else 'rechts'} bereikt" if lim else ""))
            elif now - self.last_seen > self.LOST_STOP:
                self._hold()
                with self.lock:
                    waiting = self.pending is not None
                self._status("zoekt gekozen persoon" if waiting
                             else "gekozen persoon kwijt" if self.picked and self.target is not None
                             else "zoekt persoon")

    def stop(self, halt=True):
        """halt=False als er direct een ander commando (preset/handbediening) volgt."""
        self.halt_on_exit = halt
        self.running = False
