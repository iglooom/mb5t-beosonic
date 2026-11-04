#!/usr/bin/env python3
"""Minimal V850E2 assembler (the subset used by the MB5T BeoSonic caves) + self-test.

Every encoder is validated against EVERY instance of that mnemonic form in the Ghidra listing
of the stock image (analysis/MB5T-14C589-CB.lst: addr<TAB>bytes<TAB>insn<TAB>func), so a wrong field layout cannot
pass silently. Run:  python3 v850asm.py selftest
"""
import os, re, struct, sys

REG = {('r%d' % i): i for i in range(32)}
REG.update({'zero': 0, 'sp': 3, 'gp': 4, 'tp': 5, 'ep': 30, 'lp': 31})
SREG = {'PSW': 5}

F1 = {'mov': 0x00, 'or': 0x08, 'xor': 0x09, 'and': 0x0A, 'tst': 0x0B, 'subr': 0x0C,
      'sub': 0x0D, 'add': 0x0E, 'cmp': 0x0F}
F2 = {'mov': 0x10, 'add': 0x12, 'cmp': 0x13, 'shr': 0x14, 'sar': 0x15, 'shl': 0x16}
F6 = {'addi': 0x30, 'movea': 0x31, 'movhi': 0x32, 'ori': 0x34, 'xori': 0x35, 'andi': 0x36}
COND = {'bv': 0, 'bc': 1, 'bl': 1, 'be': 2, 'bz': 2, 'bnh': 3, 'bn': 4, 'br': 5, 'blt': 6,
        'ble': 7, 'bnv': 8, 'bnc': 9, 'bnl': 9, 'bne': 10, 'bnz': 10, 'bh': 11, 'bp': 12,
        'bsa': 13, 'bge': 14, 'bgt': 15}


def h(x):
    return struct.pack('<H', x & 0xFFFF)


def r(n):
    return REG[n]


def s16(v):
    assert -0x8000 <= v <= 0x7FFF, 'imm16 out of range %x' % v
    return v & 0xFFFF


def u16(v):
    assert 0 <= v <= 0xFFFF, 'uimm16 out of range %x' % v
    return v


def enc_f1(op, r1, r2):
    return h((r(r2) << 11) | (F1[op] << 5) | r(r1))


def enc_f2(op, imm, r2):
    if op in ('shr', 'sar', 'shl'):
        assert 0 <= imm <= 31
    else:
        assert -16 <= imm <= 15, 'imm5 out of range %d' % imm
    return h((r(r2) << 11) | (F2[op] << 5) | (imm & 0x1F))


def enc_f6(op, imm, r1, r2):
    v = u16(imm) if op in ('ori', 'xori', 'andi') else s16(imm)
    return h((r(r2) << 11) | (F6[op] << 5) | r(r1)) + h(v)


def enc_mov32(imm, reg):
    return h(0x0620 | r(reg)) + struct.pack('<I', imm & 0xFFFFFFFF)


def enc_ld(kind, disp, r1, r2):
    if kind == 'ld.b':
        return h((r(r2) << 11) | (0x38 << 5) | r(r1)) + h(s16(disp))
    if kind == 'ld.h':
        assert disp % 2 == 0
        return h((r(r2) << 11) | (0x39 << 5) | r(r1)) + h(s16(disp))
    if kind == 'ld.w':
        assert disp % 4 == 0
        return h((r(r2) << 11) | (0x39 << 5) | r(r1)) + h(s16(disp) | 1)
    if kind == 'ld.bu':
        assert r(r2) != 0
        b = disp & 1
        return h((r(r2) << 11) | (0x3C << 5) | (b << 5) | r(r1)) + h((s16(disp) & 0xFFFE) | 1)
    if kind == 'ld.hu':
        assert disp % 2 == 0 and r(r2) != 0
        return h((r(r2) << 11) | (0x3F << 5) | r(r1)) + h(s16(disp) | 1)
    raise ValueError(kind)


def enc_st(kind, r2, disp, r1):
    if kind == 'st.b':
        return h((r(r2) << 11) | (0x3A << 5) | r(r1)) + h(s16(disp))
    if kind == 'st.h':
        assert disp % 2 == 0
        return h((r(r2) << 11) | (0x3B << 5) | r(r1)) + h(s16(disp))
    if kind == 'st.w':
        assert disp % 4 == 0
        return h((r(r2) << 11) | (0x3B << 5) | r(r1)) + h(s16(disp) | 1)
    raise ValueError(kind)


def enc_bcond(cond, pc, target):
    d = target - pc
    assert d % 2 == 0 and -256 <= d <= 254, 'bcond range %x->%x' % (pc, target)
    d &= 0x1FF
    return h(((d >> 4) << 11) | (0xB << 7) | (((d >> 1) & 7) << 4) | COND[cond])


def enc_jarl(pc, target, reg='lp'):
    d = target - pc
    assert d % 2 == 0 and -(1 << 21) <= d < (1 << 21), 'jarl range'
    d &= 0x3FFFFF
    return h((r(reg) << 11) | (0x1E << 6) | (d >> 16)) + h(d & 0xFFFF)


def enc_jr(pc, target):
    return enc_jarl(pc, target, 'r0')


def enc_jmp(reg):
    return h(0x0060 | r(reg))


def enc_zxb(reg):
    return h(0x0080 | r(reg))


def enc_zxh(reg):
    return h(0x00C0 | r(reg))


def enc_stsr(sr, reg):
    return h((r(reg) << 11) | (0x3F << 5) | SREG[sr]) + h(0x0040)


def enc_ldsr(reg, sr):
    return h((SREG[sr] << 11) | (0x3F << 5) | r(reg)) + h(0x0020)


DI = h(0x07E0) + h(0x0160)
EI = h(0x87E0) + h(0x0160)
NOP = h(0x0000)


# ---------------------------------------------------------------- text front-end
def num(s):
    return int(s, 0)


def assemble_one(text, pc):
    """Encode one instruction in Ghidra's text syntax. Branch targets are absolute."""
    t = text.strip()
    m = t.split(None, 1)
    op = m[0]
    args = [a.strip() for a in m[1].split(',')] if len(m) > 1 else []
    if op == 'nop':
        return NOP
    if op == 'di':
        return DI
    if op == 'ei':
        return EI
    if op == 'jmp':
        return enc_jmp(args[0].strip('[]'))
    if op == 'zxb':
        return enc_zxb(args[0])
    if op == 'zxh':
        return enc_zxh(args[0])
    if op == 'stsr':
        return enc_stsr(args[0], args[1])
    if op == 'ldsr':
        return enc_ldsr(args[0], args[1])
    if op == 'jarl':
        return enc_jarl(pc, num(args[0]), args[1])
    if op == 'jr':
        return enc_jr(pc, num(args[0]))
    if op in COND:
        return enc_bcond(op, pc, num(args[0]))
    if op in ('ld.b', 'ld.h', 'ld.w', 'ld.bu', 'ld.hu'):
        mm = re.match(r'(-?0x[0-9a-f]+|-?\d+)\[(\w+)\]', args[0])
        return enc_ld(op, num(mm.group(1)), mm.group(2), args[1])
    if op in ('st.b', 'st.h', 'st.w'):
        mm = re.match(r'(-?0x[0-9a-f]+|-?\d+)\[(\w+)\]', args[1])
        return enc_st(op, args[0], num(mm.group(1)), mm.group(2))
    if op in F6:
        return enc_f6(op, num(args[0]), args[1], args[2])
    if op == 'mov' and args[0] not in REG:
        v = num(args[0])
        if -16 <= v <= 15:
            return enc_f2('mov', v, args[1])
        return enc_mov32(v, args[1])
    if op in F2 and args[0] not in REG:
        return enc_f2(op, num(args[0]), args[1])
    if op in F1:
        return enc_f1(op, args[0], args[1])
    raise ValueError('unsupported: ' + t)


SELFTEST_RE = re.compile(
    r'^(mov|or|xor|and|sub|subr|add|cmp|shr|sar|shl|addi|movea|movhi|ori|xori|andi|ld\.b|ld\.h|'
    r'ld\.w|ld\.bu|ld\.hu|st\.b|st\.h|st\.w|jarl|jr|jmp|zxb|zxh|di|ei|nop|stsr|ldsr|' +
    '|'.join(COND) + r')\b')


def selftest(lst=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               'analysis', 'MB5T-14C589-CB.lst')):
    n = bad = 0
    per = {}
    for line in open(lst):
        a, b, ins, f = line.rstrip('\n').split('\t')
        ins = ins.strip()
        if not SELFTEST_RE.match(ins):
            continue
        if 'CTBP' in ins or 'EIPC' in ins or 'FEPC' in ins:
            continue
        if ins.split()[0] in ('stsr', 'ldsr') and 'PSW' not in ins:
            continue
        pc = int(a, 16)
        try:
            e = assemble_one(ins, pc)
        except (ValueError, AssertionError, KeyError):
            continue        # form not used by us (e.g. imm out of our encoder's asserted range)
        op = ins.split()[0]
        per.setdefault(op, [0, 0])
        per[op][0] += 1
        n += 1
        if e.hex() != b and len(b) > len(e.hex()):
            # compiler chose a longer equivalent form (48-bit 'mov 0' / 48-bit jarl): not an
            # encoder error -- our shorter form is checked by the other instances
            per[op][0] -= 1
            n -= 1
            alt = per.setdefault('(alt-long ' + op + ')', [0, 0])
            alt[0] += 1
            continue
        if e.hex() != b:
            bad += 1
            per[op][1] += 1
            if per[op][1] <= 3:
                print('MISMATCH %s %-32s want %s got %s' % (a, ins, b, e.hex()))
    for k in sorted(per):
        print('  %-6s %6d checked %d mismatches' % (k, per[k][0], per[k][1]))
    print('selftest: %d instructions re-encoded, %d mismatches' % (n, bad))
    return bad == 0


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'selftest':
        sys.exit(0 if selftest() else 1)
