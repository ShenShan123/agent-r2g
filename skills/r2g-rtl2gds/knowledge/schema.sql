-- r2g-rtl2gds knowledge store schema. DO NOT edit at runtime —
-- all writes go through scripts/knowledge_db.py::ensure_schema.

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
