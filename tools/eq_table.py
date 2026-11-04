#!/usr/bin/env python3
"""LB5T-14C656-BH (Explorer U625 amp EQ table): inflate, verify, list tone records.

The VBF payload (0x2ED4 B "@0xFE002800") is a plain zlib stream (CINFO 7, Adler-32 trailer).
It inflates to 0x70000 B = the amp EQ area 0x10000..0x7FFFF, same layout as LJ6T/JX7T
(TLV preset blocks, stride 0x2000 here). Decompression is done by the amp's bootloader
during download (no inflate code in the MB5T app or in the 14C591 SBL).

usage:
  eq_table.py inflate [VBF|blob] [out.bin]   write the flat 0x10000 image
  eq_table.py verify  [flat.bin]             block csums + 0x77FFC Adler word
  eq_table.py tone    [flat.bin]             tag-0x61 records per preset, dB per value
  eq_table.py selftest
"""
import math, os, struct, sys, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths as P                         # noqa: E402
VBF = P.EXPLORER_EQ_VBF
FLAT = P.EXPLORER_EQ_FLAT
LOAD = 0x10000
EQ_SUM_AT, EQ_LO, EQ_HI = 0x77FFC, 0x10000, 0x77C00   # region table entry {0x10000, 0x77BFF}
STRIDE = 0x2000
TYPES = {1: 'peak', 2: 'lowshelf', 4: 'highshelf', 8: 'gain'}
MASKS = {1: 'Bass', 2: 'Mid', 4: 'Treble', 8: 'volume-dep.', 0x10: 'mask 0x10',
         0x4000: 'BeoSonic level', 0x8000: 'BeoSonic HS', 0x10000: 'BeoSonic LS'}

sys.path.insert(0, HERE)
from mb5t_cksum import adler_fw          # exact port of FUN_00088e3a type 0


def vbf_blob(path):
    d = open(path, 'rb').read()
    if not d.startswith(b'vbf_version'):
        return d
    i, depth = d.index(b'header'), 0
    while True:                            # skip the brace-balanced header
        c = d[i:i + 1]
        if c == b'{':
            depth += 1
        elif c == b'}':
            depth -= 1
            if depth == 0:
                break
        i += 1
    i += 1
    addr, n = struct.unpack_from('>II', d, i)
    return d[i + 8:i + 8 + n]


def inflate(blob):
    o = zlib.decompressobj()
    r = o.decompress(blob)
    assert o.eof and not o.unused_data, 'trailing data after zlib stream'
    assert len(r) == 0x70000, hex(len(r))
    return r


def blocks(img):
    for base in range(0, 0x77A00 - LOAD, STRIDE):      # presets end before the tail region
        size = struct.unpack_from('<I', img, base)[0]
        if not 8 <= size < STRIDE:
            continue
        csum = struct.unpack_from('<I', img, base + 4)[0]
        n = (size - 4) // 4
        s = sum(struct.unpack_from('<%dI' % n, img, base + 8)) & 0xFFFFFFFF
        yield base, size, csum == (-s) & 0xFFFFFFFF


def tlvs(img, base, size):
    o, end = base + 8, base + 4 + size
    while o + 8 <= end:
        t, l = struct.unpack_from('<II', img, o)
        yield t, img[o + 8:o + 8 + l]
        o += 8 + ((l + 3) & ~3)


def s16(x):
    return x - 0x10000 if x & 0x8000 else x


def rec61(r):
    mask, typ, _, idx, k = struct.unpack_from('>5I', r, 0)
    return dict(mask=mask, type=typ, idx=idx, f=math.atan(k / 2 ** 31) * 48000 / math.pi,
                q=struct.unpack_from('>H', r, 0x14)[0] / 16384,
                sl=struct.unpack_from('>4I', r, 0x16),
                ys=[s16(x) for x in struct.unpack_from('>4H', r, 0x26)],
                bp=[s16(x) for x in struct.unpack_from('>5H', r, 0x2e)])


def c2a6(p, v):
    hi = s16(p >> 16)
    return (hi * v + (((p & 0xffff) * v + 0x8000) >> 16)) * 2


def gain_db(rec, v):
    """Piecewise-linear value -> Q.11 gain, as FUN_0008c4ce (MB5T) / FUN_0008d90c (PJ6T)."""
    sl, ys, bp = rec['sl'], rec['ys'], rec['bp']
    for i in range(4):
        if bp[i] <= v <= bp[i + 1]:
            g = (ys[i] + (c2a6(sl[i], v) >> 3)) * 2
            break
    else:
        g = (ys[0] + (c2a6(sl[0], bp[0]) >> 3)) * 2 if v < bp[0] else (ys[3] + (c2a6(sl[3], bp[4]) >> 3)) * 2
    return 20 * math.log10(g / 2048.0) if g > 0 else float('-inf')


def name_of(img, base, size):
    for t, v in tlvs(img, base, size):
        if t == 0x27:
            return v.split(b'\0')[0].decode('latin1')
    return '?'


def cmd_verify(img):
    ok = True
    for base, size, good in blocks(img):
        print('@%06x size %5d csum %s  %s' % (LOAD + base, size, 'OK ' if good else 'BAD', name_of(img, base, size)))
        ok &= good
    stored = struct.unpack_from('>I', img, EQ_SUM_AT - LOAD)[0]
    calc = adler_fw(img[EQ_LO - LOAD:EQ_HI - LOAD])
    print('0x77FFC stored %08X calc %08X %s' % (stored, calc, 'OK' if stored == calc else 'BAD'))
    return ok and stored == calc


def cmd_tone(img):
    for base, size, _ in blocks(img):
        print('%s' % name_of(img, base, size))
        pos = 0
        for t, v in tlvs(img, base, size):
            if t != 0x61:
                continue
            r = rec61(v)
            hi = max(r['bp'])
            pts = sorted(set([r['bp'][0], (r['bp'][0] + hi) // 2, hi]))
            print('  pos %d idx %d %-14s %-9s %7.1f Hz Q %.3f  value %s -> %s dB' % (
                pos, r['idx'], MASKS.get(r['mask'], hex(r['mask'])), TYPES.get(r['type'], r['type']),
                r['f'], r['q'], pts, ['%+.2f' % gain_db(r, p) for p in pts]))
            pos += 1


def selftest():
    blob = vbf_blob(VBF)
    assert len(blob) == 0x2ED4 and blob[:2] == b'\x68\x81', 'unexpected VBF payload'
    img = inflate(blob)
    assert cmd_verify(img)
    os.makedirs(os.path.dirname(FLAT), exist_ok=True)
    open(FLAT, 'wb').write(img)            # flat image used by tone_emu.py
    # the explorer tone controls the BeoSonic patch drives (presets 2..17, i.e. blocks 1..16)
    for base, size, _ in blocks(img):
        nm = name_of(img, base, size)
        if not nm[0].isdigit() or nm.startswith('1_'):
            continue
        recs = {rec61(v)['mask']: rec61(v) for t, v in tlvs(img, base, size) if t == 0x61}
        for m, f, typ in ((1, 60, 1), (2, 1000, 1), (4, 6000, 4)):
            r = recs[m]
            assert r['type'] == typ and abs(r['f'] - f) < 0.5, (nm, m, r)
            assert abs(gain_db(r, 7)) < 0.02 and abs(gain_db(r, 14) - 7) < 0.02 and abs(gain_db(r, 0) + 7) < 0.02
    print('selftest PASS')


def main():
    a = sys.argv[1:]
    if not a or a[0] == 'selftest':
        return selftest()
    if a[0] == 'inflate':
        img = inflate(vbf_blob(a[1] if len(a) > 1 else VBF))
        out = a[2] if len(a) > 2 else FLAT
        open(out, 'wb').write(img)
        print('wrote', out, hex(len(img)))
        return
    img = open(a[1] if len(a) > 1 else FLAT, 'rb').read()
    {'verify': cmd_verify, 'tone': cmd_tone}[a[0]](img)


if __name__ == '__main__':
    main()
