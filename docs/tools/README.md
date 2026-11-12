# Screenshots opnieuw maken

De screenshots in `docs/screenshots/` zijn gemaakt met nagemaakte demodata (3 camera's, getekende kerkbeelden), zonder echte camera's.

```bash
pip install pillow playwright && python -m playwright install chromium
python docs/tools/demo.py /tmp/ptz-demo                      # demodata: cameras.json + thumbs
python docs/tools/nep_propresenter.py 50001 &                # nagebootste ProPresenter (verzonnen playlist) voor 🎵 Liederen
python docs/tools/nep_voordienst.py 50020 &                  # nagebootste Voordienst app (bearer token test-token-123)
PTZ_DATA=/tmp/ptz-demo PTZ_YT_BASE=http://127.0.0.1:9 PTZ_MIDI_FAKE=1 PTZ_UPDATE=0 PTZ_CAMSTATUS=0 python ptz_control.py --no-open --no-tray &   # PTZ_MIDI_FAKE: nagebootste V-1HD (nep_v1hd.py), ingang 1 in beeld
python docs/tools/screenshots.py docs/screenshots             # alle screenshots (1440×900, licht, donker, telefoon)
```

Draait de server op een andere poort (bijv. omdat de echte PTZ Control al op 8080 draait), zet dan `PTZ_URL=http://127.0.0.1:<poort>` voor `screenshots.py` en `rondleiding_test.py`.

## Rondleiding testen

Met dezelfde server (demodata) draaien: `python docs/tools/rondleiding_test.py`. Doorloopt alle stappen op drie schermformaten en meldt stappen zonder zichtbaar doel, kaarten buiten beeld en camera-commando's tijdens de rondleiding.

## Nep-camera's

`python docs/tools/nep_camera.py 3 9101` start 3 nagebootste (uit `oefen.py`, dezelfde als de oefenstand) PTZOptics-camera's (HTTP-CGI op 9101–9103, VISCA over TCP op 9201–9203) die presets onthouden, geleidelijk bewegen en een foto met de positie geven. Handig voor *onderweg*, *ongedaan maken*, de camerastatus en de wizard (`host` = `127.0.0.1:9101`, `visca_port` = 9201).

## Nep-ProPresenter

`python docs/tools/nep_propresenter.py 50001` bootst de ProPresenter-API na (alleen de GET's die `liederen.py` gebruikt), met verzonnen playlists. Met `--oud` gedraagt hij zich als ProPresenter 7.9 (`/v1/presentation/current`, `/v1/playlist/current`). Testbediening: `/test/trigger?i=<n>` (item n uit de playlist op het scherm, headers tellen mee), `/test/clear`, `/test/playlist?name=…`, `/test/library?name=…` (presentatie buiten de playlist).

## Nep-Voordienst app

`python docs/tools/nep_voordienst.py 50020 [--token <token>]` geeft op `/api/liederen` verzonnen liederen, alleen met `Authorization: Bearer test-token-123` (anders 401). Testbediening: `/test/huidig?n=<k>`, `/test/token?t=<nieuw>` (oude token werkt dan niet meer), `/test/stats`.

## Volgen simuleren

`python docs/tools/volg_sim.py /tmp/ptz-volg` (met opencv-python + numpy) start PTZ Control op poort 8080 (`PTZ_PORT`) met één nagebootste camera *Sim* die echt draait op de pan-commando's van de tracker. Op een getekend podium loopt *rood* heen en weer, kruist *blauw* er voor langs en staat *groen* stil. Uiterlijk (kleding), volgkader en pan-regelaar zijn de echte code van `tracker.py`; alleen de detector is vervangen (vindt de getekende hoofden). Toestand: `http://127.0.0.1:8091/state` (`PTZ_SIM_PORT`) met per persoon de plek in beeld (0–1), `/reset` zet de camera terug, `/pan?x=0.3` zet hem op een plek. De panpositie (voor de grenzen) komt via een nagebootste VISCA-poort 8092 (`PTZ_SIM_VISCA`). Klik op *Kader…* naast de volgknop om te klikken en het kader te verslepen.
