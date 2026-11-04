#!/usr/bin/env python3
"""Run the STOCK MB5T tone engine (FUN_0008b92a / b874 / c4ce / designers) on emulated RAM, with
the BeoSonic caves in place, and check the designed biquads acoustically.

Stock instructions are executed from Ghidra's SLEIGH listing TEXT (analysis/MB5T-14C589-CB.lst), so the
decoding of the stock code is independent of our assembler/decoder. Patched or cave bytes (no
listing entry, or bytes differ) go through v850emu's byte decoder.

Checks
  1. coefficient layout: the stock designers on Explorer's own records give the documented
     curves (Bass 60 Hz peak +-7 dB, Treble 6 kHz shelf) -> fixes word order/scaling;
  2. swap: patched FUN_0008b92a with BeoSonic on gives Kuga LS 60 Hz / HS 7.5 kHz / +-2 dB
     for the pad points, BeoSonic off restores the stock coefficients bit-exactly, preset
     reload (LEQ) + on re-swaps, masks 8/0x10 stages untouched.

  python3 tools/tone_emu.py      (exit 1 on failure)
"""
import cmath, itertools, math, os, re, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import v850emu as E                 # noqa: E402
import build as B                   # noqa: E402
import paths as P                   # noqa: E402
import eq_table as EQ               # noqa: E402

M32 = 0xFFFFFFFF
REG = {('r%d' % i): i for i in range(32)}
REG.update(sp=3, gp=4, tp=5, ep=30, lp=31)
COND = {'bv': 0, 'bc': 1, 'bl': 1, 'be': 2, 'bz': 2, 'bnh': 3, 'bn': 4, 'br': 5, 'blt': 6, 'ble': 7,
        'bnv': 8, 'bnc': 9, 'bnl': 9, 'bne': 10, 'bnz': 10, 'bh': 11, 'bp': 12, 'bsa': 13,
        'bge': 14, 'bgt': 15}

LISTING = {}
for ln in open(P.LISTING):
    p = ln.rstrip('\n').split('\t')
    if len(p) >= 3:
        LISTING[int(p[0], 16)] = (p[1], p[2].strip())

PIMG = open(os.environ.get('BEO_IMG', P.OUT_BIN), 'rb').read()
SIMG = open(B.STOCK, 'rb').read()
fails = 0


def check(c, msg):
    global fails
    if not c:
        fails += 1
        if fails < 30:
            print('FAIL', msg)


def sx(v, b):
    return E.sx(v, b)


class CPU(E.CPU):
    def __init__(self, rom):
        super().__init__(rom)
        self.tcache = {}

    def text_ok(self, pc):
        e = LISTING.get(pc)
        if not e:
            return None
        b = bytes.fromhex(e[0])
        o = pc - self.rb
        return e[1] if self.rom[o:o + len(b)] == b else None

    def step(self):
        pc = self.pc
        if pc in self.land:
            raise StopIteration(pc)
        if pc in self.stubs:
            self.stubs[pc](self)
            self.pc = self.r[31]
            return
        t = self.text_ok(pc)
        if t is None:
            return super().step()
        self.steps += 1
        if self.steps > 2000000:
            raise E.Halt('step limit')
        self.exec_text(pc, t, len(LISTING[pc][0]) // 2)

    # ------------------------------------------------------------ text semantics
    def val(self, a):
        return self.r[REG[a]] if a in REG else int(a, 0) & M32

    def mem(self, a):
        m = re.match(r'(-?0x[0-9a-f]+|-?\d+)?\[(\w+)\]', a)
        return (self.r[REG[m.group(2)]] + int(m.group(1) or '0', 0)) & M32

    def logic(self, v):
        self.OV = 0
        return self.setzs(v)

    def exec_text(self, pc, t, n):
        R = self.r
        op, _, rest = t.partition(' ')
        a = [x.strip() for x in rest.split(',')] if rest else []
        nxt = pc + n
        S = self.setr
        if op in COND:
            tgt = int(a[0], 0)
            self.pc = tgt if self.cond(COND[op]) else nxt
            return
        if op == 'jarl':
            S(REG[a[1]], nxt); self.pc = int(a[0], 0); return
        if op == 'jr':
            self.pc = int(a[0], 0); return
        if op == 'jmp':
            self.pc = R[REG[a[0].strip('[]')]]; return
        if op == 'mov':
            S(REG[a[1]], self.val(a[0]))
        elif op == 'movea':
            S(REG[a[2]], R[REG[a[1]]] + sx(int(a[0], 0), 16))
        elif op == 'movhi':
            S(REG[a[2]], R[REG[a[1]]] + (int(a[0], 0) << 16))
        elif op == 'add':
            v = self.val(a[0]) if a[0] in REG else sx(int(a[0], 0), 5) & M32
            S(REG[a[1]], self.addf(R[REG[a[1]]], v))
        elif op == 'addi':
            S(REG[a[2]], self.addf(R[REG[a[1]]], sx(int(a[0], 0), 16) & M32))
        elif op == 'sub':
            S(REG[a[1]], self.subf(R[REG[a[1]]], R[REG[a[0]]]))
        elif op == 'subr':
            S(REG[a[1]], self.subf(R[REG[a[0]]], R[REG[a[1]]]))
        elif op == 'cmp':
            v = self.val(a[0]) if a[0] in REG else sx(int(a[0], 0), 5) & M32
            self.subf(R[REG[a[1]]], v)
        elif op in ('shl', 'shr', 'sar'):
            if len(a) == 3:
                sh, src, dst = R[REG[a[0]]] & 31, R[REG[a[1]]], REG[a[2]]
            else:
                sh = (R[REG[a[0]]] & 31) if a[0] in REG else int(a[0], 0)
                src, dst = R[REG[a[1]]], REG[a[1]]
            if op == 'shl':
                res = (src << sh) & M32
                self.CY = (src >> (32 - sh)) & 1 if sh else 0
            elif op == 'shr':
                res = src >> sh
                self.CY = (src >> (sh - 1)) & 1 if sh else 0
            else:
                res = (sx(src, 32) >> sh) & M32
                self.CY = (src >> (sh - 1)) & 1 if sh else 0
            self.OV = 0
            S(dst, self.setzs(res))
        elif op in ('and', 'or', 'xor'):
            f = {'and': lambda x, y: x & y, 'or': lambda x, y: x | y, 'xor': lambda x, y: x ^ y}[op]
            S(REG[a[1]], self.logic(f(R[REG[a[1]]], R[REG[a[0]]])))
        elif op in ('andi', 'ori', 'xori'):
            f = {'andi': lambda x, y: x & y, 'ori': lambda x, y: x | y, 'xori': lambda x, y: x ^ y}[op]
            S(REG[a[2]], self.logic(f(R[REG[a[1]]], int(a[0], 0) & 0xFFFF)))
        elif op == 'not':
            S(REG[a[1]], self.logic(~R[REG[a[0]]] & M32))
        elif op == 'tst':
            self.logic(R[REG[a[1]]] & R[REG[a[0]]])
        elif op == 'zxb':
            S(REG[a[0]], R[REG[a[0]]] & 0xFF)
        elif op == 'zxh':
            S(REG[a[0]], R[REG[a[0]]] & 0xFFFF)
        elif op == 'sxb':
            S(REG[a[0]], sx(R[REG[a[0]]], 8))
        elif op == 'sxh':
            S(REG[a[0]], sx(R[REG[a[0]]], 16))
        elif op in ('ld.b', 'ld.bu', 'ld.h', 'ld.hu', 'ld.w', 'sld.b', 'sld.bu', 'sld.h', 'sld.hu', 'sld.w'):
            k = op.split('.')[1]
            sz = {'b': 1, 'bu': 1, 'h': 2, 'hu': 2, 'w': 4}[k]
            v = self.rd(self.mem(a[0]), sz)
            if k in ('b', 'h'):
                v = sx(v, 8 * sz)
            S(REG[a[1]], v)
        elif op in ('st.b', 'st.h', 'st.w', 'sst.b', 'sst.h', 'sst.w'):
            sz = {'b': 1, 'h': 2, 'w': 4}[op.split('.')[1]]
            self.wr(self.mem(a[1]), sz, R[REG[a[0]]])
        elif op in ('mul', 'mulu'):
            x = self.val(a[0]) if a[0] in REG else int(a[0], 0)
            y = R[REG[a[1]]]
            if op == 'mul':
                x = sx(x, 9) if a[0] not in REG else sx(x, 32)
                p = sx(y, 32) * x
            else:
                p = y * (x & (M32 if a[0] in REG else 0x1FF))
            p &= (1 << 64) - 1
            S(REG[a[1]], p & M32)
            if a[2] != 'r0' and REG[a[2]] != REG[a[1]]:
                S(REG[a[2]], p >> 32)
            elif REG[a[2]] == REG[a[1]] and a[2] != 'r0':
                S(REG[a[2]], p >> 32)
        elif op == 'mulh':
            x = sx(self.val(a[0]), 16) if a[0] in REG else sx(int(a[0], 0), 5)
            S(REG[a[1]], (sx(R[REG[a[1]]], 16) * x) & M32)
        elif op == 'mulhi':
            S(REG[a[2]], (sx(R[REG[a[1]]], 16) * int(a[0], 0)) & M32)
        elif op in ('mac', 'macu'):
            r1, r2 = R[REG[a[0]]], R[REG[a[1]]]
            p3 = int(re.findall(r'r(\d+)', a[2])[0]); p4 = int(re.findall(r'r(\d+)', a[3])[0])
            acc = R[p3] | (R[p3 + 1] << 32)
            if op == 'mac':
                acc = sx(acc, 64) + sx(r1, 32) * sx(r2, 32)
            else:
                acc = acc + r1 * r2
            acc &= (1 << 64) - 1
            S(p4, acc & M32); S(p4 + 1, acc >> 32)
        elif op == 'divqu':
            d, v = R[REG[a[0]]], R[REG[a[1]]]
            if d == 0:
                self.OV = 1
            else:
                q, rm = v // d, v % d
                self.OV = 0
                S(REG[a[1]], self.setzs(q))
                if a[2] != 'r0':
                    S(REG[a[2]], rm)
        elif op == 'nop':
            pass
        else:
            raise E.Halt('text op %r at %x' % (t, pc))
        self.pc = nxt


# ---------------------------------------------------------------- scaffolding
GP = B.GP
SP0 = 0xFEDFA000
CTX = GP - 0x934                      # tone ctx (FUN_0009c8e0 / DSP task)
ARR = GP + 0x4D8                      # record array (set by the tone-ctx init: ctx+4 = gp+0x4D8)
COEF = 0xFEDF0400                     # coefficient buffer (*(ctx+0x10)) - test location


def lb5t_records(preset='2-DA-PP-U625-12Spk-SurrOff-Approved.EQF'):
    img = open(EQ.FLAT, 'rb').read()
    for base, size, _ in EQ.blocks(img):
        if EQ.name_of(img, base, size) == preset:
            return [bytes(v[:0x38]) for t, v in EQ.tlvs(img, base, size) if t == 0x61]
    raise SystemExit('preset?')


DESC = 0xFEDF0800                     # fake preset descriptor (FUN_000896fc layout)
VA0 = 0x03010000                      # fake flash-object addresses of the raw records


def load_stock(c, raws):
    """Run the STOCK loader FUN_0008b27e(ctx, desc) with FUN_0008c362 (vread) stubbed."""
    w = c.wr
    w(DESC + 0x34, 2, len(raws))
    src = {}
    for i, r in enumerate(raws):
        w(DESC + 0x36 + 2 * i, 2, 0x38)
        w(DESC + 0x44 + 4 * i, 4, VA0 + 0x100 * i)
        for k, b in enumerate(r):
            src[VA0 + 0x100 * i + k] = b

    def vread(cpu):
        dst, sa, n = cpu.r[6], cpu.r[7], cpu.r[8]
        for k in range(n):
            b = src[sa + k] if VA0 <= sa < VA0 + 0x10000 else cpu.rd(sa + k, 1)
            cpu.wr(dst + k, 1, b)
        cpu.r[10] = 0
    c.stubs = {0x8C362: vread}
    c.r[6], c.r[7], c.r[31] = CTX, DESC, 0xDEAD4
    c.land = {0xDEAD4}
    c.run(0x8B27E)
    c.stubs = {}
    return c.r[10]


def setup(img, raws, vals=(7, 7, 7, 15, 0, 0), recs_native=None):
    c = CPU(img)
    c.code_lo = None
    c.r[3], c.r[4], c.r[5] = SP0, GP, 0x89080
    for i in range(20, 30):
        c.r[i] = 0x1000 + i
    w = lambda a, n, v: c.wr(a, n, v)
    w(CTX + 0, 2, 7)
    w(CTX + 4, 4, ARR)
    w(CTX + 0x10, 4, COEF)
    rc = load_stock(c, raws)
    assert rc == 0, 'stock loader returned %x' % rc
    if recs_native is not None:                      # override RAM records (negative tests)
        for i, r in enumerate(recs_native):
            for k in range(0x3C):
                c.ram[ARR + 0x3C * i + k] = r[k]
    b, m, t, v8, v10, v20 = vals
    for off, v in ((8, b), (9, m), (10, t), (0xB, v8), (0xC, v10)):
        w(CTX + off, 1, v)
    w(CTX + 0xE, 2, v20)
    for off in (0x2E, 0x2F, 0x30, 0x31, 0x32):
        w(CTX + off, 1, 0x7E)
    w(CTX + 0x34, 2, 0x7E7E)
    return c


def run_b92a(c, force=0):
    c.r[6], c.r[7], c.r[31] = CTX, force, 0xDEAD0
    c.land = {0xDEAD0}
    c.run(0x8B92A)


def coefs(c, pos):
    return [sx(c.rd(COEF + 0x14 * pos + 4 * i, 4), 32) for i in range(5)]


# coefficient word order: found from Explorer's records (check 1), then used everywhere
ORDER = None


def resp(cw, f, order=None):
    o = order or ORDER
    b0, b1, b2, a1, a2 = [cw[i] / 2.0 ** o[5] * (o[6][i]) for i in o[:5]]
    z = cmath.exp(-2j * math.pi * f / 48000)
    h = (b0 + b1 * z + b2 * z * z) / (1 - a1 * z - a2 * z * z)
    return 20 * math.log10(abs(h))


def find_order(cands):
    """cands: list of (coefs, {freq: expected_dB}). Pick the (perm, scale, sign) that fits all."""
    best = None
    for perm in itertools.permutations(range(5)):
        for sc in (23, 24, 22, 28):
            for sg in itertools.product((1, -1), repeat=2):
                sign = (1, 1, 1, sg[0], sg[1])
                o = list(perm) + [sc, sign]
                err = 0
                try:
                    for cw, exp in cands:
                        for f, db in exp.items():
                            err = max(err, abs(resp(cw, f, o) - db))
                except (ValueError, ZeroDivisionError):
                    continue
                if best is None or err < best[0]:
                    best = (err, o)
    return best


def main():
    global ORDER
    raw = lb5t_records()
    nat = [B.native(r, EQ.rec61(r)['mask']) for r in raw]
    masks = [EQ.rec61(r)['mask'] for r in raw]
    print('Explorer preset 2 record masks by position:', [hex(m) for m in masks])
    # ---- 0. our native() conversion == the stock loader's RAM records
    c = setup(SIMG, raw)
    arr = bytes(c.ram.get(ARR + k, 0) for k in range(0x3C * len(raw)))
    ok0 = all(arr[0x3C * i:0x3C * i + 0x16] == nat[i][:0x16] and
              arr[0x3C * i + 0x18:0x3C * i + 0x3A] == nat[i][0x18:0x3A] for i in range(len(raw)))
    check(ok0, 'native() != stock loader output')
    print('native() vs stock loader: %s; ctx+0x1c=%d +0x26=%d +0x28=%#x count=%d' % (
        'OK' if ok0 else 'FAIL', c.rd(CTX + 0x1C, 1), c.rd(CTX + 0x26, 1), c.rd(CTX + 0x28, 2),
        c.rd(CTX + 0x2C, 2)))
    nat = [arr[0x3C * i:0x3C * i + 0x3C] for i in range(len(raw))]   # exact RAM image incl. pads
    # ---- 1. stock engine, stock image, Explorer records: find the coefficient layout
    cands = []
    for bv, tv in ((14, 0), (0, 14), (7, 7), (11, 3)):
        c = setup(SIMG, raw, vals=(bv, 7, tv, 15, 0, 0))
        run_b92a(c, 1)
        pb, pt = masks.index(1), masks.index(4)
        gb = EQ.gain_db(EQ.rec61(raw[pb]), bv)
        gt = EQ.gain_db(EQ.rec61(raw[pt]), tv)
        cands.append((coefs(c, pb), {60: gb, 5: None or 0.0, 20000: 0.0}))
        cands.append((coefs(c, pt), {20: 0.0, 23000: gt}))
    # peak at 60 Hz: DC/HF ~ 0 dB, centre = gain; shelf: DC 0, top = gain
    for cw, exp in cands:
        exp.pop(5, None)
    err, ORDER = find_order(cands)
    print('coefficient layout: order %s scale 2^-%d signs %s, max err %.3f dB' % (
        ORDER[:5], ORDER[5], ORDER[6], err))
    check(err < 0.35, 'stock designers do not reproduce the documented Explorer curves (%.2f dB)' % err)

    # ---- 2. patched image: off -> identical to stock; on -> Kuga BeoSonic shelves
    import beosonic_model as M
    kuga = {k: EQ.rec61(v) for k, v in B.kuga_records().items()}

    def gp(c, d, n=1):
        return c.rd(GP + d, n)

    def set_on(c, on, bb=50, bt=50, flat=0):
        c.wr(GP + B.DSPON, 2, B.MAGIC if on else 0)
        c.wr(GP + B.DSPB, 1, bb)
        c.wr(GP + B.DSPT, 1, bt)
        c.wr(GP + B.G_FLAT, 1, flat)

    for uv in ((7, 7, 7), (12, 3, 9), (0, 14, 14)):
        cs = setup(SIMG, raw, vals=uv + (15, 0, 0)); run_b92a(cs, 1)
        cp = setup(PIMG, raw, vals=uv + (15, 0, 0)); set_on(cp, False)
        cp.wr(GP + B.SWAPPED, 4, 0x12345678)
        run_b92a(cp, 1)
        same = all(coefs(cs, p) == coefs(cp, p) for p in range(len(nat)))
        check(same, 'off: patched engine differs from stock %s' % (uv,))
    print('off/stock equality: OK' if not fails else 'off/stock equality: FAIL')

    n = 0
    pos = {m: masks.index(m) for m in (1, 2, 4)}
    for (hx, hy) in ((15, 15), (15, 4), (27, 19), (5, 23), (0, 15), (30, 15), (15, 0), (15, 30), (8, 8)):
        w = M.xy_to_weights(hx + 1, hy + 1)
        if w is None:
            continue
        cc, dd = w['w_0x10000'], w['w_0x8000']
        uv = (11, 4, 2)
        c = setup(PIMG, raw, vals=uv + (15, 0, 0))
        c.wr(GP + B.SWAPPED, 4, 0)
        set_on(c, True, cc, dd)
        before8 = [coefs(c, p) for p in range(len(nat))]
        run_b92a(c, 0)
        n += 1
        ls, lv, hs = (EQ.gain_db(kuga[0x10000], cc), EQ.gain_db(kuga[0x4000], 100 - cc),
                      EQ.gain_db(kuga[0x8000], dd))
        rb, rm, rt = coefs(c, pos[1]), coefs(c, pos[2]), coefs(c, pos[4])
        got = (resp(rb, 10), resp(rb, 3000), resp(rm, 1000), resp(rt, 23500), resp(rt, 500))
        exp = (ls, 0.0, lv, hs, 0.0)
        ok = all(abs(g - e) < 0.35 for g, e in zip(got, exp))
        check(ok, 'on (%d,%d) c=%d d=%d got %s exp %s' % (hx, hy, cc, dd,
              ['%.2f' % x for x in got], ['%.2f' % x for x in exp]))
        # LS corner ~60 Hz: half gain near 60 Hz
        mid = resp(rb, 60)
        check(abs(ls) < 0.5 or 0.3 < mid / ls < 0.7, 'LS corner (%d,%d) %.2f/%.2f' % (hx, hy, mid, ls))
        check(c.rd(GP + B.SWAPPED, 4) == B.SWMAGIC, 'SWAPPED not set')
        for m, p in pos.items():
            check(c.rd(ARR + 0x3C * p, 4) == m, 'mask word changed at pos %d' % p)
            sv = bytes(c.ram.get(GP + B.SAVEBUF + 0x3C * (m >> 1) + k, 0) for k in range(0x3C))
            check(sv == nat[p], 'SAVEBUF[%d] != original' % m)
        for p in range(len(nat)):
            if p not in pos.values() and masks[p] not in (1, 2, 4):
                pass
        # ---- off again: bit-exact stock coefficients for the user's tone
        set_on(c, False)
        run_b92a(c, 0)
        cs = setup(SIMG, raw, vals=uv + (15, 0, 0)); run_b92a(cs, 1)
        for p in pos.values():
            check(coefs(c, p) == coefs(cs, p), 'off after on: pos %d differs from stock' % p)
        arr = bytes(c.ram.get(ARR + k, 0) for k in range(0x3C * len(nat)))
        check(arr == b''.join(nat), 'records not restored bit-exactly')
        check(c.rd(GP + B.SWAPPED, 4) == 0, 'SWAPPED not cleared')
    # ---- shadow invalidation: weights equal to the previous user values must still re-design
    for uv in ((11, 4, 2), (50, 50, 50), (0, 100, 7)):
        c = setup(PIMG, raw, vals=uv + (15, 0, 0)); c.wr(GP + B.SWAPPED, 4, 0)
        set_on(c, False); run_b92a(c, 0)                    # shadows = user values
        set_on(c, True, uv[0], uv[2])                         # c = user Bass, d = user Treble
        c.wr(CTX + 9, 1, 100 - uv[0])                         # and Mid such that 100-c == user Mid
        c.wr(CTX + 0x2F, 1, 100 - uv[0])
        run_b92a(c, 0)
        rb, rm, rt = coefs(c, pos[1]), coefs(c, pos[2]), coefs(c, pos[4])
        exp = (EQ.gain_db(kuga[0x10000], uv[0]), EQ.gain_db(kuga[0x4000], 100 - uv[0]),
               EQ.gain_db(kuga[0x8000], uv[2]))
        got = (resp(rb, 10), resp(rm, 1000), resp(rt, 23500))
        check(all(abs(g - e) < 0.35 for g, e in zip(got, exp)) and resp(rt, 500) > -0.35,
              'equal-value swap %s got %s exp %s' % (uv, got, exp))
    # ---- flat flag: overrides inactive, records stock
    c = setup(PIMG, raw, vals=(11, 4, 2, 15, 0, 0)); c.wr(GP + B.SWAPPED, 4, 0)
    set_on(c, True, 100, 0, flat=1); run_b92a(c, 1)
    arr = bytes(c.ram.get(ARR + k, 0) for k in range(0x3C * len(nat)))
    check(arr == b''.join(nat) and c.rd(GP + B.SWAPPED, 4) == 0, 'flat=1 must not swap')
    # ---- precheck failure: duplicate mask 1 -> no swap, stock values
    bad = list(nat); bad[pos[2]] = nat[pos[1]]
    c = setup(PIMG, raw, vals=(11, 4, 2, 15, 0, 0), recs_native=bad); c.wr(GP + B.SWAPPED, 4, 0)
    set_on(c, True, 100, 0); run_b92a(c, 1)
    arr = bytes(c.ram.get(ARR + k, 0) for k in range(0x3C * len(bad)))
    check(arr == b''.join(bad) and c.rd(GP + B.SWAPPED, 4) == 0, 'dup mask must not swap')
    # ---- LEQ: after a reload the RAM records are stock; next call with on swaps again
    c = setup(PIMG, raw, vals=(11, 4, 2, 15, 0, 0)); c.wr(GP + B.SWAPPED, 4, 0)
    set_on(c, True, 100, 0); run_b92a(c, 0)
    for i, r in enumerate(nat):                                   # loader re-copies flash records
        for k in range(0x3C):
            c.ram[ARR + 0x3C * i + k] = r[k]
    def loader(cpu):
        cpu.r[10] = 0
    c.stubs = {B.F_LOADER: loader}
    c.r[31] = 0xBEEF0; c.land = {0xBEEF0}
    c.r[10] = 0x55
    c.run(int(B.json_labels()['LEQ'], 16) if hasattr(B, 'json_labels') else LEQ_ADDR)
    check(c.rd(GP + B.SWAPPED, 4) == 0 and c.r[10] == 0 and c.r[3] == SP0, 'LEQ')
    c.stubs = {}
    run_b92a(c, 1)
    rb = coefs(c, pos[1])
    check(abs(resp(rb, 10) - 6.0) < 0.35 and c.rd(GP + B.SWAPPED, 4) == B.SWMAGIC, 're-swap after reload')
    print('tone engine cases', n + 6, 'FAILURES', fails)
    return fails


if __name__ == '__main__':
    import json
    LEQ_ADDR = int(json.load(open(P.MANIFEST))['labels']['LEQ'], 16)
    sys.exit(1 if main() else 0)
