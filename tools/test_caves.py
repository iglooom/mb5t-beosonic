#!/usr/bin/env python3
"""Cave tests on the EMITTED bytes (v850emu byte decoder) against independent Python models.

  python3 tools/test_caves.py   -> per-cave case counts, exit 1 on any mismatch
Covers RX, INIT, TICK (import / frames / snapshot+arm / DSP refresh / 0x22D TX), LOADH, CMPH,
EM/ET value paths. The tone-engine side (EB swap, real coefficients) is in tone_emu.py.
"""
import json, os, random, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import v850emu as E                  # noqa: E402
import build as B                    # noqa: E402
import paths as P                    # noqa: E402

img = open(os.environ.get('BEO_IMG', P.OUT_BIN), 'rb').read()
man = json.load(open(P.MANIFEST))
L = {k: int(v, 16) for k, v in man['labels'].items()}
GP = B.GP
SP0 = 0xFEDFA000
LUTC = img[B.LUTC - 0x80000:B.LUTC - 0x80000 + 961]
LUTD = img[B.LUTD - 0x80000:B.LUTD - 0x80000 + 961]
fails = 0


def check(c, msg):
    global fails
    if not c:
        fails += 1
        if fails < 25:
            print('FAIL', msg)


def new_cpu():
    c = E.CPU(img)
    c.r[3], c.r[4], c.r[5] = SP0, GP, 0x89080
    c.code_lo, c.code_hi = B.CAVE, B.TPL
    for i in range(20, 30):
        c.r[i] = 0x1000 + i
    return c


def rb(c, d):
    return c.ram.get(GP + d, 0)


def wb(c, d, v):
    c.ram[GP + d] = v & 0xFF


def rh(c, d):
    return rb(c, d) | rb(c, d + 1) << 8


def wh(c, d, v):
    wb(c, d, v); wb(c, d + 1, v >> 8)


def rw(c, d):
    return rh(c, d) | rh(c, d + 2) << 16


def ww(c, d, v):
    for i in range(4):
        wb(c, d + i, v >> (8 * i))


def idx(x, y):
    return 31 * (y - 1) + (x - 1)


def enc(x, y, on):
    st = 2 if on else 1
    return x | y << 8 | st << 16 | ((x ^ y ^ st ^ B.CHK_X) & 0xFF) << 24


# ======================================================================= LUT
def test_lut():
    import beosonic_model as M
    n = 0
    for yr in range(1, 32):
        for xr in range(1, 32):
            w = M.xy_to_weights(xr, yr)
            n += 1
            if w is None:
                check(LUTC[idx(xr, yr)] == 0xFF, 'LUTC reject %d,%d' % (xr, yr))
            else:
                check((LUTC[idx(xr, yr)], LUTD[idx(xr, yr)]) == (w['w_0x10000'], w['w_0x8000']),
                      'LUT %d,%d' % (xr, yr))
    print('LUT  cases', n, 'rejected', sum(1 for x in LUTC if x == 0xFF))


# ======================================================================= RX (unchanged from v1)
def test_rx():
    n = 0
    for cid in list(range(0x100, 0x110)) + [0x22D, 0x7DF, 0, 0x7FF, 0x305]:
        for ide in (0, 0x8000):
            for dlc in range(10):
                for b5, b6, b7 in ((0, 0, 0), (2, 0, 0), (1, 0, 0), (0xFF, 0x10, 5), (0, 0x1F, 0x1F)):
                    c = new_cpu()
                    hdr = (cid << 2) | ide | 1
                    c.r[16], c.r[31] = hdr, 0xAB84E
                    c.land = {0xAB84E, 0xAB896}
                    wb(c, B.G_FRAME_DLC, dlc); wb(c, B.G_B5, b5); wb(c, B.G_B6, b6); wb(c, B.G_B7, b7)
                    ww(c, B.RXW, 0xDEADBEEF)
                    land = c.run(L['RX'])
                    n += 1
                    mine = (hdr & 0x1FFC) == 0x414
                    pl = ((b5 & 3) << 16) | b6 << 8 | b7
                    exp = 0x01000000 | pl if mine and dlc >= 8 and pl else 0xDEADBEEF
                    check(land == (0xAB896 if mine else 0xAB84E) and rw(c, B.RXW) == exp and
                          c.r[28] == hdr & 0x1FFC and c.r[16] == hdr and not c.mmio_w, 'RX %x %d' % (cid, dlc))
    print('RX   cases', n)


# ======================================================================= INIT
def test_init():
    c = new_cpu()
    for d in range(0, 0x30):
        wb(c, B.D + d, 0xA5)
    wb(c, B.G_FLAT, 1)
    c.r[31], c.land, c.r[6] = 0xA72FC, {0xA70D6}, 0x1234
    land = c.run(L['INIT'])
    check(land == 0xA70D6 and c.r[31] == 0xA72FC and c.r[6] == 0x1234, 'INIT landing')
    exp = {B.ON: 0, B.CX: 16, B.CY: 16, B.BB: 50, B.BT: 50, B.PEND: 0, B.TXNOW: 1, B.LASTFLAT: 1,
           B.DSPB: 50, B.DSPT: 50, B.TRIG: 7, B.IMPORTED: 0, B.CHG: 0}
    for k, v in exp.items():
        check(rb(c, k) == v, 'INIT var %x=%x want %x' % (k, rb(c, k), v))
    check(rw(c, B.RXW) == 0 and rh(c, B.TXCNT) == 0 and rh(c, B.DSPON) == 0 and rw(c, B.SNAP) == 0
          and rw(c, B.SNAPM) == 0, 'INIT words')
    check(rb(c, B.LOADSEQ) == 0xA5, 'INIT must not touch LOADSEQ (app task owns it)')
    print('INIT cases 1')


# ======================================================================= TICK
class MB1:
    def __init__(self, c, stuck=False, trq=False):
        self.ctrl = (2 if trq else 0) | (1 if stuck else 0)
        self.stuck, self.c, self.sent = stuck, c, None
        c.mmio_r[B.MB1_CTRL] = lambda n: self.ctrl
        c.mmio_wh[B.MB1_CTRL] = self.w

    def w(self, n, v):
        clr, st = v & 0xFF, v >> 8
        if self.stuck:
            clr &= ~1
        self.ctrl = (self.ctrl & ~clr) | st
        if st & 2:
            mm = {a: v for a, nn, v in self.c.mmio_w}
            self.sent = dict(id=mm.get(B.MB1_CTRL - 8), dlc=mm.get(B.MB1_DATA + 0x20),
                             conf=mm.get(B.MB1_DATA + 0x24),
                             data=[mm.get(B.MB1_DATA + 4 * i) for i in range(8)])


VARS = dict(on=B.ON, cx=B.CX, cy=B.CY, bb=B.BB, bt=B.BT, pend=B.PEND, txnow=B.TXNOW,
            lastflat=B.LASTFLAT, dspb=B.DSPB, dspt=B.DSPT, trig=B.TRIG, imported=B.IMPORTED,
            lastseq=B.LASTSEQ, chg=B.CHG)
ENV0 = dict(flat=0, dspq=0xFEDF5000, appbass=9, dspbass=5, txen=1, txdis=0, canstat=0, trq=0,
            stuck=0, nvl=1, loadseq=3, rec=0xFFFFFFFF, tick=0x12345678)


def base_state(**kw):
    s = dict(on=0, cx=16, cy=16, bb=50, bt=50, pend=0, txnow=0, lastflat=0, dspb=50, dspt=50,
             trig=7, imported=1, lastseq=3, chg=0, txcnt=500, dspon=0, snap=0, snapm=0,
             arm=0, armt=0)
    s.update(kw)
    return s


def valid_rec(rec):
    x, y, st, ck = rec & 0xFF, rec >> 8 & 0xFF, rec >> 16 & 0xFF, rec >> 24
    return (ck == (x ^ y ^ st ^ B.CHK_X) & 0xFF and st in (1, 2) and 1 <= x <= 31 and 1 <= y <= 31
            and LUTC[idx(x, y)] != 0xFF)


def model_tick(s, rxw, e):
    s = dict(s)
    if e['nvl'] == 1 and (s['imported'] == 0 or s['lastseq'] != e['loadseq']):
        s['lastseq'], s['imported'] = e['loadseq'], 1
        r = e['rec']
        if valid_rec(r):
            x, y, on = r & 0xFF, r >> 8 & 0xFF, (r >> 16 & 0xFF) == 2
        else:
            x, y, on = 16, 16, False
        s['cx'], s['cy'], s['on'] = x, y, int(on)
        s['bb'], s['bt'] = LUTC[idx(x, y)], LUTD[idx(x, y)]
        s['pend'] = s['txnow'] = 1
        s['snap'], s['snapm'] = enc(x, y, on), B.SNAPMAGIC
    if rxw:
        s['txnow'] = 1
        en, X, Y = (rxw >> 16) & 3, (rxw >> 8) & 0xFF, rxw & 0xFF
        if en == 2 and not s['on']:
            s['on'] = 1; s['pend'] = 1; s['chg'] = 1
        elif en == 1 and s['on']:
            s['on'] = 0; s['pend'] = 1; s['chg'] = 1
        if s['on'] and 1 <= X <= 31 and 1 <= Y <= 31 and LUTC[idx(X, Y)] != 0xFF \
                and (X, Y) != (s['cx'], s['cy']):
            s['cx'], s['cy'] = X, Y
            s['bb'], s['bt'] = LUTC[idx(X, Y)], LUTD[idx(X, Y)]
            s['pend'] = s['chg'] = 1
    if s['chg']:
        s['chg'] = 0
        if s['imported']:
            s['snap'], s['snapm'] = enc(s['cx'], s['cy'], s['on']), B.SNAPMAGIC
            s['armt'], s['arm'] = e['tick'], 1
    flat = e['flat']
    if flat != s['lastflat']:
        s['lastflat'] = flat; s['pend'] = 1
    post = None
    if s['pend'] and flat != 1 and e['dspq']:
        if s['on']:
            s['dspb'], s['dspt'], s['dspon'] = s['bb'], s['bt'], B.MAGIC
        else:
            s['dspon'] = 0
        s['trig'] = e['appbass'] if e['appbass'] <= 14 else e['dspbass']
        s['pend'] = 0
        post = (e['dspq'], 0, 3, 0x1011, s['trig'])
    sent = None
    if s['txcnt'] == 0:
        go = True
    else:
        s['txcnt'] -= 1
        go = bool(s['txnow'])
    if go and (e['txen'] & 1) and not (e['txdis'] & 1) and not (e['canstat'] & 0x10) \
            and not e['trq'] and not e['stuck']:
        sent = dict(id=0x22D << 2, dlc=8, conf=1,
                    data=[s['cx'], s['cy'], 0x80 if s['on'] else 0x40, 0, 0, 0, 0, 0])
        s['txnow'], s['txcnt'] = 0, B.PERIOD
    return s, post, sent


def run_tick(s, rxw, e):
    c = new_cpu()
    for k, d in VARS.items():
        wb(c, d, s[k])
    wh(c, B.TXCNT, s['txcnt']); wh(c, B.DSPON, s['dspon'])
    ww(c, B.SNAP, s['snap']); ww(c, B.SNAPM, s['snapm'])
    wb(c, B.G_ARM, s['arm']); ww(c, B.G_ARMT, s['armt'])
    ww(c, B.RXW, rxw)
    wb(c, B.G_FLAT, e['flat']); ww(c, B.G_DSPQ, e['dspq']); wb(c, B.G_DSPPRIO, 3)
    wb(c, B.G_APPBASS, e['appbass']); wb(c, B.G_DSPBASS, e['dspbass'])
    wb(c, B.G_TXEN, e['txen']); wb(c, B.G_TXDIS, e['txdis'])
    wb(c, B.G_NVLOADED, e['nvl']); wb(c, B.LOADSEQ, e['loadseq']); ww(c, B.G_REC10, e['rec'])
    ww(c, B.G_TICK, e['tick'])
    c.mmio_r[B.CAN_STAT] = e['canstat']
    mb = MB1(c, stuck=e['stuck'], trq=e['trq'])
    calls = []

    def adfee(cpu):
        calls.append('adfee')

    def post(cpu):
        sp = cpu.r[3]
        p, ln = cpu.rd(sp, 4), cpu.rd(sp + 4, 4)
        calls.append(('post', cpu.r[6], cpu.r[7], cpu.r[8], cpu.r[9], cpu.rd(p, 1), ln, p))
        cpu.r[10] = 0
        for r in range(6, 20):
            if r != 10:
                cpu.r[r] = 0xBAD0000 + r
    c.stubs = {B.F_ADFEE: adfee, B.F_8C7CC: post}
    c.r[31], c.land = 0xA739E, {0xA739E}
    land = c.run(L['TICK'])
    check(land == 0xA739E and c.r[3] == SP0, 'TICK landing/sp')
    for r in list(range(20, 30)) + [4, 5]:
        check(c.r[r] == new_cpu().r[r], 'TICK clobbered r%d' % r)
    check(calls and calls[0] == 'adfee', 'TICK must call adfee first')
    check(rw(c, B.RXW) == 0, 'RXW not cleared')
    check(rw(c, B.G_REC10) == e['rec'] and rb(c, B.LOADSEQ) == e['loadseq'], 'TICK wrote OEM record/LOADSEQ')
    out = {k: rb(c, d) for k, d in VARS.items()}
    out.update(txcnt=rh(c, B.TXCNT), dspon=rh(c, B.DSPON), snap=rw(c, B.SNAP), snapm=rw(c, B.SNAPM),
               arm=rb(c, B.G_ARM), armt=rw(c, B.G_ARMT))
    posts = [(p[1], p[2], p[3], p[4], p[5]) for p in calls if p != 'adfee']
    for p in calls:
        if p != 'adfee':
            check(p[6] == 1 and p[7] == GP + B.TRIG, 'post len/ptr')
    return out, posts, mb.sent


def cmp_tick(s, rxw, e, tag):
    got, posts, sent = run_tick(s, rxw, e)
    exp, post, esent = model_tick(s, rxw, e)
    ok = got == exp and posts == ([post] if post else []) and sent == esent
    if not ok:
        diff = {k: (got.get(k), exp.get(k)) for k in exp if got.get(k) != exp.get(k)}
        check(False, '%s diff %s posts %s/%s tx %s/%s' % (tag, diff, posts, post, sent, esent))
    return exp


def test_tick():
    n = 0
    e = dict(ENV0)
    # (a) exhaustive frames from on/off x two positions (imported, no reload)
    for on in (0, 1):
        for cxy in ((16, 16), (6, 24)):
            for en in range(4):
                for X in range(32):
                    for Y in range(32):
                        s = base_state(on=on, cx=cxy[0], cy=cxy[1])
                        cmp_tick(s, 0x01000000 | en << 16 | X << 8 | Y, e, 'frame')
                        n += 1
    # (b) import: every record byte pattern class x loaded flag x imported/lastseq
    recs = [enc(x, y, on) for x in (1, 6, 16, 31) for y in (1, 16, 24, 31) for on in (0, 1)]
    recs += [0xFFFFFFFF, 0, 0x00020000, enc(16, 16, 1) ^ 0x01000000, enc(1, 1, 1), enc(31, 31, 1),
             16 | 16 << 8 | 3 << 16 | ((16 ^ 16 ^ 3 ^ 0xB5) << 24), 0 | 16 << 8 | 2 << 16 | ((0 ^ 16 ^ 2 ^ 0xB5) << 24),
             32 | 16 << 8 | 2 << 16 | ((32 ^ 16 ^ 2 ^ 0xB5) << 24)]
    rnd = random.Random(7)
    recs += [rnd.getrandbits(32) for _ in range(200)]
    for rec in recs:
        for nvl in (0, 1, 2):
            for imported, lastseq in ((0, 3), (1, 3), (1, 2)):
                for rxw in (0, 0x01020000, 0x01001A07):
                    s = base_state(imported=imported, lastseq=lastseq, on=1, cx=6, cy=24)
                    cmp_tick(s, rxw, dict(e, rec=rec, nvl=nvl), 'import %08x' % rec)
                    n += 1
    # (c) gating / flat / DSP / TX variants
    for txcnt in (0, 1, 1000):
        for txnow in (0, 1):
            for txen in (0, 1):
                for txdis in (0, 1):
                    for cs in (0, 0x10):
                        for trq in (0, 1):
                            for stuck in (0, 1):
                                s = base_state(txcnt=txcnt, txnow=txnow, on=1, cx=28, cy=20)
                                cmp_tick(s, 0, dict(e, txen=txen, txdis=txdis, canstat=cs, trq=trq,
                                                    stuck=stuck), 'tx')
                                n += 1
    for flat in (0, 1):
        for last in (0, 1):
            for pend in (0, 1):
                for dspq in (0, 0xFEDF5000):
                    for ab in (0, 7, 14, 15):
                        for on in (0, 1):
                            s = base_state(pend=pend, lastflat=last, on=on, bb=87, bt=13)
                            cmp_tick(s, 0, dict(e, flat=flat, dspq=dspq, appbass=ab), 'dsp')
                            n += 1
    # (d) random sequences incl. reloads, profile switches, unloaded phases, boot from garbage
    for seed in range(4):
        rnd = random.Random(seed)
        s = base_state(imported=0, lastseq=rnd.randrange(256), snap=rnd.getrandbits(32),
                       snapm=rnd.getrandbits(32))
        env = dict(e, nvl=0, loadseq=rnd.randrange(256), rec=rnd.choice(recs))
        for i in range(2500):
            if rnd.random() < 0.01:
                env['nvl'] = 1
            if rnd.random() < 0.004:
                env['loadseq'] = (env['loadseq'] + 1) & 0xFF
                env['rec'] = rnd.choice(recs + [s['snap']])
            if rnd.random() < 0.002:
                env['nvl'] = rnd.choice((0, 1))
            env['tick'] = (env['tick'] + 1) & 0xFFFFFFFF
            env['flat'] = 1 if rnd.random() < 0.03 else 0
            rxw = (0x01000000 | rnd.randrange(4) << 16 | rnd.randrange(33) << 8 | rnd.randrange(33)) \
                if rnd.random() < 0.25 else 0
            s = cmp_tick(s, rxw, env, 'seq%d/%d' % (seed, i))
            n += 1
    print('TICK cases', n)


# ======================================================================= LOADH / CMPH / EM / ET
def test_loadh():
    n = 0
    for seq in (0, 1, 0x7F, 0xFF):
        for ret in (0, 1, 0xFFFFFFFF):
            c = new_cpu()
            wb(c, B.LOADSEQ, seq)
            ww(c, B.SNAPM, B.SNAPMAGIC)
            got = []

            def apply(cpu, ret=ret):
                got.append(cpu.r[3])
                cpu.r[10] = ret
                for r in range(6, 20):
                    if r != 10:
                        cpu.r[r] = 0xBAD0000 + r
            c.stubs = {B.F_APPLY: apply}
            c.r[31], c.land = 0x98900, {0x98900}
            land = c.run(L['LOADH'])
            n += 1
            check(land == 0x98900 and got and c.r[10] == ret and rb(c, B.LOADSEQ) == (seq + 1) & 0xFF
                  and c.r[3] == SP0 and all(c.r[r] == new_cpu().r[r] for r in range(20, 30))
                  and rw(c, B.SNAPM) == 0,
                  'LOADH seq=%d ret=%x' % (seq, ret))
    print('LOADH cases', n)


def test_cmph():
    n = 0
    rnd = random.Random(3)
    vals = [0, 0xFFFFFFFF, enc(16, 16, 0), enc(28, 20, 1)] + [rnd.getrandbits(32) for _ in range(6)]
    for snapm in (B.SNAPMAGIC, 0, B.SNAPMAGIC ^ 1, 0xFFFFFFFF):
        for snap in vals:
            for rec in vals:
                for r29 in (0, 1):
                    c = new_cpu()
                    ww(c, B.SNAPM, snapm); ww(c, B.SNAP, snap); ww(c, B.G_REC10, rec)
                    ww(c, B.G_HDR, 0xC0FFEE11)
                    c.r[29] = r29
                    c.r[31], c.land = 0x98138, {0x98138}
                    land = c.run(L['CMPH'])
                    n += 1
                    wr_ = snapm == B.SNAPMAGIC and snap != rec
                    check(land == 0x98138 and c.r[1] == 0xC0FFEE11 and
                          rw(c, B.G_REC10) == (snap if wr_ else rec) and
                          c.r[29] == (1 if wr_ else r29) and
                          all(c.r[r] == new_cpu().r[r] for r in list(range(20, 29)) + [3, 4, 5]),
                          'CMPH m=%x s=%x r=%x' % (snapm, snap, rec))
    print('CMPH cases', n)


def test_emet():
    n = 0
    CTX = 0xFEDF8CEC
    for cave, field, sh, shreg in (('EM', 9, 0x2F, 19), ('ET', 10, 0x30, 18)):
        for sw in (B.SWMAGIC, 0, B.SWMAGIC ^ 0x100):
            for v in (0, 1, 13, 50, 87, 99, 100, 101, 127, 200, 255):
                for orig in (0, 7, 14, 0xFD):
                    c = new_cpu()
                    c.r[6] = c.r[29] = CTX
                    c.ram[CTX + field] = orig
                    c.ram[CTX + sh] = 0x5B
                    ww(c, B.SWAPPED, sw)
                    wb(c, B.DSPB, v); wb(c, B.DSPT, (v * 7) & 0xFF)
                    c.r[31], c.land = 0x8B000, {0x8B000}
                    c.run(L[cave])
                    n += 1
                    o = orig - 256 if orig & 0x80 else orig
                    if sw != B.SWMAGIC:
                        exp = o
                    elif cave == 'EM':
                        exp = 100 - v if v <= 100 else o
                    else:
                        t = (v * 7) & 0xFF
                        exp = t if t <= 100 else o
                    check(E.sx(c.r[7], 32) == exp and c.r[shreg] == 0x5B and c.r[30] == CTX and
                          c.r[28] == new_cpu().r[28] and not c.mmio_w,
                          '%s sw=%x v=%d orig=%d -> %d exp %d' % (cave, sw, v, o, E.sx(c.r[7], 32), exp))
    print('EMET cases', n)


if __name__ == '__main__':
    test_lut()
    test_rx()
    test_init()
    test_tick()
    test_loadh()
    test_cmph()
    test_emet()
    print('FAILURES', fails)
    sys.exit(1 if fails else 0)
