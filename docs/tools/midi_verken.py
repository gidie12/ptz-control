"""Verkenning: luister naar de Roland V-1HD via MIDI en laat zien wat hij stuurt.

Gebruik (op de Mac mini, V-1HD met USB aan de Mac):
    .venv/bin/pip install mido python-rtmidi
    .venv/bin/python docs/tools/midi_verken.py --lijst
    .venv/bin/python docs/tools/midi_verken.py                # poort met "V-1HD" in de naam
    .venv/bin/python docs/tools/midi_verken.py --poort "V-1HD" --vraag 1 --log v1hd.txt

Druk daarna op de mixer op ingangsknoppen, beweeg de A/B-fader, kies memories.
Het script toont elk bericht ruw én ontleed. Met --vraag N vraagt het elke N s
via SysEx de fader, video sel A/B en de knoplampjes op en toont wijzigingen.
Stuur de log terug: daarmee bepalen we hoe 'live' betrouwbaar te herkennen is.

Er wordt niets naar de mixer gestuurd behalve leesverzoeken (RQ1), tenzij je
--test-kies gebruikt (zet A-bus op ingang 1..4, één keer).
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import roland_v1hd as rv  # noqa: E402

try:
    import mido
except ImportError:
    sys.exit("mido ontbreekt: pip install mido python-rtmidi")


def hexs(b):
    return " ".join("%02X" % x for x in b)


def kies_poort(namen, zoek):
    for n in namen:
        if zoek.lower() in n.lower():
            return n
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lijst", action="store_true", help="toon MIDI-poorten en stop")
    ap.add_argument("--poort", default="V-1HD", help="(deel van) de poortnaam, standaard V-1HD")
    ap.add_argument("--dev", type=lambda s: int(s, 0), default=rv.DEFAULT_DEV,
                    help="SysEx device-ID (standaard 0x10 = 17 in het menu)")
    ap.add_argument("--vraag", type=float, default=0, help="elke N seconden de stand opvragen (0 = uit)")
    ap.add_argument("--log", help="schrijf alles ook naar dit bestand")
    ap.add_argument("--test-kies", type=int, metavar="N", help="zet A-bus één keer op ingang N (1-4)")
    a = ap.parse_args()

    ins, outs = mido.get_input_names(), mido.get_output_names()
    if a.lijst:
        print("Ingangen:", *ins or ["(geen)"], sep="\n  ")
        print("Uitgangen:", *outs or ["(geen)"], sep="\n  ")
        return
    pin, pout = kies_poort(ins, a.poort), kies_poort(outs, a.poort)
    if not pin:
        sys.exit("Geen MIDI-ingang met '%s'. Gevonden: %s" % (a.poort, ins))
    logf = open(a.log, "a", encoding="utf-8") if a.log else None

    def say(s):
        line = "%s  %s" % (time.strftime("%H:%M:%S"), s)
        print(line, flush=True)
        if logf:
            logf.write(line + "\n")
            logf.flush()

    st = rv.V1HDState()
    say("Luisteren op '%s'%s" % (pin, (", versturen naar '%s'" % pout) if pout else " (geen uitgang gevonden)"))
    out = mido.open_output(pout) if pout else None

    if a.test_kies and out:
        n = max(1, min(4, a.test_kies)) - 1
        for m in (mido.Message("control_change", control=0, value=0),
                  mido.Message("control_change", control=32, value=0),
                  mido.Message("program_change", program=n)):
            out.send(m)  # ⚠️ op MIDI Rx CHANNEL 1 (mido-kanaal 0)
        say("Verstuurd: A-bus ingang %d (kanaal 1)" % (n + 1))

    vorige_leds = None
    volgende = 0
    with mido.open_input(pin) as inp:
        try:
            while True:
                for msg in inp.iter_pending():
                    if msg.type in ("active_sensing", "clock"):
                        continue
                    d = msg.dict()
                    raw = hexs(msg.bytes())
                    uitleg = st.feed(d)
                    say("%-40s %s" % (raw, uitleg or msg))
                    if st.leds and st.leds != vorige_leds:
                        if vorige_leds:
                            diff = ["%02X:%02X→%02X" % (i, o, n) for i, (o, n) in
                                    enumerate(zip(vorige_leds, st.leds)) if o != n]
                            say("  lampjes gewijzigd: " + " ".join(diff))
                        else:
                            say("  lampjes 73 02 00..18: " + hexs(st.leds))
                        vorige_leds = list(st.leds)
                    live = st.live()
                    if live and uitleg:
                        say("  → vermoedelijk live: %s (A=%s B=%s)" % (live, st.sel["A"], st.sel["B"]))
                if a.vraag and out and time.time() >= volgende:
                    out.send(mido.Message("sysex", data=rv.rq1(rv.ADDR_VIDEO, rv.VIDEO_SIZE, a.dev)))
                    out.send(mido.Message("sysex", data=rv.rq1(rv.ADDR_LED, rv.LED_SIZE, a.dev)))
                    volgende = time.time() + a.vraag
                time.sleep(0.01)
        except KeyboardInterrupt:
            say("Gestopt.")


if __name__ == "__main__":
    main()
