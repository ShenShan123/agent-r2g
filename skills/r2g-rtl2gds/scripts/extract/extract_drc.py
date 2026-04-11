#!/usr/bin/env python3
"""
Extract DRC results from KLayout lyrdb and count report.
Produces a JSON summary with violation counts and categories.
"""
from pathlib import Path
import json
import re
import sys
import xml.etree.ElementTree as ET


def parse_drc_count(drc_dir: Path) -> int:
    """Parse 6_drc_count.rpt for total violation count."""
    count_file = drc_dir / '6_drc_count.rpt'
    if count_file.exists():
        text = count_file.read_text(encoding='utf-8', errors='ignore').strip()
        try:
            return int(text)
        except ValueError:
            pass
    return -1


def parse_lyrdb(drc_dir: Path) -> dict:
    """Parse KLayout lyrdb (XML) for DRC violation categories."""
    lyrdb_file = drc_dir / '6_drc.lyrdb'
    if not lyrdb_file.exists():
        return {}

    categories = {}
    try:
        tree = ET.parse(lyrdb_file)
        root = tree.getroot()

        # Parse categories
        cat_map = {}
        for cat in root.iter('category'):
            name_el = cat.find('name')
            desc_el = cat.find('description')
            if name_el is not None and name_el.text:
                cat_name = name_el.text.strip()
                cat_desc = desc_el.text.strip() if desc_el is not None and desc_el.text else ''
                cat_map[cat_name] = cat_desc

        # Count violations per category
        for item in root.iter('item'):
            cat_el = item.find('category')
            if cat_el is not None and cat_el.text:
                cat_name = cat_el.text.strip()
                if cat_name not in categories:
                    categories[cat_name] = {
                        'count': 0,
                        'description': cat_map.get(cat_name, ''),
                    }
                categories[cat_name]['count'] += 1

    except ET.ParseError:
        # Fallback: count <value> tags
        text = lyrdb_file.read_text(encoding='utf-8', errors='ignore')
        count = text.count('<value>')
        if count > 0:
            categories['unknown'] = {'count': count, 'description': 'Parsed via fallback'}

    return categories


def parse_drc_log(drc_dir: Path) -> dict:
    """Parse DRC log for runtime info."""
    info = {}
    log_file = drc_dir / '6_drc.log'
    if not log_file.exists():
        log_file = drc_dir / 'drc_run.log'
    if not log_file.exists():
        return info

    text = log_file.read_text(encoding='utf-8', errors='ignore')

    # Look for elapsed time
    m = re.search(r'(?:real|elapsed|Total time)[:\s]+([\d.]+)', text)
    if m:
        info['elapsed_seconds'] = float(m.group(1))

    # Look for errors
    if 'error' in text.lower():
        error_lines = [l.strip() for l in text.splitlines() if 'error' in l.lower()]
        info['errors'] = error_lines[:5]

    return info


def main():
    if len(sys.argv) < 3:
        print('usage: extract_drc.py <project-root> <output.json>', file=sys.stderr)
        sys.exit(1)

    project_root = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    drc_dir = project_root / 'drc'

    total_count = parse_drc_count(drc_dir)
    categories = parse_lyrdb(drc_dir)
    log_info = parse_drc_log(drc_dir)

    # If total count was not from count file, sum categories
    if total_count < 0 and categories:
        total_count = sum(c['count'] for c in categories.values())

    result = {
        'status': 'clean' if total_count == 0 else ('fail' if total_count > 0 else 'unknown'),
        'total_violations': total_count if total_count >= 0 else None,
        'categories': categories,
        'log_info': log_info,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(out_path)


if __name__ == '__main__':
    main()
