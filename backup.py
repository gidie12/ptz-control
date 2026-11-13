"""
Back-up en herstel voor PTZ Control.

1. Back-upbestand: cameralijst, presetnamen, volg-instellingen, thumbnails en
   (indien uitgelezen) presetposities en beeldinstellingen, in één JSON-bestand.
2. Presetposities: elke preset oproepen, wachten tot de camera stilstaat, pan/tilt/zoom/focus
   uitlezen via VISCA. Terugzetten = absoluut naar die positie rijden en de preset opnieuw opslaan.
3. Beeldinstellingen: belichting, witbalans, kleur, scherpte, flip/mirror via VISCA uitlezen en terugzetten.

Alles via VISCA over TCP (standaard poort 5678), ook bij camera's die verder HTTP-CGI gebruiken.
LET OP: uitlezen en terugzetten van presetposities laat de camera's bewegen.
"""
import socket
import time

TIMEOUT = 2.0

# PTZOptics: 0 = opstartpositie, 90 = home, 91-94/96-99 gereserveerd, 95 = OSD-menu aan/uit.
# Normale presets: 1-89 en 100-254. Deze nummers roepen we nooit op en slaan we nooit op.
RESERVED = set(range(90, 100))


def usable(n):
    return 1 <= n <= 254 and n not in RESERVED


# ---------------------------------------------------------------- VISCA laag
class ViscaError(Exception):
    pass


def _host(cam):
    return cam["host"].split(":")[0]


def visca_session(cam):
    return socket.create_connection((_host(cam), int(cam.get("visca_port") or 5678)), timeout=TIMEOUT)


def _messages(sock, deadline):
    """Lees VISCA-berichten (afgesloten met FF) tot de deadline."""
    buf = b""
    while time.time() < deadline:
        sock.settimeout(max(0.05, deadline - time.time()))
        try:
            chunk = sock.recv(256)
        except socket.timeout:
            return
        if not chunk:
            return
        buf += chunk
        while b"\xff" in buf:
            msg, buf = buf.split(b"\xff", 1)
            yield msg + b"\xff"


def visca_cmd(sock, hexstr, wait=0.6):
    """Stuur een commando; geeft False bij een foutmelding van de camera."""
    sock.sendall(bytes.fromhex(hexstr.replace(" ", "")))
    deadline = time.time() + wait
    for msg in _messages(sock, deadline):
        if len(msg) >= 3 and msg[1] & 0xF0 == 0x60:   # 90 6x = fout
            return False
        if len(msg) >= 2 and msg[1] & 0xF0 == 0x50:   # 90 5x = klaar
            return True
    return True  # sommige camera's sturen geen completion; niet als fout zien


def visca_inq(sock, hexstr):
    """Stuur een inquiry en geef de payload-bytes terug (tussen 'y0 50' en 'FF')."""
    sock.sendall(bytes.fromhex(hexstr.replace(" ", "")))
    for msg in _messages(sock, time.time() + TIMEOUT):
        if len(msg) >= 3 and msg[1] == 0x50:
            return msg[2:-1]
        if len(msg) >= 3 and msg[1] & 0xF0 == 0x60:
            raise ViscaError(f"camera weigert {hexstr}")
    raise ViscaError(f"geen antwoord op {hexstr}")


def nib(payload):
    """'0p 0q 0r 0s' -> getal."""
    v = 0
    for b in payload:
        v = (v << 4) | (b & 0x0F)
    return v


def to_nib(value, count):
    value &= (1 << (4 * count)) - 1
    return " ".join(f"0{(value >> (4 * (count - 1 - i))) & 0xF:x}" for i in range(count))


def signed(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


# ---------------------------------------------------------------- positie
def read_pan(sock):
    """Alleen de panpositie (één inquiry), voor de grenzen bij volgen."""
    pt = visca_inq(sock, "81 09 06 12 FF")
    pn = 5 if len(pt) >= 9 else 4
    return signed(nib(pt[:pn]), 4 * pn)


def read_position(sock):
    pt = visca_inq(sock, "81 09 06 12 FF")
    n = len(pt)
    pn = 5 if n >= 9 else 4          # sommige camera's geven pan met 5 nibbles
    tn = n - pn
    pos = {
        "pan": signed(nib(pt[:pn]), 4 * pn),
        "tilt": signed(nib(pt[pn:]), 4 * tn),
        "pt_nibbles": [pn, tn],
        "zoom": nib(visca_inq(sock, "81 09 04 47 FF")),
    }
    try:
        pos["af"] = visca_inq(sock, "81 09 04 38 FF")[0] == 0x02
        pos["focus"] = nib(visca_inq(sock, "81 09 04 48 FF"))
    except ViscaError:
        pass
    return pos


def wait_still(sock, timeout=10.0, min_time=0.8):
    """Wacht tot pan/tilt/zoom/focus twee keer achter elkaar gelijk zijn."""
    start = time.time()
    last = None
    while time.time() - start < timeout:
        time.sleep(0.35)
        try:
            p = read_position(sock)
        except ViscaError:
            continue
        key = (p["pan"], p["tilt"], p["zoom"], p.get("focus"))   # ook focus moet stilstaan
        if key == last and time.time() - start >= min_time:
            return p
        last = key
    return read_position(sock)


def goto_position(sock, pos):
    pn, tn = pos.get("pt_nibbles", [4, 4])
    visca_cmd(sock, f"81 01 06 02 14 12 {to_nib(pos['pan'], pn)} {to_nib(pos['tilt'], tn)} FF")
    visca_cmd(sock, f"81 01 04 47 {to_nib(pos['zoom'], 4)} FF")
    if "focus" in pos and not pos.get("af", True):
        visca_cmd(sock, "81 01 04 38 03 FF")             # handmatige focus
        visca_cmd(sock, f"81 01 04 48 {to_nib(pos['focus'], 4)} FF")
    elif pos.get("af"):
        visca_cmd(sock, "81 01 04 38 02 FF")             # autofocus


# ---------------------------------------------------------------- beeldinstellingen
# (sleutel, label, inquiry, soort, set-sjabloon)  soort: byte = 1 byte | nib = 00 00 0p 0q | onoff = 02/03
IMAGE = [
    ("ae_mode",   "Belichtingsmodus",   "81 09 04 39 FF", "byte",  "81 01 04 39 {b} FF"),
    ("shutter",   "Sluitertijd",        "81 09 04 4A FF", "nib",   "81 01 04 4A {n} FF"),
    ("iris",      "Diafragma",          "81 09 04 4B FF", "nib",   "81 01 04 4B {n} FF"),
    ("gain",      "Gain",               "81 09 04 4C FF", "nib",   "81 01 04 4C {n} FF"),
    ("bright",    "Helderheid",         "81 09 04 4D FF", "nib",   "81 01 04 4D {n} FF"),
    ("expcomp_on", "Belichtingscorr. aan", "81 09 04 3E FF", "onoff", "81 01 04 3E {o} FF"),
    ("expcomp",   "Belichtingscorrectie", "81 09 04 4E FF", "nib", "81 01 04 4E {n} FF"),
    ("backlight", "Tegenlicht",         "81 09 04 33 FF", "onoff", "81 01 04 33 {o} FF"),
    ("wb_mode",   "Witbalansmodus",     "81 09 04 35 FF", "byte",  "81 01 04 35 {b} FF"),
    ("r_gain",    "Rood (witbalans)",   "81 09 04 43 FF", "nib",   "81 01 04 43 {n} FF"),
    ("b_gain",    "Blauw (witbalans)",  "81 09 04 44 FF", "nib",   "81 01 04 44 {n} FF"),
    ("aperture",  "Scherpte",           "81 09 04 42 FF", "nib",   "81 01 04 42 {n} FF"),
    ("saturation", "Verzadiging",       "81 09 04 49 FF", "nib",   "81 01 04 49 {n} FF"),
    ("flip",      "Beeld omdraaien",    "81 09 04 66 FF", "onoff", "81 01 04 66 {o} FF"),
    ("mirror",    "Spiegelen",          "81 09 04 61 FF", "onoff", "81 01 04 61 {o} FF"),
]
IMAGE_LABELS = {k: label for k, label, *_ in IMAGE}


def read_image(sock):
    out, missing = {}, []
    for key, _label, inq, kind, _set in IMAGE:
        try:
            p = visca_inq(sock, inq)
            out[key] = p[0] if kind in ("byte", "onoff") else nib(p[-4:])
        except ViscaError:
            missing.append(key)
    return out, missing


def apply_image(sock, values):
    """Modi eerst (anders worden handmatige waarden genegeerd), daarna de waarden."""
    failed = []
    order = [k for k in ("ae_mode", "wb_mode", "expcomp_on") if k in values] + \
            [k for k, *_ in IMAGE if k in values and k not in ("ae_mode", "wb_mode", "expcomp_on")]
    spec = {k: (kind, tpl) for k, _l, _i, kind, tpl in IMAGE}
    for key in order:
        kind, tpl = spec[key]
        v = int(values[key])
        cmd = tpl.format(b=f"{v:02x}", o=f"{v:02x}", n=f"00 00 {to_nib(v, 2)}")
        if not visca_cmd(sock, cmd):
            failed.append(key)   # bv. sluitertijd in automatische modus: normaal
    return failed


# ---------------------------------------------------------------- taken per camera
def first_usable(count):
    """De eerste `count` bruikbare presetnummers (zo telt de interface de tegels)."""
    return [n for n in range(1, 255) if usable(n)][:max(0, count)]


def count_upto(n):
    """Aantal tegels nodig om preset n te tonen."""
    return sum(1 for k in range(1, n + 1) if usable(k))


def preset_list(cam, preset_count, mode):
    if mode == "all":
        return first_usable(preset_count)
    used = set(int(n) for n in (cam.get("presets") or {}).keys())
    used |= set(cam.get("thumbs") or [])
    used |= set(int(n) for n in (cam.get("saved_positions") or {}).keys())
    return sorted(n for n in used if usable(n)) or first_usable(preset_count)


def capture_camera(cam, presets, do_positions, do_image, progress, stamp, on_preset=None):
    """Leest posities/beeld van één camera. Geeft (positions, image, fouten) terug.
    Elke positie krijgt 'moved': of de camera bij het oproepen echt bewoog (zo niet: preset waarschijnlijk leeg).
    on_preset(n, pos) wordt na elke aangekomen preset aangeroepen (bv. om een foto te maken)."""
    errors, positions, image = [], {}, None
    presets = [n for n in presets if usable(n)]
    with visca_session(cam) as s:
        if do_image:
            progress("beeldinstellingen lezen…")
            image, missing = read_image(s)
            image["_captured"] = stamp
            if missing:
                errors.append("niet ondersteund: " + ", ".join(IMAGE_LABELS[m] for m in missing))
        if do_positions:
            try:
                prev = read_position(s)
            except ViscaError:
                prev = None
            for i, n in enumerate(presets, 1):
                progress(f"preset {n} ({i}/{len(presets)})")
                if not visca_cmd(s, f"81 01 04 3F 02 {n:02x} FF"):
                    errors.append(f"preset {n}: oproepen geweigerd")
                    continue
                try:
                    pos = wait_still(s)
                    key = lambda p: (p["pan"], p["tilt"], p["zoom"])  # noqa: E731
                    pos["moved"] = prev is None or key(pos) != key(prev)
                    pos["_captured"] = stamp
                    positions[str(n)] = pos
                    prev = pos
                    if on_preset:
                        on_preset(n, pos)
                except ViscaError as e:
                    errors.append(f"preset {n}: {e}")
    return positions, image, errors


def restore_camera(cam, do_positions, do_image, progress):
    errors = []
    saved_pos = cam.get("saved_positions") or {}
    saved_img = cam.get("saved_image") or {}
    with visca_session(cam) as s:
        if do_positions:
            items = sorted(((n, p) for n, p in saved_pos.items() if usable(int(n))), key=lambda kv: int(kv[0]))
            for i, (n, pos) in enumerate(items, 1):
                progress(f"preset {n} terugzetten ({i}/{len(items)})")
                try:
                    goto_position(s, pos)
                    wait_still(s)
                    if not visca_cmd(s, f"81 01 04 3F 01 {int(n):02x} FF"):
                        errors.append(f"preset {n}: opslaan geweigerd")
                except (ViscaError, OSError) as e:
                    errors.append(f"preset {n}: {e}")
        if do_image and saved_img:
            progress("beeldinstellingen terugzetten…")
            values = {k: v for k, v in saved_img.items() if not k.startswith("_")}
            failed = apply_image(s, values)
            if failed:
                errors.append("niet toegepast (kan normaal zijn in automatische modus): "
                              + ", ".join(IMAGE_LABELS.get(f, f) for f in failed))
    return errors


# ---------------------------------------------------------------- presetaantal testen
CAP_PROBES = [1, 32, 64, 89, 100, 128, 150, 200, 254]
CAP_TOL = 12          # toegestane afwijking (pan/tilt-eenheden) bij terugkomen
CAP_OFFSET = 220      # zo ver draait de camera opzij voor de testpositie


def _goto_pt(sock, pan, tilt, nibbles, zoom=None):
    pn, tn = nibbles
    visca_cmd(sock, f"81 01 06 02 14 12 {to_nib(pan, pn)} {to_nib(tilt, tn)} FF")
    if zoom is not None:
        visca_cmd(sock, f"81 01 04 47 {to_nib(zoom, 4)} FF")
    return wait_still(sock)


def test_preset_slot(sock, n):
    """Test of preset n kan worden opgeslagen en opgeroepen; herstelt een bestaande preset.
    Geeft dict: ok, note, had_preset, left_test (testpositie blijft staan onder dit nummer)."""
    res = {"n": n, "ok": False, "note": "", "had_preset": False, "left_test": False}
    key = lambda p: (p["pan"], p["tilt"], p["zoom"])  # noqa: E731
    before = read_position(sock)
    if not visca_cmd(sock, f"81 01 04 3F 02 {n:02x} FF"):
        res["note"] = "oproepen geweigerd door camera"
        return res
    old = wait_still(sock)
    res["had_preset"] = key(old) != key(before)
    nib = old.get("pt_nibbles", [4, 4])
    # 1) testpositie opzij en opslaan
    target = _goto_pt(sock, old["pan"] + CAP_OFFSET, old["tilt"], nib)
    if not visca_cmd(sock, f"81 01 04 3F 01 {n:02x} FF"):
        res["note"] = "opslaan geweigerd door camera"
        _goto_pt(sock, old["pan"], old["tilt"], nib)
        return res
    # 2) andere kant op, dan terugroepen
    away = _goto_pt(sock, old["pan"] - CAP_OFFSET, old["tilt"], nib)
    if abs(away["pan"] - target["pan"]) <= 3 * CAP_TOL:
        away = _goto_pt(sock, old["pan"], old["tilt"] + CAP_OFFSET // 2, nib)   # tegen een eindstop: via tilt
    visca_cmd(sock, f"81 01 04 3F 02 {n:02x} FF")
    back = wait_still(sock)
    hit = abs(back["pan"] - target["pan"]) <= CAP_TOL and abs(back["tilt"] - target["tilt"]) <= CAP_TOL
    moved_back = abs(back["pan"] - away["pan"]) > CAP_TOL or abs(back["tilt"] - away["tilt"]) > CAP_TOL
    res["ok"] = hit
    if not hit:
        res["note"] = "camera kwam niet terug op de teststand" if moved_back else "camera reageerde niet (preset niet ondersteund)"
    # 3) herstellen
    if res["had_preset"]:
        _goto_pt(sock, old["pan"], old["tilt"], nib, zoom=old["zoom"])
        if visca_cmd(sock, f"81 01 04 3F 01 {n:02x} FF"):
            res["note"] = (res["note"] + "; " if res["note"] else "") + "bestaande preset teruggezet"
        else:
            res["note"] = (res["note"] + "; " if res["note"] else "") + "LET OP: bestaande preset kon niet worden teruggezet"
    elif hit:
        # was leeg: testpositie weer wissen (VISCA Memory Reset)
        if visca_cmd(sock, f"81 01 04 3F 00 {n:02x} FF"):
            res["note"] = "was leeg; testpositie weer gewist"
        else:
            res["left_test"] = True
            res["note"] = "was leeg; wissen niet ondersteund, bevat nu een testpositie"
    return res


def capacity_test(cam, progress):
    """Vindt het hoogste werkende presetnummer: eerst grenswaarden, daarna halveren."""
    results = {}
    with visca_session(cam) as s:
        start = read_position(s)

        def run(n):
            if n in results or not usable(n):
                return results.get(n, {}).get("ok", False)
            progress(f"preset {n} testen… ({len(results) + 1})")
            try:
                results[n] = test_preset_slot(s, n)
            except ViscaError as e:
                results[n] = {"n": n, "ok": False, "note": str(e), "had_preset": False, "left_test": False}
            return results[n]["ok"]

        for n in CAP_PROBES:
            run(n)
        oks = [n for n in results if results[n]["ok"]]
        best = max(oks) if oks else 0
        fails_above = sorted(n for n in results if not results[n]["ok"] and n > best)
        if fails_above:
            lo, hi = best, fails_above[0]          # lo werkt (of 0), hi faalt
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if not usable(mid):                # 90-99 overslaan
                    mid = 89 if mid < 95 else 100
                    if mid <= lo or mid >= hi:
                        break
                if run(mid):
                    lo = mid
                else:
                    hi = mid
            best = lo
        _goto_pt(s, start["pan"], start["tilt"], start.get("pt_nibbles", [4, 4]), zoom=start["zoom"])
    total = sum(1 for n in range(1, best + 1) if usable(n))
    gaps = sorted(n for n in results if n < best and not results[n]["ok"])
    return {"max": best, "total": total, "gaps": gaps,
            "tested": [results[n] for n in sorted(results)]}
