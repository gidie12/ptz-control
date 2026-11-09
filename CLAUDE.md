# CLAUDE.md — PTZ Control

Eigen webbediening voor PTZOptics-camera's in de kerk (livestream), als vervanging van het PTZOptics-programma.
Dit bestand legt vast **welke aannames zijn gedaan** en **hoe je hier werkt**. Lees het voor je iets wijzigt.

Legenda: ✅ geverifieerd (getest of in officiële docs gevonden) · ⚠️ aanname, niet getest op echte hardware · 🧑 keuze/wens van de gebruiker

---

## 1. Context en wensen van de gebruiker

- 🧑 **3 PTZOptics-camera's** die **tegelijk** bediend worden (Links / Midden / Rechts in de testdata; echte namen staan in `cameras.json`).
- 🧑 **Presets zijn de hoofdfunctie.** Het hoofdscherm is een presetraster per camera; handbediening is secundair (zijbalk).
- 🧑 **Geen live preview.** Bewust verwijderd. Thumbnails op presettegels mogen wel (worden alleen gemaakt bij opslaan/vernieuwen).
- 🧑 **PTZJoy-joystick** (PTZOptics PT-JOY-G4 / SuperJoy) moet integreren.
- 🧑 **Xbox-controller** als zelfgebouwde vervanging van de PTZJoy.
- 🧑 **Persoon volgen: alleen pan** via software. Tilt en zoom nooit aanpassen door de tracker.
- 🧑 **Geen YouTube/stream-bediening in deze software.** Een eerdere OBS-koppeling is op verzoek verwijderd; streamen gebeurt buiten deze app. Niet opnieuw toevoegen zonder expliciete vraag.
- 🧑 Draait op een **Mac mini (Apple Silicon)** ("livestreams-mac-mini"). Er is ook een **Windows-starter** gevraagd.
- 🧑 UI, README, commits en communicatie in het **Nederlands**.
- ⚠️ Het cameramodel is **onbekend** (Move/Studio/G2/G3 niet bevestigd). Daarom geen ingebouwde auto-tracking gebruikt.

## 2. Architectuur

| Bestand | Rol |
|---|---|
| `ptz_control.py` | HTTP-server (alleen stdlib), camera-commando's (HTTP-CGI + VISCA), config, SSE-events, joystick-endpoints, tracking-beheer |
| `index.html` | Volledige UI (vanilla JS, geen build), presets, scènes, handbediening, Xbox-controller, dialogen |
| `tracker.py` | Volgmodule: RTSP-grabber, persoonsdetectie (YOLO of OpenCV HOG), pan-regelaar |
| `Installeer.command` / `Installeer.bat` | Maakt `.venv` en installeert `requirements.txt` (`--no-pause` / `/nopause` als ze door de starter worden aangeroepen) |
| `Start PTZ Control.command` / `.bat` | Eén-klik-start: installeert bij eerste keer, start met `--open` |
| `cameras.json` | Lokale config (camera's, presetnamen, scènes). **Bevat camerawachtwoorden in platte tekst. Nooit committen** (staat in `.gitignore`) |
| `thumbs/` | Preset-thumbnails (`<camid>_<n>.jpg`), lokaal, niet committen |

- Kern heeft **geen externe packages** nodig; numpy/opencv/ultralytics zijn alleen voor volgen. ⚠️ Python **3.9+** (installers checken dat; de code zelf vermijdt nieuwere syntax).
- Server luistert op **0.0.0.0:8080**, **zonder authenticatie**. ⚠️ Aanname: vertrouwd kerknetwerk. Iedereen op het LAN kan camera's en de stream bedienen.
- Live-sync tussen schermen via **Server-Sent Events** (`/api/events`): `select`, `preset`, `thumb`, `config`, `track`, `state`.
- Per-apparaat-instellingen (snelheden, tegelgrootte, Xbox-opties) staan in **localStorage**, niet op de server.
- Standaardweergave is **"Alle camera's"** (kolommen) zodra er >1 camera is.
- **Scènes** = meerdere camera's tegelijk naar een preset (zoals SuperPresets van de joystick).

## 3. Camera-protocol (PTZOptics)

- ✅ HTTP-CGI: `http://<ip>/cgi-bin/ptzctrl.cgi?ptzcmd&...` met `up/down/left/right/leftup/rightup/leftdown/rightdown`, `ptzstop`, `home`, `zoomin/zoomout/zoomstop`, `poscall/posset` (bron: PTZOptics developer docs).
- ✅ Zoom-snelheid via `zoomin&<speed>`.
- ⚠️ Snelheden: **pan 1–24, tilt 1–20, zoom/focus 1–7**. Standaard VISCA-bereik; niet per model gecontroleerd.
- ⚠️ **Focus: `focusin` = ver, `focusout` = dichtbij.** Niet geverifieerd; kan omgekeerd zijn. Knoppen heten "Focus ver/dichtbij".
- ⚠️ `poscall&<n>&<speed>` (preset-snelheid) is waarschijnlijk niet op alle firmware ondersteund (stond als feature request op het PTZOptics-forum). Backend ondersteunt het, de UI gebruikt het niet.
- ⚠️ Presets 0–254 toegestaan; UI toont standaard 12 (instelbaar 1–64).
- ⚠️ **Autofocus gaat altijd via VISCA over TCP poort 5678**, ook bij protocol HTTP, omdat er geen betrouwbaar HTTP-commando voor gevonden is:
  - auto `81 01 04 38 02 FF`, handmatig `81 01 04 38 03 FF`, one-push `81 01 04 18 01 FF`.
- ⚠️ VISCA over IP = **raw VISCA over TCP 5678** (geen Sony VISCA-over-IP-header). Pan/tilt `81 01 06 01 VV WW XX YY FF`, preset `81 01 04 3F 02/01 pp FF`.
- ⚠️ Snapshot voor thumbnails: `http://<ip>/snapshot.jpg`.
- ⚠️ Inloggen: Basic én Digest auth worden geprobeerd als gebruiker/wachtwoord is ingevuld.
- Camera-host mag een poort bevatten (`ip:poort`); exacte host-match gaat voor IP-match (alleen relevant voor tests met meerdere nep-camera's op één IP).

## 4. PTZJoy-joystick (PT-JOY-G4 / SuperJoy)

- ✅ USB op de joystick is **alleen voor firmware**; geen USB-besturing mogelijk.
- ✅ Joystick bestuurt camera's **zelf**. De app luistert mee via HTTP-commando's uit **Custom Buttons** en **Automatic Triggers** (firmware **4.1.8+**), met variabele `{{CURRENT_CAM}}` (= IP van de gekozen camera).
- Endpoints (allemaal GET): `/api/joy/select`, `/api/joy/preset`, `/api/joy/scene`, `/api/joy/cmd`, `/api/joy/track`. `cam` = id, volgnummer (1-based) of IP.
- ⚠️ De pc met de app heeft een **vast IP** nodig, anders kloppen de joystick-URL's niet.
- ⚠️ Joystick-bewegingen zijn **onzichtbaar** voor de app: als de joystick een camera beweegt terwijl volgen aan staat, werken ze tegen elkaar in.

## 5. Xbox-controller

- Via de **Gamepad API** van de browser ("standard" mapping). ✅ Getest met een nagebootste gamepad in Chromium, **niet** met een echte controller.
- ⚠️ Werkt alleen als de bedieningspagina **open en zichtbaar** is. Bij tabblad naar achtergrond / venster kwijt → alles stoppen.
- Indeling: linker stick pan/tilt (kwadratische curve, deadzone 0.18, 8 richtingen), L3 fijn-modus (max/4), RT/LT zoom, rechter stick ↑↓ focus, R3 one-push AF, A/B/X/Y preset 1–4, LB+ = 5–8, Menu+knop = preset opslaan (trilt), D-pad ←/→ camera, ↑ volgende scène, ↓ home, RB volgen aan/uit, View alles stoppen.
- Commando's alleen bij verandering, maximaal ~12/s (80 ms).
- ⚠️ Trillen (`vibrationActuator`) werkt niet in elke browser; is optioneel.

## 6. Persoon volgen (alleen pan)

- 🧑 **Alleen pan.** De tracker stuurt uitsluitend `left`/`right` (tilt-snelheid 1, geen tilt-richting) en `ptzstop`. Nooit tilt of zoom wijzigen.
- ⚠️ Videobron: **`rtsp://<ip>:554/2`** (substream), per camera aan te passen. Niet geverifieerd op hun camera's; RTSP moet aan staan.
- Detectie: **YOLOv8n via `ultralytics`** als geïnstalleerd (device: `cuda` → `mps` → `cpu`), anders **OpenCV HOG**.
- ✅ **`opencv-python` moet < 5**: versie 5.x mist `HOGDescriptor` (gevonden bij testen). Vastgezet in `requirements.txt`.
- Doelkeuze: grootste persoon (meestal de spreker vooraan), daarna dezelfde persoon blijven volgen (max. sprong 0.22 beeldbreedte). Kwijt >0.4 s → pan stoppen; >2.5 s → nieuw doel kiezen.
- Regelaar: dode zone 0.10 met hysterese (stopt bij 0.05), snelheid evenredig tot `track_speed` (standaard 6), optie `track_invert` voor ondersteboven gemonteerde camera.
- Handbediening, home, stop of een preset op die camera **zet volgen uit** (tracker eerst stoppen, dan pas het nieuwe commando, zodat er geen pan meer achteraan komt). Zoom en focus mogen tijdens volgen.
- Preset-optie "na oproepen automatisch volgen": start **2,5 s** na de preset (aanname: dan is de camera aangekomen).
- ✅ Getest in een **simulatie** (camera die echt draait op de commando's, spreker loopt heen en weer, tweede persoon staat stil): gemiddeld 9°, max 14° van het midden bij 60° beeldhoek. ⚠️ De simulatie nam **4°/s per snelheidsstap** aan; echte camera's wijken af, dus `track_speed` moet in een repetitie worden afgesteld.

## 7. Bewust níet in de software

- 🧑 **YouTube/stream starten en stoppen** (en opnemen). Was eerst gebouwd via OBS WebSocket, daarna op verzoek volledig verwijderd (server, UI, joystick-endpoint). Een oude `obs`-sectie in `cameras.json` wordt bij opslaan weggegooid.
- 🧑 **Live preview** van de camerabeelden.

## 8. Installatie en platform

- 🧑 Mac mini (Apple Silicon). `.venv` moet **op de Mac zelf** worden gemaakt (een in de cloud gebouwde venv werkt daar niet). De starters doen dat automatisch bij de eerste start.
- ⚠️ Windows-scripts (`.bat`) zijn **niet uitgevoerd** (geen Windows beschikbaar), alleen gereviewd. `.gitattributes` forceert CRLF voor `.bat` en LF voor `.command`.
- ⚠️ Windows met NVIDIA: `ultralytics` installeert standaard CPU-torch; GPU vereist de CUDA-index (zie README).
- macOS Gatekeeper ("onbekende ontwikkelaar") en Windows SmartScreen/firewall vragen eenmalig om bevestiging.

## 9. Werken aan deze repo

- **Talen:** Nederlands voor UI-teksten, README, commitberichten en uitleg aan de gebruiker.
- **Nooit committen:** `cameras.json`, `thumbs/`, `.venv/`, `*.pt`.
- **Controles na wijzigingen:**
  ```bash
  python3 -m py_compile ptz_control.py tracker.py
  python3 -c "import re;s=open('index.html').read();open('/tmp/a.js','w').write(re.search(r'<script>(.*)</script>',s,re.S).group(1))" && node --check /tmp/a.js
  bash -n Installeer.command && bash -n "Start PTZ Control.command"
  ```
- **Testen zonder hardware:** gebruik nep-camera's (kleine `ThreadingHTTPServer` die `ptzctrl.cgi`-aanroepen logt en een JPEG op `/snapshot.jpg` geeft), en voor volgen een `SimGrabber` + `FakeDetector` die `tracker.FrameGrabber` / `_DETECTOR` vervangen. Xbox testen door `navigator.getGamepads` te vervangen in Playwright.
- Wijzig je camera-commando's, controleer dan beide paden (HTTP-CGI én VISCA) en dat `do_action()` tracking correct stopt.
- Eerst zonder echte hardware getest; **alles met ⚠️ hierboven moet bij de eerste repetitie in de kerk gecontroleerd worden**.
