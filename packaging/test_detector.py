"""Controle voor de build: herkent de detector van de losse app (YOLOX via cv2.dnn) een half silhouet?

    python3 packaging/test_detector.py <foto> <minimaal aantal personen>

De build gebruikt zidane.jpg uit het ultralytics-pakket (twee mannen, alleen bovenlichaam);
die foto zit niet in de app. Faalt (exitcode 1) als er geen YOLOX gebruikt wordt of te weinig personen.
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
