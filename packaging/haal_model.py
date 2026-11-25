"""Haal de modellen voor 'persoon volgen' op (als ONNX, gebruikt via OpenCV; geen torch nodig):

- YOLOX-tiny (Megvii, Apache 2.0): personen vinden, ook een half silhouet.
- YuNet (OpenCV Zoo, MIT): gezichten vinden.
- SFace int8 (OpenCV Zoo, Apache 2.0): vingerafdruk van een gezicht, om de aangeklikte persoon te herkennen.

Wordt gebruikt door de build (.github/workflows/build.yml) en kan ook met de hand:
    python3 packaging/haal_model.py            -> models/*.onnx
De losse app neemt de bestanden mee. Met 'ultralytics' geïnstalleerd (Start PTZ Control.command) gebruikt de app
YOLOv8 voor personen; de gezichtsmodellen zijn dan nog steeds nodig voor aanklikken op gezicht.
"""
import hashlib
import os
import sys
import urllib.request

ZOO = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
MODELS = [
    # (bestand, adres, sha256)
    ("yolox_tiny.onnx", "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx",
     "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7"),
    ("face_detection_yunet_2023mar.onnx", ZOO + "face_detection_yunet/face_detection_yunet_2023mar.onnx",
     "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    ("face_recognition_sface_2021dec_int8.onnx", ZOO + "face_recognition_sface/face_recognition_sface_2021dec_int8.onnx",
     "2b0e941e6f16cc048c20aee0c8e31f569118f65d702914540f7bfdc14048d78a"),
]
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DIR = os.path.join(ROOT, "models")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(name, url, want):
    dest = os.path.join(DIR, name)
    if os.path.isfile(dest) and sha256(dest) == want:
        print(f"Model is er al: {dest}")
        return True
    tmp = dest + ".download"
    print(f"Downloaden: {url}")
    urllib.request.urlretrieve(url, tmp)
    got = sha256(tmp)
    if got != want:
        os.remove(tmp)
        print(f"Controlesom van {name} klopt niet ({got}), bestand weggegooid.", file=sys.stderr)
        return False
    os.replace(tmp, dest)
    print(f"Opgeslagen: {dest}")
    return True


def main():
    os.makedirs(DIR, exist_ok=True)
    ok = all([fetch(*m) for m in MODELS])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
