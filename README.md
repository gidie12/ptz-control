# PTZ Control

Eigen webbediening voor PTZOptics-camera's, te bedienen met muis/touch, toetsenbord of een Xbox-controller.

## Installeren en starten

**Eén bestand, één klik:** *Start PTZ Control* installeert waar nodig en start daarna de app. De eerste keer maakt hij de `.venv` (een paar minuten); na een update met gewijzigde `requirements.txt` werkt hij de packages vanzelf bij. Daarna opent de bediening in je browser.

| | Mac | Windows |
|---|---|---|
| Starten | dubbelklik **PTZ Control.app** (of *Start PTZ Control.command*) | dubbelklik **Start PTZ Control.bat**, daarna de snelkoppeling **PTZ Control** |
| In Dock / taakbalk | sleep **PTZ Control.app** naar het Dock | rechtsklik op **PTZ Control** in het Startmenu → *Aan taakbalk vastmaken* |
| Opnieuw installeren afdwingen | in Terminal: `bash "Start PTZ Control.command" --installeer` | in cmd: `"Start PTZ Control.bat" /installeer` |
| Snelkoppeling opnieuw maken | | in cmd: `"Start PTZ Control.bat" /snelkoppeling` |
| Vereist | Python 3.9+ (python.org of `brew install python`) | Python 3.9+ (python.org, vink *Add python.exe to PATH* aan, of `winget install -e --id Python.Python.3.12`) |

- **Mac – app-icoon:** *PTZ Control.app* moet in de map van PTZ Control blijven staan (hij start de starter uit die map). Sleep hem naar het Dock; dat maakt alleen een verwijzing. Draait de app al, dan opent een klik alleen de bediening in de browser. Anders opent Terminal met het logboek; dat venster sluiten = stoppen.
- **Mac:** meldt macOS "onbekende ontwikkelaar", rechtsklik → **Open** (eenmalig).
- **Windows – snelkoppeling:** de starter maakt bij de eerste keer de snelkoppeling **PTZ Control** met icoon in het Startmenu en op het bureaublad.
- **Windows:** vraagt de firewall om toegang, kies **Privénetwerken**, anders kunnen tablets de app niet bereiken.
  SmartScreen ("Windows heeft uw pc beschermd"): **Meer info → Toch uitvoeren**.
- **Windows met NVIDIA-videokaart:** het volgen gebruikt standaard de processor. Voor de GPU eenmalig:
  `.venv\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cu124` (daarna rekent hij op `cuda`).
- Stoppen: sluit het venster van de starter.

Handmatig kan ook:
```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python ptz_control.py --open     # poort 8080 (andere poort: --port 9000)
# Windows: .venv\Scripts\python.exe ptz_control.py --open
```
Open `http://localhost:8080`, of op een tablet `http://<ip-van-deze-pc>:8080` (het adres staat in de console).
Voor de bediening zijn geen extra packages nodig, alleen Python 3.8 of nieuwer (volgen: zie hieronder). Instellingen komen in `cameras.json`.

## Eerste keer
1. **🎮 → ⚙ Camera's** (menu rechtsboven): voeg je camera's toe (naam + IP). Gebruik HTTP-CGI; kies VISCA (TCP 5678) alleen als HTTP niet werkt.
2. Je bestaande presets op de camera werken meteen. Rechtsklik, lang indrukken of ✎ op een tegel om een naam te geven
   en een **thumbnail** te maken van het huidige beeld.
3. **＋ Preset opslaan** → tik op een tegel: de huidige positie wordt opgeslagen en er wordt automatisch een thumbnail gemaakt.
4. **Scènes**: zet elke camera op een preset en klik op *Scène van huidige stand*. Eén klik zet dan alle camera's tegelijk goed.

## Meerdere camera's tegelijk
Met 2 of meer camera's opent de app in **Alle camera's**: elke camera een kolom met de LIVE-preset en alle presettegels.
Klik op de kopregel van een kolom, of druk **Shift+1/2/3**, om te kiezen welke camera je bedient; je blijft in *Alle camera's*. Daarna roept **1–9** die preset op die camera op. Snel wisselen gaat dus zo: **Shift+1, 4** (camera 1 → preset 4), **Shift+2, 1** (camera 2 → preset 1). Werkt ook in volledig scherm.
Met **Volg selectie** (rechtsboven) wisselen alle geopende schermen mee als ergens anders (of met de controller) een andere camera gekozen wordt.

**⛶ Volledig scherm** (in het menu onder 🎮 rechtsboven, of toets **F**) toont alle camera's over het hele scherm, zonder kopbalk en zijbalk; presets blijven klikbaar en de controller werkt gewoon. Sluiten met **Esc** of de knop rechtsonder.
De **camerabediening** in de zijbalk klap je in en uit door op de titel te klikken; dat wordt per apparaat onthouden.


## Presets laden van de camera
Staan er al presets in de camera's (bijv. gemaakt met het oude PTZOptics-programma of de afstandsbediening)? Klik op **📥 Laden van camera**
boven het presetraster. De camera's kunnen geen lijst van hun presets geven, dus de app roept preset 1 t/m het gekozen nummer op,
wacht tot de camera stilstaat en maakt een **foto** voor de tegel. Presets waarbij de camera niet beweegt, worden als leeg
overgeslagen. De positie wordt meteen bewaard voor de back-up; bestaande presetnamen blijven staan. Geef de gevonden presets
daarna een naam met ✎. ⚠ De camera's bewegen hierbij: niet tijdens een dienst.

**Gereserveerde presets:** PTZOptics gebruikt 90 (home), 95 (OSD-menu) en 91–99 voor vaste functies. De app roept deze nooit op
en slaat er niets onder op (ook niet via de Xbox-controller). Gebruik 1–89 of 100–254.

## Back-up van presets en camera-instellingen
**💾 Back-up** in het menu onder 🎮 rechtsboven:

1. **Back-upbestand downloaden / terugzetten**: cameralijst, presetnamen, scènes, volg-instellingen, thumbnails en (als uitgelezen)
   presetposities en beeldinstellingen in één `.json`-bestand. Wachtwoorden alleen als je dat aanvinkt; zonder wachtwoorden houdt
   terugzetten de huidige wachtwoorden.
2. **Uit de camera's lezen**: per preset pan/tilt/zoom/focus en de beeldinstellingen (belichting, witbalans, kleur, scherpte,
   flip/mirror). ⚠ De camera's **rijden langs al hun presets**, niet doen tijdens een dienst. Daarna gaat elke camera terug naar de
   preset die LIVE stond.
3. **Naar de camera's terugzetten**: rijdt naar de bewaarde posities, slaat de presets opnieuw op en zet het beeld terug.
   Handig na een fabrieksreset of bij een vervangende camera (pas dan eerst het IP aan bij ⚙ Camera's).

Tip: lees de camera's uit en download een back-up na elke wijziging aan de presets. Dit gaat via VISCA (TCP-poort 5678).

## Persoon volgen (alleen pan)
De app kan een spreker volgen die over het podium loopt. Alleen **pan** wordt bijgestuurd; tilt en zoom blijven zoals de preset ze zet.

Installeren op de pc waar de app draait:
```
pip install opencv-python numpy          # verplicht
pip install ultralytics                   # aanbevolen: nauwkeuriger (YOLO), liefst met een NVIDIA-GPU
```
Zonder `ultralytics` gebruikt de app de ingebouwde OpenCV-persoonsdetector (werkt op elke pc, minder nauwkeurig).

Gebruik:
- **🎯 volgen** in de kolomkop (of de knop in de bediening, of toets **T**) zet volgen aan/uit. De status staat erbij: *volgt*, *zoekt persoon*, *geen beeld*.
- In het preset-venster (✎): **Na oproepen automatisch volgen**. Bijv. "Preekstoel": camera gaat naar het shot en begint na 2,5 s te volgen.
- Handbediening, home of een andere preset op die camera zet volgen direct uit. Zoom en focus mogen tijdens het volgen.
- Bij meerdere mensen in beeld kiest hij de grootste (meestal de spreker vooraan) en blijft daarna bij diezelfde persoon.
- ⚙ Camera's per camera: **Volg-stream** (standaard `rtsp://IP:554/2`, de lichte substream), **max. volgsnelheid** en **pan omgekeerd** voor een ondersteboven gemonteerde camera.

## Xbox-controller
Verbind de controller via Bluetooth of USB met de Mac (of iPad/tablet) waarop de bediening open staat en druk op een knop.
De knop **🎮 controller** rechtsboven toont de status en is ook het **menu** (Controller-instellingen, Knoppenindeling, Volledig scherm, Back-up, Camera's). Zachte beweging, tilt omkeren en max. snelheid staan in de zijbalk onder de camerabediening.

**Zachte beweging** (standaard aan): de camera trekt geleidelijk op (ca. 1 seconde naar topsnelheid) en de stick reageert fijner rond het midden. Uitzetten geeft direct de volle snelheid.

Sticks en triggers liggen vast. De knoppen stel je per knop in via **🎮 → Knoppenindeling** rechtsboven (druk in dat venster op een knop van de controller en de regel licht op); die indeling staat in `cameras.json` en geldt dus voor alle schermen. Standaard:

| Controller | Functie |
|---|---|
| Linker stick | Pan/tilt, snelheid naar hoe ver je duwt (indrukken = fijn-modus) |
| RT / LT | Zoom in / uit, snelheid naar hoe diep je drukt |
| Rechter stick ↑/↓ | Focus ver / dichtbij (indrukken = autofocus) |
| A · B · X · Y | Preset 1–4 · met LB (Shift) ingedrukt: 5–8 |
| ☰ Menu + A/B/X/Y | Huidige positie opslaan als preset (trilt ter bevestiging) |
| D-pad ←/→ | Vorige / volgende camera · ↑ volgende scène · ↓ home |
| RB | Volgen (pan) aan/uit · View = alles stoppen |

Elke knop kan ook worden: een vaste camera, een vaste scène, een andere preset, home, volgen, autofocus, fijn-modus, alles stoppen, of niets.
**Shift** telt bij presets een instelbaar getal op (standaard 4); **Opslaan** vasthouden + presetknop slaat de huidige positie op.

De bediening-pagina moet open en zichtbaar zijn (de browser geeft de controller alleen door aan het actieve tabblad).
Gaat het tabblad naar de achtergrond, dan stopt elke beweging automatisch.

## Sneltoetsen
Pijltjes = bewegen · Q/A = zoom · W/S = focus · 1–9 = preset · Shift+1–9 = camera kiezen (in Alle camera's blijf je in die weergave) · H = home · T = volgen · F = volledig scherm · Spatie = stop

## Tip
Geef de pc met deze app een vast IP-adres (DHCP-reservering), dan blijft het tabletadres hetzelfde.
