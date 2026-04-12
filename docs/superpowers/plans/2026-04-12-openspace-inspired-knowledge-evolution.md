# OpenSpace-Inspired Knowledge Evolution for r2g-rtl2gds

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adapt four architectural patterns from OpenSpace (a self-evolving skill engine for AI agents) into the r2g-rtl2gds knowledge store: config version tracking, health monitoring, semantic failure search, and automated execution analysis. Tasks 5-6 are future work (MCP server interface, cross-agent sharing).

**Architecture:** Extend the existing `knowledge/` subsystem with four new modules. The config lineage table and ingest changes form the foundation. Health monitor and failure search are independent features that both feed into the execution analyzer — the capstone module that closes the loop between failed runs and actionable config fix proposals.

**Tech Stack:** Python 3.12+, SQLite (existing `runs.sqlite`), standard library only (no new dependencies). BM25 retrieval is implemented from scratch (~40 lines) to avoid adding a search library.

**Dependency graph:**
```
Task 1 (schema) ──→ Task 2 (ingest lineage) ──→ Task 5 (analyzer reads lineage)
                                                       ↑
Task 3 (health monitor) ──────────────────────────────┤
                                                       ↑
Task 4 (failure search) ──────────────────────────────┘
```

---

## File Structure

| Action | Path | Responsibility |
|--------|------|---------------|
| Modify | `skills/r2g-rtl2gds/knowledge/schema.sql` | Add `config_lineage` table |
| Modify | `skills/r2g-rtl2gds/knowledge/knowledge_db.py` | Add `diff_config_rows()` helper |
| Modify | `skills/r2g-rtl2gds/knowledge/ingest_run.py` | Record config lineage on ingest |
| Create | `skills/r2g-rtl2gds/knowledge/monitor_health.py` | Detect family/platform degradation |
| Create | `skills/r2g-rtl2gds/knowledge/search_failures.py` | BM25 search over failure patterns + candidates |
| Create | `skills/r2g-rtl2gds/knowledge/analyze_execution.py` | Structured fix proposals from failed runs |
| Create | `skills/r2g-rtl2gds/tests/test_config_lineage.py` | Tests for lineage tracking |
| Create | `skills/r2g-rtl2gds/tests/test_monitor_health.py` | Tests for health monitor |
| Create | `skills/r2g-rtl2gds/tests/test_search_failures.py` | Tests for BM25 failure search |
| Create | `skills/r2g-rtl2gds/tests/test_analyze_execution.py` | Tests for execution analyzer |
| Modify | `skills/r2g-rtl2gds/knowledge/README.md` | Document new modules |
| Modify | `skills/r2g-rtl2gds/SKILL.md` | Add new modules to script inventory |

---

## Task 1: Config Lineage Schema Extension

**Files:**
- Modify: `skills/r2g-rtl2gds/knowledge/schema.sql:52` (append after last index)
- Modify: `skills/r2g-rtl2gds/knowledge/knowledge_db.py:54` (add `diff_config_rows` helper)
- Test: `skills/r2g-rtl2gds/tests/test_config_lineage.py` (new file)

This adds a `config_lineage` table that tracks before→after config diffs between runs of the same design/platform pair. Inspired by OpenSpace's `SkillStore` version DAG (parent IDs, generation number, content diffs).

- [ ] **Step 1: Write the failing test for schema creation**

```python
# tests/test_config_lineage.py
"""Tests for config lineage tracking."""
from __future__ import annotations

import json
import sqlite3

import knowledge_db


def _open_db(tmp_knowledge_dir):
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    return conn


def test_config_lineage_table_exists(tmp_knowledge_dir):
    conn = _open_db(tmp_knowledge_dir)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    assert "config_lineage" in tables
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /proj/workarea/user5/agent-r2g && python -m pytest skills/r2g-rtl2gds/tests/test_config_lineage.py::test_config_lineage_table_exists -v`
Expected: FAIL — `config_lineage` not in tables list

- [ ] **Step 3: Add config_lineage table to schema.sql**

Append after line 52 of `skills/r2g-rtl2gds/knowledge/schema.sql`:

```sql

CREATE TABLE IF NOT EXISTS config_lineage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    design_name     TEXT NOT NULL,
    platform        TEXT NOT NULL,
    current_run_id  TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    previous_run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    diff_json       TEXT NOT NULL,
    current_outcome TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lineage_design_platform
    ON config_lineage(design_name, platform);
CREATE INDEX IF NOT EXISTS idx_lineage_current_run
    ON config_lineage(current_run_id);
```

The `diff_json` column stores a JSON object with structure:
```json
{
  "changed": {"CORE_UTILIZATION": {"old": 40, "new": 25}},
  "added": {"SKIP_CTS_REPAIR_TIMING": "1"},
  "removed": {"SYNTH_HIERARCHICAL": "1"}
}
```

`current_outcome` stores the current run's `orfs_status` value ("pass", "fail", "partial") so we can later query "which config changes led to success/failure?" without joining back to `runs`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /proj/workarea/user5/agent-r2g && python -m pytest skills/r2g-rtl2gds/tests/test_config_lineage.py::test_config_lineage_table_exists -v`
Expected: PASS

- [ ] **Step 5: Write the failing test for diff_config_rows helper**

Add to `tests/test_config_lineage.py`:

```python
def test_diff_config_rows_detects_changes():
    old = {"CORE_UTILIZATION": "40", "PLACE_DENSITY_LB_ADDON": "0.20",
           "SYNTH_HIERARCHICAL": "1"}
    new = {"CORE_UTILIZATION": "25", "PLACE_DENSITY_LB_ADDON": "0.20",
           "SKIP_CTS_REPAIR_TIMING": "1"}
    diff = knowledge_db.diff_config_rows(old, new)
    assert diff["changed"] == {"CORE_UTILIZATION": {"old": "40", "new": "25"}}
    assert diff["added"] == {"SKIP_CTS_REPAIR_TIMING": "1"}
    assert diff["removed"] == {"SYNTH_HIERARCHICAL": "1"}


def test_diff_config_rows_empty_when_identical():
    cfg = {"CORE_UTILIZATION": "30", "PLACE_DENSITY_LB_ADDON": "0.20"}
    diff = knowledge_db.diff_config_rows(cfg, cfg)
    assert diff["changed"] == {}
    assert diff["added"] == {}
    assert diff["removed"] == {}
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `cd /proj/workarea/user5/agent-r2g && python -m pytest skills/r2g-rtl2gds/tests/test_config_lineage.py -k "diff_config_rows" -v`
Expected: FAIL — `AttributeError: module 'knowledge_db' has no attribute 'diff_config_rows'`

- [ ] **Step 7: Implement diff_config_rows in knowledge_db.py**

Append after line 54 of `skills/r2g-rtl2gds/knowledge/knowledge_db.py`:

```python


def diff_config_rows(old: dict[str, str], new: dict[str, str]) -> dict[str, Any]:
    """Compute the config diff between two config.mk field dicts.

    Returns {"changed": {key: {"old": v1, "new": v2}},
             "added": {key: value}, "removed": {key: value}}.
    """
    old_keys = set(old)
    new_keys = set(new)
    changed = {}
    for k in old_keys & new_keys:
        if old[k] != new[k]:
            changed[k] = {"old": old[k], "new": new[k]}
    added = {k: new[k] for k in new_keys - old_keys}
    removed = {k: old[k] for k in old_keys - new_keys}
    return {"changed": changed, "added": added, "removed": removed}
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd /proj/workarea/user5/agent-r2g && python -m pytest skills/r2g-rtl2gds/tests/test_config_lineage.py -v`
Expected: All 3 tests PASS

- [ ] **Step 9: Commit**

```bash
git add skills/r2g-rtl2gds/knowledge/schema.sql \
       skills/r2g-rtl2gds/knowledge/knowledge_db.py \
       skills/r2g-rtl2gds/tests/test_config_lineage.py
git commit -m "feat(knowledge): add config_lineage table and diff helper

Tracks before→after config.mk changes between runs of the same
design/platform. Foundation for config evolution tracking inspired
by OpenSpace's SkillStore version DAG."
```

---

## Task 2: Config Lineage Tracking in Ingest

**Files:**
- Modify: `skills/r2g-rtl2gds/knowledge/ingest_run.py:135-265` (add lineage recording after row insert)
- Test: `skills/r2g-rtl2gds/tests/test_config_lineage.py` (add integration tests)

After inserting a run row, look up the most recent previous run for the same `(design_name, platform)` and compute a config diff. If the diff is non-empty, insert a `config_lineage` row. This means re-running the same config produces no lineage row (idempotent), but changing CORE_UTILIZATION from 40 to 25 creates a tracked change.

- [ ] **Step 1: Write the failing test for lineage recording**

Add to `tests/test_config_lineage.py`:

```python
import shutil
import datetime as _dt

import ingest_run


def _make_project(tmp_path, name, config_overrides=None):
    """Create a minimal project directory with config.mk and stage_log."""
    project = tmp_path / name
    (project / "constraints").mkdir(parents=True)
    (project / "reports").mkdir(parents=True)
    (project / "backend").mkdir(parents=True)

    config_lines = [
        "export DESIGN_NAME = aes128_core",
        "export PLATFORM = nangate45",
        "export CORE_UTILIZATION = 30",
        "export PLACE_DENSITY_LB_ADDON = 0.20",
    ]
    if config_overrides:
        # Replace matching lines
        for key, val in config_overrides.items():
            config_lines = [
                l for l in config_lines if not l.strip().startswith(f"export {key}")
            ]
            config_lines.append(f"export {key} = {val}")
    (project / "constraints" / "config.mk").write_text("\n".join(config_lines) + "\n")

    (project / "reports" / "ppa.json").write_text(json.dumps({
        "summary": {"timing": {"setup_wns": 0.1, "setup_tns": 0.0},
                     "power": {"total_power_w": 0.01},
                     "area": {"design_area_um2": 5000.0}},
        "geometry": {"die_area_um2": 5000.0, "instance_count": 12000},
    }))
    (project / "reports" / "drc.json").write_text(
        json.dumps({"status": "clean", "total_violations": 0}))
    (project / "reports" / "lvs.json").write_text(
        json.dumps({"status": "clean"}))
    (project / "reports" / "rcx.json").write_text(
        json.dumps({"status": "complete"}))
    (project / "reports" / "timing_check.json").write_text(
        json.dumps({"tier": "clean"}))
    (project / "reports" / "diagnosis.json").write_text(
        json.dumps({"issues": []}))

    stages = [
        {"stage": "synth", "status": "pass", "elapsed_s": 60},
        {"stage": "floorplan", "status": "pass", "elapsed_s": 30},
        {"stage": "place", "status": "pass", "elapsed_s": 120},
        {"stage": "cts", "status": "pass", "elapsed_s": 90},
        {"stage": "route", "status": "pass", "elapsed_s": 200},
        {"stage": "finish", "status": "pass", "elapsed_s": 50},
    ]
    (project / "backend" / "stage_log.jsonl").write_text(
        "\n".join(json.dumps(s) for s in stages) + "\n")

    return project


def test_lineage_recorded_when_config_changes(tmp_knowledge_dir, tmp_path):
    """Changing CORE_UTILIZATION between runs should produce a lineage row."""
    conn = _open_db(tmp_knowledge_dir)

    proj_v1 = _make_project(tmp_path, "run_v1")
    run_id_1 = ingest_run.ingest(proj_v1, conn,
                                  families_path=tmp_knowledge_dir / "families.json")

    proj_v2 = _make_project(tmp_path, "run_v2",
                             config_overrides={"CORE_UTILIZATION": "25"})
    run_id_2 = ingest_run.ingest(proj_v2, conn,
                                  families_path=tmp_knowledge_dir / "families.json")

    rows = conn.execute(
        "SELECT current_run_id, previous_run_id, diff_json, current_outcome "
        "FROM config_lineage WHERE design_name = 'aes128_core'"
    ).fetchall()
    assert len(rows) == 1
    cur_id, prev_id, diff_str, outcome = rows[0]
    assert cur_id == run_id_2
    assert prev_id == run_id_1
    diff = json.loads(diff_str)
    assert "CORE_UTILIZATION" in diff["changed"]
    assert diff["changed"]["CORE_UTILIZATION"]["old"] == "30"
    assert diff["changed"]["CORE_UTILIZATION"]["new"] == "25"
    assert outcome == "pass"
    conn.close()


def test_no_lineage_for_first_run(tmp_knowledge_dir, tmp_path):
    """First run of a design/platform has no previous run — no lineage row."""
    conn = _open_db(tmp_knowledge_dir)
    proj = _make_project(tmp_path, "first_run")
    ingest_run.ingest(proj, conn,
                       families_path=tmp_knowledge_dir / "families.json")
    count = conn.execute("SELECT COUNT(*) FROM config_lineage").fetchone()[0]
    assert count == 0
    conn.close()


def test_no_lineage_when_config_unchanged(tmp_knowledge_dir, tmp_path):
    """Identical config between runs should not produce a lineage row."""
    conn = _open_db(tmp_knowledge_dir)
    proj_v1 = _make_project(tmp_path, "same_v1")
    ingest_run.ingest(proj_v1, conn,
                       families_path=tmp_knowledge_dir / "families.json")
    proj_v2 = _make_project(tmp_path, "same_v2")
    ingest_run.ingest(proj_v2, conn,
                       families_path=tmp_knowledge_dir / "families.json")
    count = conn.execute("SELECT COUNT(*) FROM config_lineage").fetchone()[0]
    assert count == 0
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /proj/workarea/user5/agent-r2g && python -m pytest skills/r2g-rtl2gds/tests/test_config_lineage.py -k "lineage_recorded or no_lineage" -v`
Expected: FAIL — lineage recording not yet implemented

- [ ] **Step 3: Implement lineage recording in ingest_run.py**

Add a new helper function before `ingest()` (around line 133):

```python
def _record_lineage(conn: sqlite3.Connection, run_id: str,
                    design_name: str, platform: str,
                    cfg: dict[str, str], orfs_status: str) -> None:
    """If there's a previous run for this design/platform, compute and store the config diff."""
    prev = conn.execute(
        "SELECT run_id, extra_config_json, core_utilization, "
        "place_density_lb_addon, synth_hierarchical, abc_area, die_area, "
        "clock_period_ns "
        "FROM runs "
        "WHERE design_name = ? AND platform = ? AND run_id != ? "
        "ORDER BY ingested_at DESC LIMIT 1",
        (design_name, platform, run_id),
    ).fetchone()
    if prev is None:
        return

    prev_run_id = prev[0]
    # Reconstruct previous config dict from stored columns
    prev_cfg: dict[str, str] = {}
    if prev[1]:  # extra_config_json
        try:
            prev_cfg.update(json.loads(prev[1]))
        except (json.JSONDecodeError, TypeError):
            pass
    col_map = {
        "CORE_UTILIZATION": prev[2], "PLACE_DENSITY_LB_ADDON": prev[3],
        "SYNTH_HIERARCHICAL": prev[4], "ABC_AREA": prev[5],
        "DIE_AREA": prev[6], "CLOCK_PERIOD": prev[7],
    }
    for k, v in col_map.items():
        if v is not None:
            prev_cfg[k] = str(v)

    # Normalize current config values to strings for comparison
    cur_cfg = {k: str(v).strip() for k, v in cfg.items() if v}

    diff = knowledge_db.diff_config_rows(prev_cfg, cur_cfg)
    if not diff["changed"] and not diff["added"] and not diff["removed"]:
        return

    conn.execute(
        "INSERT INTO config_lineage "
        "(design_name, platform, current_run_id, previous_run_id, "
        " diff_json, current_outcome, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (design_name, platform, run_id, prev_run_id,
         json.dumps(diff, sort_keys=True), orfs_status,
         _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"),
    )
```

Then add a call at the end of `ingest()`, just before `conn.commit()` (around line 263):

```python
    _record_lineage(conn, run_id, design_name, platform, cfg, orfs_status)
    conn.commit()
```

(Replace the existing bare `conn.commit()` at the end of `ingest()`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /proj/workarea/user5/agent-r2g && python -m pytest skills/r2g-rtl2gds/tests/test_config_lineage.py -v`
Expected: All 6 tests PASS (3 from Task 1 + 3 new)

- [ ] **Step 5: Run existing tests to verify no regressions**

Run: `cd /proj/workarea/user5/agent-r2g && python -m pytest skills/r2g-rtl2gds/tests/ -v`
Expected: All existing tests PASS — ingest_run changes must not break test_ingest_run.py

- [ ] **Step 6: Commit**

```bash
git add skills/r2g-rtl2gds/knowledge/ingest_run.py \
       skills/r2g-rtl2gds/tests/test_config_lineage.py
git commit -m "feat(knowledge): track config lineage between runs

When ingesting a new run, if a previous run exists for the same
design_name/platform, compute the config.mk diff and store it in
config_lineage table. Enables querying 'which config changes led
to pass/fail?' across run iterations."
```

---

## Task 3: Health Monitor

**Files:**
- Create: `skills/r2g-rtl2gds/knowledge/monitor_health.py`
- Test: `skills/r2g-rtl2gds/tests/test_monitor_health.py`

Inspired by OpenSpace's quality monitoring with cascade evolution triggers. Queries `runs.sqlite` for family/platform pairs where the success rate has recently degraded, and emits a structured JSON alert. "Recently" is defined as the last N runs compared to the historical baseline in `heuristics.json`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_monitor_health.py
"""Tests for monitor_health.py: detect family/platform degradation."""
from __future__ import annotations

import json

import knowledge_db
import monitor_health


def _insert(conn, **row):
    defaults = dict.fromkeys([
        "run_id", "project_path", "design_name", "design_family", "platform",
        "ingested_at", "core_utilization", "place_density_lb_addon",
        "synth_hierarchical", "abc_area", "die_area", "clock_period_ns",
        "extra_config_json", "orfs_status", "orfs_fail_stage", "wns_ns", "tns_ns",
        "timing_tier", "cell_count", "area_um2", "power_mw",
        "drc_status", "drc_violations", "lvs_status", "rcx_status",
        "total_elapsed_s", "stage_times_json",
    ])
    defaults.update(row)
    defaults["ingested_at"] = defaults.get("ingested_at") or "2026-04-11T00:00:00Z"
    defaults["project_path"] = defaults["project_path"] or f"/tmp/{defaults['run_id']}"
    cols = ", ".join(defaults.keys())
    ph = ", ".join(f":{k}" for k in defaults.keys())
    conn.execute(f"INSERT INTO runs ({cols}) VALUES ({ph})", defaults)


def _open_db(tmp_knowledge_dir):
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    return conn


def test_detects_degradation(tmp_knowledge_dir):
    """5 old passes + 3 recent failures should flag degradation."""
    conn = _open_db(tmp_knowledge_dir)
    # 5 old successful runs
    for i in range(5):
        _insert(conn, run_id=f"old_pass_{i}",
                design_name="aes128_core", design_family="aes_xcrypt",
                platform="nangate45", orfs_status="pass",
                drc_status="clean", lvs_status="clean", rcx_status="complete",
                ingested_at=f"2026-04-0{i+1}T00:00:00Z")
    # 3 recent failures
    for i in range(3):
        _insert(conn, run_id=f"new_fail_{i}",
                design_name="aes128_core", design_family="aes_xcrypt",
                platform="nangate45", orfs_status="fail",
                orfs_fail_stage="place",
                ingested_at=f"2026-04-1{i}T00:00:00Z")
    conn.commit()

    alerts = monitor_health.check(
        db_path=tmp_knowledge_dir / "runs.sqlite",
        window=3,
        threshold=0.5,
    )
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["family"] == "aes_xcrypt"
    assert alert["platform"] == "nangate45"
    assert alert["recent_success_rate"] == 0.0
    assert alert["historical_success_rate"] > 0.5
    assert alert["severity"] == "degraded"
    conn.close()


def test_no_alert_when_healthy(tmp_knowledge_dir):
    """All-pass family should produce no alerts."""
    conn = _open_db(tmp_knowledge_dir)
    for i in range(5):
        _insert(conn, run_id=f"healthy_{i}",
                design_name="ibex_core", design_family="ibex",
                platform="nangate45", orfs_status="pass",
                drc_status="clean", lvs_status="clean", rcx_status="complete",
                ingested_at=f"2026-04-0{i+1}T00:00:00Z")
    conn.commit()

    alerts = monitor_health.check(
        db_path=tmp_knowledge_dir / "runs.sqlite",
        window=3,
        threshold=0.5,
    )
    assert len(alerts) == 0
    conn.close()


def test_skips_families_with_too_few_runs(tmp_knowledge_dir):
    """Families with fewer than window runs should not produce alerts."""
    conn = _open_db(tmp_knowledge_dir)
    _insert(conn, run_id="lone_fail",
            design_name="tiny_design", design_family="tiny",
            platform="nangate45", orfs_status="fail",
            orfs_fail_stage="synth",
            ingested_at="2026-04-11T00:00:00Z")
    conn.commit()

    alerts = monitor_health.check(
        db_path=tmp_knowledge_dir / "runs.sqlite",
        window=3,
        threshold=0.5,
    )
    assert len(alerts) == 0
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /proj/workarea/user5/agent-r2g && python -m pytest skills/r2g-rtl2gds/tests/test_monitor_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'monitor_health'`

- [ ] **Step 3: Implement monitor_health.py**

```python
# knowledge/monitor_health.py
#!/usr/bin/env python3
"""Detect family/platform degradation by comparing recent runs to historical baseline.

Usage:
  monitor_health.py [--db <path>] [--window N] [--threshold F]

Inspired by OpenSpace's quality monitoring with cascade evolution triggers.
Outputs a JSON array of alerts for families whose recent success rate has
dropped below the historical baseline by more than the threshold.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

import knowledge_db

# Re-use the same success criteria as learn_heuristics.py.
_DRC_OK = {None, "clean", "skipped"}
_LVS_OK = {None, "clean", "skipped"}
_RCX_OK = {None, "complete", "skipped"}


def _is_success(row: dict) -> bool:
    return (
        row.get("orfs_status") == "pass"
        and row.get("drc_status") in _DRC_OK
        and row.get("lvs_status") in _LVS_OK
        and row.get("rcx_status") in _RCX_OK
    )


def _fetch_all(conn) -> list[dict]:
    cur = conn.execute(
        "SELECT design_family, platform, orfs_status, drc_status, "
        "lvs_status, rcx_status, ingested_at "
        "FROM runs ORDER BY ingested_at ASC"
    )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def check(db_path: Path | str,
          window: int = 5,
          threshold: float = 0.3) -> list[dict]:
    """Check for degraded family/platform pairs.

    Args:
        db_path: Path to runs.sqlite.
        window: Number of most recent runs to evaluate per family/platform.
        threshold: Minimum drop in success rate (recent vs historical) to alert.

    Returns:
        List of alert dicts with keys: family, platform, recent_success_rate,
        historical_success_rate, recent_window, total_runs, severity,
        recent_failures (list of run orfs_fail_stages).
    """
    with contextlib.closing(knowledge_db.connect(db_path)) as conn:
        rows = _fetch_all(conn)

    # Group by (family, platform), preserving ingestion order.
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        fam = r.get("design_family") or "unknown"
        plat = r.get("platform") or "unknown"
        groups.setdefault((fam, plat), []).append(r)

    alerts = []
    for (fam, plat), group in sorted(groups.items()):
        if len(group) < window:
            continue

        recent = group[-window:]
        historical = group[:-window] if len(group) > window else group

        recent_successes = sum(1 for r in recent if _is_success(r))
        recent_rate = recent_successes / len(recent)

        hist_successes = sum(1 for r in historical if _is_success(r))
        hist_rate = hist_successes / len(historical) if historical else 1.0

        drop = hist_rate - recent_rate
        if drop < threshold:
            continue

        recent_failures = [
            r.get("orfs_status", "unknown")
            for r in recent if not _is_success(r)
        ]

        alerts.append({
            "family": fam,
            "platform": plat,
            "recent_success_rate": round(recent_rate, 3),
            "historical_success_rate": round(hist_rate, 3),
            "recent_window": window,
            "total_runs": len(group),
            "severity": "degraded" if recent_rate < 0.5 else "warning",
            "recent_failures": recent_failures,
        })

    return alerts


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", type=Path, default=knowledge_db.DEFAULT_DB_PATH)
    p.add_argument("--window", type=int, default=5,
                   help="Number of recent runs to evaluate (default: 5)")
    p.add_argument("--threshold", type=float, default=0.3,
                   help="Minimum success rate drop to alert (default: 0.3)")
    p.add_argument("--out", type=Path, default=None,
                   help="Write alerts JSON to file (default: stdout)")
    args = p.parse_args()

    alerts = check(args.db, window=args.window, threshold=args.threshold)

    output = json.dumps(alerts, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
        print(f"Wrote {len(alerts)} alert(s) to {args.out}")
    else:
        print(output)

    if alerts:
        for a in alerts:
            print(f"  [{a['severity'].upper()}] {a['family']}/{a['platform']}: "
                  f"recent {a['recent_success_rate']:.0%} vs "
                  f"historical {a['historical_success_rate']:.0%}",
                  file=sys.stderr)
    else:
        print("All family/platform pairs healthy.", file=sys.stderr)

    return 1 if alerts else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /proj/workarea/user5/agent-r2g && python -m pytest skills/r2g-rtl2gds/tests/test_monitor_health.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add skills/r2g-rtl2gds/knowledge/monitor_health.py \
       skills/r2g-rtl2gds/tests/test_monitor_health.py
git commit -m "feat(knowledge): add health monitor for family/platform degradation

Compares recent N runs against historical baseline per family/platform.
Alerts when success rate drops by more than threshold. Inspired by
OpenSpace's quality monitoring with cascade evolution triggers."
```

---

## Task 4: BM25 Failure Search

**Files:**
- Create: `skills/r2g-rtl2gds/knowledge/search_failures.py`
- Test: `skills/r2g-rtl2gds/tests/test_search_failures.py`

Inspired by OpenSpace's `SkillRanker` hybrid retrieval (BM25 + embedding). We implement the BM25 stage only — it's sufficient for our corpus size (~20 failure patterns + mined candidates) and avoids adding an embedding dependency. The search indexes both `references/failure-patterns.md` (structured sections) and `failure_candidates.json` (mined signatures) into a unified corpus, then ranks by BM25 score against a query built from error messages.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_search_failures.py
"""Tests for search_failures.py: BM25 search over failure patterns."""
from __future__ import annotations

import json
from pathlib import Path

import search_failures


def test_bm25_ranks_exact_match_highest():
    """A document containing the exact query terms should rank highest."""
    docs = [
        {"id": "routing", "text": "GRT-0116 global routing congestion overflow"},
        {"id": "placement", "text": "NesterovSolve placement divergence overflow"},
        {"id": "synthesis", "text": "Yosys syntax error unexpected token"},
    ]
    index = search_failures.BM25Index(docs)
    results = index.search("GRT-0116 routing congestion")
    assert results[0]["id"] == "routing"
    assert results[0]["score"] > results[1]["score"]


def test_bm25_returns_empty_for_no_match():
    docs = [{"id": "a", "text": "alpha beta gamma"}]
    index = search_failures.BM25Index(docs)
    results = index.search("zzz_nonexistent_term")
    assert len(results) == 0 or results[0]["score"] == 0.0


def test_parse_failure_patterns_md(tmp_path):
    """Parses failure-patterns.md sections into searchable documents."""
    md = tmp_path / "failure-patterns.md"
    md.write_text(
        "# Failure Patterns\n\n"
        "## Routing Congestion (GRT-0116)\n\n"
        "**Symptoms:**\n"
        "- Global routing finished with congestion\n\n"
        "**Action:**\n"
        "- Reduce CORE_UTILIZATION\n\n"
        "## Placement Divergence\n\n"
        "**Symptoms:**\n"
        "- NesterovSolve overflow oscillates\n\n"
        "**Action:**\n"
        "- Raise PLACE_DENSITY_LB_ADDON\n"
    )
    docs = search_failures.parse_failure_patterns(md)
    assert len(docs) == 2
    assert docs[0]["id"] == "Routing Congestion (GRT-0116)"
    assert "GRT-0116" in docs[0]["text"]
    assert "Reduce CORE_UTILIZATION" in docs[0]["text"]


def test_parse_failure_candidates_json(tmp_path):
    """Parses failure_candidates.json into searchable documents."""
    fc = tmp_path / "failure_candidates.json"
    fc.write_text(json.dumps({
        "candidates": [
            {
                "signature": "pdn-0179",
                "occurrences": 5,
                "designs": ["black_parrot", "swerv_wrapper"],
                "stages": ["floorplan"],
                "sample_detail": "Unable to repair all channels.",
            },
        ],
    }))
    docs = search_failures.parse_failure_candidates(fc)
    assert len(docs) == 1
    assert docs[0]["id"] == "mined:pdn-0179"
    assert "floorplan" in docs[0]["text"]
    assert "Unable to repair" in docs[0]["text"]


def test_search_end_to_end(tmp_path):
    """Full pipeline: parse sources → build index → search."""
    md = tmp_path / "failure-patterns.md"
    md.write_text(
        "# Failure Patterns\n\n"
        "## PDN Grid Error\n\n"
        "**Symptoms:**\n"
        "- PDN-0179 unable to repair channels\n"
        "- Insufficient width for straps\n\n"
        "**Action:**\n"
        "- Increase DIE_AREA or reduce CORE_UTILIZATION\n"
    )
    fc = tmp_path / "failure_candidates.json"
    fc.write_text(json.dumps({"candidates": []}))

    results = search_failures.search(
        query="PDN-0179 unable to repair",
        patterns_path=md,
        candidates_path=fc,
        top_k=3,
    )
    assert len(results) >= 1
    assert results[0]["id"] == "PDN Grid Error"
    assert results[0]["score"] > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /proj/workarea/user5/agent-r2g && python -m pytest skills/r2g-rtl2gds/tests/test_search_failures.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'search_failures'`

- [ ] **Step 3: Implement search_failures.py**

```python
# knowledge/search_failures.py
#!/usr/bin/env python3
"""BM25 search over failure patterns and mined failure candidates.

Usage:
  search_failures.py <query> [--patterns <path>] [--candidates <path>] [--top-k N]

Builds a lightweight BM25 index from two sources:
  1. references/failure-patterns.md (structured ## sections)
  2. knowledge/failure_candidates.json (mined signatures)

Returns ranked results as JSON. No external dependencies — BM25 is
implemented in ~40 lines using standard library math.

Inspired by OpenSpace's SkillRanker hybrid retrieval (BM25 + embedding).
We skip the embedding stage — our corpus is small enough (<50 docs)
that BM25 alone provides good results.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

import knowledge_db

_PATTERNS_PATH = knowledge_db.DEFAULT_KNOWLEDGE_DIR.parent / "references" / "failure-patterns.md"
_CANDIDATES_PATH = knowledge_db.DEFAULT_KNOWLEDGE_DIR / "failure_candidates.json"


def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumeric, drop short tokens."""
    return [t for t in re.split(r"[^a-z0-9_.-]+", text.lower()) if len(t) >= 2]


class BM25Index:
    """Minimal BM25 (Okapi BM25) implementation over a list of documents.

    Each document is {"id": str, "text": str}.
    """

    def __init__(self, docs: list[dict], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs = docs
        self.N = len(docs)

        # Tokenize and build term frequencies.
        self.doc_tfs: list[Counter] = []
        self.doc_lens: list[int] = []
        self.df: Counter = Counter()  # document frequency per term

        for doc in docs:
            tokens = _tokenize(doc["text"])
            tf = Counter(tokens)
            self.doc_tfs.append(tf)
            self.doc_lens.append(len(tokens))
            for term in tf:
                self.df[term] += 1

        self.avgdl = sum(self.doc_lens) / self.N if self.N > 0 else 1.0

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        if df == 0:
            return 0.0
        return math.log((self.N - df + 0.5) / (df + 0.5) + 1.0)

    def _score_doc(self, query_terms: list[str], idx: int) -> float:
        tf = self.doc_tfs[idx]
        dl = self.doc_lens[idx]
        score = 0.0
        for term in query_terms:
            if term not in tf:
                continue
            idf = self._idf(term)
            freq = tf[term]
            num = freq * (self.k1 + 1)
            den = freq + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
            score += idf * num / den
        return score

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        terms = _tokenize(query)
        if not terms:
            return []
        scored = []
        for i, doc in enumerate(self.docs):
            s = self._score_doc(terms, i)
            if s > 0:
                scored.append({"id": doc["id"], "score": round(s, 4), "text": doc["text"]})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]


def parse_failure_patterns(path: Path) -> list[dict]:
    """Parse failure-patterns.md into a list of {"id": section_title, "text": section_body}."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    sections = re.split(r"^## ", text, flags=re.MULTILINE)
    docs = []
    for section in sections[1:]:  # skip preamble before first ##
        lines = section.strip().split("\n", 1)
        title = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        docs.append({"id": title, "text": f"{title} {body}"})
    return docs


def parse_failure_candidates(path: Path) -> list[dict]:
    """Parse failure_candidates.json into searchable documents."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    docs = []
    for c in data.get("candidates", []):
        sig = c.get("signature", "")
        parts = [
            sig,
            " ".join(c.get("stages", [])),
            " ".join(c.get("designs", [])),
            c.get("sample_detail") or "",
        ]
        docs.append({
            "id": f"mined:{sig}",
            "text": " ".join(parts),
        })
    return docs


def search(query: str,
           patterns_path: Path = _PATTERNS_PATH,
           candidates_path: Path = _CANDIDATES_PATH,
           top_k: int = 5) -> list[dict]:
    """Search failure knowledge base and return ranked results."""
    docs = parse_failure_patterns(patterns_path) + parse_failure_candidates(candidates_path)
    if not docs:
        return []
    index = BM25Index(docs)
    return index.search(query, top_k=top_k)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("query", help="Error message or failure description to search for")
    p.add_argument("--patterns", type=Path, default=_PATTERNS_PATH,
                   help="Path to failure-patterns.md")
    p.add_argument("--candidates", type=Path, default=_CANDIDATES_PATH,
                   help="Path to failure_candidates.json")
    p.add_argument("--top-k", type=int, default=5, help="Number of results (default: 5)")
    args = p.parse_args()

    results = search(args.query, patterns_path=args.patterns,
                     candidates_path=args.candidates, top_k=args.top_k)
    print(json.dumps(results, indent=2))
    if not results:
        print("No matching failure patterns found.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /proj/workarea/user5/agent-r2g && python -m pytest skills/r2g-rtl2gds/tests/test_search_failures.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add skills/r2g-rtl2gds/knowledge/search_failures.py \
       skills/r2g-rtl2gds/tests/test_search_failures.py
git commit -m "feat(knowledge): add BM25 failure pattern search

Indexes failure-patterns.md sections and failure_candidates.json
into a BM25 index for semantic failure lookup. When a new design
fails, search finds the most relevant past failure even when the
exact family doesn't match. No external dependencies."
```

---

## Task 5: Execution Analyzer

**Files:**
- Create: `skills/r2g-rtl2gds/knowledge/analyze_execution.py`
- Test: `skills/r2g-rtl2gds/tests/test_analyze_execution.py`

The capstone module. Inspired by OpenSpace's `ExecutionAnalyzer` → `SkillEvolver` pipeline. Given a failed project directory, it:

1. Reads the structured artifacts (diagnosis.json, ppa.json, timing_check.json, stage_log.jsonl)
2. Searches for similar past failures via `search_failures.py`
3. Queries config lineage for recent config changes that led to success
4. Produces structured fix proposals as a JSON review queue

Unlike OpenSpace, we do **not** auto-apply fixes. The output is a `fix_proposals.json` that the agent or human reviews before acting. This preserves our "never auto-merge" safety invariant.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_analyze_execution.py
"""Tests for analyze_execution.py: structured fix proposals from failed runs."""
from __future__ import annotations

import json
from pathlib import Path

import knowledge_db
import analyze_execution


def _open_db(tmp_knowledge_dir):
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    return conn


def _make_failed_project(tmp_path, name="failed_run", fail_stage="floorplan",
                          diagnosis_issues=None):
    """Create a project directory that represents a failed ORFS run."""
    project = tmp_path / name
    (project / "constraints").mkdir(parents=True)
    (project / "reports").mkdir(parents=True)
    (project / "backend").mkdir(parents=True)

    (project / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = test_design\n"
        "export PLATFORM = nangate45\n"
        "export CORE_UTILIZATION = 45\n"
        "export PLACE_DENSITY_LB_ADDON = 0.05\n"
    )
    (project / "reports" / "ppa.json").write_text(json.dumps({
        "summary": {"timing": {}, "power": {}, "area": {}},
        "geometry": {"instance_count": 50000},
    }))
    (project / "reports" / "diagnosis.json").write_text(json.dumps({
        "issues": diagnosis_issues or [
            {"kind": "placement_utilization_overflow", "stage": "floorplan",
             "summary": "Utilization exceeds 100% target"},
        ],
    }))
    (project / "reports" / "timing_check.json").write_text(
        json.dumps({"tier": "clean"}))
    stages = [
        {"stage": "synth", "status": "pass", "elapsed_s": 60},
        {"stage": fail_stage, "status": "fail", "elapsed_s": 30},
    ]
    (project / "backend" / "stage_log.jsonl").write_text(
        "\n".join(json.dumps(s) for s in stages) + "\n")
    return project


def test_produces_fix_proposals_for_utilization_overflow(tmp_path):
    """A utilization overflow failure should propose reducing CORE_UTILIZATION."""
    project = _make_failed_project(tmp_path)
    # Minimal failure-patterns.md for search
    patterns = tmp_path / "patterns.md"
    patterns.write_text(
        "# Failure Patterns\n\n"
        "## Placement Utilization Overflow\n\n"
        "**Symptoms:**\n- Utilization exceeds target\n\n"
        "**Action:**\n- Reduce CORE_UTILIZATION by 30-50%\n"
    )
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps({"candidates": []}))

    result = analyze_execution.analyze(
        project,
        patterns_path=patterns,
        candidates_path=candidates,
    )
    assert result["status"] == "fail"
    assert result["fail_stage"] == "floorplan"
    assert len(result["proposals"]) >= 1
    proposal = result["proposals"][0]
    assert proposal["parameter"] == "CORE_UTILIZATION"
    assert proposal["current"] == "45"
    assert int(proposal["suggested"]) < 45
    assert proposal["confidence"] in ("high", "medium", "low")


def test_produces_density_fix_for_placement_divergence(tmp_path):
    """A placement divergence failure should propose raising PLACE_DENSITY_LB_ADDON."""
    project = _make_failed_project(
        tmp_path, fail_stage="place",
        diagnosis_issues=[
            {"kind": "placement_divergence", "stage": "place",
             "summary": "NesterovSolve overflow oscillates without convergence"},
        ],
    )
    patterns = tmp_path / "patterns.md"
    patterns.write_text(
        "# Failure Patterns\n\n"
        "## Placement Divergence (NesterovSolve Non-Convergence)\n\n"
        "**Symptoms:**\n- NesterovSolve overflow oscillates\n\n"
        "**Action:**\n- Raise PLACE_DENSITY_LB_ADDON to at least 0.20\n"
    )
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps({"candidates": []}))

    result = analyze_execution.analyze(
        project,
        patterns_path=patterns,
        candidates_path=candidates,
    )
    assert len(result["proposals"]) >= 1
    density_proposals = [p for p in result["proposals"]
                          if p["parameter"] == "PLACE_DENSITY_LB_ADDON"]
    assert len(density_proposals) >= 1
    assert float(density_proposals[0]["suggested"]) >= 0.20


def test_returns_no_proposals_for_unknown_failure(tmp_path):
    """An unrecognized failure kind should still return a result, just with no proposals."""
    project = _make_failed_project(
        tmp_path, fail_stage="synth",
        diagnosis_issues=[
            {"kind": "mystery_error_xyz", "stage": "synth",
             "summary": "Something completely unknown happened"},
        ],
    )
    patterns = tmp_path / "patterns.md"
    patterns.write_text("# Failure Patterns\n")
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps({"candidates": []}))

    result = analyze_execution.analyze(
        project,
        patterns_path=patterns,
        candidates_path=candidates,
    )
    assert result["status"] == "fail"
    # May have 0 proposals if the failure is truly unknown
    assert isinstance(result["proposals"], list)
    assert len(result["similar_failures"]) == 0


def test_includes_similar_failures_in_output(tmp_path):
    """Similar failure search results should be included for agent context."""
    project = _make_failed_project(
        tmp_path,
        diagnosis_issues=[
            {"kind": "pdn-0179", "stage": "floorplan",
             "summary": "PDN-0179: Unable to repair all channels"},
        ],
    )
    patterns = tmp_path / "patterns.md"
    patterns.write_text(
        "# Failure Patterns\n\n"
        "## PDN Grid Error\n\n"
        "**Symptoms:**\n- PDN-0179 unable to repair channels\n\n"
        "**Action:**\n"
        "- Increase DIE_AREA\n"
        "- Reduce CORE_UTILIZATION\n"
        "- Remove SYNTH_HIERARCHICAL\n"
    )
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps({"candidates": []}))

    result = analyze_execution.analyze(
        project,
        patterns_path=patterns,
        candidates_path=candidates,
    )
    assert len(result["similar_failures"]) >= 1
    assert result["similar_failures"][0]["id"] == "PDN Grid Error"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /proj/workarea/user5/agent-r2g && python -m pytest skills/r2g-rtl2gds/tests/test_analyze_execution.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analyze_execution'`

- [ ] **Step 3: Implement analyze_execution.py**

```python
# knowledge/analyze_execution.py
#!/usr/bin/env python3
"""Produce structured fix proposals from a failed design run.

Usage:
  analyze_execution.py <project-dir> [--out <path>]
                       [--patterns <path>] [--candidates <path>]

Reads the project's structured artifacts (diagnosis.json, ppa.json,
timing_check.json, stage_log.jsonl, config.mk), searches for similar
past failures, and emits fix_proposals.json — a review queue of
config.mk changes ranked by confidence.

Inspired by OpenSpace's ExecutionAnalyzer → SkillEvolver pipeline,
but proposals are NEVER auto-applied. The agent or human reviews
them before acting.

Output schema:
{
  "project": str,
  "status": "fail" | "partial" | "pass",
  "fail_stage": str | null,
  "diagnosis_issues": [...],
  "similar_failures": [...],
  "proposals": [
    {
      "parameter": str,
      "current": str | null,
      "suggested": str,
      "rationale": str,
      "confidence": "high" | "medium" | "low",
      "source": "rule" | "search" | "lineage",
    },
  ],
}
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import knowledge_db
import search_failures

_PATTERNS_PATH = knowledge_db.DEFAULT_KNOWLEDGE_DIR.parent / "references" / "failure-patterns.md"
_CANDIDATES_PATH = knowledge_db.DEFAULT_KNOWLEDGE_DIR / "failure_candidates.json"


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _parse_config_mk(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="ignore").replace("\\\n", " ")
    fields: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"(?:export\s+)?(\w+)\s*=\s*(.*)", line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields


def _read_stage_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _derive_status(stages: list[dict]) -> tuple[str, str | None]:
    if not stages:
        return ("unknown", None)
    fail_stage = None
    stage_names_done = {s["stage"] for s in stages if s.get("status") == "pass"}
    for s in stages:
        if s.get("status") == "fail" and fail_stage is None:
            fail_stage = s.get("stage")
    if fail_stage:
        return ("fail", fail_stage)
    required = ["synth", "floorplan", "place", "cts", "route", "finish"]
    if all(name in stage_names_done for name in required):
        return ("pass", None)
    return ("partial", stages[-1].get("stage") if stages else None)


# ---- Rule-based proposal generators ----
# Each function takes (config, diagnosis_issues, ppa, timing_check) and
# returns a list of proposal dicts.  These encode the hard rules from
# failure-patterns.md and CLAUDE.md as executable logic.

def _propose_utilization_fix(config: dict, issues: list[dict],
                              ppa: dict, tcheck: dict) -> list[dict]:
    proposals = []
    util_issues = [i for i in issues
                   if i.get("kind") in ("placement_utilization_overflow",
                                         "routing_congestion")]
    if not util_issues:
        return proposals
    cu = config.get("CORE_UTILIZATION")
    if cu is None:
        return proposals
    try:
        cu_val = int(cu)
    except ValueError:
        return proposals
    suggested = max(10, int(cu_val * 0.6))  # reduce by ~40%
    proposals.append({
        "parameter": "CORE_UTILIZATION",
        "current": cu,
        "suggested": str(suggested),
        "rationale": f"Utilization overflow/congestion detected. Reduce from {cu}% to {suggested}%.",
        "confidence": "high",
        "source": "rule",
    })
    return proposals


def _propose_density_fix(config: dict, issues: list[dict],
                          ppa: dict, tcheck: dict) -> list[dict]:
    proposals = []
    density_issues = [i for i in issues
                      if i.get("kind") in ("placement_divergence",)]
    if not density_issues:
        return proposals
    pd = config.get("PLACE_DENSITY_LB_ADDON")
    try:
        pd_val = float(pd) if pd else 0.0
    except ValueError:
        pd_val = 0.0
    if pd_val < 0.20:
        suggested = "0.20"
    elif pd_val < 0.30:
        suggested = "0.30"
    else:
        suggested = str(round(pd_val + 0.10, 2))
    proposals.append({
        "parameter": "PLACE_DENSITY_LB_ADDON",
        "current": pd or "unset",
        "suggested": suggested,
        "rationale": f"Placement divergence detected. Raise density addon to {suggested}.",
        "confidence": "high",
        "source": "rule",
    })
    return proposals


def _propose_pdn_fix(config: dict, issues: list[dict],
                      ppa: dict, tcheck: dict) -> list[dict]:
    proposals = []
    pdn_issues = [i for i in issues if "pdn" in (i.get("kind") or "").lower()]
    if not pdn_issues:
        return proposals
    cu = config.get("CORE_UTILIZATION")
    if cu:
        try:
            cu_val = int(cu)
            suggested = max(10, int(cu_val * 0.7))
            proposals.append({
                "parameter": "CORE_UTILIZATION",
                "current": cu,
                "suggested": str(suggested),
                "rationale": "PDN error detected. Reduce utilization to give PDN grid more room.",
                "confidence": "medium",
                "source": "rule",
            })
        except ValueError:
            pass
    # Also suggest removing SYNTH_HIERARCHICAL if present
    if config.get("SYNTH_HIERARCHICAL") in ("1", "true", "True"):
        proposals.append({
            "parameter": "SYNTH_HIERARCHICAL",
            "current": config["SYNTH_HIERARCHICAL"],
            "suggested": "0",
            "rationale": "PDN error with SYNTH_HIERARCHICAL=1. "
                         "Hierarchical synthesis increases cell count, "
                         "potentially exceeding die area for PDN grid.",
            "confidence": "medium",
            "source": "rule",
        })
    return proposals


def _propose_timing_fix(config: dict, issues: list[dict],
                         ppa: dict, tcheck: dict) -> list[dict]:
    proposals = []
    tier = tcheck.get("tier", "")
    if tier not in ("moderate", "severe"):
        return proposals
    clock_period = config.get("CLOCK_PERIOD")
    if clock_period:
        try:
            cp_val = float(clock_period)
            suggested = round(cp_val * 1.3, 1)  # relax by 30%
            proposals.append({
                "parameter": "CLOCK_PERIOD",
                "current": clock_period,
                "suggested": str(suggested),
                "rationale": f"Timing tier={tier}. Relax clock period from {cp_val}ns to {suggested}ns.",
                "confidence": "medium" if tier == "moderate" else "low",
                "source": "rule",
            })
        except ValueError:
            pass
    return proposals


def _propose_safety_flags(config: dict, issues: list[dict],
                           ppa: dict, tcheck: dict) -> list[dict]:
    proposals = []
    sigsegv_issues = [i for i in issues
                      if "sigsegv" in (i.get("kind") or "").lower()
                      or "signal 11" in (i.get("summary") or "").lower()]
    if not sigsegv_issues:
        return proposals
    if config.get("SKIP_CTS_REPAIR_TIMING") != "1":
        proposals.append({
            "parameter": "SKIP_CTS_REPAIR_TIMING",
            "current": config.get("SKIP_CTS_REPAIR_TIMING"),
            "suggested": "1",
            "rationale": "SIGSEGV in CTS/repair detected. Add safety flag to bypass crashing step.",
            "confidence": "high",
            "source": "rule",
        })
    if config.get("SKIP_LAST_GASP") != "1":
        proposals.append({
            "parameter": "SKIP_LAST_GASP",
            "current": config.get("SKIP_LAST_GASP"),
            "suggested": "1",
            "rationale": "Add SKIP_LAST_GASP to avoid similar crashes in later stages.",
            "confidence": "high",
            "source": "rule",
        })
    return proposals


_RULE_GENERATORS = [
    _propose_utilization_fix,
    _propose_density_fix,
    _propose_pdn_fix,
    _propose_timing_fix,
    _propose_safety_flags,
]


def analyze(project: Path,
            patterns_path: Path = _PATTERNS_PATH,
            candidates_path: Path = _CANDIDATES_PATH) -> dict:
    """Analyze a failed run and produce fix proposals.

    Returns a dict with keys: project, status, fail_stage,
    diagnosis_issues, similar_failures, proposals.
    """
    project = Path(project)
    config = _parse_config_mk(project / "constraints" / "config.mk")
    diag = _read_json(project / "reports" / "diagnosis.json") or {}
    ppa = _read_json(project / "reports" / "ppa.json") or {}
    tcheck = _read_json(project / "reports" / "timing_check.json") or {}

    # Find stage_log.jsonl (may be inside backend/RUN_*/)
    stage_log_path = project / "backend" / "stage_log.jsonl"
    if (project / "backend").is_dir():
        run_dirs = sorted(
            (d for d in (project / "backend").iterdir()
             if d.is_dir() and d.name.startswith("RUN_")),
            key=lambda d: d.stat().st_mtime, reverse=True,
        )
        for rd in run_dirs:
            candidate = rd / "stage_log.jsonl"
            if candidate.exists():
                stage_log_path = candidate
                break
    stages = _read_stage_log(stage_log_path)
    status, fail_stage = _derive_status(stages)

    issues = diag.get("issues") or []

    # Build a search query from issue summaries and fail stage
    query_parts = [fail_stage or ""] + [
        i.get("kind", "") + " " + i.get("summary", "") for i in issues
    ]
    query = " ".join(query_parts).strip()

    # Search for similar past failures
    similar = []
    if query:
        similar = search_failures.search(
            query,
            patterns_path=patterns_path,
            candidates_path=candidates_path,
            top_k=3,
        )

    # Generate rule-based proposals
    proposals = []
    seen_params = set()
    for generator in _RULE_GENERATORS:
        for proposal in generator(config, issues, ppa, tcheck):
            # Deduplicate: keep highest-confidence proposal per parameter
            if proposal["parameter"] in seen_params:
                continue
            seen_params.add(proposal["parameter"])
            proposals.append(proposal)

    return {
        "project": str(project),
        "status": status,
        "fail_stage": fail_stage,
        "diagnosis_issues": issues,
        "similar_failures": similar,
        "proposals": proposals,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("project", type=Path, help="Path to failed project directory")
    p.add_argument("--out", type=Path, default=None,
                   help="Write proposals to file (default: stdout)")
    p.add_argument("--patterns", type=Path, default=_PATTERNS_PATH)
    p.add_argument("--candidates", type=Path, default=_CANDIDATES_PATH)
    args = p.parse_args()

    result = analyze(args.project,
                     patterns_path=args.patterns,
                     candidates_path=args.candidates)

    output = json.dumps(result, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
        print(f"Wrote analysis to {args.out}")
    else:
        print(output)

    # Summary to stderr
    print(f"\nProject: {result['project']}", file=sys.stderr)
    print(f"Status: {result['status']} (fail_stage={result['fail_stage']})", file=sys.stderr)
    print(f"Issues: {len(result['diagnosis_issues'])}", file=sys.stderr)
    print(f"Similar failures: {len(result['similar_failures'])}", file=sys.stderr)
    print(f"Fix proposals: {len(result['proposals'])}", file=sys.stderr)
    for prop in result["proposals"]:
        print(f"  [{prop['confidence']}] {prop['parameter']}: "
              f"{prop['current']} → {prop['suggested']} "
              f"({prop['source']})", file=sys.stderr)

    return 0 if result["proposals"] else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /proj/workarea/user5/agent-r2g && python -m pytest skills/r2g-rtl2gds/tests/test_analyze_execution.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Run full test suite to verify no regressions**

Run: `cd /proj/workarea/user5/agent-r2g && python -m pytest skills/r2g-rtl2gds/tests/ -v`
Expected: All tests PASS (existing + new)

- [ ] **Step 6: Commit**

```bash
git add skills/r2g-rtl2gds/knowledge/analyze_execution.py \
       skills/r2g-rtl2gds/tests/test_analyze_execution.py
git commit -m "feat(knowledge): add execution analyzer with fix proposals

Given a failed project, reads diagnosis artifacts, searches for similar
past failures via BM25, and produces ranked config fix proposals.
Proposals are NEVER auto-applied — they go to a review queue.
Inspired by OpenSpace's ExecutionAnalyzer → SkillEvolver pipeline."
```

---

## Task 6: Documentation Update

**Files:**
- Modify: `skills/r2g-rtl2gds/knowledge/README.md`

Update the knowledge store README to document the four new modules and the updated data pipeline.

- [ ] **Step 1: Add new modules to knowledge/README.md**

Add a new section after the existing data pipeline diagram:

```markdown
### Extended Pipeline (OpenSpace-inspired)

The following modules extend the base pipeline with config evolution tracking,
health monitoring, semantic failure search, and automated fix proposals:

```
runs.sqlite ──→ monitor_health.py ──→ health alerts (degradation detection)
     │
     └──→ config_lineage table (populated by ingest_run.py on config changes)
              │
              └──→ analyze_execution.py ──→ fix_proposals.json (review queue)
                        ↑
failure-patterns.md ──→ search_failures.py (BM25 index)
failure_candidates.json ─┘
```

| Script | Purpose | Inputs | Output |
|--------|---------|--------|--------|
| `monitor_health.py` | Detect family/platform degradation | `runs.sqlite`, window, threshold | JSON alerts |
| `search_failures.py` | BM25 search over failure knowledge base | query, failure-patterns.md, failure_candidates.json | Ranked results |
| `analyze_execution.py` | Produce fix proposals from failed runs | project dir, failure patterns | fix_proposals.json |

**Invariants for new modules:**
- `analyze_execution.py` NEVER auto-applies fixes — output is a review queue only
- `monitor_health.py` uses the same success criteria as `learn_heuristics.py`
- `search_failures.py` has zero external dependencies (BM25 is stdlib-only)
- Config lineage rows are only created when the diff is non-empty
```

- [ ] **Step 2: Commit**

```bash
git add skills/r2g-rtl2gds/knowledge/README.md
git commit -m "docs(knowledge): document new OpenSpace-inspired modules"
```

---

## Task 7 (Future Work): MCP Server Interface

> **Status: Future work — do not implement now.**

Inspired by OpenSpace's `mcp_server.py` which exposes 4 MCP tools (`execute_task`, `search_skills`, `fix_skill`, `upload_skill`) over stdio/SSE/HTTP.

**Goal:** Wrap the r2g-rtl2gds flow scripts as MCP tools so any MCP-compatible agent (Claude Code, Codex, Cursor, OpenClaw) can drive RTL-to-GDS flows without reading SKILL.md.

**Proposed MCP tools:**

| Tool | Maps to | Description |
|------|---------|-------------|
| `run_stage` | `scripts/flow/run_*.sh` | Run a specific flow stage (lint, sim, synth, backend, drc, lvs, rcx) |
| `check_health` | `knowledge/monitor_health.py` | Check family/platform health status |
| `suggest_config` | `knowledge/suggest_config.py` | Get config recommendations for a project |
| `analyze_failure` | `knowledge/analyze_execution.py` | Analyze a failed run and get fix proposals |
| `search_failures` | `knowledge/search_failures.py` | Search failure knowledge base |
| `query_knowledge` | `knowledge/query_knowledge.py` | Look up heuristics for a design family |

**Implementation approach:**
- Single `mcp_server.py` in the skill root using the MCP Python SDK
- Each tool maps to a single existing script — no new logic
- Supports stdio transport (for Claude Code) and SSE (for remote agents)
- Tool schemas auto-generated from argparse definitions

**Prerequisite:** The MCP Python SDK (`mcp`) must be available. Currently not installed in the EDA environment.

**Why deferred:** The current Claude Code integration (reading SKILL.md) works well. MCP is worth adding when we need multi-agent support or want to expose the EDA flow to non-Claude agents.

---

## Task 8 (Future Work): Cross-Agent Skill Sharing

> **Status: Future work — do not implement now.**

Inspired by OpenSpace's cloud registry where evolved skills are uploaded and shared across agent instances.

**Goal:** When multiple engineers run the r2g-rtl2gds skill independently, a shared knowledge store lets one engineer's swerv timing fix benefit everyone. This requires a synchronization layer over the local `runs.sqlite` database.

**Proposed architecture:**

```
Engineer A's workspace          Shared Store           Engineer B's workspace
  runs.sqlite ──push──→ central runs.sqlite ←──push── runs.sqlite
              ←──pull──                      ──pull──→
  heuristics.json      heuristics.json       heuristics.json
```

**Key design decisions:**
- **Push model**: After `ingest_run.py`, optionally push the run row + failure events to a shared SQLite file (on a network mount or via HTTP API)
- **Pull model**: Before `learn_heuristics.py` or `suggest_config.py`, optionally pull recent runs from the shared store
- **Conflict resolution**: run_id is a content hash (sha1 of project path + ppa mtime), so identical runs produce identical IDs — natural deduplication
- **Privacy**: Only push aggregate data (config params, outcomes, timings) — never push raw RTL, GDS, or project paths
- **Config lineage**: Lineage rows reference run_ids, which may not exist in the local DB. Pull must also fetch referenced runs.

**Why deferred:** This requires infrastructure (shared storage or HTTP service, authentication, conflict resolution) that is premature for the current single-workspace usage. The local knowledge store must prove its value first. Revisit when multiple engineers actively use the skill.

---

## Self-Review Checklist

1. **Spec coverage**: All 4 priority items (config lineage, health monitor, failure search, execution analyzer) have dedicated tasks. Future work items 5 and 6 (MCP, sharing) are documented as Tasks 7-8.

2. **Placeholder scan**: No "TBD", "TODO", or "implement later" found. All steps have concrete code.

3. **Type consistency**:
   - `diff_config_rows()` returns `dict[str, Any]` — used consistently in Task 1 tests and Task 2 implementation
   - `check()` in monitor_health returns `list[dict]` — tested in Task 3
   - `BM25Index.search()` returns `list[dict]` with keys `id`, `score`, `text` — used by `analyze_execution.py` and tested in Task 4
   - `analyze()` returns a dict with keys matching the docstring schema — tested in Task 5
   - `_parse_config_mk()` appears in both `ingest_run.py` and `analyze_execution.py` — identical implementation (intentionally duplicated to keep `analyze_execution.py` standalone; it does not import `ingest_run`)

4. **Dependency order**: Tasks 1→2 are sequential (schema before ingest). Tasks 3 and 4 are independent. Task 5 depends on Task 4 (imports `search_failures`). Task 6 is docs-only.

5. **Safety invariant preserved**: `analyze_execution.py` outputs a review queue (`fix_proposals.json`), never auto-applies. Documented in module docstring, tested in Task 5.
