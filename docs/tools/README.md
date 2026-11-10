# Screenshots opnieuw maken

De screenshots in `docs/screenshots/` zijn gemaakt met nagemaakte demodata (3 camera's, getekende kerkbeelden), zonder echte camera's.

```bash
pip install pillow playwright && python -m playwright install chromium
python docs/tools/demo.py /tmp/ptz-demo                      # demodata: cameras.json + thumbs
PTZ_DATA=/tmp/ptz-demo PTZ_YT_BASE=http://127.0.0.1:9 PTZ_MIDI_FAKE=1 PTZ_UPDATE=0 PTZ_CAMSTATUS=0 python ptz_control.py --no-open --no-tray &   # PTZ_MIDI_FAKE: nagebootste V-1HD (nep_v1hd.py), ingang 1 in beeld
python docs/tools/screenshots.py docs/screenshots             # alle screenshots (1440×900, licht, donker, telefoon)
```

## Rondleiding testen

Met dezelfde server (demodata) draaien: `python docs/tools/rondleiding_test.py`. Doorloopt alle stappen op drie schermformaten en meldt stappen zonder zichtbaar doel, kaarten buiten beeld en camera-commando's tijdens de rondleiding.

## Nep-camera's

`python docs/tools/nep_camera.py 3 9101` start 3 nagebootste (uit `oefen.py`, dezelfde als de oefenstand) PTZOptics-camera's (HTTP-CGI op 9101–9103, VISCA over TCP op 9201–9203) die presets onthouden, geleidelijk bewegen en een foto met de positie geven. Handig voor *onderweg*, *ongedaan maken*, de camerastatus en de wizard (`host` = `127.0.0.1:9101`, `visca_port` = 9201).
