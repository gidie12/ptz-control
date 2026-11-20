"""Snelle test van de pan-regelaar van *persoon volgen* zonder camera, zonder beeld en zonder klok.

Laat `tracker.PanFollower` een nagebootste persoon volgen met een nagebootste camera (reageert 0,15 s later, trekt
geleidelijk op, beeld loopt achter) in veel situaties achter elkaar: uitgezoomd en ingezoomd, korte en lange
beeldvertraging, met en zonder positie van de camera. Een minuut volgen kost een fractie van een seconde.

    python docs/tools/volg_test.py                     # tabel met alle situaties
    python docs/tools/volg_test.py --oud /tmp/oud.py   # ernaast: de regelaar uit een oudere tracker.py
                                                       # (git show v1.0.38:tracker.py > /tmp/oud.py)

Per situatie (gemiddeld over 3 runs van 60 s):
  buiten   = deel van de tijd dat het midden van de persoon buiten het volgkader staat
  kwijt    = deel van de tijd dat hij (bijna) uit beeld is
  afw      = gemiddelde afstand tot de doelpositie (deel van de beeldbreedte)
  omk/min  = hoe vaak de camera van richting wisselt (de persoon zelf keert in 'loopt' 9,6× en in 'ijsbeert' 7,5× per minuut)
  cmd/min  = commando's naar de camera

Scenario's (in graden, dus ingezoomd is hetzelfde lopen veel sneller in beeld):
  stap     staat stil, stapt 12 graden opzij en blijft daar staan
  loopt    loopt rustig heen en weer (tot 6 graden/s)
  ijsbeert loopt 4 s, staat 4 s
  staat    staat en wiegt een beetje

Alleen de regelaar: persoonsdetectie en het vasthouden van één persoon (`Identity`) test je met volg_sim.py.
"""
import importlib.util
import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import tracker  # noqa: E402

NOISE = 0.008        # onrust van de detector (deel van de beeldbreedte)
FRAME = {"l": 0.3, "r": 0.7, "x": 0.5}


class Clock:
    """Nagebootste klok voor een oudere regelaar die zelf time.time() aanroept."""
    t = 1000.0

    def time(self):
        return self.t

    def sleep(self, s):
        pass


CLK = Clock()


def scenario(name):
    if name == "stap":
        return lambda t: 0 if t < 5 else min(12, (t - 5) * 6)
    if name == "loopt":
        return lambda t: 12 * math.sin(t * 2 * math.pi / 12.5)
    if name == "ijsbeert":
        def f(t):
            ph = t % 16
            return -10 + 5 * ph if ph < 4 else 10 if ph < 8 else 10 - 5 * (ph - 8) if ph < 12 else -10
        return f
    if name == "staat":
        return lambda t: 0.6 * math.sin(t * 2.1) + 0.3 * math.sin(t * 5.3)
    raise KeyError(name)


def run(sc, fov=58.0, lat=0.45, deg_step=4.0, visca=True, fov_err=1.0, csign=1, secs=60, fps=8, max_speed=6,
        seed=1, old=None):
    """Eén run. old = module met een oude `PanController` (anders de huidige `PanFollower`)."""
    random.seed(seed)
    person = scenario(sc)
    cam = vel = 0.0
    cur, sent, last_dir = 0, 0, 0
    queue, hist, polls = [], [], []
    w = min(0.6, 0.09 * 58 / fov)                 # ingezoomd is de persoon breder in beeld
    if old:
        ctrl, target = old.PanController(max_speed=max_speed), None
    else:
        ctrl = tracker.PanFollower(max_speed=max_speed)
    st = {"n": 0, "out": 0, "lost": 0, "err": 0.0, "cmds": 0, "rev": 0}
    dt, t, next_det, next_poll = 0.01, 0.0, 0.0, 0.0
    while t < secs:
        now = CLK.t = 1000.0 + t
        while queue and queue[0][0] <= t:
            cur = queue.pop(0)[1]
        vel += max(-90 * dt, min(90 * dt, cur * deg_step - vel))      # optrekken 90 graden/s²
        cam += vel * dt
        x = 0.5 + (person(t) - cam) / fov
        hist.append((t, x))
        if t >= 3:
            st["n"] += 1
            st["err"] += abs(x - FRAME["x"])
            st["out"] += x < FRAME["l"] or x > FRAME["r"]
            st["lost"] += x < 0.02 or x > 0.98
        if visca and t >= next_poll:
            next_poll += 0.15
            polls.append((now, round(cam * tracker.COUNTS_PER_DEG) * csign))
            del polls[:-40]
        if t >= next_det:
            next_det += 1.0 / fps
            seen = next((hx for ht, hx in reversed(hist) if ht <= t - lat), None)
            if seen is not None and -0.02 < seen < 1.02:
                seen += random.gauss(0, NOISE)
                if old:
                    target = seen if target is None else 0.6 * seen + 0.4 * target
                    x1, x2 = target - w / 2, target + w / 2
                    span = FRAME["r"] - FRAME["l"]
                    outside = abs(target - FRAME["x"]) > max(0.05, span * 0.25) if w >= span * 0.9 \
                        else x1 < FRAME["l"] or x2 > FRAME["r"]
                    urgent = (x1 <= 0.02 and target < FRAME["x"]) or (x2 >= 0.98 and target > FRAME["x"])
                    res = ctrl.update(target - FRAME["x"], outside, urgent)
                    step = sent if res is None else 0 if res[0] == "stop" else res[1] if res[0] == "right" else -res[1]
                else:
                    zoom = None
                    if visca:
                        est = max(tracker.WIDE_FOV / tracker.ZOOM_RATIO, min(tracker.WIDE_FOV, fov * fov_err))
                        zoom = 16384 * math.log(tracker.WIDE_FOV / est) / math.log(tracker.ZOOM_RATIO)
                        ctrl.observe(list(polls), zoom)
                    step = ctrl.steer(now, seen, seen - w / 2, seen + w / 2, FRAME, list(polls) if visca else None)
            elif old:
                target = None
                ctrl.halt()
                step = 0
            else:
                ctrl.hold(now)
                ctrl.forget()
                step = 0
            if step != sent:
                st["cmds"] += 1
                d = (step > 0) - (step < 0)
                if d and last_dir and d != last_dir:
                    st["rev"] += 1
                last_dir = d or last_dir
                sent = step
                queue.append((t + 0.15, step))        # de camera reageert 0,15 s later
        t += dt
    n = max(1, st["n"])
    return {"buiten": 100 * st["out"] / n, "kwijt": 100 * st["lost"] / n, "afw": st["err"] / n,
            "omk": st["rev"] * 60 / secs, "cmd": st["cmds"] * 60 / secs, "lat": getattr(ctrl, "lat", None)}


def average(sc, old=None, **kw):
    rs = [run(sc, seed=s, old=old, **kw) for s in (1, 2, 3)]
    return {k: sum(r[k] for r in rs) / len(rs) for k in ("buiten", "kwijt", "afw", "omk", "cmd")}


def cell(a):
    return "%4.0f%% %4.0f%% %5.2f %5.0f %5.0f" % (a["buiten"], a["kwijt"], a["afw"], a["omk"], a["cmd"])


def main():
    old = None
    if "--oud" in sys.argv:
        spec = importlib.util.spec_from_file_location("oude_tracker", sys.argv[sys.argv.index("--oud") + 1])
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        old.time = CLK
    rows = [("beeldhoek %d°, vertraging %.2f s" % (fov, lat), {"fov": fov, "lat": lat})
            for lat in (0.25, 0.45, 0.8) for fov in (58, 30, 15)]
    rows += [("30°, geen positie van de camera", {"fov": 30, "visca": False}),
             ("30°, beeldhoek 40% te smal geschat", {"fov": 30, "fov_err": 0.6}),
             ("30°, beeldhoek 60% te breed geschat", {"fov": 30, "fov_err": 1.6}),
             ("30°, camera telt andersom", {"fov": 30, "csign": -1}),
             ("30°, camera 1,7°/s per stap", {"fov": 30, "deg_step": 1.7}),
             ("30°, camera 8°/s per stap", {"fov": 30, "deg_step": 8.0})]
    head = "buiten kwijt   afw omk/m cmd/m"
    print("%-36s %-9s %s%s" % ("situatie", "scenario", head, "   | oud: " + head if old else ""))
    tot = {"nieuw": [0.0] * 4, "oud": [0.0] * 4}
    n = 0
    for label, kw in rows:
        for sc in ("stap", "loopt", "ijsbeert", "staat"):
            a = average(sc, **kw)
            line = "%-36s %-9s %s" % (label, sc, cell(a))
            pairs = [("nieuw", a)]
            if old:
                b = average(sc, old=old, **kw)
                line += "   |      " + cell(b)
                pairs.append(("oud", b))
            for k, v in pairs:
                for i, key in enumerate(("buiten", "kwijt", "afw", "omk")):
                    tot[k][i] += v[key]
            n += 1
            print(line)
    for k in ("nieuw", "oud") if old else ("nieuw",):
        b, kw_, afw, omk = (v / n for v in tot[k])
        print("GEMIDDELD %-5s buiten %.0f%%  kwijt %.0f%%  afwijking %.3f  omkeringen/min %.1f" % (k, b, kw_, afw, omk))


if __name__ == "__main__":
    main()
