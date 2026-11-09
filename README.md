# PTZ Control

Eigen webbediening voor PTZOptics-camera's, met koppeling aan de PT-JOY-G4 / SuperJoy.

## Installeren en starten

**Eén klik:** de starter installeert bij de eerste keer automatisch de `.venv` (een paar minuten) en opent daarna de bediening in je browser.

| | Mac | Windows |
|---|---|---|
| Starten | dubbelklik **Start PTZ Control.command** | dubbelklik **Start PTZ Control.bat** |
| Opnieuw installeren / bijwerken | **Installeer.command** | **Installeer.bat** |
| Vereist | Python 3.9+ (python.org of `brew install python`) | Python 3.9+ (python.org, vink *Add python.exe to PATH* aan, of `winget install -e --id Python.Python.3.12`) |

- **Mac:** meldt macOS "onbekende ontwikkelaar", rechtsklik → **Open** (eenmalig).
- **Windows:** vraagt de firewall om toegang, kies **Privénetwerken**, anders kunnen tablets en de joystick de app niet bereiken.
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
1. **⚙ Camera's**: voeg je camera's toe (naam + IP). Gebruik HTTP-CGI; kies VISCA (TCP 5678) alleen als HTTP niet werkt.
2. Je bestaande presets op de camera werken meteen. Rechtsklik, lang indrukken of ✎ op een tegel om een naam te geven
   en een **thumbnail** te maken van het huidige beeld.
3. **＋ Preset opslaan** → tik op een tegel: de huidige positie wordt opgeslagen en er wordt automatisch een thumbnail gemaakt.
4. **Scènes**: zet elke camera op een preset en klik op *Scène van huidige stand*. Eén klik zet dan alle camera's tegelijk goed.

## Meerdere camera's tegelijk
Met 2 of meer camera's opent de app in **Alle camera's**: elke camera een kolom met de LIVE-preset en alle presettegels.
Klik op de kopregel van een kolom (of Shift+1/2/3) om te kiezen welke camera de pijltjes/handbediening aanstuurt.

## YouTube-stream via OBS
In OBS Studio (28+): *Extra → WebSocket-serverinstellingen* → server aanzetten, wachtwoord kiezen.
In de app: **⚙ Camera's → YouTube-stream via OBS** → IP van de OBS-pc, poort 4455, wachtwoord.
Daarna verschijnt rechtsboven **● Live gaan** (met looptijd tijdens de stream) en **⏺ Rec**. Starten en stoppen vraagt altijd om bevestiging.
De YouTube-streamsleutel stel je zoals gewoonlijk in OBS in.

## Joystick koppelen (PT-JOY-G4 / SuperJoy, firmware 4.1.8+)
De joystick blijft de camera's zelf aansturen. Deze app luistert mee via HTTP-commando's uit de webinterface van de joystick.
De knop **🕹 Joystick** in de app laat de URL's zien met het juiste IP; klik op een URL om hem te kopiëren.

| Waar in de joystick | URL |
|---|---|
| Automatic Trigger (bij camerawissel) | `http://PC:8080/api/joy/select?cam={{CURRENT_CAM}}` |
| Custom button: scène | `http://PC:8080/api/joy/scene?n=1` (nummer of naam) |
| Custom button: preset | `http://PC:8080/api/joy/preset?cam={{CURRENT_CAM}}&n=3` |
| Custom button: stream aan/uit | `http://PC:8080/api/joy/obs?action=toggle_stream` |
| Custom button: ander commando | `http://PC:8080/api/joy/cmd?cam={{CURRENT_CAM}}&action=autofocus&on=1` |

`cam` mag een IP-adres, volgnummer (1, 2…) of id zijn. Mogelijke acties: `move`, `stop`, `home`, `zoom`, `focus`, `autofocus`, `onepush_af`, `preset_call`, `preset_set`.
Met **Volg joystick** (rechtsboven) springt de app automatisch naar de camera die op de joystick gekozen is. Alle geopende tablets blijven onderling gelijk.

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
- Joystick-knop: `http://PC:8080/api/joy/track?cam={{CURRENT_CAM}}&on=toggle`

Let op: beweeg je dezelfde camera met de joystick terwijl volgen aan staat, dan werken ze tegen elkaar in. Zet volgen eerst uit (bijv. met een joystickknop).

## Xbox-controller (vervangt de PTZJoy)
Verbind de controller via Bluetooth of USB met de Mac (of iPad/tablet) waarop de bediening open staat en druk op een knop.
De knop **🎮** rechtsboven toont de status en de volledige knoppenindeling.

| Controller | Functie |
|---|---|
| Linker stick | Pan/tilt, snelheid naar hoe ver je duwt (indrukken = fijn-modus) |
| RT / LT | Zoom in / uit, snelheid naar hoe diep je drukt |
| Rechter stick ↑/↓ | Focus ver / dichtbij (indrukken = autofocus) |
| A · B · X · Y | Preset 1–4 · met LB: 5–8 |
| ☰ Menu + A/B/X/Y | Huidige positie opslaan als preset (trilt ter bevestiging) |
| D-pad ←/→ | Vorige / volgende camera · ↑ volgende scène · ↓ home |
| RB | Volgen (pan) aan/uit · View = alles stoppen |

De bediening-pagina moet open en zichtbaar zijn (de browser geeft de controller alleen door aan het actieve tabblad).
Gaat het tabblad naar de achtergrond, dan stopt elke beweging automatisch.

## Sneltoetsen
Pijltjes = bewegen · Q/A = zoom · W/S = focus · 1–9 = preset · Shift+1–9 = camera · H = home · T = volgen · Spatie = stop

## Tip
Geef de pc met deze app een vast IP-adres (DHCP-reservering), anders kloppen de joystick-URL's niet meer.
