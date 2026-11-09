#!/bin/bash
# Maakt .venv aan en installeert requirements.txt. Dubbelklik in Finder, of: bash Installeer.command
cd "$(dirname "$0")" || exit 1
PAUSE=1; [ "$1" = "--no-pause" ] && PAUSE=0
pause() { if [ "$PAUSE" = 1 ]; then read -r -p "Druk op Enter om te sluiten…"; fi; return 0; }

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Geen Python 3.9+ gevonden."
  echo "Installeer Python via https://www.python.org/downloads/macos/ (of: brew install python) en start dit opnieuw."
  pause; exit 1
fi
echo "▶ Python: $("$PY" --version) ($(command -v "$PY"))"

if [ ! -x .venv/bin/python ]; then
  echo "▶ .venv aanmaken…"
  "$PY" -m venv .venv || { echo "venv aanmaken mislukt"; pause; exit 1; }
else
  echo "▶ .venv bestaat al, packages bijwerken…"
fi

.venv/bin/python -m pip install --upgrade pip
echo "▶ Packages installeren (eerste keer kan dit enkele minuten duren)…"
if ! .venv/bin/python -m pip install -r requirements.txt; then
  echo "✕ Installatie mislukt. Zie de foutmelding hierboven."
  pause; exit 1
fi

echo "▶ Controleren en volgmodel klaarzetten…"
.venv/bin/python -c "import tracker; print('OpenCV OK' if tracker.available() else tracker.CV_ERROR); d = tracker.Detector(); print('Persoonsdetectie:', d.kind, '· rekent op', d.device)"

chmod +x "Start PTZ Control.command" 2>/dev/null
echo
echo "✓ Klaar. Start de app met 'Start PTZ Control.command'."
pause
exit 0
