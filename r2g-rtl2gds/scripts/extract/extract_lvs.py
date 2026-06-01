#!/usr/bin/env python3
"""
Extract LVS results from KLayout lvsdb report.
Produces a JSON summary with match/mismatch status and details.
"""
from pathlib import Path
import json
import re
import sys
import xml.etree.ElementTree as ET


def parse_lvsdb(lvs_dir: Path) -> dict:
    """Parse KLayout lvsdb (XML) for LVS comparison results."""
    lvsdb_file = lvs_dir / '6_lvs.lvsdb'
    if not lvsdb_file.exists():
        return {}

    result = {}
    try:
        tree = ET.parse(lvsdb_file)
        root = tree.getroot()

        # Look for status elements
        for status_el in root.iter('status'):
            if status_el.text:
                result['raw_status'] = status_el.text.strip()

        # Count mismatches
        mismatches = 0
        for mismatch in root.iter('mismatch'):
            mismatches += 1
        result['mismatch_count'] = mismatches

        # Look for net/device counts
        for net_el in root.iter('net_count'):
            if net_el.text:
                result.setdefault('net_count', int(net_el.text))
        for dev_el in root.iter('device_count'):
            if dev_el.text:
                result.setdefault('device_count', int(dev_el.text))
        for pin_el in root.iter('pin_count'):
            if pin_el.text:
                result.setdefault('pin_count', int(pin_el.text))

    except ET.ParseError:
        # KLayout lvsdb may use text format (#%lvsdb-klayout), not XML
        text = lvsdb_file.read_text(encoding='utf-8', errors='ignore')
        lower_text = text.lower()
        if 'mismatch' in lower_text:
            result['raw_status'] = 'text_mismatch_found'
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

    return result


_CRASH_RE = re.compile(
    r"signal number:\s*\d+|segmentation|sigsegv|sort_circuit|gen_log_entry"
    r"|ruby_run_node|klayout_crash\.log",
    re.I,
)

_DEVICE_EXTRACT_RE = re.compile(
    r'"extract_devices"\s+in:\s+FreePDK45|"netlist"\s+in:\s+FreePDK45'
    r"|extract_devices|\"netlist\"",
    re.I,
)


def _read_both_logs(lvs_dir: Path) -> tuple[str, str]:
    """Return (text_6_lvs, text_run_log) for crash-detection; empty string if absent."""
    def _read(p: Path) -> str:
        return p.read_text(encoding='utf-8', errors='ignore') if p.exists() else ''
    return _read(lvs_dir / '6_lvs.log'), _read(lvs_dir / 'lvs_run.log')


def parse_lvs_log(lvs_dir: Path) -> dict:
    """Parse LVS log for status and runtime info.

    Reads both 6_lvs.log and lvs_run.log so that crash signatures present only
    in lvs_run.log (e.g. ``ERROR: Signal number: 11``) are detected correctly.
    Sets info['crash'] = True and info['crash_line'] when a crash is found.
    Sets info['reached_device_extraction'] = True when device-extraction progress
    is logged (indicating an incomplete run, not just a missing log).
    """
    info = {}
    text_main, text_run = _read_both_logs(lvs_dir)
    combined = text_main + "\n" + text_run

    if not combined.strip():
        return info

    # --- crash detection (checked across BOTH logs) ---
    m_crash = _CRASH_RE.search(combined)
    if m_crash:
        info['crash'] = True
        info['crash_line'] = m_crash.group(0)

    # --- device-extraction progress (indicates run started but may not have
    #     produced a verdict) ---
    if _DEVICE_EXTRACT_RE.search(combined):
        info['reached_device_extraction'] = True

    # Use main log (6_lvs.log) preferentially for verdict/timing; fall back to
    # lvs_run.log so the rest of the logic mirrors the original behaviour.
    text = text_main if text_main.strip() else text_run
    lower = text.lower()

    # Determine match status from log — check negative patterns FIRST
    # because "netlists match" is a substring of "netlists don't match"
    if "don't match" in lower or 'do not match' in lower or 'not match' in lower:
        info['log_status'] = 'mismatch'
    elif 'netlists match' in lower or 'lvs clean' in lower or 'circuits match' in lower:
        info['log_status'] = 'match'
    elif 'not supported' in lower:
        info['log_status'] = 'not_supported'

    # Look for elapsed time
    m = re.search(r'(?:real|elapsed|Total time)[:\s]+([\d.]+)', text)
    if m:
        info['elapsed_seconds'] = float(m.group(1))

    # Look for errors
    error_lines = [l.strip() for l in text.splitlines()
                   if 'error' in l.lower() and 'no error' not in l.lower()]
    if error_lines:
        info['errors'] = error_lines[:5]

    # Surface crash line as a prominent error so diagnose_signoff_fix can find it
    if info.get('crash') and info.get('crash_line'):
        errors = info.setdefault('errors', [])
        crash_entry = f"CRASH: {info['crash_line']}"
        if crash_entry not in errors:
            errors.insert(0, crash_entry)

    return info


def main():
    if len(sys.argv) < 3:
        print('usage: extract_lvs.py <project-root> <output.json>', file=sys.stderr)
        sys.exit(1)

    project_root = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    lvs_dir = project_root / 'lvs'

    # Honor a fresh skip marker only if no actual LVS log exists. Stale
    # `lvs_result.json` files from a previous run (when the platform had no
    # rules) must NOT override a successful new LVS log/lvsdb.
    skip_file = lvs_dir / 'lvs_result.json'
    log_present = (lvs_dir / '6_lvs.log').exists() or (lvs_dir / 'lvs_run.log').exists()
    if skip_file.exists() and not log_present:
        try:
            skip_data = json.loads(skip_file.read_text(encoding='utf-8'))
            if skip_data.get('status') == 'skipped':
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(json.dumps(skip_data, indent=2), encoding='utf-8')
                print(out_path)
                return
        except Exception:
            pass

    lvsdb_result = parse_lvsdb(lvs_dir)
    log_info = parse_lvs_log(lvs_dir)

    lvsdb_exists = (lvs_dir / '6_lvs.lvsdb').exists()

    # Determine overall status
    mismatch_count = lvsdb_result.get('mismatch_count', -1)
    log_status = log_info.get('log_status', '')

    result_reason: str | None = None

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
        # Distinguish crash vs. incomplete vs. truly unknown
        if log_info.get('crash'):
            status = 'crash'
            result_reason = 'klayout_cpp_crash'
        elif log_info.get('reached_device_extraction') and not lvsdb_exists:
            # Run got deep enough to extract devices / write netlist but then
            # died before producing a match/mismatch verdict and no lvsdb file.
            status = 'incomplete'
            result_reason = 'lvs_no_verdict_no_lvsdb'
        else:
            status = 'unknown'

    result = {
        'status': status,
        'mismatch_count': mismatch_count if mismatch_count >= 0 else None,
        'lvsdb': lvsdb_result,
        'log_info': log_info,
    }
    if result_reason is not None:
        result['reason'] = result_reason

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(out_path)


if __name__ == '__main__':
    main()
