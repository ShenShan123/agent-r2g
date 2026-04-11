# r2g-rtl2gds Knowledge Store (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a cross-run learning store to the `r2g-rtl2gds` skill so empirically-derived heuristics replace the hardcoded tables in `suggest_config.py`, and so repeatable failure signatures surface as a reviewable rule queue — adapted from the OpenSpace self-evolution pattern (episodic memory + rolling analysis window) but without the SQLite version DAG (Phase 3, deferred).

**Architecture:** A new `knowledge/` directory inside the skill owns a SQLite database (`runs.sqlite`) populated by a new `ingest_run.py` script that reads the already-structured JSON artifacts produced at the end of each flow (`ppa.json`, `drc.json`, `lvs.json`, `rcx.json`, `timing_check.json`, `diagnosis.json`, `stage_log.jsonl`). Two derivation scripts run on top of the DB: `learn_heuristics.py` → `heuristics.json` (empirical per-family bounds consumed by `suggest_config.py`), and `mine_rules.py` → `failure_candidates.json` (review queue for new failure-pattern entries). No deterministic script is replaced; `suggest_config.py` gains a read of `heuristics.json` with transparent fallback to its current hardcoded tables.

**Tech Stack:** Python 3, stdlib `sqlite3` (no new deps), stdlib `statistics`, `pathlib`, `json`, `re`. Tests use `pytest` (already installed). All new code lives inside `skills/r2g-rtl2gds/`.

---

## Preamble: Repo context

- The repository is **not** a git repo (`git rev-parse` fails). "Commit" steps below are marked as **checkpoints** — log a one-line progress note and continue. If a future user initialises git, they can replay the checkpoints as real commits.
- pytest 8.3.5 is already on PATH.
- `eda-runs/` is empty on disk; tests rely entirely on self-contained fixtures. The seed `families.json` encodes the 7 validated families from `CLAUDE.md:227-240` so that `suggest_config.py` has useful output even before the first real run is ingested.
- All new Python files must be executable (`#!/usr/bin/env python3`) and use the same docstring / import style as `knowledge/suggest_config.py` and `scripts/extract/extract_ppa.py`.
- Never break the existing deterministic-script contract: new scripts read artifacts, they do not rewrite them. `suggest_config.py` still accepts the same CLI and still returns the same JSON shape — only the `recommendations` values change when learned data is available.

---

## File Structure

**New files (create):**

| Path | Responsibility |
|---|---|
| `skills/r2g-rtl2gds/knowledge/README.md` | One-page explainer of the knowledge store layout and the ingest → learn → consume loop. |
| `skills/r2g-rtl2gds/knowledge/schema.sql` | DDL for `runs` and `failure_events` tables + indexes. Single source of truth for the DB schema. |
| `skills/r2g-rtl2gds/knowledge/families.json` | Seed mapping `design_name → design_family` + regex patterns for unknown designs. Seeds the 7 validated families. |
| `skills/r2g-rtl2gds/knowledge/.gitkeep` | Keeps the directory present when `runs.sqlite`, `heuristics.json`, `failure_candidates.json` are absent (pre-first-run state). |
| `skills/r2g-rtl2gds/knowledge/knowledge_db.py` | Thin module: `connect(db_path)`, `ensure_schema(conn)`, `FAMILIES` loader, `infer_family(design_name)`. Shared by ingest / learn / query / mine. |
| `skills/r2g-rtl2gds/knowledge/ingest_run.py` | Reads one `eda-runs/<project>/` directory, upserts a row into `runs.sqlite`, writes any failure events. Idempotent. |
| `skills/r2g-rtl2gds/knowledge/learn_heuristics.py` | Rebuilds `knowledge/heuristics.json` from `runs.sqlite`. Pure derivation, no network. |
| `skills/r2g-rtl2gds/knowledge/query_knowledge.py` | Read-only CLI + importable API (`get_family_heuristics`). Consumed by `suggest_config.py` and the agent. |
| `skills/r2g-rtl2gds/knowledge/mine_rules.py` | Scans `failure_events` for repeated signatures and writes `knowledge/failure_candidates.json` as a review queue. |
| `skills/r2g-rtl2gds/tests/__init__.py` | Empty, marks `tests` as a package. |
| `skills/r2g-rtl2gds/tests/conftest.py` | Adds `scripts/` to `sys.path`; shared fixtures for a tmp skill root with a seeded `families.json`. |
| `skills/r2g-rtl2gds/tests/fixtures/sample_run_success/...` | Minimal `eda-runs/<project>` skeleton for a clean aes128_core pass. |
| `skills/r2g-rtl2gds/tests/fixtures/sample_run_fail_pdn/...` | Minimal `eda-runs/<project>` skeleton for an ORFS PDN-0179 floorplan failure. |
| `skills/r2g-rtl2gds/tests/test_knowledge_db.py` | Unit tests for schema bootstrap and family inference. |
| `skills/r2g-rtl2gds/tests/test_ingest_run.py` | Unit tests for ingestion of the two fixtures + idempotency. |
| `skills/r2g-rtl2gds/tests/test_learn_heuristics.py` | Unit tests for empirical derivation from a pre-populated DB. |
| `skills/r2g-rtl2gds/tests/test_query_knowledge.py` | Unit tests for the read API. |
| `skills/r2g-rtl2gds/tests/test_mine_rules.py` | Unit tests for failure-signature rollup. |
| `skills/r2g-rtl2gds/tests/test_suggest_config_integration.py` | Integration test: `suggest_config.py` prefers learned heuristics when `heuristics.json` covers the family. |

**Files to modify:**

| Path | Change |
|---|---|
| `skills/r2g-rtl2gds/knowledge/suggest_config.py` | Load `knowledge/heuristics.json` via `query_knowledge.get_family_heuristics`. When a match exists, override `CORE_UTILIZATION` / `PLACE_DENSITY_LB_ADDON` with learned values and push an `explanations` note citing the sample size. Otherwise behave exactly as today. |
| `skills/r2g-rtl2gds/SKILL.md` | Add a new numbered workflow step **"13. Ingest Run"** after Reports; document that `knowledge/ingest_run.py` must run after every successful *or* failed flow to feed the store. Add a short subsection under "Workflow" explaining `heuristics.json`. |
| `CLAUDE.md` (project-level) | Append the 4 new scripts to the Script Inventory table; add a "Knowledge Store" subsection under "Project Layout" describing `skills/r2g-rtl2gds/knowledge/`. |

---

## Task 1: Knowledge-store scaffolding (schema + families seed + DB module)

**Files:**
- Create: `skills/r2g-rtl2gds/knowledge/README.md`
- Create: `skills/r2g-rtl2gds/knowledge/schema.sql`
- Create: `skills/r2g-rtl2gds/knowledge/families.json`
- Create: `skills/r2g-rtl2gds/knowledge/.gitkeep`
- Create: `skills/r2g-rtl2gds/knowledge/knowledge_db.py`
- Create: `skills/r2g-rtl2gds/tests/__init__.py`
- Create: `skills/r2g-rtl2gds/tests/conftest.py`
- Create: `skills/r2g-rtl2gds/tests/test_knowledge_db.py`

- [ ] **Step 1.1: Write `skills/r2g-rtl2gds/knowledge/schema.sql`**

```sql
-- r2g-rtl2gds knowledge store schema. DO NOT edit at runtime —
-- all writes go through knowledge/knowledge_db.py::ensure_schema.

CREATE TABLE IF NOT EXISTS runs (
    run_id                  TEXT PRIMARY KEY,
    project_path            TEXT NOT NULL,
    design_name             TEXT,
    design_family           TEXT,
    platform                TEXT,
    ingested_at             TEXT NOT NULL,

    -- config inputs (parsed from constraints/config.mk)
    core_utilization        REAL,
    place_density_lb_addon  REAL,
    synth_hierarchical      INTEGER,
    abc_area                INTEGER,
    die_area                TEXT,
    clock_period_ns         REAL,
    extra_config_json       TEXT,

    -- outcomes (parsed from reports/*.json)
    orfs_status             TEXT,
    orfs_fail_stage         TEXT,
    wns_ns                  REAL,
    tns_ns                  REAL,
    timing_tier             TEXT,
    cell_count              INTEGER,
    area_um2                REAL,
    power_mw                REAL,
    drc_status              TEXT,
    drc_violations          INTEGER,
    lvs_status              TEXT,
    rcx_status              TEXT,

    -- timings
    total_elapsed_s         REAL,
    stage_times_json        TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_family_platform ON runs(design_family, platform);
CREATE INDEX IF NOT EXISTS idx_runs_design_platform ON runs(design_name, platform);

CREATE TABLE IF NOT EXISTS failure_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    stage       TEXT,
    signature   TEXT NOT NULL,
    detail      TEXT
);

CREATE INDEX IF NOT EXISTS idx_failure_signature ON failure_events(signature);
CREATE INDEX IF NOT EXISTS idx_failure_run ON failure_events(run_id);
```

- [ ] **Step 1.2: Write `skills/r2g-rtl2gds/knowledge/families.json`**

```json
{
  "mappings": {
    "aes128_core":   "aes_xcrypt",
    "ibex_core":     "ibex",
    "riscv_top":     "riscv32i",
    "RocketTile":    "tinyRocket",
    "vga_enh_top":   "vga_enh_top",
    "swerv_wrapper": "swerv",
    "black_parrot":  "bp_multi_top"
  },
  "patterns": [
    {"regex": "^aes",                       "family": "aes_xcrypt"},
    {"regex": "^ibex",                      "family": "ibex"},
    {"regex": "^riscv",                     "family": "riscv32i"},
    {"regex": "^rocket",                    "family": "tinyRocket"},
    {"regex": "^swerv",                     "family": "swerv"},
    {"regex": "^(bp_|black_parrot)",        "family": "bp_multi_top"},
    {"regex": "^vga",                       "family": "vga_enh_top"}
  ]
}
```

- [ ] **Step 1.3: Write `skills/r2g-rtl2gds/knowledge/README.md`**

```markdown
# r2g-rtl2gds Knowledge Store

This directory is the skill's cross-run memory. It is **not** a cache — it is
the input to `suggest_config.py` and `failure-patterns.md` review.

## Layout

| File | Producer | Consumer |
|---|---|---|
| `schema.sql` | hand-edited | `knowledge/knowledge_db.py` at `ensure_schema` time |
| `families.json` | hand-edited seed; append as new designs ship | `knowledge/knowledge_db.py::infer_family` |
| `runs.sqlite` | `knowledge/ingest_run.py` (one row per ingested run) | `learn_heuristics.py`, `mine_rules.py`, `query_knowledge.py` |
| `heuristics.json` | `knowledge/learn_heuristics.py` | `suggest_config.py`, agent, dashboard |
| `failure_candidates.json` | `knowledge/mine_rules.py` | human reviewer → `references/failure-patterns.md` |

## Loop

```
              (run the flow)
                   │
                   ▼
     reports/*.json, stage_log.jsonl, diagnosis.json
                   │
      ingest_run.py │
                   ▼
              runs.sqlite ──► learn_heuristics.py ──► heuristics.json
                       │                                     │
                       │                                     └──► suggest_config.py
                       │
                       └─────► mine_rules.py ──► failure_candidates.json
                                                    │
                                                    └──► (human review) ──► failure-patterns.md
```

## Invariants

1. `ingest_run.py` only reads structured JSON artifacts; it never parses raw ORFS logs. If an artifact is missing, the corresponding column is NULL.
2. `heuristics.json` is **advisory**. `suggest_config.py` falls back to its hardcoded tables when no learned data is available for a family/platform.
3. `failure_candidates.json` is never auto-merged into `failure-patterns.md` — it is a human review queue.
4. The SQLite DB is append-only semantically: `run_id = sha1(project_path + ":" + ppa_json_mtime)`, so re-ingesting the same completed run is a no-op, while a new run iteration produces a new row.
```

- [ ] **Step 1.4: Write `skills/r2g-rtl2gds/knowledge/.gitkeep`** (empty file)

- [ ] **Step 1.5: Write `skills/r2g-rtl2gds/tests/__init__.py`** (empty file)

- [ ] **Step 1.6: Write `skills/r2g-rtl2gds/tests/conftest.py`**

```python
"""Shared pytest fixtures for r2g-rtl2gds knowledge-store tests."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

# Make scripts/ importable as plain modules.
SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def tmp_knowledge_dir(tmp_path: Path) -> Path:
    """A throw-away knowledge/ directory with the real schema + families seed."""
    kdir = tmp_path / "knowledge"
    kdir.mkdir()
    shutil.copy(SKILL_ROOT / "knowledge" / "schema.sql", kdir / "schema.sql")
    shutil.copy(SKILL_ROOT / "knowledge" / "families.json", kdir / "families.json")
    return kdir


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures"
```

- [ ] **Step 1.7: Write the failing test `skills/r2g-rtl2gds/tests/test_knowledge_db.py`**

```python
"""Tests for knowledge_db module: schema bootstrap and family inference."""
from __future__ import annotations

import sqlite3

import knowledge_db


def test_ensure_schema_creates_tables(tmp_knowledge_dir):
    db_path = tmp_knowledge_dir / "runs.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    assert {"runs", "failure_events"}.issubset(names)
    conn.close()


def test_ensure_schema_is_idempotent(tmp_knowledge_dir):
    db_path = tmp_knowledge_dir / "runs.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    conn.close()


def test_infer_family_direct_mapping(tmp_knowledge_dir):
    families = knowledge_db.load_families(tmp_knowledge_dir / "families.json")
    assert knowledge_db.infer_family("aes128_core", families) == "aes_xcrypt"
    assert knowledge_db.infer_family("RocketTile", families) == "tinyRocket"


def test_infer_family_pattern_fallback(tmp_knowledge_dir):
    families = knowledge_db.load_families(tmp_knowledge_dir / "families.json")
    # Not in mappings, but matches a pattern
    assert knowledge_db.infer_family("aes_new_variant", families) == "aes_xcrypt"
    assert knowledge_db.infer_family("bp_something", families) == "bp_multi_top"


def test_infer_family_unknown_returns_first_token(tmp_knowledge_dir):
    families = knowledge_db.load_families(tmp_knowledge_dir / "families.json")
    assert knowledge_db.infer_family("foobar_top", families) == "foobar"
```

- [ ] **Step 1.8: Run the failing tests**

```bash
cd /data1/shenshan/agent_with_openroad && \
  python3 -m pytest skills/r2g-rtl2gds/tests/test_knowledge_db.py -v
```

Expected: **all 5 tests FAIL** with `ModuleNotFoundError: No module named 'knowledge_db'`.

- [ ] **Step 1.9: Implement `skills/r2g-rtl2gds/knowledge/knowledge_db.py`**

```python
#!/usr/bin/env python3
"""Shared SQLite + family-inference helpers for the knowledge store.

Imported by ingest_run.py, learn_heuristics.py, query_knowledge.py,
and mine_rules.py. No CLI.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KNOWLEDGE_DIR = SKILL_ROOT / "knowledge"
DEFAULT_DB_PATH = DEFAULT_KNOWLEDGE_DIR / "runs.sqlite"
DEFAULT_SCHEMA_PATH = DEFAULT_KNOWLEDGE_DIR / "schema.sql"
DEFAULT_FAMILIES_PATH = DEFAULT_KNOWLEDGE_DIR / "families.json"


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_schema(conn: sqlite3.Connection,
                  schema_path: Path | str = DEFAULT_SCHEMA_PATH) -> None:
    ddl = Path(schema_path).read_text(encoding="utf-8")
    conn.executescript(ddl)
    conn.commit()


def load_families(families_path: Path | str = DEFAULT_FAMILIES_PATH) -> dict[str, Any]:
    data = json.loads(Path(families_path).read_text(encoding="utf-8"))
    if "mappings" not in data:
        data["mappings"] = {}
    if "patterns" not in data:
        data["patterns"] = []
    return data


def infer_family(design_name: str, families: dict[str, Any]) -> str:
    if not design_name:
        return "unknown"
    mappings: dict[str, str] = families.get("mappings", {})
    if design_name in mappings:
        return mappings[design_name]
    for entry in families.get("patterns", []):
        if re.search(entry["regex"], design_name, re.IGNORECASE):
            return entry["family"]
    return design_name.split("_", 1)[0].lower()
```

- [ ] **Step 1.10: Run the tests to verify they pass**

```bash
cd /data1/shenshan/agent_with_openroad && \
  python3 -m pytest skills/r2g-rtl2gds/tests/test_knowledge_db.py -v
```

Expected: **all 5 tests PASS**.

- [ ] **Step 1.11: Checkpoint**

Log: `Task 1 complete: knowledge-store scaffolding in place.`
(Skip `git commit` — not a git repo.)

---

## Task 2: `ingest_run.py` — read structured artifacts into `runs.sqlite`

**Files:**
- Create: `skills/r2g-rtl2gds/knowledge/ingest_run.py`
- Create: `skills/r2g-rtl2gds/tests/fixtures/sample_run_success/` (tree below)
- Create: `skills/r2g-rtl2gds/tests/fixtures/sample_run_fail_pdn/` (tree below)
- Create: `skills/r2g-rtl2gds/tests/test_ingest_run.py`

- [ ] **Step 2.1: Build the success fixture**

Create these files under `skills/r2g-rtl2gds/tests/fixtures/sample_run_success/`:

`constraints/config.mk`:
```makefile
export DESIGN_NAME = aes128_core
export PLATFORM    = nangate45
export VERILOG_FILES = /tmp/fake/aes128_core.v
export SDC_FILE      = /tmp/fake/constraint.sdc
export CORE_UTILIZATION = 25
export PLACE_DENSITY_LB_ADDON = 0.20
export ABC_AREA = 1
```

`constraints/constraint.sdc`:
```tcl
create_clock -name core_clock -period 4.0 [get_ports clk]
```

`reports/ppa.json` (must match the nested shape that `scripts/extract/extract_ppa.py` actually emits — see `extract_ppa.py:152-249`):
```json
{
  "summary": {
    "area":   {"design_area_um2": 28500.0, "utilization": 0.25, "total_cell_area": 28500.0},
    "timing": {"setup_wns": -0.05, "setup_tns": -0.12, "hold_wns": 0.02, "hold_tns": 0.0},
    "power":  {"total_power_w": 0.0143, "internal_power_w": 0.009,
               "switching_power_w": 0.004, "leakage_power_w": 0.0013},
    "drc":    {}
  },
  "geometry": {
    "die_area_um2": 48400.0,
    "core_area_um2": 46000.0,
    "utilization": 0.27,
    "instance_count": 12412,
    "stdcell_count": 12412,
    "macro_count": 0
  },
  "run_dir": "backend/RUN_stub"
}
```

`reports/timing_check.json` (from `check_timing.py:317` — `tier` is top-level):
```json
{"tier": "minor", "wns_tier": "minor", "tns_tier": "clean",
 "wns": -0.05, "tns": -0.12, "suggested_clock_period": 4.1}
```

`reports/drc.json` (from `extract_drc.py:110-115` — status values are `clean|fail|unknown`, count key is `total_violations`):
```json
{"status": "clean", "total_violations": 0, "categories": {}, "log_info": {}}
```

`reports/lvs.json` (from `extract_lvs.py:131-149` — status values are `clean|fail|skipped|unknown`):
```json
{"status": "clean", "mismatch_count": 0, "lvsdb": {}, "log_info": {"log_status": "match"}}
```

`reports/rcx.json` (from `extract_rcx.py:177-188` — status values are `complete|empty|no_spef|skipped`; capacitance key is `total_cap_ff`):
```json
{"status": "complete", "net_count": 10234, "total_cap_ff": 12345.6, "total_res_ohm": 987.6,
 "cap_unit": "1.0 FF", "res_unit": "1.0 OHM"}
```

`reports/diagnosis.json`:
```json
{"issues": []}
```

`backend/stage_log.jsonl`:
```
{"stage": "synth",     "status": "pass", "elapsed_s": 35.2}
{"stage": "floorplan", "status": "pass", "elapsed_s": 18.4}
{"stage": "place",     "status": "pass", "elapsed_s": 210.1}
{"stage": "cts",       "status": "pass", "elapsed_s": 64.0}
{"stage": "route",     "status": "pass", "elapsed_s": 420.9}
{"stage": "finish",    "status": "pass", "elapsed_s": 55.3}
```

- [ ] **Step 2.2: Build the failure fixture**

Create these files under `skills/r2g-rtl2gds/tests/fixtures/sample_run_fail_pdn/`:

`constraints/config.mk`:
```makefile
export DESIGN_NAME = black_parrot
export PLATFORM    = nangate45
export VERILOG_FILES = /tmp/fake/bp.v
export SDC_FILE      = /tmp/fake/constraint.sdc
export CORE_UTILIZATION = 40
export PLACE_DENSITY_LB_ADDON = 0.20
export SYNTH_HIERARCHICAL = 1
export ABC_AREA = 1
export DIE_AREA = 0 0 1800 1800
```

`reports/ppa.json` (partial — synth ran, geometry populated, no timing/power yet because floorplan failed):
```json
{
  "summary": {"area": {}, "timing": {}, "power": {}, "drc": {}},
  "geometry": {"instance_count": 198432}
}
```

`reports/diagnosis.json`:
```json
{
  "issues": [
    {
      "kind": "pdn-0179",
      "stage": "floorplan",
      "summary": "PDN-0179: Unable to repair all channels.",
      "suggestion": "Increase DIE_AREA, reduce PLACE_DENSITY_LB_ADDON, or remove SYNTH_HIERARCHICAL+ABC_AREA combination."
    }
  ]
}
```

`backend/stage_log.jsonl`:
```
{"stage": "synth",     "status": "pass", "elapsed_s": 1180.0}
{"stage": "floorplan", "status": "fail", "elapsed_s": 95.4}
```

(Intentionally no drc/lvs/rcx/timing_check/ppa-full artifacts — the run failed at floorplan.)

- [ ] **Step 2.3: Write the failing test `skills/r2g-rtl2gds/tests/test_ingest_run.py`**

```python
"""Tests for ingest_run.py: read artifacts → SQLite row."""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import ingest_run
import knowledge_db


def _stage(fixtures_dir: Path, name: str, tmp_path: Path) -> Path:
    """Copy a fixture project into tmp_path so mtimes are fresh."""
    dst = tmp_path / name
    shutil.copytree(fixtures_dir / name, dst)
    return dst


def _open_db(tmp_knowledge_dir: Path) -> sqlite3.Connection:
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    return conn


def test_ingest_success_run_writes_row(fixtures_dir, tmp_knowledge_dir, tmp_path):
    project = _stage(fixtures_dir, "sample_run_success", tmp_path)
    conn = _open_db(tmp_knowledge_dir)

    run_id = ingest_run.ingest(project, conn,
                               families_path=tmp_knowledge_dir / "families.json")
    assert run_id

    row = conn.execute(
        "SELECT design_name, design_family, platform, orfs_status, "
        "core_utilization, place_density_lb_addon, cell_count, "
        "wns_ns, timing_tier, drc_status, lvs_status, rcx_status, "
        "total_elapsed_s "
        "FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    assert row is not None
    (design_name, design_family, platform, orfs_status, core_util, pdens,
     cell_count, wns, tier, drc, lvs, rcx, elapsed) = row
    assert design_name == "aes128_core"
    assert design_family == "aes_xcrypt"
    assert platform == "nangate45"
    assert orfs_status == "pass"
    assert core_util == 25.0
    assert abs(pdens - 0.20) < 1e-9
    assert cell_count == 12412
    assert abs(wns - (-0.05)) < 1e-9
    assert tier == "minor"
    # Status values come straight from extract_{drc,lvs,rcx}.py, which use
    # 'clean' for DRC/LVS success and 'complete' for RCX success.
    assert drc == "clean"
    assert lvs == "clean"
    assert rcx == "complete"
    assert elapsed and elapsed > 800.0  # sum of stage times
    conn.close()


def test_ingest_failure_run_writes_row_and_failure_event(
    fixtures_dir, tmp_knowledge_dir, tmp_path,
):
    project = _stage(fixtures_dir, "sample_run_fail_pdn", tmp_path)
    conn = _open_db(tmp_knowledge_dir)

    run_id = ingest_run.ingest(project, conn,
                               families_path=tmp_knowledge_dir / "families.json")

    row = conn.execute(
        "SELECT orfs_status, orfs_fail_stage, design_family, cell_count, "
        "drc_status, lvs_status, rcx_status "
        "FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    orfs_status, fail_stage, fam, cell_count, drc, lvs, rcx = row
    assert orfs_status == "fail"
    assert fail_stage == "floorplan"
    assert fam == "bp_multi_top"
    assert cell_count == 198432
    # Signoff stages never ran
    assert drc in (None, "skipped")
    assert lvs in (None, "skipped")
    assert rcx in (None, "skipped")

    events = conn.execute(
        "SELECT stage, signature FROM failure_events WHERE run_id = ? ORDER BY signature",
        (run_id,),
    ).fetchall()
    assert ("floorplan", "pdn-0179") in events
    conn.close()


def test_ingest_is_idempotent(fixtures_dir, tmp_knowledge_dir, tmp_path):
    project = _stage(fixtures_dir, "sample_run_success", tmp_path)
    conn = _open_db(tmp_knowledge_dir)
    id1 = ingest_run.ingest(project, conn,
                            families_path=tmp_knowledge_dir / "families.json")
    id2 = ingest_run.ingest(project, conn,
                            families_path=tmp_knowledge_dir / "families.json")
    assert id1 == id2
    (count,) = conn.execute("SELECT COUNT(*) FROM runs").fetchone()
    assert count == 1
    conn.close()
```

- [ ] **Step 2.4: Run tests to verify they fail**

```bash
cd /data1/shenshan/agent_with_openroad && \
  python3 -m pytest skills/r2g-rtl2gds/tests/test_ingest_run.py -v
```

Expected: **FAIL** with `ModuleNotFoundError: No module named 'ingest_run'`.

- [ ] **Step 2.5: Implement `skills/r2g-rtl2gds/knowledge/ingest_run.py`**

```python
#!/usr/bin/env python3
"""Ingest one eda-runs/<project> directory into knowledge/runs.sqlite.

Usage:
  ingest_run.py <project-dir>
  ingest_run.py <project-dir> --db <path>

Reads the structured JSON artifacts the flow already produces:
  constraints/config.mk
  reports/ppa.json
  reports/timing_check.json
  reports/drc.json
  reports/lvs.json
  reports/rcx.json
  reports/diagnosis.json
  backend/stage_log.jsonl

Nothing here parses raw ORFS logs — if an artifact is missing, the
corresponding column is left NULL. Idempotent: re-ingesting the same
completed run produces the same run_id.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

import knowledge_db


_CONFIG_LINE_RE = re.compile(r"(?:export\s+)?(\w+)\s*=\s*(.*)")


def _parse_config_mk(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="ignore").replace("\\\n", " ")
    fields: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _CONFIG_LINE_RE.match(line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_stage_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _derive_orfs_status(stages: list[dict[str, Any]]) -> tuple[str, str | None]:
    if not stages:
        return ("unknown", None)
    saw_fail = False
    fail_stage = None
    last_stage_name = None
    stage_names_done = {s.get("stage") for s in stages if s.get("status") == "pass"}
    for s in stages:
        if s.get("status") not in ("pass", "fail"):
            continue
        last_stage_name = s.get("stage")
        if s.get("status") == "fail" and not saw_fail:
            saw_fail = True
            fail_stage = s.get("stage")
    if saw_fail:
        return ("fail", fail_stage)
    required = ["synth", "floorplan", "place", "cts", "route", "finish"]
    if all(name in stage_names_done for name in required):
        return ("pass", None)
    return ("partial", last_stage_name)


def _compute_run_id(project: Path, ppa_path: Path) -> str:
    marker = str(ppa_path.stat().st_mtime_ns) if ppa_path.exists() else ""
    h = hashlib.sha1()
    h.update(str(project.resolve()).encode("utf-8"))
    h.update(b":")
    h.update(marker.encode("utf-8"))
    return h.hexdigest()


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> int | None:
    f = _to_float(v)
    return int(f) if f is not None else None


def _coerce_bool_int(s: str | None) -> int | None:
    if s is None:
        return None
    s = s.strip()
    if s in ("1", "true", "TRUE", "True", "yes"):
        return 1
    if s in ("0", "false", "FALSE", "False", "no", ""):
        return 0
    return None


def ingest(project: Path,
           conn: sqlite3.Connection,
           families_path: Path | None = None) -> str:
    project = Path(project)
    if not project.is_dir():
        raise FileNotFoundError(f"Project directory not found: {project}")

    families_path = Path(families_path) if families_path else knowledge_db.DEFAULT_FAMILIES_PATH
    families = knowledge_db.load_families(families_path)

    cfg = _parse_config_mk(project / "constraints" / "config.mk")
    design_name = cfg.get("DESIGN_NAME", "unknown")
    design_family = knowledge_db.infer_family(design_name, families)
    platform = cfg.get("PLATFORM", "nangate45")

    ppa = _read_json(project / "reports" / "ppa.json") or {}
    summary = ppa.get("summary", {}) if isinstance(ppa, dict) else {}
    timing = summary.get("timing", {}) if isinstance(summary, dict) else {}
    power = summary.get("power", {}) if isinstance(summary, dict) else {}
    area = summary.get("area", {}) if isinstance(summary, dict) else {}
    geometry = ppa.get("geometry", {}) if isinstance(ppa, dict) else {}

    drc = _read_json(project / "reports" / "drc.json") or {}
    lvs = _read_json(project / "reports" / "lvs.json") or {}
    rcx = _read_json(project / "reports" / "rcx.json") or {}
    tcheck = _read_json(project / "reports" / "timing_check.json") or {}
    diag = _read_json(project / "reports" / "diagnosis.json") or {}
    stage_log = _read_stage_log(project / "backend" / "stage_log.jsonl")

    orfs_status, fail_stage = _derive_orfs_status(stage_log)
    total_elapsed = sum(_to_float(s.get("elapsed_s")) or 0.0 for s in stage_log) or None

    # Cell count: prefer geometry.instance_count (authoritative, from 6_report.json),
    # fall back to geometry.stdcell_count when instance_count is absent in partial runs.
    cell_count = _to_int(geometry.get("instance_count"))
    if cell_count is None:
        cell_count = _to_int(geometry.get("stdcell_count"))

    # Area: geometry.die_area_um2 is authoritative; area.design_area_um2 is a
    # placer-stage estimate used as fallback.
    area_um2 = _to_float(geometry.get("die_area_um2"))
    if area_um2 is None:
        area_um2 = _to_float(area.get("design_area_um2"))

    # Power: extract_ppa.py stores total_power_w in Watts; convert to mW.
    total_power_w = _to_float(power.get("total_power_w"))
    power_mw = total_power_w * 1000.0 if total_power_w is not None else None

    ppa_path = project / "reports" / "ppa.json"
    run_id = _compute_run_id(project, ppa_path)

    row = {
        "run_id":            run_id,
        "project_path":      str(project.resolve()),
        "design_name":       design_name,
        "design_family":     design_family,
        "platform":          platform,
        "ingested_at":       _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",

        "core_utilization":       _to_float(cfg.get("CORE_UTILIZATION")),
        "place_density_lb_addon": _to_float(cfg.get("PLACE_DENSITY_LB_ADDON")),
        "synth_hierarchical":     _coerce_bool_int(cfg.get("SYNTH_HIERARCHICAL")),
        "abc_area":               _coerce_bool_int(cfg.get("ABC_AREA")),
        "die_area":               cfg.get("DIE_AREA"),
        "clock_period_ns":        _to_float(cfg.get("CLOCK_PERIOD")),
        "extra_config_json":      json.dumps({
            k: v for k, v in cfg.items()
            if k not in {
                "DESIGN_NAME", "PLATFORM", "CORE_UTILIZATION",
                "PLACE_DENSITY_LB_ADDON", "SYNTH_HIERARCHICAL", "ABC_AREA",
                "DIE_AREA", "CLOCK_PERIOD",
            }
        }, sort_keys=True),

        "orfs_status":     orfs_status,
        "orfs_fail_stage": fail_stage,
        "wns_ns":          _to_float(timing.get("setup_wns")),
        "tns_ns":          _to_float(timing.get("setup_tns")),
        "timing_tier":     tcheck.get("tier"),
        "cell_count":      cell_count,
        "area_um2":        area_um2,
        "power_mw":        power_mw,
        "drc_status":      drc.get("status"),          # clean | fail | unknown
        "drc_violations":  _to_int(drc.get("total_violations")),
        "lvs_status":      lvs.get("status"),          # clean | fail | skipped | unknown
        "rcx_status":      rcx.get("status"),          # complete | empty | no_spef | skipped

        "total_elapsed_s":  total_elapsed,
        "stage_times_json": json.dumps(stage_log, sort_keys=True),
    }

    columns = list(row.keys())
    placeholders = ", ".join(f":{c}" for c in columns)
    conn.execute(
        f"INSERT OR REPLACE INTO runs ({', '.join(columns)}) VALUES ({placeholders})",
        row,
    )

    # Rebuild failure events for this run (idempotent).
    conn.execute("DELETE FROM failure_events WHERE run_id = ?", (run_id,))
    for issue in (diag.get("issues") or []):
        sig = (issue.get("kind") or "").strip()
        if not sig:
            continue
        conn.execute(
            "INSERT INTO failure_events (run_id, stage, signature, detail) "
            "VALUES (?, ?, ?, ?)",
            (run_id, issue.get("stage"), sig, issue.get("summary")),
        )
    if orfs_status == "fail" and fail_stage:
        conn.execute(
            "INSERT INTO failure_events (run_id, stage, signature, detail) "
            "VALUES (?, ?, ?, ?)",
            (run_id, fail_stage, f"orfs-fail-{fail_stage}", None),
        )
    conn.commit()
    return run_id


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("project", type=Path, help="Path to eda-runs/<project> directory")
    p.add_argument("--db", type=Path, default=knowledge_db.DEFAULT_DB_PATH,
                   help="SQLite database path (default: knowledge/runs.sqlite)")
    p.add_argument("--schema", type=Path, default=knowledge_db.DEFAULT_SCHEMA_PATH,
                   help="Schema SQL path")
    p.add_argument("--families", type=Path, default=knowledge_db.DEFAULT_FAMILIES_PATH,
                   help="families.json path")
    args = p.parse_args()

    conn = knowledge_db.connect(args.db)
    knowledge_db.ensure_schema(conn, schema_path=args.schema)
    run_id = ingest(args.project, conn, families_path=args.families)
    # Warn loudly if the run is about to be classified 'unknown' because
    # stage_log.jsonl is missing — this silently excludes runs from learning.
    status_row = conn.execute(
        "SELECT orfs_status FROM runs WHERE run_id = ?", (run_id,),
    ).fetchone()
    if status_row and status_row[0] == "unknown":
        print(
            f"WARNING: no backend/stage_log.jsonl under {args.project}; "
            "orfs_status='unknown'. This run will NOT contribute to "
            "learn_heuristics.py. Re-run via run_orfs.sh to emit stage_log.jsonl.",
            file=sys.stderr,
        )
    conn.close()
    print(f"Ingested run_id={run_id} from {args.project}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2.6: Run tests to verify they pass**

```bash
cd /data1/shenshan/agent_with_openroad && \
  python3 -m pytest skills/r2g-rtl2gds/tests/test_ingest_run.py -v
```

Expected: **all 3 tests PASS**.

- [ ] **Step 2.7: Checkpoint**

Log: `Task 2 complete: ingest_run.py + success/fail fixtures.`

---

## Task 3: `learn_heuristics.py` — derive `heuristics.json` from runs.sqlite

**Files:**
- Create: `skills/r2g-rtl2gds/knowledge/learn_heuristics.py`
- Create: `skills/r2g-rtl2gds/tests/test_learn_heuristics.py`

- [ ] **Step 3.1: Write the failing test**

```python
"""Tests for learn_heuristics.py."""
from __future__ import annotations

import json

import knowledge_db
import learn_heuristics


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
    defaults["ingested_at"] = "2026-04-11T00:00:00Z"
    defaults["project_path"] = defaults["project_path"] or f"/tmp/{defaults['run_id']}"
    cols = ", ".join(defaults.keys())
    ph = ", ".join(f":{k}" for k in defaults.keys())
    conn.execute(f"INSERT INTO runs ({cols}) VALUES ({ph})", defaults)


def _seed_aes_family(conn, good: int, bad: int):
    for i in range(good):
        _insert(conn, run_id=f"aes_good_{i}", design_name="aes128_core",
                design_family="aes_xcrypt", platform="nangate45",
                core_utilization=20.0 + i, place_density_lb_addon=0.20,
                cell_count=12000 + i * 100,
                orfs_status="pass",
                # Match the real values emitted by extract_{drc,lvs,rcx}.py
                drc_status="clean", lvs_status="clean", rcx_status="complete",
                total_elapsed_s=2000 + i * 10)
    for i in range(bad):
        _insert(conn, run_id=f"aes_bad_{i}", design_name="aes128_core",
                design_family="aes_xcrypt", platform="nangate45",
                core_utilization=45.0, place_density_lb_addon=0.05,
                cell_count=12500,
                orfs_status="fail", orfs_fail_stage="place",
                total_elapsed_s=900)


def test_learn_produces_family_heuristics(tmp_knowledge_dir):
    db_path = tmp_knowledge_dir / "runs.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    _seed_aes_family(conn, good=5, bad=2)
    conn.commit()
    conn.close()

    out = tmp_knowledge_dir / "heuristics.json"
    learn_heuristics.learn(db_path, out)

    data = json.loads(out.read_text())
    assert data["source_run_count"] == 7
    fam = data["families"]["aes_xcrypt"]["platforms"]["nangate45"]
    # Only successful runs inform min/max/median bounds
    assert fam["success_count"] == 5
    assert fam["core_utilization"]["min_safe"] == 20.0
    assert fam["core_utilization"]["max_safe"] == 24.0
    assert fam["core_utilization"]["median"] == 22.0
    assert abs(fam["place_density_lb_addon"]["min_safe"] - 0.20) < 1e-9
    assert fam["success_rate"] == 5 / 7


def test_learn_skips_families_with_too_few_samples(tmp_knowledge_dir):
    db_path = tmp_knowledge_dir / "runs.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    _insert(conn, run_id="lonely_0", design_name="foobar",
            design_family="foobar", platform="nangate45",
            core_utilization=30, place_density_lb_addon=0.20,
            orfs_status="pass")
    conn.commit()
    conn.close()

    out = tmp_knowledge_dir / "heuristics.json"
    learn_heuristics.learn(db_path, out)
    data = json.loads(out.read_text())
    assert "foobar" not in data["families"]
```

- [ ] **Step 3.2: Run tests to verify they fail**

```bash
cd /data1/shenshan/agent_with_openroad && \
  python3 -m pytest skills/r2g-rtl2gds/tests/test_learn_heuristics.py -v
```

Expected: **FAIL** with `ModuleNotFoundError: No module named 'learn_heuristics'`.

- [ ] **Step 3.3: Implement `skills/r2g-rtl2gds/knowledge/learn_heuristics.py`**

```python
#!/usr/bin/env python3
"""Derive empirical per-family heuristics from runs.sqlite.

Usage:
  learn_heuristics.py [--db <path>] [--out <path>]

Writes knowledge/heuristics.json. Pure derivation — no network, no execution.
A family/platform pair is included only when at least MIN_SUCCESSFUL
successful runs exist. Failed-run config values never inform min_safe /
max_safe / median bounds.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import statistics
import sys
from pathlib import Path

import knowledge_db

MIN_SUCCESSFUL = 3

# Real status values written by the extract_{drc,lvs,rcx}.py scripts.
# Do not accept "pass" here — no extractor ever emits it, and accepting
# a phantom value would silently mask schema drift.
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


def _fetch_rows(conn) -> list[dict]:
    cur = conn.execute("SELECT * FROM runs")
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _p90(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = max(0, int(round(0.9 * (len(s) - 1))))
    return s[idx]


def _family_platform_entry(runs: list[dict]) -> dict | None:
    successes = [r for r in runs if _is_success(r)]
    if len(successes) < MIN_SUCCESSFUL:
        return None

    cu_vals = [r["core_utilization"] for r in successes
               if r.get("core_utilization") is not None]
    pd_vals = [r["place_density_lb_addon"] for r in successes
               if r.get("place_density_lb_addon") is not None]
    cell_vals = [r["cell_count"] for r in runs if r.get("cell_count") is not None]
    elapsed_vals = [r["total_elapsed_s"] for r in runs
                    if r.get("total_elapsed_s") is not None]

    entry: dict = {
        "sample_size": len(runs),
        "success_count": len(successes),
        "success_rate": len(successes) / len(runs),
    }
    if cu_vals:
        entry["core_utilization"] = {
            "min_safe": min(cu_vals),
            "max_safe": max(cu_vals),
            "median": statistics.median(cu_vals),
        }
    if pd_vals:
        entry["place_density_lb_addon"] = {
            "min_safe": min(pd_vals),
            "max_safe": max(pd_vals),
            "median": statistics.median(pd_vals),
        }
    if cell_vals:
        entry["typical_cell_count"] = int(statistics.median(cell_vals))
    if elapsed_vals:
        entry["p90_elapsed_s"] = _p90(elapsed_vals)
    return entry


def learn(db_path: Path | str,
          out_path: Path | str) -> dict:
    db_path = Path(db_path)
    out_path = Path(out_path)

    conn = knowledge_db.connect(db_path)
    rows = _fetch_rows(conn)
    conn.close()

    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        fam = r.get("design_family") or "unknown"
        plat = r.get("platform") or "unknown"
        groups.setdefault((fam, plat), []).append(r)

    families: dict[str, dict] = {}
    for (fam, plat), group_rows in groups.items():
        entry = _family_platform_entry(group_rows)
        if entry is None:
            continue
        fam_obj = families.setdefault(fam, {"platforms": {}})
        fam_obj["platforms"][plat] = entry

    data = {
        "generated_at": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "source_run_count": len(rows),
        "min_successful_runs_required": MIN_SUCCESSFUL,
        "families": families,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", type=Path, default=knowledge_db.DEFAULT_DB_PATH)
    p.add_argument("--out", type=Path,
                   default=knowledge_db.DEFAULT_KNOWLEDGE_DIR / "heuristics.json")
    args = p.parse_args()
    data = learn(args.db, args.out)
    total = sum(len(f["platforms"]) for f in data["families"].values())
    print(f"Wrote {args.out} ({len(data['families'])} families, "
          f"{total} family/platform entries, {data['source_run_count']} runs).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3.4: Run tests to verify they pass**

```bash
cd /data1/shenshan/agent_with_openroad && \
  python3 -m pytest skills/r2g-rtl2gds/tests/test_learn_heuristics.py -v
```

Expected: **both tests PASS**.

- [ ] **Step 3.5: Checkpoint**

Log: `Task 3 complete: learn_heuristics.py derives family/platform bounds.`

---

## Task 4: `query_knowledge.py` — read API consumed by other scripts

**Files:**
- Create: `skills/r2g-rtl2gds/knowledge/query_knowledge.py`
- Create: `skills/r2g-rtl2gds/tests/test_query_knowledge.py`

- [ ] **Step 4.1: Write the failing test**

```python
"""Tests for query_knowledge.py."""
from __future__ import annotations

import json

import query_knowledge


def _write(tmp_knowledge_dir, payload: dict):
    (tmp_knowledge_dir / "heuristics.json").write_text(json.dumps(payload))


def test_get_family_heuristics_hit(tmp_knowledge_dir):
    _write(tmp_knowledge_dir, {
        "families": {
            "aes_xcrypt": {
                "platforms": {
                    "nangate45": {
                        "sample_size": 10, "success_count": 10,
                        "success_rate": 1.0,
                        "core_utilization": {"min_safe": 20, "max_safe": 30, "median": 25},
                        "place_density_lb_addon": {"min_safe": 0.15, "median": 0.20},
                    },
                },
            },
        },
    })
    result = query_knowledge.get_family_heuristics(
        "aes_xcrypt", "nangate45",
        heuristics_path=tmp_knowledge_dir / "heuristics.json",
    )
    assert result is not None
    assert result["core_utilization"]["median"] == 25
    assert result["sample_size"] == 10


def test_get_family_heuristics_miss(tmp_knowledge_dir):
    _write(tmp_knowledge_dir, {"families": {}})
    result = query_knowledge.get_family_heuristics(
        "nonexistent", "nangate45",
        heuristics_path=tmp_knowledge_dir / "heuristics.json",
    )
    assert result is None


def test_get_family_heuristics_no_file(tmp_knowledge_dir):
    result = query_knowledge.get_family_heuristics(
        "aes_xcrypt", "nangate45",
        heuristics_path=tmp_knowledge_dir / "heuristics.json",
    )
    assert result is None
```

- [ ] **Step 4.2: Run tests to verify they fail**

```bash
cd /data1/shenshan/agent_with_openroad && \
  python3 -m pytest skills/r2g-rtl2gds/tests/test_query_knowledge.py -v
```

Expected: **FAIL** with `ModuleNotFoundError`.

- [ ] **Step 4.3: Implement `skills/r2g-rtl2gds/knowledge/query_knowledge.py`**

```python
#!/usr/bin/env python3
"""Read-only API over knowledge/heuristics.json.

Usage (CLI):
  query_knowledge.py family <family> [--platform <p>]
  query_knowledge.py list

Other scripts (notably suggest_config.py) import this module directly
and call get_family_heuristics().
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import knowledge_db

DEFAULT_HEURISTICS_PATH = knowledge_db.DEFAULT_KNOWLEDGE_DIR / "heuristics.json"


def _load(heuristics_path: Path | str = DEFAULT_HEURISTICS_PATH) -> dict[str, Any]:
    p = Path(heuristics_path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def get_family_heuristics(family: str,
                          platform: str,
                          heuristics_path: Path | str = DEFAULT_HEURISTICS_PATH
                          ) -> dict[str, Any] | None:
    data = _load(heuristics_path)
    fam = (data.get("families") or {}).get(family)
    if not fam:
        return None
    return (fam.get("platforms") or {}).get(platform)


def list_families(heuristics_path: Path | str = DEFAULT_HEURISTICS_PATH
                  ) -> list[tuple[str, str]]:
    data = _load(heuristics_path)
    out: list[tuple[str, str]] = []
    for fam_name, fam in (data.get("families") or {}).items():
        for plat in (fam.get("platforms") or {}):
            out.append((fam_name, plat))
    return sorted(out)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    pf = sub.add_parser("family", help="Look up heuristics for one family/platform")
    pf.add_argument("family")
    pf.add_argument("--platform", default="nangate45")
    pf.add_argument("--heuristics", type=Path, default=DEFAULT_HEURISTICS_PATH)

    pl = sub.add_parser("list", help="List known (family, platform) pairs")
    pl.add_argument("--heuristics", type=Path, default=DEFAULT_HEURISTICS_PATH)

    args = p.parse_args()

    if args.cmd == "family":
        result = get_family_heuristics(args.family, args.platform,
                                       heuristics_path=args.heuristics)
        if result is None:
            print(f"No heuristics for ({args.family}, {args.platform}).",
                  file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 0

    if args.cmd == "list":
        pairs = list_families(heuristics_path=args.heuristics)
        if not pairs:
            print("(empty)")
            return 0
        for fam, plat in pairs:
            print(f"{fam}\t{plat}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4.4: Run tests to verify they pass**

```bash
cd /data1/shenshan/agent_with_openroad && \
  python3 -m pytest skills/r2g-rtl2gds/tests/test_query_knowledge.py -v
```

Expected: **all 3 tests PASS**.

- [ ] **Step 4.5: Checkpoint**

Log: `Task 4 complete: query_knowledge.py read API.`

---

## Task 5: `mine_rules.py` — failure-signature review queue

**Files:**
- Create: `skills/r2g-rtl2gds/knowledge/mine_rules.py`
- Create: `skills/r2g-rtl2gds/tests/test_mine_rules.py`

- [ ] **Step 5.1: Write the failing test**

```python
"""Tests for mine_rules.py."""
from __future__ import annotations

import json

import knowledge_db
import mine_rules


def _insert_run(conn, **row):
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
    defaults["ingested_at"] = "2026-04-11T00:00:00Z"
    defaults["project_path"] = defaults["project_path"] or f"/tmp/{defaults['run_id']}"
    cols = ", ".join(defaults.keys())
    ph = ", ".join(f":{k}" for k in defaults.keys())
    conn.execute(f"INSERT INTO runs ({cols}) VALUES ({ph})", defaults)


def test_mine_surfaces_repeated_signature(tmp_knowledge_dir):
    db_path = tmp_knowledge_dir / "runs.sqlite"
    conn = knowledge_db.connect(db_path)
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    # Three PDN-0179 failures across two distinct designs
    for i, design in enumerate(["black_parrot", "black_parrot", "swerv_wrapper"]):
        rid = f"fail_{i}"
        _insert_run(conn, run_id=rid, design_name=design,
                    design_family="bp_multi_top" if "parrot" in design else "swerv",
                    platform="nangate45",
                    core_utilization=40.0, place_density_lb_addon=0.20,
                    synth_hierarchical=1, abc_area=1,
                    orfs_status="fail", orfs_fail_stage="floorplan")
        conn.execute(
            "INSERT INTO failure_events (run_id, stage, signature, detail) "
            "VALUES (?, ?, ?, ?)",
            (rid, "floorplan", "pdn-0179", "Unable to repair all channels."),
        )
    # One irrelevant single failure (should not surface)
    _insert_run(conn, run_id="noise", design_name="ibex_core",
                design_family="ibex", platform="nangate45",
                orfs_status="fail", orfs_fail_stage="route")
    conn.execute(
        "INSERT INTO failure_events (run_id, stage, signature, detail) "
        "VALUES (?, ?, ?, ?)",
        ("noise", "route", "grt-0116", None),
    )
    conn.commit()
    conn.close()

    out = tmp_knowledge_dir / "failure_candidates.json"
    mine_rules.mine(db_path, out)

    data = json.loads(out.read_text())
    sigs = {c["signature"]: c for c in data["candidates"]}
    assert "pdn-0179" in sigs
    assert sigs["pdn-0179"]["occurrences"] == 3
    assert sigs["pdn-0179"]["distinct_designs"] == 2
    assert "grt-0116" not in sigs  # below threshold
```

- [ ] **Step 5.2: Run tests to verify they fail**

```bash
cd /data1/shenshan/agent_with_openroad && \
  python3 -m pytest skills/r2g-rtl2gds/tests/test_mine_rules.py -v
```

Expected: **FAIL** with `ModuleNotFoundError`.

- [ ] **Step 5.3: Implement `skills/r2g-rtl2gds/knowledge/mine_rules.py`**

```python
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


def mine(db_path: Path | str,
         out_path: Path | str,
         min_occurrences: int = 3,
         min_distinct_designs: int = 2) -> dict:
    db_path = Path(db_path)
    out_path = Path(out_path)

    conn = knowledge_db.connect(db_path)
    rows = _fetch(conn)
    conn.close()

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
```

- [ ] **Step 5.4: Run tests to verify they pass**

```bash
cd /data1/shenshan/agent_with_openroad && \
  python3 -m pytest skills/r2g-rtl2gds/tests/test_mine_rules.py -v
```

Expected: **test PASSES**.

- [ ] **Step 5.5: Checkpoint**

Log: `Task 5 complete: mine_rules.py review queue.`

---

## Task 6: Integrate learned heuristics into `suggest_config.py`

**Files:**
- Modify: `skills/r2g-rtl2gds/knowledge/suggest_config.py`
- Create: `skills/r2g-rtl2gds/tests/test_suggest_config_integration.py`

- [ ] **Step 6.1: Write the failing integration test**

```python
"""Integration test: suggest_config.py prefers learned heuristics when present."""
from __future__ import annotations

import json
from pathlib import Path

import suggest_config


def _make_fake_project(tmp_path: Path) -> Path:
    project = tmp_path / "aes_run"
    (project / "constraints").mkdir(parents=True)
    (project / "rtl").mkdir()
    (project / "synth").mkdir()
    (project / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = aes128_core\n"
        "export PLATFORM = nangate45\n"
        "export VERILOG_FILES = /tmp/fake.v\n"
        "export SDC_FILE = /tmp/fake.sdc\n"
    )
    (project / "rtl" / "aes128_core.v").write_text(
        "module aes128_core(input clk); endmodule\n"
    )
    (project / "synth" / "synth.log").write_text("Number of cells: 12412\n")
    return project


def test_suggest_config_uses_learned_heuristics(tmp_path, tmp_knowledge_dir,
                                                monkeypatch):
    project = _make_fake_project(tmp_path)

    # Pre-populate heuristics.json. Use CU median=22 which is BELOW the
    # hard-coded crypto clamp of min(cu, 25) — that way we exercise the
    # "learned value wins" path cleanly, without colliding with the
    # design-type safety rail. A separate test below covers the clamp.
    heur_path = tmp_knowledge_dir / "heuristics.json"
    heur_path.write_text(json.dumps({
        "families": {
            "aes_xcrypt": {
                "platforms": {
                    "nangate45": {
                        "sample_size": 10,
                        "success_count": 10,
                        "success_rate": 1.0,
                        "core_utilization": {"min_safe": 20, "max_safe": 24, "median": 22},
                        "place_density_lb_addon": {"min_safe": 0.15,
                                                    "max_safe": 0.25,
                                                    "median": 0.22},
                    },
                },
            },
        },
    }))

    # Only the explicit `heuristics_path=HEURISTICS_PATH` argument matters
    # because suggest_config.recommend passes it explicitly; no need to
    # monkeypatch query_knowledge.DEFAULT_HEURISTICS_PATH.
    monkeypatch.setattr(suggest_config, "HEURISTICS_PATH", heur_path)

    result = suggest_config.recommend(project)

    assert result["design_name"] == "aes128_core"
    # Path documentation — exercises the crypto code path intentionally.
    assert result["design_type"] == "crypto"
    assert result["size_class"] == "medium"
    # Learned median 22 survives the crypto clamp (min(22, 25) == 22).
    assert result["recommendations"]["CORE_UTILIZATION"] == 22
    assert abs(result["recommendations"]["PLACE_DENSITY_LB_ADDON"] - 0.22) < 1e-9
    assert any("learned" in e.lower() for e in result["explanations"])
    assert result.get("learned_source") == "aes_xcrypt/nangate45"


def test_design_type_clamp_still_fires_over_learned_value(
    tmp_path, tmp_knowledge_dir, monkeypatch,
):
    """Safety rail test: a too-aggressive learned CU is clamped by crypto rule."""
    project = _make_fake_project(tmp_path)
    heur_path = tmp_knowledge_dir / "heuristics.json"
    heur_path.write_text(json.dumps({
        "families": {
            "aes_xcrypt": {
                "platforms": {
                    "nangate45": {
                        "sample_size": 3,
                        "success_count": 3,
                        "success_rate": 1.0,
                        "core_utilization": {"min_safe": 30, "max_safe": 40, "median": 35},
                    },
                },
            },
        },
    }))
    monkeypatch.setattr(suggest_config, "HEURISTICS_PATH", heur_path)

    result = suggest_config.recommend(project)
    # Crypto safety clamp min(35, 25) == 25, NOT the learned 35.
    assert result["recommendations"]["CORE_UTILIZATION"] == 25
    assert result.get("learned_source") == "aes_xcrypt/nangate45"


def test_suggest_config_falls_back_without_heuristics(tmp_path, tmp_knowledge_dir,
                                                      monkeypatch):
    project = _make_fake_project(tmp_path)
    # Non-existent heuristics file
    heur_path = tmp_knowledge_dir / "heuristics.json"
    monkeypatch.setattr(suggest_config, "HEURISTICS_PATH", heur_path)

    result = suggest_config.recommend(project)
    # Document the path: crypto/medium means base 25, crypto clamp min(25, 25) == 25.
    assert result["design_type"] == "crypto"
    assert result["size_class"] == "medium"
    assert result["recommendations"]["CORE_UTILIZATION"] == 25
    assert result.get("learned_source") is None
```

- [ ] **Step 6.2: Run tests to verify they fail**

```bash
cd /data1/shenshan/agent_with_openroad && \
  python3 -m pytest skills/r2g-rtl2gds/tests/test_suggest_config_integration.py -v
```

Expected: **FAIL** — `learned_source` key missing, `CORE_UTILIZATION != 28`.

- [ ] **Step 6.3: Modify `suggest_config.py`**

At the top, immediately after the existing `from pathlib import Path`, add:

```python
import knowledge_db
import query_knowledge

HEURISTICS_PATH = knowledge_db.DEFAULT_KNOWLEDGE_DIR / "heuristics.json"
FAMILIES_PATH = knowledge_db.DEFAULT_FAMILIES_PATH
```

Inside `recommend()`, insert the learned-heuristics block **immediately after** `recommendations.update(params_by_size.get(size_class, params_by_size['unknown']))` (currently line 125) and **before** the `# Design-type adjustments` comment at line 127. Placing it here makes the learned values the new baseline; the existing `bus_heavy` / `macro_heavy` / `crypto` clamps then still fire as safety rails on top of any learned value.

```python
    # --- Learned-heuristics override (before design-type adjustments) ----
    # Learned values become the new baseline. The design-type clamps below
    # still apply, so e.g. a bus_heavy design with a learned median of 28
    # will still be clamped to 15 by the existing bus_heavy rule. This is
    # intentional: safety rails beat empirical medians.
    learned_source = None
    learned = None
    try:
        families = knowledge_db.load_families(FAMILIES_PATH)
        family = knowledge_db.infer_family(config.get('DESIGN_NAME', ''), families)
        learned = query_knowledge.get_family_heuristics(
            family, platform, heuristics_path=HEURISTICS_PATH,
        )
    except (OSError, json.JSONDecodeError):
        learned = None

    if learned:
        cu = learned.get('core_utilization') or {}
        pd = learned.get('place_density_lb_addon') or {}
        if 'median' in cu:
            recommendations['CORE_UTILIZATION'] = cu['median']
        if 'median' in pd:
            recommendations['PLACE_DENSITY_LB_ADDON'] = pd['median']
        learned_source = f"{family}/{platform}"
        explanations.append(
            f"Learned heuristics for {family}/{platform} "
            f"(n={learned.get('sample_size', 0)}, "
            f"success_rate={learned.get('success_rate', 0):.2f}): "
            f"CORE_UTILIZATION={recommendations.get('CORE_UTILIZATION')}, "
            f"PLACE_DENSITY_LB_ADDON={recommendations.get('PLACE_DENSITY_LB_ADDON')}"
        )
    # ----------------------------------------------------------------------
```

Note: no `from __future__ import annotations` needed — the snippet above uses plain `= None` defaults (no `str | None` syntax), so it's safe on Python 3.9+.

And extend the final `return` dict:

```python
    return {
        'design_name': config.get('DESIGN_NAME', 'unknown'),
        'platform': platform,
        'cell_count': cell_count,
        'size_class': size_class,
        'design_type': design_type,
        'synth_stats': synth_stats,
        'recommendations': recommendations,
        'explanations': explanations,
        'learned_source': learned_source,
    }
```

- [ ] **Step 6.4: Run the integration tests**

```bash
cd /data1/shenshan/agent_with_openroad && \
  python3 -m pytest skills/r2g-rtl2gds/tests/test_suggest_config_integration.py -v
```

Expected: **both tests PASS**.

- [ ] **Step 6.5: Run the full test suite to verify nothing regressed**

```bash
cd /data1/shenshan/agent_with_openroad && \
  python3 -m pytest skills/r2g-rtl2gds/tests/ -v
```

Expected: **all ≥15 tests PASS** (5 in test_knowledge_db + 3 in test_ingest_run + 2 in test_learn_heuristics + 3 in test_query_knowledge + 1 in test_mine_rules + 3 in test_suggest_config_integration).

- [ ] **Step 6.6: Manual smoke test (end-to-end, using the success fixture)**

```bash
cd /data1/shenshan/agent_with_openroad && \
  rm -f skills/r2g-rtl2gds/knowledge/runs.sqlite \
         skills/r2g-rtl2gds/knowledge/heuristics.json && \
  python3 skills/r2g-rtl2gds/knowledge/ingest_run.py \
    skills/r2g-rtl2gds/tests/fixtures/sample_run_success && \
  python3 skills/r2g-rtl2gds/knowledge/ingest_run.py \
    skills/r2g-rtl2gds/tests/fixtures/sample_run_fail_pdn && \
  python3 skills/r2g-rtl2gds/knowledge/learn_heuristics.py && \
  python3 skills/r2g-rtl2gds/knowledge/query_knowledge.py list && \
  python3 skills/r2g-rtl2gds/knowledge/mine_rules.py --min-occurrences 1 --min-distinct-designs 1 && \
  rm -f skills/r2g-rtl2gds/knowledge/runs.sqlite \
         skills/r2g-rtl2gds/knowledge/heuristics.json \
         skills/r2g-rtl2gds/knowledge/failure_candidates.json
```

Expected: every command exits 0; `query_knowledge.py list` prints `(empty)` because only 1 successful run (below `MIN_SUCCESSFUL=3`); `mine_rules.py` with the relaxed thresholds writes `failure_candidates.json` listing `pdn-0179` and `orfs-fail-floorplan`. The cleanup at the end leaves the skill in its pre-smoke state.

- [ ] **Step 6.7: Checkpoint**

Log: `Task 6 complete: suggest_config.py consumes learned heuristics; full suite green.`

---

## Task 7: Documentation updates (SKILL.md + CLAUDE.md)

**Files:**
- Modify: `skills/r2g-rtl2gds/SKILL.md`
- Modify: `CLAUDE.md`

No new tests — docs only. Run the full pytest suite afterwards as a sanity check that nothing broke.

- [ ] **Step 7.1: Add Workflow step 13 ("Ingest Run") to `SKILL.md`**

After the existing Reports section, insert:

```markdown
### 13. Ingest the Run into the Knowledge Store

After **every** flow — successful, failed, or partial — run:

```bash
python3 skills/r2g-rtl2gds/knowledge/ingest_run.py eda-runs/<project>
```

This reads the structured JSON artifacts produced by the extraction scripts
and appends one row to `skills/r2g-rtl2gds/knowledge/runs.sqlite`. It never
parses raw ORFS logs.

Then rebuild derived artifacts:

```bash
python3 skills/r2g-rtl2gds/knowledge/learn_heuristics.py
python3 skills/r2g-rtl2gds/knowledge/mine_rules.py
```

- `knowledge/heuristics.json` is consumed automatically by
  `suggest_config.py` on the next project — no CLI changes required.
- `knowledge/failure_candidates.json` is a **review queue**, not a rule
  source. Surface new signatures to the user and, if confirmed, edit
  `references/failure-patterns.md` by hand.

A family/platform pair appears in `heuristics.json` only after at least
**3 successful runs** under that configuration.
```

- [ ] **Step 7.2: Add a "Knowledge Store" subsection to the "Project Layout" section of `CLAUDE.md`**

Append after the existing Project Layout tree:

```markdown
### Knowledge Store (inside the skill)

```
skills/r2g-rtl2gds/knowledge/
  schema.sql               # SQLite DDL
  families.json            # design_name → design_family mapping + patterns
  runs.sqlite              # one row per ingested run (generated)
  heuristics.json          # empirical per-family bounds (generated)
  failure_candidates.json  # review queue for new failure-patterns.md entries (generated)
```

Populated by `knowledge/ingest_run.py`, derived by `knowledge/learn_heuristics.py`
and `knowledge/mine_rules.py`, consumed by `knowledge/suggest_config.py` and
`knowledge/query_knowledge.py`. Phase 2 only — no version DAG yet (deferred).
```

- [ ] **Step 7.3: Append the 4 new scripts to the Script Inventory tables in `CLAUDE.md`**

Add to the "Analysis & Extraction Scripts (Python)" table:

```markdown
| `ingest_run.py` | Ingest one eda-runs/<project> directory into the knowledge store | `<project-dir> [--db <path>]` | `knowledge/runs.sqlite` (upsert) |
| `learn_heuristics.py` | Derive empirical per-family bounds from runs.sqlite | `[--db <path>] [--out <path>]` | `knowledge/heuristics.json` |
| `query_knowledge.py` | Read-only API + CLI over heuristics.json | `family <name> \| list` | stdout JSON |
| `mine_rules.py` | Surface repeated failure signatures as a review queue | `[--min-occurrences N]` | `knowledge/failure_candidates.json` |
```

Also add `knowledge_db.py` to an "Internal modules" note (a one-liner under the table).

- [ ] **Step 7.4: Run the full test suite one last time**

```bash
cd /data1/shenshan/agent_with_openroad && \
  python3 -m pytest skills/r2g-rtl2gds/tests/ -v
```

Expected: **all tests PASS.**

- [ ] **Step 7.5: Final checkpoint**

Log: `Phase 2 complete — knowledge store operational, documentation updated, Phase 3 (version DAG) deferred.`

---

## Future Work (Phase 3, explicitly deferred)

The following OpenSpace-inspired capabilities were scoped *out* of this plan and should be considered for a Phase 3:

1. **Version DAG for skill content** — immutable SQLite snapshots of
   `SKILL.md`, `config.mk` templates, and reference docs with parent
   pointers, enabling `FIX` / `DERIVED` / `CAPTURED` evolution types.
2. **Analyzer LLM loop** — after each run, an LLM analyses `runs.sqlite` +
   the run artifacts and proposes concrete `SKILL.md` / `failure-patterns.md`
   edits as unified diffs.
3. **Auto-merge with anti-loop guards** — newly-evolved skills require N
   fresh data points before re-evaluation (OpenSpace's `min_selections=5`
   pattern).
4. **Execution-analysis rolling window** — a per-skill `recent_analyses`
   table bounded to 50 entries, used as short-term episodic memory during
   evolution.
5. **Suggestion-accuracy feedback loop** — record the `suggest_config.py`
   output (in particular `learned_source` and `recommendations`) alongside
   each run's actual config, so `learn_heuristics.py` can eventually report
   "the suggester would have steered this run correctly" vs "the operator
   overrode and it was right to." This is the signal that turns Phase 2
   from empirical bounds into a feedback-driven recommender. Deferred
   only because the telemetry column does not yet exist; adding it is a
   one-line schema bump once Phase 2 is stable.

Phase 2 deliberately stops short of these so every change in this plan
remains deterministic and auditable. Phase 3 would require introducing an
LLM into the learning loop, which is a separate trust boundary and should
be planned on its own.
