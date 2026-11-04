#!/usr/bin/env python3
"""vbftool - parse / verify / extract / merge / patch / diff Volvo-Ford VBF containers.

Pure stdlib. Big-endian. No writes unless the subcommand says so.

Container:  ASCII `vbf_version = X.Y;` + `header { ... }`  then, with NO separator,
            repeated binary block records  [start:u32][len:u32][data:len][crc16:u16].
  crc16 = CRC-16/CCITT-FALSE over the UNCOMPRESSED data.
  header file_checksum = zlib.crc32 over everything from the first block's start field to EOF.
  data_format_identifier = 0x10 -> block data is LZSS(EI=10,EJ=4,P=1) compressed.

Subcommands:
  info      <vbf...>                       header fields + block table
  verify    <vbf...>                       CRC-16 per block + file CRC-32 + trailing-byte check
  extract   <vbf> [-o DIR]                 write each block to DIR (decompressed if DFI 0x10)
  merge     <vbf...> -o IMG [--lo A --hi B]  flat 0xFF-filled image at load addresses
  sum8      <vbf> --at ADDR [--start A] [--end E]   read/compute an internal additive checksum
  patch     <vbf> -o OUT --set ADDR=HEX [--set ...] [--expect ADDR=HEX ...]
                        [--sum8-at ADDR --sum8-start A --sum8-end E]
            edit flat-address bytes in place, then repair sum8 -> block CRC16 -> file CRC32
  diff      <a.vbf> <b.vbf>                clustered byte diff of the merged images
  findsum   <a.vbf> <b.vbf> --at ADDR      brute-force (algo,range) reproducing the stored
                                           word in BOTH images (internal-checksum hunt)
"""
import argparse, binascii, os, re, struct, sys, zlib

# ---------------------------------------------------------------- container

def header_end(d):
    """Byte offset just past the header's matching '}'. Brace-matched, not line-based."""
    i = d.find(b"header")
    if i < 0:
        raise ValueError("no 'header' keyword")
    j = d.find(b"{", i)
    if j < 0:
        raise ValueError("no '{' after 'header'")
    depth = 0
    while j < len(d):
        if d[j] == 0x7B:
            depth += 1
        elif d[j] == 0x7D:
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    raise ValueError("unterminated header")


def _walk(d, ds):
    blocks, p = [], ds
    while p + 8 <= len(d):
        a, l = struct.unpack(">II", d[p:p + 8])
        if l == 0 or p + 8 + l + 2 > len(d):
            break
        blocks.append(dict(start=a, length=l, dataoff=p + 8, crcoff=p + 8 + l,
                           data=d[p + 8:p + 8 + l],
                           crc16=struct.unpack(">H", d[p + 8 + l:p + 10 + l])[0]))
        p += 8 + l + 2
    return blocks, p


def parse(path):
    """Returns dict(raw, header, fields, data_start, blocks, trailing).

    data_start is chosen so the block walk consumes the file exactly; the header's
    '}' is normally the last byte before the blocks (NO whitespace separator), but a
    few Hexview variants insert one space. Probing a few offsets and requiring a
    clean walk avoids both the 'skip all whitespace' bug (eats a 0x20 length byte)
    and the 'assume zero gap' bug.
    """
    d = open(path, "rb").read()
    he = header_end(d)
    hdr = d[:he].decode("latin1")
    best = None
    for ds in range(he, min(he + 8, len(d))):
        blocks, end = _walk(d, ds)
        if blocks and end == len(d):
            best = (ds, blocks, 0)
            break
        if best is None and blocks:
            best = (ds, blocks, len(d) - end)
    if best is None:
        best = (he, [], len(d) - he)
    ds, blocks, trailing = best
    return dict(path=path, raw=d, header=hdr, fields=parse_header(hdr),
                data_start=ds, blocks=blocks, trailing=trailing)


def parse_header(h):
    f = {}
    for k in ("vbf_version", "sw_part_number", "sw_part_type", "network",
              "frame_format", "can_frame_format", "description"):
        m = re.search(k + r"\s*=\s*([^;]+);", h, re.S)
        if m:
            f[k] = " ".join(m.group(1).replace('"', " ").split())
    for k in ("ecu_address", "call", "file_checksum", "data_format_identifier"):
        m = re.search(k + r"\s*=\s*0x([0-9A-Fa-f]+)", h)
        if m:
            f[k] = int(m.group(1), 16)
    for k in ("erase", "omit"):
        m = re.search(k + r"\s*=\s*\{(.*?)\};", h, re.S)
        if m:
            n = [int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]+)", m.group(1))]
            f[k] = list(zip(n[0::2], n[1::2]))
    f["signed"] = "sw_signature" in h
    return f


def crc16(d):
    return binascii.crc_hqx(bytes(d), 0xFFFF)          # CRC-16/CCITT-FALSE


def file_crc32(raw, data_start):
    return zlib.crc32(raw[data_start:]) & 0xFFFFFFFF

# ---------------------------------------------------------------- LZSS (DFI 0x10)

_EI, _EJ, _P = 10, 4, 1
_N, _F = 1 << _EI, (1 << _EJ) + _P


def lzss_decode(c):
    out = bytearray(); buf = bytearray(b" " * _N); r = 0
    idx = 0; mask = 0; cur = 0

    def bits(n):
        nonlocal idx, mask, cur
        x = 0
        for _ in range(n):
            if mask == 0:
                if idx >= len(c):
                    return None
                cur = c[idx]; idx += 1; mask = 128
            x = (x << 1) | (1 if cur & mask else 0)
            mask >>= 1
        return x

    while True:
        b = bits(1)
        if b is None:
            break
        if b:
            ch = bits(8)
            if ch is None:
                break
            out.append(ch); buf[r] = ch; r = (r + 1) & (_N - 1)
        else:
            i = bits(_EI); j = bits(_EJ)
            if i is None or j is None or i == 0:
                break
            i -= 1
            for k in range(j + 2):
                ch = buf[(i + k) & (_N - 1)]
                out.append(ch); buf[r] = ch; r = (r + 1) & (_N - 1)
    return bytes(out)


def block_payload(v, b):
    """Uncompressed bytes of a block (what the crc16 covers and what gets flashed)."""
    if v["fields"].get("data_format_identifier") == 0x10:
        return lzss_decode(b["data"])
    return b["data"]

# ---------------------------------------------------------------- merged image


def merge(vbfs, lo=None, hi=None):
    segs = []
    for v in vbfs:
        for b in v["blocks"]:
            segs.append((b["start"], block_payload(v, b)))
    if not segs:
        raise SystemExit("no blocks")
    if lo is None:
        lo = min(a for a, _ in segs)
    if hi is None:
        hi = max(a + len(x) for a, x in segs)
    img = bytearray(b"\xFF" * (hi - lo))
    for a, x in segs:
        if a + len(x) <= lo or a >= hi:
            continue
        img[a - lo:a - lo + len(x)] = x[:hi - a]
    return img, lo

# ---------------------------------------------------------------- commands


def _hx(n):
    return int(n, 16) if str(n).lower().startswith("0x") else int(n, 0)


def cmd_info(a):
    for p in a.vbf:
        v = parse(p)
        f = v["fields"]
        print("=== %s  (%d bytes)" % (os.path.basename(p), len(v["raw"])))
        for k in ("vbf_version", "sw_part_number", "sw_part_type", "network",
                  "frame_format", "can_frame_format", "description"):
            if k in f:
                print("   %-24s %s" % (k, f[k]))
        for k in ("ecu_address", "call", "data_format_identifier", "file_checksum"):
            if k in f:
                print("   %-24s 0x%X" % (k, f[k]))
        if f["signed"]:
            print("   %-24s yes (sw_signature present - rebuild INVALIDATES it)" % "signed")
        for k in ("erase", "omit"):
            if k in f:
                print("   %-24s %d region(s)" % (k, len(f[k])))
                for s, l in f[k]:
                    print("        0x%08X len 0x%08X" % (s, l))
        print("   data_start 0x%X  blocks %d  trailing %d"
              % (v["data_start"], len(v["blocks"]), v["trailing"]))
        for i, b in enumerate(v["blocks"]):
            u = block_payload(v, b)
            extra = "" if len(u) == b["length"] else "  -> unpacked 0x%X" % len(u)
            print("     blk%-2d load=0x%08X len=0x%08X crc16=0x%04X%s"
                  % (i, b["start"], b["length"], b["crc16"], extra))


def cmd_verify(a):
    bad = 0
    for p in a.vbf:
        v = parse(p)
        ok = True
        for i, b in enumerate(v["blocks"]):
            c = crc16(block_payload(v, b))
            if c != b["crc16"]:
                ok = False
                print("  blk%d 0x%08X crc16 stored=0x%04X calc=0x%04X FAIL"
                      % (i, b["start"], b["crc16"], c))
        fc = file_crc32(v["raw"], v["data_start"])
        hf = v["fields"].get("file_checksum")
        if hf != fc:
            ok = False
            print("  file_checksum header=0x%08X calc=0x%08X FAIL" % (hf or 0, fc))
        if v["trailing"]:
            ok = False
            print("  %d trailing bytes after last block - parse or file is wrong" % v["trailing"])
        if not v["blocks"]:
            ok = False
            print("  no blocks parsed")
        print("%-46s %s  (%d blocks)"
              % (os.path.basename(p), "OK" if ok else "FAIL", len(v["blocks"])))
        bad += 0 if ok else 1
    return 1 if bad else 0


def cmd_extract(a):
    v = parse(a.vbf)
    out = a.o or "."
    os.makedirs(out, exist_ok=True)
    base = os.path.splitext(os.path.basename(a.vbf))[0]
    for i, b in enumerate(v["blocks"]):
        u = block_payload(v, b)
        fn = os.path.join(out, "%s_blk%d_0x%08X.bin" % (base, i, b["start"]))
        open(fn, "wb").write(u)
        print("  %s  (0x%X bytes)" % (fn, len(u)))


def cmd_merge(a):
    img, lo = merge([parse(p) for p in a.vbf],
                    _hx(a.lo) if a.lo else None, _hx(a.hi) if a.hi else None)
    open(a.o, "wb").write(img)
    print("wrote %s  base=0x%08X size=0x%X" % (a.o, lo, len(img)))


def _sum8(img, lo, start, end):
    return sum(img[start - lo:end - lo]) & 0xFFFF


def cmd_sum8(a):
    v = parse(a.vbf)
    img, lo = merge([v], _hx(a.start) if a.start else None, _hx(a.end_img) if a.end_img else None)
    at = _hx(a.at)
    start = _hx(a.start) if a.start else lo
    end = _hx(a.end) if a.end else at
    stored = struct.unpack_from(">H", img, at - lo)[0]
    calc = _sum8(img, lo, start, end)
    print("sum8[0x%06X..0x%06X) @0x%06X stored=0x%04X calc=0x%04X %s"
          % (start, end, at, stored, calc, "OK" if stored == calc else "MISMATCH"))
    return 0 if stored == calc else 1


def cmd_patch(a):
    v = parse(a.vbf)
    d = bytearray(v["raw"])
    if v["fields"].get("data_format_identifier") == 0x10:
        raise SystemExit("refusing: compressed container (DFI 0x10); recompression not implemented")
    if v["fields"]["signed"]:
        print("WARNING: container carries sw_signature - any edit invalidates it", file=sys.stderr)

    def blk_for(addr):
        for b in v["blocks"]:
            if b["start"] <= addr < b["start"] + b["length"]:
                return b
        raise SystemExit("address 0x%X is in no block" % addr)

    def fo(addr):
        b = blk_for(addr)
        return b["dataoff"] + (addr - b["start"])

    for e in (a.expect or []):
        addr, hx = e.split("=")
        addr = _hx(addr); want = bytes.fromhex(hx)
        got = bytes(d[fo(addr):fo(addr) + len(want)])
        if got != want:
            raise SystemExit("expect FAIL @0x%X want %s got %s"
                             % (addr, want.hex(), got.hex()))
        print("  expect OK  0x%08X %s" % (addr, want.hex()))
    for s in (a.set or []):
        addr, hx = s.split("=")
        addr = _hx(addr); new = bytes.fromhex(hx)
        o = fo(addr)
        d[o:o + len(new)] = new
        print("  set        0x%08X %dB" % (addr, len(new)))

    # layer 1: internal additive checksum, computed on the MERGED image
    if a.sum8_at:
        tmp = dict(v); tmp["raw"] = bytes(d)
        tmp["blocks"] = [dict(b, data=bytes(d[b["dataoff"]:b["dataoff"] + b["length"]]))
                         for b in v["blocks"]]
        img, lo = merge([tmp], _hx(a.sum8_start) if a.sum8_start else None,
                        _hx(a.sum8_img_end) if a.sum8_img_end else None)
        at = _hx(a.sum8_at)
        st = _hx(a.sum8_start) if a.sum8_start else lo
        en = _hx(a.sum8_end) if a.sum8_end else at
        old = struct.unpack_from(">H", d, fo(at))[0]
        new = _sum8(img, lo, st, en)
        struct.pack_into(">H", d, fo(at), new)
        print("  sum8       0x%04X -> 0x%04X  [0x%06X..0x%06X)" % (old, new, st, en))
    # layer 2: per-block CRC-16
    for b in v["blocks"]:
        d[b["crcoff"]:b["crcoff"] + 2] = struct.pack(
            ">H", crc16(d[b["dataoff"]:b["dataoff"] + b["length"]]))
    # layer 3: file CRC-32 (same field width - never reformat the header)
    m = re.search(rb"file_checksum\s*=\s*0x([0-9A-Fa-f]+)", bytes(d[:v["data_start"]]))
    fc = zlib.crc32(bytes(d[v["data_start"]:])) & 0xFFFFFFFF
    w = len(m.group(1))
    d[m.start(1):m.end(1)] = ("%0*X" % (w, fc)).encode()
    open(a.o, "wb").write(d)
    print("  file_crc32 -> 0x%08X" % fc)
    print("WROTE %s (%d bytes)" % (a.o, len(d)))
    r = parse(a.o)
    assert r["trailing"] == 0 and len(r["blocks"]) == len(v["blocks"])
    ns = argparse.Namespace(vbf=[a.o])
    return cmd_verify(ns)


def cmd_diff(a):
    va, vb = parse(a.a), parse(a.b)
    ia, la = merge([va]); ib, lb = merge([vb])
    if la != lb or len(ia) != len(ib):
        print("images differ in extent: 0x%X+0x%X vs 0x%X+0x%X" % (la, len(ia), lb, len(ib)))
    n = min(len(ia), len(ib))
    runs = []
    i = 0
    while i < n:
        if ia[i] != ib[i]:
            j = i
            while j < n and (ia[j] != ib[j] or any(ia[k] != ib[k] for k in range(j, min(n, j + 16)))):
                j += 1
            runs.append((i, j)); i = j
        else:
            i += 1
    print("%d differing cluster(s), %d bytes total"
          % (len(runs), sum(sum(1 for k in range(s, e) if ia[k] != ib[k]) for s, e in runs)))
    for s, e in runs[:200]:
        print("  0x%06X..0x%06X  (%d B)  %s -> %s"
              % (la + s, la + e, e - s, ia[s:min(e, s + 12)].hex(), ib[s:min(e, s + 12)].hex()))


def cmd_findsum(a):
    """Find (algo, range) whose result equals the stored word in BOTH images.
    Two OEM versions of the same part are the instrument: one match is a coincidence."""
    va, vb = parse(a.a), parse(a.b)
    ia, lo = merge([va]); ib, _ = merge([vb])
    at = _hx(a.at)
    ta = struct.unpack_from(">H", ia, at - lo)[0]
    tb = struct.unpack_from(">H", ib, at - lo)[0]
    print("stored @0x%06X:  A=0x%04X  B=0x%04X" % (at, ta, tb))
    if ta == tb:
        print("WARNING: both images store the SAME word - this pair cannot discriminate")

    def s8(x): return sum(x) & 0xFFFF

    def s16be(x): return sum(struct.unpack_from(">H", x, k)[0]
                             for k in range(0, len(x) - 1, 2)) & 0xFFFF

    def s16le(x): return sum(struct.unpack_from("<H", x, k)[0]
                             for k in range(0, len(x) - 1, 2)) & 0xFFFF

    def x16(x):
        v = 0
        for k in range(0, len(x) - 1, 2):
            v ^= struct.unpack_from(">H", x, k)[0]
        return v

    def c16f(x): return binascii.crc_hqx(bytes(x), 0xFFFF)

    def c16z(x): return binascii.crc_hqx(bytes(x), 0x0000)

    algos = dict(sum8=s8, sum16be=s16be, sum16le=s16le, xor16=x16,
                 crc16_ccitt_false=c16f, crc16_xmodem=c16z)
    starts = [lo] + [_hx(s) for s in (a.start or [])]
    ends = [at, at + 2, lo + len(ia)] + [_hx(s) for s in (a.end or [])]
    hits = 0
    for st in sorted(set(starts)):
        for en in sorted(set(ends)):
            if not (lo <= st < en <= lo + len(ia)):
                continue
            A = ia[st - lo:en - lo]; B = ib[st - lo:en - lo]
            for nm, fn in algos.items():
                if fn(A) == ta and fn(B) == tb:
                    print("  *** MATCH %s [0x%06X..0x%06X)" % (nm, st, en)); hits += 1
    if not hits:
        print("  no match - widen --start/--end, or the field is not a plain checksum")
    return 0 if hits else 1


def main():
    ap = argparse.ArgumentParser(prog="vbftool", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)

    p = sp.add_parser("info"); p.add_argument("vbf", nargs="+"); p.set_defaults(fn=cmd_info)
    p = sp.add_parser("verify"); p.add_argument("vbf", nargs="+"); p.set_defaults(fn=cmd_verify)
    p = sp.add_parser("extract"); p.add_argument("vbf"); p.add_argument("-o")
    p.set_defaults(fn=cmd_extract)
    p = sp.add_parser("merge"); p.add_argument("vbf", nargs="+")
    p.add_argument("-o", required=True); p.add_argument("--lo"); p.add_argument("--hi")
    p.set_defaults(fn=cmd_merge)
    p = sp.add_parser("sum8"); p.add_argument("vbf"); p.add_argument("--at", required=True)
    p.add_argument("--start"); p.add_argument("--end"); p.add_argument("--end-img", dest="end_img")
    p.set_defaults(fn=cmd_sum8)
    p = sp.add_parser("patch"); p.add_argument("vbf"); p.add_argument("-o", required=True)
    p.add_argument("--set", action="append"); p.add_argument("--expect", action="append")
    p.add_argument("--sum8-at"); p.add_argument("--sum8-start"); p.add_argument("--sum8-end")
    p.add_argument("--sum8-img-end", dest="sum8_img_end")
    p.set_defaults(fn=cmd_patch)
    p = sp.add_parser("diff"); p.add_argument("a"); p.add_argument("b"); p.set_defaults(fn=cmd_diff)
    p = sp.add_parser("findsum"); p.add_argument("a"); p.add_argument("b")
    p.add_argument("--at", required=True)
    p.add_argument("--start", action="append"); p.add_argument("--end", action="append")
    p.set_defaults(fn=cmd_findsum)

    a = ap.parse_args()
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
