"""
Back-up en herstel voor PTZ Control.

1. Back-upbestand: cameralijst, presetnamen, scènes, volg-instellingen, thumbnails en
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
def preset_list(cam, preset_count, mode):
    if mode == "all":
        return list(range(1, preset_count + 1))
    used = set(int(n) for n in (cam.get("presets") or {}).keys())
    used |= set(cam.get("thumbs") or [])
    used |= set(int(n) for n in (cam.get("saved_positions") or {}).keys())
    return sorted(n for n in used if n >= 1) or list(range(1, preset_count + 1))


def capture_camera(cam, presets, do_positions, do_image, progress, stamp):
    """Leest posities/beeld van één camera. Geeft (positions, image, fouten) terug."""
    errors, positions, image = [], {}, None
    with visca_session(cam) as s:
        if do_image:
            progress("beeldinstellingen lezen…")
            image, missing = read_image(s)
            image["_captured"] = stamp
            if missing:
                errors.append("niet ondersteund: " + ", ".join(IMAGE_LABELS[m] for m in missing))
        if do_positions:
            for i, n in enumerate(presets, 1):
                progress(f"preset {n} ({i}/{len(presets)})")
                if not visca_cmd(s, f"81 01 04 3F 02 {n:02x} FF"):
                    errors.append(f"preset {n}: oproepen geweigerd")
                    continue
                try:
                    pos = wait_still(s)
                    pos["_captured"] = stamp
                    positions[str(n)] = pos
                except ViscaError as e:
                    errors.append(f"preset {n}: {e}")
    return positions, image, errors


def restore_camera(cam, do_positions, do_image, progress):
    errors = []
    saved_pos = cam.get("saved_positions") or {}
    saved_img = cam.get("saved_image") or {}
    with visca_session(cam) as s:
        if do_positions:
            items = sorted(saved_pos.items(), key=lambda kv: int(kv[0]))
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
