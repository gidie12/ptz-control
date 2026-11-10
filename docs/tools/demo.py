"""Maakt demodata (3 camera's, presets met nagemaakte kerkbeelden) in een map."""
import json, os, random, sys
from PIL import Image, ImageDraw, ImageFilter
out = sys.argv[1]; os.makedirs(out + "/thumbs", exist_ok=True)
W, H = 640, 360
def person(d, x, y, s, shirt):
    d.ellipse([x - 9*s, y - 34*s, x + 9*s, y - 16*s], fill=(222, 186, 160))
    d.rounded_rectangle([x - 15*s, y - 15*s, x + 15*s, y + 40*s], radius=int(8*s), fill=shirt)
def scene(kind, seed):
    r = random.Random(seed)
    im = Image.new("RGB", (W, H)); d = ImageDraw.Draw(im)
    for yy in range(H):  # warme achterwand met verloop
        t = yy / H; d.line([(0, yy), (W, yy)], fill=(int(70 + 60*t), int(52 + 38*t), int(44 + 22*t)))
    floor = int(H * (0.72 if kind != "zaal" else 0.55))
    d.rectangle([0, floor, W, H], fill=(96, 64, 40))
    for i in range(0, W, 46): d.line([(i, floor), (i - 60, H)], fill=(86, 56, 34), width=2)
    # lichtbundels
    glow = Image.new("RGB", (W, H)); g = ImageDraw.Draw(glow)
    for _ in range(3):
        x = r.randint(60, W - 60); g.polygon([(x - 20, 0), (x + 20, 0), (x + 140, floor), (x - 140, floor)], fill=(60, 50, 30))
    im = Image.blend(im, Image.eval(Image.composite(glow, im, glow.convert("L").point(lambda v: 255 if v else 0)), lambda v: v), 0.25)
    d = ImageDraw.Draw(im)
    if kind in ("zaal", "spreker", "kansel"):
        cx = W // 2; d.rectangle([cx - 6, 40, cx + 6, 150], fill=(230, 205, 150)); d.rectangle([cx - 38, 72, cx + 38, 84], fill=(230, 205, 150))
    if kind == "scherm":
        d.rectangle([110, 50, 530, 270], fill=(30, 34, 46)); d.rectangle([126, 64, 514, 256], fill=(58, 92, 140))
        for i, w in enumerate((300, 240, 280)): d.rounded_rectangle([320 - w//2, 120 + i*34, 320 + w//2, 136 + i*34], radius=6, fill=(220, 230, 245))
    if kind in ("kansel", "spreker"):
        s = 2.4 if kind == "spreker" else 1.4; x = W // 2 + (40 if kind == "kansel" else 0)
        person(d, x, floor - int(70*s), s, (40, 60, 90))
        d.polygon([(x - 50*s, floor - 30*s), (x + 50*s, floor - 30*s), (x + 36*s, floor + 10), (x - 36*s, floor + 10)], fill=(120, 80, 50))
    if kind in ("band", "zang", "drums", "gitaar", "piano"):
        n = {"band": 4, "zang": 3}.get(kind, 1); s = 1.3 if n > 1 else 2.3
        for i in range(n):
            x = int(W * (i + 1) / (n + 1)); y = floor - int(40*s)
            person(d, x, y, s, r.choice([(150, 50, 60), (40, 90, 120), (70, 110, 70), (60, 60, 70), (170, 120, 60)]))
            if kind == "drums" or (kind == "band" and i == 3):
                d.ellipse([x - 40*s, y + 10*s, x + 40*s, y + 50*s], fill=(180, 180, 190)); d.ellipse([x - 30*s, y + 16*s, x + 30*s, y + 44*s], fill=(60, 60, 70))
            if kind == "piano" or (kind == "band" and i == 0):
                d.rectangle([x - 55*s, y + 18*s, x + 55*s, y + 40*s], fill=(20, 20, 24)); d.rectangle([x - 50*s, y + 18*s, x + 50*s, y + 24*s], fill=(235, 235, 235))
            if kind in ("zang", "band") and i in (1, 2): d.line([(x + 12*s, y + 40*s), (x + 12*s, y - 10*s)], fill=(30, 30, 30), width=3)
            if kind == "gitaar": d.ellipse([x - 26*s, y + 4*s, x + 6*s, y + 30*s], fill=(150, 90, 40)); d.line([(x - 4*s, y + 12*s), (x + 46*s, y - 18*s)], fill=(80, 50, 20), width=6)
    if kind in ("zaal", "gemeente"):
        rows = 4 if kind == "zaal" else 3
        for ri in range(rows):
            y = floor + 30 + ri * 34 if kind == "zaal" else int(H * 0.45) + ri * 52
            s = 0.6 + ri * 0.25 if kind == "zaal" else 1.0 + ri * 0.35
            for x in range(30, W, int(48 * s)):
                d.ellipse([x - 9*s, y - 9*s, x + 9*s, y + 9*s], fill=r.choice([(60, 45, 35), (120, 90, 60), (200, 170, 120), (40, 40, 40)]))
                d.rounded_rectangle([x - 15*s, y + 8*s, x + 15*s, y + 34*s], radius=int(6*s), fill=r.choice([(70, 80, 110), (110, 60, 60), (80, 100, 80), (90, 90, 95)]))
    if kind == "doop":
        d.ellipse([250, floor - 50, 390, floor + 10], fill=(170, 170, 175)); d.rectangle([300, floor - 20, 340, floor + 50], fill=(150, 150, 155))
    if kind == "tafel":
        d.rectangle([140, floor - 40, 500, floor - 20], fill=(240, 236, 228)); d.rectangle([150, floor - 20, 490, floor + 40], fill=(110, 72, 44))
        d.ellipse([290, floor - 70, 350, floor - 40], fill=(200, 160, 60)); d.rectangle([400, floor - 66, 420, floor - 40], fill=(150, 30, 40))
    return im.filter(ImageFilter.GaussianBlur(0.6))
cams = [
  ("links", "Links", [("Totaal", "zaal"), ("Preekstoel", "kansel"), ("Band", "band"), ("Piano", "piano"), ("Doopvont", "doop"), ("Avondmaal", "tafel")]),
  ("midden", "Midden", [("Totaal zaal", "zaal"), ("Spreker close", "spreker"), ("Kansel", "kansel"), ("Scherm", "scherm"), ("Zangers", "zang"), ("Gemeente", "gemeente")]),
  ("rechts", "Rechts", [("Drums", "drums"), ("Gitaar", "gitaar"), ("Zang", "zang"), ("Band breed", "band"), ("Gemeente", "gemeente"), ("Tafel", "tafel")]),
]
cfg = {"cameras": [], "preset_count": 8, "tile_size": 170, "youtube": {"channel": "@kerkzwolle"},
       "midi": {"enabled": True, "port": "V-1HD", "dev": 16, "live_bus": "A", "map": {"1": "links", "2": "midden", "3": "rechts"}}}
for ci, (cid, name, ps) in enumerate(cams):
    cfg["cameras"].append({"id": cid, "name": name, "host": f"192.168.100.{81 + ci}", "protocol": "http", "visca_port": 5678,
                           "user": "admin", "password": "", "presets": {str(i + 1): n for i, (n, _) in enumerate(ps)},
                           # kleurlabels als voorbeeld: totaal = blauw, spreker = oranje, muziek = paars
                           "preset_colors": {str(i + 1): c for i, (n, _) in enumerate(ps)
                                             for c in [("blauw" if "otaal" in n or "emeente" in n else "oranje" if n in ("Preekstoel", "Spreker close", "Kansel")
                                                        else "paars" if n in ("Band", "Piano", "Drums", "Gitaar", "Zang", "Zangers", "Band breed") else "")] if c}})
    for i, (_, k) in enumerate(ps):
        scene(k, ci * 10 + i).save(f"{out}/thumbs/{cid}_{i + 1}.jpg", quality=86)
json.dump(cfg, open(out + "/cameras.json", "w"), indent=1)
print("ok")
