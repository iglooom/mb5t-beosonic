# BeoSonic for the Explorer amplifier: what it does and how we got there (plain language)

Technical details are in `TECHNICAL.md`.

## What BeoSonic is

Newer Fords with B&O sound (Kuga 3, Focus 4) have a **BeoSonic** page on the SYNC screen. It's a round pad with a dot you drag around. The corners are labelled Warm, Bright, Energetic and Relaxed, and there are presets such as Lounge, Party and Podcast.

Under the hood it is simple: the dot controls three sound filters.

- a **bass shelf at 60 Hz** (everything below it gets louder or quieter);
- a **treble shelf at 7.5 kHz**;
- a small **overall level** change of ±2 dB, so that a bass boost doesn't just make everything louder.

Moving the dot towards "Warm" adds bass and removes treble; "Bright" does the opposite. "Energetic" adds both, and "Relaxed" removes both.

## The car and the problem

The car is a Kuga 2 with retrofitted Kuga 3 B&O speakers and audio module, and an amplifier from a Ford Explorer. SYNC can show the BeoSonic page, but two separate devices are involved:

1. **The SYNC screen** draws the pad and sends a short network message: "BeoSonic on, dot at X, Y".
2. **The amplifier** does the actual sound processing. It's supposed to act on that message and reply "I'm on, dot at X, Y", and the screen shows whatever the amplifier reports.

The Explorer amplifier's software throws that message away and never replies, so the page did nothing.

## How we worked it out

1. **We studied the Kuga 3 amplifier, which does have BeoSonic.** It's the same Harman family: same processor, same sound chip, same software design. From its software we learned:
   - the exact messages and what each byte means;
   - the tables that turn the dot position into filter strengths;
   - the three filters, which are stored in the Kuga 3 sound tune;
   - how it remembers the setting.
2. **We studied the Explorer amplifier's software.** We found:
   - where incoming messages are sorted and BeoSonic was being discarded;
   - its 1-millisecond timer loop;
   - its tone-control code, which turned out to be **the same code** as in the Kuga 3 amplifier;
   - its settings memory;
   - about 250 KB of unused space for new code.
3. **We opened the Explorer's sound table.** It had been labelled "encrypted", but it is just compressed with zlib. Once unpacked it reads like the Kuga 3 table, which told us exactly what the Explorer's own Bass/Mid/Treble filters are.
4. **We cracked the firmware's self-checks.** The software verifies itself with checksums, and we can recompute them. The method was proven by reproducing the checksums of four genuine Ford files.

## What the new firmware does

Think of the original firmware as a book. We didn't rewrite any chapters. We changed **nine sentences** into "see page 400" and wrote the new material on blank pages at the back. About 3.7 KB of the 512 KB file changed.

| Job | What it does |
|---|---|
| **Listen** | Keeps SYNC's BeoSonic message instead of throwing it away. All other messages are handled exactly as before. |
| **Answer** | Replies to SYNC the way the Kuga 3 amplifier does, about once a second and immediately after any change. The screen therefore shows the right state and dot position. |
| **Shape the sound** | While BeoSonic is on, it puts the **Kuga 3's own BeoSonic filters** into the amplifier's Bass, Mid and Treble filter slots. The amplifier's own filter code then calculates them, the same code as in the Kuga 3, so the sound matches a factory Kuga 3. While it is on, your normal Bass/Mid/Treble settings are kept but bypassed, like on the Kuga 3. Turning BeoSonic off puts the original filters back exactly. |
| **Remember** | Saves on/off and the dot position in the amplifier's settings memory, using the same spare bytes the Kuga 3 uses. The amplifier's **own save routine** does the writing: 5 seconds after the last change, at switch-off, and on a profile change. Each driver memory profile keeps its own setting. Missing or damaged data falls back to "off, centre". |

Finally the firmware's checksums and the file's outer checksums are recomputed, so the amplifier and the flashing tool (UCDS) see a normal Ford update.

## Frequencies: what the Explorer had, what the Kuga 3 has, and what we load

### The Explorer amplifier's tone filters

Every Explorer sound preset (16 of them: 12 or 14 speakers × source × Surround) has exactly **7 tone-filter slots**. Each slot is one filter stage in the sound processor:

| Slot | Driven by | Explorer filter |
|---|---|---|
| 0 | volume | treble shelf at 1.4–2 kHz (+2 dB down to −7 dB as volume rises) |
| 1 | volume | bass shelf at 80–100 Hz: loudness, up to +13.5 dB at low volume, 0 at high volume |
| 2 | internal parameter | 300 Hz shelf, 0…+4 dB |
| 3 | **Bass** | **60 Hz peak**, Q 0.707, −7…+7 dB (about 1 dB per step) |
| 4 | **Mid** | **1 kHz peak**, Q 0.707, −7…+7 dB |
| 5 | **Treble** | **6 kHz treble shelf**, −7…+7 dB |
| 6 | – | spare (does nothing) |

### The Kuga 3's BeoSonic filters

The Kuga 3 B&O tune has three separate BeoSonic filters:

| Kuga 3 BeoSonic filter | Driven by | Range |
|---|---|---|
| **60 Hz bass shelf** (slope 0.707) | weight c (0…100) | −6…+6 dB (50 = 0 dB) |
| **overall level** | 100 − c | −2…+2 dB |
| **7.5 kHz treble shelf** (slope 0.707) | weight d (0…100) | −6…+6 dB |

The weights c and d come from the dot position, through tables taken from the Kuga 3 firmware. Examples:

| Point | Bass 60 Hz | Treble 7.5 kHz | Level |
|---|---|---|---|
| Neutral | 0 | 0 | 0 |
| Lounge | +4.6 dB | −4.3 dB | −1.5 dB |
| Party | +3.7 dB | +5.3 dB | −1.2 dB |
| Podcast | −5.1 dB | −1.1 dB | +1.7 dB |
| Warm (edge) | +6 dB | −6 dB | −2 dB |
| Bright (edge) | −6 dB | +6 dB | +2 dB |
| Energetic (edge) | +6 dB | +6 dB | −2 dB |
| Relaxed (edge) | −6 dB | −6 dB | +2 dB |

The key difference from the Explorer's Bass/Treble: a **shelf** raises or lowers everything below 60 Hz (or above 7.5 kHz), while the Explorer's **peak** only affects a band around 60 Hz. So Kuga BeoSonic bass sounds broad and full rather than like a boom at one frequency.

### Why we borrow the Bass/Mid/Treble slots

The Kuga 3 has 10 slots per preset, with dedicated ones for BeoSonic. The Explorer has 7, with only one spare, and we need three. But when BeoSonic is on, the Kuga 3 **switches off** Bass/Mid/Treble anyway (sets them flat). So while BeoSonic is on, the Explorer's three Bass/Mid/Treble slots can host the BeoSonic filters, and the result is the same as on the Kuga.

### What happens when BeoSonic is switched on and used, step by step

1. SYNC sends "on" and the dot position. The amplifier turns the position into weights c and d using the Kuga 3 tables.
2. The amplifier tells its sound module to recalculate the tone.
3. Just before the recalculation, our code sees that BeoSonic has just been switched on, and:
   - copies the three original filter descriptions (60 Hz peak, 1 kHz peak, 6 kHz shelf) to a reserve area in memory;
   - writes the Kuga 3 filter descriptions in their place: the **Bass** slot gets the 60 Hz shelf, the **Mid** slot the ±2 dB level, the **Treble** slot the 7.5 kHz shelf;
   - keeps each slot's "label", i.e. which control drives it, so the stock code still treats them as Bass/Mid/Treble.
4. It marks all three slots for recalculation.
5. Instead of your Bass/Mid/Treble values (0…14), it feeds in the weights: c to the Bass slot, 100 − c to the Mid slot, d to the Treble slot. Each filter description has its own value-to-decibel curve: for the shelves 0 → −6 dB, 50 → 0, 100 → +6 dB; for the level 0 → −2 dB, 100 → +2 dB.
6. The amplifier's stock code calculates the filter coefficients. It's the same code as in the Kuga 3, so it produces the same coefficients. It then sends them to the sound processor.
7. When the dot moves, only the weights change, and the three slots are recalculated.
8. When BeoSonic is switched off, the original descriptions are copied back from the reserve area and the slots are recalculated with your Bass/Mid/Treble. The result is bit-for-bit what it was before.
9. When the source or Surround mode changes, the amplifier reloads the sound preset, which puts the stock filters back in the slots. Our code notices and immediately loads the BeoSonic filters again.

### What stays unchanged

- The whole Explorer sound tune: crossovers between speakers, delays, per-speaker EQ.
- The volume-dependent loudness (slots 0 and 1) and the 300 Hz shelf (slot 2) keep working as usual, also with BeoSonic on.

## How we made sure it was safe before it went into the car

- **We ran the amplifier's own code in a simulator.** That included its tone engine and its save routine, run from an independent disassembly. We fed it the new filters and checked the resulting sound curves:
  - BeoSonic off is bit-for-bit identical to stock;
  - BeoSonic on matches the Kuga 3 at every pad position;
  - the save writes correct, checksum-valid data.
- **We ran over 37,000 scenarios against a model of the intended behaviour:** every dot position and on/off combination, broken messages, reloads, profile switches, random memory contents at power-up.
- **We broke the code on purpose**, 13 different small mutations, to prove the tests catch mistakes. Every one was caught.
- **A second disassembler (Ghidra) read the new firmware back**, and it matched what was intended exactly.
- **We left a way back.** The bootloader isn't touched, so the original firmware can always be flashed back. The stock firmware ignores the saved BeoSonic bytes.

## Result

Flashed and tested in the car: the BeoSonic page works, the sound changes like on a Kuga 3, normal Bass/Mid/Treble come back when it's off, and the setting is remembered across restarts.
