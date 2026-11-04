# MB5T-14C589-CB (Explorer U625 Harman amp): non-volatile storage reference

Scope: how the stock application stores data (external SPI EEPROM and internal data flash), the record map, the checksum, and how the Kuga 3 amplifier (PJ6T-14C589-AD) keeps its BeoSonic state.
Base facts: gp = 0xFEDF9620, tp = 0x89080 (`TECHNICAL.md`).
Tags: **[V]** = read from code/data or reproduced by a tool; **[I]** = inferred.
Function names are Ghidra `FUN_xxxxxxxx` in `analysis/MB5T-14C589-CB.c`.
Tool: `tools/nvm_tool.py` (checksum port + emulation check, record codec, dump decoder, list of access sites).

There are two NV stores:
- **SPI EEPROM:** the audio records (0x3200800 header, 0x3200820 + 0x20·i slots).
- **Internal data flash:** DE00 @0x80, DE02 @0xE0 and the software-change word @0x200 (separate API, §2).

---

## 1. SPI EEPROM: driver stack

### 1.1 Virtual address space [V]
The app reaches every memory through a virtual-region table: 24 entries × 0x2C bytes at gp−0x56DC (0xFEDF3F44).
- `FUN_0008c034` allocates an entry; `FUN_0008c0de` activates it (+0x29 = 1).
- `FUN_0008c140(addr, &len)` looks up the entry and clips len to the region end.

| Entry field | Meaning |
|---|---|
| +0x00 / +0x04 | virtual base / size |
| +0x08 flags | bit0 = backed by a device driver; bit1 = bounce buffer + region semaphore; 8 = plain memcpy |
| +0x0C flags | bit0 = byte-swap on read/write (`FUN_0008c194`); bit1 = length in 32-bit units |
| +0x10 | physical offset (device) or backing address (memcpy) |
| +0x14 / +0x18 | device handle / seek ioctl code |
| +0x1C / +0x20 | bounce buffer / chunk size |
| +0x24 | region semaphore |

Regions registered by the stock app:

| Virtual base | Size | Backing | Registered in |
|---|---|---|---|
| **0x03200000** | **0x1000** | **"EEPROM" driver** (flags 0xB, seek ioctl 0x82, buffer gp−0x3458 (0xFEDF61C8), chunk 0x100, own semaphore) | `FUN_0008f4b4` |
| 0x00000000 | 0x100000 | code flash (memcpy) | `FUN_0009b010` |
| 0x03010000 | 0x68000 | code flash @0x10000 | `FUN_0009b010` |
| 0xFEDF0000 | 0x10000 | RAM | `FUN_0009b010` |
| 0x03300000.. / 0x03000100 / 0x00800200 | small | ROM constants (identification strings, build date) | `FUN_0009b010` |
| 0x03100000 (0xF), 0x03100100 (8) | – | RAM shadows gp−0x2A14 / gp−0x2800 (not NV) | `FUN_000934c6`, `FUN_00099b88` |
| 0x04400000 | 0x20 | "CDCE913" clock-synth driver over "V850Fx4-IIC-bitbang" | `FUN_0009b25a` |

### 1.2 Public API [V]
| Function | Signature | Notes |
|---|---|---|
| **`FUN_0008c362`** | `int vread(void *dst, u32 vaddr, u32 len)` | returns bytes read, or −2 if no region matches. Optional swap per +0x0C. |
| **`FUN_0008c3ea`** | `void vwrite(const void *src, u32 vaddr, u32 len)` | returns nothing useful; **errors are not reported**. |
| `FUN_0008c232` | core: `(region, off, buf, len, dir)` | chunks by +0x20 under the region semaphore; calls seek (`FUN_00089338(dev, 0x82, phys)`), then driver read/write. |
| `FUN_0008c45a` | `u32 vread32(vaddr)` | wrapper |

- EEPROM address encoding: **vaddr = 0x03200000 + EEPROM byte offset** (0x000..0xFFF).
- Both calls are **synchronous and blocking** in the caller's task. They take the region semaphore and the driver semaphore (`FUN_000ba18c`, OS wait) and may sleep (`FUN_000ba7a4`). Never call them from an ISR [V]. Avoid the 1 ms CAN-task tick as well (§1.3 timing).

### 1.3 "EEPROM" device driver [V]
- Driver descriptor in .data (ROM image 0xBEBDC → RAM 0xFEDF2760): name "EEPROM" (0x83958).
  vtable: open 0xB19C8, close 0xB19E6, **read 0xB1B44**, **write 0xB1E4C**, **ioctl 0xB1EC0**.
- It talks to the "V850Fx4-SPI" driver (0x81A08); SPI config list at 0x819A8 (CS port registers 0xFF40000C/0xFF40030C).
- Chip table at 0x83960: {name, page<<16 | size, tWC<<16 | read-chunk}.

| Index | Part | Size | Page | tWC (ticks) |
|---|---|---|---|---|
| 0 | ST95080 | 0x400 | 32 | 10 |
| 1 | ST95160 | 0x800 | 32 | 10 |
| **2** | **ST95320** | **0x1000** | **32** | **5** |
| 3 | ST95640 | 0x2000 | 32 | 5 |

- **Selected part: index 2 = ST95320 (4 KiB).** The config struct gp−0x267C has +4 = 2, set in the system task `FUN_0009b62c` before `FUN_000b198a`; ioctl 0x80 loads the table entry. This matches the region size 0x1000 [V].
- ioctl codes: 0x80 select part, 0x81 SPI handle, 0x82 seek (rejects ≥ size, −5), 0x83 command buffer, 0x84 status buffer, 0x86 tWC override, 2 → "is EEPROM".
- **Read** `FUN_000b1a28`:
  1. Poll RDSR (0x05) until WIP = 0. Between polls it sleeps tWC ticks (`FUN_000ba7a4(5)`). At most 7 polls.
  2. Then `READ` (0x03, addr hi, addr lo) in chunks.
- **Write** `FUN_000b1e4c`:
  1. Bounds check (addr + len ≤ size, else −8).
  2. **`FUN_000b1bda(dev, 3)`**: poll WIP, then WREN (0x06) + **WRSR** that clears BP1:BP0. This is done on every write call, so each write also costs one status-register write cycle.
  3. **`FUN_000b1cf6`**: per 32-byte page, poll WIP (sleep 5 ticks while busy), WREN, `WRITE` (0x02, addr hi, addr lo, data). Page splits use page size 32.
- **Completion:** the driver does not wait after the last page. The next access polls WIP. A 0x18-byte record in one page therefore costs about one tWC of blocking (for the WRSR cycle) plus SPI time, about 5–10 ms with a 1 ms tick [I: tick].
- **Error handling:** if WIP is still set after 7 polls (about 35 ms), sticky flag dev+0x26 = 1. **Every later access then returns −11 until reset.** `vwrite` does not propagate it. No retry, no wear levelling, no write counting in the driver [V].
- Which task does the physical write: the caller's. Callers in the stock app are the app/audio task (`FUN_0008f54e`) and the system task (`FUN_0009b62c`, shutdown message 0x1020) [V call graph; task identity I].

## 2. Internal data flash (not the EEPROM) [V]
- API: **`FUN_000926f6(off, buf, len)` read** and **`FUN_000927c0(off, buf, len)` write**.
  - Offsets are 0..0x7FFF in the 32 KiB data-flash window at 0xFE000000. 0xFE00xxxx is also accepted.
  - len and off must be even. They return 0 on success.
  - They are gated by gp−0x7CF8 (FDL ready, set by `FUN_000926bc`).
- Implemented by the flash library at 0xC08E8/0xC0BF8 with busy-polling (CPU spins, no OS sleep).
  - Write = erase command over ⌈len/32⌉ units starting at off>>5, then program.
  - So **any write erases whole 32-byte units** [V code; DF block size I].
- The EQ table LB5T-14C656 (0xFE002800) lives in the same window.
- Record format used by the DIDs: `data[n] | pad to 4 | Adler (§3) big-endian`. All-0xFF = blank.

| DF offset | Size | Content | Read / write |
|---|---|---|---|
| 0x000 | 2 | marker written at shutdown and by `FUN_000a45f0` | `FUN_0009b62c` |
| 0x060 | 0x10 | Harman service routine data | `FUN_000a5118` |
| **0x080** | 8 | **DE00** (3 B + pad + cksum BE) | `FUN_000a65fe` / `FUN_000a4aae` |
| 0x0A0 | 0x18 | service data (F1xx) | `FUN_000a9a2a` / `FUN_000a5118` |
| 0x0C0 | 0x18 | F113 | `FUN_000a6a2c` / `FUN_000a5118` |
| **0x0E0** | 6 | **DE02** (1 B + pad + cksum BE) | `FUN_000a674c` / `FUN_000a4c02` |
| 0x100, 0x120 | 8 each | 4 B + cksum BE | `FUN_000a4e2a`/`a4dd0`, `FUN_000a4d0a`/`a4cb0` |
| 0x140, 0x160 | 8 each | 4 B + cksum BE | `FUN_000a4ef0`/`a4f9c` via `FUN_000a4ff2` |
| 0x1A0 | 8 | reset-cause record | `FUN_0009c122`/`9c1e2` |
| **0x200** | 4 | copy of the app Adler word 0xFFBFC ("new software" detection) | `FUN_0009b2e0` |

When 0x200 differs from 0xFFBFC, `FUN_0009b2e0` reprograms the CDCE913 clock synthesiser over I²C and then stores the new word. This runs once after each reflash [V].

**Do not use the data flash for BeoSonic state.** The erase granularity, busy-polling and limited endurance make it a poor fit; the EQ table shares the window.

## 3. Checksum used by every NV record [V]
`FUN_00092698(buf, len8)` → `FUN_00088f26` with ctx {type 0, buf, len, **seed 0**} → `FUN_00088e3a(buf, len, 0, mode 0)`:
```
a = b = 0
for x in buf[:len & 0xFF]:
    a += x;  if a > 0xFFFF or a > 0xFFF1: a = (a + 0xF) & 0xFFFF
    b += a;  if b > 0xFFFF or b > 0xFFF1: b = (b + 0xF) & 0xFFFF
return b << 16 | a
```
- This is the same routine as the app-flash check (`mb5t_cksum.adler_fw`), but with seed 0 instead of 1. len is truncated to 8 bits.
- EEPROM records store the u32 **little-endian** (raw RAM copy). Data-flash DID records store it **big-endian**.
- `nvm_tool.py selftest`: the port matches the emulated stock ROM code (`FUN_00092698`→`88f26`→`88e3a`) on 306 vectors (0..300 bytes, edge patterns), 0 mismatches.
- PJ6T `FUN_00093b18` → `FUN_0008a0bc` is the identical construction [V decompile].

## 4. EEPROM map (ST95320, 4 KiB) [V unless noted]
All constant EEPROM addresses in the image were enumerated: every `mov imm32` / data word in 0x03200000..0x03200FFF, both `movhi 0x320` sites, and every `vread`/`vwrite` site with a computed address. Run `nvm_tool.py sites` to list them.

| EE offset | Len | Content | Check | Writer → when |
|---|---|---|---|---|
| 0x000 | 0x0C | 2 × u32 counters (+1 each write) + cksum(8) | cksum LE @+8 | `FUN_0009b3ec`, at every shutdown (sys msg 0x1020) |
| 0x020 | 0x0C | second copy of 0x000 | same | same |
| 0x0C0 | 0x3C | record A (gp−0x6710) | cksum(0x38) | `FUN_00099014` |
| 0x100 | 0x3C | record A copy | same | same |
| 0x180 / 0x1A0 | 0x0C each | 12-byte records selected by request byte0 = 0/1, written by `FUN_0008e1da` from the UDS dispatcher `FUN_0008ec78` (fingerprint-like [I]) | – | tester request only |
| 0x2A0 | 0x0C | DTC-manager state (gp−0x3070) | cksum(8) | `FUN_00090d6e` |
| 0x2C0 | 0x0B | DTC bitmap, 0x56 bits (gp−0x3050) | – | `FUN_00090d5e` |
| 0x3C0 | 0x38 | record B | cksum(0x32) | `FUN_0009c524` / `FUN_0009ebc0` |
| 0x400 | 0x240 | event memory: 12 × 0x30 (RAM mirror gp−0x2E40) | per record | `FUN_00090d12` |
| 0x700 | 0x60 | event memory: 2 × 0x30 (mirror gp−0x2C00) | per record | `FUN_00090d12` |
| **0x800** | 0x18 | **audio header** (RAM gp−0x2914) | cksum(0x14) @+0x14 | `FUN_00097ffc` |
| **0x820 + 0x20·i**, i = 0..4 | 0x18 | **audio slot i** (RAM copy of the active slot at gp−0x2974) | cksum(0x14) @+0x14 | `FUN_00097ffc`, `FUN_0009836a`, `FUN_00093580` |

Never written by the stock app [V by enumeration; contents on a real part unknown]:
- 0x00C–0x01F, 0x02C–0x0BF, 0x0FC–0x0FF, 0x13C–0x17F, 0x18C–0x19F, 0x1AC–0x29F, 0x2AC–0x2BF, 0x2CB–0x3BF, 0x3F8–0x3FF, 0x640–0x6FF, 0x760–0x7FF;
- the tails 0x818–0x81F and 0x838+0x20·i .. 0x83F+0x20·i;
- **0x8C0–0xFFF**.

Caveats:
- The UDS RequestDownload/TransferData path (`FUN_0008e728`) and the Harman service routine (`FUN_000a5118` case 4) can write any virtual address, so a tester or the EOL station may have used "free" space [I].
- An older app version might also have used it [I].

### 4.1 Audio header @0x800 (RAM gp−0x2914 = 0xFEDF6D0C) [V `FUN_00097ffc`, `FUN_0009370e`]
| Off | Field (live source) |
|---|---|
| +0x00 | u32 write counter |
| +0x04 | gp−0x7C5B |
| +0x08 | u32 gp−0x29F4 |
| +0x0C | gp−0x28F6 |
| +0x0D | gp−0x2944 |
| +0x0E | gp−0x294B |
| **+0x0F** | **active slot index** gp−0x294C (→ gp−0x2905) |
| +0x10 | gp−0x2996 |
| +0x11..+0x13 | unused |
| +0x14 | cksum(0x14) LE |

### 4.2 Audio slot record (RAM copy gp−0x2974 = 0xFEDF6CAC, 0x18 bytes, stride 0x20) [V]
| Off | MB5T field (live source in `FUN_00097ffc`) | Default (`FUN_0009382e`) | PJ6T same offset |
|---|---|---|---|
| +0x00 | u32 write counter (not incremented while 0xFFFFFFFF) | – | same |
| +0x04 | gp−0x28F8 | 1 | same |
| +0x05 / +0x06 | gp−0x2943 / gp−0x2942 | 7 / 7 | same |
| **+0x07 / +0x08 / +0x09** | **Bass gp−0x2941 / Mid gp−0x2940 / Treble gp−0x293F** | 7 / 7 / 7 | same |
| +0x0A | gp−0x2992 (only if gp−0x2995 ≠ 2) | 3 | (4) |
| +0x0B..+0x0E | four levels gp−0x293D..−0x293A (each clamped by a limit byte gp−0x29B1.., 0x1E = no limit) | 6, 0x12, 0x0C, 0x0A | (7, 0x12, 0x0C, 0x0A) |
| +0x0F | **unused** | – | unused |
| **+0x10** | **unused** | – | **BeoSonic X** (raw 1..31) |
| **+0x11** | **unused** | – | **BeoSonic Y** |
| **+0x12** | **unused** | – | **BeoSonic state** (1 = off, 2 = on) |
| +0x13 | **unused** | – | unused |
| +0x14 | cksum(0x14) LE | – | same |
| (+0x18..+0x1F) | never written (EEPROM only) | – | same |

- "Unused" [V]: a listing scan finds no gp-relative or ep-relative (base gp−0x2974) access to gp−0x2965..−0x2961. The only whole-record operations are `vread` and memcpy, and they **preserve** these bytes:
  - `FUN_000937b4` loads all 0x18 bytes;
  - `FUN_0009382e` rewrites only +4..+0xE;
  - `FUN_00097ffc` changes only the listed fields, then the counter and cksum;
  - `FUN_0009836a` copies the whole RAM record into another slot.
- So bytes written there by a patch survive every stock write, including a later **rollback to the stock app**. That is safe: the stock app CRCs them but never interprets them.
- Slots: i = 0..3 are personal profiles (memory keys, selected by CAN via msg 0x20B5 when the config bit gp−0x297C.5 is set); **i = 4 = default/guest** [I semantics, V indexing]. The active slot is gp−0x294C, mirrored in header +0x0F (gp−0x2905).
- Validity (`FUN_000937b4`): stored cksum == cksum(0x14), or counter == cksum == 0xFFFFFFFF (blank). Otherwise the DTC-manager flag is set (`FUN_00090264(1)`) and +4..+0xE get defaults. Bytes +0x0F..+0x13 keep whatever was read.

### 4.3 When the audio records are read/written [V]
| Event | Code path | Effect |
|---|---|---|
| Wake/active (app msg 0x2084 = 1, at most every 3 s) | `FUN_0009660a` → `FUN_0009370e` | read header; bad cksum → header defaults + `FUN_00093580` (reset all slots) |
| Profile state machine (`FUN_00098804`, run from the 20 ms app timer) | state 1 waits up to 429 ms for a profile index from CAN (0x20B5), else uses header +0x0F. State 2 = **`FUN_000988ce`** | `FUN_00097ffc(3)` flush (if loaded), `FUN_000937b4(gp−0x2974, slot)` load, **`FUN_00093c8c`** apply to live values + DSP, then **gp−0x2947 = 1 ("audio NVM loaded")** |
| Any tone/level change | `FUN_00094502`, `9488a`, `94a98`, `95ccc`, `95680`, `98428`, `98ea0` → `FUN_00097ffc(1, now)` | arm: gp−0x7BE0 = 1, gp−0x7BE4 = now (re-armed each change, so it is a debounce) |
| App timer msg 0x1048 (period 20 ticks) | `FUN_000950a8` → `FUN_00097ffc(2, now)` | if armed and now − t_arm > 5000: compare record vs live, write slot (and header) **only if different** |
| Forced flush | `FUN_00097ffc(3, …)` from `FUN_000956d8` (gp−0x2995 → 2), `FUN_0009836a`, `FUN_000984ce`, `FUN_000988ce` | immediate compare + write |
| **Shutdown** | system task msg 0x1020 → `FUN_00095ace` → `FUN_00097ffc(3, now, 1)`; app msg 0x1045 (posted on sys msg 0x203B) → same | immediate flush |
| "Store to profile i" (msg 0x20B6) | `FUN_0009836a(i)` | flush, then copy RAM record → slot i |
| Audio reset (msg 0x20AF) | `FUN_00093580` | defaults into all 5 slots (keeps +0x0F..+0x13), slot 4 active |

`FUN_00097ffc` returns at once unless gp−0x2947 == 1, so nothing is written before the first load [V].

## 5. PJ6T (BeoSonic amp): how it persists BeoSonic [V]
The PJ6T code is the same code base with three extra record fields.

| Role | PJ6T | MB5T |
|---|---|---|
| vread / vwrite | `FUN_0008d7a0` / `FUN_0008d828` | `FUN_0008c362` / `FUN_0008c3ea` |
| cksum | `FUN_00093b18` | `FUN_00092698` |
| slot load (+defaults) | `FUN_00094ddc` (+`94e56`) | `FUN_000937b4` (+`9382e`) |
| header load | `FUN_00094d36` | `FUN_0009370e` |
| save logic | `FUN_00099c0a` | `FUN_00097ffc` |
| profile load / apply | `FUN_0009a51e` / **`FUN_0009538a`** | `FUN_000988ce` / **`FUN_00093c8c`** |
| store to profile / reset | `FUN_00099fba` / `FUN_00094ba4` | `FUN_0009836a` / `FUN_00093580` |
| RAM record / header / slot idx | gp−0x28B4 / gp−0x2854 / gp−0x2845 | gp−0x2974 / gp−0x2914 / gp−0x2905 |
| loaded flag / arm flag+time | gp−0x2887 / gp−0x7B84, −0x7B88 | gp−0x2947 / gp−0x7BE0, −0x7BE4 |

- **Record:** +0x10 = confirmed X (gp−0x5799), +0x11 = confirmed Y (−0x5798), +0x12 = state (−0x5797, 1 off / 2 on). They are part of the cksum(0x14). `FUN_00099c0a` compares them like the tone fields and copies them before the write.
- **When written:** the DSP acks 0x1090/0x1091 call `FUN_00099c0a(1, now)` (arm). It is written by the 20 ms timer ≥ 5 s after the last change, or at the forced flush points (shutdown, profile change). So it is **per profile slot**, debounced 5 s, written only when changed.
- **Load:** `FUN_0009538a` copies +0x10..+0x12 into the confirmed X/Y/state, then `FUN_0009af8c` (only after the config DE00 arrived):
  - state 2 → DSP enable + X/Y;
  - state 1 → stays off with X/Y "restore pending";
  - any other value → nothing.
- **Defaults** for a bad record (`FUN_00094e56`): X = Y = 0x10, state 2 (on) if DE00 byte1.4 is set, else 0/0/0.

## 6. Use by the BeoSonic firmware
The firmware stores X, Y, state and a check byte in audio slot bytes +0x10..+0x13 and lets the stock `FUN_00097ffc` write them (compare hook, stock debounce/flush). Details: `TECHNICAL.md` §4.3–4.5.
