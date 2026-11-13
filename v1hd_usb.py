"""Roland V-1HD direct via USB (zonder MIDI-driver / rtmidi): libusb via pyusb.

Zelfde functie als de MIDI-koppeling (midi.py): de mixer stuurt over USB dezelfde
MIDI-berichten, alleen verpakt in USB-MIDI-pakketten van 4 bytes op een bulk-endpoint.
Deze module leest/schrijft die endpoints rechtstreeks, zodat er geen MIDI-poort
(Roland-driver, CoreMIDI, rtmidi) nodig is.

Packages: `pyusb` + `libusb-package` (levert libusb mee, dus geen Homebrew/Zadig-libusb
nodig). Zonder die packages meldt de status waarom USB direct niet werkt.

⚠️ Aannames, niet getest op de echte mixer:
- Roland vendor-ID 0582; het product-ID van de V-1HD is onbekend, dus er wordt gezocht
  op productnaam (bevat het filter, standaard "V-1HD") of op "vid:pid" (bv. "0582:0123").
- De mixer gebruikt het standaard USB-MIDI-pakketformaat (CIN in de lage nibble van
  byte 0), ook als hij zich als vendor-specifiek apparaat meldt (zoals veel Roland-apparaten).
- macOS: werkt alleen als geen andere driver (Roland V-1HD-driver) het apparaat al claimt.
  Windows: libusb heeft een WinUSB-driver nodig (bv. via Zadig); anders "geen toegang".

Het ontleden (`UsbMidiParser`, `sysex_packets`) is alleen standaard-Python en apart te testen.
"""

ROLAND_VID = 0x0582

# aantal MIDI-bytes per Code Index Number (USB-MIDI 1.0, tabel 4-1)
CIN_LEN = {0x2: 2, 0x3: 3, 0x4: 3, 0x5: 1, 0x6: 2, 0x7: 3, 0x8: 3, 0x9: 3,
           0xA: 3, 0xB: 3, 0xC: 2, 0xD: 2, 0xE: 3, 0xF: 1}

_TYPES = {0x80: "note_off", 0x90: "note_on", 0xA0: "polytouch", 0xB0: "control_change",
          0xC0: "program_change", 0xD0: "aftertouch", 0xE0: "pitchwheel"}
_SYS = {0xF8: "clock", 0xFA: "start", 0xFB: "continue", 0xFC: "stop", 0xFE: "active_sensing",
        0xFF: "reset", 0xF1: "quarter_frame", 0xF2: "songpos", 0xF3: "song_select", 0xF6: "tune_request"}


def decode(raw):
    """Eén compleet MIDI-bericht (bytes) → mido-achtige dict (zoals midi.py die gebruikt)."""
    raw = list(raw)
    if not raw:
        return None
    st = raw[0]
    if st == 0xF0:
        data = raw[1:-1] if raw[-1] == 0xF7 else raw[1:]
        return {"type": "sysex", "data": data}
    if st >= 0xF0:
        return {"type": _SYS.get(st, "system_%02x" % st)}
    t, ch = _TYPES.get(st & 0xF0), st & 0x0F
    d = raw[1:] + [0, 0]
    if t == "control_change":
        return {"type": t, "channel": ch, "control": d[0], "value": d[1]}
    if t == "program_change":
        return {"type": t, "channel": ch, "program": d[0]}
    if t in ("note_on", "note_off", "polytouch"):
        return {"type": t, "channel": ch, "note": d[0], ("velocity" if t != "polytouch" else "value"): d[1]}
    if t == "aftertouch":
        return {"type": t, "channel": ch, "value": d[0]}
    if t == "pitchwheel":
        return {"type": t, "channel": ch, "pitch": (d[0] | (d[1] << 7)) - 8192}
    return {"type": "unknown"}


class UsbMidiParser:
    """Zet USB-MIDI-pakketten (veelvoud van 4 bytes) om in [(dict, raw)], ook SysEx over meerdere pakketten."""

    MAX_SYSEX = 4096

    def __init__(self):
        self.sysex = None

    def feed(self, data):
        out = []
        data = bytes(data)
        for i in range(0, len(data) - 3, 4):
            cin = data[i] & 0x0F
            b = list(data[i + 1:i + 4])
            if cin in (0x0, 0x1):
                continue                      # gereserveerd / opvulling
            n = CIN_LEN.get(cin, 0)
            msg = b[:n]
            if cin == 0x4 or (self.sysex is not None and cin in (0x5, 0x6, 0x7)) or (cin in (0x5, 0x6, 0x7) and b[0] == 0xF0):
                if b[0] == 0xF0:
                    self.sysex = []
                if self.sysex is None:
                    continue                  # vervolg zonder begin: weggooien
                self.sysex.extend(msg)
                if len(self.sysex) > self.MAX_SYSEX:
                    self.sysex = None
                    continue
                if cin != 0x4 or (msg and msg[-1] == 0xF7):
                    raw, self.sysex = self.sysex, None
                    if raw and raw[-1] != 0xF7:
                        raw.append(0xF7)
                    out.append((decode(raw), raw))
                continue
            if not msg:
                continue
            d = decode(msg)
            if d:
                out.append((d, msg))
        return out


def sysex_packets(data, cable=0):
    """SysEx zonder F0/F7 (zoals rv.rq1 het geeft) → USB-MIDI-pakketten."""
    raw = [0xF0] + [x & 0x7F for x in data] + [0xF7]
    out = bytearray()
    c = (cable & 0x0F) << 4
    while len(raw) > 3:
        out += bytes([c | 0x4] + raw[:3])
        raw = raw[3:]
    pad = raw + [0] * (3 - len(raw))
    out += bytes([c | (0x4 + len(raw))] + pad)   # 1 → 5, 2 → 6, 3 → 7
    return bytes(out)


def short_packet(msg, cable=0):
    """Kanaalbericht (2–3 bytes) → één pakket (voor tests / later bedienen)."""
    st = msg[0]
    cin = st >> 4 if st < 0xF0 else 0xF
    b = list(msg) + [0, 0]
    return bytes([((cable & 0x0F) << 4) | cin] + b[:3])


# ------------------------------------------------------------------ libusb (pyusb)
def _filter_match(want, vid, pid, name):
    w = (want or "").strip().lower()
    if ":" in w:
        try:
            v, p = (int(x, 16) for x in w.split(":", 1))
            return (vid, pid) == (v, p)
        except ValueError:
            return False
    return vid == ROLAND_VID and (not w or w in (name or "").lower())


class UsbBackend:
    """Zelfde vorm als midi.MidoBackend: names() → (ins, outs), open(in, out) → poort met poll/send_sysex/close.

    `finder` (tests): functie die een lijst nep-apparaten geeft met dezelfde methodes als
    _UsbDev hieronder (name, vid, pid, read, write, close).
    """

    def __init__(self, finder=None):
        if finder is None:
            import usb.core  # noqa: F401  (ImportError → USB direct niet beschikbaar)
            self._usb = usb
            self._backend = _libusb_backend()
            finder = self._find
        self.finder = finder
        self._devs = {}

    def _find(self):
        res = []
        for d in self._usb.core.find(find_all=True, idVendor=ROLAND_VID, backend=self._backend) or []:
            res.append(_UsbDev(self._usb, d))
        return res

    def names(self):
        self._devs = {}
        for d in self.finder():
            key = "%s (USB %04x:%04x)" % (d.name or "Roland", d.vid, d.pid)
            self._devs[key] = d
        names = list(self._devs)
        return names, names

    def match(self, want):
        """Eerste gevonden apparaat dat bij het filter past (naam of vid:pid)."""
        for key, d in self._devs.items():
            if _filter_match(want, d.vid, d.pid, d.name):
                return key
        # productnaam niet leesbaar (rechten) en precies één Roland-apparaat zonder naam: dat nemen
        noname = [k for k, d in self._devs.items() if d.vid == ROLAND_VID and not d.name]
        if ":" not in (want or "") and len(noname) == 1:
            return noname[0]
        return None

    def open(self, in_name, out_name=None):
        d = self._devs.get(in_name)
        if d is None:
            raise OSError("apparaat niet meer gevonden")
        d.open()
        return _UsbPort(d)


def _libusb_backend():
    """libusb uit het package libusb-package (meegeleverd), anders wat pyusb zelf vindt."""
    try:
        import libusb_package
        return libusb_package.get_libusb1_backend()
    except Exception:  # noqa: BLE001
        return None


class _UsbDev:
    """Eén echt USB-apparaat: interface met bulk IN + OUT zoeken en claimen."""

    def __init__(self, usb, dev):
        self.usb, self.dev = usb, dev
        self.vid, self.pid = dev.idVendor, dev.idProduct
        try:
            self.name = usb.util.get_string(dev, dev.iProduct) if dev.iProduct else ""
        except Exception:  # noqa: BLE001  (geen rechten om strings te lezen)
            self.name = ""
        self.ep_in = self.ep_out = None
        self.intf = None
        self._detached = False

    def open(self):
        u, d = self.usb, self.dev
        try:
            d.get_active_configuration()
        except u.core.USBError:
            d.set_configuration()
        cfg = d.get_active_configuration()
        # interface met een bulk- of interrupt-IN én -OUT (MIDI-streaming), voorkeur voor bulk
        best = None
        for intf in cfg:
            eps = list(intf)
            ein = [e for e in eps if u.util.endpoint_direction(e.bEndpointAddress) == u.util.ENDPOINT_IN
                   and u.util.endpoint_type(e.bmAttributes) in (u.util.ENDPOINT_TYPE_BULK, u.util.ENDPOINT_TYPE_INTR)]
            eout = [e for e in eps if u.util.endpoint_direction(e.bEndpointAddress) == u.util.ENDPOINT_OUT
                    and u.util.endpoint_type(e.bmAttributes) in (u.util.ENDPOINT_TYPE_BULK, u.util.ENDPOINT_TYPE_INTR)]
            if ein and eout:
                score = (intf.bInterfaceClass == 1 and intf.bInterfaceSubClass == 3) * 2 + (intf.bInterfaceClass == 0xFF)
                if best is None or score > best[0]:
                    best = (score, intf, ein[0], eout[0])
        if not best:
            raise OSError("geen MIDI-endpoints gevonden op dit USB-apparaat")
        _, intf, self.ep_in, self.ep_out = best
        n = intf.bInterfaceNumber
        try:
            if d.is_kernel_driver_active(n):
                d.detach_kernel_driver(n)
                self._detached = True
        except (NotImplementedError, u.core.USBError):
            pass
        try:
            if intf.bAlternateSetting:
                d.set_interface_altsetting(n, intf.bAlternateSetting)
        except u.core.USBError:
            pass
        try:
            u.util.claim_interface(d, n)
        except u.core.USBError as e:
            raise OSError("geen toegang tot de USB-interface (%s). Gebruikt een andere driver of app de mixer al?" % e)
        self.intf = n

    def read(self, timeout_ms=5):
        try:
            return bytes(self.ep_in.read(max(64, self.ep_in.wMaxPacketSize), timeout=timeout_ms))
        except self.usb.core.USBTimeoutError:
            return b""
        except self.usb.core.USBError as e:
            if getattr(e, "errno", None) in (110, 60) or "timed out" in str(e).lower():
                return b""
            raise

    def write(self, data):
        self.ep_out.write(data, timeout=500)

    def close(self):
        try:
            if self.intf is not None:
                self.usb.util.release_interface(self.dev, self.intf)
                if self._detached:
                    try:
                        self.dev.attach_kernel_driver(self.intf)
                    except Exception:  # noqa: BLE001
                        pass
            self.usb.util.dispose_resources(self.dev)
        except Exception:  # noqa: BLE001
            pass
        self.intf = None


class _UsbPort:
    """Zelfde interface als midi._MidoPort."""

    def __init__(self, dev):
        self.dev = dev
        self.parser = UsbMidiParser()

    def poll(self):
        res = []
        for _ in range(16):              # hooguit 16 leesacties per ronde
            data = self.dev.read()
            if not data:
                break
            for d, raw in self.parser.feed(data):
                if d.get("type") in ("active_sensing", "clock"):
                    continue
                res.append((d, raw))
        return res

    def send_sysex(self, data):
        self.dev.write(sysex_packets(data))

    def close(self):
        self.dev.close()
