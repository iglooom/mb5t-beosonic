#!/usr/bin/env python3
"""Compare Ghidra's (independent SLEIGH) disassembly of the patched image with the builder's
intended listing, instruction by instruction, and check every hook decodes to the intended jarl.
Input: release/ghidra_patch_dis.txt (tools/ghidra/DisAt.java), release/cave_listing.txt, release/manifest.json."""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths as P  # noqa: E402
OUT = P.RELEASE


def norm(s):
    s = s.strip()
    s = re.sub(r'^(bl|bnl|bz|bnz)\b', lambda m: {'bl': 'bc', 'bnl': 'bnc', 'bz': 'be', 'bnz': 'bne'}[m.group(1)], s)
    s = s.replace(' ', '')
    s = re.sub(r'0x([0-9a-f]+)', lambda m: '0x%x' % int(m.group(1), 16), s)
    s = re.sub(r'(?<![0-9a-fx])(-?)(\d+)(?=[\[,]|$)', lambda m: m.group(0), s)
    return s


gh = {}
for ln in open(os.path.join(OUT, 'ghidra_patch_dis.txt')):
    p = ln.rstrip('\n').split('\t')
    if len(p) == 3:
        gh[int(p[0], 16)] = (p[1], p[2])
mine = []
for ln in open(os.path.join(OUT, 'cave_listing.txt')):
    m = re.match(r'\s+([0-9a-f]{6})\s+([0-9a-f]+)\s+(.*)$', ln)
    if m:
        mine.append((int(m.group(1), 16), m.group(2), m.group(3)))

bad = 0
SYN = {'sld.b': 'ld.b'}
for a, b, t in mine:
    g = gh.get(a)
    if g is None:
        print('MISSING in Ghidra listing: %x %s' % (a, t)); bad += 1; continue
    if g[0] != b:
        print('BYTES differ %x: ghidra %s mine %s' % (a, g[0], b)); bad += 1; continue
    gt, mt = norm(g[1]), norm(t)
    # Ghidra prints jarl/jr/branches with 0x000xxxxx targets -> normalised above
    if gt != mt:
        print('TEXT differ %x: ghidra [%s] mine [%s]' % (a, g[1], t)); bad += 1
print('cave: %d instructions compared, %d differences' % (len(mine), bad))

man = json.load(open(os.path.join(OUT, 'manifest.json')))
for hk in man['hooks']:
    a = hk['addr']
    g = gh.get(a)
    want = 'jarl0x%x,lp' % hk['target']
    ok = g is not None and norm(g[1]) == want
    if hk['new'].endswith('0000'):
        n = gh.get(a + 4)
        ok = ok and n is not None and n[1].strip() == 'nop'
    print('hook %x -> %s: ghidra [%s] %s' % (a, hk['label'], g[1] if g else None, 'OK' if ok else 'BAD'))
    bad += 0 if ok else 1
print('TOTAL differences', bad)
sys.exit(1 if bad else 0)
