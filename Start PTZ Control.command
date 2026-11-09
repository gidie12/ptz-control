#!/bin/bash
# PTZ Control starten (macOS). Eerste keer: installeert automatisch de .venv.
# Stoppen: dit venster sluiten of Ctrl+C.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "Eerste start: .venv wordt geïnstalleerd…"
  bash ./Installeer.command --no-pause || { read -r -p "Installatie mislukt. Enter om te sluiten…"; exit 1; }
fi
exec .venv/bin/python ptz_control.py --open "$@"
