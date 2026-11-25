"""Controle voor de build: herkent de detector van de losse app (YOLOX via cv2.dnn) een half silhouet?

    python3 packaging/test_detector.py <foto> <minimaal aantal personen>

De build gebruikt zidane.jpg uit het ultralytics-pakket (twee mannen, alleen bovenlichaam);
die foto zit niet in de app. Faalt (exitcode 1) als er geen YOLOX gebruikt wordt of te weinig personen.
🧑 Controleert ook de gezichtsmodellen (YuNet + SFace): een gezicht per persoon, en dezelfde persoon op halve
grootte herkend terwijl de ander duidelijk iemand anders is.
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.modules.setdefault("ultralytics", None)  # dwing het pad van de losse app af (zonder YOLOv8/torch)

import cv2  # noqa: E402
import tracker  # noqa: E402


def main():
    img, need = sys.argv[1], int(sys.argv[2])
    det = tracker.Detector([ROOT])
    people = det.detect(cv2.imread(img))
    print(f"detector={det.kind} personen={len(people)}")
    for p in people:
        print("  x=%.2f y=%.2f b=%.2f h=%.2f score=%.2f" % p)
    if det.kind != "yolox":
        print(f"FOUT: verwacht YOLOX, kreeg {det.kind} ({det.note})", file=sys.stderr)
        return 1
    if len(people) < need:
        print(f"FOUT: minder dan {need} personen gevonden", file=sys.stderr)
        return 1
    faces = tracker.Faces([ROOT])
    if not faces.ok:
        print(f"FOUT: gezichtsmodellen niet geladen ({faces.note})", file=sys.stderr)
        return 1
    im = cv2.imread(img)
    small = cv2.resize(im, None, fx=0.5, fy=0.5)
    big = sorted(people)[:need]
    f1 = [faces.feature(im, faces.find(im, p)) for p in big]
    f2 = [faces.feature(small, faces.find(small, p)) for p in sorted(det.detect(small))[:need]]
    if any(f is None for f in f1 + f2):
        print("FOUT: niet bij elke persoon een herkenbaar gezicht gevonden", file=sys.stderr)
        return 1
    same = [faces.match(a, b) for a, b in zip(f1, f2)]
    other = faces.match(f1[0], f1[1])
    print("gezichten: zelfde persoon %s, ander %.2f" % (", ".join("%.2f" % v for v in same), other))
    if min(same) < tracker.FACE_SAME or other > tracker.FACE_OTHER:
        print("FOUT: gezichtsherkenning klopt niet", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
