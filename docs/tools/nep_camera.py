"""Nagebootste PTZOptics-camera's voor tests (HTTP-CGI + VISCA over TCP), zonder echte hardware.

De camera's zelf staan in ``oefen.py`` (in de hoofdmap; ook gebruikt door de oefenstand in de app).
Elke camera onthoudt pan/tilt/zoom/focus en presets, beweegt geleidelijk naar een preset of absolute positie,
en geeft een JPEG op /snapshot.jpg met de huidige positie erin getekend.

    python docs/tools/nep_camera.py 3 9101        # 3 camera's: HTTP op 9101.., VISCA op 9201..
    → host "127.0.0.1:9101", visca_port 9201 (enz.)
"""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from oefen import Cam, start_cam  # noqa: E402,F401


def start(n=3, base=9101):
    return [start_cam(f"Camera {i + 1}", base + i, base + 100 + i) for i in range(n)]


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    base = int(sys.argv[2]) if len(sys.argv) > 2 else 9101
    start(n, base)
    print(f"{n} nep-camera's: HTTP 127.0.0.1:{base}…, VISCA {base + 100}…")
    threading.Event().wait()
