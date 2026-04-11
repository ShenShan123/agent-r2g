---
name: r2g-rtl2gds
description: Drive an open-source EDA workflow from RTL to GDS with signoff checks (DRC, LVS, RCX) using OpenROAD-flow-scripts (ORFS), Yosys, KLayout, and OpenRCX. Use when the user wants to turn a hardware spec or RTL into synthesis results, place-and-route, GDS output, signoff verification, parasitic extraction, or report summaries. Also use when iterating on PPA, diagnosing flow failures, or viewing a multi-project dashboard.
metadata:
  requires:
    bins: [python3, yosys, iverilog, vvp, openroad]
    optional_bins: [verilator, klayout, magic, netgen-lvs, gtkwave, sta, opensta]
    env:
      OPENROAD_EXE: /usr/bin/openroad
      YOSYS_EXE: /opt/pdk_klayout_openroad/oss-cad-suite/bin/yosys
      ORFS_ROOT: /opt/EDA4AI/OpenROAD-flow-scripts
      PDK_ROOT: /opt/pdks
  warnings:
    - Core skill operations are file-based and safe
    - Backend runs invoke make inside ORFS and may take several minutes
    - Default platform is nangate45; change PLATFORM in config.mk for other PDKs
    - DRC uses KLayout (all platforms) or Magic (sky130 only); LVS uses KLayout or Netgen (sky130 only)
    - RCX uses OpenRCX via OpenROAD
    - LVS/DRC gracefully skip when rules are absent for a platform
---
# r2g-rtl2gds Skill

Execute a staged, artifact-first open-source EDA flow from specification to GDSII with full signoff checks using OpenROAD-flow-scripts (ORFS). Prefer deterministic scripts for execution, keeping the agent focused on planning, generation, diagnosis, and iteration.

## Environment Setup

Before running any flow, source the environment:

```bash
source /opt/openroad_tools_env.sh
```

Key paths:
- ORFS root: `/opt/EDA4AI/OpenROAD-flow-scripts`
- ORFS flow directory: `/opt/EDA4AI/OpenROAD-flow-scripts/flow`
- Available platforms: `nangate45`, `sky130hd`, `sky130hs`, `asap7`, `gf180`, `ihp-sg13g2`
- Default platform: `nangate45`

## Workflow

### 1. Normalize the Specification First

- Convert free-form requirements into a structured specification before writing RTL.
- Read `references/spec-template.md` and produce `input/normalized-spec.yaml`.
- If clock/reset, IO, target flow, or timing targets are missing, stop and ask the user or record explicit assumptions.

### 2. Initialize a Project Directory

- Create a run folder under `eda-runs/<design-name>/` using `scripts/init_project.py`.
- The layout follows `references/workflow.md`.
- Directories created: `input/`, `rtl/`, `tb/`, `constraints/`, `lint/`, `sim/`, `synth/`, `backend/`, `drc/`, `lvs/`, `rcx/`, `reports/`.

### 3. Generate RTL and Testbench Separately

- Write RTL to `rtl/design.v`.
- Write testbench to `tb/testbench.v`.
- Keep assumptions and design notes in `reports/rtl-notes.md`.

### 4. Run Validation in Strict Order

1. Run `scripts/validate_config.py <project-dir>` before ORFS backend to catch config/RTL issues early.
2. Run lint/syntax checks before simulation.
3. Run simulation before synthesis.
4. Run synthesis before backend (ORFS).
5. Do not skip failed stages unless the user explicitly requests it.

### 5. Run Backend with ORFS

- Prepare `constraints/config.mk` and `constraints/constraint.sdc`.
- Use `scripts/run_orfs.sh` to invoke the ORFS Makefile.
- ORFS runs place-and-route natively (no Docker required).
- Collect results from the ORFS results directory.

### 5b. Check Timing Before Signoff (Tiered WNS + TNS)

After ORFS completes, extract PPA and run the timing gate:

1. Run `scripts/extract_ppa.py <project-dir> reports/ppa.json` to extract timing metrics.
2. Run `scripts/check_timing.py <project-dir>` to classify WNS and TNS and write `reports/timing_check.json`.
3. The script independently classifies WNS and TNS, then takes the **worse** of the two as the combined tier. A design with small WNS but large TNS (many slightly-violating paths) is caught.
4. Read `reports/timing_check.json` and act on the `tier`:

| Tier | Criteria | Agent Action |
|------|----------|-------------|
| **clean** | WNS >= 0, TNS >= 0 | Proceed to signoff. |
| **minor** | WNS >= -2.0 AND TNS >= -10.0 | Auto-fix: update `clk_period` in constraint.sdc to `suggested_clock_period` from the JSON, then re-run backend. Report the fix to the user after the fact. |
| **moderate** | WNS >= -5.0 AND TNS >= -100.0 (but not clean/minor) | **Stop.** Present the numbered `options` from the JSON to the user. Wait for their choice. |
| **severe** | WNS < -5.0 OR TNS < -100.0 | **Stop.** Present options with strong warning. |
| **unconstrained** | WNS > 1e+30 | **Stop.** SDC clock port mismatch. Present options. Do NOT proceed. |

5. The JSON includes `wns_tier` and `tns_tier` fields so the agent can explain which metric triggered the tier (e.g., "TNS escalated this from minor to moderate").
6. Only proceed to signoff checks (step 6) after timing is resolved.

### 6. Run Signoff Checks (DRC, LVS, RCX)

After a successful backend run, run signoff checks in order:

#### DRC (Design Rule Check)

Two tool options are available:

1. **KLayout DRC** (default) — `scripts/run_drc.sh <project-dir> [platform]`
   - Uses ORFS `make drc` target with platform `.lydrc` rules
   - Outputs: `drc/6_drc.lyrdb`, `drc/6_drc_count.rpt`, `drc/6_drc.log`

2. **Magic DRC** (sky130 only) — `scripts/run_magic_drc.sh <project-dir> [platform]`
   - Uses Magic's built-in DRC engine with sky130A tech file
   - Requires sky130A PDK at `/opt/pdks/sky130A/`
   - Outputs: `drc/magic_drc.rpt`, `drc/magic_drc_count.rpt`, `drc/magic_drc_result.json`
   - Supported platforms: sky130hd, sky130hs

#### LVS (Layout vs Schematic)

Two tool options are available:

1. **KLayout LVS** (default) — `scripts/run_lvs.sh <project-dir> [platform]`
   - Uses ORFS `make lvs` target with platform `.lylvs` rules + CDL netlist
   - **Gracefully skips** platforms without LVS rules (produces `lvs/lvs_result.json` with status "skipped")
   - Outputs: `lvs/6_lvs.lvsdb`, `lvs/6_lvs.log`, `lvs/6_final.cdl`
   - nangate45: uses adapted FreePDK45 rules with `connect_implicit("VDD"/"VSS")` for bulk merging and `schematic.purge` for unused cell pins (e.g., QN on DFFR_X1)
   - **Large design warning**: KLayout LVS on designs >100K cells (black_parrot, swerv) takes >60 minutes. Use `LVS_TIMEOUT=7200` for these designs. The default 3600s may not be enough.

2. **Netgen LVS** (sky130 only) — `scripts/run_netgen_lvs.sh <project-dir> [platform]`
   - Two-step flow: Magic extracts SPICE from GDS, then Netgen compares against Verilog netlist
   - Requires sky130A PDK at `/opt/pdks/sky130A/` (Magic tech + Netgen setup.tcl)
   - Outputs: `lvs/extracted.spice`, `lvs/netgen_lvs.rpt`, `lvs/netgen_lvs_result.json`
   - Supported platforms: sky130hd, sky130hs

#### RCX (Parasitic Extraction)

3. **RCX** — `scripts/run_rcx.sh <project-dir> [platform]`
   - OpenRCX parasitic extraction via OpenROAD
   - Generates Tcl script (`rcx/run_rcx.tcl`) with `define_process_corner`, `extract_parasitics`, `write_spef`
   - Reads `6_final.odb` from ORFS results, writes SPEF output
   - Outputs: `rcx/6_final.spef`, `rcx/rcx.log`, `rcx/run_rcx.tcl`

Extract results into JSON for reporting and dashboard:
- `scripts/extract_drc.py <project-root> reports/drc.json`
- `scripts/extract_lvs.py <project-root> reports/lvs.json`
- `scripts/extract_rcx.py <project-root> reports/rcx.json`

#### Platform Support Matrix

| Platform | KLayout DRC | KLayout LVS | Magic DRC | Netgen LVS | RCX |
|----------|-------------|-------------|-----------|------------|-----|
| nangate45 | Yes | Yes | No | No | Yes |
| sky130hd | Yes | Yes | Yes | Yes | Yes |
| sky130hs | Yes | Yes | Yes | Yes | Yes |
| asap7 | Yes | No | No | No | Yes |
| gf180 | Yes | Yes | No | No | Yes |
| ihp-sg13g2 | Yes | Yes | No | No | Yes |

### 7. Treat Artifacts as Source of Truth

- Save logs, reports, VCD waveforms, netlists, SPEF, configurations, and summary files.
- Prefer file outputs over GUI tools. GUI viewers like GTKWave/KLayout are optional helpers.

### 8. Diagnose Before Editing

- For failures, read `references/failure-patterns.md`.
- Classify the failure: specification gap, RTL bug, testbench bug, synthesis issue, backend/configuration issue, DRC violation, LVS mismatch, or RCX extraction error.
- Fix the smallest plausible cause first.

### 9. Summarize Each Stage Clearly

- State pass/fail status.
- List key artifact paths.
- Record assumptions, blockers, and next recommended actions.
- For signoff: report DRC violation count, LVS match/skip status, RCX net count and total capacitance.

### 10. Ingest the Run into the Knowledge Store

After **every** flow — successful, failed, or partial — run:

```bash
python3 skills/r2g-rtl2gds/scripts/ingest_run.py eda-runs/<project>
```

This reads the structured JSON artifacts produced by the extraction scripts
and appends one row to `skills/r2g-rtl2gds/knowledge/runs.sqlite`. It never
parses raw ORFS logs.

Then rebuild derived artifacts:

```bash
python3 skills/r2g-rtl2gds/scripts/learn_heuristics.py
python3 skills/r2g-rtl2gds/scripts/mine_rules.py
```

- `knowledge/heuristics.json` is consumed automatically by
  `suggest_config.py` on the next project — no CLI changes required.
- `knowledge/failure_candidates.json` is a **review queue**, not a rule
  source. Surface new signatures to the user and, if confirmed, edit
  `references/failure-patterns.md` by hand.

A family/platform pair appears in `heuristics.json` only after at least
**3 successful runs** under that configuration.

## Hard Rules

- Do not start backend if simulation is failing.
- Do not start ORFS if synthesis failed or the top module is unclear.
- Do not start signoff checks (DRC/LVS/RCX) if backend did not produce a GDS/ODB.
- Run `check_timing.py` after every backend run. It checks both WNS and TNS. For minor violations (WNS >= -2.0 AND TNS >= -10.0), auto-fix by increasing clock period and re-running. For moderate/severe/unconstrained, stop and present numbered fix options — do not proceed without the user's decision.
- Do not silently invent missing interfaces, clocks, resets, or timing targets without documenting assumptions.
- Prefer single-clock MVP flows. Macro designs (fakeram45) are supported with proper config (see "Macro / Hard Memory Designs"). Escalate to the user before attempting CDC, multi-clock, or DFT.
- Use the scripts in `scripts/` for repeatable operations instead of re-inventing shell commands each time.
- Always source `/opt/openroad_tools_env.sh` before running EDA tools.

## Default Project Layout

```text
eda-runs/<design-name>/
├── input/
│   ├── raw-spec.md
│   └── normalized-spec.yaml
├── rtl/
│   └── design.v
├── tb/
│   └── testbench.v
├── constraints/
│   ├── config.mk
│   └── constraint.sdc
├── lint/
│   └── lint.log
├── sim/
│   ├── sim.log
│   └── output.vcd
├── synth/
│   ├── synth.ys
│   ├── synth.log
│   └── synth_output.v
├── backend/
│   └── RUN_<timestamp>/
│       ├── final/              # GDS, DEF, ODB
│       ├── logs/               # Per-stage logs
│       ├── reports/            # Timing, area, power
│       ├── drc/                # DRC results (copied)
│       ├── lvs/                # LVS results (copied)
│       └── rcx/                # RCX results (copied)
├── drc/
│   ├── 6_drc.lyrdb            # KLayout DRC violation database (XML)
│   ├── 6_drc_count.rpt        # Violation count
│   ├── 6_drc.log              # DRC log
│   └── drc_run.log            # Full make output
├── lvs/
│   ├── 6_lvs.lvsdb            # KLayout LVS comparison database (XML)
│   ├── 6_lvs.log              # LVS log
│   ├── 6_final.cdl            # CDL netlist
│   ├── lvs_run.log            # Full make output
│   └── lvs_result.json        # Only if skipped (no rules)
├── rcx/
│   ├── 6_final.spef           # SPEF parasitic data
│   ├── rcx.log                # OpenRCX extraction log
│   └── run_rcx.tcl            # Generated Tcl extraction script
├── reports/
│   ├── ppa.json               # PPA metrics + geometry
│   ├── progress.json          # ORFS stage completion
│   ├── run-history.json       # Multi-run comparison
│   ├── run-compare.json       # Baseline vs current delta
│   ├── diagnosis.json         # Issue detection & suggestions
│   ├── drc.json               # DRC summary (violations, categories)
│   ├── lvs.json               # LVS summary (match/mismatch/skipped)
│   ├── rcx.json               # RCX summary (net count, cap, res)
│   └── demo-summary.md        # Human-readable summary
└── metadata.json
```

## Resource Map

- Read `references/spec-template.md` when the specification is incomplete or ambiguous.
- Read `references/workflow.md` when you need the phase-by-phase execution order.
- Read `references/orfs-playbook.md` before setting up or debugging the ORFS backend.
- Read `references/failure-patterns.md` when a run fails and you need a triage path.
- Read `references/ppa-report-guide.md` when summarizing synthesis/backend reports.
- Use scripts in `scripts/` for initialization, spec normalization, environment checks, lint, simulation, synthesis, ORFS backend, DRC, LVS, RCX extraction, result collection, GDS preview rendering, dashboard generation, and run summaries.
- Use `assets/examples/simple-arbiter/` as the first smoke-test case.
- Use `assets/config-template.mk` and `assets/constraint-template.sdc` as default backend configuration templates.

## Quick Start

### Prerequisites

**Required Tools (all installed on this machine):**
- `python3` (3.13+)
- `yosys` (synthesis)
- `iverilog` + `vvp` (simulation)
- `openroad` (place & route, OpenRCX)

**Optional Tools (also available):**
- `verilator` (faster lint/simulation)
- `klayout` (GDS visualization, DRC, LVS)
- `magic` (DRC, SPICE extraction for sky130)
- `netgen-lvs` (LVS comparison for sky130)
- `gtkwave` (waveform viewing)
- `sta` / `opensta` (static timing analysis)

### Running a Full Flow

1. Source the environment: `source /opt/openroad_tools_env.sh`
2. Initialize a run directory with `scripts/init_project.py <design-name>`.
3. Save user requirements to `input/raw-spec.md`.
4. Normalize them into `input/normalized-spec.yaml` using `scripts/normalize_spec.py`.
5. Write or copy `rtl/design.v` and `tb/testbench.v`.
6. Run `scripts/check_env.sh` to verify tool availability.
7. Run `scripts/run_lint.sh`, then `scripts/run_sim.sh`, then `scripts/run_synth.sh`.
8. After those pass, prepare `constraints/config.mk` and `constraints/constraint.sdc`.
9. Run `scripts/run_orfs.sh <project-dir>` for the backend.
10. Extract PPA: `scripts/extract_ppa.py <project-dir> reports/ppa.json`
11. Run timing gate: `scripts/check_timing.py <project-dir>` — reads `reports/timing_check.json`:
    - `tier=clean`: proceed to step 12.
    - `tier=minor`: auto-fix clock period per `suggested_clock_period`, re-run step 9, then re-check.
    - `tier=moderate/severe/unconstrained`: **stop, present options to user, wait for decision**.
    - Check `wns_tier` and `tns_tier` to explain which metric drove the tier.
12. Run signoff checks (only after timing gate passes or user approves):
    - `scripts/run_drc.sh <project-dir> [platform]` (KLayout DRC)
    - `scripts/run_magic_drc.sh <project-dir> [platform]` (Magic DRC, sky130 only)
    - `scripts/run_lvs.sh <project-dir> [platform]` (KLayout LVS)
    - `scripts/run_netgen_lvs.sh <project-dir> [platform]` (Netgen LVS, sky130 only)
    - `scripts/run_rcx.sh <project-dir> [platform]`
13. Extract remaining results:
    - `scripts/extract_drc.py <project-root> reports/drc.json`
    - `scripts/extract_lvs.py <project-root> reports/lvs.json`
    - `scripts/extract_rcx.py <project-root> reports/rcx.json`
14. Diagnose issues: `scripts/build_diagnosis.py <project-root> reports/diagnosis.json`
15. Get config suggestions: `scripts/suggest_config.py <project-dir>` (optional, useful for tuning)
16. Collect artifacts with `scripts/collect_reports.py` and summarize with `scripts/summarize_run.py`.
17. Generate the dashboard with `scripts/generate_multi_project_dashboard.py`.
18. Serve it with `scripts/serve_multi_project_dashboard.py 8765`.

## MVP Scope

Default to an MVP flow that supports:
- Single module or small design
- Single clock domain
- Simple reset behavior
- Generated or hand-authored Verilog RTL
- Testbench-driven simulation
- Yosys synthesis
- ORFS backend run with nangate45 platform
- DRC signoff check
- LVS signoff check (where platform supports it)
- OpenRCX parasitic extraction (SPEF)
- Report collection, signoff summary, and dashboard

Escalate to the user before attempting CDC, multi-clock constraints, DFT, or signoff-quality closure.

### Macro / Hard Memory Designs (Validated)

Macro designs using fakeram45 on nangate45 are supported and validated (riscv32i, tinyRocket, swerv, bp_multi_top — all produce GDS + pass RCX). LVS passes for designs <150K cells; designs >150K cells may need extended LVS timeout.

Some designs instantiate hard memory macros (fakeram45 on nangate45, SRAM on sky130). These require extra config:

1. **Verilog blackbox stubs** — Provide module definitions for all macros referenced in the RTL. For designs using SYNTH_HIERARCHICAL=1, stubs must be actual module implementations (not `(* blackbox *)` attributes), because Yosys CELLMATCH pass needs cost info. Wrap the fakeram macros inside the BSG-style wrapper modules with real port connections.

2. **ADDITIONAL_LEFS / ADDITIONAL_LIBS** — Point to the platform's LEF and LIB files for each macro type.

3. **CDL_FILE for LVS** — The platform config.mk sets a default CDL_FILE that only includes standard cells. Macro designs need a combined CDL with both standard cells and fakeram subcircuit definitions. Use `override export CDL_FILE` (the `override` keyword is critical — without it, the platform config.mk, which is included after the design config, will silently overwrite your CDL_FILE).

4. **MACRO_PLACEMENT_TCL** — Provide a Tcl script for macro placement. Use `find_macros` (not `all_macros`) to check for macros, and call `global_placement` before `macro_placement`:
   ```tcl
   if {[find_macros] != ""} {
     global_placement -density [place_density_with_lb_addon] -pad_left 2 -pad_right 2
     macro_placement -halo {10 10} -style corner_max_wl
   }
   ```

5. **GDS_ALLOW_EMPTY** — Set `export GDS_ALLOW_EMPTY = fakeram.*` so ORFS allows empty GDS cells for the macro stubs.

6. **Safety flags** — Large macro designs need `SKIP_CTS_REPAIR_TIMING=1` and `SKIP_LAST_GASP=1` to avoid OpenROAD crashes.

## ORFS Backend Details

### config.mk Format

```makefile
export DESIGN_NAME = <top_module>
export PLATFORM    = nangate45

export VERILOG_FILES = <absolute_path_to_rtl>
export SDC_FILE      = <absolute_path_to_sdc>

export CORE_UTILIZATION = 30
export PLACE_DENSITY_LB_ADDON = 0.20
```

For macro designs, add (note the `override` on CDL_FILE):
```makefile
export ADDITIONAL_LEFS = $(PLATFORM_DIR)/lef/fakeram45_64x32.lef
export ADDITIONAL_LIBS = $(PLATFORM_DIR)/lib/fakeram45_64x32.lib
export GDS_ALLOW_EMPTY = fakeram.*
export MACRO_PLACEMENT_TCL = /absolute/path/to/macro_placement.tcl
override export CDL_FILE = /absolute/path/to/combined_with_fakerams.cdl
```

The `override` keyword on CDL_FILE is essential: ORFS includes the platform config.mk *after* the design config.mk, so a bare `export CDL_FILE` gets silently overwritten by the platform default (which only has standard cells).

### config.mk Validation Rules (Hard)

Before running ORFS, validate every config.mk against these rules:

1. **PLACE_DENSITY_LB_ADDON ≥ 0.10** — Values below 0.10 cause placement divergence (NesterovSolve stuck). Use 0.20+ for macro designs.
2. **Bus-heavy designs need CORE_UTILIZATION ≤ 15%** — Crossbars, interconnect fabrics, and bus arbiters have high routing demand.
3. **Large macro designs need safety flags** — Designs with >50K instances or SRAM macros (swerv, black_parrot, ibex) must include:
   ```makefile
   export SKIP_CTS_REPAIR_TIMING = 1
   export SKIP_LAST_GASP = 1
   ```
   Without these, OpenROAD may SIGSEGV during CTS timing repair.
4. **All VERILOG_FILES must be absolute paths** and point to existing files.
5. **DESIGN_NAME must exactly match the RTL top module name.**

### constraint.sdc Format

```tcl
current_design <top_module>
set clk_name  core_clock
set clk_port_name clk
set clk_period 10.0
set clk_io_pct 0.2

set clk_port [get_ports $clk_port_name]
create_clock -name $clk_name -period $clk_period $clk_port

set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
```

### Running ORFS

The `scripts/run_orfs.sh` script:
1. Copies RTL and constraints to an ORFS-compatible design directory
2. Derives a unique `FLOW_VARIANT` from the project directory name (prevents collisions)
3. Runs `make DESIGN_CONFIG=<config.mk> FLOW_VARIANT=<variant>` with optional timeout
4. Collects results back to the project directory

Resource control via environment variables:
```bash
ORFS_TIMEOUT=7200    # Max runtime in seconds (default: 2 hours)
ORFS_MAX_CPUS=4      # Limit CPU cores via taskset (default: all)
```

### Shell Script Safety Rules (Validated Against 70 Designs)

These rules are enforced in all 6 execution scripts (`run_orfs.sh`, `run_lvs.sh`, `run_rcx.sh`, `run_drc.sh`, `run_magic_drc.sh`, `run_netgen_lvs.sh`):

1. **PIPESTATUS capture** — When piping `timeout ... | tee`, temporarily disable `set -e` and `pipefail` to capture the correct exit code:
   ```bash
   set +e +o pipefail
   setsid timeout ... make ... 2>&1 | tee logfile
   STATUS=${PIPESTATUS[0]}
   set -e -o pipefail
   ```
   Never use `|| true` after a pipeline — it resets PIPESTATUS to 0 and masks all failures.

2. **Environment isolation** — Always `unset SCRIPTS_DIR` before calling ORFS make. ORFS Makefile uses this variable internally; an external value causes "No rule to make target synth.sh" errors.

3. **Process group kills** — Use `setsid timeout` (not bare `timeout`) so the entire process tree is killed on timeout. Without `setsid`, grandchild processes (klayout, openroad) survive as zombies, consuming memory and holding file locks.

4. **LVS timeout scaling** — Default `LVS_TIMEOUT=3600` works for designs <100K cells. For swerv-class (~145K), use 4200s. For bp_multi_top-class (~200K), use 7200s. ORFS and RCX always complete within default timeouts.

### Running Signoff After ORFS

After a successful ORFS run, the signoff scripts operate on the ORFS results in-place:

- `run_drc.sh` — runs `make DESIGN_CONFIG=<config.mk> drc` in ORFS flow directory (KLayout)
- `run_magic_drc.sh` — runs Magic DRC with sky130A tech file (sky130 only)
- `run_lvs.sh` — runs `make DESIGN_CONFIG=<config.mk> lvs` in ORFS flow directory (KLayout)
- `run_netgen_lvs.sh` — extracts SPICE via Magic, compares with Netgen (sky130 only)
- `run_rcx.sh` — runs OpenROAD directly with a generated Tcl script reading `6_final.odb`

All scripts collect results back to the project's `drc/`, `lvs/`, `rcx/` directories and also copy them into the latest `backend/RUN_*/` subdirectory.
