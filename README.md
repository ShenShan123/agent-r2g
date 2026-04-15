# agent-r2g

An AI-driven open-source EDA skill that takes a natural-language hardware spec (or RTL) and drives it all the way to **GDSII + signoff (DRC, LVS, RCX)** through [OpenROAD-flow-scripts](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts), Yosys, KLayout, Magic, Netgen, and OpenRCX.

The core deliverable is the `r2g-rtl2gds` skill (at `skills/r2g-rtl2gds/`), plus a set of repo-level batch tools (`tools/`) validated on **495 RTL designs** across 19 families (81% pass on the first sweep, 93% after the fix campaign documented below).

---

## What's in this repo

```
agent-r2g/
├── skills/r2g-rtl2gds/            # The skill (install this into Claude Code)
│   ├── SKILL.md                   # Entry point — metadata, workflow, hard rules
│   ├── scripts/                   # 30 stateless Python/Shell CLIs for the flow
│   │   ├── flow/                  #   stage runners (run_lint, run_orfs, run_drc, …)
│   │   ├── extract/               #   parse tool output into JSON
│   │   ├── project/               #   init/normalize/validate project & spec
│   │   ├── reports/               #   timing gate, diagnosis, history
│   │   └── dashboard/             #   GDS render + multi-project HTML dashboard
│   ├── knowledge/                 # Self-contained knowledge-store subsystem
│   ├── references/                # Workflow guide, failure patterns, PPA guide
│   ├── assets/                    # config.mk / constraint.sdc templates
│   └── tests/                     # pytest suite
├── tools/                         # Repo-level batch orchestration
│   ├── setup_rtl_designs.py       # Scaffold design_cases/ from rtl_designs/
│   ├── batch_orfs_only.sh         # Parallel ORFS-only runner with per-case flock
│   ├── batch_flow.sh              # Full flow (ORFS + signoff)
│   └── fix_orfs_failures.py       # Log-signature-driven config.mk rewriter
├── docs/                          # Campaign reports and design notes
├── CLAUDE.md                      # Project instructions for Claude Code
└── LICENSE
```

---

## Requirements

The skill is validated against these tools on Linux (tested on RHEL 8.10). All are assumed pre-installed and discoverable after sourcing the environment.

| Tool | Purpose | Required? |
|------|---------|-----------|
| Python 3.10+ | skill scripts | yes |
| Yosys | synthesis | yes |
| iverilog / vvp | simulation | yes |
| OpenROAD | place & route, OpenRCX | yes |
| OpenROAD-flow-scripts | full backend flow | yes |
| Verilator | faster lint/sim | optional |
| KLayout | GDS viewer, DRC, LVS | optional |
| Magic + Netgen | sky130 DRC/LVS | optional |
| OpenSTA | signoff STA | optional |
| sky130A PDK | sky130 DRC/LVS/SPICE | optional |

See the skill's `scripts/flow/check_env.sh` for an auto-detect pass.

### Default paths the skill expects

The skill defaults to these locations (override via env var in parentheses):

- ORFS root: `/opt/EDA4AI/OpenROAD-flow-scripts` (`ORFS_ROOT`)
- Environment script: `/opt/openroad_tools_env.sh` (auto-sourced if present)
- sky130A PDK: `/opt/pdks/sky130A/`
- OpenROAD binary: `/usr/bin/openroad`
- Yosys binary: resolved from `$ORFS_ROOT/tools/install/yosys/bin/yosys` (or from your PATH)
- Platform configs: `$ORFS_ROOT/flow/platforms/{nangate45,sky130hd,sky130hs,asap7,gf180,ihp-sg13g2}`

---

## Installing the skill

### For Claude Code

Drop the `skills/r2g-rtl2gds/` directory into your Claude Code skills location:

```bash
# Option A: symlink from the repo (recommended for development)
mkdir -p ~/.claude/skills
ln -s "$(pwd)/skills/r2g-rtl2gds" ~/.claude/skills/r2g-rtl2gds

# Option B: copy
mkdir -p ~/.claude/skills
cp -r skills/r2g-rtl2gds ~/.claude/skills/
```

Restart Claude Code. The skill advertises itself automatically through `SKILL.md`'s frontmatter — ask Claude something like *"take this RTL through to GDS on nangate45"* and it will invoke the skill.

### For other agent harnesses

The skill is a self-contained directory. Point your harness's skill loader at `skills/r2g-rtl2gds/SKILL.md` as the entry document. All referenced scripts are relative to that directory.

---

## Quick start (standalone, no agent)

The skill's shell/Python scripts are usable directly. Example end-to-end run for a small counter RTL:

```bash
source /opt/openroad_tools_env.sh

SKILL=skills/r2g-rtl2gds
DESIGN=my_counter

# 1. Scaffold a project directory
python3 $SKILL/scripts/project/init_project.py $DESIGN

# 2. Drop RTL and testbench in place
cp my_counter.v   design_cases/$DESIGN/rtl/design.v
cp tb_counter.v   design_cases/$DESIGN/tb/testbench.v

# 3. Write constraints (see $SKILL/assets/config-template.mk and constraint-template.sdc)
cp $SKILL/assets/config-template.mk     design_cases/$DESIGN/constraints/config.mk
cp $SKILL/assets/constraint-template.sdc design_cases/$DESIGN/constraints/constraint.sdc
# ...then edit DESIGN_NAME, VERILOG_FILES, clk_port_name to match your RTL

# 4. Pre-flight checks
bash   $SKILL/scripts/flow/check_env.sh
bash   $SKILL/scripts/flow/run_lint.sh  design_cases/$DESIGN/rtl/design.v        design_cases/$DESIGN/lint/lint.log
bash   $SKILL/scripts/flow/run_sim.sh   design_cases/$DESIGN/rtl/design.v         design_cases/$DESIGN/tb/testbench.v  design_cases/$DESIGN/sim
bash   $SKILL/scripts/flow/run_synth.sh design_cases/$DESIGN/rtl/design.v         my_counter                            design_cases/$DESIGN/synth

# 5. Backend (place and route → GDS)
bash   $SKILL/scripts/flow/run_orfs.sh  design_cases/$DESIGN nangate45

# 6. PPA + timing gate (auto-fix minor WNS/TNS, otherwise stop and present options)
python3 $SKILL/scripts/extract/extract_ppa.py design_cases/$DESIGN design_cases/$DESIGN/reports/ppa.json
python3 $SKILL/scripts/reports/check_timing.py design_cases/$DESIGN

# 7. Signoff
bash    $SKILL/scripts/flow/run_drc.sh design_cases/$DESIGN nangate45
bash    $SKILL/scripts/flow/run_lvs.sh design_cases/$DESIGN nangate45
bash    $SKILL/scripts/flow/run_rcx.sh design_cases/$DESIGN nangate45

# 8. Dashboard (optional, HTML + GDS previews)
python3 $SKILL/scripts/dashboard/generate_multi_project_dashboard.py
python3 $SKILL/scripts/dashboard/serve_multi_project_dashboard.py 8765
```

A smoke-test example lives at `skills/r2g-rtl2gds/assets/examples/simple-arbiter/`.

---

## Batch mode

`tools/` drives the skill across hundreds of designs in parallel.

### Scaffold projects from an RTL catalog

Place raw designs under `rtl_designs/<name>/` with a `design_meta.json` (minimum keys: `design`, `top`, `platform`, `rtl_files`). Then:

```bash
python3 tools/setup_rtl_designs.py
```

This emits `design_cases/<name>/` for every entry, generating size-aware `config.mk` and clock-aware `constraint.sdc`. Version 2 of this script auto-detects:

- Clock port names via posedge/negedge signal analysis
- IO pin count (so the initial floorplan has enough perimeter)
- Largest inferred memory (sets `SYNTH_MEMORY_MAX_BITS` so Yosys doesn't reject)
- Unresolved ``\`include`` targets (adds `VERILOG_INCLUDE_DIRS`)

### Run ORFS across all projects

```bash
# 8-way parallel, 2-hour per-stage timeout
bash tools/batch_orfs_only.sh 8 7200

# Or limit to a subset
echo -e "my_counter\naes_core" > cases.txt
DESIGNS_LIST=cases.txt bash tools/batch_orfs_only.sh 4 3600
```

Results land in `design_cases/_batch/orfs_results.jsonl` (one JSON line per case).

### Auto-fix failures and retry

```bash
# Classify each failure in orfs_results.jsonl and rewrite the failing config.mk
python3 tools/fix_orfs_failures.py

# Re-run only the failed cases
grep -oE '"case": "[^"]+"' design_cases/_batch/orfs_results.jsonl | \
  awk -F'"' '{print $4}' > failed.txt
DESIGNS_LIST=failed.txt bash tools/batch_orfs_only.sh 8 7200
```

`fix_orfs_failures.py` handles six known failure signatures:
1. `SYNTH_MEMORY_MAX_BITS` exceeded → raise to 128 Kbit
2. `PPL-0024` IO pin overflow → explicit `DIE_AREA` from log-reported required perimeter
3. `FLW-0024` place density > 1.0 → drop to `CORE_UTILIZATION = 10`
4. `PDN-0179/0185` insufficient strap width → enlarge die
5. Missing `\`include` → add `VERILOG_INCLUDE_DIRS` + stub
6. Stage timeout → lower density, request longer timeout

Full pattern catalog with symptoms and fixes: `skills/r2g-rtl2gds/references/failure-patterns.md`.

---

## Validated scale

Tested on **495 heterogeneous RTL designs** (ICCAD benchmarks, verilog-ethernet, wb2axip, OpenCores, RISC-V cores, koios, VTR, zipcpu, etc.):

| Pass | Designs | Cumulative pass rate |
|------|---------|----------------------|
| 1 (initial ORFS sweep) | 402 / 495 | 81.2% |
| 2 (after `fix_orfs_failures.py` + config rewrites) | +59 rescued | **93.1%** |

34 remaining failures break down into two buckets — both outside the skill's remit:
- 14 data-inventory issues (missing header files, missing RTL, wrong top module in `design_meta.json`)
- 20 single-stage timeouts on very large datapath/FIFO designs (rescuable by raising `ORFS_TIMEOUT` to 4 hours)

Full retry analysis: `docs/batch_orfs_retry_report.md`.

---

## Platform support matrix

| Platform | KLayout DRC | KLayout LVS | Magic DRC | Netgen LVS | OpenRCX |
|----------|-------------|-------------|-----------|------------|---------|
| `nangate45` | yes | yes | no | no | yes |
| `sky130hd` | yes | yes | yes | yes | yes |
| `sky130hs` | yes | yes | yes | yes | yes |
| `asap7` | yes | no | no | no | yes |
| `gf180` | yes | yes | no | no | yes |
| `ihp-sg13g2` | yes | yes | no | no | yes |

LVS gracefully skips for platforms without `.lylvs` rules (reports `status: "skipped"`).

---

## License

See `LICENSE`.
