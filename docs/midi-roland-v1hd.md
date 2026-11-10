# Verkenning: Roland V-1HD via MIDI

Branch `verken/midi-roland`. Nog **geen functie in de app**: dit is uitzoekwerk plus een script om de echte mixer te testen.

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

## Hoe 'live' te bepalen (open vragen)

Er bestaat geen kant-en-klaar tally-bericht. Twee routes:

1. **Uit de knoppen en de fader**: bijhouden welke ingang op A en op B staat en waar de fader staat. Fader aan één kant = die bus live, ertussen = overgang. ⚠️ Onbekend welke kant van de fader A is, en of de V-1HD in A/B- of PGM/PST-modus staat (dan wisselen de rijen na een overgang).
2. **Uit de lampjes** (`73 02 xx`) regelmatig opvragen: de mixer weet zelf wat rood (program) is. Waarschijnlijk het betrouwbaarst, maar welk byte bij welke knop hoort en welke waarde 'rood' is, moet nog op de echte mixer worden uitgezocht.

## Testen op de Mac mini

```bash
.venv/bin/pip install mido python-rtmidi
.venv/bin/python docs/tools/midi_verken.py --lijst
.venv/bin/python docs/tools/midi_verken.py --vraag 1 --log v1hd.txt
```

Op de mixer: `MIDI OUT` op **OUT**, device-ID noteren. Dan rustig:

1. op de A-rij ingang 1, 2, 3, 4 drukken; idem op de B-rij;
2. de fader langzaam van de ene naar de andere kant, en terug;
3. een overgang met CUT en met MIX;
4. memory 1 en 2 kiezen.

Noteer per stap welke ingang op het programmabeeld staat. `v1hd.txt` terugsturen: daarmee kunnen we de lampjes koppelen aan de knoppen en bepalen welke route we nemen.

## Mogelijke functies daarna

1. **Tally in de app**: rode rand om de camera die live staat (koppeling ingang → camera per camera instellen).
2. **Beveiliging**: preset/beweging op de live camera eerst laten bevestigen of blokkeren.
3. **Automatisch kiezen**: na een wissel de camera kiezen die net van program af is.
4. **Mixer bedienen vanuit de app** (ingang kiezen via program change).

## Code

- `roland_v1hd.py`: berichten opbouwen (RQ1/DT1, checksum) en ontleden (`V1HDState`), alleen standaard-Python.
- `docs/tools/midi_verken.py`: luisterscript met `mido` + `python-rtmidi` (nog niet in `requirements.txt`).
