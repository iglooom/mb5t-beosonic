#!/usr/bin/env python3
"""End-to-end persistence check: run the STOCK save routine FUN_00097ffc (text-exec, tone_emu.CPU)
with the CMPH hook in place, and capture the EEPROM write it issues (FUN_0008c3ea stubbed).

Cases: periodic mode 2 (armed, > 5 s), mode 2 not yet due, forced flush mode 3, NVM not loaded,
SNAP equal to the record (no write), SNAP invalid magic, stock image (never writes our bytes),
tone change + BeoSonic change together. Each written slot must carry the BeoSonic bytes and a
checksum that the stock validity rule (nvm_tool.rec_ok, port of FUN_000937b4) accepts.

  python3 tools/test_persist.py
"""
import os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tone_emu as T              # noqa: E402
import build as B                 # noqa: E402
import nvm_tool as NV             # noqa: E402

GP = B.GP
REC = GP - 0x2974                 # RAM audio slot record (0x18 B)
HDRR = GP - 0x2914                # RAM header record
fails = 0
LIVE = {-0x28F8: 1, -0x2943: 7, -0x2942: 7, -0x2941: 9, -0x2940: 7, -0x293F: 5, -0x2992: 3,
        -0x293D: 6, -0x293C: 0x12, -0x293B: 0x0C, -0x293A: 0x0A}


def check(c, msg):
    global fails
    if not c:
        fails += 1
        print('FAIL', msg)


def mk(img, beo_rec, snap, snapm, loaded=1, armed=1, armt=0, tone_changed=False):
    c = T.CPU(img)
    c.code_lo = None
    c.r[3], c.r[4], c.r[5] = T.SP0, GP, 0x89080
    rec = bytearray(0x18)
    struct.pack_into('<I', rec, 0, 41)
    for off, (d, v) in zip(range(4, 0xF), sorted(LIVE.items(), key=lambda kv: 0)):
        pass
    # record mirrors live values (no tone change) unless tone_changed
    for i, (off, name, src) in enumerate(NV.SLOT_FIELDS):
        if src is not None:
            rec[off] = LIVE[src]
    rec[0x10:0x14] = beo_rec
    struct.pack_into('<I', rec, 0x14, NV.nvm_cksum(bytes(rec), 0x14))
    for k, b in enumerate(rec):
        c.ram[REC + k] = b
    for d, v in LIVE.items():
        c.wr(GP + d, 1, v)
    if tone_changed:
        c.wr(GP - 0x2941, 1, 12)
    # header record equal to its live sources so only the slot can be written
    hdr = bytearray(0x18)
    for k, b in enumerate(hdr):
        c.ram[HDRR + k] = b
    c.wr(GP - 0x2900, 4, NV.nvm_cksum(bytes(hdr), 0x14))
    for d_hdr, d_live in ((-0x2907, -0x2944), (-0x2910, -0x7C5B), (-0x2904, -0x2996),
                          (-0x2908, -0x28F6), (-0x2905, -0x294C), (-0x2906, -0x294B)):
        c.wr(GP + d_live, 1, c.rd(GP + d_hdr, 1))
    c.wr(GP - 0x29F4, 4, c.rd(GP - 0x290C, 4))
    c.wr(GP - 0x2947, 1, loaded)
    c.wr(GP + B.G_ARM, 1, armed)
    c.wr(GP + B.G_ARMT, 4, armt)
    c.wr(GP + B.SNAP, 4, int.from_bytes(snap, 'little'))
    c.wr(GP + B.SNAPM, 4, snapm)
    for d in (-0x29B1, -0x29AF, -0x29AE, -0x29B0):          # level limits (0x1E = no limit)
        c.wr(GP + d, 1, 0x1E)
    writes = []

    def vwrite(cpu):
        dst, n = cpu.r[7], cpu.r[8]
        writes.append((dst, bytes(cpu.rd(cpu.r[6] + k, 1) for k in range(n))))
        cpu.r[10] = 0

    def zero(cpu):
        cpu.r[10] = 0
    c.stubs = {0x8C3EA: vwrite, 0xA904E: zero, 0x97DA4: zero}
    return c, writes


def run(c, mode, now, p3=0):
    c.r[6], c.r[7], c.r[8], c.r[31] = mode, now, p3, 0xDEAD8
    c.land = {0xDEAD8}
    c.run(0x97FFC)


def main():
    old = NV.beo_encode(16, 16, 1)
    new = NV.beo_encode(28, 20, 2)
    cases = [
        # name, img, mode, now, armt, loaded, armed, snapm, snap, tone, expect_write, expect_bytes
        ('periodic due', T.PIMG, 2, 6000, 0, 1, 1, B.SNAPMAGIC, new, False, True, new),
        ('periodic not due', T.PIMG, 2, 4000, 0, 1, 1, B.SNAPMAGIC, new, False, False, None),
        ('forced flush', T.PIMG, 3, 10, 0, 1, 0, B.SNAPMAGIC, new, False, True, new),
        ('not loaded', T.PIMG, 3, 10, 0, 0, 1, B.SNAPMAGIC, new, False, False, None),
        ('snap == record', T.PIMG, 3, 10, 0, 1, 1, B.SNAPMAGIC, old, False, False, None),
        ('snap magic bad', T.PIMG, 3, 10, 0, 1, 1, 0x12345678, new, False, False, None),
        ('tone + beo', T.PIMG, 3, 10, 0, 1, 1, B.SNAPMAGIC, new, True, True, new),
        ('tone only', T.PIMG, 3, 10, 0, 1, 1, B.SNAPMAGIC, old, True, True, old),
        ('stock img', T.SIMG, 3, 10, 0, 1, 1, B.SNAPMAGIC, new, False, False, None),
        ('stock tone', T.SIMG, 3, 10, 0, 1, 1, B.SNAPMAGIC, new, True, True, old),
    ]
    for name, img, mode, now, armt, ld, armed, sm, snap, tone, ew, eb in cases:
        c, writes = mk(img, old, snap, sm, loaded=ld, armed=armed, armt=armt, tone_changed=tone)
        c.wr(GP - 0x2905, 1, 2)                     # active slot 2 (header +0x0F)
        c.wr(GP - 0x294C, 1, 2)
        run(c, mode, now)
        slot = [w for w in writes if 0x3200820 <= w[0] < 0x3200820 + 5 * 0x20]
        other = [w for w in writes if w not in slot]
        check(not other, '%s: unexpected writes %s' % (name, [hex(w[0]) for w in other]))
        if not ew:
            check(not slot, '%s: wrote %s' % (name, slot))
            print('%-18s no write  OK' % name if not slot else '')
            continue
        check(len(slot) == 1 and slot[0][0] == 0x3200820 + 2 * 0x20 and len(slot[0][1]) == 0x18,
              '%s: slot write %s' % (name, [(hex(a), len(d)) for a, d in slot]))
        if not slot:
            continue
        d = slot[0][1]
        check(d[0x10:0x14] == eb, '%s: beo bytes %s want %s' % (name, d[0x10:0x14].hex(), eb.hex()))
        check(NV.rec_ok(d), '%s: OEM validity rule rejects the written record' % name)
        check(struct.unpack_from('<I', d, 0)[0] == 42, '%s: counter' % name)
        check(d[7] == (12 if tone else 9), '%s: Bass byte' % name)
        x, y, st, ok = NV.beo_decode(d[0x10:0x14])
        print('%-18s wrote slot 2: X=%d Y=%d st=%d valid=%s cksum OK=%s' % (name, x, y, st, ok, NV.rec_ok(d)))
    print('persist FAILURES', fails)
    return fails


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
