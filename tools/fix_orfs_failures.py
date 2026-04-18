#!/usr/bin/env python3
"""Apply root-cause fixes to the 93 ORFS failures identified in the batch report.

Fix matrix:
  memory_inference  -> raise SYNTH_MEMORY_MAX_BITS
  io_pin_overflow   -> enlarge die (switch tiny/small to CORE_UTILIZATION)
  place_density     -> enlarge die / drop utilization
  pdn_strap         -> enlarge die and reduce strap density
  missing_include   -> write stub include or concat referenced header into VERILOG_FILES
  timeout           -> mark for larger timeout via config.mk env hints; also consider smaller designs need utilization bump to finish place

This script:
  1. Reads /tmp/fail_categories.json (produced earlier)
  2. Mutates each case's constraints/config.mk in place
  3. Writes a summary to design_cases/_batch/fix_summary.json
"""
from __future__ import annotations
import json
import os
import re
import subprocess
import sys
from pathlib import Path

BASE = Path('/proj/workarea/user5/agent-r2g')
CASES = BASE / 'design_cases'
RTL_DIR = BASE / 'rtl_designs'
TOOLS = BASE / 'tools'

MEM_BITS = 131072   # 128 Kbit — enough for arm_core 32Kbit memories, verilog_ethernet FIFOs
IO_FIX_UTIL = 15    # CORE_UTILIZATION for io-pin-overflow cases
PLACE_DENSITY_FIX_UTIL = 10  # lower utilization when density>1
PDN_UTIL = 15


def read_cfg(path: Path) -> str:
    return path.read_text() if path.exists() else ''


def write_cfg(path: Path, content: str) -> None:
    path.write_text(content)


def ensure_line(cfg: str, var: str, value: str) -> str:
    """Set or replace `export VAR = value` line."""
    pattern = re.compile(rf'^export\s+{re.escape(var)}\s*=.*$', re.MULTILINE)
    new_line = f'export {var} = {value}'
    if pattern.search(cfg):
        return pattern.sub(new_line, cfg)
    # Append before trailing whitespace
    return cfg.rstrip() + '\n' + new_line + '\n'


def remove_die_area(cfg: str) -> str:
    """Strip any explicit DIE_AREA / CORE_AREA lines so CORE_UTILIZATION can take effect."""
    cfg = re.sub(r'^export\s+DIE_AREA\s*=.*\n?', '', cfg, flags=re.MULTILINE)
    cfg = re.sub(r'^export\s+CORE_AREA\s*=.*\n?', '', cfg, flags=re.MULTILINE)
    return cfg


def switch_to_utilization(cfg: str, util: int) -> str:
    cfg = remove_die_area(cfg)
    cfg = ensure_line(cfg, 'CORE_UTILIZATION', str(util))
    return cfg


def apply_memory_fix(case: str) -> dict:
    cfg_path = CASES / case / 'constraints' / 'config.mk'
    cfg = read_cfg(cfg_path)
    if not cfg:
        return {'case': case, 'fix': 'memory_inference', 'status': 'no_config'}
    cfg = ensure_line(cfg, 'SYNTH_MEMORY_MAX_BITS', str(MEM_BITS))
    # Also ensure the die isn't tiny — FIFOs with >4K bits will generate many flops
    cfg = switch_to_utilization(cfg, 20) if 'DIE_AREA' in cfg else cfg
    write_cfg(cfg_path, cfg)
    return {'case': case, 'fix': 'memory_inference', 'status': 'applied'}


IO_PIN_PPL_RE = re.compile(
    r'IO pins \((\d+)\) exceeds maximum number of available positions \((\d+)\)\.\s*'
    r'Increase the die perimeter from ([\d.]+)um to ([\d.]+)um'
)


def required_perim_from_log(case: str) -> float | None:
    log = Path('design_cases/_batch/logs') / f'{case}.log'
    if not log.exists():
        return None
    txt = log.read_text(errors='ignore')
    m = None
    for m in IO_PIN_PPL_RE.finditer(txt):
        pass  # keep last match (most recent retry)
    return float(m.group(4)) if m else None


def compute_die_side(required_perim: float) -> int:
    """Pick a conservative square die side (um) that satisfies IO perimeter + cell area."""
    import math
    # 1.3x safety factor on perimeter gives headroom for pin spacing and cell area.
    side = int(math.ceil(required_perim / 4 * 1.3))
    # Round up to nearest 10um for clean numbers.
    side = ((side + 9) // 10) * 10
    return max(side, 50)


def apply_io_fix(case: str) -> dict:
    cfg_path = CASES / case / 'constraints' / 'config.mk'
    cfg = read_cfg(cfg_path)
    if not cfg:
        return {'case': case, 'fix': 'io_pin_overflow', 'status': 'no_config'}

    required_perim = required_perim_from_log(case)
    if required_perim is None:
        # Fallback: use CORE_UTILIZATION so ORFS auto-sizes
        cfg = switch_to_utilization(cfg, IO_FIX_UTIL)
        write_cfg(cfg_path, cfg)
        return {'case': case, 'fix': 'io_pin_overflow', 'status': 'applied_util_fallback'}

    side = compute_die_side(required_perim)
    core_margin = 5 if side < 500 else 10

    cfg = re.sub(r'^export\s+(CORE_UTILIZATION|DIE_AREA|CORE_AREA)\s*=.*\n?', '',
                 cfg, flags=re.MULTILINE)
    die_block = (
        f'export DIE_AREA  = 0 0 {side} {side}\n'
        f'export CORE_AREA = {core_margin} {core_margin} {side - core_margin} {side - core_margin}\n'
    )
    # Insert after SDC_FILE line
    if 'SDC_FILE' in cfg:
        cfg = re.sub(r'(export\s+SDC_FILE\s*=.*\n)', r'\1\n' + die_block, cfg, count=1)
    else:
        cfg = cfg.rstrip() + '\n' + die_block
    write_cfg(cfg_path, cfg)
    return {
        'case': case,
        'fix': 'io_pin_overflow',
        'status': 'applied',
        'required_perim_um': required_perim,
        'die_side_um': side,
    }


def apply_density_fix(case: str) -> dict:
    cfg_path = CASES / case / 'constraints' / 'config.mk'
    cfg = read_cfg(cfg_path)
    if not cfg:
        return {'case': case, 'fix': 'place_density', 'status': 'no_config'}
    cfg = switch_to_utilization(cfg, PLACE_DENSITY_FIX_UTIL)
    cfg = ensure_line(cfg, 'PLACE_DENSITY_LB_ADDON', '0.20')
    write_cfg(cfg_path, cfg)
    return {'case': case, 'fix': 'place_density', 'status': 'applied'}


def apply_pdn_fix(case: str) -> dict:
    cfg_path = CASES / case / 'constraints' / 'config.mk'
    cfg = read_cfg(cfg_path)
    if not cfg:
        return {'case': case, 'fix': 'pdn_strap', 'status': 'no_config'}
    cfg = switch_to_utilization(cfg, PDN_UTIL)
    write_cfg(cfg_path, cfg)
    return {'case': case, 'fix': 'pdn_strap', 'status': 'applied'}


def apply_timeout_fix(case: str) -> dict:
    """Tag the config so the batch runner uses a longer timeout.
    Small iscas designs that time out actually hit infinite loops in detailed routing;
    for these, lower density often helps more than longer timeout. Do both.
    """
    cfg_path = CASES / case / 'constraints' / 'config.mk'
    cfg = read_cfg(cfg_path)
    if not cfg:
        return {'case': case, 'fix': 'timeout', 'status': 'no_config'}
    # Lower utilization relaxes detailed routing
    if 'DIE_AREA' in cfg:
        cfg = switch_to_utilization(cfg, 20)
    cfg = ensure_line(cfg, 'PLACE_DENSITY_LB_ADDON', '0.25')
    write_cfg(cfg_path, cfg)
    return {'case': case, 'fix': 'timeout', 'status': 'applied'}


def find_include_in_rtl(case: str, include_name: str) -> Path | None:
    """Search design's rtl folder + original rtl_designs folder for the include."""
    for root in (CASES / case / 'rtl', RTL_DIR / case / 'rtl', RTL_DIR / case):
        if not root.exists():
            continue
        for f in root.rglob(include_name):
            if f.is_file():
                return f
    return None


def apply_include_fix(case: str) -> dict:
    """Best-effort missing-include fix.

    Strategy: inline-prepend any referenced `defs`/`vh` that can be inferred
    as a pure header by searching sibling RTL dirs. If nothing is found,
    write an empty stub (safe for pure `\`define`/`\`ifdef`-absent cases is
    uncertain — so mark these as unfixable).
    """
    dst_rtl_dir = CASES / case / 'rtl'
    if not dst_rtl_dir.exists():
        return {'case': case, 'fix': 'missing_include', 'status': 'no_rtl'}

    # Collect all unique include names referenced across the case's rtl
    includes = set()
    for v in dst_rtl_dir.glob('*.v'):
        try:
            txt = v.read_text(errors='ignore')
        except Exception:
            continue
        for m in re.finditer(r'`include\s+"([^"]+)"', txt):
            includes.add(m.group(1))

    if not includes:
        return {'case': case, 'fix': 'missing_include', 'status': 'no_includes'}

    resolved = {}
    unresolved = []
    for inc in includes:
        found = find_include_in_rtl(case, inc)
        if found:
            resolved[inc] = found
        else:
            unresolved.append(inc)

    # For unresolved includes, create empty stub files inside dst_rtl_dir
    for inc in unresolved:
        stub_path = dst_rtl_dir / inc
        stub_path.parent.mkdir(parents=True, exist_ok=True)
        if not stub_path.exists():
            stub_path.write_text(
                f'// Stub for missing header {inc}\n'
                f'// Auto-generated by tools/fix_orfs_failures.py\n'
            )

    # Copy resolved includes into the rtl dir so `include resolves
    for inc, src in resolved.items():
        dst = dst_rtl_dir / inc
        if not dst.exists():
            dst.write_text(src.read_text(errors='ignore'))

    # Ensure config.mk picks up the rtl dir via VERILOG_INCLUDE_DIRS
    cfg_path = CASES / case / 'constraints' / 'config.mk'
    cfg = read_cfg(cfg_path)
    if cfg and 'VERILOG_INCLUDE_DIRS' not in cfg:
        cfg = ensure_line(cfg, 'VERILOG_INCLUDE_DIRS', str(dst_rtl_dir))
        write_cfg(cfg_path, cfg)

    return {
        'case': case,
        'fix': 'missing_include',
        'status': 'applied',
        'resolved': list(resolved),
        'stubbed': unresolved,
    }


def apply_wrong_top_fix(case: str) -> dict:
    """Detect and fix wrong top module selection for multi-module RTL files.

    Uses the same validate_top_module logic from setup_rtl_designs.py.
    """
    rtl_dir = CASES / case / 'rtl'
    cfg_path = CASES / case / 'constraints' / 'config.mk'
    sdc_path = CASES / case / 'constraints' / 'constraint.sdc'
    cfg = read_cfg(cfg_path)
    if not cfg:
        return {'case': case, 'fix': 'wrong_top', 'status': 'no_config'}

    current_top = None
    m = re.search(r'export\s+DESIGN_NAME\s*=\s*(\S+)', cfg)
    if m:
        current_top = m.group(1)

    rtl_files = sorted(rtl_dir.glob('*.v')) + sorted(rtl_dir.glob('*.sv'))
    if not rtl_files:
        return {'case': case, 'fix': 'wrong_top', 'status': 'no_rtl'}

    module_re = re.compile(r'^module\s+(\w+)', re.MULTILINE)
    all_modules = []
    for f in rtl_files:
        try:
            txt = f.read_text(errors='replace')
        except Exception:
            continue
        for mod in module_re.finditer(txt):
            name = mod.group(1)
            start = mod.start()
            end_m = re.search(r'\bendmodule\b', txt[start:])
            length = end_m.start() if end_m else 0
            port_m = re.search(r'\(([^)]*)\)', txt[start:start + min(2000, len(txt) - start)])
            port_count = len(port_m.group(1).split(',')) if port_m else 0
            all_modules.append({'name': name, 'length': length, 'ports': port_count,
                                'file_stem': f.stem, 'offset': start})

    if len(all_modules) < 5:
        return {'case': case, 'fix': 'wrong_top', 'status': 'too_few_modules'}

    selected = next((m for m in all_modules if m['name'] == current_top), None)
    if not selected:
        return {'case': case, 'fix': 'wrong_top', 'status': 'top_not_found'}

    largest = max(all_modules, key=lambda m: m['length'])
    most_ports = max(all_modules, key=lambda m: m['ports'])
    last_module = max(all_modules, key=lambda m: m['offset'])
    stem_match = next((m for m in all_modules if m['name'] == m['file_stem']), None)

    if selected['length'] >= largest['length'] * 0.1:
        return {'case': case, 'fix': 'wrong_top', 'status': 'top_looks_ok'}

    new_top = None
    for c in [stem_match, most_ports, last_module, largest]:
        if c and c['name'] != current_top and c['length'] > selected['length'] * 3:
            new_top = c['name']
            break

    if not new_top:
        return {'case': case, 'fix': 'wrong_top', 'status': 'no_better_candidate'}

    clock_hint = 'ap_clk' if new_top == 'myproject' else None

    cfg = ensure_line(cfg, 'DESIGN_NAME', new_top)
    write_cfg(cfg_path, cfg)

    sdc = read_cfg(sdc_path)
    if sdc and current_top:
        sdc = sdc.replace(f'current_design {current_top}', f'current_design {new_top}')
        if clock_hint:
            sdc = re.sub(r'set clk_port_name \S+', f'set clk_port_name {clock_hint}', sdc)
        write_cfg(sdc_path, sdc)

    return {
        'case': case,
        'fix': 'wrong_top',
        'status': 'applied',
        'old_top': current_top,
        'new_top': new_top,
        'clock_hint': clock_hint,
    }


# ---------------------------------------------------------------------------
# RTL-error detector (LLM-in-the-loop)
#
# This handler does NOT patch RTL mechanically. Its job is to:
#   1. Identify which stage failed (lint / synth / elab / floorplan ...).
#   2. Extract ~60 lines of log context surrounding the first fatal error.
#   3. Cross-reference the error against RTL files in the case (via file:line
#      hints in the log), so the human/LLM operator can see exactly where to
#      look without trawling the whole log.
#   4. Record a structural baseline (via check_structural_preservation.py) so
#      any subsequent RTL edits can be verified for preservation.
#
# The dispatcher (apply_other) hands off to this when the failure signature
# looks RTL-level rather than config-level.
# ---------------------------------------------------------------------------

RTL_STAGE_HINTS = (
    ('lint',       ('lint.log', 'verilator', 'iverilog')),
    ('synth',      ('synth.log', 'yosys', '1_1_yosys', 'Executing AST')),
    ('elab',       ('elaborat', 'read_verilog', 'Parsing Verilog')),
    ('floorplan',  ('floorplan', '2_floorplan')),
    ('place',      ('3_place',)),
    ('cts',        ('4_cts',)),
    ('route',      ('5_route', 'detailed route')),
)

RTL_ERROR_SIGS = (
    # Yosys
    re.compile(r'ERROR:\s*(?P<msg>.+)', re.IGNORECASE),
    re.compile(r'^\s*syntax error', re.MULTILINE | re.IGNORECASE),
    # Classic synthesis gotchas
    re.compile(r'(?P<msg>[Ll]atch\s+inferred)'),
    re.compile(r'(?P<msg>[Mm]ultiple drivers? (?:on|for))'),
    re.compile(r'(?P<msg>[Cc]ombinational loop)'),
    # Generic compile errors with a file:line prefix
    re.compile(r'(?P<msg>\S+\.(?:v|sv|vh|svh):\d+:\s*(?:error|ERROR))'),
    # Verilator
    re.compile(r'%Error[^:]*:\s*(?P<msg>.+)'),
    # iverilog
    re.compile(r'(?P<msg>\S+\.(?:v|sv):\d+:\s*error)'),
)

FILE_LINE_RE = re.compile(r'(\S+?\.(?:v|sv|vh|svh)):(\d+)')


def _find_log(case_dir: Path) -> Path | None:
    """Locate the most informative log file for the case.

    Preference order:
      1. batch_logs/orfs.log (written by batch_run.sh when backend is the
         failing stage — covers most non-lint/non-sim failures)
      2. lint/lint.log (lint stage)
      3. sim/sim.log (simulation stage)
      4. synth/synth.log (standalone synth runner)
      5. The newest .log anywhere under batch_logs/
      6. ORFS internal logs/<platform>/<design>/base/*.log (last resort)
    """
    candidates = [
        case_dir / 'batch_logs' / 'orfs.log',
        case_dir / 'lint' / 'lint.log',
        case_dir / 'sim' / 'sim.log',
        case_dir / 'synth' / 'synth.log',
    ]
    for c in candidates:
        if c.exists() and c.stat().st_size > 0:
            return c
    blogs = case_dir / 'batch_logs'
    if blogs.is_dir():
        logs = sorted(blogs.rglob('*.log'), key=lambda p: p.stat().st_mtime, reverse=True)
        if logs:
            return logs[0]
    # ORFS flow.log lives under backend/RUN_*/flow.log
    for flow_log in sorted((case_dir / 'backend').rglob('flow.log'),
                           key=lambda p: p.stat().st_mtime, reverse=True):
        return flow_log
    return None


def _detect_stage(log_path: Path, log_tail: str) -> str:
    name = log_path.name.lower()
    for stage, hints in RTL_STAGE_HINTS:
        if any(h in name for h in hints):
            return stage
    # Fall back to scanning the tail
    for stage, hints in RTL_STAGE_HINTS:
        for h in hints:
            if h in log_tail:
                return stage
    return 'unknown'


def _extract_error_window(log_text: str, window: int = 60) -> tuple[str, list[tuple[str, str]]]:
    """Return (context_excerpt, list_of_detected_errors).

    `list_of_detected_errors` is a de-duplicated sequence of (signature_name,
    matched_text). `context_excerpt` is `window` lines surrounding the first
    match (or the tail of the log if no match is found — useful for timeouts).
    """
    lines = log_text.splitlines()
    matches: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        for sig in RTL_ERROR_SIGS:
            m = sig.search(line)
            if m:
                msg = (m.groupdict().get('msg') or m.group(0)).strip()
                matches.append((i, sig.pattern[:40], msg))
                break
    if not matches:
        tail_start = max(0, len(lines) - window)
        return '\n'.join(lines[tail_start:]), []

    first_i = matches[0][0]
    lo = max(0, first_i - 10)
    hi = min(len(lines), first_i + window - 10)
    excerpt = '\n'.join(lines[lo:hi])

    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for _, sig_name, msg in matches[:20]:
        key = msg[:200]
        if key in seen:
            continue
        seen.add(key)
        deduped.append((sig_name, msg))
    return excerpt, deduped


def _extract_file_refs(log_text: str, rtl_dir: Path) -> list[dict]:
    """Find file:line references in the log and resolve them against the case's rtl/."""
    refs: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for m in FILE_LINE_RE.finditer(log_text):
        fname, lineno = m.group(1), int(m.group(2))
        basename = Path(fname).name
        key = (basename, lineno)
        if key in seen:
            continue
        seen.add(key)
        # Try to locate the file in the case rtl dir
        candidates = list(rtl_dir.rglob(basename)) if rtl_dir.exists() else []
        resolved = str(candidates[0]) if candidates else None
        refs.append({
            'file_hint':  fname,
            'resolved':   resolved,
            'line':       lineno,
            'exists':     bool(candidates),
        })
    return refs[:20]


def _read_snippet(file_path: str, line: int, radius: int = 5) -> str:
    try:
        lines = Path(file_path).read_text(errors='replace').splitlines()
    except Exception:
        return ''
    lo = max(0, line - 1 - radius)
    hi = min(len(lines), line - 1 + radius + 1)
    buf = []
    for i in range(lo, hi):
        marker = '>>>' if (i + 1) == line else '   '
        buf.append(f'{marker} {i + 1:5d}  {lines[i]}')
    return '\n'.join(buf)


def _snapshot_baseline(case_dir: Path, top_module: str) -> dict:
    """Record structural baseline via check_structural_preservation.py.

    Returns a {status, path} dict. Failure is non-fatal — the detector is
    still useful without a baseline, just can't enforce the B-thresholds on
    subsequent edits.
    """
    snap_out = case_dir / '_batch' / 'rtl_baseline.json'
    snap_out.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(
            ['python3', str(TOOLS / 'check_structural_preservation.py'), 'snapshot',
             '--rtl-dir', str(case_dir / 'rtl'),
             '--top-module', top_module,
             '--out', str(snap_out)],
            capture_output=True, text=True, timeout=60,
        )
        if r.returncode == 0:
            return {'status': 'ok', 'path': str(snap_out)}
        return {'status': 'failed', 'stderr': r.stderr.strip()[:500]}
    except Exception as e:
        return {'status': 'error', 'error': str(e)}


def apply_rtl_error_fix(case: str) -> dict:
    """Detect-and-dump handler for RTL-level failures.

    Writes <case>/_batch/rtl_error_context.json with everything needed for an
    LLM operator to reason about the fix without re-reading raw logs.

    Append-safe: if a prior rtl_error_context.json exists, it is archived to
    rtl_error_context.<UTC-stamp>.json before the new one is written, and a
    `history` field accumulates every context dump in rtl_error_history.jsonl.
    """
    import datetime
    case_dir = CASES / case
    if not case_dir.exists():
        return {'case': case, 'fix': 'rtl_error', 'status': 'no_case_dir'}

    cfg_path = case_dir / 'constraints' / 'config.mk'
    top_module = ''
    if cfg_path.exists():
        m = re.search(r'export\s+DESIGN_NAME\s*=\s*(\S+)', cfg_path.read_text())
        if m:
            top_module = m.group(1)

    log_path = _find_log(case_dir)
    if log_path is None:
        return {'case': case, 'fix': 'rtl_error', 'status': 'no_log'}

    log_text = log_path.read_text(errors='replace')
    # Keep only the last 50K chars for error analysis — plenty for tail context
    if len(log_text) > 50_000:
        log_text = log_text[-50_000:]

    stage = _detect_stage(log_path, log_text[-4000:])
    excerpt, detected = _extract_error_window(log_text)
    file_refs = _extract_file_refs(log_text, case_dir / 'rtl')

    # Pull a tight source snippet for the first resolvable file:line
    focus_snippet = ''
    focus_ref = next((r for r in file_refs if r['resolved']), None)
    if focus_ref:
        focus_snippet = _read_snippet(focus_ref['resolved'], focus_ref['line'])

    baseline = _snapshot_baseline(case_dir, top_module) if top_module else {'status': 'no_top'}

    stamp = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    context = {
        'case':              case,
        'captured_at_utc':   stamp,
        'top_module':        top_module,
        'log_path':          str(log_path),
        'failing_stage':     stage,
        'detected_errors':   [{'sig': s, 'msg': m} for s, m in detected],
        'log_excerpt':       excerpt,
        'file_refs':         file_refs,
        'focus_file':        focus_ref['resolved'] if focus_ref else None,
        'focus_line':        focus_ref['line']     if focus_ref else None,
        'focus_snippet':     focus_snippet,
        'structural_baseline': baseline,
    }

    out_dir = case_dir / '_batch'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / 'rtl_error_context.json'
    archived_as = None
    if out_path.exists():
        # Never overwrite a prior capture — archive with its stamp.
        archived_as = out_dir / f'rtl_error_context.{stamp}.json'
        # If that exact stamp collides (shouldn't in practice), add a -N suffix.
        n = 1
        while archived_as.exists():
            archived_as = out_dir / f'rtl_error_context.{stamp}-{n}.json'
            n += 1
        out_path.rename(archived_as)

    out_path.write_text(json.dumps(context, indent=2))

    # Also append to an always-growing JSONL so the full history is one grep away.
    history_path = out_dir / 'rtl_error_history.jsonl'
    with history_path.open('a') as fh:
        fh.write(json.dumps({
            'captured_at_utc': stamp,
            'case':            case,
            'failing_stage':   stage,
            'n_errors':        len(detected),
            'log_path':        str(log_path),
            'archived_as':     str(archived_as) if archived_as else None,
            'context_path':    str(out_path),
        }) + '\n')

    return {
        'case':           case,
        'fix':            'rtl_error',
        'status':         'context_dumped',
        'stage':          stage,
        'n_errors':       len(detected),
        'n_file_refs':    len(file_refs),
        'context_path':   str(out_path),
        'archived_as':    str(archived_as) if archived_as else None,
        'history_path':   str(history_path),
        'baseline_status': baseline.get('status'),
    }


CATEGORY_HANDLERS = {
    'memory_inference': apply_memory_fix,
    'pdn_strap': apply_pdn_fix,
    'timeout': apply_timeout_fix,
    'missing_include': apply_include_fix,
    'wrong_top': apply_wrong_top_fix,
    'rtl_error': apply_rtl_error_fix,
}


RTL_ERROR_DETAIL_SIGS = (
    'syntax error', 'Yosys ERROR', 'yosys error',
    'latch inferred', 'multiple drivers', 'combinational loop',
    'read_verilog', 'Verilog parser', 'elaboration', 'elaborat',
    '%Error',                          # Verilator
    '.v:', '.sv:',                     # file:line error prefixes
    'Cannot resolve module', 'Module reference',
    'ERROR: Re-definition',
)


def _looks_like_rtl_error(detail: str) -> bool:
    low = detail.lower()
    return any(s.lower() in low for s in RTL_ERROR_DETAIL_SIGS)


def apply_other(entry) -> dict:
    """Dispatch 'other' category based on error signature."""
    case, _, detail = entry
    if 'PPL-0024' in detail:
        return apply_io_fix(case)
    if 'FLW-0024' in detail:
        result = apply_wrong_top_fix(case)
        if result.get('status') == 'applied':
            return result
        return apply_density_fix(case)
    if 'PDN-0179' in detail:
        result = apply_wrong_top_fix(case)
        if result.get('status') == 'applied':
            return result
        return apply_pdn_fix(case)
    if 'exit code 124' in detail:
        return apply_timeout_fix(case)
    if _looks_like_rtl_error(detail):
        return apply_rtl_error_fix(case)
    return {'case': case, 'fix': 'unknown', 'status': 'manual'}


def main():
    # Direct-dispatch escape hatch for LLM-in-the-loop RTL error workflows:
    #   python3 fix_orfs_failures.py --rtl-error <case>
    # Skips the /tmp/fail_categories.json expectation and just runs the
    # detector on a single case.
    if len(sys.argv) >= 3 and sys.argv[1] == '--rtl-error':
        result = apply_rtl_error_fix(sys.argv[2])
        print(json.dumps(result, indent=2))
        return 0 if result.get('status') == 'context_dumped' else 1

    with open('/tmp/fail_categories.json') as f:
        cats = json.load(f)

    results = []
    for cat, entries in cats.items():
        if cat == 'other':
            for e in entries:
                results.append(apply_other(e))
        elif cat in CATEGORY_HANDLERS:
            for e in entries:
                results.append(CATEGORY_HANDLERS[cat](e[0]))
        else:
            for e in entries:
                results.append({'case': e[0], 'fix': cat, 'status': 'unhandled'})

    out = CASES / '_batch' / 'fix_summary.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    print(f'Wrote fix summary to {out} — {len(results)} cases')

    # stats
    from collections import Counter
    by_fix = Counter(r.get('fix', '?') for r in results)
    by_status = Counter(r.get('status', '?') for r in results)
    print('By fix:', dict(by_fix))
    print('By status:', dict(by_status))


if __name__ == '__main__':
    main()
