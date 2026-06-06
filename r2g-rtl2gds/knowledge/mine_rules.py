#!/usr/bin/env python3
"""Surface repeated failure signatures as a review queue.

Usage:
  mine_rules.py [--db <path>] [--out <path>]
                [--min-occurrences 3] [--min-distinct-designs 2]

Scans failure_events + runs, groups by signature, and emits
knowledge/failure_candidates.json — a human-review queue for new
entries in references/failure-patterns.md.

Never auto-merges into failure-patterns.md.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import knowledge_db


def _fetch(conn) -> list[dict]:
    sql = (
        "SELECT r.design_name, r.design_family, r.platform, "
        "r.core_utilization, r.place_density_lb_addon, "
        "r.synth_hierarchical, r.abc_area, "
        "f.signature, f.stage, f.detail "
        "FROM failure_events f "
        "JOIN runs r ON r.run_id = f.run_id"
    )
    cur = conn.execute(sql)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _median(values):
    cleaned = [v for v in values if v is not None]
    return statistics.median(cleaned) if cleaned else None


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _mine_fix_candidates(conn, min_resolved: int = 3) -> list[dict]:
    """Roll up resolved fix episodes into evidence-backed promotion candidates.

    Reads fix_trajectories and groups per
    (design_family, platform, check_type, violation_class, winning_strategy).
    Keeps groups with at least ``min_resolved`` resolved episodes. The result is
    a human-review queue for promotion into references/failure-patterns.md — it is
    NEVER auto-written there (that file stays human-curated, per spec D1/§12).

    Returns [] if the fix_trajectories table is absent so existing behavior is
    unaffected on legacy databases.
    """
    if not _table_exists(conn, "fix_trajectories"):
        return []

    cur = conn.execute(
        "SELECT design_family, platform, check_type, violation_class, "
        "winning_strategy, outcome, fix_session_id "
        "FROM fix_trajectories"
    )
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]

    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        key = (
            r["design_family"], r["platform"], r["check_type"],
            r["violation_class"], r["winning_strategy"],
        )
        by_key[key].append(r)

    candidates = []
    for key, group in sorted(by_key.items(), key=lambda kv: tuple("" if x is None else str(x) for x in kv[0])):
        family, platform, check, violation_class, winning_strategy = key
        resolved = [r for r in group if r["outcome"] == "resolved"]
        abandoned = [r for r in group if r["outcome"] == "abandoned"]
        if len(resolved) < min_resolved:
            continue
        total = len(resolved) + len(abandoned)
        candidates.append({
            "family": family,
            "platform": platform,
            "check": check,
            "violation_class": violation_class,
            "winning_strategy": winning_strategy,
            "resolved": len(resolved),
            "abandoned": len(abandoned),
            "clearance_rate": (len(resolved) / total) if total else None,
            "example_session": resolved[0]["fix_session_id"],
        })
    return candidates


def mine(db_path: Path | str,
         out_path: Path | str,
         min_occurrences: int = 3,
         min_distinct_designs: int = 2) -> dict:
    db_path = Path(db_path)
    out_path = Path(out_path)

    with contextlib.closing(knowledge_db.connect(db_path)) as conn:
        rows = _fetch(conn)
        fix_candidates = _mine_fix_candidates(conn)

    by_sig: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_sig[r["signature"]].append(r)

    candidates = []
    for sig, group in sorted(by_sig.items()):
        distinct_designs = {r["design_name"] for r in group}
        if len(group) < min_occurrences:
            continue
        if len(distinct_designs) < min_distinct_designs:
            continue
        candidates.append({
            "signature": sig,
            "occurrences": len(group),
            "distinct_designs": len(distinct_designs),
            "designs": sorted(distinct_designs),
            "stages": sorted({r["stage"] for r in group if r["stage"]}),
            "config_medians": {
                "core_utilization": _median([r["core_utilization"] for r in group]),
                "place_density_lb_addon": _median(
                    [r["place_density_lb_addon"] for r in group]
                ),
                "synth_hierarchical_rate": (
                    sum(1 for r in group if r["synth_hierarchical"]) / len(group)
                ),
                "abc_area_rate": sum(1 for r in group if r["abc_area"]) / len(group),
            },
            "sample_detail": next((r["detail"] for r in group if r["detail"]), None),
        })

    data = {
        "generated_at": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "min_occurrences": min_occurrences,
        "min_distinct_designs": min_distinct_designs,
        "candidates": candidates,
        "fix_candidates": fix_candidates,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", type=Path, default=knowledge_db.DEFAULT_DB_PATH)
    p.add_argument("--out", type=Path,
                   default=knowledge_db.DEFAULT_KNOWLEDGE_DIR / "failure_candidates.json")
    p.add_argument("--min-occurrences", type=int, default=3)
    p.add_argument("--min-distinct-designs", type=int, default=2)
    args = p.parse_args()

    data = mine(args.db, args.out,
                min_occurrences=args.min_occurrences,
                min_distinct_designs=args.min_distinct_designs)
    print(f"Wrote {args.out} ({len(data['candidates'])} candidate signatures).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
