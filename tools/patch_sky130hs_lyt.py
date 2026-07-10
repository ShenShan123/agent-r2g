#!/usr/bin/env python3
"""Patch ORFS sky130hs.lyt: port sky130hd's modern <lefdef> reader options.

Why (failure-patterns.md #33): this ORFS checkout's sky130hs.lyt still carries
the LEGACY KLayout lefdef option names with wrong datatypes (<routing-suffix>/
<routing-datatype>0, <pins-suffix>/<pins-datatype>2) and is missing
<produce-special-routing>, while sky130hd.lyt was rewritten to the modern names
(<routing-suffix-string>.drawing + <routing-datatype-string>20,
<special-pins-…>16, <special-via_geometry-…>44, …). KLayout 0.30.x ignores the
legacy names, so ORFS's def2stream merge SILENTLY drops every DEF-derived shape
on sky130hs — routing wires, vias, pin rects, special (power) routing — leaving
a 6_final.gds whose top cell holds only text labels and cell placements. Magic
then extracts a portless top subckt and every Netgen LVS reports a false
top-pin mismatch (100% of sky130hs designs; all LVS-clean on sky130hd).

The fix ports sky130hd's <lefdef> block verbatim, substituting sky130hs's own
<layer-map> (same sky130A table; kept to stay conservative). Idempotent: a
second run detects the modern options and exits 0. The original is backed up
once as sky130hs.lyt.orig. Re-run after any ORFS update that reverts the file.

Usage:  python3 tools/patch_sky130hs_lyt.py [--check]
          --check: exit 0 if patched, 2 if the legacy/broken options are live.
"""
import os
import re
import shutil
import sys

FLOW = os.environ.get("FLOW_DIR") or os.path.join(
    os.environ.get("ORFS_ROOT", "/proj/workarea/user5/OpenROAD-flow-scripts"), "flow")
HD = os.path.join(FLOW, "platforms", "sky130hd", "sky130hd.lyt")
HS = os.path.join(FLOW, "platforms", "sky130hs", "sky130hs.lyt")

LEFDEF_RE = re.compile(r"<lefdef>.*?</lefdef>", re.S)
MAP_RE = re.compile(r"<layer-map>.*?</layer-map>", re.S)


def main() -> int:
    check = "--check" in sys.argv
    for p in (HD, HS):
        if not os.path.isfile(p):
            print(f"ERROR: {p} not found (set ORFS_ROOT/FLOW_DIR)", file=sys.stderr)
            return 1
    hs_txt = open(HS, encoding="utf-8").read()
    hs_lefdef = LEFDEF_RE.search(hs_txt)
    if not hs_lefdef:
        print(f"ERROR: no <lefdef> section in {HS}", file=sys.stderr)
        return 1
    modern = ("<routing-datatype-string>" in hs_lefdef.group(0)
              and "<produce-special-routing>" in hs_lefdef.group(0))
    if modern:
        print("sky130hs.lyt already patched (modern lefdef options present)")
        return 0
    if check:
        print("sky130hs.lyt UNPATCHED: legacy lefdef options — def2stream drops "
              "all DEF geometry (failure-patterns.md #33)")
        return 2

    hd_lefdef = LEFDEF_RE.search(open(HD, encoding="utf-8").read())
    if not hd_lefdef:
        print(f"ERROR: no <lefdef> section in {HD}", file=sys.stderr)
        return 1
    hs_map = MAP_RE.search(hs_lefdef.group(0))
    new_lefdef = hd_lefdef.group(0)
    if hs_map:
        new_lefdef = MAP_RE.sub(lambda _: hs_map.group(0), new_lefdef, count=1)

    orig = HS + ".orig"
    if not os.path.exists(orig):
        shutil.copy2(HS, orig)
    open(HS, "w", encoding="utf-8").write(LEFDEF_RE.sub(
        lambda _: new_lefdef, hs_txt, count=1))
    print(f"patched {HS} (backup: {orig}) — re-run ORFS 'finish'/merge for any "
          f"sky130hs GDS produced before this patch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
