"""Test van de volgmodule zonder camera, beeld of server: opruimen na een onverwachte fout, een mislukt
stop-commando opnieuw sturen, de meldingen in de status (guess, meas), het meetlog (formaat, doorgaan,
doorschuiven) en de afstelling. Draait in ~12 s; exit 1 bij een fout. Zie ook volg_test.py (de regelaar zelf).

    python docs/tools/tracker_test.py
"""
import sys, time, os, threading
import tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
TMP = tempfile.mkdtemp()
import numpy as np, tracker as T

class Grab:
    def __init__(self, url): self.error = self.error_detail = ""; self.on = True; self.stopped = False
    def start(self): pass
    def stop(self): self.stopped = True
    def latest(self): return (np.zeros((90, 160, 3), np.uint8), T.clock()) if self.on else (None, 0.0)
T.FrameGrabber = Grab
class Det:
    kind, note = "test", ""
    def __init__(self): self.x = 0.9
    def detect(self, f): return [(self.x, 0.5, 0.09, 0.5, 0.9)]
def mk(send, read_pan=None, **kw):
    st = []
    t = T.CameraTracker({"id": "c", "name": "Cam; 1"}, "x", Det(), send, st.append, dict({"max_speed": 6, "read_pan": read_pan}, **kw))
    return t, st
ok = True
def check(name, cond):
    global ok; ok &= bool(cond); print(("OK   " if cond else "FOUT ") + name)

# 1. onverwachte fout in de lus: camera stopt, lezers stoppen, status = fout
sent = []
class RP:
    closed = False
    def __call__(self): return 100
    def zoom(self): return 0
    def close(self): RP.closed = True
t, st = mk(lambda a, args: sent.append((a, args)), read_pan=RP())
t.start(); time.sleep(1.2)
check("draait naar rechts (persoon rechts buiten het kader)", any(a == "move" and g["dir"] == "right" for a, g in sent))
check("alleen pan: tilt-snelheid 1, alleen left/right", all(a == "stop" or (g["dir"] in ("left", "right") and g["tilt"] == 1) for a, g in sent))
t._pick = lambda *a: 1 / 0
t.join(3)
check("thread gestopt na de fout", not t.is_alive())
check("stop gestuurd na de fout", sent and sent[-1][0] == "stop")
check("grabber en poller gestopt, VISCA dicht", t.grabber.stopped and not t.poller.running and (time.sleep(0.5) or RP.closed))
check("status = fout met melding", st[-1]["problem"] and "gestopt door een fout" in st[-1]["status"])
check("status heeft meas en guess", "meas" in st[-1] and st[-1]["guess"] == "")

# 2. mislukt stop-commando wordt herhaald
sent2, fail = [], {"n": 0}
def send2(a, args):
    if a == "stop" and fail["n"] < 2:
        fail["n"] += 1; raise OSError("time-out")
    sent2.append(a)
t, st = mk(send2); t.start(); time.sleep(1.0)
t.detector.detect = lambda f: []          # persoon weg -> stop
time.sleep(3.5)
check("stop mislukte 2×, daarna toch aangekomen", fail["n"] == 2 and sent2 and sent2[-1] == "stop")
t.stop(); t.join(2)

# 3. zonder poller: guess-melding
t, st = mk(lambda a, g: None); t.start(); time.sleep(0.6); t.stop(); t.join(2)
check("zonder positielezer: guess gevuld", st[-1]["guess"] == "camerapositie lezen staat uit" and st[-1]["meas"]["pos"] is False)

# 4. zoom niet te lezen: guess na een paar seconden
class RP2(RP):
    def zoom(self): raise OSError("geen antwoord")
t, st = mk(lambda a, g: None, read_pan=RP2()); t.start(); time.sleep(2.6); t.stop(); t.join(2)
check("zoom onleesbaar: guess noemt de zoom", "zoom" in st[-1]["guess"] and st[-1]["meas"]["pos"] is False)

# 5. meetlog: formaat, doorgaan na herstart, doorschuiven, oud formaat
p, p1 = os.path.join(TMP, "volgen.csv"), os.path.join(TMP, "volgen.1.csv")
d = {"x": .5, "err": .01, "img_v": .1, "move": .2, "cam": .3, "want": .4, "fov": 58.0, "gain": 1.23, "lat": .45, "step": -3, "urgency": 0, "panning": 1, "x1": .4, "x2": .6, "pos": True}
L = T.TrackLog(p); L.write("Cam; 1", d); L.write("Cam; 1", d)
lines = open(p, encoding="utf-8-sig").read().splitlines()
check("meetlog: kop + 2 regels, puntkomma en decimale komma, BOM", len(lines) == 3 and lines[0] == T.TrackLog.COLS and open(p, "rb").read(3) == b"\xef\xbb\xbf" and ";1,23;0,45;-3;" in lines[1] and len(lines[1].split(";")) == len(lines[0].split(";")))
L2 = T.TrackLog(p); L2.write("c", d)
check("meetlog gaat door na een herstart", len(open(p, encoding="utf-8-sig").read().splitlines()) == 4 and L2.lines == 3)
T.TrackLog.MAX_LINES = 5
L3 = T.TrackLog(p)
for _ in range(4): L3.write("c", d)
check("meetlog schuift door als het vol is", os.path.exists(p1) and len(open(p, encoding="utf-8-sig").read().splitlines()) == 3 and len(open(p1, encoding="utf-8-sig").read().splitlines()) == 6)
open(p, "w").write("tijd,camera,x\n1,a,2\n")
L4 = T.TrackLog(p); L4.write("c", d)
check("oud formaat (v1.0.40) schuift door, nieuw bestand begint", open(p, encoding="utf-8-sig").readline().strip() == T.TrackLog.COLS and "tijd,camera" in open(p1).read())
# 6. afstelling: vertraging blijft staan als alleen een andere schuif verandert
c = T.PanFollower(); c.lat = 0.81
c.set_tune({"speed": 1.5}); check("set_tune (andere schuif) laat de gemeten vertraging staan", c.lat == 0.81)
c.set_tune({"speed": 1.5, "lead": 0.6}); check("set_tune (lead) zet de vertraging", c.lat == 0.6)
import shutil
shutil.rmtree(TMP, ignore_errors=True)
print("ALLES OK" if ok else "ER ZIJN FOUTEN")
os._exit(0 if ok else 1)
