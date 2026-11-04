"""Repository-relative locations of every input/output (single source of truth)."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INPUTS = os.path.join(ROOT, 'inputs')
RELEASE = os.path.join(ROOT, 'release')
ANALYSIS = os.path.join(ROOT, 'analysis')

STOCK_VBF = os.path.join(INPUTS, 'MB5T-14C589-CB.VBF')          # Explorer amp app, stock
STOCK_BIN = os.path.join(INPUTS, 'MB5T-14C589-CB_0x80000.bin')  # its flat image @0x80000
EXPLORER_EQ_VBF = os.path.join(INPUTS, 'LB5T-14C656-BH.VBF')    # Explorer EQ table (zlib)
EXPLORER_EQ_FLAT = os.path.join(ANALYSIS, 'LB5T-14C656-BH_0x10000.bin')   # inflated by eq_table.py
KUGA_APP = os.path.join(INPUTS, 'PJ6T-14C589-AD_0x80000.bin')    # Kuga 3 BeoSonic amp app (pad tables)
KUGA_EQ = os.path.join(INPUTS, 'LJ6T-14C656-BC_0x10000.bin')     # Kuga 3 B&O EQ table (BeoSonic records)
LISTING = os.path.join(ANALYSIS, 'MB5T-14C589-CB.lst')          # Ghidra listing of the stock app

NAME = 'MB5T-14C589-CB_BeoSonic'
OUT_BIN = os.path.join(RELEASE, NAME + '.bin')
OUT_VBF = os.path.join(RELEASE, NAME + '.VBF')
MANIFEST = os.path.join(RELEASE, 'manifest.json')
CAVE_LISTING = os.path.join(RELEASE, 'cave_listing.txt')
PATCH_ARGS = os.path.join(RELEASE, 'vbf_patch_args.txt')
GHIDRA_DIS = os.path.join(RELEASE, 'ghidra_patch_dis.txt')
