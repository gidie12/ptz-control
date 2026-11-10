"""Nagebootste Roland V-1HD voor tests en screenshots (geen echte MIDI nodig).

Gedrag (zoals uit de Remote Control Guide, ⚠️ lampjes-codering verzonnen):
- press(bus, n): stuurt CC0/CC32 + program change, zoals een knopdruk op de mixer;
- antwoordt op RQ1 voor 71 00 00 (fader + video sel A/B) en 73 02 00 (lampjes) met DT1.

Gebruik in de server: omgevingsvariabele PTZ_MIDI_FAKE=<begin-ingang A-rij>, bv. PTZ_MIDI_FAKE=2.
"""
import collections
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import roland_v1hd as rv  # noqa: E402

GREEN, RED = 0x01, 0x02   # ⚠️ verzonnen waarden


class FakeV1HD:
    def __init__(self, a=1, b=1, name="V-1HD"):
        self.name = name
        self.sel = {"A": a, "B": b}
        self.q = collections.deque()
        self.lock = threading.Lock()
        self.sent = []

    def press(self, bus, n):
        with self.lock:
            self.sel[bus] = n
            msb = 0 if bus == "A" else 1
            self.q.append(({"type": "control_change", "channel": 0, "control": 0, "value": msb}, [0xB0, 0, msb]))
            self.q.append(({"type": "control_change", "channel": 0, "control": 32, "value": 0}, [0xB0, 32, 0]))
            self.q.append(({"type": "program_change", "channel": 0, "program": n - 1}, [0xC0, n - 1]))

    def leds(self):
        led = [0] * rv.LED_SIZE
        for i in range(4):   # ⚠️ aangenomen: A-1..4 op 00..03, B-1..4 op 04..07
            led[i] = RED if self.sel["A"] == i + 1 else GREEN
            led[4 + i] = GREEN
        return led

    def answer(self, data):
        d = list(data)
        if len(d) < 13 or d[6] != rv.RQ1:
            return
        addr = tuple(d[7:10])
        if addr == rv.ADDR_VIDEO:
            vals = [0, 0] + [0] * 6 + [self.sel["A"] - 1, self.sel["B"] - 1]
        elif addr == rv.ADDR_LED:
            vals = self.leds()
        else:
            return
        msg = rv.dt1(addr, vals, d[1])
        self.q.append(({"type": "sysex", "data": msg}, [0xF0] + msg + [0xF7]))


class FakeBackend:
    def __init__(self, dev):
        self.dev = dev

    def names(self):
        return [self.dev.name + " MIDI"], [self.dev.name + " MIDI"]

    def open(self, i, o):
        return _Port(self.dev)


class _Port:
    def __init__(self, dev):
        self.dev = dev

    def poll(self):
        with self.dev.lock:
            out = list(self.dev.q)
            self.dev.q.clear()
        return out

    def send_sysex(self, data):
        with self.dev.lock:
            self.dev.sent.append(list(data))
            self.dev.answer(data)

    def close(self):
        pass
