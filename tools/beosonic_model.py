#!/usr/bin/env python3
"""Re-implementation of the Harman 'Sapphire 6ch' BeoSonic X/Y -> tone mapping,
driven by the bytes of the actual firmware/EQ images (no hand-copied constants).

Firmware refs (PJ6T-14C589-AD, base 0x80000, tp = 0x89080):
  FUN_0008cdb8  XY -> (u,v) via T1/T2, then weights via T3/T4
  FUN_0008d90c  piecewise-linear weight -> gain (Q.11, 0x800 = 0 dB)
  0x61 records  in the 14C656 EQ image (global TLV block) = filter definitions

usage: beosonic_model.py [X Y]        (X,Y = HMI coordinates 0..30)
"""
import math, struct, sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths as P  # noqa: E402
APP = P.KUGA_APP
EQ = os.environ.get("EQ", P.KUGA_EQ)
TP = 0x89080
OFF_T1, OFF_T2, OFF_T3, OFF_T4 = -0x7598, -0x71d4, -0x6e2e, -0x6a6a

app = open(APP, 'rb').read()
eq = open(EQ, 'rb').read()


def b(addr):
    return app[addr - 0x80000]


def xy_to_weights(xr, yr):
    """xr, yr = raw CAN values (X+1, Y+1), exactly like FUN_0008cdb8."""
    if not (1 <= xr < 32 and 1 <= yr < 32):
        return None
    i = TP + xr - 31 * yr                    # mulhi 0x1f,Y ; subr X ; add tp
    u, v = b(i + OFF_T1), b(i + OFF_T2)
    if u >= 31 or v >= 31:                   # outside the circular pad -> rejected (-1)
        return None
    j = TP + v - 31 * u
    c = b(j + OFF_T3)
    d = b(j + OFF_T4)
    return dict(u=u, v=v, w_0x10000=c, w_0x8000=d, w_0x4000=100 - c)


def s16(x):
    return x - 0x10000 if x & 0x8000 else x


def c2a6(p, v):
    hi = s16(p >> 16)
    return (hi * v + (((p & 0xffff) * v + 0x8000) >> 16)) * 2


def records():
    """Global-block tag 0x61 records (filter definitions), big-endian payload."""
    out, o = [], 8
    end = struct.unpack_from('<I', eq, 0)[0]
    while o + 8 <= end:
        t, l = struct.unpack_from('<II', eq, o)
        if t == 0x61 and l == 0x38:
            r = eq[o + 8:o + 8 + 0x38]
            mask, typ, _, idx, k = struct.unpack_from('>5I', r, 0)
            q = struct.unpack_from('>H', r, 0x14)[0]
            sl = struct.unpack_from('>4I', r, 0x16)
            ys = [s16(x) for x in struct.unpack_from('>4H', r, 0x26)]
            bp = [s16(x) for x in struct.unpack_from('>5H', r, 0x2e)]
            f = math.atan(k / 2**31) * 48000 / math.pi
            out.append(dict(off=0x10000 + o, mask=mask, type=typ, idx=idx, f=f,
                            q=q / 16384, sl=sl, ys=ys, bp=bp))
        o += 8 + l
    return out


def gain_q11(rec, v):
    sl, ys, bp = rec['sl'], rec['ys'], rec['bp']
    for i in range(4):
        if bp[i] <= v <= bp[i + 1]:
            return (ys[i] + (c2a6(sl[i], v) >> 3)) * 2
    if v < bp[0]:
        return (ys[0] + (c2a6(sl[0], bp[0]) >> 3)) * 2
    return (ys[3] + (c2a6(sl[3], bp[4]) >> 3)) * 2


def db(g):
    return 20 * math.log10(g / 2048.0) if g > 0 else float('-inf')


TYPES = {1: 'peak', 2: 'lowshelf', 4: 'highshelf', 8: 'gain'}


def main():
    recs = {r['mask']: r for r in records()}
    if len(sys.argv) == 3:
        pts = [('cmd', int(sys.argv[1]), int(sys.argv[2]))]
    else:
        pts = [('Neutral', 15, 15), ('Lounge', 15, 4), ('Party', 27, 19), ('Podcast', 5, 23),
               ('Relaxed', 0, 15), ('Energetic', 30, 15), ('Warm', 15, 0), ('Bright', 15, 30)]
        print('filter records (EQ global block):')
        for r in records():
            print('  @%05x idx %d mask %05x %-9s f=%7.1f Hz q=%.3f gain(bp %s)' % (
                r['off'], r['idx'], r['mask'], TYPES.get(r['type'], r['type']), r['f'], r['q'], r['bp']))
    for name, x, y in pts:
        w = xy_to_weights(x + 1, y + 1)
        if w is None:
            print('%-9s X=%2d Y=%2d -> rejected (outside pad)' % (name, x, y))
            continue
        ls = db(gain_q11(recs[0x10000], w['w_0x10000']))
        hs = db(gain_q11(recs[0x8000], w['w_0x8000']))
        g = db(gain_q11(recs[0x4000], w['w_0x4000']))
        print('%-9s X=%2d Y=%2d u=%2d v=%2d w=(%3d,%3d,%3d)  LS100 %+5.1f dB  HS7k %+5.1f dB  level %+4.1f dB' % (
            name, x, y, w['u'], w['v'], w['w_0x10000'], w['w_0x8000'], w['w_0x4000'], ls, hs, g))


if __name__ == '__main__':
    main()
