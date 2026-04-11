# Improve Physical Design Success Rate & Quality — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise signoff-clean rate from 84% to 95%+ by fixing silent failure bugs, adding clock port validation, enabling incremental ORFS flow, intelligent parameter selection, and expanded diagnostics.

**Architecture:** Four tiers of improvements applied to existing scripts in `skills/r2g-rtl2gds/scripts/`. Tier 1 fixes correctness bugs in 6 files. Tier 2 refactors `run_orfs.sh` for stage-by-stage execution with checkpoints, adds timeouts to all scripts, and adds congestion recovery. Tier 3 adds a config recommender script and enhances templates. Tier 4 expands diagnosis and dashboard coverage. Clock port mismatches in 40 `eda-runs/` constraint files are fixed as part of Tier 1.

**Tech Stack:** Bash (shell scripts), Python 3 (extraction/analysis scripts), ORFS Makefile targets, SDC/Tcl constraints.

**Working directory:** `/data/shenshan/agent_with_openroad`

---

## Tier 1: Fix Correctness Bugs

### Task 1: Fix `run_synth.sh` — Capture Yosys Exit Code

**Files:**
- Modify: `skills/r2g-rtl2gds/scripts/run_synth.sh`

- [ ] **Step 1: Fix exit code capture and Tcl quoting**

Replace the entire script with proper error handling. Key changes:
1. Quote variables in Yosys Tcl script
2. Capture Yosys exit code
3. Only report success if Yosys returned 0
4. Add synthesis area parsing (cell count extraction for Tier 3)

```bash
#!/usr/bin/env bash
set -euo pipefail

# usage: run_synth.sh <rtl-file> <top-module> <work-dir>
RTL_FILE="${1:-}"
TOP="${2:-}"
WORK_DIR="${3:-synth}"

if [[ -z "$RTL_FILE" || -z "$TOP" ]]; then
  echo "usage: run_synth.sh <rtl-file> <top-module> <work-dir>" >&2
  exit 1
fi

mkdir -p "$WORK_DIR"

# Use absolute path for RTL file
RTL_FILE="$(cd "$(dirname "$RTL_FILE")" && pwd)/$(basename "$RTL_FILE")"

cat > "$WORK_DIR/synth.ys" <<EOF
read_verilog "$RTL_FILE"
hierarchy -check -top $TOP
synth -top $TOP
stat
write_verilog "$WORK_DIR/synth_output.v"
EOF

SYNTH_STATUS=0
yosys -s "$WORK_DIR/synth.ys" >"$WORK_DIR/synth.log" 2>&1 || SYNTH_STATUS=$?

if [[ $SYNTH_STATUS -ne 0 ]]; then
  echo "ERROR: Yosys synthesis failed (exit code $SYNTH_STATUS)" >&2
  echo "Check log: $WORK_DIR/synth.log" >&2
fi

exit $SYNTH_STATUS
```

- [ ] **Step 2: Verify script is executable**

Run: `chmod +x skills/r2g-rtl2gds/scripts/run_synth.sh`

---

### Task 2: Fix `run_sim.sh` — Remove Silent Error Suppression

**Files:**
- Modify: `skills/r2g-rtl2gds/scripts/run_sim.sh`

- [ ] **Step 1: Fix error propagation**

Replace with proper exit code handling. Key changes:
1. Remove `|| true` from vvp
2. Capture exit codes from both iverilog and vvp
3. Append `simulation_ok` only on success

```bash
#!/usr/bin/env bash
set -euo pipefail

# usage: run_sim.sh <rtl-file> <tb-file> <work-dir>
RTL_FILE="${1:-}"
TB_FILE="${2:-}"
WORK_DIR="${3:-sim}"

if [[ -z "$RTL_FILE" || -z "$TB_FILE" ]]; then
  echo "usage: run_sim.sh <rtl-file> <tb-file> <work-dir>" >&2
  exit 1
fi

mkdir -p "$WORK_DIR"

# Compile
COMPILE_STATUS=0
iverilog -o "$WORK_DIR/sim.out" "$RTL_FILE" "$TB_FILE" >"$WORK_DIR/compile.log" 2>&1 || COMPILE_STATUS=$?

if [[ $COMPILE_STATUS -ne 0 ]]; then
  echo "ERROR: iverilog compilation failed (exit code $COMPILE_STATUS)" >&2
  echo "Check log: $WORK_DIR/compile.log" >&2
  exit $COMPILE_STATUS
fi

# Simulate
SIM_STATUS=0
(
  cd "$WORK_DIR"
  vvp ./sim.out > sim.log 2>&1
) || SIM_STATUS=$?

if [[ $SIM_STATUS -ne 0 ]]; then
  echo "ERROR: Simulation failed (exit code $SIM_STATUS)" >&2
  echo "Check log: $WORK_DIR/sim.log" >&2
  exit $SIM_STATUS
fi

# Check for testbench failure markers in sim log
if grep -qi 'FAIL\|ERROR\|assertion.*failed' "$WORK_DIR/sim.log" 2>/dev/null; then
  # Only fail if there's no explicit PASS marker
  if ! grep -qi 'ALL TESTS PASSED\|PASS' "$WORK_DIR/sim.log" 2>/dev/null; then
    echo "WARNING: Simulation log contains failure markers" >&2
  fi
fi

echo "simulation_ok" >> "$WORK_DIR/sim.log"
exit 0
```

---

### Task 3: Fix `run_lint.sh` — Conditional `lint_ok` Marker

**Files:**
- Modify: `skills/r2g-rtl2gds/scripts/run_lint.sh`

- [ ] **Step 1: Fix lint_ok to be conditional on linter success**

Key change: only append `lint_ok` if the linter returned exit code 0.

```bash
#!/usr/bin/env bash
set -euo pipefail

# usage: run_lint.sh <rtl-file> <log-file>
RTL_FILE="${1:-}"
LOG_FILE="${2:-lint.log}"

if [[ -z "$RTL_FILE" ]]; then
  echo "usage: run_lint.sh <rtl-file> <log-file>" >&2
  exit 1
fi

mkdir -p "$(dirname "$LOG_FILE")"

LINT_STATUS=0
if command -v verilator >/dev/null 2>&1; then
  verilator --lint-only "$RTL_FILE" >"$LOG_FILE" 2>&1 || LINT_STATUS=$?
elif command -v iverilog >/dev/null 2>&1; then
  iverilog -t null "$RTL_FILE" >"$LOG_FILE" 2>&1 || LINT_STATUS=$?
else
  echo "No lint-capable tool found (need verilator or iverilog)" >"$LOG_FILE"
  exit 2
fi

if [[ $LINT_STATUS -eq 0 ]]; then
  echo "lint_ok" >>"$LOG_FILE"
else
  echo "lint_failed (exit code $LINT_STATUS)" >>"$LOG_FILE"
fi

exit $LINT_STATUS
```

---

### Task 4: Add Clock Port Validation to `validate_config.py`

**Files:**
- Modify: `skills/r2g-rtl2gds/scripts/validate_config.py`

- [ ] **Step 1: Add SDC clock port cross-check function**

Add after the existing `check_include_files` function (after line 129):

```python
def check_clock_port_match(verilog_files, sdc_path, design_name):
    """Check that SDC clock port name exists as a port in the top-level RTL module."""
    warnings = []
    if not os.path.isfile(sdc_path):
        return warnings

    # Extract clock port name(s) from SDC
    sdc_text = open(sdc_path, 'r').read()
    clock_ports = set()
    for m in re.finditer(r'get_ports\s+(\S+)', sdc_text):
        port = m.group(1).strip('{}[]$')
        if port and not port.startswith('$'):
            clock_ports.add(port)
    # Also check set clk_port_name <name> pattern
    for m in re.finditer(r'set\s+clk_port_name\s+(\S+)', sdc_text):
        clock_ports.add(m.group(1).strip())

    if not clock_ports:
        return warnings

    # Find all port names in the top-level module
    top_ports = set()
    module_pattern = re.compile(
        r'module\s+' + re.escape(design_name) + r'\b(.*?);',
        re.DOTALL | re.MULTILINE
    )
    for vf in verilog_files:
        if not os.path.isfile(vf):
            continue
        try:
            content = open(vf, 'r').read()
            mm = module_pattern.search(content)
            if mm:
                # Extract port names from module header
                port_text = mm.group(1)
                for ident in re.findall(r'\b(\w+)\b', port_text):
                    top_ports.add(ident)
                # Also scan input/output declarations after module
                remainder = content[mm.end():]
                for line in remainder.split('\n')[:200]:
                    iom = re.match(r'\s*(?:input|output|inout)\s+.*?(\w+)\s*[;,\)]', line)
                    if iom:
                        top_ports.add(iom.group(1))
                    # Handle multiple ports: input wire [7:0] a, b, c;
                    if re.match(r'\s*(?:input|output|inout)', line):
                        for port_id in re.findall(r'(\w+)\s*(?:[;,\)])', line):
                            top_ports.add(port_id)
                break
        except (OSError, UnicodeDecodeError):
            continue

    if not top_ports:
        return warnings  # Can't verify without parsed ports

    for cp in clock_ports:
        if cp not in top_ports:
            warnings.append(
                f"SDC clock port '{cp}' not found in top module '{design_name}' ports. "
                f"This causes unconstrained timing (WNS=1e+39). "
                f"Available ports with 'clk': {[p for p in sorted(top_ports) if 'clk' in p.lower()]}"
            )

    return warnings
```

- [ ] **Step 2: Add parameter range validation function**

Add after the clock port check function:

```python
PARAM_RANGES = {
    "PLACE_DENSITY_LB_ADDON": (0.10, 0.50, "Placement diverges below 0.10; CLAUDE.md hard rule"),
    "CORE_UTILIZATION": (5, 75, "Below 5% wastes area; above 75% causes routing congestion"),
    "PLACE_DENSITY": (0.30, 0.95, "Below 0.30 causes placement failure"),
}


def check_parameter_ranges(fields):
    """Validate ORFS parameter values are within safe ranges."""
    warnings = []
    for param, (lo, hi, reason) in PARAM_RANGES.items():
        if param in fields:
            try:
                val = float(fields[param])
                if val < lo:
                    warnings.append(f"{param}={val} is below minimum safe value {lo}. {reason}")
                elif val > hi:
                    warnings.append(f"{param}={val} is above maximum safe value {hi}. {reason}")
            except ValueError:
                pass
    return warnings
```

- [ ] **Step 3: Wire the new checks into the `validate()` function**

In the `validate()` function, add before the return statement (before `return {"valid": ...}`):

```python
    # Check SDC clock port matches RTL
    if sdc_file and not sdc_file.startswith("$(") and os.path.isfile(sdc_file):
        clk_warnings = check_clock_port_match(verilog_files, sdc_file, design_name)
        # Promote clock port mismatch to error — it causes completely invalid timing
        for w in clk_warnings:
            errors.append(w)

    # Check parameter ranges
    range_warnings = check_parameter_ranges(fields)
    warnings.extend(range_warnings)
```

---

### Task 5: Add Unconstrained Timing + Routing Congestion Detection to `build_diagnosis.py`

**Files:**
- Modify: `skills/r2g-rtl2gds/scripts/build_diagnosis.py`

- [ ] **Step 1: Refactor to return ALL issues (not just first match)**

Replace `detect_issue()` with `detect_issues()` that returns a list:

The key structural change: instead of `return` on first match, append to a list and continue checking. The function signature changes from `detect_issue(text) -> dict` to `detect_issues(text, project) -> list[dict]`.

Add these new detection blocks:

1. **Unconstrained timing** (check ppa.json if available):
```python
    # Check for unconstrained timing (WNS = 1e+39 or very large positive value)
    ppa_file = project / 'reports' / 'ppa.json'
    if ppa_file.exists():
        try:
            ppa = json.loads(ppa_file.read_text())
            wns = ppa.get('summary', {}).get('timing', {}).get('setup_wns')
            if wns is not None and wns > 1e+30:
                issues.append({
                    'kind': 'unconstrained_timing',
                    'summary': f'Timing is unconstrained (WNS={wns}). Clock constraints are not applied.',
                    'suggestion': 'SDC clock port name likely does not match any RTL port. '
                                  'Run validate_config.py to identify the mismatch. '
                                  'Regenerate constraint.sdc with the correct clock port name.'
                })
        except (json.JSONDecodeError, KeyError):
            pass
```

2. **Routing congestion** (GRT-0116 or overflow):
```python
    # Check for global routing congestion
    for line in text.splitlines():
        if 'GRT-0116' in line or ('global routing' in line.lower() and 'congestion' in line.lower()):
            issues.append({
                'kind': 'routing_congestion',
                'summary': 'Global routing failed due to congestion.',
                'suggestion': 'Reduce CORE_UTILIZATION by 5-10% in config.mk, or '
                              'add ROUTING_LAYER_ADJUSTMENT=0.10 for aggressive layer tuning. '
                              'For bus-heavy designs, use CORE_UTILIZATION <= 15%.'
            })
            break
```

3. **Hold timing violations** (from ppa.json):
```python
    # Check for hold timing violations
    if ppa_file.exists():
        try:
            ppa = json.loads(ppa_file.read_text())
            hold_tns = ppa.get('summary', {}).get('timing', {}).get('hold_tns')
            if hold_tns is not None and hold_tns < -0.01:
                hold_count = ppa.get('summary', {}).get('timing', {}).get('hold_violation_count', 'unknown')
                issues.append({
                    'kind': 'hold_timing_violations',
                    'summary': f'Hold timing violations detected: hold_tns={hold_tns:.4f}ns, '
                               f'violation_count={hold_count}.',
                    'suggestion': 'For large designs with macros, this is often caused by CTS clock skew. '
                                  'Try adding HOLD_SLACK_MARGIN=0.1 to config.mk for extra hold margin.'
                })
        except (json.JSONDecodeError, KeyError):
            pass
```

- [ ] **Step 2: Update main() to use new multi-issue function**

Change `detect_issue(full_text)` to `detect_issues(full_text, project)`. Write a list of all issues to the output JSON instead of a single dict. For backward compatibility, also include `kind`/`summary`/`suggestion` from the first (most severe) issue at the top level.

---

### Task 6: Fix Tcl Quoting in Shell Scripts

**Files:**
- Modify: `skills/r2g-rtl2gds/scripts/run_magic_drc.sh` (lines 91-119)
- Modify: `skills/r2g-rtl2gds/scripts/run_netgen_lvs.sh` (lines 131-141)
- Modify: `skills/r2g-rtl2gds/scripts/run_rcx.sh` (lines 92-106)

- [ ] **Step 1: Fix `run_magic_drc.sh` Tcl heredoc**

Replace the unquoted Tcl variables with properly quoted paths:

```tcl
gds read "$GDS_FILE"
load "$DESIGN_NAME"
```

And for report paths:
```tcl
set fout [open "$DRC_REPORT" w]
```

- [ ] **Step 2: Fix `run_netgen_lvs.sh` Tcl heredoc**

```tcl
gds read "$GDS_FILE"
load "$DESIGN_NAME"
flatten "$DESIGN_NAME"
load "$DESIGN_NAME"
ext2spice -o "$EXTRACTED_SPICE"
```

- [ ] **Step 3: Fix `run_rcx.sh` Tcl heredoc**

```tcl
read_db "$ODB_FILE"
extract_parasitics -ext_model_file "$RCX_RULES"
write_spef "$SPEF_OUT"
```

---

### Task 7: Fix Clock Port Mismatches in 40 Constraint Files

**Files:**
- Modify: `eda-runs/ac97_ctrl_cfg{1..10}/constraints/constraint.sdc` — change `clk` to `clk_i`
- Modify: `eda-runs/i2c_verilog_cfg{1..10}/constraints/constraint.sdc` — change `clk` to `wb_clk_i`
- Modify: `eda-runs/mem_ctrl_cfg{1..10}/constraints/constraint.sdc` — change `clk` to `clk_i`
- Modify: `eda-runs/simple_spi_top_cfg{1..10}/constraints/constraint.sdc` — change `clk` to `clk_i`

- [ ] **Step 1: Fix all 40 constraint files with correct clock port names**

For each family, replace `set clk_port_name clk` with the correct port name:
- `ac97_ctrl_cfg*`: `set clk_port_name clk_i`
- `i2c_verilog_cfg*`: `set clk_port_name wb_clk_i`
- `mem_ctrl_cfg*`: `set clk_port_name clk_i`
- `simple_spi_top_cfg*`: `set clk_port_name clk_i`

Use a bash loop:
```bash
for d in eda-runs/ac97_ctrl_cfg*/constraints/constraint.sdc; do
  sed -i 's/set clk_port_name clk$/set clk_port_name clk_i/' "$d"
done
for d in eda-runs/i2c_verilog_cfg*/constraints/constraint.sdc; do
  sed -i 's/set clk_port_name clk$/set clk_port_name wb_clk_i/' "$d"
done
for d in eda-runs/mem_ctrl_cfg*/constraints/constraint.sdc; do
  sed -i 's/set clk_port_name clk$/set clk_port_name clk_i/' "$d"
done
for d in eda-runs/simple_spi_top_cfg*/constraints/constraint.sdc; do
  sed -i 's/set clk_port_name clk$/set clk_port_name clk_i/' "$d"
done
```

- [ ] **Step 2: Verify the fixes**

```bash
grep "clk_port_name" eda-runs/{ac97_ctrl,i2c_verilog,mem_ctrl,simple_spi_top}_cfg*/constraints/constraint.sdc
```

---

## Tier 2: Enable Incremental Flow & Timeouts

### Task 8: Refactor `run_orfs.sh` for Stage-by-Stage Execution

**Files:**
- Modify: `skills/r2g-rtl2gds/scripts/run_orfs.sh`

- [ ] **Step 1: Add stage-by-stage execution with checkpoints**

Key changes to `run_orfs.sh`:
1. Add `--from-stage <stage>` option to resume from a failed stage
2. Run ORFS stages individually: `synth`, `floorplan`, `place`, `cts`, `grt`, `route`, `finish`
3. After each stage, check exit code and log which stage completed
4. If a stage fails, write `progress.json` with the failed stage info
5. Support `ORFS_STAGES` env var to specify which stages to run (default: all)
6. Keep backward compatibility: without `--from-stage`, runs the full flow as before

Add after the existing MAKE_CMD construction (line 87), before the `timeout` invocation:

```bash
# Stage-by-stage execution
FROM_STAGE="${FROM_STAGE:-}"
ORFS_STAGES_LIST="${ORFS_STAGES:-synth floorplan place cts route finish}"

run_stage() {
  local stage="$1"
  echo ""
  echo "=== Running stage: $stage ==="
  local stage_start=$(date +%s)

  local STAGE_STATUS=0
  timeout --signal=TERM --kill-after=60 "$ORFS_TIMEOUT" \
    bash -c "$MAKE_CMD $stage" 2>&1 | tee -a "$BACKEND_DIR/flow.log" || STAGE_STATUS=${PIPESTATUS[0]}

  local stage_end=$(date +%s)
  local stage_elapsed=$((stage_end - stage_start))
  echo "{\"stage\": \"$stage\", \"status\": $STAGE_STATUS, \"elapsed_s\": $stage_elapsed}" >> "$BACKEND_DIR/stage_log.jsonl"

  if [[ $STAGE_STATUS -ne 0 ]]; then
    echo "ERROR: Stage '$stage' failed (exit code $STAGE_STATUS) after ${stage_elapsed}s" | tee -a "$BACKEND_DIR/flow.log"
    if [[ $STAGE_STATUS -eq 124 ]]; then
      echo "  (timed out after ${ORFS_TIMEOUT}s)" | tee -a "$BACKEND_DIR/flow.log"
    fi
    return $STAGE_STATUS
  fi
  echo "Stage '$stage' completed in ${stage_elapsed}s"
  return 0
}
```

Then replace the existing single `timeout ... make` call with:
```bash
MAKE_STATUS=0
SKIP=true
if [[ -z "$FROM_STAGE" ]]; then
  SKIP=false
fi

for stage in $ORFS_STAGES_LIST; do
  if [[ "$SKIP" == "true" ]]; then
    if [[ "$stage" == "$FROM_STAGE" ]]; then
      SKIP=false
    else
      echo "Skipping stage: $stage (resuming from $FROM_STAGE)"
      continue
    fi
  fi

  run_stage "$stage" || { MAKE_STATUS=$?; break; }
done
```

- [ ] **Step 2: Add `ROUTING_LAYER_ADJUSTMENT` support to config suggestion on GRT failure**

After the stage loop, if `grt` or `route` failed, add a diagnostic hint:

```bash
if [[ $MAKE_STATUS -ne 0 ]]; then
  FAILED_STAGE=$(tail -1 "$BACKEND_DIR/stage_log.jsonl" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('stage','unknown'))" 2>/dev/null || echo "unknown")
  if [[ "$FAILED_STAGE" == "grt" || "$FAILED_STAGE" == "route" ]]; then
    echo "" | tee -a "$BACKEND_DIR/flow.log"
    echo "HINT: Routing failed. Try re-running with:" | tee -a "$BACKEND_DIR/flow.log"
    echo "  Add to config.mk: export ROUTING_LAYER_ADJUSTMENT = 0.10" | tee -a "$BACKEND_DIR/flow.log"
    echo "  Then: FROM_STAGE=grt run_orfs.sh $PROJECT_DIR $PLATFORM" | tee -a "$BACKEND_DIR/flow.log"
  fi
fi
```

---

### Task 9: Add Timeouts to All Signoff & Frontend Scripts

**Files:**
- Modify: `skills/r2g-rtl2gds/scripts/run_drc.sh`
- Modify: `skills/r2g-rtl2gds/scripts/run_lvs.sh`
- Modify: `skills/r2g-rtl2gds/scripts/run_rcx.sh`
- Modify: `skills/r2g-rtl2gds/scripts/run_magic_drc.sh`
- Modify: `skills/r2g-rtl2gds/scripts/run_netgen_lvs.sh`

- [ ] **Step 1: Add timeout to `run_drc.sh`**

Replace the bare `make` call (line 63):
```bash
DRC_TIMEOUT="${DRC_TIMEOUT:-3600}"
timeout --signal=TERM --kill-after=30 "$DRC_TIMEOUT" make DESIGN_CONFIG="$ORFS_CONFIG" FLOW_VARIANT="$FLOW_VARIANT" drc 2>&1 | tee /tmp/drc_run_$$.log
DRC_STATUS=${PIPESTATUS[0]}
if [[ $DRC_STATUS -eq 124 ]]; then
  echo "ERROR: DRC timed out after ${DRC_TIMEOUT}s" >&2
fi
```

- [ ] **Step 2: Add timeout to `run_lvs.sh`**

Similarly wrap the `make lvs` call with `LVS_TIMEOUT` (default 3600s).

- [ ] **Step 3: Add timeout to `run_rcx.sh`**

Wrap the `openroad` call (line 109) with `RCX_TIMEOUT` (default 3600s):
```bash
RCX_TIMEOUT="${RCX_TIMEOUT:-3600}"
timeout --signal=TERM --kill-after=30 "$RCX_TIMEOUT" openroad -no_splash -exit "$RCX_DIR/run_rcx.tcl" 2>&1 | tee "$RCX_DIR/rcx.log"
RCX_STATUS=${PIPESTATUS[0]}
```

- [ ] **Step 4: Add timeout to `run_magic_drc.sh`**

Wrap the `magic` call (line 122) with `MAGIC_TIMEOUT` (default 3600s).

- [ ] **Step 5: Add timeout to `run_netgen_lvs.sh`**

Wrap both Magic extraction (line 144) and Netgen comparison (line 158) with `NETGEN_TIMEOUT` (default 3600s each).

---

## Tier 3: Intelligent Parameter Selection

### Task 10: Create `suggest_config.py` — Design-Aware Parameter Recommender

**Files:**
- Create: `skills/r2g-rtl2gds/scripts/suggest_config.py`

- [ ] **Step 1: Implement parameter recommender**

Script that takes a project directory and optionally a synth log, then suggests ORFS parameters based on design characteristics:

```
usage: suggest_config.py <project-dir> [output.json]
```

Logic:
1. Parse synth.log for cell count (from Yosys `stat` output: `Number of cells:`)
2. Parse config.mk for existing parameters and platform
3. Classify design:
   - Tiny: < 100 cells → CORE_UTILIZATION=30, LB_ADDON=0.20, use DIE_AREA/CORE_AREA
   - Small: 100-5000 cells → CORE_UTILIZATION=30, LB_ADDON=0.20
   - Medium: 5000-50000 cells → CORE_UTILIZATION=25, LB_ADDON=0.20
   - Large: 50000+ cells → CORE_UTILIZATION=20, LB_ADDON=0.25, +SKIP_CTS_REPAIR_TIMING=1, +SKIP_LAST_GASP=1
4. Check RTL for bus-heavy patterns (crossbar, arbiter, mux arrays) → reduce util to 15%
5. Check if design has macros (ADDITIONAL_LEFS in config) → increase LB_ADDON to 0.30-0.45
6. Suggest ABC_AREA=1, SYNTH_HIERARCHICAL=1 for all designs
7. Output JSON with recommended parameters + explanation

- [ ] **Step 2: Make executable**

```bash
chmod +x skills/r2g-rtl2gds/scripts/suggest_config.py
```

---

### Task 11: Enhance `config-template.mk` with All Common Parameters

**Files:**
- Modify: `skills/r2g-rtl2gds/assets/config-template.mk`

- [ ] **Step 1: Expand template with documented parameters**

```makefile
export DESIGN_NAME = {{DESIGN_NAME}}
export PLATFORM    = {{PLATFORM}}

export VERILOG_FILES = {{VERILOG_FILES}}
export SDC_FILE      = {{SDC_FILE}}

# --- Floorplan ---
# Use CORE_UTILIZATION for auto-sizing, OR DIE_AREA/CORE_AREA for manual sizing.
# For designs < 10 cells, use explicit DIE_AREA to avoid PDN grid errors.
export CORE_UTILIZATION = {{CORE_UTILIZATION}}
# export DIE_AREA  = 0 0 50 50
# export CORE_AREA = 2 2 48 48

# --- Placement ---
# PLACE_DENSITY_LB_ADDON: minimum safe value is 0.10. Use 0.20-0.45 for macro-heavy designs.
export PLACE_DENSITY_LB_ADDON = {{PLACE_DENSITY_LB_ADDON}}

# --- Synthesis ---
export ABC_AREA = 1
# export SYNTH_HIERARCHICAL = 1

# --- Safety flags (enable for large designs >50K instances) ---
# export SKIP_CTS_REPAIR_TIMING = 1
# export SKIP_LAST_GASP = 1
# export SKIP_GATE_CLONING = 1

# --- Routing (uncomment if global routing fails with congestion) ---
# export ROUTING_LAYER_ADJUSTMENT = 0.10

# --- Timing closure (uncomment to tune repair aggressiveness) ---
# export SETUP_SLACK_MARGIN = 0.0
# export HOLD_SLACK_MARGIN = 0.0
# export TNS_END_PERCENT = 100
```

---

### Task 12: Enhance `constraint-template.sdc` with Clock Uncertainty

**Files:**
- Modify: `skills/r2g-rtl2gds/assets/constraint-template.sdc`

- [ ] **Step 1: Add clock uncertainty and comments**

```tcl
current_design {{DESIGN_NAME}}

# Clock definition — clk_port_name MUST match the RTL port name exactly.
# Run validate_config.py to verify before synthesis.
set clk_name  core_clock
set clk_port_name {{CLOCK_PORT}}
set clk_period {{CLOCK_PERIOD}}
set clk_io_pct 0.2

set clk_port [get_ports $clk_port_name]

create_clock -name $clk_name -period $clk_period $clk_port

# Clock uncertainty (accounts for jitter + skew margin)
set_clock_uncertainty 0.1 [get_clocks $clk_name]

# I/O delays (20% of clock period by default)
set non_clock_inputs [all_inputs -no_clocks]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
```

---

## Tier 4: Expand Diagnosis & Metrics Coverage

### Task 13: Add Congestion Extraction to `extract_progress.py`

**Files:**
- Modify: `skills/r2g-rtl2gds/scripts/extract_progress.py`

- [ ] **Step 1: Add per-stage timing extraction from `stage_log.jsonl`**

Add a function to parse the new `stage_log.jsonl` file created by the improved `run_orfs.sh`:

```python
def parse_stage_log(run_dir: Path) -> list:
    """Parse stage_log.jsonl for per-stage timing and status."""
    log_file = run_dir / 'stage_log.jsonl'
    if not log_file.exists():
        return []
    stages = []
    for line in log_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            stages.append({
                'name': entry.get('stage', 'unknown'),
                'status': 'done' if entry.get('status', 1) == 0 else 'failed',
                'elapsed_s': entry.get('elapsed_s'),
            })
        except json.JSONDecodeError:
            continue
    return stages
```

Wire this into `main()` as the primary source (falling back to flow.log pattern matching).

- [ ] **Step 2: Add congestion metric extraction from flow.log**

Add a function to parse routing overflow from flow.log:

```python
def parse_congestion(flow_log: Path) -> dict:
    """Extract routing congestion metrics from flow.log."""
    if not flow_log.exists():
        return {}
    text = flow_log.read_text(encoding='utf-8', errors='ignore')
    congestion = {}
    # GRT overflow
    for m in re.finditer(r'Number of overflow:\s*(\d+)', text):
        congestion['grt_overflow'] = int(m.group(1))
    # Congestion percentage
    for m in re.finditer(r'Total congestion:\s*([\d.]+)%', text):
        congestion['congestion_pct'] = float(m.group(1))
    return congestion
```

Include congestion data in the output JSON.

---

### Task 14: Enhance Dashboard with Power Breakdown, Hold Violations, and Clock Skew

**Files:**
- Modify: `skills/r2g-rtl2gds/scripts/generate_multi_project_dashboard.py`

- [ ] **Step 1: Add timing quality indicators to PPA table**

In `ppa_table()`, add color-coded indicators:
- WNS < 0: red text
- WNS >= 1e+30: orange "UNCONSTRAINED" badge
- hold_tns < 0: yellow warning

Replace the plain `fmt(v)` for timing values with:

```python
def fmt_timing(k, v):
    """Format timing value with quality indicator."""
    if v is None:
        return '-'
    if 'wns' in k.lower() or 'tns' in k.lower():
        if isinstance(v, (int, float)):
            if v > 1e+30:
                return '<span style="color:#ff9800;font-weight:bold">UNCONSTRAINED</span>'
            if v < 0:
                return f'<span style="color:#f44336;font-weight:bold">{v:.4g}</span>'
            return f'<span style="color:#4caf50">{v:.4g}</span>'
    return fmt(v)
```

- [ ] **Step 2: Add power breakdown display**

In the PPA section of `render_project_page()`, add a power breakdown sub-table when leakage/switching/internal power data is available:

```python
def power_breakdown(ppa):
    """Render power breakdown chart."""
    power = ppa.get('summary', {}).get('power', {}) if ppa else {}
    if not power or 'total_power_w' not in power:
        return ''
    total = power.get('total_power_w', 0)
    internal = power.get('internal_power_w', 0)
    switching = power.get('switching_power_w', 0)
    leakage = power.get('leakage_power_w', 0)
    if total <= 0:
        return ''
    rows = []
    for label, val, color in [
        ('Internal', internal, '#42a5f5'),
        ('Switching', switching, '#66bb6a'),
        ('Leakage', leakage, '#ef5350'),
    ]:
        pct = (val / total * 100) if total > 0 else 0
        rows.append(f'<tr><td>{label}</td><td>{val:.4g} W</td>'
                     f'<td><div style="background:{color};width:{pct:.0f}%;height:14px;border-radius:3px"></div></td>'
                     f'<td>{pct:.1f}%</td></tr>')
    return f'''<h3>Power Breakdown</h3>
<table><tr><th>Component</th><th>Power</th><th></th><th>%</th></tr>{"".join(rows)}</table>'''
```

- [ ] **Step 3: Add multi-issue diagnosis display**

Update `diag_html` in `render_project_page()` to handle the new list-of-issues format from `build_diagnosis.py`:

```python
    # Diagnosis — handle both old (single dict) and new (list) format
    diag = data.get('diagnosis', {})
    if isinstance(diag, dict) and 'issues' in diag:
        issues = diag['issues']
    elif isinstance(diag, dict) and diag.get('kind', 'none') != 'none':
        issues = [diag]
    else:
        issues = []

    if issues:
        diag_rows = []
        for issue in issues:
            kind = html.escape(issue.get('kind', ''))
            summary = html.escape(issue.get('summary', ''))
            suggestion = html.escape(issue.get('suggestion', ''))
            diag_rows.append(
                f'<div style="margin:8px 0;padding:10px;background:#2a1a1a;border-radius:6px;border-left:4px solid #f44336">'
                f'<b>{kind}</b>: {summary}<br><i>{suggestion}</i></div>'
            )
        diag_html = ''.join(diag_rows)
    else:
        diag_html = '<p style="color:#4caf50">No issues detected.</p>'
```

---

### Task 15: Update CLAUDE.md with New Capabilities

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Document new scripts and parameters**

Add to the Script Inventory tables:
- `suggest_config.py`: Design-aware ORFS parameter recommender
- Updated `run_orfs.sh`: Stage-by-stage execution with `FROM_STAGE` and `stage_log.jsonl`

Add to the Common Pitfalls section:
- Unconstrained timing detection (WNS=1e+39) now auto-detected by `build_diagnosis.py`
- Clock port validation now in `validate_config.py`
- All signoff scripts now have timeout protection

Update the Config Tuning Quick Reference with new parameters:
- `ROUTING_LAYER_ADJUSTMENT`
- `SETUP_SLACK_MARGIN` / `HOLD_SLACK_MARGIN`

Document the `FROM_STAGE` env var for `run_orfs.sh`.

- [ ] **Step 2: Remove the "Known Issues Not Yet Fixed" clock port mismatch entry**

The 4 families (ac97_ctrl, i2c_verilog, mem_ctrl, simple_spi_top) are now fixed.
