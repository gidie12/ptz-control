# Screenshots opnieuw maken

De screenshots in `docs/screenshots/` zijn gemaakt met nagemaakte demodata (3 camera's, getekende kerkbeelden), zonder echte camera's.

```bash
pip install pillow playwright && python -m playwright install chromium
python docs/tools/demo.py /tmp/ptz-demo                      # demodata: cameras.json + thumbs
PTZ_DATA=/tmp/ptz-demo PTZ_YT_BASE=http://127.0.0.1:9 python ptz_control.py --no-open --no-tray &
python docs/tools/screenshots.py docs/screenshots             # alle screenshots (1440×900, licht, donker, telefoon)
```

## Rondleiding testen

Met dezelfde server (demodata) draaien: `python docs/tools/rondleiding_test.py`. Doorloopt alle stappen op drie schermformaten en meldt stappen zonder zichtbaar doel, kaarten buiten beeld en camera-commando's tijdens de rondleiding.
