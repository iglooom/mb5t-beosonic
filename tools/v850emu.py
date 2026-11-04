#!/usr/bin/env python3
"""Tiny V850E2 interpreter for the instruction subset used by the BeoSonic caves.

The decoder is written from the V850E2 format tables (bit fields), independently of the
encoder in v850asm.py, so emulating the EMITTED bytes tests the encoder as well as the logic.
Unknown opcodes raise; the runner stops at explicit 'landing' addresses and returns the PC.
"""
import struct

M32 = 0xFFFFFFFF


def sx(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


class Halt(Exception):
    pass


class CPU:
    def __init__(self, rom, rom_base=0x80000):
        self.rom, self.rb = rom, rom_base
        self.r = [0] * 32
        self.ram = {}               # byte-addressed
        self.mmio_w = []            # (addr, size, value) log
        self.mmio_r = {}            # addr -> callable(size) or value
        self.mmio_wh = {}           # addr -> callable(size, value)
        self.Z = self.S = self.OV = self.CY = 0
        self.psw_id = 0
        self.stubs = {}             # addr -> fn(cpu); returns via lp
        self.land = set()           # stop when pc in land
        self.pc = 0
        self.steps = 0
        self.code_lo = self.code_hi = None

    # ---------------------------------------------------------------- memory
    def is_mmio(self, a):
        return 0xFF000000 <= a <= 0xFFFFFFFF and not (0xFEDF0000 <= a < 0xFEE00000)

    def rd(self, a, n):
        a &= M32
        if self.rb <= a < self.rb + len(self.rom):
            return int.from_bytes(self.rom[a - self.rb:a - self.rb + n], 'little')
        if 0xFEDF0000 <= a < 0xFEE00000:
            return int.from_bytes(bytes(self.ram.get(a + i, 0) for i in range(n)), 'little')
        if self.is_mmio(a):
            f = self.mmio_r.get(a)
            if f is None:
                raise Halt('unmodelled MMIO read %08x' % a)
            return f(n) if callable(f) else f
        raise Halt('read outside model %08x' % a)

    def wr(self, a, n, v):
        a &= M32
        v &= (1 << (8 * n)) - 1
        if 0xFEDF0000 <= a < 0xFEE00000:
            for i in range(n):
                self.ram[a + i] = (v >> (8 * i)) & 0xFF
            return
        if self.is_mmio(a):
            self.mmio_w.append((a, n, v))
            f = self.mmio_wh.get(a)
            if f:
                f(n, v)
            return
        raise Halt('write outside RAM/MMIO %08x (ROM write?)' % a)

    def h(self, a):
        return self.rd(a, 2)

    # ---------------------------------------------------------------- flags
    def setzs(self, v):
        v &= M32
        self.Z = int(v == 0)
        self.S = v >> 31
        return v

    def addf(self, a, b):
        res = (a + b) & M32
        self.CY = int(a + b > M32)
        self.OV = int(((a ^ res) & (b ^ res)) >> 31 & 1)
        return self.setzs(res)

    def subf(self, a, b):          # a - b
        res = (a - b) & M32
        self.CY = int(b > a)
        self.OV = int(((a ^ b) & (a ^ res)) >> 31 & 1)
        return self.setzs(res)

    def cond(self, c):
        Z, S, OV, CY = self.Z, self.S, self.OV, self.CY
        return [OV, CY, Z, CY | Z, S, 1, S ^ OV, (S ^ OV) | Z,
                1 - OV, 1 - CY, 1 - Z, 1 - (CY | Z), 1 - S, 0, 1 - (S ^ OV), 1 - ((S ^ OV) | Z)][c]

    # ---------------------------------------------------------------- exec
    def setr(self, n, v):
        if n:
            self.r[n] = v & M32

    def step(self):
        pc = self.pc
        if pc in self.land:
            raise StopIteration(pc)
        if pc in self.stubs:
            self.stubs[pc](self)
            self.pc = self.r[31]
            return
        if self.code_lo is not None and not (self.code_lo <= pc < self.code_hi):
            raise Halt('pc %x left the modelled code and is not a stub/landing' % pc)
        self.steps += 1
        if self.steps > 100000:
            raise Halt('step limit')
        w = self.h(pc)
        r1, r2 = w & 0x1F, (w >> 11) & 0x1F
        op6 = (w >> 5) & 0x3F
        R = self.r
        # 48-bit mov imm32: 0000 0110 001r rrrr
        if (w & 0xFFE0) == 0x0620:
            imm = self.rd(pc + 2, 4)
            self.setr(r1, imm)
            self.pc = pc + 6
            return
        if w == 0x0000:                               # nop
            self.pc = pc + 2
            return
        if op6 < 0x10:                                # format I
            if op6 == 0x00:                           # mov
                self.setr(r2, R[r1])
            elif op6 == 0x03 and r2 == 0:             # jmp [r1]
                self.pc = R[r1]
                return
            elif op6 == 0x04 and r2 == 0:             # zxb
                self.setr(r1, R[r1] & 0xFF)
            elif op6 == 0x06 and r2 == 0:             # zxh
                self.setr(r1, R[r1] & 0xFFFF)
            elif op6 == 0x08:
                self.setr(r2, self.setzs(R[r2] | R[r1])); self.OV = 0
            elif op6 == 0x09:
                self.setr(r2, self.setzs(R[r2] ^ R[r1])); self.OV = 0
            elif op6 == 0x0A:
                self.setr(r2, self.setzs(R[r2] & R[r1])); self.OV = 0
            elif op6 == 0x0D:                         # sub r1,r2 : r2 = r2 - r1
                self.setr(r2, self.subf(R[r2], R[r1]))
            elif op6 == 0x0E:
                self.setr(r2, self.addf(R[r2], R[r1]))
            elif op6 == 0x0F:                         # cmp r1,r2 : r2 - r1
                self.subf(R[r2], R[r1])
            else:
                raise Halt('fmt I op %x at %x' % (op6, pc))
            self.pc = pc + 2
            return
        if op6 < 0x18:                                # format II
            imm = r1
            if op6 == 0x10:
                self.setr(r2, sx(imm, 5))
            elif op6 == 0x12:
                self.setr(r2, self.addf(R[r2], sx(imm, 5) & M32))
            elif op6 == 0x13:
                self.subf(R[r2], sx(imm, 5) & M32)
            elif op6 == 0x14:                         # shr
                v = R[r2]
                self.CY = (v >> (imm - 1)) & 1 if imm else 0
                self.setr(r2, self.setzs(v >> imm)); self.OV = 0
            elif op6 == 0x16:                         # shl
                v = R[r2]
                self.CY = (v >> (32 - imm)) & 1 if imm else 0
                self.setr(r2, self.setzs(v << imm)); self.OV = 0
            else:
                raise Halt('fmt II op %x at %x' % (op6, pc))
            self.pc = pc + 2
            return
        if (w >> 7) & 0xF == 0xB:                     # format III bcond
            d = sx((((w >> 11) & 0x1F) << 4) | (((w >> 4) & 7) << 1), 9)
            self.pc = (pc + d) & M32 if self.cond(w & 0xF) else pc + 2
            return
        w2 = self.h(pc + 2)
        if (w >> 6) & 0x1F == 0x1E and op6 in (0x3C, 0x3D) and r2 != 0 and (w2 & 1):
            # ld.bu disp16[r1],r2 : bit5 of w = disp bit0
            disp = sx((w2 & 0xFFFE) | ((w >> 5) & 1), 16)
            self.setr(r2, self.rd(R[r1] + disp, 1))
            self.pc = pc + 4
            return
        if (w >> 6) & 0x1F == 0x1E:                   # format V jarl/jr
            disp = sx(((w & 0x3F) << 16) | w2, 22)
            if disp & 1:
                raise Halt('odd jarl disp')
            self.setr(r2, pc + 4)
            self.pc = (pc + disp) & M32
            return
        if op6 == 0x3F and r2 != 0 and (w2 & 1) and w2 != 0x0020 and w2 != 0x0040:
            # ld.hu disp16[r1],r2
            self.setr(r2, self.rd(R[r1] + sx(w2 & 0xFFFE, 16), 2))
            self.pc = pc + 4
            return
        if op6 == 0x3F:                               # format IX/X system
            if w == 0x07E0 and w2 == 0x0160:
                self.di = 1
            elif w == 0x87E0 and w2 == 0x0160:
                self.di = 0
            elif w2 == 0x0040 and r1 == 5:            # stsr PSW,r2
                self.setr(r2, (self.Z | self.S << 1 | self.OV << 2 | self.CY << 3 |
                               (getattr(self, 'di', 0) << 5)))
            elif w2 == 0x0020 and r2 == 5:            # ldsr r1,PSW
                v = R[r1]
                self.Z, self.S, self.OV, self.CY = v & 1, v >> 1 & 1, v >> 2 & 1, v >> 3 & 1
                self.di = v >> 5 & 1
            else:
                raise Halt('sys %04x %04x at %x' % (w, w2, pc))
            self.pc = pc + 4
            return
        imm = w2
        if op6 == 0x30:
            self.setr(r2, self.addf(R[r1], sx(imm, 16) & M32))
        elif op6 == 0x31:
            self.setr(r2, R[r1] + sx(imm, 16))
        elif op6 == 0x32:
            self.setr(r2, R[r1] + (imm << 16))
        elif op6 == 0x34:
            self.setr(r2, self.setzs(R[r1] | imm)); self.OV = 0
        elif op6 == 0x36:
            self.setr(r2, self.setzs(R[r1] & imm)); self.OV = 0
        elif op6 == 0x38:
            self.setr(r2, sx(self.rd(R[r1] + sx(imm, 16), 1), 8))
        elif op6 == 0x39:
            if imm & 1:
                self.setr(r2, self.rd(R[r1] + sx(imm & 0xFFFE, 16), 4))
            else:
                self.setr(r2, sx(self.rd(R[r1] + sx(imm, 16), 2), 16))
        elif op6 == 0x3A:
            self.wr(R[r1] + sx(imm, 16), 1, R[r2])
        elif op6 == 0x3B:
            if imm & 1:
                self.wr(R[r1] + sx(imm & 0xFFFE, 16), 4, R[r2])
            else:
                self.wr(R[r1] + sx(imm, 16), 2, R[r2])
        else:
            raise Halt('op6 %x at %x (w=%04x)' % (op6, pc, w))
        self.pc = pc + 4

    def run(self, pc):
        self.pc = pc
        self.steps = 0
        try:
            while True:
                self.step()
        except StopIteration as e:
            return e.args[0]
