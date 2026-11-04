#!/bin/sh
# Independent decode check: import the built image raw into Ghidra (V850 SLEIGH), force-disassemble
# the hook sites and the cave area, then compare with the builder's listing (tools/verify_dis.py).
#   GHIDRA=/opt/ghidra sh tools/ghidra_verify.sh
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
GHIDRA=${GHIDRA:-/opt/ghidra}
W="$ROOT/ghidra_tmp"           # no dot-prefixed path elements: Ghidra rejects them
rm -rf "$W"; mkdir -p "$W"
H="$GHIDRA/support/analyzeHeadless"
"$H" "$W" proj -import "$ROOT/release/MB5T-14C589-CB_BeoSonic.bin" -processor V850:LE:32:default \
    -loader BinaryLoader -loader-baseAddr 0x80000 -noanalysis > "$W/import.log" 2>&1
OUT="$ROOT/release/ghidra_patch_dis.txt" \
RANGES=0x8b92a-0x8b98e,0x98130-0x9813c,0x988f8-0x98904,0x9cbb2-0x9cbbe,0xa72f0-0xa7300,0xa7396-0xa73a4,0xab846-0xab852,0xc1000-0xc1640 \
"$H" "$W" proj -process MB5T-14C589-CB_BeoSonic.bin -noanalysis -scriptPath "$ROOT/tools/ghidra" \
    -postScript DisAt.java > "$W/dis.log" 2>&1
rm -rf "$W"
python3 "$ROOT/tools/verify_dis.py"
