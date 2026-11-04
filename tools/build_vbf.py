#!/usr/bin/env python3
"""Wrap the patched flat image into a VBF identical in header/format to the OEM file.

1. read out/vbf_patch_args.txt (clusters = hooks + caves + LUT + both internal checksum words,
   each with the OEM bytes asserted via --expect),
2. vbftool patch  (block CRC-16 + file CRC-32 recomputed),
3. restore the OEM lower-case spelling of the file_checksum digits (vbftool writes upper case;
   the header is not covered by any CRC, but UCDS should see the exact OEM header format),
4. assert: header identical to OEM except those 8 digits; block table identical; merged image ==
   out/MB5T-14C589-CB_beosonic.bin; internal checksums verify.
"""
import os, re, subprocess, sys, zlib, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mb5t_cksum as CK   # noqa: E402

import paths as P  # noqa: E402
T = os.path.join(HERE, 'vbftool.py')
OEM = P.STOCK_VBF
OUTV = P.OUT_VBF
PBIN = P.OUT_BIN

args = []
for ln in open(P.PATCH_ARGS):
    args += ln.split()
r = subprocess.run(['python3', T, 'patch', OEM, '-o', OUTV] + args, capture_output=True, text=True)
print(r.stdout[-1500:], r.stderr[-1500:])
assert r.returncode == 0, 'vbftool patch failed'

d = bytearray(open(OUTV, 'rb').read())
o = open(OEM, 'rb').read()
m = re.search(rb'file_checksum\s*=\s*0x([0-9A-Fa-f]+)', d[:0x400])
d[m.start(1):m.end(1)] = m.group(1).lower()
open(OUTV, 'wb').write(d)

# header identity (everything before the data, except the 8 checksum digits)
mo = re.search(rb'file_checksum\s*=\s*0x([0-9A-Fa-f]+)', o[:0x400])
assert m.start(1) == mo.start(1) and len(d) == len(o)
hd = o.find(b'}', o.find(b'header')) if False else None
diff_hdr = [i for i in range(0x2DB) if d[i] != o[i]]
assert all(mo.start(1) <= i < mo.end(1) for i in diff_hdr), 'header changed outside file_checksum'
# block container: start/len identical, data == patched image
assert d[0x2DB:0x2DB + 8] == o[0x2DB:0x2DB + 8]
data = bytes(d[0x2DB + 8:0x2DB + 8 + 0x80000])
assert data == open(PBIN, 'rb').read(), 'VBF payload != patched image'
fc = zlib.crc32(bytes(d[0x2DB:])) & 0xFFFFFFFF
assert int(m.group(1), 16) == fc
assert CK.app_word(data) == CK.rd(data, CK.APP_SUM_AT)
stock = open(CK.SELFTEST['MB5T-14C589-CB'], 'rb').read()
assert CK.all_word_delta(stock, data)[0] == CK.rd(data, CK.ALL_SUM_AT)
print('header bytes changed:', len(diff_hdr), '(file_checksum digits only)')
print('file_checksum 0x%08x' % fc)
print('sha256', hashlib.sha256(bytes(d)).hexdigest())
print(subprocess.run(['python3', T, 'verify', OUTV], capture_output=True, text=True).stdout)
print(subprocess.run(['python3', T, 'diff', OEM, OUTV], capture_output=True, text=True).stdout)
