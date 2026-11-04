# MB5T-14C589-CB BeoSonic firmware: technical reference

Patched application firmware for the Harman amplifier of the Ford Explorer U625 ("Sapphire 10ch", part `MB5T-14C589-CB`). It adds the B&O **BeoSonic** sound feature exactly as the Kuga 3 / Focus 4 B&O amplifier implements it. That covers three parts: SYNC protocol, filters, and per-profile memory.

- **Status:** flashed and tested in the car (Kuga 2 with retrofitted Kuga 3 B&O speakers and ACM, and this Explorer amplifier). It works.
- **Tags:** **[V]** = read from code/data or reproduced by a tool in this repository; **[I]** = inferred.
- **Function names** are Ghidra `FUN_xxxxxxxx` from `analysis/MB5T-14C589-CB.c` / `.lst`.

---

## 1. Target

| Item | Value |
|---|---|
| ECU | amplifier, diagnostic CAN 0x783 (request) / 0x78B (response), I-CAN/HS3 500 kbit/s |
| Application | `MB5T-14C589-CB` (DID F188), one VBF block 0x80000..0xFFFFF |
| EQ table | `LB5T-14C656-BH` (not modified) |
| MCU / DSP | Renesas V850E2/Fx4 (GHS compiler) / ADI SIGMA300 |
| Registers | gp = 0xFEDF9620, tp = 0x89080 (reset code 0x883E6..0x88426) |
| Stock VBF | `inputs/MB5T-14C589-CB.VBF`, sha256 `e66c56c7…1897a3b5` |
| Stock image | `inputs/MB5T-14C589-CB_0x80000.bin`, sha256 `dcf89afc…05026e44` |
| **Release VBF** | `release/MB5T-14C589-CB_BeoSonic.VBF`, sha256 **`fb6a8c0e3b7bd4689a5b59c8b58b76a9b034ce9531f464e70ca061def4bcd7f4`** |
| Release image | `release/MB5T-14C589-CB_BeoSonic.bin`, sha256 `fb304cf1ff5b7b346024ed48a5721d0c3b4d43dd87451b2d3b03c9c1405385f9` |

## 2. BeoSonic as implemented by the Kuga 3 amplifier (reference behaviour)

The Kuga 3 / Focus 4 B&O amplifier is `PJ6T-14C589-AD`: the same Harman family, CPU and DSP, and largely the same code base.

### 2.1 CAN protocol with SYNC 3 [V bench capture + PJ6T code]
| Frame | Direction | Bytes |
|---|---|---|
| **0x105** | SYNC → amp, event-driven; each request is followed by an all-zero frame 260 ms later | b5 = enable request (2 = on, 1 = off, 0 = idle); b6 = X + 1; b7 = Y + 1 (pad 0..30 → raw 1..31) |
| **0x22D** | amp → SYNC, about 1 s period plus on every change | b0 = X raw, b1 = Y raw, b2 bits 7:6 = 1 off / 2 on / 0 not configured |

- SYNC shows the BeoSonic page when the APIM has the B&O Tone Touch HMI enabled.
- The page shows what 0x22D reports.
- Pad presets in HMI coordinates: Neutral (15,15), Lounge (15,4), Party (27,19), Podcast (5,23).

### 2.2 Pad → filter weights [V]
`FUN_0008cdb8` (PJ6T) works in two steps:
1. Two 31×31 ROM tables T1/T2 (tp−0x7598, tp−0x71D4) warp the round pad onto a square (u, v). Points outside the circle are rejected.
2. Two more tables, T3/T4 (tp−0x6E2E, tp−0x6A6A), give the weights **c, d ∈ 0..100**. `tools/beosonic_model.py` reads these tables from `inputs/PJ6T-14C589-AD_0x80000.bin`.

### 2.3 The filters [V]
The filters are tag-0x61 tone records of the active EQ preset. In all four Kuga 3 tune presets of `LJ6T-14C656-BC` they are:

| Record mask | Filter | Driven by | Range |
|---|---|---|---|
| 0x10000 | low shelf **60 Hz**, slope 0.707 | c | −6..+6 dB |
| 0x4000 | broadband level | 100 − c | −2..+2 dB |
| 0x8000 | high shelf **7.5 kHz**, slope 0.707 | d | −6..+6 dB |

- So X behaves like a loudness/"smile" axis, and Y like a warm↔bright tilt.
- While BeoSonic is on, Bass/Mid/Treble are flat but stay stored.
- X/Y and on/off are saved per audio profile in the EEPROM slot record (+0x10 X, +0x11 Y, +0x12 state).

### 2.4 What the stock Explorer amplifier lacks
- **RX:** it drops 0x105 in its software filter.
- **TX:** it never sends 0x22D.
- **Filters:** its EQ table has no BeoSonic records.
- **Memory:** it does not use slot bytes +0x10..+0x13.

## 3. Stock MB5T facts the patch relies on

### 3.1 Integrity layers [V]
| Word | Covers | Algorithm | Checked by |
|---|---|---|---|
| **0xFFBFC** | [0x80000, 0xFF800) | Adler, seed 1, `FUN_00088e3a` type 0. a/b are reduced by 0xFFF1 only above 0xFFF1, in 0x400 chunks | background monitor `FUN_0009a072` → DTC-manager flag; `FUN_0009b2e0` compares it with data-flash 0x200 ("new software") |
| **0xFFEAC** | [0x00000, 0xFFC00), whole flash including the bootloader | same | `FUN_0009a540` (diagnostic status) |
| 0x77FFC | [0x10000, 0x77C00), EQ area | same | other-flash monitor |
| VBF | block CRC-16 + file CRC-32 | – | flasher |

- The bootloader bytes are not in the VBF. 0xFFEAC is therefore updated by **delta**: Adler is linear per byte position.
- The delta is proven by reproducing the stock words of two different builds (LB5T-AG ↔ MB5T-CB, implied prefix state (30251, 26228)).
- Repair order: 0xFFBFC, then 0xFFEAC, then the VBF CRCs.
- `tools/mb5t_cksum.py selftest` reproduces all of this on four OEM apps.

### 3.2 CAN [V]
- **RX.** AFCAN at 0xFF480000.
  - Mailboxes 30/31 use mask 1 (all don't-care), so they receive every frame no dedicated mailbox takes.
  - `FUN_000ab81e` filters those frames in software against 10 IDs. **0x105 is dropped.**
  - The frame image lives at gp+0xA04 (data), gp+0xA0C (DLC), gp+0xA10 (ID<<2).
- **TX.** All six stock frames go through mailbox 0 with a software pending mask. **Mailboxes 1 and 2 are configured unused** by `FUN_000aba16`.
  - Mailbox m registers: data byte i at 0xFF481000 + 0x40·m + 4·i, DLC +0x20, MCONF +0x24, MIDH 0xFF489030 + 0x40·m, MCTRL 0xFF489038 + 0x40·m. MCTRL bits 15:8 set, bits 7:0 clear; RDY = bit 0, TRQ = bit 1.
  - Stock TX gates: gp+0x9EE bit0 (TX enabled), gp+0x9EF bit0 (communication control), 0xFF48024C bit4 (controller not usable).
- **CAN task.**
  - Entry 0xA72F0 calls `FUN_000a70d6` at start.
  - Its 1 ms timer branch (0xA739A) calls `FUN_000adfee`.
  - Messages are posted with `FUN_0008c7cc(queue, 0, prio, msgId, &payload, len)`.

### 3.3 Tone path [V]
- **App tone values:** gp−0x2941 Bass, −0x2940 Mid, −0x293F Treble. Range 0..14, 7 = flat, 0xF = never set. gp−0x299E is the app "flat" flag (1 = forced flat).
- **DSP task.**
  - The DSP queue is *(gp−0x7C78).
  - Messages 0x1011/0x1013/0x1012 store Bass/Mid/Treble into the **tone ctx gp−0x934** (+8/+9/+0xA).
  - `FUN_0009f33c` then runs `FUN_0008b92a(ctx, 0)`, and SigmaDSP ioctl 0x907 if any stage is dirty.
- **`FUN_0008b92a(ctx, force)`.** For masks 1/2/4/8/0x10/0x20 it compares each value with its shadow (ctx+0x2E…+0x34). For each changed one it calls `FUN_0008b874(mask, value, ctx)`.
- **`FUN_0008b874`.** It walks the record array *(ctx+4) (count u16 at ctx+0x2C, 0x3C bytes per record) and handles every record whose mask matches:
  1. It interpolates the gain from the record's piecewise-linear curve (`FUN_0008c4ce`).
  2. It designs the biquad by type: 1 peak `FUN_0008b442`; 2/4 low/high shelf `FUN_0008b64c`; 8 gain `FUN_0008b250`.
  3. It writes 5 words **b0 b1 b2 a1 a2 (8.24)** to ctx+0x10 + 0x14·**position**, and sets dirty bit 1<<position.
- The DSP stage is the record **position**. The capacity is 7 (ctx+0, set at init; the array is at gp+0x4D8).
- **Records** are loaded only by `FUN_0008b27e(ctx, preset)`. Its only caller is `FUN_0009c8e0` (preset change), which afterwards calls `FUN_0008b92a(ctx, 1)` and ioctl 0x907.
  - The loader copies 0x16 + 0x22 bytes per record into the 0x3C layout and byte-swaps the fields.
  - Native layout: +0 mask, +4 type, +8, +0xC, +0x10 K = tan(πf/48000) Q31, +0x14 Q/slope Q14, +0x18 4×u32 slopes, +0x28 4×s16 offsets, +0x30 5×s16 breakpoints.
- **Same code as the Kuga 3 amp.** The loader, the dispatch, the interpolator and all three designers are **instruction-for-instruction identical to PJ6T** (`FUN_0008c5a6/c76a/c974/c578/d90c`). Only branch targets and a +4 shift of the ctx fields differ. The same records therefore give the same coefficients.

### 3.4 EQ table LB5T-14C656-BH [V]
- The VBF payload is a **plain zlib stream**, decompressed by the bootloader. It inflates to 0x70000 bytes = the EQ area 0x10000..0x7FFFF (`tools/eq_table.py`).
- Layout: TLV preset blocks, stride 0x2000, format 5.4; 19 presets, all block checksums OK; 0x77FFC reproduced.
- **Preset id = DID DE01**:

  | Id | Presets |
  |---|---|
  | 4 | 12-speaker (2–9) |
  | 5 | 14-speaker (10–17) |
  | 0 | protection |
  | 0xFE | test |

  The selector `FUN_00089696` takes the id from msg 0x1040 (gp−0x7C5D, initialised from the audio header). The DE01 read handler 0xA66E0 returns the same byte (gp+0x967). The source and surround variant come from a mask (msg 0x103D).
- **12 vs 14 speakers** (DA, Surround Off):

  | Speaker group | 12Spk (id 4) | 14Spk (id 5) |
  |---|---|---|
  | Front | woofers up to 3.5 kHz | woofers 50–248 Hz plus midranges 229 Hz–3.9 kHz |
  | Tweeters | HPF 2.5 kHz | HPF 2.5 kHz |
  | Subwoofer | its own DSP block | a different DSP block |

- **Tone records per preset (7):**

  | Pos | Mask | Filter |
  |---|---|---|
  | 0, 1 | 8 | volume-dependent loudness: shelves at about 1.4–2 kHz and 80–100 Hz |
  | 2 | 0x10 | 300 Hz shelf |
  | 3 | 1 Bass | peak 60 Hz, Q 0.707, −7..+7 dB |
  | 4 | 2 Mid | peak 1 kHz, Q 0.707, −7..+7 dB |
  | 5 | 4 Treble | high shelf 6 kHz, −7..+7 dB |
  | 6 | – | spare unity stage |

### 3.5 Non-volatile storage
See `EEPROM.md`.
- The audio slot record is 0x18 bytes at EEPROM 0x820 + 0x20·slot, with its RAM copy at gp−0x2974.
- Bytes **+0x0F..+0x13 are unused by MB5T and preserved by every stock write**, including after a rollback.
- The checksum is an Adler variant with seed 0 over 0x14 bytes at +0x14 (`FUN_00092698`).
- `FUN_00097ffc(mode, now, p3)` is the stock save routine:
  - mode 1 arms it: gp−0x7BE0 = 1, gp−0x7BE4 = now;
  - mode 2, every 20 ms, writes once more than 5000 ticks have passed;
  - mode 3 is a forced flush: at shutdown, on a profile change and on store-to-profile.
  - Before writing it compares the record fields with the live values. The "changed" flag is held in **r29**. If anything changed it bumps the counter, recomputes the checksum and calls `FUN_0008c3ea(src, 0x3200820 + slot·0x20, 0x18)`.
- `FUN_000988ce` loads a slot (on wake and on a profile change). It calls `FUN_00093c8c` to apply the slot, then sets gp−0x2947 = 1 ("audio NVM loaded").
- OS tick: word gp−0x6580 (`FUN_000ba90a`).

## 4. The patch

### 4.1 Hooks [V Ghidra decode]
Every hook is a 4- or 6-byte instruction replaced by `jarl <cave>,lp` (+`nop`).
- The builder asserts the stock bytes at each site.
- It also asserts that no hook interior is a branch target.
- At every site, lp is already saved by the host prologue.

| Site | Stock instruction | Cave | Context |
|---|---|---|---|
| 0xAB84A | `andi 0x1ffc,r16,r28` (`FUN_000ab81e`) | RX | CAN RX ISR |
| 0xA72F8 | `jarl 0xA70D6` (CAN task start) | INIT | CAN task |
| 0xA739A | `jarl 0xADFEE` (1 ms branch) | TICK | CAN task |
| 0x8B93A | `ld.b 0x8[r6],r7` (`FUN_0008b92a`, Bass) | EB | DSP task |
| 0x8B956 | `mov r29,ep; sld.b 0x2f[ep],r19; sld.b 0x9[ep],r7` (Mid) | EM | DSP task |
| 0x8B970 | `mov r29,ep; sld.b 0x30[ep],r18; sld.b 0xa[ep],r7` (Treble) | ET | DSP task |
| 0x9CBB6 | `jarl 0x8B27E` (record loader, `FUN_0009c8e0`) | LEQ | DSP task |
| 0x988FC | `jarl 0x93C8C` (apply slot, `FUN_000988ce`) | LOADH | app task |
| 0x98134 | `ld.w -0x2914[gp],r1` (record compare, `FUN_00097ffc`) | CMPH | app/system task |

### 4.2 Layout
- **ROM**, all in the stock `00 40` fill (0xC0DB0..0xFFBFC free):

  | Range | Content |
  |---|---|
  | 0xC1000..0xC163E | code (1,598 B) |
  | 0xC2000 | 3 × 0x3C native filter templates |
  | 0xC2100 | LUTC: 961 × c, or 0xFF = outside the pad |
  | 0xC2500 | LUTD: 961 × d |

  The LUT index is 31·(Y−1) + (X−1), with X/Y raw.
- **RAM** 0xFEDFF000..0xFEDFF0F4 (gp+0x59E0..). The stock crt table places .data/.bss at 0xFEDF1600..0xFEDFAB48 (startup stack and libc words up to 0xFEDFACA0). No instruction or constant references 0xFEDFACA0..0xFEDFFFF0. The car test confirms the block is free.

| Off | Name | Off | Name |
|---|---|---|---|
| +0x00 u32 | RXW (latched request) | +0x10/+0x11 | DSPB/DSPT (published c/d) |
| +0x04 | ON | +0x12 | TRIG (refresh payload) |
| +0x05/+0x06 | CX/CY (raw X/Y) | +0x14 u32 | SNAP (record bytes to save) |
| +0x07/+0x08 | BB/BT (c/d of CX/CY) | +0x18 u32 | SNAPM (= 0x5A3CB5A5 when SNAP is valid) |
| +0x09 | PEND (DSP refresh pending) | +0x1C..+0x1F | LOADSEQ, LASTSEQ, IMPORTED, CHG |
| +0x0A | TXNOW | +0x20 u32 | SWAPPED (= 0x5A3C0001 when the records carry the templates) |
| +0x0B | LASTFLAT | +0x40..+0xF3 | SAVEBUF (3 original records) |
| +0x0C u16 | TXCNT | +0x0E u16 | DSPON (= 0x5A3C when on) |

Cross-task values are written with single aligned stores. "Valid" states need 32-bit magics, so garbage RAM at power-up cannot pass as valid.

### 4.3 Caves
- **RX** (ISR). For ID 0x105 with DLC ≥ 8 and a non-zero payload, it stores `0x01000000 | (b5&3)<<16 | b6<<8 | b7` in RXW and jumps to the stock epilogue. Every other ID returns unchanged. It uses only dead registers.
- **INIT.** Sets the state to: off, X = Y = 16 (pad centre), c = d = 50, no refresh pending, 0x22D due now, SNAP/SNAPM/IMPORTED/CHG cleared. Then it jumps to the stock `FUN_000a70d6`.
- **TICK** (every 1 ms, after the stock `FUN_000adfee`):
  1. *Import.* When gp−0x2947 = 1 and (not yet imported or LOADSEQ changed):
     - Decode record bytes +0x10..+0x13. They are valid only if 1 ≤ X, Y ≤ 31, state ∈ {1, 2}, chk = X^Y^st^0xB5, and the point is inside the pad. Anything else gives off at (16,16).
     - Apply X/Y/state, look up c/d, request a DSP refresh and a 0x22D, and set SNAP from the imported state.
  2. *Frame.* Take RXW atomically (di/ei).
     - Enable 2 while off → on; enable 1 while on → off.
     - X/Y are accepted only while on, and only if inside the pad. Then CX/CY/BB/BT are updated.
     - Every request triggers an immediate 0x22D echo.
  3. *Snapshot.* On any accepted change, once imported: SNAP = encode(CX, CY, state). It then arms the stock save debounce like a tone change: gp−0x7BE4 = OS tick, gp−0x7BE0 = 1.
  4. *Refresh.* On a change or an edge of the app flat flag, and only if not flat and the DSP queue exists:
     - publish DSPB/DSPT and DSPON;
     - post DSP msg 0x1011 with the app Bass value, or the DSP's own value if never set. The stock handler re-runs the tone engine.
  5. *0x22D.* Every 1000 ticks and on request, when the stock gates allow, from mailbox 1. It clears RDY (bounded poll), sets MCONF = TX, MIDH = 0x22D, DLC 8, data `X Y (on?0x80:0x40) 00…`, then RDY and TRQ. MCONF is rewritten each time, so it survives a bus-off re-init.
- **EB** (start of `FUN_0008b92a`; r6 = r29 = ctx):
  - "Wanted" = DSPON is magic and the app flat flag ≠ 1.
  - If that differs from SWAPPED, the swap state changes:
    - *On:* first check that masks 1/2/4 each occur exactly once; otherwise do nothing. Then copy each original record to SAVEBUF and copy the template over it, keeping the mask word and words +8/+0xC.
    - *Off:* copy SAVEBUF back.
    - In both cases, write 0x7F to the Bass/Mid/Treble shadows so all three stages are redesigned in this call.
  - While swapped, the Bass value is c.
- **EM / ET** (Mid / Treble field loads). They replay the stock loads. While swapped they return 100 − c and d respectively; otherwise the stock values.
- **LEQ.** Calls the stock loader, then clears SWAPPED: the loader has just written stock records. The stock forced `FUN_0008b92a(ctx,1)` that follows re-swaps if BeoSonic is on.
- **LOADH.** Calls the stock `FUN_00093c8c`, invalidates SNAPM and increments LOADSEQ. TICK then re-imports. Until it does, no save can write the previous profile's state into the newly loaded slot.
- **CMPH** (inside the stock record compare). If SNAPM is valid and SNAP differs from record bytes +0x10..+0x13, it copies SNAP into the record and sets **r29 = 1** (the stock "changed" flag). It then replays `ld.w -0x2914[gp],r1`. The **stock** code bumps the counter, recomputes the checksum and writes the slot. Timing follows the stock rules: 5 s debounce, shutdown, profile change, store.

### 4.4 Filter templates [V]
- The three BeoSonic records of preset `2_SurrOFF_CX482_6ch_LHD_MP_Final` (`inputs/LJ6T-14C656-BC_0x10000.bin`) are converted to the native layout exactly as the stock loader does it.
- The conversion is checked against the emulated stock loader.
- Mask words are rewritten to 1 / 2 / 4 so that the stock dispatch drives them from the Bass/Mid/Treble fields:

| Slot | Template | Value |
|---|---|---|
| Bass (mask 1) | LS 60 Hz, type 2 | c |
| Mid (mask 2) | level, type 8 | 100 − c |
| Treble (mask 4) | HS 7.5 kHz, type 4 | d |

### 4.5 Persisted bytes (slot record +0x10..+0x13)
`X raw (1..31)`, `Y raw (1..31)`, `state (1 = off, 2 = on)`, `chk = X ^ Y ^ state ^ 0xB5`.
- The layout is the Kuga 3's, plus the check byte.
- Each audio profile slot keeps its own setting.
- A blank, stock or invalid record imports as off at the centre. That default is written at the next save.

## 5. Verification (all in `tools/`)

| Tool | What it proves |
|---|---|
| `mb5t_cksum.py selftest` | the 0xFFBFC/0xFFEAC algorithms and the delta reproduce four OEM apps byte-exact |
| `v850asm.py selftest` | the cave assembler re-encodes 59,814 stock instructions of the used forms with 0 mismatches |
| `eq_table.py selftest` | EQ table inflate, block checksums, 0x77FFC, and the Explorer tone records |
| `nvm_tool.py selftest` | the NV checksum port matches emulation of the stock `FUN_00092698` (306 vectors) |
| `tone_emu.py` | runs the **stock** loader, tone engine and designers from the Ghidra listing text, with patched bytes through an independent byte decoder (see below) |
| `test_caves.py` | emitted cave bytes vs independent Python models: LUT 961, RX 2,100, INIT, TICK 33,211 (exhaustive frames; 232 record patterns × load/import states; TX/DSP gates; random 10,000-tick runs with reloads and garbage boot RAM), LOADH, CMPH 800, EM/ET 264. 0 failures |
| `test_persist.py` | runs the **stock** save routine with CMPH (see below) |
| `fault_inject.py` | 13 single-instruction mutations of the caves, each detected by at least one harness |
| `ghidra_verify.sh` + `verify_dis.py` | Ghidra's SLEIGH decode equals the builder listing (515 instructions); all 9 hooks decode to the intended `jarl` |
| `build_vbf.py` (uses `vbftool.py`) | VBF header identical to stock except the checksum digits; payload = image; CRCs OK; the diff against stock is exactly 15 clusters (9 hooks, code, templates, 2 LUTs, 2 checksum words) |

**`tone_emu.py` checks:**
- The stock loader's RAM records equal the template conversion.
- The coefficient layout fits the Explorer's documented curves within 0.002 dB.
- **Off:** coefficients are bit-identical to the stock engine.
- **On:** every pad point gives the Kuga gains within 0.35 dB.
- Restore is bit-exact.
- Weights equal to the previous values still trigger a redesign.
- The flat flag or a corrupt record set prevents swapping.
- A reload re-swaps.

**`test_persist.py` checks:**
- Due and forced saves write the BeoSonic bytes with a checksum that the stock validity rule accepts.
- Not-due, not-loaded, unchanged, invalid-snapshot and stock-image cases write nothing.

`build.py` is reproducible; the release hashes in §1 are its output.

## 6. Flashing, test, rollback
1. **Flash.** Flash `release/MB5T-14C589-CB_BeoSonic.VBF` with UCDS like any 14C589 application. The VBF header is the stock one (part number, `sw_part_type EXE`, address 0x783, erase map). Read DTCs afterwards.
2. **SYNC.** The APIM must have the B&O Tone Touch / BeoSonic HMI enabled.
3. **Expected behaviour:**
   - The page tracks the amp.
   - On: Bass/Mid/Treble are bypassed and the Kuga 3 filters apply. Warm and Energetic are about 2 dB quieter, Bright and Relaxed about 2 dB louder.
   - Off: the user's tone returns unchanged.
   - The setting survives ignition cycles once it has been held for more than 5 s or the amp has shut down normally.
4. **Rollback.** Reflash `inputs/MB5T-14C589-CB.VBF`. The bootloader and the other flash regions are untouched. The stock app ignores slot bytes +0x10..+0x13.

## 7. Known limits
- **No configuration gate.** The amp always reports "configured" in 0x22D. The Kuga 3 amp gates on DE00 byte1 bit4.
- **Bootloader.** The bootloader (0..0xFFFF) is not available. It accepted this image in the car.
- **Residual profile-switch race** [I]: a TICK would have to be preempted exactly between writing SNAP and SNAPM, during a profile switch, with a save in the same ≤ 1 ms. The worst case is one profile receiving another's BeoSonic position.
- **Tune.** The vehicle tune itself is the Explorer's (DE01 4 = 12-speaker, 5 = 14-speaker presets). The BeoSonic filters are applied on top, as on the Kuga 3.
