# Fix Skill Scripts & Improve Layout Quality — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 3 buggy extraction/diagnosis scripts in the `r2g-rtl2gds` skill and improve layout quality for one representative config per affected design family.

**Architecture:** All changes target `skills/r2g-rtl2gds/scripts/` (the permanent skill code). Each design family has 10 backend configs — we fix one representative config first. Verify each script fix against real design data in `eda-runs/`.

**Tech Stack:** Python 3, Bash, OpenROAD-flow-scripts, KLayout, Yosys

**Scope:** 7 tasks covering 3 script bugs + 4 categories of physical design issues. Each task is self-contained and independently testable.

---

## File Map

| File | Responsibility | Action |
|------|---------------|--------|
| `skills/r2g-rtl2gds/scripts/extract_ppa.py` | Parse PPA metrics from ORFS reports | Fix: read timing/power from `6_report.json` instead of regex on flow.log |
| `skills/r2g-rtl2gds/scripts/extract_lvs.py` | Parse LVS results from KLayout lvsdb | Fix: handle non-XML lvsdb format, fix contraction matching |
| `skills/r2g-rtl2gds/scripts/build_diagnosis.py` | Detect issues from flow logs | Fix: eliminate false positives in pattern matching |
| `skills/r2g-rtl2gds/references/failure-patterns.md` | Document known failure modes | Update: add antenna DRC, hold violations, IR-drop, unconstrained timing |

Temporary test designs (one representative per family, in `eda-runs/`):

| Issue | Representative Config | Verification |
|-------|----------------------|--------------|
| False LVS clean | `eda-runs/riscv32i_cfg1/` | Re-run `extract_lvs.py`, confirm status=`fail` |
| Bogus PPA timing | `eda-runs/swerv_cfg1/` | Re-run `extract_ppa.py`, confirm real hold TNS |
| False diagnosis | `eda-runs/fifo_cfg1/` | Re-run `build_diagnosis.py`, confirm no false positive |
| DRC antenna violations | `eda-runs/fifo_cfg1/` | Existing DRC data shows 56 violations |
| Hold timing violations | `eda-runs/swerv_cfg1/` | Existing `6_report.json` shows hold TNS=-0.507ns |
| Clock port mismatch | `eda-runs/ac97_ctrl_cfg2/` | Inspect SDC vs RTL port names |
| RTL syntax error | `eda-runs/usbf_top_cfg1/` | Inspect failing Verilog file |

---

### Task 1: Fix `extract_lvs.py` — False-Clean Bug

**Problem:** The script reports `status: "clean"` for riscv32i designs whose LVS log says `"ERROR : Netlists don't match"`. Two bugs:
1. KLayout lvsdb uses `#%lvsdb-klayout` text format (not XML). `ET.parse()` throws `ParseError`, fallback counts substring `"mismatch"` which may be 0 even when netlists don't match.
2. Log check (line 71) looks for `"netlists do not match"` but KLayout uses the contraction `"don't match"`.

**Files:**
- Modify: `skills/r2g-rtl2gds/scripts/extract_lvs.py`
- Test data: `eda-runs/riscv32i_cfg1/lvs/6_lvs.log`, `eda-runs/riscv32i_cfg1/lvs/6_lvs.lvsdb`

- [ ] **Step 1: Read current riscv32i LVS artifacts to confirm the bug**

```bash
# Check what the log actually says
grep -i "match\|error" eda-runs/riscv32i_cfg1/lvs/6_lvs.log | head -10
# Check current lvs.json output
cat eda-runs/riscv32i_cfg1/reports/lvs.json
# Check lvsdb format (first line)
head -1 eda-runs/riscv32i_cfg1/lvs/6_lvs.lvsdb
```

Expected: log contains `"ERROR : Netlists don't match"`, lvs.json says `"status": "clean"`, lvsdb starts with `#%lvsdb-klayout` (not XML).

- [ ] **Step 2: Fix the contraction matching in `parse_lvs_log`**

In `extract_lvs.py` line 71, change the log status check to also match `"don't match"`:

```python
# Line 69-72: add "don't match" pattern
if 'netlists match' in lower or 'lvs clean' in lower:
    info['log_status'] = 'match'
elif "netlists don't match" in lower or 'netlists do not match' in lower or 'mismatch' in lower:
    info['log_status'] = 'mismatch'
```

But be careful: `"netlists match"` is a substring of `"netlists don't match"`. Check the negative case first:

```python
if "don't match" in lower or 'do not match' in lower or 'not match' in lower:
    info['log_status'] = 'mismatch'
elif 'netlists match' in lower or 'lvs clean' in lower or 'circuits match' in lower:
    info['log_status'] = 'match'
elif 'not supported' in lower:
    info['log_status'] = 'not_supported'
```

- [ ] **Step 3: Fix the lvsdb fallback parser**

In `extract_lvs.py` lines 46-52, improve the `ParseError` fallback to not blindly set `mismatch_count = 0`:

```python
except ET.ParseError:
    # KLayout lvsdb may use text format (#%lvsdb-klayout), not XML
    text = lvsdb_file.read_text(encoding='utf-8', errors='ignore')
    lower_text = text.lower()
    if 'mismatch' in lower_text:
        result['raw_status'] = 'text_mismatch_found'
        # Count actual mismatch lines (not just substring occurrences)
        result['mismatch_count'] = sum(1 for line in text.splitlines() if 'mismatch' in line.lower())
    elif "don't match" in lower_text or 'not match' in lower_text:
        result['raw_status'] = 'text_not_match'
        result['mismatch_count'] = -1  # unknown count, but known mismatch
    elif 'match' in lower_text:
        result['raw_status'] = 'text_match_found'
        result['mismatch_count'] = 0
    else:
        result['raw_status'] = 'text_unparsed'
        # Do NOT set mismatch_count — leave it absent so status logic doesn't assume clean
```

- [ ] **Step 4: Fix status determination logic**

In `extract_lvs.py` line 120, don't let a missing/defaulted `mismatch_count` override a clear log mismatch. Change the logic so `log_status` takes priority:

```python
if log_status == 'mismatch':
    status = 'fail'
elif log_status == 'match' and mismatch_count <= 0:
    status = 'clean'
elif mismatch_count > 0:
    status = 'fail'
elif mismatch_count == 0 and log_status == '':
    status = 'clean'  # lvsdb says clean, no log to contradict
elif log_status == 'not_supported':
    status = 'skipped'
else:
    status = 'unknown'
```

- [ ] **Step 5: Verify the fix against riscv32i_cfg1**

```bash
cd /data/shenshan/agent_with_openroad
python3 skills/r2g-rtl2gds/scripts/extract_lvs.py eda-runs/riscv32i_cfg1 /tmp/test_lvs.json
cat /tmp/test_lvs.json
```

Expected: `"status": "fail"` (not "clean").

- [ ] **Step 6: Verify no regression on a known-clean design**

```bash
python3 skills/r2g-rtl2gds/scripts/extract_lvs.py eda-runs/fifo_v3 /tmp/test_lvs_clean.json
cat /tmp/test_lvs_clean.json
```

Expected: `"status": "clean"`.

---

### Task 2: Fix `extract_ppa.py` — Read Timing/Power from 6_report.json

**Problem:** The regex `r'tns\s+([-\d.]+)'` on flow.log matches the ORFS command `-repair_tns 100` instead of the actual TNS value. Every design gets `setup_tns: 100.0`. The script already reads `6_report.json` for geometry but doesn't extract timing or power from it.

**Files:**
- Modify: `skills/r2g-rtl2gds/scripts/extract_ppa.py`
- Test data: `eda-runs/swerv_cfg1/backend/` (latest RUN directory)

- [ ] **Step 1: Check what timing/power keys exist in a real 6_report.json**

```bash
# Find a 6_report.json for swerv_cfg1
find eda-runs/swerv_cfg1/backend -name "6_report.json" | tail -1
# List all timing/power keys
python3 -c "
import json, sys
rj = json.load(open(sys.argv[1]))
for k,v in sorted(rj.items()):
    if 'timing' in k or 'power' in k or 'slack' in k or 'tns' in k or 'wns' in k:
        print(f'{k} = {v}')
" $(find eda-runs/swerv_cfg1/backend -name "6_report.json" | tail -1)
```

- [ ] **Step 2: Add timing/power extraction from 6_report.json**

After the existing geometry extraction block (around line 213), add timing and power extraction from the same `6_report.json`:

```python
# Extract timing from 6_report.json (more reliable than flow.log regex)
# NOTE: The flow.log fallback (lines 176-185) is deliberately kept for
# incomplete runs where 6_report.json doesn't exist. This block overwrites
# those values only when reliable data is available.
timing_map = {
    'setup_wns': 'finish__timing__setup__ws',
    'setup_tns': 'finish__timing__setup__tns',
    'hold_wns': 'finish__timing__hold__ws',
    'hold_tns': 'finish__timing__hold__tns',
    'clock_skew_setup': 'finish__clock__skew__setup',
    'clock_skew_hold': 'finish__clock__skew__hold',
    'setup_violation_count': 'finish__timing__drv__setup_violation_count',
    'hold_violation_count': 'finish__timing__drv__hold_violation_count',
    'max_cap_violations': 'finish__timing__drv__max_cap',
    'max_slew_violations': 'finish__timing__drv__max_slew',
}
report_timing = {}
for out_key, json_key in timing_map.items():
    if json_key in rj:
        report_timing[out_key] = rj[json_key]
if report_timing:
    ppa['summary']['timing'] = report_timing

# Extract power from 6_report.json
power_map = {
    'total_power_w': 'finish__power__total',
    'internal_power_w': 'finish__power__internal__total',
    'switching_power_w': 'finish__power__switching__total',
    'leakage_power_w': 'finish__power__leakage__total',
}
report_power = {}
for out_key, json_key in power_map.items():
    if json_key in rj:
        report_power[out_key] = rj[json_key]
if report_power:
    ppa['summary']['power'] = report_power
```

This should **replace** the flow.log-parsed values when `6_report.json` is available — put this block so it overwrites the earlier parsed values.

- [ ] **Step 3: Verify against swerv_cfg1**

```bash
python3 skills/r2g-rtl2gds/scripts/extract_ppa.py eda-runs/swerv_cfg1 /tmp/test_ppa.json
python3 -c "import json; d=json.load(open('/tmp/test_ppa.json')); print(json.dumps(d['summary']['timing'], indent=2)); print(json.dumps(d['summary']['power'], indent=2))"
```

Expected: `setup_tns` should NOT be 100.0. `hold_tns` should be approximately -0.507. Power fields should be populated.

- [ ] **Step 4: Verify against a simple design (fifo_cfg1)**

```bash
python3 skills/r2g-rtl2gds/scripts/extract_ppa.py eda-runs/fifo_cfg1 /tmp/test_ppa_fifo.json
python3 -c "import json; d=json.load(open('/tmp/test_ppa_fifo.json')); print(json.dumps(d['summary']['timing'], indent=2))"
```

Expected: `setup_tns` should be 0 or a small real value, not 100.0.

---

### Task 3: Fix `build_diagnosis.py` — Eliminate False Positives

**Problem:** Two false-positive patterns affect 348 out of 348 diagnosed designs:
1. `placement_utilization_overflow` (276/348): independently matches `"utilization"` and `"overflow"` from normal NesterovSolve log lines and area reports.
2. `make_error` (72/348): matches `"make: ***"` from older failed runs when the latest run succeeded.

**Files:**
- Modify: `skills/r2g-rtl2gds/scripts/build_diagnosis.py`
- Test data: `eda-runs/fifo_cfg1/` (should detect DRC issue, not utilization overflow), `eda-runs/des_cfg1/` (should detect nothing — clean design)

- [ ] **Step 1: Fix the utilization overflow check (line 65)**

Replace the broad text-wide check with a line-level check that requires both keywords on the same line, or matches specific ORFS error messages:

```python
# 2. Utilization overflow — require specific error patterns, not independent keywords
utilization_error = False
for line in text.splitlines():
    ll = line.lower()
    if ('utilization' in ll and ('exceeds' in ll or '100%' in ll)) or \
       ('[error' in ll and 'utilization' in ll):
        utilization_error = True
        break
if utilization_error:
    return {
        'kind': 'placement_utilization_overflow',
        'summary': 'Placement failed because utilization is too high.',
        'suggestion': 'Reduce CORE_UTILIZATION in config.mk or simplify the design.'
    }
```

- [ ] **Step 2: Fix the make_error check (line 145)**

Only flag make errors if they appear in the **latest** flow.log section (not in historical logs). Since `detect_issue` receives the full concatenated text with `=== flow.log ===` markers, we can check the flow.log section specifically. Alternatively, since `main()` already reads only the latest run's flow.log (line 183-188), the real issue is that `make: ***` appears in non-flow logs or in flow.log as informational output.

Tighten the pattern to require it at line start or as a standalone error indicator, and move it below other more specific checks so it doesn't shadow them:

```python
# 9. Make/build errors — only match if flow.log section has make error at end
make_error_found = False
flow_section = ''
for section in text.split('=== '):
    if section.startswith('flow.log'):
        flow_section = section
        break
if flow_section:
    # Check last 50 lines of flow.log for make errors
    flow_lines = flow_section.strip().splitlines()
    tail = '\n'.join(flow_lines[-50:]).lower()
    if 'make: ***' in tail or ('error' in tail and 'exit status' in tail):
        make_error_found = True
if make_error_found:
    return {
        'kind': 'make_error',
        'summary': 'ORFS make target failed.',
        'suggestion': 'Check flow.log for the specific failing stage and error details.'
    }
```

- [ ] **Step 3: Fix LVS contraction bug (line 129)**

The same `"don't match"` contraction bug exists here as in `extract_lvs.py`. Change line 129:

```python
# Before (misses KLayout's contraction):
if 'netlists do not match' in lower or 'lvs mismatch' in lower:

# After:
if "netlists don't match" in lower or 'netlists do not match' in lower or 'lvs mismatch' in lower:
```

- [ ] **Step 4: Add antenna DRC detection BEFORE the general DRC check**

The antenna check must go BEFORE the general DRC check (lines 114-126), otherwise the general check catches antenna violations first and returns `drc_errors` instead of the more specific `drc_antenna`. Insert this BEFORE the existing `# 8. DRC errors` block:

```python
# 8a. Antenna DRC violations (more specific, check before general DRC)
antenna_match = re.search(r'(\d+)\s*(?:antenna\s*)?violation', lower)
if antenna_match and int(antenna_match.group(1)) > 0 and 'antenna' in lower:
    return {
        'kind': 'drc_antenna',
        'summary': f'{antenna_match.group(1)} antenna DRC violations found.',
        'suggestion': 'Long metal routes accumulate charge during fabrication. Enable antenna repair in ORFS or add diode insertion as a post-route step.'
    }
```

- [ ] **Step 5: Verify against fifo_cfg1**

```bash
python3 skills/r2g-rtl2gds/scripts/build_diagnosis.py eda-runs/fifo_cfg1 /tmp/test_diag_fifo.json
cat /tmp/test_diag_fifo.json
```

Expected: Should NOT show `placement_utilization_overflow`. Should ideally show `drc_antenna` or `none` (since DRC log may not be in the standard paths).

- [ ] **Step 6: Verify against des_cfg1 (clean design)**

```bash
python3 skills/r2g-rtl2gds/scripts/build_diagnosis.py eda-runs/des_cfg1 /tmp/test_diag_des.json
cat /tmp/test_diag_des.json
```

Expected: `"kind": "none"` — no false positive.

---

### Task 4: Update `failure-patterns.md` — Add New Failure Modes

**Problem:** The reference doc doesn't cover antenna DRC, hold violations, IR-drop issues, or unconstrained timing (clock port mismatch at SDC level where the flow still completes but with meaningless timing).

**Files:**
- Modify: `skills/r2g-rtl2gds/references/failure-patterns.md`

- [ ] **Step 1: Add "Antenna DRC Violations" section**

After the existing "DRC Violations" subsection under "Signoff Check Failures", add:

```markdown
### Antenna DRC Violations

**Symptoms:**
- DRC report shows METAL*_ANTENNA violations (e.g., METAL4_ANTENNA, METAL5_ANTENNA)
- All violations are antenna-rule related; no spacing/width violations
- Violation counts vary across configs of the same design (layout-dependent)

**Root Cause:**
Long unbroken metal routes accumulate charge during plasma etching, which can damage thin gate oxides at the route endpoint. FIFO designs with deep address buses are particularly susceptible because the router creates long metal paths from address logic to SRAM-like structures.

**Action:**
- Enable ORFS antenna repair: add `export ANTENNA_CHECK = 1` and `export DIODE_INSERTION = 1` to config.mk (if supported by platform)
- If ORFS doesn't support native antenna repair: manually insert diode cells as antenna protection
- Increase die area to give the router more freedom to break long metal paths
- As a workaround, try `export DETAILED_ROUTE_ARGS = -droute_end_iteration 10` for more routing iterations
```

- [ ] **Step 2: Add "Hold Timing Violations Post-CTS" section**

```markdown
### Hold Timing Violations Post-CTS

**Symptoms:**
- `6_report.json` shows `finish__timing__hold__tns < 0` and `finish__timing__hold__ws < 0`
- Hold violation count > 0 in final timing report
- High clock skew (>1ns) reported
- Designs with many macros (>20) are most affected

**Root Cause:**
Macro-heavy designs (swerv, bp_multi_top) have high clock skew (~1.5ns) due to macro placement spreading clock sinks far apart. CTS cannot fully equalize the skew, leaving hold violations. Designs that require `SKIP_CTS_REPAIR_TIMING=1` (OpenROAD crash workaround) are especially affected since hold repair is also skipped.

**Action:**
- If `SKIP_CTS_REPAIR_TIMING=1` was set as a crash workaround, check if the OpenROAD version has been updated to fix the SIGSEGV
- Increase `CTS_CLUSTER_SIZE` or `CTS_CLUSTER_DIAMETER` to reduce clock skew
- Add post-CTS hold margin: increase SDC hold uncertainty with `set_clock_uncertainty -hold 0.05 [all_clocks]`
- As a last resort, increase clock period to give more hold margin
```

- [ ] **Step 3: Add "Unconstrained Timing (Silent Clock Mismatch)" section**

```markdown
### Unconstrained Timing (Silent Clock Mismatch)

**Symptoms:**
- Backend completes successfully with GDS output
- `6_report.json` shows very large positive WNS (e.g., 1e+38) — effectively unconstrained
- Power analysis shows >50% leakage ratio (zero switching activity assumed)
- SDC `create_clock` targets a port name that doesn't exist in RTL

**Root Cause:**
The SDC `clk_port_name` doesn't match the actual RTL clock port. OpenROAD silently skips the clock constraint when the port isn't found (no hard error), so the entire flow runs without timing constraints. Common mismatches:
- SDC uses `clk` but RTL has `clk_i` (ac97_ctrl, mem_ctrl, simple_spi_top)
- SDC uses `clk` but RTL has `wb_clk_i` (i2c_verilog)

**Action:**
- Verify clock port: `grep 'input.*clk' rtl/design.v` and compare with SDC `clk_port_name`
- Fix SDC to use the exact RTL port name
- Re-run synthesis and backend
- Prevention: add a pre-flight check in `run_orfs.sh` that warns if SDC clock port is not found in RTL
```

- [ ] **Step 4: Add "Severe IR-Drop" section**

```markdown
### Severe IR-Drop (>10% VDD)

**Symptoms:**
- `6_report.json` shows `finish__power__internal__total` is high relative to design size
- VDD worst-case voltage drop exceeds 10% of nominal (e.g., >0.11V on 1.1V supply)
- May cause functional failures in silicon at worst-case PVT corners

**Root Cause:**
Insufficient power delivery network (PDN) for the design's power density. Common in AES/crypto designs with high toggle rates and dense placement.

**Action:**
- Reduce placement density: lower `CORE_UTILIZATION` to spread cells
- Strengthen PDN: add more power straps or increase strap width (platform-dependent config)
- If possible, increase die area to reduce power density
- Check that `PLACE_DENSITY_LB_ADDON` is not causing excessive local density
```

---

### Task 5: Investigate and Document Clock Port Mismatches (4 families)

**Problem:** 4 design families (ac97_ctrl, i2c_verilog, mem_ctrl, simple_spi_top_cfg) have SDC clock port `clk` but the RTL uses a different port name. The backend completes but with meaningless timing.

This task only investigates and documents the mismatches — it does NOT re-run the backend (that's a separate, expensive step).

**Files:**
- Read: `eda-runs/ac97_ctrl_cfg2/constraints/constraint.sdc`
- Read: `eda-runs/ac97_ctrl_cfg2/rtl/` (find the RTL clock port)
- Read: `eda-runs/i2c_verilog_cfg1/constraints/constraint.sdc`
- Read: `eda-runs/i2c_verilog_cfg1/rtl/`
- Read: `eda-runs/mem_ctrl_cfg1/constraints/constraint.sdc`
- Read: `eda-runs/mem_ctrl_cfg1/rtl/`
- Read: `eda-runs/simple_spi_top_cfg1/constraints/constraint.sdc`
- Read: `eda-runs/simple_spi_top_cfg1/rtl/`

- [ ] **Step 1: For each family, confirm the clock port mismatch**

```bash
for design in ac97_ctrl_cfg2 i2c_verilog_cfg1 mem_ctrl_cfg1 simple_spi_top_cfg1; do
  echo "=== $design ==="
  echo "SDC clock port:"
  grep 'clk_port_name' eda-runs/$design/constraints/constraint.sdc
  echo "RTL clock input:"
  grep -n 'input.*clk' eda-runs/$design/rtl/*.v | head -3
  echo ""
done
```

- [ ] **Step 2: Document the correct clock port for each family**

Create a summary noting the correct clock port for future re-runs:

| Family | SDC has | RTL has | Correct value |
|--------|---------|---------|---------------|
| ac97_ctrl | `clk` | `clk_i` | `clk_i` |
| i2c_verilog | `clk` | `wb_clk_i` | `wb_clk_i` |
| mem_ctrl | `clk` | `clk_i` | `clk_i` |
| simple_spi_top_cfg | `clk` | `clk_i` | `clk_i` |

- [ ] **Step 3: Verify that the `_v` series of simple_spi_top does NOT have this issue**

```bash
grep 'clk_port_name' eda-runs/simple_spi_top_v1/constraints/constraint.sdc
grep -n 'input.*clk' eda-runs/simple_spi_top_v1/rtl/*.v | head -3
```

Expected: `simple_spi_top_v1` should have the correct clock port (this confirms the bug is limited to the `_cfg` series).

---

### Task 6: Investigate RTL Syntax Errors (2 families)

**Problem:** usbf_top and wb_dma_top fail Yosys synthesis due to Verilog reserved keyword collisions. These are documented in `failure-patterns.md` under "RTL Reserved Keywords as Identifiers" but the RTL hasn't been fixed.

**Files:**
- Read: `eda-runs/usbf_top_cfg1/rtl/` (find the offending file)
- Read: `eda-runs/wb_dma_top_cfg1/rtl/` (find the offending file)

- [ ] **Step 1: Confirm the usbf_top syntax error**

```bash
# Find the error location
grep -n 'int\|bit\|logic\|byte' eda-runs/usbf_top_cfg1/rtl/usbf_ep_rf.v | head -20
# Check the specific line from the error (line 198)
sed -n '195,205p' eda-runs/usbf_top_cfg1/rtl/usbf_ep_rf.v
```

- [ ] **Step 2: Confirm the wb_dma_top syntax error**

```bash
grep -n 'int\|bit\|logic\|byte' eda-runs/wb_dma_top_cfg1/rtl/wb_dma_ch_rf.v | head -20
sed -n '94,100p' eda-runs/wb_dma_top_cfg1/rtl/wb_dma_ch_rf.v
```

- [ ] **Step 3: Document the required renames**

For each family, identify every occurrence of the reserved keyword in every file that uses it (module definition + all instantiation sites):

```bash
# For usbf_top: find all files referencing the reserved keyword
grep -rn '\bint\b' eda-runs/usbf_top_cfg1/rtl/*.v | grep -v '//' | head -20
# For wb_dma_top:
grep -rn '\bint\b' eda-runs/wb_dma_top_cfg1/rtl/*.v | grep -v '//' | head -20
```

Document: which files need editing, which identifiers need renaming, what they should be renamed to (e.g., `int` → `int_o`).

---

### Task 7: Update Real Report Files for Representative Configs

**Purpose:** After Tasks 1-3 verification (done via `/tmp/` in each task's own steps), update the actual `reports/` files in `eda-runs/` so the dashboard and other consumers see corrected data. This is NOT re-verification — it's the commit step for the report data.

**Files:**
- Run: `skills/r2g-rtl2gds/scripts/extract_lvs.py`
- Run: `skills/r2g-rtl2gds/scripts/extract_ppa.py`
- Run: `skills/r2g-rtl2gds/scripts/build_diagnosis.py`

- [ ] **Step 1: Update LVS report for riscv32i_cfg1**

```bash
cd /data/shenshan/agent_with_openroad
python3 skills/r2g-rtl2gds/scripts/extract_lvs.py eda-runs/riscv32i_cfg1 eda-runs/riscv32i_cfg1/reports/lvs.json
```

- [ ] **Step 2: Update PPA reports for swerv_cfg1 and fifo_cfg1**

```bash
python3 skills/r2g-rtl2gds/scripts/extract_ppa.py eda-runs/swerv_cfg1 eda-runs/swerv_cfg1/reports/ppa.json
python3 skills/r2g-rtl2gds/scripts/extract_ppa.py eda-runs/fifo_cfg1 eda-runs/fifo_cfg1/reports/ppa.json
```

- [ ] **Step 3: Update diagnosis reports for des_cfg1, fifo_cfg1, swerv_cfg1, aes_cfg2**

```bash
python3 skills/r2g-rtl2gds/scripts/build_diagnosis.py eda-runs/des_cfg1 eda-runs/des_cfg1/reports/diagnosis.json
python3 skills/r2g-rtl2gds/scripts/build_diagnosis.py eda-runs/fifo_cfg1 eda-runs/fifo_cfg1/reports/diagnosis.json
python3 skills/r2g-rtl2gds/scripts/build_diagnosis.py eda-runs/swerv_cfg1 eda-runs/swerv_cfg1/reports/diagnosis.json
python3 skills/r2g-rtl2gds/scripts/build_diagnosis.py eda-runs/aes_cfg2 eda-runs/aes_cfg2/reports/diagnosis.json
```

---

## Summary of Expected Outcomes

| Task | What Changes | Designs Verified | Success Criteria |
|------|-------------|-----------------|-----------------|
| 1 | `extract_lvs.py` | riscv32i_cfg1, fifo_v3 | Mismatch detected; clean still works |
| 2 | `extract_ppa.py` | swerv_cfg1, fifo_cfg1 | Real timing/power values; no bogus TNS=100 |
| 3 | `build_diagnosis.py` | fifo_cfg1, des_cfg1, swerv_cfg1, aes_cfg2 | Zero false positives; LVS contraction fixed |
| 4 | `failure-patterns.md` | — (documentation) | 4 new sections added |
| 5 | Investigation only | ac97_ctrl_cfg2, i2c_verilog_cfg1, mem_ctrl_cfg1, simple_spi_top_cfg1 | Clock mismatches documented |
| 6 | Investigation only | usbf_top_cfg1, wb_dma_top_cfg1 | Reserved keyword renames documented |
| 7 | Update real reports | riscv32i_cfg1, swerv_cfg1, fifo_cfg1, des_cfg1, aes_cfg2 | Dashboard shows corrected data |

**Tasks 1-3** are code changes to the skill scripts (the core deliverable).
**Task 4** updates the reference documentation.
**Tasks 5-6** are investigation/documentation only (no backend re-runs).
**Task 7** is verification of Tasks 1-3.
