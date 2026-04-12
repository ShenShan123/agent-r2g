"""Tests for config lineage tracking."""
from __future__ import annotations

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
