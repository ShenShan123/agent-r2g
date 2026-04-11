# r2g-rtl2gds Skill Improvements Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tiered post-backend timing gate to the r2g-rtl2gds skill that checks **both WNS and TNS**. Minor timing issues are auto-fixed by the agent. Moderate and severe issues stop the flow and present the user with numbered fix options. Also improve diagnostic coverage, config validation, and document hidden scripts.

**Architecture:** A new `check_timing.py` script reads `ppa.json` and classifies timing into tiers based on the **worse of** the WNS tier and the TNS tier. A design with small WNS but large TNS (many slightly-violating paths) is just as problematic as one with large WNS. The script writes a structured JSON result (`reports/timing_check.json`) containing the tier, concrete fix options with pre-calculated values, and whether the agent should auto-fix or ask the user. The agent reads this JSON: for `minor` tier it applies the suggested clock period fix and re-runs backend; for `moderate`/`severe`/`unconstrained` tiers it presents the numbered options to the user and waits for a decision.

**Tech Stack:** Python 3, Bash, ORFS JSON reports (`6_report.json`), existing `extract_ppa.py` output format

---

## Timing Tier Definitions

The final tier is the **worse of** the WNS-based tier and the TNS-based tier.

### WNS Tiers

| Tier | WNS Range | Meaning |
|------|-----------|---------|
| **clean** | WNS >= 0 | No setup violations on any path |
| **minor** | -2.0 <= WNS < 0 | Worst path is slightly violating |
| **moderate** | -5.0 <= WNS < -2.0 | Worst path has significant violation |
| **severe** | WNS < -5.0 | Worst path is far from closure |
| **unconstrained** | WNS > 1e+30 | SDC config error — no constraints applied |

### TNS Tiers

| Tier | TNS Range | Meaning |
|------|-----------|---------|
| **clean** | TNS >= 0 | No cumulative slack deficit |
| **minor** | -10.0 <= TNS < 0 | Few paths, small total deficit |
| **moderate** | -100.0 <= TNS < -10.0 | Many paths or moderate deficit |
| **severe** | TNS < -100.0 | Widespread timing failure |

### Combined Tier and Agent Behavior

| Combined Tier | Agent Behavior |
|---------------|----------------|
| **clean** | Proceed to signoff. No action. |
| **minor** | Auto-fix: increase clock period by `|WNS| + 1.0 ns`, re-run backend. Report what was done after the fact. |
| **moderate** | **Stop.** Present user with numbered options. Wait for decision. |
| **severe** | **Stop.** Present user with numbered options + strong warning. |
| **unconstrained** | **Stop.** SDC config error. Present fix options. |

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `skills/r2g-rtl2gds/scripts/check_timing.py` | **Create** | Tiered post-backend timing gate: classifies WNS+TNS, outputs `timing_check.json` with tier + options |
| `skills/r2g-rtl2gds/scripts/build_diagnosis.py` | **Modify** | Add `minor_setup_violation` and `severe_setup_violation` issue kinds checking both WNS and TNS |
| `skills/r2g-rtl2gds/scripts/validate_config.py` | **Modify** | Add ADDITIONAL_LEFS/LIBS file existence check; add DIE_AREA > CORE_AREA sanity check |
| `skills/r2g-rtl2gds/scripts/suggest_config.py` | **Modify** | Add LVS_TIMEOUT recommendation for >100K cells; add GDS_ALLOW_EMPTY for fakeram designs |
| `skills/r2g-rtl2gds/SKILL.md` | **Modify** | Add tiered timing gate (WNS+TNS) to workflow; document hidden scripts; add hard rules |
| `skills/r2g-rtl2gds/references/workflow.md` | **Modify** | Insert Phase 5b (timing gate) between backend and signoff; update Phase 7 |
| `skills/r2g-rtl2gds/references/failure-patterns.md` | **Modify** | Add "Setup Timing Violations" pattern with tiered WNS+TNS escalation |
| `CLAUDE.md` | **Modify** | Add timing gate to Flow Execution Order; add `check_timing.py` to Script Inventory |

---

## Task 1: Create `check_timing.py` — tiered WNS+TNS gate with structured output

**Files:**
- Create: `skills/r2g-rtl2gds/scripts/check_timing.py`

This is the core deliverable. It reads `reports/ppa.json` (produced by `extract_ppa.py`), independently classifies WNS and TNS into tiers, takes the **worse of the two** as the combined tier, computes concrete fix options with pre-calculated values, and writes a structured JSON result to `reports/timing_check.json`.

Exit codes:
- 0 = proceed (clean or minor — agent handles autonomously)
- 1 = user decision needed (moderate, severe, or unconstrained)
- 2 = usage error or missing data

- [ ] **Step 1: Create the script**

```python
#!/usr/bin/env python3
"""
Post-backend timing gate with tiered response based on WNS and TNS.

Reads reports/ppa.json and classifies timing into tiers based on the WORSE
of the WNS tier and the TNS tier:
  clean        (WNS >= 0,  TNS >= 0)         — proceed to signoff
  minor        (WNS >= -2, TNS >= -10)        — agent auto-fixes
  moderate     (WNS >= -5, TNS >= -100)       — stop, present options
  severe       (WNS < -5  OR TNS < -100)      — stop, strong warning
  unconstrained (WNS > 1e+30)                 — stop, SDC config error

The combined tier = max(wns_tier, tns_tier) where severity ordering is:
  clean < minor < moderate < severe < unconstrained

Writes structured result to reports/timing_check.json.

Exit codes:
  0 — proceed (clean or minor auto-fixable)
  1 — user decision needed (moderate / severe / unconstrained)
  2 — usage error or missing data
"""
import json
import math
import re
import sys
from pathlib import Path

# Severity ordering for tier comparison
TIER_ORDER = {'clean': 0, 'minor': 1, 'moderate': 2, 'severe': 3, 'unconstrained': 4}


def worse_tier(a: str, b: str) -> str:
    """Return the more severe of two tiers."""
    return a if TIER_ORDER.get(a, 0) >= TIER_ORDER.get(b, 0) else b


def classify_wns(wns: float, moderate_thr: float, severe_thr: float) -> str:
    """Classify WNS into a tier."""
    if wns > 1e+30:
        return 'unconstrained'
    if wns < severe_thr:
        return 'severe'
    if wns < moderate_thr:
        return 'moderate'
    if wns < 0:
        return 'minor'
    return 'clean'


def classify_tns(tns: float, moderate_thr: float, severe_thr: float) -> str:
    """Classify TNS into a tier."""
    if tns < severe_thr:
        return 'severe'
    if tns < moderate_thr:
        return 'moderate'
    if tns < 0:
        return 'minor'
    return 'clean'


def read_clock_period(project: Path) -> float | None:
    """Read clock period from constraint.sdc."""
    sdc_file = project / 'constraints' / 'constraint.sdc'
    if not sdc_file.exists():
        return None
    sdc_text = sdc_file.read_text(encoding='utf-8', errors='ignore')
    m = re.search(r'set\s+clk_period\s+([\d.]+)', sdc_text)
    return float(m.group(1)) if m else None


def read_core_utilization(project: Path) -> float | None:
    """Read CORE_UTILIZATION from config.mk."""
    config_file = project / 'constraints' / 'config.mk'
    if not config_file.exists():
        return None
    for line in config_file.read_text(encoding='utf-8', errors='ignore').splitlines():
        m = re.match(r'export\s+CORE_UTILIZATION\s*=\s*([\d.]+)', line)
        if m:
            return float(m.group(1))
    return None


def build_options_moderate(wns: float, tns: float, violation_count,
                           clock_period: float | None,
                           utilization: float | None,
                           wns_tier: str, tns_tier: str) -> list[dict]:
    """Build numbered fix options for moderate timing violations."""
    options = []
    # Determine the clock period increase based on WNS
    if clock_period and wns < 0:
        new_period = math.ceil((clock_period + abs(wns) * 1.5) * 2) / 2
        options.append({
            'number': len(options) + 1,
            'action': 'increase_clock_period',
            'description': f'Increase clock period from {clock_period} ns to {new_period} ns '
                           f'(+{new_period - clock_period:.1f} ns) and re-run backend',
            'new_value': new_period,
            'risk': 'low — conservative, reduces target frequency',
        })
    if utilization and utilization > 15:
        new_util = max(10, utilization - 10)
        options.append({
            'number': len(options) + 1,
            'action': 'reduce_utilization',
            'description': f'Reduce CORE_UTILIZATION from {utilization}% to {new_util}% '
                           f'and re-run backend',
            'new_value': new_util,
            'risk': 'low — gives placer more freedom, increases die area',
        })
    if clock_period and wns < 0 and utilization and utilization > 15:
        new_period = math.ceil((clock_period + abs(wns)) * 2) / 2
        new_util = max(10, utilization - 5)
        options.append({
            'number': len(options) + 1,
            'action': 'adjust_both',
            'description': f'Increase clock period to {new_period} ns AND '
                           f'reduce utilization to {new_util}%, re-run backend',
            'new_value': {'clock_period': new_period, 'utilization': new_util},
            'risk': 'low — balanced approach',
        })
    options.append({
        'number': len(options) + 1,
        'action': 'accept_and_proceed',
        'description': 'Accept timing violations and proceed to signoff anyway',
        'risk': 'high — chip will not meet target frequency',
    })
    options.append({
        'number': len(options) + 1,
        'action': 'stop_and_restructure',
        'description': 'Stop flow. Restructure RTL to shorten critical paths.',
        'risk': 'none — no further resources spent until design is fixed',
    })
    return options


def build_options_severe(wns: float, tns: float, violation_count,
                         clock_period: float | None,
                         utilization: float | None,
                         wns_tier: str, tns_tier: str) -> list[dict]:
    """Build numbered fix options for severe timing violations."""
    options = []
    if clock_period and wns < 0:
        new_period = math.ceil((clock_period + abs(wns) * 2.0) * 2) / 2
        options.append({
            'number': len(options) + 1,
            'action': 'increase_clock_period',
            'description': f'Significantly increase clock period from {clock_period} ns '
                           f'to {new_period} ns (+{new_period - clock_period:.1f} ns) '
                           f'and re-run backend',
            'new_value': new_period,
            'risk': 'medium — large frequency reduction, may not meet system requirements',
        })
    if utilization and utilization > 15:
        new_util = max(10, utilization - 15)
        options.append({
            'number': len(options) + 1,
            'action': 'reduce_utilization',
            'description': f'Reduce CORE_UTILIZATION from {utilization}% to {new_util}% '
                           f'and re-run backend',
            'new_value': new_util,
            'risk': 'low — gives placer much more freedom, significantly increases die area',
        })
    options.append({
        'number': len(options) + 1,
        'action': 'accept_and_proceed',
        'description': 'Accept timing violations and proceed to signoff anyway '
                       '(WARNING: chip will NOT work at target frequency)',
        'risk': 'very high — non-functional at target frequency',
    })
    options.append({
        'number': len(options) + 1,
        'action': 'stop_and_restructure',
        'description': 'Stop flow. Restructure RTL or change target frequency. (RECOMMENDED)',
        'risk': 'none — prevents wasting signoff time on a broken design',
    })
    return options


def build_options_unconstrained(clock_period: float | None) -> list[dict]:
    """Build fix options for unconstrained timing (SDC mismatch)."""
    return [
        {
            'number': 1,
            'action': 'fix_sdc_and_rerun',
            'description': 'Run validate_config.py to find the SDC/RTL clock port mismatch, '
                           'fix constraint.sdc, re-run synthesis and backend',
            'risk': 'none — this is always the right fix',
        },
        {
            'number': 2,
            'action': 'stop',
            'description': 'Stop flow entirely. The GDS is non-functional without timing constraints.',
            'risk': 'none',
        },
    ]


def format_timing_summary(wns, tns, violation_count, clock_period,
                          wns_tier, tns_tier) -> str:
    """Format a human-readable timing summary line."""
    parts = [f'WNS = {wns:.4f} ns [{wns_tier}]']
    if isinstance(tns, (int, float)):
        parts.append(f'TNS = {tns:.4f} ns [{tns_tier}]')
    else:
        parts.append(f'TNS = {tns}')
    if violation_count != 'N/A':
        parts.append(f'Violations = {violation_count}')
    if clock_period:
        pct = abs(wns) / clock_period * 100 if wns < 0 else 0
        parts.append(f'Clock = {clock_period} ns')
        if pct > 0:
            parts.append(f'WNS is {pct:.1f}% of period')
    return '  ' + ', '.join(parts)


def main():
    if len(sys.argv) < 2:
        print('usage: check_timing.py <project-dir> [--wns-threshold <ns>] [--tns-threshold <ns>]',
              file=sys.stderr)
        sys.exit(2)

    project = Path(sys.argv[1])
    ppa_file = project / 'reports' / 'ppa.json'
    out_file = project / 'reports' / 'timing_check.json'

    # WNS thresholds
    wns_moderate = -2.0
    wns_severe = -5.0
    # TNS thresholds
    tns_moderate = -10.0
    tns_severe = -100.0

    # Parse optional overrides
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == '--wns-threshold' and i + 1 < len(args):
            try:
                wns_moderate = float(args[i + 1])
                wns_severe = wns_moderate * 2.5
            except ValueError:
                print(f'ERROR: invalid --wns-threshold: {args[i + 1]}', file=sys.stderr)
                sys.exit(2)
            i += 2
        elif args[i] == '--tns-threshold' and i + 1 < len(args):
            try:
                tns_moderate = float(args[i + 1])
                tns_severe = tns_moderate * 10.0
            except ValueError:
                print(f'ERROR: invalid --tns-threshold: {args[i + 1]}', file=sys.stderr)
                sys.exit(2)
            i += 2
        else:
            i += 1

    if not ppa_file.exists():
        print(f'WARNING: {ppa_file} not found. Run extract_ppa.py first.', file=sys.stderr)
        sys.exit(2)

    try:
        ppa = json.loads(ppa_file.read_text(encoding='utf-8', errors='ignore'))
    except json.JSONDecodeError as e:
        print(f'ERROR: failed to parse {ppa_file}: {e}', file=sys.stderr)
        sys.exit(2)

    timing = ppa.get('summary', {}).get('timing', {})
    wns = timing.get('setup_wns')
    tns_raw = timing.get('setup_tns')
    violation_count = timing.get('setup_violation_count', 'N/A')
    hold_wns = timing.get('hold_wns')
    hold_tns = timing.get('hold_tns')

    # Handle missing WNS
    if wns is None or not isinstance(wns, (int, float)):
        result = {
            'tier': 'unknown', 'wns': None, 'tns': None,
            'wns_tier': 'unknown', 'tns_tier': 'unknown',
            'message': 'No setup_wns found in ppa.json. Timing data may be missing.',
            'options': [
                {'number': 1, 'action': 'proceed_anyway',
                 'description': 'Proceed to signoff (the GDS may be non-functional)',
                 'risk': 'unknown'},
                {'number': 2, 'action': 'stop',
                 'description': 'Stop and investigate why timing data is missing',
                 'risk': 'none'},
            ],
        }
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print('TIMING GATE: No timing data available.')
        print('DECISION NEEDED — choose an option:')
        for opt in result['options']:
            print(f"  [{opt['number']}] {opt['description']} (risk: {opt['risk']})")
        sys.exit(1)

    clock_period = read_clock_period(project)
    utilization = read_core_utilization(project)

    # Classify WNS and TNS independently
    wns_tier = classify_wns(wns, wns_moderate, wns_severe)
    tns = tns_raw if isinstance(tns_raw, (int, float)) else 0.0
    tns_tier = classify_tns(tns, tns_moderate, tns_severe) if isinstance(tns_raw, (int, float)) else 'clean'

    # Combined tier = worse of the two (except unconstrained is WNS-only)
    if wns_tier == 'unconstrained':
        combined_tier = 'unconstrained'
    else:
        combined_tier = worse_tier(wns_tier, tns_tier)

    tns_display = f'{tns:.4f}' if isinstance(tns_raw, (int, float)) else 'N/A'

    result = {
        'wns': wns,
        'tns': tns_raw,
        'wns_tier': wns_tier,
        'tns_tier': tns_tier,
        'tier': combined_tier,
        'violation_count': violation_count,
        'clock_period': clock_period,
        'utilization': utilization,
        'hold_wns': hold_wns,
        'hold_tns': hold_tns,
        'thresholds': {
            'wns_moderate': wns_moderate,
            'wns_severe': wns_severe,
            'tns_moderate': tns_moderate,
            'tns_severe': tns_severe,
        },
    }

    summary_line = format_timing_summary(
        wns, tns_display, violation_count, clock_period, wns_tier, tns_tier)

    # --- Tier: UNCONSTRAINED ---
    if combined_tier == 'unconstrained':
        result['auto_fixable'] = False
        result['options'] = build_options_unconstrained(clock_period)
        result['message'] = (
            f'Unconstrained timing detected (WNS = {wns}). '
            f'SDC clock port does not match RTL — the GDS is non-functional.'
        )
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(result, indent=2), encoding='utf-8')

        print(f'TIMING GATE FAILED [UNCONSTRAINED]')
        print(summary_line)
        print()
        print('Root cause: SDC clock port name does not match RTL port.')
        print('The entire backend ran without timing constraints.')
        print()
        print('DECISION NEEDED — choose an option:')
        for opt in result['options']:
            print(f"  [{opt['number']}] {opt['description']} (risk: {opt['risk']})")
        sys.exit(1)

    # --- Tier: SEVERE ---
    if combined_tier == 'severe':
        result['auto_fixable'] = False
        result['options'] = build_options_severe(
            wns, tns, violation_count, clock_period, utilization, wns_tier, tns_tier)
        escalation = []
        if wns_tier == 'severe':
            escalation.append(f'WNS={wns:.4f}ns is below {wns_severe}ns threshold')
        if tns_tier == 'severe':
            escalation.append(f'TNS={tns_display}ns is below {tns_severe}ns threshold')
        result['message'] = (
            f'Severe timing violations. {"; ".join(escalation)}. '
            f'The chip will NOT work at the target frequency.'
        )
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(result, indent=2), encoding='utf-8')

        print(f'TIMING GATE FAILED [SEVERE] (wns_tier={wns_tier}, tns_tier={tns_tier})')
        print(summary_line)
        if wns_tier != tns_tier:
            print(f'  Note: tier escalated by {"TNS" if tns_tier == "severe" else "WNS"}')
        print()
        print('DECISION NEEDED — choose an option:')
        for opt in result['options']:
            print(f"  [{opt['number']}] {opt['description']} (risk: {opt['risk']})")
        sys.exit(1)

    # --- Tier: MODERATE ---
    if combined_tier == 'moderate':
        result['auto_fixable'] = False
        result['options'] = build_options_moderate(
            wns, tns, violation_count, clock_period, utilization, wns_tier, tns_tier)
        escalation = []
        if wns_tier in ('moderate', 'severe'):
            escalation.append(f'WNS={wns:.4f}ns')
        if tns_tier in ('moderate', 'severe'):
            escalation.append(f'TNS={tns_display}ns')
        result['message'] = (
            f'Moderate timing violations ({", ".join(escalation)}). '
            f'Timing is not closed — user decision required.'
        )
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(result, indent=2), encoding='utf-8')

        print(f'TIMING GATE FAILED [MODERATE] (wns_tier={wns_tier}, tns_tier={tns_tier})')
        print(summary_line)
        if wns_tier != tns_tier:
            print(f'  Note: tier escalated by {"TNS" if TIER_ORDER[tns_tier] > TIER_ORDER[wns_tier] else "WNS"}')
        print()
        print('DECISION NEEDED — choose an option:')
        for opt in result['options']:
            print(f"  [{opt['number']}] {opt['description']} (risk: {opt['risk']})")
        sys.exit(1)

    # --- Tier: MINOR ---
    if combined_tier == 'minor':
        new_period = None
        if clock_period:
            new_period = math.ceil((clock_period + abs(wns) + 1.0) * 2) / 2
        result['auto_fixable'] = True
        result['suggested_clock_period'] = new_period
        result['message'] = (
            f'Minor timing violations (WNS={wns:.4f}ns [{wns_tier}], '
            f'TNS={tns_display}ns [{tns_tier}]). '
            f'Auto-fix: increase clock period '
            f'from {clock_period}ns to {new_period}ns and re-run backend.'
            if clock_period and new_period else
            f'Minor timing violations (WNS={wns:.4f}ns, TNS={tns_display}ns). '
            f'Auto-fix: increase clock period and re-run backend.'
        )
        result['options'] = []
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(result, indent=2), encoding='utf-8')

        print(f'TIMING GATE [MINOR] (wns_tier={wns_tier}, tns_tier={tns_tier}) — auto-fixable')
        print(summary_line)
        if new_period and clock_period:
            print(f'  Suggested fix: increase clock period '
                  f'{clock_period}ns -> {new_period}ns (+{new_period - clock_period:.1f}ns)')
        print(f'  Agent should apply fix and re-run backend.')
        sys.exit(0)

    # --- Tier: CLEAN ---
    result['auto_fixable'] = False
    result['options'] = []
    result['message'] = f'Timing clean. WNS={wns:.4f}ns, TNS={tns_display}ns.'
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(result, indent=2), encoding='utf-8')

    print(f'TIMING GATE PASSED [CLEAN]')
    print(summary_line)
    if hold_wns is not None and isinstance(hold_wns, (int, float)) and hold_wns < 0:
        hold_tns_val = f', hold_tns={hold_tns:.4f}ns' if isinstance(hold_tns, (int, float)) else ''
        print(f'  Note: Hold violations present (hold_wns={hold_wns:.4f}ns{hold_tns_val}) — '
              f'not blocking, but worth reviewing.')
    sys.exit(0)


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x skills/r2g-rtl2gds/scripts/check_timing.py
```

- [ ] **Step 3: Smoke test all tiers including TNS escalation**

```bash
mkdir -p /tmp/test_timing/reports /tmp/test_timing/constraints

cat > /tmp/test_timing/constraints/constraint.sdc << 'SDCEOF'
set clk_port_name clk
set clk_period 10.0
create_clock -name core_clock -period $clk_period [get_ports $clk_port_name]
SDCEOF
cat > /tmp/test_timing/constraints/config.mk << 'MKEOF'
export DESIGN_NAME = test
export PLATFORM = nangate45
export VERILOG_FILES = /tmp/test.v
export SDC_FILE = /tmp/test_timing/constraints/constraint.sdc
export CORE_UTILIZATION = 30
MKEOF

# Test CLEAN: WNS=0.5, TNS=0.0
echo '{"summary":{"timing":{"setup_wns":0.5,"setup_tns":0.0}}}' \
  > /tmp/test_timing/reports/ppa.json
python3 skills/r2g-rtl2gds/scripts/check_timing.py /tmp/test_timing; echo "EXIT: $?"
python3 -c "import json; d=json.load(open('/tmp/test_timing/reports/timing_check.json')); print(d['tier'], d['wns_tier'], d['tns_tier'])"
# Expected: EXIT:0, clean clean clean

# Test MINOR by WNS: WNS=-0.8, TNS=-2.5
echo '{"summary":{"timing":{"setup_wns":-0.8,"setup_tns":-2.5,"setup_violation_count":5}}}' \
  > /tmp/test_timing/reports/ppa.json
python3 skills/r2g-rtl2gds/scripts/check_timing.py /tmp/test_timing; echo "EXIT: $?"
python3 -c "import json; d=json.load(open('/tmp/test_timing/reports/timing_check.json')); print(d['tier'], d['wns_tier'], d['tns_tier'], d.get('suggested_clock_period'))"
# Expected: EXIT:0, minor minor minor, suggested_clock_period=12.0

# Test TNS ESCALATION: WNS=-0.5 (minor), TNS=-50.0 (moderate) → combined=moderate
echo '{"summary":{"timing":{"setup_wns":-0.5,"setup_tns":-50.0,"setup_violation_count":100}}}' \
  > /tmp/test_timing/reports/ppa.json
python3 skills/r2g-rtl2gds/scripts/check_timing.py /tmp/test_timing; echo "EXIT: $?"
python3 -c "import json; d=json.load(open('/tmp/test_timing/reports/timing_check.json')); print(d['tier'], d['wns_tier'], d['tns_tier'])"
# Expected: EXIT:1, moderate minor moderate (TNS escalated to moderate)

# Test TNS SEVERE ESCALATION: WNS=-1.0 (minor), TNS=-500.0 (severe) → combined=severe
echo '{"summary":{"timing":{"setup_wns":-1.0,"setup_tns":-500.0,"setup_violation_count":500}}}' \
  > /tmp/test_timing/reports/ppa.json
python3 skills/r2g-rtl2gds/scripts/check_timing.py /tmp/test_timing; echo "EXIT: $?"
python3 -c "import json; d=json.load(open('/tmp/test_timing/reports/timing_check.json')); print(d['tier'], d['wns_tier'], d['tns_tier'])"
# Expected: EXIT:1, severe minor severe (TNS escalated to severe)

# Test MODERATE by WNS: WNS=-3.5, TNS=-80.0
echo '{"summary":{"timing":{"setup_wns":-3.5,"setup_tns":-80.0,"setup_violation_count":25}}}' \
  > /tmp/test_timing/reports/ppa.json
python3 skills/r2g-rtl2gds/scripts/check_timing.py /tmp/test_timing; echo "EXIT: $?"
python3 -c "import json; d=json.load(open('/tmp/test_timing/reports/timing_check.json')); print(d['tier'], len(d['options']), 'options')"
# Expected: EXIT:1, moderate, 5 options

# Test SEVERE by WNS: WNS=-8.5, TNS=-2500.0
echo '{"summary":{"timing":{"setup_wns":-8.5,"setup_tns":-2500.0,"setup_violation_count":150}}}' \
  > /tmp/test_timing/reports/ppa.json
python3 skills/r2g-rtl2gds/scripts/check_timing.py /tmp/test_timing; echo "EXIT: $?"
python3 -c "import json; d=json.load(open('/tmp/test_timing/reports/timing_check.json')); print(d['tier'], d['wns_tier'], d['tns_tier'])"
# Expected: EXIT:1, severe severe severe

# Test UNCONSTRAINED: WNS=1e+39
echo '{"summary":{"timing":{"setup_wns":1e+39}}}' \
  > /tmp/test_timing/reports/ppa.json
python3 skills/r2g-rtl2gds/scripts/check_timing.py /tmp/test_timing; echo "EXIT: $?"
python3 -c "import json; print(json.load(open('/tmp/test_timing/reports/timing_check.json'))['tier'])"
# Expected: EXIT:1, unconstrained

rm -rf /tmp/test_timing
```

- [ ] **Step 4: Commit**

```bash
git add skills/r2g-rtl2gds/scripts/check_timing.py
git commit -m "feat(r2g): add check_timing.py — tiered WNS+TNS gate with auto-fix for minor issues"
```

---

## Task 2: Add tiered setup violation detection (WNS+TNS) to `build_diagnosis.py`

**Files:**
- Modify: `skills/r2g-rtl2gds/scripts/build_diagnosis.py:184-214` (timing checks section)

The existing `build_diagnosis.py` detects unconstrained timing (WNS > 1e+30) and hold violations, but checks neither WNS setup violations nor TNS. Refactor the three independent `ppa_file` reads into a single shared read, and add `minor_setup_violation` and `severe_setup_violation` issue kinds that consider both WNS and TNS.

- [ ] **Step 1: Replace lines 184-214 with consolidated WNS+TNS timing checks**

Replace the three separate `if ppa_file.exists()` blocks (lines 184-214) with:

```python
    # === Timing checks from ppa.json (WNS + TNS) ===
    ppa_file = project / 'reports' / 'ppa.json'
    ppa_data = None
    if ppa_file.exists():
        try:
            ppa_data = json.loads(ppa_file.read_text(encoding='utf-8', errors='ignore'))
        except (json.JSONDecodeError, TypeError):
            pass

    if ppa_data:
        timing = ppa_data.get('summary', {}).get('timing', {})
        wns = timing.get('setup_wns')
        tns = timing.get('setup_tns')
        count = timing.get('setup_violation_count', 'N/A')

        # 11. Unconstrained timing (WNS = 1e+39)
        if wns is not None and isinstance(wns, (int, float)) and wns > 1e+30:
            issues.append({
                'kind': 'unconstrained_timing',
                'summary': f'Timing is unconstrained (WNS={wns}). Clock constraints not applied.',
                'suggestion': 'SDC clock port name likely does not match RTL port. '
                              'Run validate_config.py to identify the mismatch.'
            })

        # 11b. Severe setup violations (WNS < -2.0 OR TNS < -100.0)
        elif ((wns is not None and isinstance(wns, (int, float)) and wns < -2.0) or
              (tns is not None and isinstance(tns, (int, float)) and tns < -100.0)):
            wns_s = f'{wns:.4f}' if isinstance(wns, (int, float)) else 'N/A'
            tns_s = f'{tns:.4f}' if isinstance(tns, (int, float)) else 'N/A'
            issues.append({
                'kind': 'severe_setup_violation',
                'summary': f'Severe setup timing violations: WNS={wns_s}ns, TNS={tns_s}ns, count={count}.',
                'suggestion': 'Timing is far from closure. Run check_timing.py for '
                              'numbered fix options. Do not proceed to signoff without user approval.'
            })

        # 11c. Minor setup violations (WNS < 0 OR TNS < 0, but not severe)
        elif ((wns is not None and isinstance(wns, (int, float)) and wns < 0) or
              (tns is not None and isinstance(tns, (int, float)) and tns < 0)):
            wns_s = f'{wns:.4f}' if isinstance(wns, (int, float)) else 'N/A'
            tns_s = f'{tns:.4f}' if isinstance(tns, (int, float)) else 'N/A'
            issues.append({
                'kind': 'minor_setup_violation',
                'summary': f'Minor setup timing violations: WNS={wns_s}ns, TNS={tns_s}ns, count={count}.',
                'suggestion': 'Auto-fixable: increase clock period and re-run backend. '
                              'Run check_timing.py for exact suggested values.'
            })

        # 12. Hold timing violations
        hold_tns = timing.get('hold_tns')
        if hold_tns is not None and isinstance(hold_tns, (int, float)) and hold_tns < -0.01:
            hold_count = timing.get('hold_violation_count', 'unknown')
            issues.append({
                'kind': 'hold_timing_violations',
                'summary': f'Hold timing violations: hold_tns={hold_tns:.4f}ns, count={hold_count}.',
                'suggestion': 'For large designs with macros, caused by CTS clock skew. '
                              'Try HOLD_SLACK_MARGIN=0.1 in config.mk.'
            })
```

- [ ] **Step 2: Test diagnosis with TNS escalation**

```bash
mkdir -p /tmp/test_diag/reports

# Test severe by WNS
echo '{"summary":{"timing":{"setup_wns":-5.0,"setup_tns":-120.0,"setup_violation_count":42}}}' \
  > /tmp/test_diag/reports/ppa.json
python3 skills/r2g-rtl2gds/scripts/build_diagnosis.py /tmp/test_diag /tmp/test_diag/reports/diagnosis.json
python3 -c "import json; d=json.load(open('/tmp/test_diag/reports/diagnosis.json')); print(d['kind'])"
# Expected: "severe_setup_violation"

# Test severe by TNS (WNS is minor but TNS is huge)
echo '{"summary":{"timing":{"setup_wns":-0.5,"setup_tns":-500.0,"setup_violation_count":1000}}}' \
  > /tmp/test_diag/reports/ppa.json
python3 skills/r2g-rtl2gds/scripts/build_diagnosis.py /tmp/test_diag /tmp/test_diag/reports/diagnosis.json
python3 -c "import json; d=json.load(open('/tmp/test_diag/reports/diagnosis.json')); print(d['kind'])"
# Expected: "severe_setup_violation" (TNS < -100 triggers severe)

# Test minor
echo '{"summary":{"timing":{"setup_wns":-0.5,"setup_tns":-1.2,"setup_violation_count":3}}}' \
  > /tmp/test_diag/reports/ppa.json
python3 skills/r2g-rtl2gds/scripts/build_diagnosis.py /tmp/test_diag /tmp/test_diag/reports/diagnosis.json
python3 -c "import json; d=json.load(open('/tmp/test_diag/reports/diagnosis.json')); print(d['kind'])"
# Expected: "minor_setup_violation"

# Test clean
echo '{"summary":{"timing":{"setup_wns":0.5,"setup_tns":0.0}}}' \
  > /tmp/test_diag/reports/ppa.json
python3 skills/r2g-rtl2gds/scripts/build_diagnosis.py /tmp/test_diag /tmp/test_diag/reports/diagnosis.json
python3 -c "import json; d=json.load(open('/tmp/test_diag/reports/diagnosis.json')); print(d['kind'])"
# Expected: "none"

rm -rf /tmp/test_diag
```

- [ ] **Step 3: Commit**

```bash
git add skills/r2g-rtl2gds/scripts/build_diagnosis.py
git commit -m "feat(r2g): add tiered WNS+TNS setup violation detection to build_diagnosis.py"
```

---

## Task 3: Update SKILL.md — tiered WNS+TNS timing gate and hidden scripts

**Files:**
- Modify: `skills/r2g-rtl2gds/SKILL.md:72` (after step 5, before step 6)
- Modify: `skills/r2g-rtl2gds/SKILL.md:155` (hard rules)
- Modify: `skills/r2g-rtl2gds/SKILL.md:259-273` (quick start steps 10-14)

- [ ] **Step 1: Insert new workflow section 5b after line 72**

After SKILL.md line 72 (`- Collect results from the ORFS results directory.`), insert:

```markdown
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
```

- [ ] **Step 2: Add hard rules after line 155**

After line 155 (`- Do not start signoff checks (DRC/LVS/RCX) if backend did not produce a GDS/ODB.`), insert:

```markdown
- Run `check_timing.py` after every backend run. It checks both WNS and TNS. For minor violations (WNS >= -2.0 AND TNS >= -10.0), auto-fix by increasing clock period and re-running. For moderate/severe/unconstrained, stop and present numbered fix options — do not proceed without the user's decision.
```

- [ ] **Step 3: Update Quick Start steps 10-14**

After SKILL.md line 259 (step 9: `run_orfs.sh`), replace steps 10-14 with:

```markdown
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
```

- [ ] **Step 4: Commit**

```bash
git add skills/r2g-rtl2gds/SKILL.md
git commit -m "docs(r2g): add tiered WNS+TNS timing gate to workflow, hard rules, and quick start"
```

---

## Task 4: Update `references/workflow.md` — Phase 5b and Phase 7

**Files:**
- Modify: `skills/r2g-rtl2gds/references/workflow.md:42-56` (between Phase 5 and Phase 6)
- Modify: `skills/r2g-rtl2gds/references/workflow.md:104-115` (Phase 7)

- [ ] **Step 1: Insert Phase 5b after line 42**

After workflow.md line 42 (`5. Collect results with scripts/collect_orfs_results.py.`), insert:

```markdown
## Phase 5b: Timing Gate (Tiered WNS + TNS)

After backend completes, extract PPA and run the timing gate:

1. Run `scripts/extract_ppa.py <project-dir> reports/ppa.json`.
2. Run `scripts/check_timing.py <project-dir>`.
3. The script classifies WNS and TNS independently and takes the **worse** as the combined tier.
4. Read `reports/timing_check.json` and act on the `tier` field:
   - **clean** (WNS >= 0, TNS >= 0): Proceed to Phase 6.
   - **minor** (WNS >= -2.0 AND TNS >= -10.0): Auto-fix. Update `clk_period` in constraint.sdc to `suggested_clock_period`, re-run Phase 4+5, then re-check.
   - **moderate** (WNS >= -5.0 AND TNS >= -100.0, not clean/minor): **Stop. Present numbered options to user.** Wait for decision.
   - **severe** (WNS < -5.0 OR TNS < -100.0): **Stop. Present options with strong warning.** Recommend "stop and restructure RTL".
   - **unconstrained** (WNS > 1e+30): **Stop. SDC config error.** Do NOT proceed.
5. Check `wns_tier` and `tns_tier` in the JSON to explain which metric triggered escalation.
```

- [ ] **Step 2: Update Phase 7 to note PPA already extracted**

Replace workflow.md Phase 7 block (lines 104-115) with:

```markdown
## Phase 7: Report Extraction

Extract all metrics into JSON for dashboard integration.
Note: `extract_ppa.py` already ran in Phase 5b. Re-run only if backend was re-run after Phase 5b.

```bash
# PPA was already extracted in Phase 5b; re-extract only if backend was re-run:
# scripts/extract_ppa.py <project-root> reports/ppa.json
scripts/extract_drc.py <project-root> reports/drc.json
scripts/extract_lvs.py <project-root> reports/lvs.json
scripts/extract_rcx.py <project-root> reports/rcx.json
scripts/extract_progress.py <project-root> reports/progress.json
scripts/build_diagnosis.py <project-root> reports/diagnosis.json
```
```

- [ ] **Step 3: Commit**

```bash
git add skills/r2g-rtl2gds/references/workflow.md
git commit -m "docs(r2g): add tiered WNS+TNS Phase 5b timing gate, update Phase 7"
```

---

## Task 5: Update `references/failure-patterns.md` — tiered WNS+TNS violation patterns

**Files:**
- Modify: `skills/r2g-rtl2gds/references/failure-patterns.md:431` (after "Unconstrained Timing" section)

- [ ] **Step 1: Add new tiered failure pattern after line 431**

After the "Unconstrained Timing (Silent Clock Mismatch)" section (line 431), insert:

```markdown
### Setup Timing Violations (Tiered WNS + TNS Response)

`check_timing.py` classifies timing into tiers based on the **worse of** the WNS tier and the TNS tier. A design with small WNS but large TNS (many slightly-violating paths) is treated as severely as one with large WNS.

**Thresholds (defaults, overridable via `--wns-threshold` and `--tns-threshold`):**

| Metric | Minor | Moderate | Severe |
|--------|-------|----------|--------|
| WNS | -2.0 to 0 ns | -5.0 to -2.0 ns | < -5.0 ns |
| TNS | -10.0 to 0 ns | -100.0 to -10.0 ns | < -100.0 ns |

#### Minor Setup Violations (combined tier = minor)

**Criteria:** WNS >= -2.0 AND TNS >= -10.0 (both metrics are minor or clean)

**Agent Action (automatic — no user interaction):**
1. Read `suggested_clock_period` from `reports/timing_check.json`
2. Update `clk_period` in `constraints/constraint.sdc` to the suggested value
3. Re-run synthesis and backend
4. Re-run `check_timing.py` to verify fix worked
5. Report the change to the user after the fact

#### Moderate Setup Violations (combined tier = moderate)

**Criteria:** WNS >= -5.0 AND TNS >= -100.0, but at least one metric is moderate

**Common scenario — TNS escalation:** WNS is only -0.5ns (minor) but TNS is -50ns (moderate) because 100 paths each violate by 0.5ns. The design looks "almost clean" by WNS alone but has widespread timing failure.

**Agent Action (stop and present options):**
1. Print the numbered `options` from `timing_check.json` to the user
2. Note which metric (`wns_tier` vs `tns_tier`) drove the escalation
3. Wait for the user to choose an option number
4. Apply the chosen fix and re-run backend, OR accept violations and proceed

**Typical options presented:**
1. Increase clock period to X ns (calculated)
2. Reduce CORE_UTILIZATION to Y%
3. Both: increase period + reduce utilization
4. Accept violations and proceed to signoff anyway (risk: high)
5. Stop flow and restructure RTL

#### Severe Setup Violations (combined tier = severe)

**Criteria:** WNS < -5.0 OR TNS < -100.0 (at least one metric is severe)

**Agent Action (stop and present options with strong warning):**
- Same options as moderate, but with stronger warnings and "stop and restructure RTL" recommended
- If WNS exceeds 50% of clock period, flag that architectural changes are needed

**Escalation Criteria:**
- **Moderate:** Agent may attempt one auto-tuning iteration (config change) if user picks option 1/2/3. If still moderate after retry, present options again.
- **Severe:** Always escalate to user immediately. Do not attempt auto-tuning.
- **Large TNS with small WNS:** Indicates widespread shallow violations. Increasing clock period is usually effective (all paths get more margin). This is noted in the options.
- **Large WNS with small TNS:** Indicates one deep critical path. Clock period increase alone may not help — RTL restructuring may be needed.
```

- [ ] **Step 2: Commit**

```bash
git add skills/r2g-rtl2gds/references/failure-patterns.md
git commit -m "docs(r2g): add tiered WNS+TNS timing violation patterns with escalation criteria"
```

---

## Task 6: Update CLAUDE.md — flow order, script inventory, pitfalls

**Files:**
- Modify: `CLAUDE.md` (project root)

- [ ] **Step 1: Insert timing gate in Flow Execution Order**

In the "Flow Execution Order (Strict)" section, after step 7 (Backend), insert:

```markdown
8. **Timing Gate** — `scripts/check_timing.py <project-dir>` (checks both WNS and TNS; minor: auto-fix; moderate/severe: **stop and present options to user**)
```

Renumber: DRC -> 9, LVS -> 10, RCX -> 11, Reports -> 12.

- [ ] **Step 2: Add `check_timing.py` to Script Inventory table**

In the "Analysis & Extraction Scripts (Python)" table, add:

```markdown
| `check_timing.py` | Tiered post-backend WNS+TNS gate (auto-fix minor, present options for moderate/severe) | `<project-dir> [--wns-threshold <ns>] [--tns-threshold <ns>]` | `timing_check.json` (tier, wns_tier, tns_tier, options, suggested_clock_period) |
```

- [ ] **Step 3: Add to Common Pitfalls**

Add to the Common Pitfalls list:

```markdown
- **Severe WNS/TNS not caught**: Always run `check_timing.py` after `extract_ppa.py` and before signoff. It checks both WNS and TNS — a design with small WNS but large TNS (many violating paths) is equally problematic. Minor violations (WNS >= -2.0 AND TNS >= -10.0) are auto-fixed. Moderate/severe stop the flow with numbered options for the user.
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add tiered WNS+TNS check_timing.py to flow order, inventory, and pitfalls"
```

---

## Task 7: Extend `validate_config.py` — macro file checks and area sanity

**Files:**
- Modify: `skills/r2g-rtl2gds/scripts/validate_config.py`

### 7a: Add ADDITIONAL_LEFS/LIBS file existence check

- [ ] **Step 1: Read current validate_config.py**

Read the full file to identify where to add the new checks (after existing VERILOG_FILES validation).

- [ ] **Step 2: Add file existence checks for ADDITIONAL_LEFS and ADDITIONAL_LIBS**

After the existing VERILOG_FILES path validation logic, add:

```python
    # Check ADDITIONAL_LEFS files exist
    add_lefs = config.get('ADDITIONAL_LEFS', '')
    if add_lefs:
        for lef_token in add_lefs.split():
            if '$(' in lef_token:
                continue
            lef_path = Path(lef_token)
            if not lef_path.exists():
                warnings.append(
                    f"ADDITIONAL_LEFS file not found: {lef_token}. "
                    f"Macro LEF must exist for floorplanning."
                )

    # Check ADDITIONAL_LIBS files exist
    add_libs = config.get('ADDITIONAL_LIBS', '')
    if add_libs:
        for lib_token in add_libs.split():
            if '$(' in lib_token:
                continue
            lib_path = Path(lib_token)
            if not lib_path.exists():
                warnings.append(
                    f"ADDITIONAL_LIBS file not found: {lib_token}. "
                    f"Macro LIB must exist for synthesis and timing."
                )
```

### 7b: Add DIE_AREA / CORE_AREA sanity check

- [ ] **Step 3: Add area sanity check**

After the parameter range validation section, add:

```python
    # Check DIE_AREA > CORE_AREA
    die_area = config.get('DIE_AREA', '')
    core_area = config.get('CORE_AREA', '')
    if die_area and core_area:
        try:
            die_coords = [float(x) for x in die_area.split()]
            core_coords = [float(x) for x in core_area.split()]
            if len(die_coords) == 4 and len(core_coords) == 4:
                die_w = die_coords[2] - die_coords[0]
                die_h = die_coords[3] - die_coords[1]
                core_w = core_coords[2] - core_coords[0]
                core_h = core_coords[3] - core_coords[1]
                if core_w >= die_w or core_h >= die_h:
                    warnings.append(
                        f"CORE_AREA ({core_area}) is not smaller than DIE_AREA ({die_area}). "
                        f"Core must fit inside die with margin for IO pads and power rings."
                    )
                if core_coords[0] < die_coords[0] or core_coords[1] < die_coords[1]:
                    warnings.append(
                        f"CORE_AREA origin ({core_coords[0]}, {core_coords[1]}) is outside "
                        f"DIE_AREA origin ({die_coords[0]}, {die_coords[1]})."
                    )
        except (ValueError, IndexError):
            pass
```

- [ ] **Step 4: Test with a macro config**

```bash
mkdir -p /tmp/test_validate/constraints /tmp/test_validate/rtl
cat > /tmp/test_validate/constraints/config.mk << 'EOF'
export DESIGN_NAME = test_design
export PLATFORM = nangate45
export VERILOG_FILES = /tmp/test_validate/rtl/design.v
export SDC_FILE = /tmp/test_validate/constraints/constraint.sdc
export CORE_UTILIZATION = 30
export ADDITIONAL_LEFS = /nonexistent/path/fakeram.lef
export DIE_AREA = 0 0 100 100
export CORE_AREA = 0 0 200 200
EOF
echo 'module test_design(input clk); endmodule' > /tmp/test_validate/rtl/design.v
cat > /tmp/test_validate/constraints/constraint.sdc << 'EOF'
set clk_port_name clk
create_clock -name core_clock -period 10.0 [get_ports $clk_port_name]
EOF
python3 skills/r2g-rtl2gds/scripts/validate_config.py /tmp/test_validate
# Expected: warnings about ADDITIONAL_LEFS not found and CORE_AREA >= DIE_AREA
rm -rf /tmp/test_validate
```

- [ ] **Step 5: Commit**

```bash
git add skills/r2g-rtl2gds/scripts/validate_config.py
git commit -m "feat(r2g): add macro file and area sanity checks to validate_config.py"
```

---

## Task 8: Extend `suggest_config.py` — LVS timeout and GDS_ALLOW_EMPTY

**Files:**
- Modify: `skills/r2g-rtl2gds/scripts/suggest_config.py`

- [ ] **Step 1: Read current suggest_config.py**

Read the full file to identify the recommendation output section.

- [ ] **Step 2: Add LVS_TIMEOUT recommendation for large designs**

In the recommendation generation section (after safety flags), add:

```python
    # LVS timeout recommendation based on estimated cell count
    if size_class == 'large' or (design_type == 'macro_heavy' and size_class == 'medium'):
        recommendations['LVS_TIMEOUT'] = '7200  # Large/macro design — KLayout LVS needs extended timeout'

    # GDS_ALLOW_EMPTY for fakeram designs
    if design_type == 'macro_heavy':
        recommendations['GDS_ALLOW_EMPTY'] = 'fakeram.*  # Allow empty GDS cells for macro stubs'
```

- [ ] **Step 3: Commit**

```bash
git add skills/r2g-rtl2gds/scripts/suggest_config.py
git commit -m "feat(r2g): add LVS_TIMEOUT and GDS_ALLOW_EMPTY recommendations to suggest_config.py"
```

---

## Task 9: Final integration test

- [ ] **Step 1: Verify all scripts compile**

```bash
python3 -m py_compile skills/r2g-rtl2gds/scripts/check_timing.py
python3 -m py_compile skills/r2g-rtl2gds/scripts/build_diagnosis.py
python3 -m py_compile skills/r2g-rtl2gds/scripts/validate_config.py
python3 -m py_compile skills/r2g-rtl2gds/scripts/suggest_config.py
echo "All scripts compile OK"
```

- [ ] **Step 2: Test the full diagnosis + timing gate chain with TNS escalation**

```bash
mkdir -p /tmp/test_full/reports /tmp/test_full/constraints

cat > /tmp/test_full/constraints/constraint.sdc << 'EOF'
set clk_port_name clk
set clk_period 10.0
create_clock -name core_clock -period $clk_period [get_ports $clk_port_name]
EOF
cat > /tmp/test_full/constraints/config.mk << 'EOF'
export DESIGN_NAME = test
export PLATFORM = nangate45
export VERILOG_FILES = /tmp/test.v
export SDC_FILE = /tmp/test_full/constraints/constraint.sdc
export CORE_UTILIZATION = 30
EOF

# Scenario 1: Clean — both WNS and TNS clean
echo '{"summary":{"timing":{"setup_wns":0.5,"setup_tns":0.0}}}' \
  > /tmp/test_full/reports/ppa.json
python3 skills/r2g-rtl2gds/scripts/build_diagnosis.py /tmp/test_full /tmp/test_full/reports/diagnosis.json
python3 skills/r2g-rtl2gds/scripts/check_timing.py /tmp/test_full; echo "EXIT: $?"
# Expected: diagnosis=none, tier=clean, EXIT=0

# Scenario 2: Minor WNS, minor TNS — auto-fix
echo '{"summary":{"timing":{"setup_wns":-1.2,"setup_tns":-5.0,"setup_violation_count":8}}}' \
  > /tmp/test_full/reports/ppa.json
python3 skills/r2g-rtl2gds/scripts/check_timing.py /tmp/test_full; echo "EXIT: $?"
python3 -c "import json; d=json.load(open('/tmp/test_full/reports/timing_check.json')); print('tier:', d['tier'], 'wns:', d['wns_tier'], 'tns:', d['tns_tier'])"
# Expected: tier=minor, wns=minor, tns=minor, EXIT=0

# Scenario 3: KEY TEST — Minor WNS but moderate TNS → combined=moderate
echo '{"summary":{"timing":{"setup_wns":-0.5,"setup_tns":-50.0,"setup_violation_count":100}}}' \
  > /tmp/test_full/reports/ppa.json
python3 skills/r2g-rtl2gds/scripts/check_timing.py /tmp/test_full; echo "EXIT: $?"
python3 -c "import json; d=json.load(open('/tmp/test_full/reports/timing_check.json')); print('tier:', d['tier'], 'wns:', d['wns_tier'], 'tns:', d['tns_tier'])"
# Expected: tier=moderate, wns=minor, tns=moderate, EXIT=1 (TNS escalated!)

# Scenario 4: KEY TEST — Minor WNS but severe TNS → combined=severe
echo '{"summary":{"timing":{"setup_wns":-1.0,"setup_tns":-500.0,"setup_violation_count":500}}}' \
  > /tmp/test_full/reports/ppa.json
python3 skills/r2g-rtl2gds/scripts/check_timing.py /tmp/test_full; echo "EXIT: $?"
python3 -c "import json; d=json.load(open('/tmp/test_full/reports/timing_check.json')); print('tier:', d['tier'], 'wns:', d['wns_tier'], 'tns:', d['tns_tier'])"
# Expected: tier=severe, wns=minor, tns=severe, EXIT=1 (TNS escalated!)

# Scenario 5: Severe by WNS
echo '{"summary":{"timing":{"setup_wns":-8.5,"setup_tns":-2500.0,"setup_violation_count":150}}}' \
  > /tmp/test_full/reports/ppa.json
python3 skills/r2g-rtl2gds/scripts/check_timing.py /tmp/test_full; echo "EXIT: $?"
python3 -c "import json; d=json.load(open('/tmp/test_full/reports/timing_check.json')); print('tier:', d['tier'])"
# Expected: tier=severe, EXIT=1

# Scenario 6: Diagnosis also catches TNS-driven severe
echo '{"summary":{"timing":{"setup_wns":-0.3,"setup_tns":-200.0,"setup_violation_count":400}}}' \
  > /tmp/test_full/reports/ppa.json
python3 skills/r2g-rtl2gds/scripts/build_diagnosis.py /tmp/test_full /tmp/test_full/reports/diagnosis.json
python3 -c "import json; d=json.load(open('/tmp/test_full/reports/diagnosis.json')); print(d['kind'])"
# Expected: "severe_setup_violation" (TNS < -100 triggers severe in diagnosis too)

rm -rf /tmp/test_full
```

- [ ] **Step 3: Run check_env.sh**

```bash
source /opt/openroad_tools_env.sh
bash skills/r2g-rtl2gds/scripts/check_env.sh
```

- [ ] **Step 4: Commit (if any fixes needed)**

```bash
# Only if fixes were needed
git add -A
git commit -m "fix(r2g): integration test fixes for tiered WNS+TNS timing gate"
```
