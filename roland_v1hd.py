"""Roland V-1HD via MIDI: berichten opbouwen en ontleden (verkenning).

Alleen standaard-Python; de MIDI-poort zelf (mido/python-rtmidi) zit in
docs/tools/midi_verken.py. Bron: Roland "V-1HD Remote Control Guide" (2015).

Aannames (⚠️ niet getest op de echte mixer):
- Model-ID 00 00 00 20, standaard device-ID 10H.
- Bij een druk op een ingangsknop stuurt de V-1HD (MIDI OUT = OUT, en altijd
  via USB) CC0 (bank MSB) + CC32 (LSB) + program change:
  bank 00 = A-bus ingang 1-4, bank 01 = B-bus ingang 1-4, bank 50H = memory 1-8.
- CC17 = A/B-fader. Welke kant 0 is (A of B) is nog onbekend.
- Via SysEx RQ1 op 71 00 00 (fader, 14-bit) en 71 00 08/09 (video sel A/B)
  en 73 02 00-18 (knoplampjes) is de huidige stand op te vragen.
"""

ROLAND = 0x41
MODEL = (0x00, 0x00, 0x00, 0x20)
RQ1, DT1 = 0x11, 0x12
DEFAULT_DEV = 0x10

ADDR_VIDEO = (0x71, 0x00, 0x00)   # fader (00-01), video sel A (08), B (09)
VIDEO_SIZE = 0x0A
ADDR_LED = (0x73, 0x02, 0x00)     # lampjes van de knoppen, alleen lezen
LED_SIZE = 0x19
ADDR_MEMSEL = (0x73, 0x01, 0x00)

BUS = {0x00: "A", 0x01: "B"}
BANK_MEMORY = 0x50


def checksum(data):
    """Roland-checksum over adres + data/grootte."""
    r = sum(data) % 128
    return 0 if r == 0 else 128 - r


def rq1(addr, size, dev=DEFAULT_DEV):
    """SysEx-verzoek (zonder F0/F7, zoals mido het wil)."""
    s = ((size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F)
    body = list(addr) + list(s)
    return [ROLAND, dev, *MODEL, RQ1, *body, checksum(body)]


def dt1(addr, data, dev=DEFAULT_DEV):
    body = list(addr) + list(data)
    return [ROLAND, dev, *MODEL, DT1, *body, checksum(body)]


def parse_sysex(data):
    """Ontleed een DT1-antwoord (zonder F0/F7). Geeft (adres, bytes) of None."""
    d = list(data)
    if len(d) < 11 or d[0] != ROLAND or tuple(d[2:6]) != MODEL or d[6] != DT1:
        return None
    body, chk = d[7:-1], d[-1]
    if checksum(body) != chk:
        return None
    return tuple(body[:3]), body[3:]


def addr_add(addr, n):
    """Adres + n (7-bit per byte)."""
    v = (addr[0] << 14) | (addr[1] << 7) | addr[2]
    v += n
    return ((v >> 14) & 0x7F, (v >> 7) & 0x7F, v & 0x7F)


class V1HDState:
    """Houdt bij wat de mixer heeft gemeld (berichten uit de knoppen én SysEx).

    feed() krijgt mido-achtige dicts: {"type": "control_change", "control", "value"},
    {"type": "program_change", "program"}, {"type": "sysex", "data"}.
    Geeft een korte Nederlandse omschrijving terug van wat er veranderde (of None).
    """

    def __init__(self):
        self.bank = (0, 0)          # laatst ontvangen bank select (MSB, LSB)
        self.sel = {"A": None, "B": None}   # ingang 1-4 per bus
        self.fader = None           # 0-127 (CC17) of 0-16383 (SysEx, zie fader14)
        self.fader14 = None
        self.memory = None
        self.leds = None            # ruwe bytes 73 02 00..18
        self.mem = {}               # adres -> waarde, voor alle SysEx-antwoorden

    def feed(self, msg):
        t = msg.get("type")
        if t == "control_change":
            c, v = msg["control"], msg["value"]
            if c == 0:
                self.bank = (v, self.bank[1])
            elif c == 32:
                self.bank = (self.bank[0], v)
            elif c == 17:
                self.fader = v
                return "fader %d" % v
            elif c == 19:
                return "overgang: %s" % {0: "WIPE", 1: "MIX", 2: "CUT"}.get(v, v)
            return None
        if t == "program_change":
            p, msb = msg["program"], self.bank[0]
            if msb in BUS and p < 4:
                bus = BUS[msb]
                self.sel[bus] = p + 1
                return "%s-bus: ingang %d" % (bus, p + 1)
            if msb == BANK_MEMORY and p < 8:
                self.memory = p + 1
                return "memory %d" % (p + 1)
            return "program change %d (bank %d/%d)" % (p, msb, self.bank[1])
        if t == "sysex":
            r = parse_sysex(msg["data"])
            if not r:
                return None
            addr, data = r
            for i, b in enumerate(data):
                self.mem[addr_add(addr, i)] = b
            return self._apply_mem()
        return None

    def _apply_mem(self):
        m = self.mem
        out = []
        a0, a1 = ADDR_VIDEO[:2]
        f = (m.get((a0, a1, 0x00)), m.get((a0, a1, 0x01)))
        if None not in f:
            # ⚠️ "14-bit": aangenomen 2× 7 bits (MSB, LSB). Kan ook nibbles zijn.
            self.fader14 = (f[0] << 7) | f[1]
            out.append("fader14 %d" % self.fader14)
        for bus, off in (("A", 0x08), ("B", 0x09)):
            v = m.get((a0, a1, off))
            if v is not None:
                self.sel[bus] = v + 1
                out.append("%s=%d" % (bus, v + 1))
        leds = [m.get(addr_add(ADDR_LED, i)) for i in range(LED_SIZE)]
        if None not in leds:
            self.leds = leds
        return ", ".join(out) or None

    def live(self, live_bus="A", a_is_zero=True):
        """Welke ingang staat (vermoedelijk) op program.

        🧑 De fader wordt in de kerk nooit gebruikt: hij staat vast aan één kant,
        dus één rij (live_bus) is altijd program en een knop daarin = direct
        schakelen. Is de faderstand wel bekend (SysEx/CC17), dan gaat die voor.
        ⚠️ Fader helemaal aan één kant = die bus live; ertussen → beide.
        a_is_zero: fader 0 = A (nog niet gecontroleerd).
        """
        if self.fader14 is not None:
            pos = self.fader14 / 16383.0
        elif self.fader is not None:
            pos = self.fader / 127.0
        else:
            v = self.sel.get(live_bus)
            return [v] if v else None
        if not a_is_zero:
            pos = 1 - pos
        if pos <= 0.02:
            return [self.sel["A"]]
        if pos >= 0.98:
            return [self.sel["B"]]
        return [self.sel["A"], self.sel["B"]]
