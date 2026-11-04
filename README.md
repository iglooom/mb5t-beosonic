# MB5T-14C589-CB BeoSonic

B&O **BeoSonic** for the Ford Explorer U625 Harman amplifier, application `MB5T-14C589-CB` (ECU 0x783). The patched application:
- answers the SYNC 3 BeoSonic page (0x105 → 0x22D) like the Kuga 3 / Focus 4 B&O amplifier;
- applies the Kuga 3's own BeoSonic filters (60 Hz shelf, 7.5 kHz shelf, ±2 dB level);
- remembers on/off and the pad position per audio profile.

**Status:** flashed and tested in the car (Kuga 2 with retrofitted Kuga 3 B&O speakers/ACM and this Explorer amplifier). It works.

## Flash
| File | sha256 |
|---|---|
| `release/MB5T-14C589-CB_BeoSonic.VBF` | `fb6a8c0e3b7bd4689a5b59c8b58b76a9b034ce9531f464e70ca061def4bcd7f4` |

- Flash it with UCDS like the stock 14C589 application.
- The SYNC APIM must have the B&O Tone Touch / BeoSonic HMI enabled.
- Rollback: flash `inputs/MB5T-14C589-CB.VBF`.

## Documents
| | |
|---|---|
| `docs/EXPLAINED.md` / `docs/EXPLAINED_RU.md` | what it does and how we got there, in plain language (English / Russian) |
| `docs/TECHNICAL.md` | self-contained reference: stock facts, hooks, caves, RAM/ROM layout, persistence, verification, limits |
| `docs/EEPROM.md` | the amplifier's EEPROM / data-flash map and checksum, and how the Kuga 3 amp stores BeoSonic |

## Layout
| Path | Content |
|---|---|
| `release/` | flashable VBF, flat image, build manifest, cave listing, `vbftool` patch arguments, Ghidra listing of the patched code |
| `tools/` | builder, assembler, emulators, checksum/EQ/NVM tools, all tests (Python 3, stdlib only), `vbftool.py`, Ghidra script |
| `inputs/` | OEM files needed to rebuild and verify: stock Explorer app and EQ table, Kuga 3 B&O app (pad tables) and EQ table (BeoSonic records), two more Harman apps for the checksum self-test |
| `analysis/` | Ghidra listing and decompile of the stock app, the inflated Explorer EQ table |

## Rebuild and verify
```
python3 tools/build.py              # release/*.bin, manifest, listings (reproducible: sha256 fb304cf1…405385f9)
python3 tools/build_vbf.py          # release/*.VBF (sha256 above)
python3 tools/mb5t_cksum.py selftest
python3 tools/v850asm.py selftest
python3 tools/eq_table.py selftest  # also writes analysis/LB5T-14C656-BH_0x10000.bin
python3 tools/nvm_tool.py selftest
python3 tools/test_caves.py
python3 tools/tone_emu.py
python3 tools/test_persist.py
python3 tools/fault_inject.py       # negative control, about 1 min
GHIDRA=/opt/ghidra sh tools/ghidra_verify.sh
```

## Note
`inputs/` and `analysis/` contain Ford/Harman firmware and derived listings, included so that the build and every check can be reproduced. They are provided for right-to-repair and interoperability on your own vehicle.
