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


def test_ingest_reads_clk_period_from_sdc_and_staged_slacks(tmp_path, tmp_knowledge_dir):
    import ingest_run, knowledge_db, json as _json
    proj = tmp_path / "design_cases" / "demo"
    (proj / "constraints").mkdir(parents=True)
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n", encoding="utf-8")
    # Period lives in the SDC, NOT config.mk (this is the bug being fixed).
    (proj / "constraints" / "constraint.sdc").write_text(
        "set clk_period 3.5\ncreate_clock -period $clk_period [get_ports clk]\n", encoding="utf-8")
    (proj / "reports" / "ppa.json").write_text(_json.dumps({
        "summary": {
            "timing": {"setup_wns": 0.4, "setup_tns": 0.0},
            "timing_staged": {"floorplan_setup_ws": 0.9,
                              "place_setup_ws": 0.5,
                              "finish_setup_ws": 0.4},
        }
    }), encoding="utf-8")

    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    rid = ingest_run.ingest(proj, conn, families_path=tmp_knowledge_dir / "families.json")
    r = conn.execute(
        "SELECT clock_period_ns, floorplan_setup_ws, place_setup_ws, finish_setup_ws "
        "FROM runs WHERE run_id=?", (rid,)).fetchone()
    assert r == (3.5, 0.9, 0.5, 0.4)
    conn.close()


def test_backfill_updates_staged_slacks_from_logs(tmp_path, tmp_knowledge_dir):
    import ingest_run, knowledge_db, json as _json
    cases = tmp_path / "design_cases"
    proj = cases / "demo"
    (proj / "constraints").mkdir(parents=True)
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n", encoding="utf-8")
    (proj / "constraints" / "constraint.sdc").write_text("set clk_period 4.0\n", encoding="utf-8")
    # An OLD ppa.json without timing_staged (pre-feature run).
    (proj / "reports" / "ppa.json").write_text(_json.dumps(
        {"summary": {"timing": {"setup_wns": 0.6}}}), encoding="utf-8")
    logs = proj / "backend" / "RUN_2026-01-01_00-00-00" / "logs"
    logs.mkdir(parents=True)
    (logs / "2_1_floorplan.json").write_text(
        _json.dumps({"floorplan__timing__setup__ws": 1.2}), encoding="utf-8")
    (logs / "3_5_place_dp.json").write_text(
        _json.dumps({"detailedplace__timing__setup__ws": 0.8}), encoding="utf-8")

    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    # First ingest the (old) run; staged columns are NULL because ppa.json lacks them.
    ingest_run.ingest(proj, conn, families_path=tmp_knowledge_dir / "families.json")
    assert conn.execute("SELECT place_setup_ws FROM runs").fetchone()[0] is None
    # Backfill from the preserved logs.
    n = ingest_run.backfill(cases, conn)
    assert n == 1
    r = conn.execute(
        "SELECT clock_period_ns, floorplan_setup_ws, place_setup_ws, finish_setup_ws "
        "FROM runs").fetchone()
    assert r == (4.0, 1.2, 0.8, 0.6)  # finish backfilled from existing wns_ns
    conn.close()


def test_backfill_filters_unconstrained_sentinel(tmp_path, tmp_knowledge_dir):
    import ingest_run, knowledge_db, json as _json
    cases = tmp_path / "design_cases"
    proj = cases / "demo"
    (proj / "constraints").mkdir(parents=True)
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n", encoding="utf-8")
    (proj / "constraints" / "constraint.sdc").write_text("set clk_period 4.0\n", encoding="utf-8")
    (proj / "reports" / "ppa.json").write_text(_json.dumps(
        {"summary": {"timing": {"setup_wns": 0.6}}}), encoding="utf-8")
    logs = proj / "backend" / "RUN_2026-01-01_00-00-00" / "logs"
    logs.mkdir(parents=True)
    (logs / "3_5_place_dp.json").write_text(
        _json.dumps({"detailedplace__timing__setup__ws": 1e39}), encoding="utf-8")
    conn = knowledge_db.connect(tmp_knowledge_dir / "runs.sqlite")
    knowledge_db.ensure_schema(conn, schema_path=tmp_knowledge_dir / "schema.sql")
    ingest_run.ingest(proj, conn, families_path=tmp_knowledge_dir / "families.json")
    ingest_run.backfill(cases, conn)
    assert conn.execute("SELECT place_setup_ws FROM runs").fetchone()[0] is None
    conn.close()
