#!/usr/bin/env python3
"""Materialize a sky130hd project from an existing (nangate45) r2g project.

Reuses the source design's RTL (absolute VERILOG_FILES paths, in place) and SDC
(copied, so timing fixes can bump clk_period without touching the source). Writes
a fresh sky130hd config.mk that follows the skill's floorplan policy (prefer
CORE_UTILIZATION over a nangate45-sized DIE_AREA) and leaves CDL_FILE unset so
run_lvs.sh injects the sky130 macro_sparecell slash-fix automatically.

Exit codes:
  0  project materialized
  2  unportable to sky130hd (hard macros / fakeram) -- caller records honest-final
  1  hard error (missing source artifacts)

usage: mk_sky130_project.py <source_project_dir> <dest_project_dir>
"""
import os
import re
import shutil
import sys
from pathlib import Path


def join_continuations(text: str) -> list[str]:
    """Collapse makefile backslash line-continuations into logical lines."""
    out, buf = [], ""
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if line.rstrip().endswith("\\"):
            buf += line.rstrip()[:-1] + " "
        else:
            out.append(buf + line)
            buf = ""
    if buf:
        out.append(buf)
    return out


def parse_config(path: Path) -> dict:
    """Parse `export KEY = VALUE` / `KEY = VALUE` / `KEY += VALUE` assignments."""
    vals: dict[str, str] = {}
    pat = re.compile(r"^\s*(?:override\s+)?(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*([:+]?=)\s*(.*)$")
    for line in join_continuations(path.read_text(encoding="utf-8", errors="ignore")):
        if line.lstrip().startswith("#"):
            continue
        m = pat.match(line)
        if not m:
            continue
        key, op, val = m.group(1), m.group(2), m.group(3).strip()
        if op == "+=" and key in vals:
            vals[key] = vals[key] + " " + val
        else:
            vals[key] = val
    return vals


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: mk_sky130_project.py <source_project_dir> <dest_project_dir>", file=sys.stderr)
        return 1
    src = Path(sys.argv[1]).resolve()
    dest = Path(sys.argv[2]).resolve()
    src_cfg = src / "constraints" / "config.mk"
    src_sdc = src / "constraints" / "constraint.sdc"
    if not src_cfg.is_file():
        print(f"ERROR: no config.mk at {src_cfg}", file=sys.stderr)
        return 1

    cfg = parse_config(src_cfg)
    design = cfg.get("DESIGN_NAME", "").strip()
    if not design:
        print(f"ERROR: DESIGN_NAME not found in {src_cfg}", file=sys.stderr)
        return 1

    # --- macro / hard-memory portability gate -------------------------------
    blob = "\n".join(f"{k}={v}" for k, v in cfg.items())
    macro_markers = ["ADDITIONAL_LEFS", "MACRO_PLACEMENT_TCL", "fakeram", "GDS_ALLOW_EMPTY"]
    if any(mk in cfg for mk in ("ADDITIONAL_LEFS", "MACRO_PLACEMENT_TCL", "GDS_ALLOW_EMPTY")) or "fakeram" in blob.lower():
        print(f"UNPORTABLE: {design} uses hard macros (nangate45 fakeram/LEF) -> needs sky130 SRAM, skip", file=sys.stderr)
        return 2

    verilog = cfg.get("VERILOG_FILES", "").strip()
    if not verilog:
        print(f"ERROR: VERILOG_FILES not found in {src_cfg}", file=sys.stderr)
        return 1
    # Resolve relative tokens against the source dir; verify >=1 file exists.
    toks = []
    for t in verilog.split():
        if t.startswith("$"):  # unresolved make var -> bail (likely macro/platform)
            print(f"UNPORTABLE: {design} VERILOG_FILES has make-var token {t}", file=sys.stderr)
            return 2
        p = Path(t)
        if not p.is_absolute():
            p = (src / t)
        toks.append(str(p))
    if not any(Path(t).is_file() for t in toks):
        print(f"ERROR: none of VERILOG_FILES exist for {design}", file=sys.stderr)
        return 1

    # --- materialize dest ---------------------------------------------------
    (dest / "constraints").mkdir(parents=True, exist_ok=True)
    for sub in ("rtl", "reports", "drc", "lvs", "rcx", "backend", "input"):
        (dest / sub).mkdir(parents=True, exist_ok=True)
    dest_sdc = dest / "constraints" / "constraint.sdc"
    if src_sdc.is_file():
        shutil.copyfile(src_sdc, dest_sdc)
    else:
        # SDC_FILE may point elsewhere; copy whatever the config referenced.
        ref = cfg.get("SDC_FILE", "").strip()
        if ref and Path(ref).is_file():
            shutil.copyfile(ref, dest_sdc)
        else:
            print(f"ERROR: no SDC for {design}", file=sys.stderr)
            return 1

    cu = cfg.get("CORE_UTILIZATION", "").strip()
    try:
        cu_val = int(float(cu)) if cu else 20
    except ValueError:
        cu_val = 20
    pdl = cfg.get("PLACE_DENSITY_LB_ADDON", "").strip()
    try:
        pdl_val = max(0.10, float(pdl)) if pdl else 0.20
    except ValueError:
        pdl_val = 0.20

    # Floorplan policy for sky130hd:
    #   Small cores cannot fit sky130hd's met4/met5 PDN straps -> floorplan aborts
    #   with PDN-0185 "Insufficient width to add straps" REGARDLESS of utilization
    #   (a 65-cell core is ~7um wide; met4 straps need ~15.2um + 13.6um offset).
    #   So small designs get an explicit DIE_AREA floored to a PDN-feasible size
    #   (cordic-validated 200um core), while designs naturally large enough use
    #   CORE_UTILIZATION (auto-sized; avoids the IO-perimeter overflow that an
    #   explicit die risks on high-pin designs).
    #   See references/failure-patterns.md "sky130 small-core PDN strap floor".
    import math
    cell_count = 0
    src_ppa = src / "reports" / "ppa.json"
    if src_ppa.is_file():
        try:
            import json as _json
            cell_count = int(_json.loads(src_ppa.read_text()).get("cell_count") or 0)
        except Exception:
            cell_count = 0
    SKY130_CELL_UM2 = 8.0          # std-cell footprint estimate
    PDN_DIE_FLOOR = 200            # um; cordic-validated minimum for met4 straps
    est_core = max(cell_count, 1) * SKY130_CELL_UM2 / (cu_val / 100.0)
    core_side = math.sqrt(est_core)
    use_floor = core_side < (PDN_DIE_FLOOR - 40)   # design too small -> needs floor

    lines = [
        f"export DESIGN_NAME = {design}",
        "export PLATFORM    = sky130hd",
        "",
        f"export VERILOG_FILES = {' '.join(toks)}",
        f"export SDC_FILE      = {dest_sdc}",
        "",
    ]
    if use_floor:
        side = PDN_DIE_FLOOR
        lines += [
            "# Small design: explicit DIE floored to a PDN-feasible size (PDN-0185).",
            f"export DIE_AREA  = 0 0 {side} {side}",
            f"export CORE_AREA = 10 10 {side - 10} {side - 10}",
            f"export PLACE_DENSITY_LB_ADDON = {pdl_val}",
            "export ABC_AREA = 1",
        ]
    else:
        lines += [
            "# Large enough for the PDN grid -> utilization-based floorplan.",
            f"export CORE_UTILIZATION = {cu_val}",
            f"export PLACE_DENSITY_LB_ADDON = {pdl_val}",
            "export ABC_AREA = 1",
        ]
    # Carry over memory / hierarchy / safety knobs when the source needed them.
    for k in ("SYNTH_MEMORY_MAX_BITS", "SYNTH_HIERARCHICAL", "ABC_CLOCK_PERIOD_IN_PS",
              "SKIP_CTS_REPAIR_TIMING", "SKIP_LAST_GASP"):
        if k in cfg and cfg[k].strip():
            lines.append(f"export {k} = {cfg[k].strip()}")
    # Always split port-to-port feedthrough nets (`assign out = in`). ORFS
    # global_place runs remove_buffers, merging both ports onto one net, which
    # SPICE cannot express -> Netgen LVS "Top level cell failed pin matching"
    # (8/13 residuals of the first 50-design wave were feedthrough-free; the 5
    # diode-free ones were ALL this). The hook is a no-op for designs without
    # feedthroughs. See r2g skill references/failure-patterns.md "sky130 LVS".
    fdbuf_hook = (Path(__file__).resolve().parent.parent
                  / "r2g-rtl2gds" / "scripts" / "flow" / "orfs_hooks"
                  / "buffer_port_feedthroughs.tcl")
    lines += [
        "",
        "# Split port-to-port feedthrough nets so Netgen LVS top-level pins match",
        f"export POST_GLOBAL_PLACE_TCL = {fdbuf_hook}",
    ]
    # CDL_FILE intentionally unset -> run_lvs.sh injects the sky130 slash-fix.
    (dest / "constraints" / "config.mk").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"OK: materialized {dest} (design={design}, CU={cu_val})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
