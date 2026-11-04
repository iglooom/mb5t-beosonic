#!/usr/bin/env python3
"""Negative control for the test harnesses: mutate one cave instruction at a time and require that
test_caves.py, tone_emu.py or test_persist.py FAILS for every mutation.

  python3 tools/fault_inject.py
"""
import os, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import v850asm as A               # noqa: E402
import build as B                 # noqa: E402
import paths as P                 # noqa: E402

PBIN = P.OUT_BIN
lst = []
cur = None
for ln in open(P.CAVE_LISTING):
    if ln.endswith(':\n') and not ln.startswith(' '):
        cur = ln[:-2]
        continue
    m = re.match(r'\s+([0-9a-f]{6})\s+([0-9a-f]+)\s+(.*)$', ln)
    if m:
        lst.append((cur, int(m.group(1), 16), m.group(2), m.group(3).strip()))

# (description, label region, original text, mutated text)
MUTS = [
    ('import: state==2 test -> ==1', 'T_iset', 'addi -0x2,r15,r0', 'addi -0x1,r15,r0'),
    ('import: chk constant 0xb5 -> 0xb4', 'T_imp', 'movea 0xb5,r0,r18', 'movea 0xb4,r0,r18'),
    ('snap: chk byte position', 'S_st', 'shl 0x18,r16', 'shl 0x10,r16'),
    ('arm: flag stored as 0', 'T_norx', 'st.b r11,%#x[gp]' % B.G_ARM, 'st.b r0,%#x[gp]' % B.G_ARM),
    ('frame: CHG not set on XY accept', 'T_acc', 'st.b r11,%#x[gp]' % B.CHG, 'st.b r0,%#x[gp]' % B.CHG),
    ('EB: template ptr step 4 -> 2', 'EB_c1', 'addi 0x4,r19,r19', 'addi 0x2,r19,r19'),
    ('EB: restore copies 14 words', 'EB_rest', 'movea 0xf,r0,r17', 'movea 0xe,r0,r17'),
    ('EB: no Treble shadow invalidation', 'EB_inv', 'st.b r10,0x30[r6]', 'st.b r10,0x31[r6]'),
    ('EM: 100-c -> 99-c', 'EM', 'movea 0x64,r0,r10', 'movea 0x63,r0,r10'),
    ('CMPH: changed flag 0', 'CMPH', 'mov 0x1,r29', 'mov 0x0,r29'),
    ('LOADH: seq +2', 'LOADH', 'addi 0x1,r1,r1', 'addi 0x2,r1,r1'),
    ('LOADH: SNAP not invalidated', 'LOADH', 'st.w r0,%#x[gp]' % B.SNAPM, 'st.w r0,%#x[gp]' % B.SNAP),
    ('LEQ: clears wrong word', 'LEQ', 'st.w r0,%#x[gp]' % B.SWAPPED, 'st.w r0,%#x[gp]' % (B.SWAPPED + 4)),
]
orig = open(PBIN, 'rb').read()
bak = PBIN + '.bak'
shutil.copy(PBIN, bak)
ok = True
try:
    for desc, region, old, new in MUTS:
        hit = None
        inreg = False
        for lab, pc, b, t in lst:
            inreg = inreg or lab == region
            if inreg and t == old:
                hit = (pc, b)
                break
        if not hit:
            print('pattern not found:', desc, old); ok = False; continue
        pc, b = hit
        nb = A.assemble_one(new, pc)
        assert len(nb) == len(b) // 2, desc
        d = bytearray(orig)
        d[pc - 0x80000:pc - 0x80000 + len(nb)] = nb
        open(PBIN, 'wb').write(d)
        det = []
        for t in ('test_caves.py', 'tone_emu.py', 'test_persist.py'):
            r = subprocess.run([sys.executable, os.path.join(HERE, t)], capture_output=True, text=True,
                               timeout=600)
            if r.returncode != 0:
                det.append(t)
        print('%-40s -> %s' % (desc, ('DETECTED by ' + ', '.join(det)) if det else 'MISSED'))
        ok &= bool(det)
finally:
    shutil.copy(bak, PBIN)
    os.remove(bak)
assert open(PBIN, 'rb').read() == orig
print('fault injection', 'PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
