#!/usr/bin/env python3
"""Harman Sapphire amp app (MB5T/LB5T/PJ6T/L1TT-14C589, V850E2 Fx4) internal checksums.

Firmware facts (MB5T-14C589-CB, read from code, see docs/MB5T_BEOSONIC_PATCH.md):
  FUN_00088e3a  = Adler-32 variant, "type 0": a,b reduced only when > 0xFFF1 (so 0xFFF1 can
                  survive unreduced); seed 1; processed in 0x400-byte chunks chained via seed.
  Region table (tp-0x6F9C.., ROM 0x820E4): [0,0xFFBFF] [0,0xFBFF] [0x10000,0x77BFF] [0x80000,0xFF7FF]
  Self-test words (u32 big-endian):
    0xFFEAC = Adler over [0x00000, 0xFFC00)  (whole flash incl. bootloader!)  -> delta-updated
    0x0FFF0 = Adler over [0x00000, 0x0FC00)  (bootloader, not in this VBF)
    0x77FFC = Adler over [0x10000, 0x77C00)  (not in this VBF)
    0xFFBFC = Adler over [0x80000, 0xFF800)  (application)  -> recomputed exactly
  The background monitor FUN_00099e42 walks regions in 0x400 chunks and on mismatch calls
  FUN_00092232 (sets fault flag gp-0x309C).

Usage:
  mb5t_cksum.py verify IMG.bin [IMG2.bin ...]   # flat app image(s) based at 0x80000
  mb5t_cksum.py selftest                         # reproduce the OEM words (Rule 0)
  mb5t_cksum.py fix OEM.bin NEW.bin -o OUT.bin   # write both app-region words into NEW
"""
import argparse, os, struct, sys

BASE = 0x80000
APP_SUM_AT, APP_LO, APP_HI = 0xFFBFC, 0x80000, 0xFF800
ALL_SUM_AT, ALL_LO, ALL_HI = 0xFFEAC, 0x00000, 0xFFC00
P = 65521


def adler_fw(data, seed=1, chunk=0x400):
    """Exact port of FUN_00088e3a with r9 (type) = 0, chunked like FUN_0009a072."""
    a, b = seed & 0xFFFF, seed >> 16
    for x in data:
        s = a + x
        if s > 0xFFFF:            # carry out of 16 bits ('bh' path): +0xF mod 2^16
            a = (s + 0xF) & 0xFFFF
        elif s > 0xFFF1:
            a = (s + 0xF) & 0xFFFF
        else:
            a = s
        s = b + a
        if s > 0xFFFF:
            b = (s + 0xF) & 0xFFFF
        elif s > 0xFFF1:
            b = (s + 0xF) & 0xFFFF
        else:
            b = s
    return (b << 16) | a          # chunking does not change the result (seed is chained)


def rd(img, addr):
    return struct.unpack('>I', img[addr - BASE:addr - BASE + 4])[0]


def app_word(img):
    return adler_fw(img[APP_LO - BASE:APP_HI - BASE])


def lin_ab(img, lo, hi):
    """Canonical mod-P contribution of bytes [lo,hi) to (sum a, sum b) of an Adler stream that
    ENDS at hi: a += sum(d), b += sum((hi-k)*d_k)."""
    d = img[lo - BASE:hi - BASE]
    n = len(d)
    sa = sum(d) % P
    sb = 0
    for k, x in enumerate(d):
        sb += (n - k) * x
    return sa, sb % P


def solve_prefix(img):
    """Adler state (a0,b0) after the unknown prefix [0,0x80000) implied by the stored 0xFFEAC."""
    st = rd(img, ALL_SUM_AT)
    A, B = st & 0xFFFF, st >> 16
    L = ALL_HI - BASE
    sa, sb = lin_ab(img, BASE, ALL_HI)
    a0 = (A - sa) % P
    b0 = (B - sb - L * a0) % P
    return a0, b0


def all_word_delta(old, new):
    """New 0xFFEAC from the OEM word + delta of the changed bytes (prefix-independent)."""
    st = rd(old, ALL_SUM_AT)
    A, B = st & 0xFFFF, st >> 16
    da = db = 0
    for off in range(ALL_HI - BASE):
        x, y = old[off], new[off]
        if x != y:
            dd = y - x
            da += dd
            db += (ALL_HI - BASE - off) * dd
    A2, B2 = (A + da) % P, (B + db) % P
    return (B2 << 16) | A2, (A2 == 0 or B2 == 0)


def fix(old, new):
    new = bytearray(new)
    # order: app word (0xFFBFC) is INSIDE the whole-flash range, so write it first
    w = app_word(new)
    new[APP_SUM_AT - BASE:APP_SUM_AT - BASE + 4] = struct.pack('>I', w)
    w2, amb = all_word_delta(old, new)
    if amb:
        raise SystemExit('ambiguous canonical residue (0 vs 0xFFF1); change a filler byte')
    new[ALL_SUM_AT - BASE:ALL_SUM_AT - BASE + 4] = struct.pack('>I', w2)
    return bytes(new), w, w2


def verify(img, ref=None):
    ok = app_word(img) == rd(img, APP_SUM_AT)
    print('  0xFFBFC app Adler [0x80000,0xFF800): stored %08X computed %08X %s' %
          (rd(img, APP_SUM_AT), app_word(img), 'OK' if ok else 'MISMATCH'))
    if ref is not None:
        p1, p2 = solve_prefix(ref), solve_prefix(img)
        ok2 = p1 == p2
        print('  0xFFEAC whole-flash Adler: implied prefix state %s vs ref %s %s' %
              (p2, p1, 'CONSISTENT' if ok2 else 'INCONSISTENT'))
        ok &= ok2
    return ok


_IN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'inputs')
SELFTEST = {
    'MB5T-14C589-CB': os.path.join(_IN, 'MB5T-14C589-CB_0x80000.bin'),
    'LB5T-14C589-AG': os.path.join(_IN, 'LB5T-14C589-AG_0x80000.bin'),
    'PJ6T-14C589-AD': os.path.join(_IN, 'PJ6T-14C589-AD_0x80000.bin'),
    'L1TT-14C589-AG': os.path.join(_IN, 'L1TT-14C589-AG_0x80000.bin'),
}


def selftest():
    imgs = {k: open(v, 'rb').read() for k, v in SELFTEST.items()}
    good = True
    for k, im in imgs.items():
        print(k)
        good &= verify(im)
        print('  implied prefix state for 0xFFEAC:', solve_prefix(im))
    # cross-version: delta update from one OEM must reproduce the other OEM's stored word
    # (PJ6T/L1TT are different products with different implied prefixes -> not a valid pair;
    #  their 0xFFBFC words are still checked above.)
    for a, b in (('LB5T-14C589-AG', 'MB5T-14C589-CB'), ('MB5T-14C589-CB', 'LB5T-14C589-AG')):
        w, _ = all_word_delta(imgs[a], imgs[b])
        ok = w == rd(imgs[b], ALL_SUM_AT)
        good &= ok
        print('delta %s -> %s: 0xFFEAC predicted %08X stored %08X %s' %
              (a, b, w, rd(imgs[b], ALL_SUM_AT), 'OK' if ok else 'FAIL'))
        out, w1, w2 = fix(imgs[a], imgs[b])
        ok = out == imgs[b]
        good &= ok
        print('fix(%s, %s) reproduces %s byte-exact: %s' % (a, b, b, ok))
    print('SELFTEST', 'PASS' if good else 'FAIL')
    return good


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    v = sp.add_parser('verify'); v.add_argument('imgs', nargs='+'); v.add_argument('--ref')
    sp.add_parser('selftest')
    f = sp.add_parser('fix'); f.add_argument('old'); f.add_argument('new'); f.add_argument('-o', required=True)
    a = ap.parse_args()
    if a.cmd == 'selftest':
        sys.exit(0 if selftest() else 1)
    if a.cmd == 'verify':
        ref = open(a.ref, 'rb').read() if a.ref else None
        ok = True
        for p in a.imgs:
            print(p); ok &= verify(open(p, 'rb').read(), ref)
        sys.exit(0 if ok else 1)
    if a.cmd == 'fix':
        out, w1, w2 = fix(open(a.old, 'rb').read(), open(a.new, 'rb').read())
        open(a.o, 'wb').write(out)
        print('0xFFBFC=%08X 0xFFEAC=%08X -> %s' % (w1, w2, a.o))


if __name__ == '__main__':
    main()
