#!/usr/bin/env python3
"""Enforce the B-threshold structural preservation rules agreed for RTL auto-fix.

Given a baseline snapshot of an RTL directory and the current on-disk RTL,
decide whether the LLM's edits are structurally compatible with "this is still
the same design" — i.e. reject obvious cheating patterns where the agent
hollows the design out to make the tool stop complaining.

Rules (hard-coded, signed off by the researcher):

  top_ports          : exact match on (name, direction, width) for the top module's port list
  module_count       : may increase, never decrease
  always_blocks      : may drop by at most max(10%, 1)
  assign_statements  : may drop by at most 20%
  code_lines         : may drop by at most max(30%, 30 lines) (comments/blank ignored)
  initial_to_output  : no new `initial` block that assigns to a top-level output
  translate_off      : any new `translate_off` / `synthesis off` region is flagged (not auto-rejected)

Usage:
    # Snapshot the baseline (before any edits)
    python3 check_structural_preservation.py snapshot \\
        --rtl-dir   design_cases/aes_core/rtl \\
        --top-module aes_core \\
        --out       design_cases/aes_core/_batch/rtl_baseline.json

    # After editing, verify the design is still structurally the same
    python3 check_structural_preservation.py verify \\
        --rtl-dir   design_cases/aes_core/rtl \\
        --baseline  design_cases/aes_core/_batch/rtl_baseline.json \\
        --out       design_cases/aes_core/_batch/rtl_structcheck.json

Exit code: 0 = pass, 2 = reject, 3 = flag-only (needs human attention).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path


RTL_GLOBS = ('*.v', '*.sv', '*.vh', '*.svh')

# ----------------------------- RTL parsing helpers -----------------------------

# Strip /* */ and // comments. Order matters — block first, then line.
_BLOCK_COMMENT_RE = re.compile(r'/\*.*?\*/', re.DOTALL)
_LINE_COMMENT_RE  = re.compile(r'//[^\n]*')


def strip_comments(text: str) -> str:
    text = _BLOCK_COMMENT_RE.sub('', text)
    text = _LINE_COMMENT_RE.sub('', text)
    return text


_MODULE_HEADER_RE = re.compile(
    r'\bmodule\s+(\w+)\s*'                     # module name
    r'(?:\#\s*\([^;]*?\))?\s*'                 # optional parameter list (non-greedy, up to first ;)
    r'(\([^;]*?\))?\s*;',                      # optional port list header, terminated by ;
    re.DOTALL,
)
_ENDMODULE_RE = re.compile(r'\bendmodule\b')
_ALWAYS_RE    = re.compile(r'\balways(?:_ff|_comb|_latch)?\b')
_ASSIGN_RE    = re.compile(r'(?<![\w.])assign\b')
_INITIAL_RE   = re.compile(r'\binitial\b')
_TRANSLATE_OFF_RE = re.compile(r'(?:synthesis\s+(?:translate_off|off)|pragma\s+translate_off)',
                               re.IGNORECASE)


def list_rtl_files(rtl_dir: Path) -> list[Path]:
    files: list[Path] = []
    for pat in RTL_GLOBS:
        files.extend(sorted(rtl_dir.rglob(pat)))
    # Filter out anything under synth/ or backend/ etc. that may have leaked in
    return [f for f in files if 'backend' not in f.parts and 'synth' not in f.parts]


def parse_port_list(port_str: str) -> list[dict]:
    """Parse a Verilog-2001-style port header into a list of {name, dir, width}.

    Handles both ANSI style (e.g. ``input [7:0] a, output reg b``) and the
    trivial case where only bare names appear (pre-2001 style), in which case
    the direction/width will be empty and the caller should cross-reference
    against later ``input``/``output`` statements in the module body.
    """
    port_str = port_str.strip()
    if port_str.startswith('('):
        port_str = port_str[1:]
    if port_str.endswith(')'):
        port_str = port_str[:-1]
    port_str = strip_comments(port_str)
    if not port_str.strip():
        return []

    out: list[dict] = []
    last_dir = ''
    last_width = ''
    for raw in _split_port_items(port_str):
        item = raw.strip()
        if not item:
            continue
        direction = last_dir
        width = last_width
        m_dir = re.match(r'\b(input|output|inout)\b', item)
        if m_dir:
            direction = m_dir.group(1)
            item = item[m_dir.end():].strip()
            last_dir = direction
        # Strip signal type keywords that carry no port-identity meaning
        item = re.sub(r'^\s*(wire|reg|logic|signed|unsigned)\b\s*', '', item)
        m_width = re.match(r'(\[[^\]]*\])', item)
        if m_width:
            width = m_width.group(1).replace(' ', '')
            item = item[m_width.end():].strip()
            last_width = width
        # Remaining should be the name (possibly followed by a default value)
        m_name = re.match(r'(\w+)', item)
        if not m_name:
            continue
        out.append({'name': m_name.group(1), 'dir': direction, 'width': width})
    return out


def _split_port_items(port_str: str) -> list[str]:
    """Split a port list on commas that are outside of brackets."""
    depth = 0
    buf: list[str] = []
    items: list[str] = []
    for ch in port_str:
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        if ch == ',' and depth == 0:
            items.append(''.join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        items.append(''.join(buf))
    return items


def extract_top_port_list(rtl_files: list[Path], top_module: str) -> list[dict]:
    """Return the ANSI-parsed port list for `top_module`. Falls back to scanning
    ``input``/``output`` declarations inside the module body for pre-2001 RTL.
    """
    for f in rtl_files:
        try:
            raw = f.read_text(errors='replace')
        except Exception:
            continue
        text = strip_comments(raw)
        for m in _MODULE_HEADER_RE.finditer(text):
            if m.group(1) != top_module:
                continue
            port_header = m.group(2) or ''
            ports = parse_port_list(port_header)
            # If header-style ports had no direction info, fall back to body scan.
            if ports and all(not p['dir'] for p in ports):
                body_start = m.end()
                body_end_m = _ENDMODULE_RE.search(text, body_start)
                body = text[body_start:body_end_m.start() if body_end_m else len(text)]
                ports = _merge_with_body_decls(ports, body)
            return ports
    return []


_BODY_DECL_RE = re.compile(
    r'\b(input|output|inout)\s*(?:(wire|reg|logic)\s*)?(\[[^\]]*\])?\s*([\w,\s]+?)\s*;',
    re.DOTALL,
)


def _merge_with_body_decls(ports: list[dict], body: str) -> list[dict]:
    lookup = {p['name']: p for p in ports}
    for m in _BODY_DECL_RE.finditer(body):
        direction, _typ, width, names = m.groups()
        width = (width or '').replace(' ', '')
        for name in [n.strip() for n in names.split(',') if n.strip()]:
            if name in lookup:
                lookup[name]['dir']   = direction
                lookup[name]['width'] = width
    return ports


def count_non_comment_lines(rtl_files: list[Path]) -> int:
    total = 0
    for f in rtl_files:
        try:
            raw = f.read_text(errors='replace')
        except Exception:
            continue
        stripped = strip_comments(raw)
        for line in stripped.splitlines():
            if line.strip():
                total += 1
    return total


def compute_structure(rtl_dir: Path, top_module: str) -> dict:
    """Compute the structural fingerprint of an RTL directory."""
    rtl_files = list_rtl_files(rtl_dir)
    module_names: list[str] = []
    always_blocks = 0
    assigns = 0
    initial_blocks = 0
    translate_off_regions = 0

    for f in rtl_files:
        try:
            raw = f.read_text(errors='replace')
        except Exception:
            continue
        text = strip_comments(raw)
        module_names.extend(m.group(1) for m in _MODULE_HEADER_RE.finditer(text))
        always_blocks += len(_ALWAYS_RE.findall(text))
        assigns       += len(_ASSIGN_RE.findall(text))
        initial_blocks += len(_INITIAL_RE.findall(text))
        # Count translate_off markers on the *raw* text (pragmas live in comments
        # but the usual Verilog way is via comment pragmas, so use raw).
        translate_off_regions += len(_TRANSLATE_OFF_RE.findall(raw))

    return {
        'top_module':        top_module,
        'top_ports':         extract_top_port_list(rtl_files, top_module),
        'module_count':      len(module_names),
        'module_names':      sorted(module_names),
        'always_blocks':     always_blocks,
        'assign_statements': assigns,
        'initial_blocks':    initial_blocks,
        'translate_off':     translate_off_regions,
        'code_lines':        count_non_comment_lines(rtl_files),
        'file_count':        len(rtl_files),
        'rtl_files':         [str(f.relative_to(rtl_dir)) for f in rtl_files],
    }


# ----------------------------- Rule evaluation -----------------------------

def _initial_drives_top_outputs(rtl_dir: Path, top_module: str,
                                top_outputs: set[str]) -> list[str]:
    """Find newly-introduced `initial` blocks that assign to top-level outputs.

    The check is conservative: any ``initial`` block whose body textually
    contains ``<output> =`` or ``<output><=`` is reported.
    """
    if not top_outputs:
        return []
    offenders: list[str] = []
    for f in list_rtl_files(rtl_dir):
        try:
            raw = f.read_text(errors='replace')
        except Exception:
            continue
        text = strip_comments(raw)
        for m in re.finditer(r'\binitial\b\s*(begin\b.*?\bend\b|[^;]*;)',
                             text, flags=re.DOTALL):
            body = m.group(0)
            for sig in top_outputs:
                if re.search(rf'\b{re.escape(sig)}\s*(?:<)?=', body):
                    offenders.append(f'{f.name}: initial assigns to output "{sig}"')
    return offenders


def verify(baseline: dict, current: dict, rtl_dir: Path) -> dict:
    reasons_reject: list[str] = []
    reasons_flag:   list[str] = []

    top = baseline.get('top_module') or current.get('top_module', '')

    # Rule 1 — top port list exact match
    base_ports = baseline.get('top_ports', [])
    curr_ports = current.get('top_ports', [])
    if base_ports != curr_ports:
        diff = _port_diff(base_ports, curr_ports)
        reasons_reject.append(f'top port list changed for "{top}": {diff}')

    # Rule 2 — module count never drops
    if current['module_count'] < baseline['module_count']:
        missing = sorted(set(baseline['module_names']) - set(current['module_names']))
        reasons_reject.append(
            f'module count dropped ({baseline["module_count"]} → {current["module_count"]}); '
            f'missing: {missing[:5]}'
        )

    # Rule 3 — always blocks
    base_always = baseline['always_blocks']
    curr_always = current['always_blocks']
    drop = base_always - curr_always
    max_drop = max(1, math.ceil(base_always * 0.10))
    if drop > max_drop:
        reasons_reject.append(
            f'always blocks dropped by {drop} ({base_always} → {curr_always}); '
            f'budget was {max_drop}'
        )

    # Rule 4 — assign statements
    base_assign = baseline['assign_statements']
    curr_assign = current['assign_statements']
    drop = base_assign - curr_assign
    max_drop = math.ceil(base_assign * 0.20)
    if drop > max_drop and drop > 1:
        reasons_reject.append(
            f'assign statements dropped by {drop} ({base_assign} → {curr_assign}); '
            f'budget was {max_drop}'
        )

    # Rule 5 — code lines
    base_lines = baseline['code_lines']
    curr_lines = current['code_lines']
    drop = base_lines - curr_lines
    max_drop = max(30, math.ceil(base_lines * 0.30))
    if drop > max_drop:
        reasons_reject.append(
            f'code lines dropped by {drop} ({base_lines} → {curr_lines}); '
            f'budget was {max_drop}'
        )

    # Rule 6 — initial drives top output
    top_outputs = {p['name'] for p in base_ports if p.get('dir') == 'output'}
    offenders = _initial_drives_top_outputs(rtl_dir, top, top_outputs)
    if offenders:
        reasons_reject.append('initial block assigns to top output: ' + '; '.join(offenders))

    # Rule 7 — new translate_off regions (flag, don't reject)
    if current['translate_off'] > baseline['translate_off']:
        delta = current['translate_off'] - baseline['translate_off']
        reasons_flag.append(
            f'{delta} new translate_off / synthesis-off region(s) introduced — please justify'
        )

    verdict = 'pass'
    if reasons_reject:
        verdict = 'reject'
    elif reasons_flag:
        verdict = 'flag'

    return {
        'verdict':           verdict,
        'top_module':        top,
        'reasons_reject':    reasons_reject,
        'reasons_flag':      reasons_flag,
        'baseline_summary':  _summarize(baseline),
        'current_summary':   _summarize(current),
    }


def _port_diff(base: list[dict], curr: list[dict]) -> str:
    base_names = [p['name'] for p in base]
    curr_names = [p['name'] for p in curr]
    added   = sorted(set(curr_names) - set(base_names))
    removed = sorted(set(base_names) - set(curr_names))
    changed: list[str] = []
    for bp in base:
        cp = next((c for c in curr if c['name'] == bp['name']), None)
        if cp and (bp['dir'] != cp['dir'] or bp['width'] != cp['width']):
            changed.append(
                f'{bp["name"]} ({bp["dir"]} {bp["width"]} → {cp["dir"]} {cp["width"]})'
            )
    parts = []
    if added:   parts.append(f'added={added}')
    if removed: parts.append(f'removed={removed}')
    if changed: parts.append(f'changed={changed}')
    return '; '.join(parts) or 'reordered'


def _summarize(s: dict) -> dict:
    return {k: s[k] for k in (
        'module_count', 'always_blocks', 'assign_statements',
        'initial_blocks', 'translate_off', 'code_lines', 'file_count',
    )}


# ----------------------------- CLI -----------------------------

def cmd_snapshot(args: argparse.Namespace) -> int:
    rtl_dir = Path(args.rtl_dir).resolve()
    if not rtl_dir.is_dir():
        print(f'error: rtl_dir not found: {rtl_dir}', file=sys.stderr)
        return 1
    struct = compute_structure(rtl_dir, args.top_module)
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(struct, indent=2))
    print(f'[snapshot] top={args.top_module} modules={struct["module_count"]} '
          f'always={struct["always_blocks"]} assigns={struct["assign_statements"]} '
          f'lines={struct["code_lines"]} ports={len(struct["top_ports"])} '
          f'→ {out_path}')
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    rtl_dir = Path(args.rtl_dir).resolve()
    baseline_path = Path(args.baseline).resolve()
    if not baseline_path.exists():
        print(f'error: baseline not found: {baseline_path}', file=sys.stderr)
        return 1
    baseline = json.loads(baseline_path.read_text())
    current = compute_structure(rtl_dir, baseline['top_module'])
    result = verify(baseline, current, rtl_dir)

    if args.out:
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2))

    print(f'[verify] verdict={result["verdict"]}')
    for r in result['reasons_reject']:
        print(f'  REJECT: {r}')
    for r in result['reasons_flag']:
        print(f'  FLAG:   {r}')

    if result['verdict'] == 'reject':
        return 2
    if result['verdict'] == 'flag':
        return 3
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)

    p_snap = sub.add_parser('snapshot', help='Record baseline structural fingerprint')
    p_snap.add_argument('--rtl-dir',    required=True)
    p_snap.add_argument('--top-module', required=True)
    p_snap.add_argument('--out',        required=True)
    p_snap.set_defaults(func=cmd_snapshot)

    p_ver = sub.add_parser('verify', help='Verify current RTL against baseline')
    p_ver.add_argument('--rtl-dir',  required=True)
    p_ver.add_argument('--baseline', required=True)
    p_ver.add_argument('--out',      default=None)
    p_ver.set_defaults(func=cmd_verify)

    args = p.parse_args()
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
