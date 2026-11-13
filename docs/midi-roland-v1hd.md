# Verkenning: Roland V-1HD via MIDI

Branch `verken/midi-roland`. In de app: Menu → **🎛 Videomixer (MIDI)** (koppeling knop → camera, IN BEELD-markering, verkenning met log). Hieronder het uitzoekwerk.

Bron: Roland *V-1HD Remote Control Guide* (versie 1.00, 2015).

## Wat de V-1HD kan

- **Aansluiting:** USB-MIDI én MIDI DIN (IN/OUT/THRU). Via **USB naar de Mac mini** is genoeg, geen losse MIDI-interface. Wat de mixer zelf maakt, gaat altijd naar USB, ongeacht de instelling MIDI OUT.
- **Menu (SETUP → MIDI):** `MIDI Rx SOURCE` (USB / MIDI IN), `MIDI OUT` (THRU / OUT), `MIDI Tx CHANNEL` 1–16, `MIDI Rx CHANNEL` 1–16/OFF, `MIDI DEVICE ID` 1–32.
- **Stuurt bij bediening (standaardmodus):**

  | Op de mixer | MIDI |
  |---|---|
  | A-bus ingang 1–4 | CC0 = 0, CC32 = 0, program change 0–3 |
  | B-bus ingang 1–4 | CC0 = 1, CC32 = 0, program change 0–3 |
  | Memory 1–8 | CC0 = 80 (50H), CC32 = 0, program change 0–7 |
  | A/B-fader | CC17 (0–127) |
  | WIPE / MIX / CUT | CC19 = 0 / 1 / 2 |

- **Ontvangt** dezelfde berichten, dus de app kan ook zelf ingangen kiezen.
- **Opvragen via SysEx (RQ1)**, model-ID `00 00 00 20`, standaard device-ID `10H`:
  - `71 00 00–01` A/B-fader (14-bit), `71 00 08` video sel A, `71 00 09` video sel B;
  - `73 02 00–18` kleur van de knoplampjes (alleen lezen, o.a. VDOSEL A-1..4 / B-1..4): daarmee is tally te bepalen;
  - `73 01 00` gekozen memory.

## Hoe 'live' te bepalen

Er bestaat geen kant-en-klaar tally-bericht. 🧑 **De fader wordt nooit gebruikt**: hij staat vast aan één kant, dus één rij is altijd live en een knop in die rij schakelt direct. Daarmee:

🧑 **Lampjes op de mixer: standaard groen, de gekozen (live) ingang wordt rood.** Groen betekent hier dus 'niet live', niet 'klaargezet'.

1. **Uit de lampjes** (hoofdroute): `73 02 xx` geeft de kleur van elke knop (VDOSEL A-1..4 / B-1..4). De ingang waarvan het lampje rood is = live, precies zoals de regie het ziet. Nog uitzoeken: welk byte bij welke knop hoort en welke waarde 'rood' is. Elke ~0,5 s opvragen, plus direct na elk program change-bericht.
2. **Uit de knoppen** (snel, als aanvulling): elke druk geeft bank + program change, dus de app reageert meteen; het lampje bevestigt daarna.

Te bevestigen met de log: is er altijd precies één knop rood?

## Testen op de Mac mini

Makkelijkst in de app: Menu → 🎛 Videomixer (MIDI) → *Verkenning starten*, knoppen indrukken, *Log downloaden*. Of los, zonder de app:

```bash
.venv/bin/pip install mido python-rtmidi
.venv/bin/python docs/tools/midi_verken.py --lijst
.venv/bin/python docs/tools/midi_verken.py --vraag 1 --log v1hd.txt
```

Op de mixer: `MIDI OUT` op **OUT**, device-ID noteren. Dan rustig:

1. de fader laten staan zoals in de dienst;
2. op de A-rij ingang 1, 2, 3, 4 drukken; idem op de B-rij; noteer per druk welke knop rood wordt;
3. memory 1 en 2 kiezen;
4. het script stoppen en opnieuw starten (dan vraagt het de beginstand op).

Noteer per stap welke ingang op het programmabeeld staat. `v1hd.txt` terugsturen: daarmee kunnen we de lampjes koppelen aan de knoppen en bepalen welke route we nemen.

## Mogelijke functies daarna

1. **Tally in de app**: rode rand om de camera die live staat (koppeling ingang → camera per camera instellen).
2. **Beveiliging**: preset/beweging op de live camera eerst laten bevestigen of blokkeren.
3. **Automatisch kiezen**: na een wissel de camera kiezen die net van program af is.
4. **Mixer bedienen vanuit de app** (ingang kiezen via program change).

## USB direct

Naast de MIDI-poort kan de app de mixer ook **rechtstreeks via USB** aanspreken (`v1hd_usb.py`, pyusb + libusb-package): zelfde MIDI-berichten, verpakt in USB-MIDI-pakketten van 4 bytes. Handig als de computer geen MIDI-poort van de V-1HD ziet (geen driver). ⚠️ Product-ID en pakketformaat niet gecontroleerd op de echte mixer: bij de eerste test de *Verkenning* draaien met *USB direct* en vergelijken met MIDI.

## Code

- `roland_v1hd.py`: berichten opbouwen (RQ1/DT1, checksum) en ontleden (`V1HDState`), alleen standaard-Python.
- `docs/tools/midi_verken.py`: luisterscript met `mido` + `python-rtmidi` (nog niet in `requirements.txt`).
