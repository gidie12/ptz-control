#!/bin/bash
# Start PTZ Control met de .venv en opent de bediening in de browser. Stoppen: dit venster sluiten of Ctrl+C.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "Nog geen .venv gevonden. Voer eerst Installeer.command uit."
  read -r -p "Druk op Enter om te sluiten…"; exit 1
fi
(sleep 2; open "http://localhost:8080") &
exec .venv/bin/python ptz_control.py "$@"
