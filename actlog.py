"""Activiteitenlog: wie deed wat (vanaf welk apparaat), plus fouten van camera's en de videomixer.

🧑 Bedoeld om achteraf te zien wat er tijdens een dienst gebeurde. Alleen stdlib.

- Regels staan in het geheugen (laatste ``KEEP``) en in ``<data>/activiteit.log`` (één JSON-object per regel),
  zodat ze een herstart overleven. Boven ``MAX_BYTES`` wordt het bestand ingekort tot de laatste ``KEEP`` regels.
- Een regel: ``{id, t, kind, who, ip, cam, msg, n}``. ``kind`` = ``actie`` | ``fout`` | ``systeem`` | ``lied`` (nummers uit ProPresenter/draaiboek).
- Herhaling: is de nieuwe regel gelijk aan de laatste van dezelfde bron (who + cam + msg) en minder dan
  ``MERGE_S`` seconden later, dan telt die regel op (``n``, ``last``) in plaats van een nieuwe te maken.
  Zo geeft een Xbox-stick of het automatisch opslaan van de tegelgrootte geen honderden regels.
"""
import collections
import json
import os
import threading
import time

KEEP = 2000
MAX_BYTES = 2_000_000
MERGE_S = 60
KINDS = ("actie", "fout", "systeem", "lied")


class Log:
    def __init__(self, path, on_add=None):
        self.path = path
        self.on_add = on_add or (lambda e: None)
        self.lock = threading.Lock()
        self.items = collections.deque(maxlen=KEEP)
        self.last_by = {}          # (who, cam) -> laatste regel van die bron
        self.next_id = 1
        self._timer = None
        self._load()

    # -------------------------------------------------------------- opslag
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                lines = f.readlines()[-KEEP:]
        except OSError:
            return
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict) or "msg" not in e:
                continue
            e["id"] = self.next_id
            self.next_id += 1
            self.items.append(e)

    def _append_file(self, e):
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(_stored(e), ensure_ascii=False) + "\n")
            if os.path.getsize(self.path) > MAX_BYTES:
                self._rewrite()
        except OSError:
            pass                    # loggen mag nooit de bediening breken

    def _rewrite(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for e in self.items:
                f.write(json.dumps(_stored(e), ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)

    def _flush_later(self):
        if self._timer is None:
            self._timer = threading.Timer(5, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self):
        with self.lock:
            self._timer = None
            try:
                self._rewrite()
            except OSError:
                pass

    # -------------------------------------------------------------- schrijven
    def add(self, msg, kind="actie", who="", ip="", cam="", now=None):
        if kind not in KINDS:
            kind = "actie"
        now = time.time() if now is None else now
        msg = str(msg)[:300]
        with self.lock:
            key = (who, cam)
            prev = self.last_by.get(key)
            if (prev is not None and prev["msg"] == msg and prev["kind"] == kind
                    and now - prev.get("last", prev["t"]) < MERGE_S and prev is self._latest_of(key)):
                prev["n"] = prev.get("n", 1) + 1
                prev["last"] = now
                e = prev
                rewrite = True
            else:
                e = {"id": self.next_id, "t": now, "kind": kind, "who": who, "ip": ip, "cam": cam, "msg": msg}
                self.next_id += 1
                self.items.append(e)
                self.last_by[key] = e
                rewrite = False
            if rewrite:
                self._flush_later()      # alleen de teller veranderd: bestand straks in één keer herschrijven
            else:
                self._append_file(e)
            out = dict(e)
        self.on_add(out)
        return out

    def _latest_of(self, key):
        for e in reversed(self.items):
            if (e.get("who", ""), e.get("cam", "")) == key:
                return e
        return None

    def clear(self):
        with self.lock:
            self.items.clear()
            self.last_by.clear()
            try:
                os.remove(self.path)
            except OSError:
                pass

    # -------------------------------------------------------------- lezen
    def since(self, since=0, limit=500):
        with self.lock:
            out = [dict(e) for e in self.items if e["id"] > since]
        return out[-limit:]

    def all(self):
        with self.lock:
            return [dict(e) for e in self.items]

    def text(self):
        rows = []
        for e in self.all():
            t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e["t"]))
            extra = f"  (×{e['n']}, laatst {time.strftime('%H:%M:%S', time.localtime(e['last']))})" if e.get("n", 1) > 1 else ""
            cam = f"[{e['cam']}] " if e.get("cam") else ""
            rows.append(f"{t}  {e['kind']:<7}  {e.get('who') or '-':<28}  {cam}{e['msg']}{extra}")
        return "\n".join(rows) + ("\n" if rows else "")


def _stored(e):
    return {k: v for k, v in e.items() if k != "id"}
