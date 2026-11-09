# CLAUDE.md — PTZ Control

Eigen webbediening voor PTZOptics-camera's in de kerk (livestream), als vervanging van het PTZOptics-programma.
Dit bestand legt vast **welke aannames zijn gedaan** en **hoe je hier werkt**. Lees het voor je iets wijzigt.

Legenda: ✅ geverifieerd (getest of in officiële docs gevonden) · ⚠️ aanname, niet getest op echte hardware · 🧑 keuze/wens van de gebruiker

---

## 1. Context en wensen van de gebruiker

- 🧑 **3 PTZOptics-camera's** die **tegelijk** bediend worden (Links / Midden / Rechts in de testdata; echte namen staan in `cameras.json`).
- 🧑 **Presets zijn de hoofdfunctie.** Het hoofdscherm is een presetraster per camera; handbediening is secundair (zijbalk).
- 🧑 **Geen live preview.** Bewust verwijderd. Thumbnails op presettegels mogen wel (worden alleen gemaakt bij opslaan/vernieuwen).
- 🧑 **Xbox-controller** is de fysieke bediening. De PTZJoy-koppeling (PT-JOY-G4 / SuperJoy, `/api/joy/*`) is op verzoek **verwijderd**; niet opnieuw toevoegen zonder expliciete vraag.
- 🧑 Knopindeling van de Xbox-controller is **instelbaar** per knop, gedeeld via `cameras.json` (`pad_map`).
- 🧑 **Persoon volgen: alleen pan** via software. Tilt en zoom nooit aanpassen door de tracker.
- 🧑 **Geen YouTube/stream-bediening in deze software.** Een eerdere OBS-koppeling is op verzoek verwijderd; streamen gebeurt buiten deze app. Niet opnieuw toevoegen zonder expliciete vraag.
- 🧑 Draait op een **Mac mini (Apple Silicon)** ("livestreams-mac-mini"). Er is ook een **Windows-starter** gevraagd.
- 🧑 **Alle presets en camera-instellingen moeten bewaard en teruggezet kunnen worden**: app-instellingen (back-upbestand), presetposities zelf én beeldinstellingen.
- 🧑 **Presets moeten geladen kunnen worden vanaf de camera** (bestaande presets in de app krijgen, met foto).
- 🧑 UI, README, commits en communicatie in het **Nederlands**.
- ⚠️ Het cameramodel is **onbekend** (Move/Studio/G2/G3 niet bevestigd). Daarom geen ingebouwde auto-tracking gebruikt.

## 2. Architectuur

| Bestand | Rol |
|---|---|
| `ptz_control.py` | HTTP-server (alleen stdlib), camera-commando's (HTTP-CGI + VISCA), config, SSE-events, tracking-beheer |
| `index.html` | Volledige UI (vanilla JS, geen build), presets, scènes, handbediening, Xbox-controller, dialogen |
| `tracker.py` | Volgmodule: RTSP-grabber, persoonsdetectie (YOLO of OpenCV HOG), pan-regelaar |
| `backup.py` | Back-up: VISCA-inquiries, presetposities lezen/terugzetten, beeldinstellingen lezen/terugzetten |
| `Start PTZ Control.command` / `.bat` | 🧑 **Installer en starter in één.** Installeert als `.venv` ontbreekt, als `requirements.txt` verschilt van `.venv/requirements.installed` (kopie die pas na een geslaagde installatie wordt gezet) of met `--installeer` / `/installeer`. Start daarna `ptz_control.py --open`; overige argumenten gaan door. Er zijn geen aparte `Installeer.*`-scripts meer. |
| `PTZ Control.app` | 🧑 **Dock-app (macOS)** met eigen icoon. `Contents/MacOS/PTZ Control` is een bash-script: draait de server al op poort 8080 (`PTZ_PORT`) → alleen browser openen; anders `open -a Terminal "Start PTZ Control.command"`. Zoekt de repo als map náást de .app, dus de .app moet in de repo blijven (Dock = verwijzing). Uitvoerbaar bit moet in git blijven (100755). |
| `app/` | Icoon (`maak_icoon.py` tekent `icon.png`, `ptz.ico` en `AppIcon.icns` met Pillow) en `snelkoppeling.ps1` (Windows: `PTZ Control.lnk` in Startmenu + bureaublad, doel `cmd.exe /s /c ""…Start PTZ Control.bat""` zodat hij aan de taakbalk kan). De .bat roept dit aan als de Startmenu-snelkoppeling ontbreekt of met `/snelkoppeling`. |
| `cameras.json` | Lokale config (camera's, presetnamen, scènes, uitgelezen `saved_positions` / `saved_image`). **Bevat camerawachtwoorden in platte tekst. Nooit committen** (staat in `.gitignore`) |
| `thumbs/` | Preset-thumbnails (`<camid>_<n>.jpg`), lokaal, niet committen |

- Kern heeft **geen externe packages** nodig; numpy/opencv/ultralytics zijn alleen voor volgen. ⚠️ Python **3.9+** (installers checken dat; de code zelf vermijdt nieuwere syntax).
- Server luistert op **0.0.0.0:8080**, **zonder authenticatie**. ⚠️ Aanname: vertrouwd kerknetwerk. Iedereen op het LAN kan de camera's bedienen (en een back-up met wachtwoorden downloaden).
- `do_HEAD` bestaat alleen voor `/api/thumb` (de UI checkt daarmee of een thumbnail bestaat na een `thumb`-event) en `/`. Ontbrak eerst, waardoor nieuwe thumbnails pas na herladen zichtbaar werden.
- Live-sync tussen schermen via **Server-Sent Events** (`/api/events`): `select`, `preset`, `thumb`, `config`, `track`, `job`, `state`.
- Per-apparaat-instellingen (snelheden, tegelgrootte, Xbox-opties) staan in **localStorage**, niet op de server.
- Standaardweergave is **"Alle camera's"** (kolommen) zodra er >1 camera is.
- 🧑 **Menu onder de 🎮-statusknop** (`#mainMenu`): Controller-instellingen, Knoppenindeling, Volledig scherm, Back-up, Camera's. De knoppen houden hun oude id's (`btnMap`, `btnWall`, `btnBackup`, `btnSettings`); klik buiten of Esc sluit het menu.
- 🧑 **Camerabediening inklapbaar** (klik op de titel; `localStorage.ctrlFolded`).
- 🧑 **⛶ Volledig scherm** / toets F: `body.wall` verbergt kop en zijbalk, zet weergave op Alle camera's en vraagt browser-fullscreen. Esc / fullscreen verlaten → terug naar vorige weergave.
- **Scènes** = meerdere camera's tegelijk naar een preset (vergelijkbaar met SuperPresets op een PTZOptics-joystick).

## 3. Camera-protocol (PTZOptics)

- ✅ HTTP-CGI: `http://<ip>/cgi-bin/ptzctrl.cgi?ptzcmd&...` met `up/down/left/right/leftup/rightup/leftdown/rightdown`, `ptzstop`, `home`, `zoomin/zoomout/zoomstop`, `poscall/posset` (bron: PTZOptics developer docs).
- ✅ Zoom-snelheid via `zoomin&<speed>`.
- ⚠️ Snelheden: **pan 1–24, tilt 1–20, zoom/focus 1–7**. Standaard VISCA-bereik; niet per model gecontroleerd.
- ⚠️ **Focus: `focusin` = ver, `focusout` = dichtbij.** Niet geverifieerd; kan omgekeerd zijn. Knoppen heten "Focus ver/dichtbij".
- ⚠️ `poscall&<n>&<speed>` (preset-snelheid) is waarschijnlijk niet op alle firmware ondersteund (stond als feature request op het PTZOptics-forum). Backend ondersteunt het, de UI gebruikt het niet.
- ⚠️ UI toont standaard 12 presets (instelbaar 1–64).
- ✅ **Gereserveerde presets 90–99** (bron: PTZOptics-documentatie over preset-functies): 0 = opstartpositie, **90 = home, 95 = OSD-menu aan/uit**, 91–94 en 96–99 vast. Normaal bereik VISCA: 1–89 en 100–254. `do_action()` **weigert** `preset_call`/`preset_set` op 90–99 (dus ook via de Xbox-controller en een eventuele knopindeling); `backup.RESERVED` / `usable()` slaan ze over bij uitlezen, laden en terugzetten. **Nooit omzeilen**: per ongeluk 95 oproepen opent het OSD-menu in de livestream.
- ⚠️ **Autofocus gaat altijd via VISCA over TCP poort 5678**, ook bij protocol HTTP, omdat er geen betrouwbaar HTTP-commando voor gevonden is:
  - auto `81 01 04 38 02 FF`, handmatig `81 01 04 38 03 FF`, one-push `81 01 04 18 01 FF`.
- ⚠️ VISCA over IP = **raw VISCA over TCP 5678** (geen Sony VISCA-over-IP-header). Pan/tilt `81 01 06 01 VV WW XX YY FF`, preset `81 01 04 3F 02/01 pp FF`.
- ⚠️ Snapshot voor thumbnails: `http://<ip>/snapshot.jpg`.
- ⚠️ Inloggen: Basic én Digest auth worden geprobeerd als gebruiker/wachtwoord is ingevuld.
- Camera-host mag een poort bevatten (`ip:poort`); exacte host-match gaat voor IP-match (alleen relevant voor tests met meerdere nep-camera's op één IP).

## 4. PTZJoy-joystick (verwijderd)

- 🧑 Op verzoek verwijderd: endpoints `/api/joy/*`, de 🕹-dialoog en de README-sectie. De Xbox-controller vervangt hem.
- Camerakeuze delen tussen schermen gaat via `POST /api/select` + SSE `select` ("Volg selectie" rechtsboven).

## 5. Xbox-controller

- Via de **Gamepad API** van de browser ("standard" mapping). ✅ Getest met een nagebootste gamepad in Chromium, **niet** met een echte controller.
- ⚠️ Werkt alleen als de bedieningspagina **open en zichtbaar** is. Bij tabblad naar achtergrond / venster kwijt → alles stoppen.
- Vast: linker stick pan/tilt (kwadratische curve, deadzone 0.18, 8 richtingen), RT/LT zoom, rechter stick ↑↓ focus.
- 🧑 Knoppen instelbaar: `cfg.pad_map = {buttons: {A: "preset:1", …}, shift: 4}` in `cameras.json` (ontbrekende knoppen vallen terug op `PAD_DEFAULT`). Acties: `preset:N`, `cam_prev`, `cam_next`, `cam:N`, `scene_next`, `scene:N`, `home`, `track`, `af`, `fine`, `stopall`, en modifiers `shift` (presets +shift) en `store` (vasthouden + presetknop = opslaan, trilt).
- Standaard: A/B/X/Y preset 1–4, LB shift (5–8), Menu opslaan, D-pad ←/→ camera, ↑ volgende scène, ↓ home, RB volgen, L3 fijn-modus (max/4), R3 one-push AF, View alles stoppen.
- Zachte beweging, tilt omkeren en max. snelheid blijven per apparaat (localStorage).
- Commando's alleen bij verandering, maximaal ~12/s (80 ms).
- 🧑 Instellingen (zacht, omkeren, max.) staan in een kaart in de **zijbalk onder de camerabediening**; 🎮-status in de kop scrolt ernaartoe.
- 🧑 **Knoppenindeling** in dialoog `#dlgMap`, te openen via het menu (🕹 Knoppenindeling) en vanuit de zijbalkkaart. Zolang die open is bestuurt de controller niets, maar een ingedrukte knop licht zijn regel op (`padHit`).
- 🧑 **Zachte beweging** (optie, standaard aan, `localStorage.padSoft`): derdemachtscurve i.p.v. kwadratisch, en optrekken met max. `max/12` stap per verzonden commando (~1 s naar topsnelheid); zoom +1 per commando. Stoppen gaat altijd direct.
- ⚠️ De Xbox-code was per ongeluk meeverwijderd in de OBS-commit (d040960) en is daarna teruggezet. Let bij grote verwijderingen in `index.html` op dat de sectie `Xbox-controller (Gamepad API)` blijft staan.
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

## 7. Back-up van presets en camera-instellingen

- **Back-upbestand** (`GET /api/backup?passwords=0|1`, `POST /api/backup/restore`): JSON met `type: "ptz-control-backup"`, `version: 1`, de volledige config en thumbnails als base64 (`"<camid>/<n>"`). Zonder wachtwoorden → bij terugzetten blijven de huidige wachtwoorden per camera-id behouden.
- **Uitlezen/terugzetten** (`POST /api/backup/capture` / `/api/backup/apply`) draaien als **taak op de achtergrond**, per camera parallel, voortgang via SSE-event `job`. Maar één taak tegelijk. Volgen wordt voor die camera's eerst gestopt.
- Resultaat staat in `cameras.json` per camera als `saved_positions` (`{"<n>": {pan, tilt, pt_nibbles, zoom, af, focus, _captured}}`) en `saved_image`. **`POST /api/config` mag deze nooit overschrijven** (server houdt zijn eigen versie), zodat een verouderd scherm ze niet kan wissen.
- Na uitlezen gaat elke camera terug naar de preset die LIVE stond.
- ⚠️ Alles via **VISCA over TCP 5678**, ook voor camera's die verder HTTP-CGI gebruiken. Gebruikte (standaard Sony/PTZOptics) commando's, **niet op hun camera's getest**:
  - positie: `81 09 06 12 FF` → `90 50 0p×4 0t×4 FF` (bij 9–10 nibbles: pan met 5 nibbles), zoom `81 09 04 47 FF`, focus `81 09 04 48 FF`, AF-modus `81 09 04 38 FF`.
  - terug: absoluut `81 01 06 02 14 12 <pan> <tilt> FF`, zoom `81 01 04 47 …`, focus `81 01 04 48 …` (alleen bij handmatige focus).
  - "aangekomen" = twee metingen 0,35 s na elkaar gelijk (pan/tilt/zoom/focus), min. 0,8 s, max. 10 s.
  - beeld: AE-modus `39`, sluiter `4A`, iris `4B`, gain `4C`, helderheid `4D`, belichtingscorr. `3E`/`4E`, tegenlicht `33`, witbalans `35`, R/B-gain `43`/`44`, scherpte `42`, verzadiging `49`, flip `66`, mirror `61`. Modi worden eerst teruggezet, dan waarden; weigeringen (bv. sluitertijd in automatische modus) worden als melding getoond, niet als fout.
  - Niet-ondersteunde inquiries worden overgeslagen en gemeld ("niet ondersteund: …").
- ✅ Getest met een **nagebootste VISCA-camera** (onthoudt posities/presets/beeld en beweegt geleidelijk): uitlezen exact gelijk, na "reset" + terugzetten alle presets en beeld identiek; ook via de UI (Playwright). Gevonden en opgelost: focus werd uitgelezen terwijl die nog bewoog.
- **Presets laden vanaf de camera** (`POST /api/presets/load {cams, upto, thumbs}`, taak-soort `load`): er bestaat **geen API om de presetlijst of -namen op te vragen** (niet gevonden in PTZOptics-docs). Daarom: preset 1..`upto` (max 89, zonder 90–99) oproepen, wachten tot stil, foto via `/snapshot.jpg`, positie bewaren. ⚠️ **"Leeg" = camera bewoog niet** t.o.v. de vorige stand (aanname: een niet-ingestelde preset doet niets bij oproepen; niet getest op hun camera's). Twee opeenvolgende presets op exact dezelfde plek worden dus ten onrechte als leeg gezien. Foto's van lege presets worden weer verwijderd; `preset_count` groeit mee (max 64). Bestaande presetnamen blijven staan; tegels met foto maar zonder naam tonen "Preset n".
- ⚠️ Fabrieksreset kan het IP wijzigen; pas dat eerst aan bij ⚙ Camera's (camera-id blijft gelijk, dus de bewaarde data blijft gekoppeld).

## 8. Bewust níet in de software

- 🧑 **YouTube/stream starten en stoppen** (en opnemen). Was eerst gebouwd via OBS WebSocket, daarna op verzoek volledig verwijderd (server, UI). Een oude `obs`-sectie in `cameras.json` wordt bij opslaan weggegooid.
- 🧑 **Live preview** van de camerabeelden.

## 9. Installatie en platform

- 🧑 Mac mini (Apple Silicon). `.venv` moet **op de Mac zelf** worden gemaakt (een in de cloud gebouwde venv werkt daar niet). De starter doet dat automatisch bij de eerste start en na gewijzigde requirements.
- ⚠️ `PTZ Control.app` is op Linux gebouwd en **niet op een Mac geprobeerd** (Gatekeeper kan eenmalig rechtsklik → Open vragen; Finder kan het icoon even cachen).
- ⚠️ Windows-scripts (`.bat`, `snelkoppeling.ps1`) zijn **niet uitgevoerd** (geen Windows beschikbaar), alleen gereviewd. `.gitattributes` forceert CRLF voor `.bat` en LF voor `.command`.
- ⚠️ Windows met NVIDIA: `ultralytics` installeert standaard CPU-torch; GPU vereist de CUDA-index (zie README).
- macOS Gatekeeper ("onbekende ontwikkelaar") en Windows SmartScreen/firewall vragen eenmalig om bevestiging.

## 10. Werken aan deze repo

- **Talen:** Nederlands voor UI-teksten, README, commitberichten en uitleg aan de gebruiker.
- **Nooit committen:** `cameras.json`, `thumbs/`, `.venv/`, `*.pt`.
- **Controles na wijzigingen:**
  ```bash
  python3 -m py_compile ptz_control.py tracker.py backup.py
  python3 -c "import re;s=open('index.html').read();open('/tmp/a.js','w').write(re.search(r'<script>(.*)</script>',s,re.S).group(1))" && node --check /tmp/a.js
  bash -n "Start PTZ Control.command" && bash -n "PTZ Control.app/Contents/MacOS/PTZ Control"
  ```
- **Testen zonder hardware:** gebruik nep-camera's (kleine `ThreadingHTTPServer` die `ptzctrl.cgi`-aanroepen logt en een JPEG op `/snapshot.jpg` geeft), voor back-ups een nep-VISCA-TCP-camera met toestand (posities, presets, beeld, geleidelijke beweging), en voor volgen een `SimGrabber` + `FakeDetector` die `tracker.FrameGrabber` / `_DETECTOR` vervangen. Xbox testen door `navigator.getGamepads` te vervangen in Playwright.
- Wijzig je camera-commando's, controleer dan beide paden (HTTP-CGI én VISCA) en dat `do_action()` tracking correct stopt.
- Eerst zonder echte hardware getest; **alles met ⚠️ hierboven moet bij de eerste repetitie in de kerk gecontroleerd worden**.
