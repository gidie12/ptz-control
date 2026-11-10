"""Haal het persoonsdetectiemodel voor 'persoon volgen' op: YOLOX-tiny (Megvii, Apache 2.0) als ONNX.

Wordt gebruikt door de build (.github/workflows/build.yml) en kan ook met de hand:
    python3 packaging/haal_model.py            -> models/yolox_tiny.onnx
De losse app neemt het bestand mee; tracker.py gebruikt het via cv2.dnn (geen torch nodig).
Met 'ultralytics' geïnstalleerd (Start PTZ Control.command) gebruikt de app YOLOv8 en is dit niet nodig.
"""
import hashlib
import os
import sys
import urllib.request

URL = "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx"
SHA256 = "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEST = os.path.join(ROOT, "models", "yolox_tiny.onnx")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    if os.path.isfile(DEST) and sha256(DEST) == SHA256:
        print(f"Model is er al: {DEST}")
        return 0
    os.makedirs(os.path.dirname(DEST), exist_ok=True)
    tmp = DEST + ".download"
    print(f"Downloaden: {URL}")
    urllib.request.urlretrieve(URL, tmp)
    got = sha256(tmp)
    if got != SHA256:
        os.remove(tmp)
        print(f"Controlesom klopt niet ({got}), bestand weggegooid.", file=sys.stderr)
        return 1
    os.replace(tmp, DEST)
    print(f"Opgeslagen: {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
