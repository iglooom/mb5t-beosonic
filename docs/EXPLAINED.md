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
