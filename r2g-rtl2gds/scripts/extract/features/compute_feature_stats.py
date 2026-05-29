#!/usr/bin/env python3
"""Roll the eight graph-feature CSVs into a compact per-design statistics JSON.

Pure stdlib (csv + statistics + math + json). Reads the feature CSVs from a features
directory and writes reports/features_stats.json. A CSV that is missing or empty is
recorded with status "skipped". Mirrors compute_label_stats.py.
"""
import csv
import json
import math
import os
import statistics
import sys

# csv name -> numeric columns to summarize (min/mean/p50/p90/p95/p99/max).
SUMMARY_COLS = {
    "nodes_gate": ["cell_area", "cell_power"],
    "nodes_net": ["fanout", "pin_count", "num_layer", "hpwl_um"],
    "nodes_iopin": ["nearest_tap_distance_um"],
    "nodes_pin": ["sum_pin_cap_fF"],
    "edges_gate_pin": [],
    "edges_pin_net": [],
    "edges_iopin_net": [],
}
# graph-level metadata scalars surfaced directly (single-row CSV).
METADATA_SCALARS = ["num_cells", "num_nets", "num_ios", "avg_fanout", "C_total"]

ORDER = ["metadata", "nodes_gate", "nodes_net", "nodes_iopin", "nodes_pin",
         "edges_gate_pin", "edges_pin_net", "edges_iopin_net"]


def _percentile(sorted_vals, q):
    n = len(sorted_vals)
    if n == 0:
        return None
    if n == 1:
        return sorted_vals[0]
    idx = q * (n - 1)
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] * (hi - idx) + sorted_vals[hi] * (idx - lo)


def numeric_summary(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    return {
        "min": vals[0],
        "max": vals[-1],
        "mean": statistics.fmean(vals),
        "p50": _percentile(vals, 0.50),
        "p90": _percentile(vals, 0.90),
        "p95": _percentile(vals, 0.95),
        "p99": _percentile(vals, 0.99),
    }


def _col_floats(rows, col):
    out = []
    for row in rows:
        try:
            out.append(float(row[col]))
        except (ValueError, KeyError, TypeError):
            pass
    return out


def _read_rows(path):
    if not os.path.exists(path):
        return None, "csv missing"
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return None, "csv empty"
    return rows, None


def summarize(features_dir, name):
    rows, reason = _read_rows(os.path.join(features_dir, f"{name}.csv"))
    if rows is None:
        return {"status": "skipped", "reason": reason}
    res = {"status": "ok", "rows": len(rows)}
    if name == "metadata":
        first = rows[0]
        for col in METADATA_SCALARS:
            try:
                res[col] = float(first[col])
            except (ValueError, KeyError, TypeError):
                res[col] = first.get(col)
    for col in SUMMARY_COLS.get(name, []):
        res[col] = numeric_summary(_col_floats(rows, col))
    return res


def build_report(features_dir, out_path, design="unknown", platform="unknown",
                 spef_present=None):
    report = {"design": design, "platform": platform, "features": {}}
    if spef_present is not None:
        report["spef_present"] = bool(spef_present)
    for name in ORDER:
        report["features"][name] = summarize(features_dir, name)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    return report


def _parse_spef_arg(val):
    if val is None:
        return None
    return str(val).strip().lower() in {"1", "true", "yes"}


def main():
    if len(sys.argv) < 3:
        print("usage: compute_feature_stats.py <features_dir> <out_json> [design] [platform] [spef_present]")
        sys.exit(1)
    features_dir = sys.argv[1]
    out_path = sys.argv[2]
    design = sys.argv[3] if len(sys.argv) > 3 else "unknown"
    platform = sys.argv[4] if len(sys.argv) > 4 else "unknown"
    spef_present = _parse_spef_arg(sys.argv[5]) if len(sys.argv) > 5 else None
    report = build_report(features_dir, out_path, design, platform, spef_present)
    ok = sum(1 for v in report["features"].values() if v["status"] == "ok")
    print(f"Wrote {out_path}: {ok}/{len(report['features'])} feature sets present")


if __name__ == "__main__":
    main()
