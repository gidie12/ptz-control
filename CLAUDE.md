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
- 🧑 **Geen YouTube/stream-bediening in deze software** (starten/stoppen). Een eerdere OBS-koppeling is op verzoek verwijderd; streamen gebeurt buiten deze app. Niet opnieuw toevoegen zonder expliciete vraag.
- 🧑 **YouTube-stream wél bekijken** (op verzoek, nov 2026): de server herkent automatisch welke stream live is op een instelbaar kanaal en de UI toont die in een **klein, verplaatsbaar en schaalbaar venster**. Zie §8a.
- 🧑 **Licht en donker thema**, keuze per apparaat (zie §2).
- 🧑 Draait op een **Mac mini (Apple Silicon)** ("livestreams-mac-mini"). Er is ook een **Windows-starter** gevraagd.
- 🧑 **Alle presets en camera-instellingen moeten bewaard en teruggezet kunnen worden**: app-instellingen (back-upbestand), presetposities zelf én beeldinstellingen.
- 🧑 **Presets moeten geladen kunnen worden vanaf de camera** (bestaande presets in de app krijgen, met foto).
- 🧑 UI, README, commits en communicatie in het **Nederlands**.
- ⚠️ Het cameramodel is **onbekend** (Move/Studio/G2/G3 niet bevestigd). Daarom geen ingebouwde auto-tracking gebruikt.

## 2. Architectuur

| Bestand | Rol |
|---|---|
| `ptz_control.py` | HTTP-server (alleen stdlib), camera-commando's (HTTP-CGI + VISCA), config, SSE-events, tracking-beheer |
| `index.html` | Volledige UI (vanilla JS, geen build), presets, handbediening, Xbox-controller, dialogen |
| `tracker.py` | Volgmodule: RTSP-grabber, persoonsdetectie (YOLO of OpenCV HOG), pan-regelaar |
| `youtube.py` | Live-detectie van het YouTube-kanaal (zonder API-sleutel) + achtergrond-`Watcher` |
| `backup.py` | Back-up: VISCA-inquiries, presetposities lezen/terugzetten, beeldinstellingen lezen/terugzetten |
| `Start PTZ Control.command` / `.bat` | 🧑 **Installer en starter in één.** Installeert als `.venv` ontbreekt, als `requirements.txt` verschilt van `.venv/requirements.installed` (kopie die pas na een geslaagde installatie wordt gezet) of met `--installeer` / `/installeer`. Start daarna `ptz_control.py --open`; overige argumenten gaan door. Er zijn geen aparte `Installeer.*`-scripts meer. |
| `PTZ Control.app` | 🧑 **Dock-app (macOS)** met eigen icoon. `Contents/MacOS/PTZ Control` is een bash-script: draait de server al op poort 8080 (`PTZ_PORT`) → alleen browser openen; anders `open -a Terminal "Start PTZ Control.command"`. Zoekt de repo als map náást de .app, dus de .app moet in de repo blijven (Dock = verwijzing). Uitvoerbaar bit moet in git blijven (100755). |
| `app/` | Icoon (`maak_icoon.py` tekent `icon.png`, `ptz.ico` en `AppIcon.icns` met Pillow) en `snelkoppeling.ps1` (Windows: `PTZ Control.lnk` in Startmenu + bureaublad, doel `cmd.exe /s /c ""…Start PTZ Control.bat""` zodat hij aan de taakbalk kan). De .bat roept dit aan als de Startmenu-snelkoppeling ontbreekt of met `/snelkoppeling`. |
| `cameras.json` | Lokale config (camera's, presetnamen, uitgelezen `saved_positions` / `saved_image`). **Bevat camerawachtwoorden in platte tekst. Nooit committen** (staat in `.gitignore`) |
| `thumbs/` | Preset-thumbnails (`<camid>_<n>.jpg`), lokaal, niet committen |

- Kern heeft **geen externe packages** nodig; numpy/opencv/ultralytics zijn alleen voor volgen. ⚠️ Python **3.9+** (installers checken dat; de code zelf vermijdt nieuwere syntax).
- Server luistert op **0.0.0.0:8080**, **zonder authenticatie**. ⚠️ Aanname: vertrouwd kerknetwerk. Iedereen op het LAN kan de camera's bedienen (en een back-up met wachtwoorden downloaden).
- `do_HEAD` bestaat alleen voor `/api/thumb` (de UI checkt daarmee of een thumbnail bestaat na een `thumb`-event) en `/`. Ontbrak eerst, waardoor nieuwe thumbnails pas na herladen zichtbaar werden.
- Live-sync tussen schermen via **Server-Sent Events** (`/api/events`): `select`, `preset`, `thumb`, `config`, `track`, `job`, `state`, `youtube`.
- Per-apparaat-instellingen (snelheden, Xbox-opties, thema, YouTube-venster) staan in **localStorage**, niet op de server. Tegelgrootte en aantal tegels staan in `cameras.json` (gedeeld).
- 🧑 **Thema** licht/donker via `html[data-theme]` en CSS-variabelen (`:root` = donker, `:root[data-theme=light]` = licht). Geen losse kleurcodes meer in de CSS gebruiken: voeg een variabele toe aan beide blokken. `localStorage.theme` = `auto`/`dark`/`light` (auto volgt `prefers-color-scheme`); een klein script in `<head>` zet het thema vóór het tekenen. Wisselen alleen via het menu (`#btnTheme`, licht ↔ donker); 🧑 het thema staat op verzoek **niet** in een instellingenvenster. Staat er nog `auto`, dan blijft dat gelden tot iemand wisselt.
- 🧑 **Bewerk-knop op presettegels**: SVG-potlood op een knop met themakleuren (`--editbg/--editfg/--editline`); was in het lichte thema te donker. Klikdetectie via `closest(".edit")`.
- 🧑 **Geen ⚙ Instellingen meer in het 🎮-menu** (op verzoek weggehaald, dubbel met het YouTube-menu). De dialoog `#dlgPrefs` (titel *YouTube-kanaal*, alleen het kanaal) opent via *Kanaal instellen…* in `#ytMenu` en via de knop in het YouTube-venster als er nog geen kanaal is. De camera-dialoog heet in het menu 📷 Camera's (id `btnSettings` ongewijzigd).
- Standaardweergave is **"Alle camera's"** (kolommen) zodra er >1 camera is.
- 🧑 **Menuknop "Menu ▾"** (`#btnPad`): gewone knop zonder controllericoon (🎮 + statusbolletje op verzoek weggehaald). De controllerstatus staat in de Xbox-kaart: groene badge `#padBadge` "● Verbonden", die zacht oplicht tijdens bewegen (`.act`).
- 🧑 **Menu onder die knop** (`#mainMenu`): Sneltoetsen, Volledig scherm, Back-up, Camera's, Presetaantal testen. De knoppen houden hun oude id's (`btnKeys`, `btnWall`, `btnBackup`, `btnSettings`, `btnCap`); klik buiten of Esc sluit het menu. 🧑 Het item *Controller-instellingen* (`btnPadSet`, sprong naar de Xbox-kaart) is op verzoek weggehaald; de instellingen staan in de zijbalk.
- 🧑 **Volledig scherm** (`body.wall`) is alleen om te bedienen: **📥 Laden van camera** en **＋ Preset opslaan** zijn daar verborgen, en een actieve opslaan-modus wordt bij het openen uitgezet (zodat een tik geen preset overschrijft).
- 🧑 **Knoppenindeling heeft één ingang**: de knop `btnMap2` in de Xbox-kaart in de zijbalk. Een tweede menu-item (`btnMap`) gaf dubbele knoppen en is op verzoek weggehaald; niet opnieuw toevoegen.
- 🧑 **Alle sneltoetsen instelbaar** in **⌨ Sneltoetsen** (`#dlgKeys`, menu-item `btnKeys`), gedeeld via `cameras.json`: camera kiezen per camera (`cfg.cameras[i].hotkey`, standaard `Shift+Digit<n>`, n ≤ 9) en de bediening via **`cfg.keymap = {actie: combinatie}`** (alleen afwijkingen; `"none"` = uit). Acties en standaard in `KEY_ACTIONS`: `up/down/left/right` (pijltjes), `zoom_in/zoom_out` (Q/A), `focus_far/focus_near` (W/S), `stop` (Spatie), `home` (H), `track` (T), `wall` (F), `youtube` (Y), `preset_1..9` (1–9). Combinatie = `KeyboardEvent.code` met `Ctrl+`/`Alt+`/`Shift+` (`comboOf`). Backspace = standaard, Delete = uit; dubbele toetsen worden rood gemarkeerd maar niet geblokkeerd. **Esc is vast**; Cmd-combinaties (`metaKey`) gaan altijd naar de browser. Vasthouden (bewegen/zoom/focus) loopt via `holding` (code → actie), zodat loslaten ook werkt als Shift al los is; Num 2/4/6/8 zonder NumLock tellen als pijltje (`combosOf`). De uitleg onder de zijbalk (`#keyHints`), het menu (Volledig scherm) en het YouTube-menu tonen de ingestelde toetsen (`renderKeyHints`). In *Alle camera's* kiest de camera-toets alleen de bediende camera (`selectCam`, origin `key`); in de enkele weergave wisselt hij van tabblad. ✅ Getest met Playwright (toets wijzigen, uitzetten, dubbel, opslaan, bediening met nieuwe toetsen).
- 🧑 **Camerabediening inklapbaar** (klik op de titel; `localStorage.ctrlFolded`).
- 🧑 **⛶ Volledig scherm** / toets F: `body.wall` verbergt kop en zijbalk, zet weergave op Alle camera's en vraagt browser-fullscreen. Esc / fullscreen verlaten → terug naar vorige weergave.
- 🧑 **Geen scènes** (meerdere camera's tegelijk naar een preset). Op verzoek verwijderd: camera's mogen niet tegelijk bewegen, omdat de regie eerst via de videomixer naar een ander shot schakelt en dan pas de camera die niet live is verplaatst. Niet opnieuw toevoegen zonder expliciete vraag. Een oude `scenes`-sectie in `cameras.json` wordt bij opslaan weggegooid, oude `scene*`-knoppen in `pad_map` worden genegeerd.

## 2a. Workspaces

- 🧑 **Workspaces** = aparte sets instellingen, alsof het aparte installaties zijn (bijv. per locatie). Per workspace eigen `cameras.json` (camera's, presets, tegels, `pad_map`, `keymap`, YouTube-kanaal, `saved_*`) en `thumbs/`.
- Opslag: `DATA_DIR/workspaces.json` = `{active, list:[{id,name}]}`. **`standaard` gebruikt de oude plek** (`DATA_DIR/cameras.json` + `thumbs/`), zodat bestaande installaties niets verhuizen; andere in `DATA_DIR/workspaces/<id>/`. `workspaces/` en `workspaces.json` staan in `.gitignore` (wachtwoorden).
- **Eén actieve workspace per server**, voor alle schermen. `ws_select()` weigert tijdens een taak, stopt volgen/timers, zet de globale `CONFIG_PATH`/`THUMB_DIR` om (alle code leest die op het moment zelf), wist `STATE`/`JOB`, zet de YouTube-status terug, stuurt SSE `workspace` (schermen herladen) en `YT.refresh()`.
- API: `GET /api/workspaces`, `POST /api/workspaces {action: select|create|rename|delete, …}`. `create` met `copy_from` kopieert config + foto's; zonder = `DEFAULT_CONFIG`. `standaard` en de actieve workspace kunnen niet weg; verwijderen wist de map definitief (UI vraagt bevestiging).
- **Per-apparaat-instellingen (localStorage) ook per workspace**: de server zet `<meta name="ptz-workspace">` in `index.html`; `store` gebruikt prefix `ws:<id>:` (niet voor `standaard`, niet voor `theme`). Bij *Kopie van* kopieert de UI ook die localStorage-sleutels van dit apparaat.
- UI: knop **📍 <naam> ▾** (`#btnWs`, menu `#wsMenu`) naast de titel: wisselen (met bevestiging), *Nieuwe workspace…*, *Workspaces beheren…* (`#dlgWs`: maken leeg/kopie, hernoemen met Enter, openen, verwijderen). Paginatitel krijgt de naam als er meer dan één is.
- 🧑 **Back-ups en workspaces**: een gewone back-up (`type: ptz-control-backup`) is van de **actieve workspace** en bevat `workspace: {id, name}`; bestandsnaam `ptz-control-backup-<naam>-<datum>.json`. Vinkje *alle workspaces* (`/api/backup?all=1`, alleen zichtbaar bij >1) → `type: ptz-control-backup-all` met `workspaces: [<back-up per workspace>]`. Terugzetten (`restore_any`) via dialoog `#dlgRestore`: één workspace **in de huidige** (`_target: current`, wachtwoorden blijven als de back-up ze niet heeft) of **als nieuwe workspace** (`_target: new`, naam uit de back-up, bij dubbele naam + datum); een alle-workspaces-back-up vervangt workspaces met hetzelfde id en maakt ontbrekende aan; andere blijven staan. `make_backup`/`restore_backup` werken op `ws_paths(id)` (ook niet-actieve workspaces); alleen bij de actieve volgen SSE `config` en `YT.refresh()`. Oude back-ups zonder `workspace` werken gewoon. ✅ Getest (API + Playwright).
- CLI: `--workspace <naam of id>` start in die workspace.
- ✅ Getest (Playwright + API): maken leeg/kopie, wisselen met twee schermen (beide herladen), aparte camera's/YouTube/localStorage, terug, verwijderen, `--workspace`.

## 3. Camera-protocol (PTZOptics)

- ✅ HTTP-CGI: `http://<ip>/cgi-bin/ptzctrl.cgi?ptzcmd&...` met `up/down/left/right/leftup/rightup/leftdown/rightdown`, `ptzstop`, `home`, `zoomin/zoomout/zoomstop`, `poscall/posset` (bron: PTZOptics developer docs).
- ✅ Zoom-snelheid via `zoomin&<speed>`.
- ⚠️ Snelheden: **pan 1–24, tilt 1–20, zoom/focus 1–7**. Standaard VISCA-bereik; niet per model gecontroleerd.
- ⚠️ **Focus: `focusin` = ver, `focusout` = dichtbij.** Niet geverifieerd; kan omgekeerd zijn. Knoppen heten "Focus ver/dichtbij".
- ⚠️ `poscall&<n>&<speed>` (preset-snelheid) is waarschijnlijk niet op alle firmware ondersteund (stond als feature request op het PTZOptics-forum). Backend ondersteunt het, de UI gebruikt het niet.
- 🧑 **Aantal tegels** (`cfg.preset_count`) = precies zoveel tegels per camera: de eerste N bruikbare nummers (90–99 overgeslagen, dus 95 tegels = 1–89 + 100–105). Instelbaar 1–244 met **"Tegels − N +"** boven het raster (en bij ⚙ Camera's). Presets met naam/foto buiten beeld geven een melding "… niet zichtbaar: klik om alles te tonen". `backup.first_usable()` / `count_upto()` tellen op dezelfde manier.
- 🧑 **Tegelgrootte** (`cfg.tile_size`, 100–320 px) en het aantal tegels worden **automatisch opgeslagen in `cameras.json`** (na 0,6 s), dus gelijk op elk scherm en na herstart. Eerder stond de grootte alleen in localStorage per browser.
- ✅ **Gereserveerde presets 90–99** (bron: PTZOptics-documentatie over preset-functies): 0 = opstartpositie, **90 = home, 95 = OSD-menu aan/uit**, 91–94 en 96–99 vast. Normaal bereik VISCA: 1–89 en 100–254. `do_action()` **weigert** `preset_call`/`preset_set` op 90–99 (dus ook via de Xbox-controller en een eventuele knopindeling); `backup.RESERVED` / `usable()` slaan ze over bij uitlezen, laden en terugzetten. **Nooit omzeilen**: per ongeluk 95 oproepen opent het OSD-menu in de livestream.
- ⚠️ **Autofocus gaat altijd via VISCA over TCP poort 5678**, ook bij protocol HTTP, omdat er geen betrouwbaar HTTP-commando voor gevonden is:
  - auto `81 01 04 38 02 FF`, handmatig `81 01 04 38 03 FF`, one-push `81 01 04 18 01 FF`.
- ⚠️ VISCA over IP = **raw VISCA over TCP 5678** (geen Sony VISCA-over-IP-header). Pan/tilt `81 01 06 01 VV WW XX YY FF`, preset `81 01 04 3F 02/01 pp FF`.
- ⚠️ Snapshot voor thumbnails: `http://<ip>/snapshot.jpg`.
- 🧑 **Elke `preset_set` maakt automatisch een thumbnail** (server-side in `do_action`, dus ook via Xbox en het presetvenster). `snapshot_thumb()` probeert 2× (0,5 s ertussen), accepteert alleen JPEG (`FF D8`) en geeft de reden terug; `/api/cmd` stuurt die mee als `thumb_error`, de UI meldt het (`storedToast`).
- ⚠️ Inloggen: Basic én Digest auth worden geprobeerd als gebruiker/wachtwoord is ingevuld.
- Camera-host mag een poort bevatten (`ip:poort`); exacte host-match gaat voor IP-match (alleen relevant voor tests met meerdere nep-camera's op één IP).

## 4. PTZJoy-joystick (verwijderd)

- 🧑 Op verzoek verwijderd: endpoints `/api/joy/*`, de 🕹-dialoog en de README-sectie. De Xbox-controller vervangt hem.
- Camerakeuze delen tussen schermen gaat via `POST /api/select` + SSE `select` ("Volg selectie" rechtsboven).

## 5. Xbox-controller

- Via de **Gamepad API** van de browser ("standard" mapping). ✅ Getest met een nagebootste gamepad in Chromium, **niet** met een echte controller.
- ⚠️ Werkt alleen als de bedieningspagina **open en zichtbaar** is. Bij tabblad naar achtergrond / venster kwijt → alles stoppen.
- Vast: linker stick pan/tilt (kwadratische curve, deadzone 0.18, 8 richtingen), RT/LT zoom, rechter stick ↑↓ focus.
- 🧑 Knoppen instelbaar: `cfg.pad_map = {buttons: {A: "preset:1", …}, shift: 4}` in `cameras.json` (ontbrekende knoppen vallen terug op `PAD_DEFAULT`). Acties: `preset:N`, `cam_prev`, `cam_next`, `cam:N`, `home`, `track`, `af`, `fine`, `stopall`, en modifiers `shift` (presets +shift) en `store` (vasthouden + presetknop = opslaan, trilt).
- Standaard: A/B/X/Y preset 1–4, LB shift (5–8), Menu opslaan, D-pad ←/→ camera, ↑ niets, ↓ home, RB volgen, L3 fijn-modus (max/4), R3 one-push AF, View alles stoppen.
- Zachte beweging, tilt omkeren en max. snelheid blijven per apparaat (localStorage).
- Commando's alleen bij verandering, maximaal ~12/s (80 ms).
- 🧑 **Groene badge "● Verbonden"** (`#padBadge`) naast de titel van de Xbox-kaart zolang er een controller is (`padUI`); daaronder de naam van de controller zonder de technische (…)-codes.
- 🧑 Instellingen (zacht, omkeren, max.) staan in een kaart in de **zijbalk onder de camerabediening**; 🎮-status in de kop scrolt ernaartoe.
- 🧑 **Knoppenprofielen** (per persoon): `cfg.pad_profiles = [{id, name, map}]` in `cameras.json` (dus per workspace en in back-ups); profiel `standaard` = de bestaande `cfg.pad_map` (naam in `cfg.pad_std_name`). **Welk profiel actief is, onthoudt elk scherm zelf** (`localStorage.padProfile`, valt terug op standaard als het profiel weg is). `padMap()` gebruikt het actieve profiel; `savePadMap`/*Standaard terugzetten* schrijven alleen in dat profiel. Kiezen in de Xbox-kaart (`#padProfCard`) en in `#dlgMap` (`#padProfMap`, + Nieuw profiel = kopie van het huidige, Hernoemen, 🗑; standaard kan niet weg). Zacht/omkeren/max blijven per scherm, niet per profiel. ✅ Getest met Playwright.
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
- **Presetaantal testen** (`POST /api/presets/captest {cams}`, taak-soort `captest`, menu 🧪): per nummer: oproepen (bestond hij al = camera bewoog), camera `CAP_OFFSET` opzij en opslaan, de andere kant op, terugroepen; "werkt" = terug binnen `CAP_TOL`. Bestaande preset daarna exact teruggezet; was hij leeg, dan **gewist met VISCA Memory Reset `81 01 04 3F 00 pp FF`** (⚠️ niet op hun camera's getest; lukt dat niet, dan meldt de app dat er een testpositie achterblijft). Eerst `CAP_PROBES` (1, 32, 64, 89, 100, 128, 150, 200, 254), daarna halveren tussen hoogste werkende en laagste falende. Resultaat in `cameras.json` als `preset_capacity` (beschermd tegen overschrijven, zoals `saved_*`). ✅ Getest met nep-camera's zonder limiet, die boven 127 weigeren en die boven 64 stil negeren: 254 / 127 / 64 gevonden, bestaande presets identiek, geen 90–99 verstuurd.
- ⚠️ Fabrieksreset kan het IP wijzigen; pas dat eerst aan bij ⚙ Camera's (camera-id blijft gelijk, dus de bewaarde data blijft gekoppeld).

## 8a. YouTube-livestream bekijken

- Config: `cfg.youtube = {channel}` in `cameras.json` (gedeeld). `POST /api/config` valideert het kanaal met `youtube.channel_path()` (400 bij onzin) en **behoudt** de bestaande `youtube` als een scherm hem niet meestuurt.
- Kanaal mag zijn: `@naam`, `naam`, `UC…`-id, of een link (`/@`, `/channel/`, `/c/`, `/user/`). Of één stream/video (`/live/<id>`, `watch?v=<id>`, `youtu.be/<id>`): dan wordt `/watch?v=<id>` opgehaald i.p.v. `<kanaal>/live`.
- ✅ **Echte YouTube gecontroleerd** (9 nov 2026, ingebouwde browser, kanaal @DrDonutt live): `/@naam/live` stuurt door naar `/naam/live`, de pagina bevat `ytInitialPlayerResponse` met `liveBroadcastDetails.isLiveNow: true` en een canonical `watch?v=`-link; met cookie `SOCS=CAI` geen toestemmingspagina. De Python-fetch zelf is nog niet vanaf de Mac geprobeerd.
- ⚠️ **Server herstarten na een update**: `index.html` wordt per verzoek van schijf gelezen, de Python-code niet. Na een `git pull` zonder herstart toont de UI wel de YouTube-knop maar geeft `/api/youtube` 404; de UI meldt dan dat de server oud is (`YT_OLD`). Zo gevonden bij de gebruiker.
- `certifi` staat in `requirements.txt` en wordt (indien aanwezig) als extra CA-bestand geladen: Python van python.org op macOS heeft anders vaak geen root-certificaten (`CERTIFICATE_VERIFY_FAILED`).
- ⚠️ **Detectie zonder API-sleutel**: server haalt `https://www.youtube.com/<kanaal>/live` op (cookies `SOCS`/`CONSENT` tegen de EU-toestemmingspagina) en leest `ytInitialPlayerResponse` (`liveBroadcastDetails.isLiveNow`, `videoDetails.isUpcoming/videoId/title`), met de canonical-link als terugval. Dit is geen officiële API: als YouTube de pagina verandert, kan het breken. Alleen getest met een **nagebootste YouTube-server** (`PTZ_YT_BASE=http://…` vervangt de basis-URL); echte YouTube was vanuit de bouwomgeving niet bereikbaar. Bij de eerste dienst controleren. Alternatief bij problemen: YouTube Data API (kost quota: `search` = 100 eenheden/aanroep).
- 🧑 **Kanaal altijd wijzigbaar**, ook tijdens een stream (op verzoek, 10 nov 2026; de eerdere vergrendeling met 🔒-melding en `youtube_locked` is verwijderd, niet opnieuw toevoegen). Na wijzigen controleert de server direct het nieuwe kanaal; `_check_sticky` houdt de oude stream alleen vast als het kanaal gelijk is gebleven.
- 🧑 **Lopende stream blijft staan** (`Watcher._check_sticky`): is er al een stream live, dan controleert de server eerst die stream zelf (`watch?v=<id>`). Alleen als die afgelopen is, kijkt hij weer naar het kanaal. De LIVE-knop en het venster springen dus niet naar een tweede stream van hetzelfde kanaal en gaan niet uit door een afwijkende `/live`-pagina. ✅ Getest met nep-pagina's.
- Controle elke 60 s (fout: 120 s), `POST /api/youtube/check` = nu controleren, `GET /api/youtube` = status. Bij een netwerkfout blijft de laatst bekende stream staan.
- UI: knop `#btnYt` in de kop (grijs / rood **LIVE** / oranje **Gepland**). 🧑 **Klik = keuzemenu** `#ytMenu` (`ytMenuRender()`): status, venster tonen/verbergen, automatisch plaatsen ✓, grootte Klein/Middel/Groot, met geluid ✓, automatisch openen ✓, nu controleren, openen op YouTube, kanaal instellen. Vinkjes en grootte laten het menu open. Toets **Y** = direct venster tonen/verbergen (`ytToggle()`). Het YouTube-menu en het hoofdmenu sluiten elkaar. Venster `#ytWin`: slepen aan `.ythead`, schalen met `#ytGrip` (alleen breedte, 16:9 vast), min. 220 px. `localStorage`: `ytGeom {x,y,w}`, `ytAuto` (standaard aan), `ytSound` (standaard uit → `mute=1`, anders blokkeert autoplay).
- 🧑 **Automatische plaatsing** (standaard, `ytGeom.auto !== false`): `ytAutoSpot()` zoekt de grootste vrije plek (breedte ≤ gekozen grootte, min. 220 px, helemaal in beeld) **rechts naast de laatste tegel** van een raster of **gecentreerd onder alle tegels**; de speler bedekt nooit een tegel. Herplaatsen bij elke wijziging van `#presetArea` (Resize/MutationObserver), `body`-class (volledig scherm), scroll en resize. 🧑 **Volledig scherm zonder vrije plek** → `body.ytres`: rechts een strook (`--ytres`, max. 35% breed) vrijhouden, tegels schuiven op, speler rechtsboven. Of de strook nog nodig is, wordt alleen opnieuw bekeken als de indeling verandert (`ytResKey`: tegels, grootte, weergave, schermmaat), anders ging hij heen en weer. Buiten volledig scherm zonder plek: rechtsonder (over de zijbalk). Zelf slepen → `auto: false` (vaste plek); ⌖ in de titelbalk of *Automatisch plaatsen* zet hem terug. ✅ Getest (Playwright, 3 camera's, 8/12/20/40 tegels, live van 8 naar 20): geen overlap, geen heen-en-weer.
- Iframe (`youtube-nocookie.com/embed/<id>`) bestaat **alleen** als het venster open is én er live is; anders verwijderd (geen afspelen/dataverkeer op de Mac mini).
- Zelf gesloten tijdens een livestream → niet steeds opnieuw automatisch openen (`ytUserClosed`, reset bij een nieuwe stream).
- ⚠️ Klik in de speler pakt de focus; na 0,4 s geeft de pagina die terug (`blur`-handler), zodat sneltoetsen en de Xbox-controller blijven werken. Toetsenbordbediening ín de speler werkt daardoor niet (bewust).
- 🧑 **Boven de camera's bij volledig scherm** (optie per apparaat, `localStorage.ytWallTop`, in `#ytMenu`): alleen bij automatisch plaatsen; venster gecentreerd bovenaan, max. 45% schermhoogte; `body.ytrestop` + `--ytrestop` schuift de tegels omlaag. Aanzetten (`ytSetWallTop`) zet automatisch plaatsen weer aan. `setWall()` roept `ytReplace()` aan. ✅ Getest met Playwright.
- ⚠️ Kanalen kunnen insluiten (embedding) uitzetten; dan toont de speler een melding. ↗ opent de stream op YouTube.

## 8. Bewust níet in de software

- 🧑 **YouTube/stream starten en stoppen** (en opnemen). Kijken mag wel (§8a). Was eerst gebouwd via OBS WebSocket, daarna op verzoek volledig verwijderd (server, UI). Een oude `obs`-sectie in `cameras.json` wordt bij opslaan weggegooid.
- 🧑 **Live preview** van de camerabeelden.
- 🧑 **Scènes** (meerdere camera's tegelijk bewegen), zie §2.

## 9. Installatie en platform

- 🧑 Mac mini (Apple Silicon). `.venv` moet **op de Mac zelf** worden gemaakt (een in de cloud gebouwde venv werkt daar niet). De starter doet dat automatisch bij de eerste start en na gewijzigde requirements.
- ⚠️ `PTZ Control.app` is op Linux gebouwd en **niet op een Mac geprobeerd** (Gatekeeper kan eenmalig rechtsklik → Open vragen; Finder kan het icoon even cachen).
- ⚠️ Windows-scripts (`.bat`, `snelkoppeling.ps1`) zijn **niet uitgevoerd** (geen Windows beschikbaar), alleen gereviewd. `.gitattributes` forceert CRLF voor `.bat` en LF voor `.command`.
- ⚠️ Windows met NVIDIA: `ultralytics` installeert standaard CPU-torch; GPU vereist de CUDA-index (zie README).
- macOS Gatekeeper ("onbekende ontwikkelaar") en Windows SmartScreen/firewall vragen eenmalig om bevestiging.

## 9a. Losse app (PyInstaller)

- 🧑 Gevraagd: een **Windows-executable en een macOS-app** zonder Python te installeren.
- `packaging/ptz_control.spec` + `packaging/requirements-build.txt`; gebouwd door **`.github/workflows/build.yml`** op `windows-latest` (één `PTZ Control.exe`, `console=False`) en `macos-15` (arm64, `PTZ Control.app`; `macos-14` is per 2 nov 2026 door GitHub uitgefaseerd en gaf "configuration not supported", `LSUIElement` = geen Dock-icoon, `NSLocalNetworkUsageDescription` voor macOS 15+, ad-hoc `codesign`). Start bij `workflow_dispatch` of tag `v*`. 🧑 Bij *Run workflow* kan de gebruiker **Release maken** aanvinken + versie invullen: dan maakt `softprops/action-gh-release` zelf tag `v<versie>` op de gebouwde commit (`target_commitish`) en de release; zonder vinkje alleen artifacts. 🧑 Leeg versieveld bij release → **automatisch de volgende versie**: job `versie` (ubuntu, `fetch-depth: 0`) pakt de hoogste tag `vX.Y.Z` (`sort -V`) en verhoogt de patch; geen tag → `v1.0.0`. Zelf invullen kan (`1.2.3` of `v1.2.3`, wordt gecontroleerd); bestaat de tag al, dan stopt de bouw. De gekozen versie gaat via `needs.versie.outputs.versie` naar de bouw- en releasejobs en staat in de samenvatting van de run. (Een dynamische standaardwaarde in het startvenster kan GitHub niet.) Kruis-bouwen kan niet: een exe alleen op Windows, een .app alleen op een Mac.
- ⚠️ **Niet op echte Windows/Mac geprobeerd.** Alleen een Linux-build van hetzelfde recept getest (start, `/api/state`, tweede start = "draait al", `/api/quit`, log). De workflow heeft per platform een rooktest (start met `--no-open --no-tray`, haalt `/` op). De Actions-API is vanuit de bouwomgeving niet bereikbaar, dus de uitkomst van de eerste run is hier niet gezien.
- Losse app = `FROZEN`: `index.html` en `icon.png` uit `sys._MEIPASS` (`RES_DIR`); **config en thumbs in `DATA_DIR`**: `%APPDATA%\PTZ Control` / `~/Library/Application Support/PTZ Control` (of `PTZ_DATA`, of draagbaar: een `cameras.json` naast de .exe/.app). Uitvoer naar `DATA_DIR/ptz_control.log` (geen console).
- Opent zelf de browser (tenzij `--no-open`). Poort bezet en het is PTZ Control → alleen browser openen en stoppen; anders melding (MessageBox / `osascript`).
- **Icoon in systeemvak/menubalk** via `pystray` (hoofdthread; server in een thread): openen, LAN-adres, map openen, afsluiten. Zonder pystray draait hij gewoon door. ⚠️ pystray op macOS (PyObjC) en Windows niet getest.
- 🧑 **⏻ PTZ Control afsluiten** in het 🎮-menu (`POST /api/quit` → `os._exit(0)` na 0,3 s), voor alle versies.
- **Geen YOLO/torch** in de losse app (te groot); volgen valt terug op OpenCV-HOG (`opencv-python-headless<5`).
- ⚠️ Niet ondertekend door Apple/Microsoft: Gatekeeper (Systeeminstellingen → Privacy en beveiliging → *Toch openen*, of `xattr -dr com.apple.quarantine`) en SmartScreen vragen eenmalig om bevestiging.

## 10. Werken aan deze repo

- **Talen:** Nederlands voor UI-teksten, README, commitberichten en uitleg aan de gebruiker.
- **Nooit committen:** `cameras.json`, `thumbs/`, `.venv/`, `*.pt`, `build/`, `dist/`.
- **Controles na wijzigingen:**
  ```bash
  python3 -m py_compile ptz_control.py tracker.py backup.py youtube.py
  python3 -c "import re;[open(f'/tmp/a{i}.js','w').write(m) for i,m in enumerate(re.findall(r'<script[^>]*>(.*?)</script>',open('index.html').read(),re.S))]" && for f in /tmp/a[0-9]*.js; do node --check "$f"; done
  bash -n "Start PTZ Control.command" && bash -n "PTZ Control.app/Contents/MacOS/PTZ Control"
  ```
- **Testen zonder hardware:** gebruik nep-camera's (kleine `ThreadingHTTPServer` die `ptzctrl.cgi`-aanroepen logt en een JPEG op `/snapshot.jpg` geeft), voor back-ups een nep-VISCA-TCP-camera met toestand (posities, presets, beeld, geleidelijke beweging), en voor volgen een `SimGrabber` + `FakeDetector` die `tracker.FrameGrabber` / `_DETECTOR` vervangen. Xbox testen door `navigator.getGamepads` te vervangen in Playwright.
- Wijzig je camera-commando's, controleer dan beide paden (HTTP-CGI én VISCA) en dat `do_action()` tracking correct stopt.
- Eerst zonder echte hardware getest; **alles met ⚠️ hierboven moet bij de eerste repetitie in de kerk gecontroleerd worden**.
