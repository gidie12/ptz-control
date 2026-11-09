#!/bin/bash
# PTZ Control (macOS): installeren én starten in één.
# - Eerste keer, of na een update met gewijzigde requirements.txt: maakt/werkt de .venv bij.
# - Daarna start de app en opent de bediening in de browser.
# Opnieuw installeren afdwingen (bijv. als het volgen niet werkt): start in Terminal met
#   bash "Start PTZ Control.command" --installeer
# Overige argumenten gaan door naar ptz_control.py (bijv. --port 9000).
# Stoppen: dit venster sluiten of Ctrl+C.
cd "$(dirname "$0")" || exit 1

FORCE=0
ARGS=()
for a in "$@"; do
  case "$a" in
    --installeer|--install) FORCE=1 ;;
    *) ARGS+=("$a") ;;
  esac
done

fail() { echo; echo "✕ $1"; read -r -p "Druk op Enter om te sluiten…"; exit 1; }

install() {
  local PY=""
  for c in python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      PY="$c"; break
    fi
  done
  if [ -z "$PY" ]; then
    echo "Geen Python 3.9+ gevonden."
    echo "Installeer Python via https://www.python.org/downloads/macos/ (of: brew install python) en start dit opnieuw."
    fail "Installatie niet mogelijk."
  fi
  echo "▶ Python: $("$PY" --version) ($(command -v "$PY"))"

  if [ ! -x .venv/bin/python ]; then
    echo "▶ .venv aanmaken…"
    "$PY" -m venv .venv || fail "venv aanmaken mislukt."
  else
    echo "▶ .venv bestaat al, packages bijwerken…"
  fi
  rm -f .venv/requirements.installed

  .venv/bin/python -m pip install --upgrade pip
  echo "▶ Packages installeren (eerste keer kan dit enkele minuten duren)…"
  .venv/bin/python -m pip install -r requirements.txt || fail "Installatie mislukt. Zie de foutmelding hierboven."

  echo "▶ Controleren en volgmodel klaarzetten…"
  .venv/bin/python -c "import tracker; print('OpenCV OK' if tracker.available() else tracker.CV_ERROR); d = tracker.Detector(); print('Persoonsdetectie:', d.kind, '· rekent op', d.device)"

  # pas na een geslaagde installatie markeren, zodat een afgebroken installatie de volgende keer opnieuw loopt
  cp requirements.txt .venv/requirements.installed
  echo "✓ Installatie klaar."
  echo
}

if [ "$FORCE" = 1 ]; then
  echo "Opnieuw installeren…"; install
elif [ ! -x .venv/bin/python ]; then
  echo "Eerste start: installeren (een paar minuten)…"; install
elif ! cmp -s requirements.txt .venv/requirements.installed; then
  echo "requirements.txt is gewijzigd: packages bijwerken…"; install
fi

exec .venv/bin/python ptz_control.py --open "${ARGS[@]}"
