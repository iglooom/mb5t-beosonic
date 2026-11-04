#!/usr/bin/env python3
"""Build the BeoSonic firmware for the Explorer U625 Harman amp app MB5T-14C589-CB.

  python3 tools/build.py     -> release/MB5T-14C589-CB_BeoSonic.bin + manifest + listings
  python3 tools/build_vbf.py -> release/MB5T-14C589-CB_BeoSonic.VBF (UCDS-flashable)

Full description: docs/TECHNICAL.md. In short:
  RX    hook in the accept-all mailbox path keeps SYNC's BeoSonic request frame 0x105.
  TICK  1 ms CAN-task hook: enable/X/Y logic, pad LUTs -> weights c/d, DSP refresh, 0x22D
        status to SYNC from the unused AFCAN mailbox 1, import/snapshot of the saved state.
  EB/EM/ET  tone engine FUN_0008b92a: while on, the Bass/Mid/Treble RAM records carry the
        Kuga 3 BeoSonic filter records (LS 60 Hz / level +-2 dB / HS 7.5 kHz) and are driven
        with c / 100-c / d; originals restored when off.
  LEQ   after every EQ preset (re)load: records are stock again.
  LOADH / CMPH  persistence in the EEPROM audio slot record (+0x10..+0x13) through the stock
        save routine FUN_00097ffc.
Internal checksums 0xFFBFC / 0xFFEAC are recomputed with mb5t_cksum.fix().
"""
import hashlib, json, os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import v850asm as A               # noqa: E402
import mb5t_cksum as CK           # noqa: E402
import eq_table as EQ             # noqa: E402
import paths as P                 # noqa: E402

STOCK, BASE, GP = P.STOCK_BIN, 0x80000, 0xFEDF9620
KUGA_EQ = P.KUGA_EQ
KUGA_PRESET = '2_SurrOFF_CX482_6ch_LHD_MP_Final.EQF'
LST = P.LISTING
OUTD = P.RELEASE

# ---------------------------------------------------------------- ROM layout (free 0040 fill)
FREE_LO, FREE_HI = 0xC0DB0, 0xFFBFC
CAVE = 0xC1000
TPL = 0xC2000                    # 3 x 0x3C native records (mask 1, 2, 4)
LUTC = 0xC2100                   # 961 B: c (0..100) or 0xFF = outside the pad
LUTD = 0xC2500                   # 961 B: d (0..100)

# ---------------------------------------------------------------- RAM (block 0xFEDFF000)
VAR = 0xFEDFF000                 # above .data/.bss + libc words, unreferenced by the stock app
D = VAR - GP                     # gp displacement 0x59E0
RXW, ON, CX, CY, BB, BT, PEND, TXNOW, LASTFLAT = (D + 0, D + 4, D + 5, D + 6, D + 7, D + 8,
                                                  D + 9, D + 10, D + 11)
TXCNT, DSPON, DSPB, DSPT, TRIG = D + 12, D + 14, D + 16, D + 17, D + 18
SNAP = D + 0x14                  # u32 record bytes X|Y<<8|st<<16|chk<<24
SNAPM = D + 0x18                 # u32 == SNAPMAGIC once SNAP is valid (garbage-proof)
LOADSEQ, LASTSEQ, IMPORTED, CHG = D + 0x1C, D + 0x1D, D + 0x1E, D + 0x1F
SWAPPED = D + 0x20               # u32 == SWMAGIC: RAM records hold the BeoSonic templates
                                 #   (then EB/EM/ET return the weights)
SAVEBUF = D + 0x40               # 3 x 0x3C originals (mask 1, 2, 4)
RAM_END = SAVEBUF + 3 * 0x3C
MAGIC, SNAPMAGIC, SWMAGIC = 0x5A3C, 0x5A3CB5A5, 0x5A3C0001   # DSPON 'on' / SNAP valid / swapped
CHK_X = 0xB5                     # chk = X ^ Y ^ st ^ 0xB5 (nvm_tool.beo_encode)
assert RAM_END + GP <= 0xFEDFFFF0

# ---------------------------------------------------------------- OEM addresses / RAM
F_ADFEE, F_A70D6, F_8C7CC, L_AB896 = 0xADFEE, 0xA70D6, 0x8C7CC, 0xAB896
F_LOADER, F_APPLY = 0x8B27E, 0x93C8C
G_FRAME_DLC, G_B5, G_B6, G_B7 = 0xA0C, 0xA09, 0xA0A, 0xA0B   # RX frame image gp+0xA04
G_FLAT = -0x299E                 # app 'tone flat/locked' flag (1 = app forces 7/7/7)
G_APPBASS = -0x2941              # app Bass value (0..14, 0xF = never set)
G_DSPBASS = -0x92C               # DSP tone ctx (gp-0x934) + 8
G_DSPQ, G_DSPPRIO = -0x7C78, -0x7C5F
G_TXEN, G_TXDIS = 0x9EE, 0x9EF
CAN_STAT = 0xFF48024C              # bit4 set -> controller not usable (stock TX gate)
MB1_CTRL = 0xFF489078              # AFCAN mailbox 1 MCTRL (0xFF489038 + 0x40*m)
MB1_DATA = 0xFF481040              # mailbox 1 data0 (bytes at +4*i), DLC +0x20, MCONF +0x24
TXID = 0x22D << 2
PERIOD = 1000                      # 0x22D period in 1 ms ticks
G_NVLOADED = -0x2947             # audio NVM record loaded (FUN_000988ce)
G_REC10 = -0x2964                # RAM audio record (gp-0x2974) + 0x10, word aligned
G_ARM, G_ARMT = -0x7BE0, -0x7BE4  # FUN_00097ffc debounce flag / time
G_TICK = -0x6580                 # OS tick word (FUN_000ba90a)
G_HDR = -0x2914                  # replayed operand of the CMPH site
C_CNT, C_ARR = 0x2C, 0x4         # tone ctx: record count (u16), record array ptr
C_SHB, C_SHM, C_SHT = 0x2E, 0x2F, 0x30   # shadows Bass/Mid/Treble

HOOKS = [  # addr, stock bytes (asserted), replacement
    (0xAB84A, 'd0e6fc1f', ('jarl', 'RX')),       # FUN_000ab81e: andi 0x1ffc,r16,r28
    (0xA739A, '80ff546c', ('jarl', 'TICK')),     # CAN task: jarl FUN_000adfee (1 ms branch)
    (0xA72F8, 'bfffdefd', ('jarl', 'INIT')),     # CAN task start: jarl FUN_000a70d6
    (0x8B93A, '063f0800', ('jarl', 'EB')),       # FUN_0008b92a: ld.b 0x8[r6],r7
    (0x8B956, '1df02f9b093b', ('jarl+nop', 'EM')),
    (0x8B970, '1df030930a3b', ('jarl+nop', 'ET')),
    (0x9CBB6, 'beffc8e6', ('jarl', 'LEQ')),     # FUN_0009c8e0: jarl FUN_0008b27e
    (0x988FC, 'bfff90b3', ('jarl', 'LOADH')),   # FUN_000988ce: jarl FUN_00093c8c
    (0x98134, '240fedd6', ('jarl', 'CMPH')),    # FUN_00097ffc: ld.w -0x2914[gp],r1
]


def src():
    # RX and INIT bodies are v1 + new vars; TICK = v1 + import/snapshot; EB/EM/ET new.
    return f"""
RX:
  andi 0x1ffc,r16,r28
  movea 0x414,r0,r1
  cmp r1,r28
  bne @RX_ret
  ld.bu {G_FRAME_DLC:#x}[gp],r17
  cmp 0x8,r17
  bl @RX_drop
  ld.bu {G_B5:#x}[gp],r17
  andi 0x3,r17,r17
  shl 0x10,r17
  ld.bu {G_B6:#x}[gp],r18
  shl 0x8,r18
  or r18,r17
  ld.bu {G_B7:#x}[gp],r18
  or r18,r17
  cmp r0,r17
  be @RX_drop
  movhi 0x100,r0,r18
  or r18,r17
  st.w r17,{RXW:#x}[gp]
RX_drop:
  jr {L_AB896:#x}
RX_ret:
  jmp [lp]

INIT:
  st.w r0,{RXW:#x}[gp]
  st.b r0,{ON:#x}[gp]
  movea 0x10,r0,r1
  st.b r1,{CX:#x}[gp]
  st.b r1,{CY:#x}[gp]
  movea 0x32,r0,r1
  st.b r1,{BB:#x}[gp]
  st.b r1,{BT:#x}[gp]
  st.b r1,{DSPB:#x}[gp]
  st.b r1,{DSPT:#x}[gp]
  mov 0x7,r1
  st.b r1,{TRIG:#x}[gp]
  st.h r0,{DSPON:#x}[gp]
  st.b r0,{PEND:#x}[gp]
  mov 0x1,r1
  st.b r1,{TXNOW:#x}[gp]
  st.h r0,{TXCNT:#x}[gp]
  ld.bu {G_FLAT:#x}[gp],r1
  st.b r1,{LASTFLAT:#x}[gp]
  st.w r0,{SNAP:#x}[gp]
  st.w r0,{SNAPM:#x}[gp]
  st.b r0,{IMPORTED:#x}[gp]
  st.b r0,{CHG:#x}[gp]
  jr {F_A70D6:#x}

TICK:
  add -0x10,sp
  st.w lp,0x8[sp]
  jarl {F_ADFEE:#x},lp
; ---- import the persisted state once the OEM record is loaded / after every reload
  ld.bu {G_NVLOADED:#x}[gp],r11
  cmp 0x1,r11
  bne @T_rx
  ld.bu {LOADSEQ:#x}[gp],r11
  ld.bu {IMPORTED:#x}[gp],r12
  cmp r0,r12
  be @T_imp
  ld.bu {LASTSEQ:#x}[gp],r12
  cmp r11,r12
  be @T_rx
T_imp:
  st.b r11,{LASTSEQ:#x}[gp]
  mov 0x1,r11
  st.b r11,{IMPORTED:#x}[gp]
  ld.w {G_REC10:#x}[gp],r10
  andi 0xff,r10,r13
  mov r10,r14
  shr 0x8,r14
  andi 0xff,r14,r14
  mov r10,r15
  shr 0x10,r15
  andi 0xff,r15,r15
  mov r10,r16
  shr 0x18,r16
  mov r13,r17
  xor r14,r17
  xor r15,r17
  movea {CHK_X:#x},r0,r18
  xor r18,r17
  cmp r16,r17
  bne @T_idef
  addi -0x1,r15,r18
  cmp 0x1,r18
  bh @T_idef
  addi -0x1,r13,r18
  addi -0x1f,r18,r0
  bc @T_idef
  addi -0x1,r14,r19
  addi -0x1f,r19,r0
  bc @T_idef
  mov r19,r17
  shl 0x5,r17
  sub r19,r17
  add r18,r17
  mov {LUTC:#x},r12
  add r17,r12
  ld.bu 0x0[r12],r12
  addi -0xff,r12,r0
  bne @T_iset
T_idef:
  movea 0x10,r0,r13
  movea 0x10,r0,r14
  mov 0x1,r15
T_iset:
  st.b r13,{CX:#x}[gp]
  st.b r14,{CY:#x}[gp]
  addi -0x2,r15,r0
  be @T_ion
  st.b r0,{ON:#x}[gp]
  br @T_ixy
T_ion:
  mov 0x1,r11
  st.b r11,{ON:#x}[gp]
T_ixy:
  addi -0x1,r13,r18
  addi -0x1,r14,r19
  mov r19,r17
  shl 0x5,r17
  sub r19,r17
  add r18,r17
  mov {LUTC:#x},r12
  add r17,r12
  ld.bu 0x0[r12],r12
  st.b r12,{BB:#x}[gp]
  mov {LUTD:#x},r12
  add r17,r12
  ld.bu 0x0[r12],r12
  st.b r12,{BT:#x}[gp]
  mov 0x1,r11
  st.b r11,{PEND:#x}[gp]
  st.b r11,{TXNOW:#x}[gp]
  jarl @T_snap,lp
; ---- frame handling (v1 semantics)
T_rx:
  stsr PSW,r19
  di
  ld.w {RXW:#x}[gp],r10
  st.w r0,{RXW:#x}[gp]
  ldsr r19,PSW
  cmp r0,r10
  be @T_norx
  mov 0x1,r11
  st.b r11,{TXNOW:#x}[gp]
  ld.bu {ON:#x}[gp],r12
  mov r10,r11
  shr 0x10,r11
  andi 0x3,r11,r11
  cmp 0x2,r11
  bne @T_en1
  cmp r0,r12
  bne @T_xy
  mov 0x1,r12
  br @T_enset
T_en1:
  cmp 0x1,r11
  bne @T_xy
  cmp r0,r12
  be @T_xy
  mov 0x0,r12
T_enset:
  st.b r12,{ON:#x}[gp]
  mov 0x1,r11
  st.b r11,{PEND:#x}[gp]
  st.b r11,{CHG:#x}[gp]
T_xy:
  cmp r0,r12
  be @T_norx
  mov r10,r13
  shr 0x8,r13
  andi 0xff,r13,r13
  andi 0xff,r10,r14
  addi -0x1,r13,r15
  addi -0x1f,r15,r0
  bc @T_norx
  addi -0x1,r14,r16
  addi -0x1f,r16,r0
  bc @T_norx
  mov r16,r17
  shl 0x5,r17
  sub r16,r17
  add r15,r17
  mov {LUTC:#x},r18
  add r17,r18
  ld.bu 0x0[r18],r19
  addi -0xff,r19,r0
  be @T_norx
  ld.bu {CX:#x}[gp],r11
  cmp r13,r11
  bne @T_acc
  ld.bu {CY:#x}[gp],r11
  cmp r14,r11
  be @T_norx
T_acc:
  st.b r13,{CX:#x}[gp]
  st.b r14,{CY:#x}[gp]
  st.b r19,{BB:#x}[gp]
  mov {LUTD:#x},r18
  add r17,r18
  ld.bu 0x0[r18],r19
  st.b r19,{BT:#x}[gp]
  mov 0x1,r11
  st.b r11,{PEND:#x}[gp]
  st.b r11,{CHG:#x}[gp]
T_norx:
; ---- accepted change -> snapshot + arm the OEM save debounce (only after the import)
  ld.bu {CHG:#x}[gp],r11
  cmp r0,r11
  be @T_flat
  st.b r0,{CHG:#x}[gp]
  ld.bu {IMPORTED:#x}[gp],r11
  cmp r0,r11
  be @T_flat
  jarl @T_snap,lp
  ld.w {G_TICK:#x}[gp],r11
  st.w r11,{G_ARMT:#x}[gp]
  mov 0x1,r11
  st.b r11,{G_ARM:#x}[gp]
T_flat:
  ld.bu {G_FLAT:#x}[gp],r11
  ld.bu {LASTFLAT:#x}[gp],r12
  cmp r11,r12
  be @T_dsp
  st.b r11,{LASTFLAT:#x}[gp]
  mov 0x1,r12
  st.b r12,{PEND:#x}[gp]
T_dsp:
  ld.bu {PEND:#x}[gp],r12
  cmp r0,r12
  be @T_tx
  cmp 0x1,r11
  be @T_tx
  ld.w {G_DSPQ:#x}[gp],r6
  cmp r0,r6
  be @T_tx
  ld.bu {ON:#x}[gp],r12
  cmp r0,r12
  be @T_puboff
  ld.bu {BB:#x}[gp],r13
  st.b r13,{DSPB:#x}[gp]
  ld.bu {BT:#x}[gp],r13
  st.b r13,{DSPT:#x}[gp]
  movea {MAGIC:#x},r0,r13
  st.h r13,{DSPON:#x}[gp]
  br @T_post
T_puboff:
  st.h r0,{DSPON:#x}[gp]
T_post:
  ld.bu {G_APPBASS:#x}[gp],r13
  cmp 0xe,r13
  bnh @T_trigok
  ld.bu {G_DSPBASS:#x}[gp],r13
T_trigok:
  st.b r13,{TRIG:#x}[gp]
  st.b r0,{PEND:#x}[gp]
  movea {TRIG:#x},gp,r13
  st.w r13,0x0[sp]
  mov 0x1,r13
  st.w r13,0x4[sp]
  mov 0x0,r7
  ld.bu {G_DSPPRIO:#x}[gp],r8
  movea 0x1011,r0,r9
  jarl {F_8C7CC:#x},lp
T_tx:
  ld.hu {TXCNT:#x}[gp],r11
  cmp r0,r11
  be @T_send
  add -0x1,r11
  st.h r11,{TXCNT:#x}[gp]
  ld.bu {TXNOW:#x}[gp],r12
  cmp r0,r12
  be @T_done
T_send:
  ld.bu {G_TXEN:#x}[gp],r12
  andi 0x1,r12,r0
  be @T_done
  ld.bu {G_TXDIS:#x}[gp],r12
  andi 0x1,r12,r0
  bne @T_done
  mov {CAN_STAT:#x},r13
  ld.bu 0x0[r13],r12
  andi 0x10,r12,r0
  bne @T_done
  mov {MB1_CTRL:#x},r13
  ld.hu 0x0[r13],r12
  andi 0x2,r12,r0
  bne @T_done
  mov 0x1,r12
  st.h r12,0x0[r13]
  movea 0x40,r0,r14
T_wait:
  ld.hu 0x0[r13],r12
  andi 0x1,r12,r0
  be @T_rdy0
  add -0x1,r14
  bne @T_wait
  br @T_done
T_rdy0:
  movea 0x5e,r0,r12
  st.h r12,0x0[r13]
  mov {MB1_DATA:#x},r15
  mov 0x1,r12
  st.b r12,0x24[r15]
  movea {TXID:#x},r0,r12
  st.h r12,-0x8[r13]
  mov 0x8,r12
  st.b r12,0x20[r15]
  ld.bu {CX:#x}[gp],r12
  st.b r12,0x0[r15]
  ld.bu {CY:#x}[gp],r12
  st.b r12,0x4[r15]
  ld.bu {ON:#x}[gp],r12
  movea 0x40,r0,r16
  cmp r0,r12
  be @T_b2
  movea 0x80,r0,r16
T_b2:
  st.b r16,0x8[r15]
  st.b r0,0xc[r15]
  st.b r0,0x10[r15]
  st.b r0,0x14[r15]
  st.b r0,0x18[r15]
  st.b r0,0x1c[r15]
  movea 0x100,r0,r12
  st.h r12,0x0[r13]
  movea 0x200,r0,r12
  st.h r12,0x0[r13]
  st.b r0,{TXNOW:#x}[gp]
  movea {PERIOD:#x},r0,r12
  st.h r12,{TXCNT:#x}[gp]
T_done:
  ld.w 0x8[sp],lp
  addi 0x10,sp,sp
  jmp [lp]

; SNAP <- encode(CX, CY, ON); local subroutine (lp = return), uses r12..r16
T_snap:
  ld.bu {CX:#x}[gp],r12
  ld.bu {CY:#x}[gp],r13
  ld.bu {ON:#x}[gp],r14
  mov 0x1,r15
  cmp r0,r14
  be @S_st
  mov 0x2,r15
S_st:
  mov r12,r16
  xor r13,r16
  xor r15,r16
  movea {CHK_X:#x},r0,r14
  xor r14,r16
  shl 0x18,r16
  shl 0x10,r15
  or r15,r16
  shl 0x8,r13
  or r13,r16
  or r12,r16
  st.w r16,{SNAP:#x}[gp]
  mov {SNAPMAGIC:#x},r16
  st.w r16,{SNAPM:#x}[gp]
  jmp [lp]

; ---- DSP task, start of FUN_0008b92a (r6 = r29 = tone ctx, r28 = force)
EB:
  ld.b 0x8[r6],r7
  mov 0x0,r8
  ld.h {DSPON:#x}[gp],r1
  movea {MAGIC:#x},r0,r10
  cmp r10,r1
  bne @EB_w
  ld.bu {G_FLAT:#x}[gp],r1
  cmp 0x1,r1
  be @EB_w
  mov 0x1,r8
EB_w:
  ld.w {SWAPPED:#x}[gp],r9
  mov {SWMAGIC:#x},r10
  mov 0x0,r1
  cmp r10,r9
  bne @EB_sw
  mov 0x1,r1
EB_sw:
  cmp r8,r1
  bne @EB_chg
  jr @EB_val
EB_chg:
  ld.w {C_ARR:#x}[r6],r11
  ld.hu {C_CNT:#x}[r6],r12
  cmp r0,r8
  bne @EB_on
  jr @EB_rest
EB_on:
; -- precheck: masks 1, 2, 4 each exactly once among the records
  mov r11,r13
  mov r12,r14
  mov 0x0,r15
EB_pc:
  cmp r0,r14
  be @EB_pcd
  ld.w 0x0[r13],r16
  addi -0x1,r16,r17
  cmp 0x3,r17
  bh @EB_pcn
  cmp 0x2,r17
  be @EB_pcn
  mov r16,r17
  and r15,r17
  bne @EB_fail
  or r16,r15
EB_pcn:
  addi 0x3c,r13,r13
  add -0x1,r14
  br @EB_pc
EB_pcd:
  cmp 0x7,r15
  bne @EB_fail
; -- swap in: originals -> SAVEBUF[k], template[k] -> record (keep words +8/+0xC)
  mov r11,r13
  mov r12,r14
EB_sl:
  cmp r0,r14
  be @EB_swd
  ld.w 0x0[r13],r16
  addi -0x1,r16,r17
  cmp 0x3,r17
  bh @EB_sn
  cmp 0x2,r17
  be @EB_sn
  shr 0x1,r16
  mov r16,r17
  shl 0x6,r17
  shl 0x2,r16
  sub r16,r17
  movea {SAVEBUF:#x},gp,r18
  add r17,r18
  mov {TPL:#x},r19
  add r17,r19
  mov r13,r16
  movea 0xf,r0,r17
EB_c1:
  ld.w 0x0[r16],r10
  st.w r10,0x0[r18]
  ld.w 0x0[r19],r10
  st.w r10,0x0[r16]
  addi 0x4,r16,r16
  addi 0x4,r18,r18
  addi 0x4,r19,r19
  add -0x1,r17
  bne @EB_c1
  ld.w -0x34[r18],r10
  st.w r10,0x8[r13]
  ld.w -0x30[r18],r10
  st.w r10,0xc[r13]
EB_sn:
  addi 0x3c,r13,r13
  add -0x1,r14
  br @EB_sl
EB_swd:
  mov {SWMAGIC:#x},r10
  st.w r10,{SWAPPED:#x}[gp]
  br @EB_inv
EB_fail:
  st.w r0,{SWAPPED:#x}[gp]
  jr @EB_val
; -- restore: SAVEBUF[k] -> record
EB_rest:
  mov r11,r13
  mov r12,r14
EB_rl:
  cmp r0,r14
  be @EB_rd
  ld.w 0x0[r13],r16
  addi -0x1,r16,r17
  cmp 0x3,r17
  bh @EB_rn
  cmp 0x2,r17
  be @EB_rn
  shr 0x1,r16
  mov r16,r17
  shl 0x6,r17
  shl 0x2,r16
  sub r16,r17
  movea {SAVEBUF:#x},gp,r18
  add r17,r18
  mov r13,r16
  movea 0xf,r0,r17
EB_c2:
  ld.w 0x0[r18],r10
  st.w r10,0x0[r16]
  addi 0x4,r16,r16
  addi 0x4,r18,r18
  add -0x1,r17
  bne @EB_c2
EB_rn:
  addi 0x3c,r13,r13
  add -0x1,r14
  br @EB_rl
EB_rd:
  st.w r0,{SWAPPED:#x}[gp]
EB_inv:
  movea 0x7f,r0,r10
  st.b r10,{C_SHB:#x}[r6]
  st.b r10,{C_SHM:#x}[r6]
  st.b r10,{C_SHT:#x}[r6]
EB_val:
  ld.w {SWAPPED:#x}[gp],r9
  mov {SWMAGIC:#x},r10
  cmp r10,r9
  bne @EB_ret
  ld.bu {DSPB:#x}[gp],r1
  movea 0x64,r0,r10
  cmp r10,r1
  bh @EB_ret
  mov r1,r7
EB_ret:
  jmp [lp]

EM:
  mov r29,ep
  ld.b 0x2f[r29],r19
  ld.b 0x9[r29],r7
  ld.w {SWAPPED:#x}[gp],r1
  mov {SWMAGIC:#x},r10
  cmp r10,r1
  bne @EM_ret
  ld.bu {DSPB:#x}[gp],r1
  movea 0x64,r0,r10
  cmp r10,r1
  bh @EM_ret
  sub r1,r10
  mov r10,r7
EM_ret:
  jmp [lp]

ET:
  mov r29,ep
  ld.b 0x30[r29],r18
  ld.b 0xa[r29],r7
  ld.w {SWAPPED:#x}[gp],r1
  mov {SWMAGIC:#x},r10
  cmp r10,r1
  bne @ET_ret
  ld.bu {DSPT:#x}[gp],r1
  movea 0x64,r0,r10
  cmp r10,r1
  bh @ET_ret
  mov r1,r7
ET_ret:
  jmp [lp]

; ---- DSP task: after every record (re)load the RAM records are the stock ones again
LEQ:
  add -0x8,sp
  st.w lp,0x0[sp]
  jarl {F_LOADER:#x},lp
  st.w r0,{SWAPPED:#x}[gp]
  ld.w 0x0[sp],lp
  addi 0x8,sp,sp
  jmp [lp]

; ---- app task: after every audio slot load (FUN_000988ce)
LOADH:
  add -0x8,sp
  st.w lp,0x0[sp]
  jarl {F_APPLY:#x},lp
  st.w r0,{SNAPM:#x}[gp]
  ld.bu {LOADSEQ:#x}[gp],r1
  addi 0x1,r1,r1
  st.b r1,{LOADSEQ:#x}[gp]
  ld.w 0x0[sp],lp
  addi 0x8,sp,sp
  jmp [lp]

; ---- app/system task, record compare in FUN_00097ffc (r29 = 'slot record changed')
CMPH:
  ld.w {SNAPM:#x}[gp],r1
  mov {SNAPMAGIC:#x},r2
  cmp r2,r1
  bne @CM_out
  ld.w {SNAP:#x}[gp],r1
  ld.w {G_REC10:#x}[gp],r2
  cmp r1,r2
  be @CM_out
  st.w r1,{G_REC10:#x}[gp]
  mov 0x1,r29
CM_out:
  ld.w {G_HDR:#x}[gp],r1
  jmp [lp]
"""


def assemble(text, org):
    """Two-pass assembler over v850asm.assemble_one; '@label' = absolute label address."""
    lines = []
    for ln in text.splitlines():
        ln = ln.split(';')[0].strip()
        if ln:
            lines.append(ln)

    def run(labels):
        pc, out, labs, listing = org, bytearray(), {}, []
        for ln in lines:
            if ln.endswith(':'):
                labs[ln[:-1]] = pc
                continue
            t = ln
            if '@' in t:
                name = t.split('@')[1].split(',')[0].strip()
                t = t.replace('@' + name, hex(labels.get(name, pc)))
            b = A.assemble_one(t, pc)
            listing.append((pc, b.hex(), t))
            out += b
            pc += len(b)
        return out, labs, listing
    _, labs, _ = run({})
    code, labs2, listing = run(labs)
    assert labs == labs2, 'label drift between passes'
    return bytes(code), labs, listing



# ---------------------------------------------------------------- data
def kuga_records():
    """Raw BE 0x38-byte BeoSonic records of the Kuga 3 tune preset, by mask."""
    d = open(KUGA_EQ, 'rb').read()
    for base in range(0, 0x67A00, 0x2800):
        size = struct.unpack_from('<I', d, base)[0]
        if not 8 <= size < 0x2800 or EQ.name_of(d, base, size) != KUGA_PRESET:
            continue
        recs = {}
        for t, v in EQ.tlvs(d, base, size):
            if t == 0x61:
                recs[EQ.rec61(v)['mask']] = bytes(v[:0x38])
        return recs
    raise SystemExit('Kuga preset not found')


def native(raw, mask):
    """0x38 BE file record -> 0x3C native RAM entry, exactly like FUN_0008b27e (+ mask swap)."""
    assert len(raw) == 0x38
    n = bytearray(0x3C)
    n[0:0x16] = raw[0:0x16]
    n[0x18:0x3A] = raw[0x16:0x38]
    for o in (0, 4, 8, 0xC, 0x10, 0x18, 0x1C, 0x20, 0x24):
        n[o:o + 4] = n[o:o + 4][::-1]
    for o in [0x14] + [0x28 + 2 * i for i in range(4)] + [0x30 + 2 * i for i in range(5)]:
        n[o:o + 2] = n[o:o + 2][::-1]
    struct.pack_into('<I', n, 0, mask)
    return bytes(n)


def templates():
    rk = kuga_records()
    pick = [(1, 0x10000), (2, 0x4000), (4, 0x8000)]   # Bass<-LS, Mid<-level, Treble<-HS
    out = b''
    for mask, src_mask in pick:
        r = EQ.rec61(rk[src_mask])
        assert abs(EQ.gain_db(r, 50)) < 0.02 and EQ.gain_db(r, 0) < -1.9 and EQ.gain_db(r, 100) > 1.9
        out += native(rk[src_mask], mask)
    return out, {hex(s): EQ.rec61(rk[s]) for _, s in pick}


def build_luts():
    import beosonic_model as M         # PJ6T pad tables T1..T4 (inputs/PJ6T app)
    lc, ld = bytearray(961), bytearray(961)
    for yr in range(1, 32):
        for xr in range(1, 32):
            i = 31 * (yr - 1) + (xr - 1)
            w = M.xy_to_weights(xr, yr)
            if w is None:
                lc[i], ld[i] = 0xFF, 0xFF
                continue
            c, d = w['w_0x10000'], w['w_0x8000']
            assert 0 <= c <= 100 and 0 <= d <= 100
            lc[i], ld[i] = c, d
    assert lc[31 * 15 + 15] == 50 and ld[31 * 15 + 15] == 50      # neutral
    assert (lc[31 * 4 + 15], ld[31 * 4 + 15]) == (87, 13)        # Lounge (15,4)
    return bytes(lc), bytes(ld)


def branch_targets():
    """All direct branch/jump targets in the stock listing (to reject hooks on their interior)."""
    import re
    t = set()
    for ln in open(LST):
        p = ln.split('\t')
        if len(p) >= 3:
            m = re.match(r'(b\w+|jr|jarl)\s+0x([0-9a-f]+)', p[2])
            if m:
                t.add(int(m.group(2), 16))
    return t


def main():
    img = bytearray(open(STOCK, 'rb').read())
    stock = bytes(img)
    sha = hashlib.sha256(stock).hexdigest()
    assert CK.app_word(stock) == CK.rd(stock, CK.APP_SUM_AT)
    code, labs, listing = assemble(src(), CAVE)
    tpl, tinfo = templates()
    lc, ld = build_luts()
    assert CAVE + len(code) <= TPL and TPL + len(tpl) <= LUTC and LUTC + 961 <= LUTD
    assert LUTD + 961 <= FREE_HI and CAVE >= FREE_LO
    for a, data in ((CAVE, code), (TPL, tpl), (LUTC, lc), (LUTD, ld)):
        o = a - BASE
        assert img[o:o + len(data)] == (b'\x00\x40' * len(data))[:len(data)], 'not free at %x' % a
        img[o:o + len(data)] = data
    bt = branch_targets()
    edits = []
    for addr, oem, (kind, lab) in HOOKS:
        o = addr - BASE
        n = len(oem) // 2
        assert img[o:o + n].hex() == oem, 'hook %x: OEM bytes differ' % addr
        assert not any(addr + k in bt for k in range(2, n, 2)), 'hook %x: interior is a target' % addr
        new = A.enc_jarl(addr, labs[lab], 'lp') + (A.NOP if kind == 'jarl+nop' else b'')
        assert len(new) == n
        img[o:o + n] = new
        edits.append(dict(addr=addr, oem=oem, new=new.hex(), target=labs[lab], label=lab))
    out, w1, w2 = CK.fix(stock, bytes(img))
    os.makedirs(OUTD, exist_ok=True)
    pbin = P.OUT_BIN
    open(pbin, 'wb').write(out)
    clusters, i, n = [], 0, len(out)
    while i < n:
        if out[i] != stock[i]:
            j = i
            while j < n and (out[j] != stock[j] or (j + 1 < n and out[j + 1] != stock[j + 1])
                             or (j + 2 < n and out[j + 2] != stock[j + 2])):
                j += 1
            clusters.append((BASE + i, stock[i:j].hex(), out[i:j].hex()))
            i = j
        else:
            i += 1
    man = dict(stock=os.path.relpath(STOCK, P.ROOT), stock_sha256=sha, out=os.path.relpath(pbin, P.ROOT),
               out_sha256=hashlib.sha256(out).hexdigest(), cave=hex(CAVE), cave_len=len(code),
               tpl=hex(TPL), lutc=hex(LUTC), lutd=hex(LUTD), kuga_eq=os.path.relpath(KUGA_EQ, P.ROOT), kuga_preset=KUGA_PRESET,
               templates={k: dict(type=v['type'], f=round(v['f'], 1), q=round(v['q'], 3), bp=v['bp'])
                          for k, v in tinfo.items()},
               labels={k: hex(v) for k, v in labs.items()}, ram=hex(GP + D), ram_end=hex(GP + RAM_END),
               hooks=edits, cksum={'0xFFBFC': '%08X' % w1, '0xFFEAC': '%08X' % w2},
               clusters=[dict(addr=hex(a), len=len(o) // 2) for a, o, _ in clusters])
    json.dump(man, open(P.MANIFEST, 'w'), indent=1)
    with open(P.CAVE_LISTING, 'w') as f:
        inv = {v: k for k, v in labs.items()}
        for pc, b, t in listing:
            if pc in inv:
                f.write('%s:\n' % inv[pc])
            f.write('  %06x  %-12s %s\n' % (pc, b, t))
    with open(P.PATCH_ARGS, 'w') as f:
        for a, o, nw in clusters:
            f.write('--expect 0x%X=%s --set 0x%X=%s\n' % (a, o, a, nw))
    print('stock sha256', sha)
    print('cave %x..%x (%d B); TPL %x; LUTC %x; LUTD %x; RAM %x..%x' % (
        CAVE, CAVE + len(code), len(code), TPL, LUTC, LUTD, GP + D, GP + RAM_END))
    print('templates', man['templates'])
    print('0xFFBFC=%08X 0xFFEAC=%08X' % (w1, w2))
    print('%d clusters:' % len(clusters))
    for a, o, nw in clusters:
        print('  0x%06X %4d B' % (a, len(o) // 2))


if __name__ == '__main__':
    main()
