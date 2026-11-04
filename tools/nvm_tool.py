#!/usr/bin/env python3
"""MB5T-14C589-CB non-volatile storage helper (SPI EEPROM ST95320 + data-flash records).

  python3 nvm_tool.py selftest            port of FUN_00092698 vs. emulation of the stock ROM code
  python3 nvm_tool.py decode DUMP.bin     decode a 4 KiB EEPROM dump (offsets 0x000..0xFFF)
  python3 nvm_tool.py slot X Y STATE [--slot-bytes HEX48]
                                          build the BeoSonic extension bytes (+0x10..+0x13) and,
                                          given an existing 0x18-byte slot record, the patched record
  python3 nvm_tool.py sites               list every EEPROM / data-flash access site in the stock app

Checksum used by all MB5T NVM records: FUN_00092698(buf, len8) -> FUN_00088f26 ctx{type 0, seed 0}
-> FUN_00088e3a(buf, len, seed=0, mode=0): Adler-32-like, a=b=0 start, 16-bit sums folded with
+0xF on carry out of bit 15 or when the sum exceeds 0xFFF1. Result (b<<16)|a.
EEPROM records store it little-endian (raw u32 from RAM); data-flash DID records store it big-endian.
"""
import os, random, re, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths as P  # noqa: E402
STOCK = P.STOCK_BIN
GP = 0xFEDF9620
TP = 0x89080


# ------------------------------------------------------------------ checksum
def nvm_cksum(data, length=None, seed=0):
    """Exact port of FUN_00092698 (length is truncated to 8 bits like the 'zxb r7')."""
    n = (len(data) if length is None else length) & 0xFF
    a, b = seed & 0xFFFF, seed >> 16
    for x in data[:n]:
        s = a + x
        a = (s + 0xF) & 0xFFFF if (s > 0xFFFF or s > 0xFFF1) else s
        s = b + a
        b = (s + 0xF) & 0xFFFF if (s > 0xFFFF or s > 0xFFF1) else s
    return (b << 16) | a


# ------------------------------------------------------------------ record formats
SLOT_BASE, SLOT_STRIDE, SLOT_COUNT, HDR = 0x820, 0x20, 5, 0x800
SLOT_FIELDS = [  # offset, name, live RAM source (gp offset) per FUN_00097ffc
    (0x00, 'counter u32', None), (0x04, 'b4 (gp-0x28F8)', -0x28F8), (0x05, 'b5 (gp-0x2943)', -0x2943),
    (0x06, 'b6 (gp-0x2942)', -0x2942), (0x07, 'Bass (gp-0x2941)', -0x2941), (0x08, 'Mid (gp-0x2940)', -0x2940),
    (0x09, 'Treble (gp-0x293F)', -0x293F), (0x0A, 'bA (gp-0x2992)', -0x2992), (0x0B, 'level1 (gp-0x293D)', -0x293D),
    (0x0C, 'level2 (gp-0x293C)', -0x293C), (0x0D, 'level3 (gp-0x293B)', -0x293B), (0x0E, 'level4 (gp-0x293A)', -0x293A),
    (0x0F, 'spare', None), (0x10, 'spare (PJ6T: BeoSonic X)', None), (0x11, 'spare (PJ6T: BeoSonic Y)', None),
    (0x12, 'spare (PJ6T: BeoSonic state)', None), (0x13, 'spare', None), (0x14, 'cksum u32 LE over 0x00..0x13', None),
]
SLOT_DEFAULTS = {4: 1, 5: 7, 6: 7, 7: 7, 8: 7, 9: 7, 10: 3, 11: 6, 12: 0x12, 13: 0x0C, 14: 0x0A}  # FUN_0009382e

BEO_MAGIC = 0xB5          # check byte = X ^ Y ^ STATE ^ BEO_MAGIC (our extension, PJ6T leaves +0x13 unused)
BEO_DEFAULT = (16, 16, 1)  # raw X, raw Y, state (1 = off, 2 = on): "off, pad centre"


def rec_ok(rec):
    """Validity rule of FUN_000937b4: cksum matches, or the record is blank (counter and cksum == -1)."""
    cnt, ck = struct.unpack_from('<I', rec, 0)[0], struct.unpack_from('<I', rec, 0x14)[0]
    return ck == nvm_cksum(rec, 0x14) or (cnt == 0xFFFFFFFF and ck == 0xFFFFFFFF)


def beo_encode(x, y, st):
    return bytes([x, y, st, x ^ y ^ st ^ BEO_MAGIC])


def beo_decode(b4, lut=None):
    """Return (x, y, st, valid). Invalid -> BEO_DEFAULT. lut(x, y) -> False when outside the pad."""
    x, y, st, chk = b4
    ok = 1 <= x <= 31 and 1 <= y <= 31 and st in (1, 2) and chk == x ^ y ^ st ^ BEO_MAGIC
    if ok and lut is not None and not lut(x, y):
        ok = False
    return (x, y, st, True) if ok else BEO_DEFAULT + (False,)


def beo_patch_slot(rec, x, y, st):
    """What the proposed save cave does to the RAM copy before writing it (mirrors FUN_00097ffc)."""
    r = bytearray(rec)
    r[0x10:0x14] = beo_encode(x, y, st)
    cnt = struct.unpack_from('<I', r, 0)[0]
    if cnt != 0xFFFFFFFF:
        struct.pack_into('<I', r, 0, (cnt + 1) & 0xFFFFFFFF)
    struct.pack_into('<I', r, 0x14, nvm_cksum(r, 0x14))
    return bytes(r)


# ------------------------------------------------------------------ EEPROM map (offsets in the 4 KiB part)
EE_MAP = [  # start, length, writer(s) / reader(s), content, check
    (0x000, 0x0C, 'FUN_0009b3ec (shutdown 0x1020)', 'power-cycle counters u32,u32 + cksum(8)', 8),
    (0x020, 0x0C, 'FUN_0009b3ec', 'second copy of 0x000', 8),
    (0x0C0, 0x3C, 'FUN_00099014 / FUN_0009969a', 'record A (cksum over 0x38 @+0x38)', 0x38),
    (0x100, 0x3C, 'FUN_00099014 / FUN_0009969a', 'record A, second copy', 0x38),
    (0x180, 0x0C, 'FUN_0008e1da (UDS dispatcher FUN_0008ec78)', 'tester record, request byte0 = 0', None),
    (0x1A0, 0x0C, 'FUN_0008e1da (UDS dispatcher FUN_0008ec78)', 'tester record, request byte0 = 1', None),
    (0x2A0, 0x0C, 'FUN_0009029e / FUN_00090d6e', 'DTC-manager record (cksum over 8 @+8)', 8),
    (0x2C0, 0x0B, 'FUN_00090218 / FUN_00090d5e', 'DTC status bitmap (0x56 bits)', None),
    (0x3C0, 0x38, 'FUN_0009c524 / FUN_0009ebc0', 'record B (cksum over 0x32)', None),
    (0x400, 0x240, 'FUN_0009029e / FUN_00090d12', 'event memory: 12 x 0x30 records (RAM mirror gp-0x2E40)', None),
    (0x700, 0x60, 'FUN_0009029e / FUN_00090d12', 'event memory: 2 x 0x30 records (RAM mirror gp-0x2C00)', None),
    (0x800, 0x18, 'FUN_0009370e / FUN_00097ffc', 'audio header (current slot, misc), cksum(0x14) @+0x14', 0x14),
] + [(SLOT_BASE + i * SLOT_STRIDE, 0x18, 'FUN_000937b4 / FUN_00097ffc / FUN_00093580 / FUN_0009836a',
      'audio slot %d (%s)' % (i, 'personal profile' if i < 4 else 'default/guest'), 0x14) for i in range(SLOT_COUNT)]


def decode(path):
    d = open(path, 'rb').read()
    assert len(d) >= 0x1000, 'need a 4 KiB dump'
    for start, ln, who, what, ck in EE_MAP:
        blk = d[start:start + ln]
        st = ''
        if ck:
            stored = struct.unpack_from('<I', blk, ck)[0]
            calc = nvm_cksum(blk, ck)
            st = 'OK' if stored == calc else ('blank' if set(blk) == {0xFF} else 'BAD %08x!=%08x' % (stored, calc))
        print('%03X+%03X %-6s %s' % (start, ln, st, what))
        if start >= SLOT_BASE or start == HDR:
            print('        ' + blk.hex(' '))
        if start >= SLOT_BASE:
            x, y, s, ok = beo_decode(blk[0x10:0x14])
            print('        BeoSonic ext: X=%d Y=%d state=%d %s' % (x, y, s, 'valid' if ok else '-> default'))
    used = bytearray(0x1000)
    for start, ln, *_ in EE_MAP:
        used[start:start + ln] = b'\1' * ln
    free = []
    i = 0
    while i < 0x1000:
        if not used[i]:
            j = i
            while j < 0x1000 and not used[j]:
                j += 1
            free.append((i, j - i, set(d[i:j]) <= {0xFF}))
            i = j
        else:
            i += 1
    print('\nunreferenced ranges (start, len, all-0xFF in this dump):')
    for f in free:
        print('  %03X+%03X %s' % f)


# ------------------------------------------------------------------ call-site extractor
def sites():
    src = open(os.path.join(P.ANALYSIS, 'MB5T-14C589-CB.c')).read()
    funcs = {}
    for f in re.split(r'\n// ==== ', src):
        m = re.match(r'(FUN_\w+) @ (\w+)', f)
        if m:
            funcs[m.group(1)] = f
    api = {'FUN_0008c362': 'vread', 'FUN_0008c3ea': 'vwrite', 'FUN_000926f6': 'dfread', 'FUN_000927c0': 'dfwrite'}
    rows = []
    for name, body in funcs.items():
        for fn, kind in api.items():
            for m in re.finditer(fn + r'\(([^;]*)\);', body):
                args = ' '.join(m.group(1).split())
                if kind.startswith('v') and '0x32' not in args and '0x31' not in args:
                    continue      # RAM/flash regions or computed addresses (see doc)
                rows.append((kind, name, args))
    for r in sorted(rows):
        print('%-8s %-14s %s' % r)


# ------------------------------------------------------------------ selftest (emulate the stock ROM code)
def _emu_class():
    """v850emu.CPU plus the short-format ops used by FUN_00092698/88f26/88e3a (sld/sst, not)."""
    import v850emu as E

    class CPU(E.CPU):
        def step(self):
            pc = self.pc
            if pc in self.land or pc in self.stubs:
                return super().step()
            w = self.h(pc)
            r1, r2 = w & 0x1F, (w >> 11) & 0x1F
            ep = self.r[30]
            k = (w >> 7) & 0xF
            if (w >> 5) & 0x3F == 0x01:                       # not r1,r2
                self.setr(r2, self.setzs(~self.r[r1] & E.M32)); self.OV = 0
            elif (w >> 4) & 0x7F == 0x06 and r2:              # sld.bu disp4[ep],r2
                self.setr(r2, self.rd(ep + (w & 0xF), 1))
            elif (w >> 4) & 0x7F == 0x07 and r2:              # sld.hu disp5[ep],r2
                self.setr(r2, self.rd(ep + ((w & 0xF) << 1), 2))
            elif k == 0x6:                                    # sld.b
                self.setr(r2, E.sx(self.rd(ep + (w & 0x7F), 1), 8))
            elif k == 0x7:                                    # sst.b
                self.wr(ep + (w & 0x7F), 1, self.r[r2])
            elif k == 0x8:                                    # sld.h
                self.setr(r2, E.sx(self.rd(ep + ((w & 0x7F) << 1), 2), 16))
            elif k == 0x9:                                    # sst.h
                self.wr(ep + ((w & 0x7F) << 1), 2, self.r[r2])
            elif k == 0xA:                                    # sld.w / sst.w
                d = (w & 0x7E) << 1
                if w & 1:
                    self.wr(ep + d, 4, self.r[r2])
                else:
                    self.setr(r2, self.rd(ep + d, 4))
            else:
                return super().step()
            self.steps += 1
            self.pc = pc + 2
    return CPU


def selftest():
    CPU = _emu_class()
    img = open(STOCK, 'rb').read()
    rnd = random.Random(1)
    vecs = [b'', b'\0' * 20, b'\xff' * 20, b'\xff' * 255, bytes(range(256)), b'\x01' * 300]
    vecs += [bytes(rnd.randrange(256) for _ in range(rnd.randrange(1, 260))) for _ in range(300)]
    bad = 0
    for v in vecs:
        c = CPU(img)
        c.r[3], c.r[4], c.r[5] = 0xFEDFA000, GP, TP
        buf = 0xFEDF8000
        for i, x in enumerate(v):
            c.ram[buf + i] = x
        c.r[6], c.r[7] = buf, len(v)          # FUN_00092698(buf, len) ; len is zero-extended to 8 bits
        c.r[31] = 0x80000                       # return landing
        c.land = {0x80000}
        c.run(0x92698)
        got = c.r[10] & 0xFFFFFFFF
        exp = nvm_cksum(v, len(v))
        if got != exp:
            bad += 1
            if bad < 5:
                print('MISMATCH len %d emu %08x port %08x' % (len(v), got, exp))
    print('FUN_00092698 emulation vs port: %d vectors, %d mismatches' % (len(vecs), bad))
    # record helpers
    blank = b'\xff' * 0x18
    assert rec_ok(blank)
    r = beo_patch_slot(blank, 27, 19, 2)
    assert rec_ok(r) and beo_decode(r[0x10:0x14])[:3] == (27, 19, 2)
    assert beo_decode(b'\xff\xff\xff\xff') == BEO_DEFAULT + (False,)
    assert beo_decode(b'\0\0\0\0') == BEO_DEFAULT + (False,)
    print('record helpers OK')
    return bad == 0


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'selftest'
    if cmd == 'selftest':
        sys.exit(0 if selftest() else 1)
    elif cmd == 'decode':
        decode(sys.argv[2])
    elif cmd == 'sites':
        sites()
    elif cmd == 'slot':
        x, y, st = (int(a, 0) for a in sys.argv[2:5])
        print('ext bytes +0x10..+0x13:', beo_encode(x, y, st).hex(' '))
        if '--slot-bytes' in sys.argv:
            rec = bytes.fromhex(sys.argv[sys.argv.index('--slot-bytes') + 1])
            print('patched record:', beo_patch_slot(rec, x, y, st).hex(' '))
    else:
        print(__doc__)
