"""Koppeling met de Roland V-1HD via MIDI of direct via USB: welke ingang is live + verkenningslog.

Draait als achtergrondthread in de server. Twee verbindingssoorten (`conn`):
- "midi": MIDI-poort van het systeem (USB-MIDI met driver) via `mido` + `python-rtmidi`;
- "usb":  rechtstreeks via libusb (`pyusb` + `libusb-package`), zonder MIDI-driver (v1hd_usb.py).
Zonder de packages start de server gewoon en meldt de status waarom het niet werkt.

Config (per workspace, in cameras.json): cfg.midi = {
    enabled: bool, conn: "midi" | "usb",
    port: "V-1HD" (deel van de poortnaam; bij USB: productnaam of "vid:pid"), dev: 16 (SysEx device-ID),
    live_bus: "A" | "B" (rij aan de kant waar de fader staat), map: {"1": camid, ... "4": camid}
}
🧑 De fader wordt nooit gebruikt; op de mixer is de gekozen ingang rood (= live).
⚠️ Niet getest op de echte mixer; de lampjes (73 02 xx) worden alleen gelogd,
live wordt bepaald uit de knopdrukken (program change) en video sel A/B (71 00 08/09).
"""
import collections
import os
import sys
import threading
import time

import roland_v1hd as rv
import v1hd_usb

POLL = 0.5          # s tussen opvragen van video sel + lampjes
RETRY = 3.0         # s tussen pogingen om de poort te openen
LOG_MAX = 5000
FAKE = None        # nagebootste mixer bij PTZ_MIDI_FAKE (alleen tests/screenshots)

CONNS = ("midi", "usb")
DEFAULTS = {"enabled": False, "conn": "midi", "port": "V-1HD", "dev": rv.DEFAULT_DEV, "live_bus": "A", "map": {}}


def norm_cfg(m):
    m = dict(m or {})
    out = dict(DEFAULTS)
    out["enabled"] = bool(m.get("enabled", False))
    out["conn"] = "usb" if str(m.get("conn", "midi")).lower() == "usb" else "midi"
    out["port"] = str(m.get("port") or DEFAULTS["port"]).strip()[:80] or DEFAULTS["port"]
    try:
        out["dev"] = max(0, min(31, int(m.get("dev", DEFAULTS["dev"]))))
    except (TypeError, ValueError):
        pass
    out["live_bus"] = "B" if str(m.get("live_bus", "A")).upper() == "B" else "A"
    mp = m.get("map") if isinstance(m.get("map"), dict) else {}
    out["map"] = {str(k): str(v) for k, v in mp.items() if str(k) in ("1", "2", "3", "4") and v}
    return out


def hexs(b):
    return " ".join("%02X" % x for x in b)


class MidoBackend:
    """Echte MIDI via mido + python-rtmidi."""

    def __init__(self):
        import mido  # noqa: F401  (ImportError → MIDI niet beschikbaar)
        self.mido = mido

    def names(self):
        return self.mido.get_input_names(), self.mido.get_output_names()

    def open(self, in_name, out_name):
        return _MidoPort(self.mido, in_name, out_name)


class _MidoPort:
    def __init__(self, mido, in_name, out_name):
        self.mido = mido
        self.inp = mido.open_input(in_name)
        self.out = mido.open_output(out_name) if out_name else None

    def poll(self):
        res = []
        for msg in self.inp.iter_pending():
            if msg.type in ("active_sensing", "clock"):
                continue
            res.append((msg.dict(), list(msg.bytes())))
        return res

    def send_sysex(self, data):
        if self.out:
            self.out.send(self.mido.Message("sysex", data=data))

    def close(self):
        for p in (self.inp, self.out):
            try:
                if p:
                    p.close()
            except Exception:  # noqa: BLE001
                pass


def _make_backend(conn="midi"):
    fake = os.environ.get("PTZ_MIDI_FAKE")
    if fake:  # tests/screenshots: nagebootste mixer (docs/tools/nep_v1hd.py), voor MIDI én USB
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "tools"))
        import nep_v1hd
        global FAKE
        if FAKE is None:
            FAKE = nep_v1hd.FakeV1HD(a=int(fake) if fake.isdigit() else 1)
        if conn == "usb":
            return v1hd_usb.UsbBackend(finder=lambda: [nep_v1hd.FakeUsbDev(FAKE)]), None
        return nep_v1hd.FakeBackend(FAKE), None
    if conn == "usb":
        try:
            return v1hd_usb.UsbBackend(), None
        except ImportError:
            return None, "USB-packages ontbreken (pyusb + libusb-package). Start PTZ Control opnieuw met de starter om ze te installeren."
        except Exception as e:  # noqa: BLE001
            return None, "USB direct niet beschikbaar: %s" % e
    try:
        return MidoBackend(), None
    except ImportError:
        return None, "MIDI-packages ontbreken (mido + python-rtmidi). Start PTZ Control opnieuw met de starter om ze te installeren."
    except Exception as e:  # noqa: BLE001
        return None, "MIDI niet beschikbaar: %s" % e


class Watcher:
    def __init__(self, get_cfg, cameras, on_change, backend=None):
        """get_cfg() → cfg.midi (ruw), cameras() → [{id,name}], on_change(snapshot)."""
        self.get_cfg = get_cfg
        self.cameras = cameras
        self.on_change = on_change
        self._fixed = backend           # tests: één vaste backend voor beide soorten
        self._backends = {}             # conn → (backend, fout); lui gemaakt
        self.backend, self.backend_error = None, None
        self.conn = None
        self.lock = threading.Lock()
        self.port = None
        self.port_name = ""
        self.error = ""
        self.ports = []
        self.state = rv.V1HDState()
        self.pressed = None          # laatst gedrukte ingang (voor oplichten in de dialoog)
        self.pressed_at = 0
        self.log = collections.deque(maxlen=LOG_MAX)
        self.log_seq = 0
        self.recording = False
        self.rec_started = None
        self._last_leds = None
        self._last_key = None
        self._cache = None
        self._wake = threading.Event()
        self._stop = False
        self.thread = threading.Thread(target=self._run, name="midi", daemon=True)

    # ------------------------------------------------------------ publiek
    def start(self):
        self.thread.start()

    def refresh(self):
        """Config gewijzigd (of workspace gewisseld): opnieuw verbinden."""
        with self.lock:
            self._close()
            self.state = rv.V1HDState()
            self._last_leds = None
            self._cache = None
        self._wake.set()
        self._emit(force=True)

    def config_changed(self, old, new):
        """Na /api/midi/config: alleen opnieuw verbinden als aan/uit, verbinding, poort of device-ID veranderde."""
        o, n = norm_cfg(old), norm_cfg(new)
        if (o["enabled"], o["conn"], o["port"], o["dev"]) != (n["enabled"], n["conn"], n["port"], n["dev"]):
            return self.refresh()
        with self.lock:
            self._cache = None
        self._emit(force=True)

    def set_recording(self, on):
        with self.lock:
            if on and not self.recording:
                self.log.clear()
                self.recording = True
                self.rec_started = time.strftime("%Y-%m-%d %H:%M:%S")
                self._log("Verkenning gestart. Poort: %s" % (self.port_name or "(niet verbonden)"))
                if self.state.leds:
                    self._log("  lampjes 73 02 00..18: " + hexs(self.state.leds))
            elif not on and self.recording:
                self._log("Verkenning gestopt.")
                self.recording = False
        self._emit(force=True)

    def log_since(self, seq):
        with self.lock:
            lines = [l for l in self.log if l[0] > seq]
            return {"seq": self.log_seq, "lines": [l[1] for l in lines], "recording": self.recording}

    def log_text(self):
        with self.lock:
            head = ["PTZ Control – MIDI-verkenning Roland V-1HD",
                    "Start: %s, poort: %s" % (self.rec_started or "-", self.port_name or "-"), ""]
            return "\n".join(head + [l[1] for l in self.log]) + "\n"

    def snapshot(self):
        with self.lock:
            return self._snapshot()

    # ------------------------------------------------------------ intern
    def _use(self, conn):
        """Backend voor deze verbindingssoort kiezen (lui maken, onthouden)."""
        if conn == self.conn and (self.backend or self.backend_error):
            return
        if self._fixed is not None:
            b, e = self._fixed, None
        else:
            if conn not in self._backends:
                self._backends[conn] = _make_backend(conn)
            b, e = self._backends[conn]
        if self.port:
            self._close()
        self.conn, self.backend, self.backend_error, self.ports = conn, b, e, []

    def _cfg(self):
        # config en cameralijst hooguit 1× per seconde van schijf lezen (de lus draait ~50×/s)
        now = time.time()
        if self._cache and now - self._cache[0] < 1.0:
            return self._cache[1]
        try:
            c = norm_cfg(self.get_cfg())
        except Exception:  # noqa: BLE001
            c = norm_cfg({})
        try:
            cams = {x.get("id") for x in (self.cameras() or [])}
        except Exception:  # noqa: BLE001
            cams = set()
        self._cache = (now, c, cams)
        return c

    def _snapshot(self):
        cfg = self._cfg()
        # 🧑 fader wordt niet gebruikt: alleen de ingestelde rij telt (faderstand negeren, kant is onbekend)
        sel = self.state.sel.get(cfg["live_bus"]) if self.port else None
        live = [sel] if sel else []
        cams = self._cache[2] if self._cache else set()
        live_cams = [cfg["map"][str(i)] for i in live if cfg["map"].get(str(i)) in cams]
        return {
            "type": "midi",
            "conn": self.conn or cfg["conn"],
            "available": self.backend is not None,
            "error": self.backend_error or self.error,
            "enabled": cfg["enabled"],
            "connected": self.port is not None,
            "port": self.port_name,
            "ports": self.ports,
            "sel": dict(self.state.sel),
            "live": live,
            "live_cams": live_cams,
            "pressed": self.pressed if time.time() - self.pressed_at < 1.5 else None,
            "recording": self.recording,
            "log_seq": self.log_seq,
        }

    def _emit(self, force=False):
        with self.lock:
            snap = self._snapshot()
        key = (snap["connected"], snap["port"], snap["error"], tuple(snap["live"]), tuple(snap["live_cams"]),
               snap["sel"].get("A"), snap["sel"].get("B"), snap["pressed"], snap["recording"], tuple(snap["ports"]),
               snap["enabled"], snap["conn"], snap["available"])
        if force or key != self._last_key:
            self._last_key = key
            try:
                self.on_change(snap)
            except Exception:  # noqa: BLE001
                pass

    def _log(self, text):
        if not self.recording:
            return
        self.log_seq += 1
        self.log.append((self.log_seq, "%s  %s" % (time.strftime("%H:%M:%S"), text)))

    def _close(self):
        if self.port:
            try:
                self.port.close()
            except Exception:  # noqa: BLE001
                pass
        self.port = None
        self.port_name = ""

    def _connect(self, cfg):
        try:
            ins, outs = self.backend.names()
        except Exception as e:  # noqa: BLE001
            self.error = "MIDI-poorten opvragen mislukt: %s" % e
            return
        self.ports = list(ins)
        usb = cfg["conn"] == "usb"
        want = cfg["port"].lower()
        if usb:
            pin = pout = self.backend.match(cfg["port"])
        else:
            pin = next((n for n in ins if want in n.lower()), None)
            pout = next((n for n in outs if want in n.lower()), None)
        if not pin:
            if usb:
                self.error = ("Geen Roland-apparaat met '%s' via USB gevonden%s. Is de V-1HD aangesloten en aan?"
                              % (cfg["port"], (" (wel: %s)" % ", ".join(ins)) if ins else ""))
            else:
                self.error = "Geen MIDI-poort met '%s' gevonden. Is de V-1HD via USB aangesloten en aan?" % cfg["port"]
            return
        try:
            self.port = self.backend.open(pin, pout)
            self.port_name = pin
            self.error = "" if pout else "Alleen ontvangen: geen MIDI-uitgang gevonden, beginstand kan niet worden opgevraagd."
            self._log("Verbonden met '%s'%s" % (pin, "" if pout else " (geen uitgang)"))
        except Exception as e:  # noqa: BLE001
            self.port = None
            self.error = ("USB-verbinding openen mislukt: %s" if usb else "MIDI-poort openen mislukt: %s") % e

    def _handle(self, d, raw):
        st = self.state
        before_sel = dict(st.sel)
        uitleg = st.feed(d)
        if d.get("type") == "program_change":
            msb = st.bank[0]
            if msb in rv.BUS and d.get("program", 9) < 4:
                self.pressed = d["program"] + 1
                self.pressed_at = time.time()
        if d.get("type") == "sysex":
            # antwoorden op het opvragen alleen loggen als er iets veranderde
            if st.leds and st.leds != self._last_leds:
                if self._last_leds:
                    diff = ["%02X:%02X→%02X" % (i, o, n) for i, (o, n) in enumerate(zip(self._last_leds, st.leds)) if o != n]
                    self._log("  lampjes gewijzigd: " + " ".join(diff))
                else:
                    self._log("  lampjes 73 02 00..18: " + hexs(st.leds))
                self._last_leds = list(st.leds)
            if st.sel != before_sel:
                self._log("%-40s video sel A=%s B=%s" % (hexs(raw)[:40], st.sel["A"], st.sel["B"]))
            return
        self._log("%-40s %s" % (hexs(raw), uitleg or d.get("type")))
        if uitleg and d.get("type") == "program_change":
            cfg = self._cfg()
            self._log("  → live (rij %s): %s" % (cfg["live_bus"], st.sel.get(cfg["live_bus"]) or "-"))

    def _run(self):
        last_poll = 0
        last_try = 0
        while not self._stop:
            cfg = self._cfg()
            if cfg["conn"] != self.conn or (cfg["enabled"] and not self.backend and not self.backend_error):
                with self.lock:
                    self._use(cfg["conn"])
            if not self.backend or not cfg["enabled"]:
                with self.lock:
                    if self.port:
                        self._close()
                    self.error = ""
                self._emit()
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            if not self.port:
                if time.time() - last_try >= RETRY:
                    last_try = time.time()
                    with self.lock:
                        self._connect(cfg)
                    last_poll = 0
                self._emit()
                self._wake.wait(0.5)
                self._wake.clear()
                continue
            try:
                msgs = self.port.poll()
                with self.lock:
                    for d, raw in msgs:
                        self._handle(d, raw)
                if time.time() - last_poll >= POLL:
                    last_poll = time.time()
                    self.port.send_sysex(rv.rq1(rv.ADDR_VIDEO, rv.VIDEO_SIZE, cfg["dev"]))
                    self.port.send_sysex(rv.rq1(rv.ADDR_LED, rv.LED_SIZE, cfg["dev"]))
            except Exception as e:  # noqa: BLE001
                with self.lock:
                    self._log("Verbinding verbroken: %s" % e)
                    self._close()
                    self.error = ("USB-verbinding verbroken: %s" if self.conn == "usb" else "MIDI-verbinding verbroken: %s") % e
            self._emit()
            time.sleep(0.02)
