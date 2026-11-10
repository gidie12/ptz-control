# PTZ Control

Eigen webbediening voor PTZOptics-camera's, te bedienen met muis/touch, toetsenbord of een Xbox-controller.

![Hoofdscherm met drie camera's](docs/screenshots/hoofdscherm.png)

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
1. **Menu → 📷 Camera's** (menu rechtsboven): voeg je camera's toe (naam + IP). Gebruik HTTP-CGI; kies VISCA (TCP 5678) alleen als HTTP niet werkt.
2. Je bestaande presets op de camera werken meteen. Rechtsklik, lang indrukken of ✎ op een tegel om een naam te geven
   en een **thumbnail** te maken van het huidige beeld.
3. **＋ Preset opslaan** → tik op een tegel: de huidige positie wordt opgeslagen en er wordt automatisch een thumbnail gemaakt.

## Rondleiding
De eerste keer dat je de bediening op een apparaat opent, start vanzelf een **rondleiding** die elke functie stap voor stap aanwijst
(presets, opslaan, laden, handbediening, volgen, Xbox-controller, sneltoetsen, YouTube en elk menu-item). Tijdens de rondleiding
reageren de camera's niet. Volgende: **→** of **Enter**, vorige: **←**, stoppen: **Esc**.
Later nog eens bekijken: **Menu → 🎓 Rondleiding**.

![Rondleiding](docs/screenshots/rondleiding.png)

## Meerdere camera's tegelijk
Met 2 of meer camera's opent de app in **Alle camera's**: elke camera een kolom met de LIVE-preset en alle presettegels.
Klik op de kopregel van een kolom, of druk **Shift+1/2/3**, om te kiezen welke camera je bedient; je blijft in *Alle camera's*. Daarna roept **1–9** die preset op die camera op. Snel wisselen gaat dus zo: **Shift+1, 4** (camera 1 → preset 4), **Shift+2, 1** (camera 2 → preset 1). Werkt ook in volledig scherm. De toets per camera is instelbaar via **Menu → ⌨ Sneltoetsen** (klik in het veld en druk bijv. F1 of Num 1; Backspace = terug naar Shift+N). Daar staat ook een overzicht van alle vaste toetsen. Een rood veld betekent dat de toets al in gebruik is.
Met **Volg selectie** (rechtsboven) wisselen alle geopende schermen mee als ergens anders (of met de controller) een andere camera gekozen wordt.

**⛶ Volledig scherm** (in het menu onder 🎮 rechtsboven, of toets **F**) toont alle camera's over het hele scherm, zonder kopbalk en zijbalk; presets blijven klikbaar en de controller werkt gewoon. Sluiten met **Esc** of de knop rechtsonder.
De **camerabediening** in de zijbalk klap je in en uit door op de titel te klikken; dat wordt per apparaat onthouden.

![Volledig scherm](docs/screenshots/volledig-scherm.png)

<img src="docs/screenshots/menu.png" width="440" alt="Menu">


## Presets laden van de camera
Staan er al presets in de camera's (bijv. gemaakt met het oude PTZOptics-programma of de afstandsbediening)? Klik op **📥 Laden van camera**
boven het presetraster. De camera's kunnen geen lijst van hun presets geven, dus de app roept preset 1 t/m het gekozen nummer op,
wacht tot de camera stilstaat en maakt een **foto** voor de tegel. Presets waarbij de camera niet beweegt, worden als leeg
overgeslagen. De positie wordt meteen bewaard voor de back-up; bestaande presetnamen blijven staan. Geef de gevonden presets
daarna een naam met ✎. ⚠ De camera's bewegen hierbij: niet tijdens een dienst.

**Gereserveerde presets:** PTZOptics gebruikt 90 (home), 95 (OSD-menu) en 91–99 voor vaste functies. De app roept deze nooit op
en slaat er niets onder op (ook niet via de Xbox-controller). Gebruik 1–89 of 100–254.

## Presetaantal testen
Menu → **🧪 Presetaantal testen**. Test per camera hoeveel presets hij echt kan opslaan en oproepen (ca. 1 minuut per
camera; de camera's bewegen, dus niet tijdens een dienst). Bestaande presets worden daarna exact teruggezet en lege nummers
weer gewist. Het resultaat (bijv. "max. preset 254 · 244 bruikbaar") blijft bewaard en is later terug te zien.

## Tegels instellen
Boven het presetraster: **Tegels − 12 +** bepaalt hoeveel presettegels je per camera ziet (90–99 worden overgeslagen),
**Grootte** de tegelgrootte. Beide worden automatisch opgeslagen en gelden op elk scherm. Staan er presets met een naam of
foto buiten beeld, dan zie je een melding; klik erop om ze te tonen.

## Back-up van presets en camera-instellingen
**💾 Back-up** in het menu onder 🎮 rechtsboven:

1. **Back-upbestand downloaden / terugzetten**: cameralijst, presetnamen, volg-instellingen, thumbnails en (als uitgelezen)
   presetposities en beeldinstellingen in één `.json`-bestand. Wachtwoorden alleen als je dat aanvinkt; zonder wachtwoorden houdt
   terugzetten de huidige wachtwoorden.
2. **Uit de camera's lezen**: per preset pan/tilt/zoom/focus en de beeldinstellingen (belichting, witbalans, kleur, scherpte,
   flip/mirror). ⚠ De camera's **rijden langs al hun presets**, niet doen tijdens een dienst. Daarna gaat elke camera terug naar de
   preset die LIVE stond.
3. **Naar de camera's terugzetten**: rijdt naar de bewaarde posities, slaat de presets opnieuw op en zet het beeld terug.
   Handig na een fabrieksreset of bij een vervangende camera (pas dan eerst het IP aan bij ⚙ Camera's).

Tip: lees de camera's uit en download een back-up na elke wijziging aan de presets. Dit gaat via VISCA (TCP-poort 5678).

![Back-up](docs/screenshots/back-up.png)

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

![Preset bewerken](docs/screenshots/preset-bewerken.png)

![Camera's instellen](docs/screenshots/cameras.png)

## Xbox-controller
Verbind de controller via Bluetooth of USB met de Mac (of iPad/tablet) waarop de bediening open staat en druk op een knop.
De controllerstatus staat in de Xbox-kaart in de zijbalk (groen *Verbonden*). Het **Menu** rechtsboven bevat Sneltoetsen, Volledig scherm, Back-up, Camera's, Videomixer (MIDI), Presetaantal testen, thema, Rondleiding, Op updates controleren en afsluiten. Zachte beweging, tilt omkeren en max. snelheid staan in de zijbalk onder de camerabediening.

**Knoppenprofielen (per persoon):** in de Xbox-kaart kies je bij *Knoppenprofiel* welke knoppenindeling deze controller gebruikt. In *Knoppenindeling instellen…* maak je een nieuw profiel (begint als kopie van het huidige), hernoem of verwijder je het. Elk scherm onthoudt zelf welk profiel actief is; de profielen staan in de back-up.

**Zachte beweging** (standaard aan): de camera trekt geleidelijk op (ca. 1 seconde naar topsnelheid) en de stick reageert fijner rond het midden. Uitzetten geeft direct de volle snelheid.

Sticks en triggers liggen vast. De knoppen stel je per knop in via **Knoppenindeling instellen…** in de Xbox-kaart (druk in dat venster op een knop van de controller en de regel licht op); die indeling staat in `cameras.json` en geldt dus voor alle schermen. Standaard:

| Controller | Functie |
|---|---|
| Linker stick | Pan/tilt, snelheid naar hoe ver je duwt (indrukken = fijn-modus) |
| RT / LT | Zoom in / uit, snelheid naar hoe diep je drukt |
| Rechter stick ↑/↓ | Focus ver / dichtbij (indrukken = autofocus) |
| A · B · X · Y | Preset 1–4 · met LB (Shift) ingedrukt: 5–8 |
| ☰ Menu + A/B/X/Y | Huidige positie opslaan als preset (trilt ter bevestiging) |
| D-pad ←/→ | Vorige / volgende camera · ↓ home (↑ vrij) |
| RB | Volgen (pan) aan/uit · View = alles stoppen |

Elke knop kan ook worden: een vaste camera, een andere preset, home, volgen, autofocus, fijn-modus, alles stoppen, of niets.
**Shift** telt bij presets een instelbaar getal op (standaard 4); **Opslaan** vasthouden + presetknop slaat de huidige positie op.

De bediening-pagina moet open en zichtbaar zijn (de browser geeft de controller alleen door aan het actieve tabblad).
Gaat het tabblad naar de achtergrond, dan stopt elke beweging automatisch.

![Knoppenindeling](docs/screenshots/knoppenindeling.png)

## YouTube-livestream bekijken
Klik op de **YouTube**-knop rechtsboven → *Kanaal instellen…* en vul het kanaal in (`@kerknaam`, een kanaal-id `UC…` of de link naar het kanaal). Een link naar één stream (`youtube.com/live/…`) mag ook, maar dan volgt hij alleen die ene stream.
**Na het bijwerken van de app altijd PTZ Control opnieuw starten** (Terminal-venster sluiten, app weer openen), anders draait de server nog de oude versie.
De app kijkt daarna elke minuut of het kanaal live is. De knop rechtsboven wordt rood (**● LIVE**) zodra er een stream loopt,
en de stream verschijnt in een **klein venster** (standaard automatisch, zonder geluid).

- Het venster zoekt zelf een vrije plek naast of onder de presettegels en schuift mee als je het aantal tegels verandert. Is er op volledig scherm geen plek, dan houdt de app rechts een strook vrij en schuiven de tegels op.
- Zelf verplaatsen: sleep aan de titelbalk (dan blijft het daar staan; ⌖ zet het weer automatisch). Groter/kleiner: sleep het hoekje rechtsonder (of kies Klein/Middel/Groot in het YouTube-menu).
- Klik op de YouTube/LIVE-knop voor een keuzemenu: venster tonen/verbergen, automatisch plaatsen, grootte, geluid, automatisch openen, nu controleren, openen op YouTube en kanaal instellen. Toets **Y** toont/verbergt het venster direct.
- Positie, grootte, automatisch openen en geluid onthoudt elk apparaat zelf; het kanaal geldt voor alle schermen.
- Alleen kijken: de stream starten en stoppen gebeurt nog steeds buiten deze app. Let op: de stream loopt een paar seconden achter.
- Het kanaal kun je altijd wijzigen, ook terwijl er een stream loopt.

<img src="docs/screenshots/youtube-menu.png" width="560" alt="YouTube-menu">

## Videomixer (Roland V-1HD)
Sluit de V-1HD met een **USB-kabel** aan op de computer waar PTZ Control draait en open Menu → **🎛 Videomixer (MIDI)**.

- Vink **Koppeling met de videomixer aan** aan. Bovenin staat of de mixer gevonden is.
- Het venster toont beide rijen van de mixer (A en B, elk knop 1–4). Beide rijen kiezen uit dezelfde vier HDMI-ingangen, dus je kiest per ingang één keer welke camera erop zit. Druk je op de mixer op een knop, dan licht hij in het venster op; de gekozen ingang op de live rij is rood, net als op de mixer.
- De camera die op de mixer gekozen is, krijgt in de app een **rode rand** en **IN BEELD** (in de kolom en op het tabblad). Zo zie je meteen welke camera je niet moet verplaatsen.
- **Live rij**: de rij aan de kant waar de fader staat (de fader wordt niet gebruikt). Standaard A.
- **Verkenning**: legt alles vast wat de mixer via MIDI stuurt. Start, druk op de knoppen van beide rijen en kies een memory, en klik **⬇ Log downloaden**. Handig als de koppeling niet klopt.
- Op de mixer: SETUP → MIDI → *MIDI OUT* op **OUT** (via USB stuurt hij altijd).

De koppeling geldt voor alle schermen en wordt per workspace en in de back-up bewaard. Nodig zijn de packages `mido` en `python-rtmidi` (installeert de starter vanzelf).

![Videomixer](docs/screenshots/videomixer.png)

## Licht of donker
Menu → **☀ Lichte modus / 🌙 Donkere modus** wisselt direct. Elk apparaat onthoudt zijn eigen keuze.

![Lichte modus](docs/screenshots/hoofdscherm-licht.png)

Op een telefoon of smalle tablet staan de onderdelen onder elkaar:

<img src="docs/screenshots/telefoon.png" width="260" alt="Op een telefoon">

## Workspaces (per locatie)
Klik op **📍 Standaard ▾** naast de titel. Een workspace is een aparte set camera's, presets, foto's, sneltoetsen, knoppenindeling en YouTube-kanaal, alsof het een aparte installatie is.
- *Nieuwe workspace…*: kies een naam en begin leeg of als kopie van een bestaande.
- Wisselen geldt voor alle schermen die met deze computer verbonden zijn (ze herladen vanzelf). Volgen stopt bij het wisselen.
- *Workspaces beheren…*: hernoemen, openen of verwijderen (verwijderen wist alles van die workspace; maak eerst een back-up als je het wilt bewaren).
- **Back-ups:** Menu → 💾 Back-up maakt een back-up van de workspace die nu open is (de naam staat in het bestand). Vink *alle workspaces* aan voor één bestand met alles. Bij terugzetten kies je: in de huidige workspace, of als nieuwe workspace. Een back-up van alle workspaces zet ze allemaal terug (workspaces die er niet in staan blijven staan).
- Direct in een bepaalde workspace starten: `python3 ptz_control.py --workspace "Kerk Zwolle"` (ook als extra argument achter `Start PTZ Control`).

## Sneltoetsen
Standaard: pijltjes = bewegen · Q/A = zoom · W/S = focus · 1–9 = preset · Shift+1–9 = camera kiezen · H = home · T = volgen · F = volledig scherm · Y = YouTube-venster · Spatie = stop · Esc = volledig scherm/opslaan-modus verlaten.
**Alle toetsen zijn aan te passen** via Menu → ⌨ Sneltoetsen (of *aanpassen* onder de bediening): klik in een veld en druk de nieuwe toets of combinatie. Backspace = standaard, Delete = uitzetten. Esc blijft vast. De instelling geldt voor alle schermen.

![Sneltoetsen](docs/screenshots/sneltoetsen.png)

## Updates
PTZ Control kijkt zelf elke 6 uur (en kort na het starten) of er een nieuwe versie is. Is die er, dan verschijnt **rechtsonder** een melding met wat er nieuw is. Zelf kijken: **Menu → 🔄 Op updates controleren**.

- **Nu bijwerken** (versie via *Start PTZ Control* / git): de app haalt de nieuwe versie op (`git pull`), werkt zo nodig de packages bij en start zichzelf opnieuw. De bediening is dan ongeveer 10 seconden weg; open schermen verversen vanzelf. ⚠ Niet tijdens een dienst.
- **Map zonder git** (bijv. de code als ZIP van GitHub gedownload, of git staat niet op deze computer): **Nu bijwerken** stapt over op de **losse app**. Die wordt gedownload en komt in dezelfde map in plaats van de Dock-snelkoppeling `PTZ Control.app` (Windows: `PTZ Control.exe`), dus het Dock-icoon blijft werken. Je instellingen, presets en foto's worden gekopieerd naar de vaste plek buiten de app (Mac: `~/Library/Application Support/PTZ Control`, Windows: `%APPDATA%\PTZ Control`); wat daar al stond, gaat naar een submap `voor-overstap-<datum>`. `cameras.json` in de oude map heet daarna `cameras.json.overgezet` (reservekopie). Vanaf dan werkt de losse app zichzelf bij.
- **Later** verbergt de melding voor die versie op dit apparaat. Op volledig scherm blijft de melding altijd weg.
- Lukt automatisch bijwerken niet (bijv. omdat er op deze computer bestanden zijn aangepast), dan zegt de melding dat; werk dan bij met `git pull` en start de app opnieuw.
- Wat er nieuw is komt uit de releasenotes: `docs/releasenotes/<versie>.md` als die er is (zie [`docs/releasenotes/README.md`](docs/releasenotes/README.md)), anders maakt de bouwstap ze uit de commitberichten sinds de vorige versie.
- **Losse app (.exe/.app):** ook die werkt zichzelf bij: **Nu bijwerken** downloadt de nieuwe versie van GitHub (Releases), sluit de app, zet de nieuwe ervoor in de plaats en start hem opnieuw. Mag de app zijn eigen map niet overschrijven (bijv. een .app die door een beheerder in *Programma's* is gezet), dan blijft de oude staan en opent Finder/Verkenner de gedownloade versie, zodat je hem zelf kunt vervangen.
- Uitzetten: start met de omgevingsvariabele `PTZ_UPDATE=0`.

<img src="docs/screenshots/update-melding.png" width="400" alt="Updatemelding rechtsonder">

## Tip
Geef de pc met deze app een vast IP-adres (DHCP-reservering), dan blijft het tabletadres hetzelfde.

## Losse app (zonder Python installeren)
Er is ook een kant-en-klare app: **`PTZ-Control-…-Windows.exe`** en **`PTZ-Control-…-macOS.zip`** (Mac met Apple-chip).
Download ze bij **Releases** op GitHub (of onder *Actions → Losse app bouwen → Artifacts*).

- **Windows:** dubbelklik op de .exe. De eerste keer: SmartScreen → *Meer informatie* → *Toch uitvoeren*, en bij de firewall *Privénetwerken* toestaan (anders kunnen andere schermen niet meedoen). Er verschijnt een camera-icoon in het systeemvak (rechtsonder, eventueel onder ^).
- **macOS:** pak de zip uit en zet **PTZ Control.app** in *Programma's*. De app is niet door Apple gecontroleerd: de eerste keer weigert macOS hem. Ga dan naar *Systeeminstellingen → Privacy en beveiliging* en klik bij PTZ Control op *Toch openen* (of in Terminal: `xattr -dr com.apple.quarantine "/Applications/PTZ Control.app"`). Sta daarna *Lokaal netwerk* toe, anders bereikt hij de camera's niet. De app heeft geen Dock-icoon maar een icoon in de **menubalk** (rechtsboven).
- Via het icoon: *Bediening openen*, het adres voor andere schermen, *Map met instellingen openen* en *Afsluiten*. Afsluiten kan ook in de bediening: Menu → **⏻ PTZ Control afsluiten**. Nog een keer op de app klikken terwijl hij draait opent alleen de bediening.
- **Instellingen en foto's** staan bij de losse app in een eigen map: Windows `%APPDATA%\PTZ Control`, Mac `~/Library/Application Support/PTZ Control`. Overstappen vanaf de Python-versie: maak daar een **Back-up** (met wachtwoorden) en zet die terug in de app, of kopieer `cameras.json` en de map `thumbs` naar die map.
- **Persoon volgen** werkt in de losse app met de ingebouwde OpenCV-detector (minder nauwkeurig dan YOLO). Wil je YOLO, gebruik dan de Python-versie (`Start PTZ Control`).
- Een nieuwe versie maken: GitHub → *Actions* → **Losse app bouwen** → *Run workflow* → vink **Release maken** aan en vul de versie in (bv. `1.1.0`). GitHub maakt dan zelf de tag, bouwt beide apps en zet ze bij Releases. Zonder vinkje wordt alleen gebouwd (downloads onder *Artifacts*).

## Screenshots bijwerken
De afbeeldingen in deze README zijn gemaakt met nagemaakte demodata; opnieuw maken: zie [`docs/tools/README.md`](docs/tools/README.md).
