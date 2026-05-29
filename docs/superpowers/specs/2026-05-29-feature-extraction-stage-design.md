# Feature-Extraction Stage — Design Spec

**Date:** 2026-05-29
**Status:** Approved (design), pending implementation
**Branch:** `feat/label-extraction-stage`

## Goal

Incorporate eight new graph-feature-generating scripts (currently sitting untracked in
`feature_test_v2/py/`) into the `r2g-rtl2gds` skill as a first-class post-flow stage that,
after an ORFS backend run completes, emits per-node / per-edge / graph-level **feature**
tables plus a per-design summary-statistics JSON.

These are the **feature (X) side** of the ML dataset and are the direct complement to the
existing **label (Y) side** produced by the label-extraction stage
([`references/label-extraction.md`](../../../r2g-rtl2gds/references/label-extraction.md),
`scripts/flow/run_labels.sh`). Features and labels are extracted from the **same**
`6_final.def` / `6_final.odb` so the per-cell / per-net rows join row-for-row.

These scripts are distinct from the existing `scripts/extract/extract_*.py` extractors
(which parse tool output into *summary JSON* for the dashboard) and from the label
extractors (which produce *regression-target* CSVs). They produce a **graph**: typed nodes
(cells, nets, I/O pins, pins), typed edges (gate→pin, pin→net, iopin→net), and one
graph-level metadata row.

## Scope (from the decisions taken 2026-05-29)

- **Orchestrator:** a separate `scripts/flow/run_features.sh`, mirroring `run_labels.sh`
  line-for-line. It does **not** touch the shipped `run_labels.sh` CLI. (A shared helper
  may be factored out later if a third dataset stage appears.)
- **Migration fidelity:** *light refactor* — vendor the eight workers + three shared
  modules near-verbatim, then (a) re-root path resolution onto `design_cases/<d>/`, (b)
  inject the platform liberty/LEF, (c) translate the Chinese usage/comment strings to
  English, (d) parameterize the Nangate-specific constants, and (e) dedup the verbatim-
  duplicated DEF/SDC parsing helpers into a shared module. **Every change is guarded by a
  byte-for-byte regression test** against the committed `feature_test_v2/output/ac97_top/`
  golden (see Validation).
- **Platforms:** *fully parameterized* — correct categorical features on all six ORFS
  platforms (`nangate45`, `sky130hd`, `sky130hs`, `asap7`, `gf180`, `ihp-sg13g2`), not
  just Nangate. The parameterized code paths are **designed to be no-ops on nangate45** so
  the golden regression holds; they diverge only off-platform.
- **Output:** per-design feature CSVs **+** a per-design summary-statistics JSON. **No**
  corpus-wide aggregation, **no** knowledge-SQLite ingest, **no** dashboard surfacing
  (all explicitly deferred, matching the label stage).
- **Execution this session:** wire + validate on a completed nangate45 design (`aes_core`
  / `ac97_top`), confirm CSV row counts and that `graph_id`+`inst_name`/`net_name` join
  cleanly to the label CSVs, then a subset backfill.

## The eight feature tables

Written to `design_cases/<design>/features/`. Each worker independently re-parses the DEF
(plus SDC/SPEF/liberty as needed) — there is **no producer→consumer dependency** among
the eight, so they are run order-independently and each fails soft.

| # | Worker | Granularity / CSV | Columns | Inputs |
|---|--------|-------------------|---------|--------|
| 1 | `metadata.py` | one row per **design** → `metadata.csv` | `graph_id,num_cells,num_nets,num_ios,avg_fanout,die_width,die_height,core_area,dbu_unit,PLACE_DENSITY,CORE_UTILIZATION,ABC_AREA,C_total,tracks_per_layer,V_nom,freq_Hz` | DEF, config.mk, SDC, SPEF, liberty |
| 2 | `nodes_gate.py` | per placed **instance** → `nodes_gate.csv` | `graph_id,inst_name,master,cell_type_id,cell_area,cell_power,x_um,y_um,orientation,orientation_id,placement_status,placement_status_id` | DEF, liberty, cell-type map |
| 3 | `nodes_net.py` | per **net** → `nodes_net.csv` | `graph_id,net_name,net_type_id,fanout,pin_count,num_drivers,num_sinks,connects_macro_flag,num_layer,hpwl_um` | DEF, SDC, liberty, tech LEF |
| 4 | `nodes_iopin.py` | per top-level **I/O pin** → `nodes_iopin.csv` | `graph_id,iopin_name,net_name,pin_x_um,pin_y_um,pin_owner_master,pin_name,pin_layer_hint,nearest_tap_distance_um,pin_direction,pin_direction_id,net_use,net_type_id` | DEF, SDC, cell-type map (tap) |
| 5 | `nodes_pin.py` | per **pin** (I/O + instance) → `nodes_pin.csv` | `graph_id,inst_name,pin_name,pin_type_id,sum_pin_cap_fF,pin_x_std_um,pin_y_std_um` | DEF, SPEF, liberty |
| 6 | `edges_iopin_net.py` | per **(I/O pin → net)** → `edges_iopin_net.csv` | `graph_id,iopin_name,net_name,net_type_id,pin_direction_id` | DEF, SDC |
| 7 | `edges_pin_net.py` | per **(pin → net)** → `edges_pin_net.csv` | `graph_id,inst_name,pin_name,pin_type_id,net_name,net_type_id` | DEF, SDC, liberty |
| 8 | `edges_gate_pin.py` | per **(gate → pin)** → `edges_gate_pin.csv` | `graph_id,inst_name,pin_name,cell_type_id,pin_type_id` | DEF, liberty, cell-type map |
| — | `reports/features_stats.json` | per-design | per-CSV row count + status + numeric summaries (min/mean/p50/p90/p95/p99/max) of key columns; platform + SPEF-present flags | — |

`graph_id` (= design name) joins to the label CSVs' `Design`; `inst_name`/`net_name`/
`iopin_name` join nodes↔edges and join to the labels' `Cell`/`Net`.

## Architecture

Mirrors `run_labels.sh` (source `_env.sh`, read `DESIGN_NAME`/`PLATFORM` from
`constraints/config.mk`, locate the collected `6_final.*`, resolve platform paths, run
fail-soft workers, roll up stats).

```
scripts/flow/run_features.sh <project-dir> [platform]          # entry point (flow stage, fail-soft)
scripts/flow/resolve_platform_paths.sh <config.mk> <plat>      # REUSED verbatim (make-eval resolver)
scripts/extract/features/case_paths.py                         # shared: argv+env path/ctx resolution
scripts/extract/features/def_parse.py                          # shared: deduped DEF/SDC parsing helpers
scripts/extract/features/lib_db.py                             # shared: liberty parser + classifiers (platform-param)
scripts/extract/features/cell_type_map.py                      # shared: per-platform master->cell_type_id
scripts/extract/features/metadata.py                           # graph-level features
scripts/extract/features/nodes_gate.py                         # per-instance
scripts/extract/features/nodes_net.py                          # per-net
scripts/extract/features/nodes_iopin.py                        # per-I/O-pin
scripts/extract/features/nodes_pin.py                          # per-pin
scripts/extract/features/edges_gate_pin.py                     # gate->pin
scripts/extract/features/edges_pin_net.py                      # pin->net
scripts/extract/features/edges_iopin_net.py                    # iopin->net
scripts/extract/features/compute_feature_stats.py              # 8 CSVs -> reports/features_stats.json
tools/run_features_batch.sh                                    # subset/full backfill driver
```

### `run_features.sh` responsibilities

1. Source `_env.sh`; bail (exit 1) if ORFS not found or no `<project-dir>` arg.
2. Read `DESIGN_NAME`/`PLATFORM` from `<project-dir>/constraints/config.mk` (platform arg
   overrides; default `nangate45`), with `|| true` on every grep under `set -euo pipefail`.
3. **Locate the routed design** from the collected backend copy: newest
   `<project-dir>/backend/RUN_*/{final,results}/6_final.def`, falling back to the live
   `$FLOW_DIR/results/<plat>/<design>/<variant>/`. If no DEF → record
   `status=skipped (no backend artifacts)` and exit 0. `DEF_FILE` env overrides (e.g. to
   feed an exported `5_route.def`).
4. **Locate the SPEF** (optional): newest `backend/RUN_*/rcx/6_final.spef`, fallbacks
   `…/results/6_final.spef`, `<project-dir>/rcx/6_final.spef`. Absence is non-fatal: the
   SPEF-derived columns (`C_total`, I/O `sum_pin_cap_fF`) degrade to 0 and the stats JSON
   records `spef_present=false`.
5. **Resolve platform values** via `resolve_platform_paths.sh` → `LIB_FILES`, `TECH_LEF`,
   `ADDITIONAL_LIBS`. The worker liberty list is `LIB_FILES + ADDITIONAL_LIBS`.
6. Run the eight workers into `<project-dir>/features/*.csv`, each via a `run_soft`
   wrapper (`timeout --signal=TERM --kill-after=30 $FEATURE_TIMEOUT`, logs to
   `features/<name>.log`, never aborts the orchestrator). Inputs passed as positional
   args (`<DEF> <out_csv> <graph_id>`) + env (`R2G_SDC`, `R2G_SPEF`, `R2G_CONFIG`,
   `R2G_LIB_FILES`, `R2G_TECH_LEF`, `R2G_PLATFORM`).
7. Run `compute_feature_stats.py` → `<project-dir>/reports/features_stats.json`.
8. Print a summary (rows per CSV, succeeded/skipped) and exit 0.

### Worker CLI contract (mirrors the label workers)

Each worker is independently runnable and unit-testable:

```
python3 <worker>.py <DEF> <out_csv> <graph_id>
  env: R2G_SDC R2G_SPEF R2G_CONFIG R2G_LIB_FILES R2G_TECH_LEF R2G_PLATFORM
```

`case_paths.resolve_case_paths()` builds the ctx dict from these (replacing the old
`feature_test_v2/input/<case>/` model). The worker `main()` bodies are otherwise unchanged
— they still read `ctx["def_path"]`, `ctx["out_csv"]`, `ctx["graph_id"]`,
`ctx["sdc_path"]`, `ctx["spef_path"]`, `ctx["config_path"]`.

### Generalization changes (the "fully parameterize", all no-ops on nangate45)

1. **`cell_type_map.py`** — the curated Nangate `COMPLETE_CELL_TYPE_MAPPING` (IDs 0–128,
   `UNKNOWN=95`) is **kept verbatim as the nangate45 map** so its IDs are preserved. For
   any other platform, a deterministic map is built at runtime by enumerating the resolved
   liberty's cell names in sorted order and assigning stable IDs (categorical features only
   need per-platform determinism + distinctness, not cross-platform alignment). Fix C9: the
   eight `fakeram45_*` keys are upper-cased so they match the `.upper()` lookup (no effect
   on ac97_top, which has no macros). Fix C6: `nodes_gate.py`/`edges_gate_pin.py` `import`
   the map instead of `exec()`-ing `net_to_pt.py`.
2. **Layer counting (`nodes_net.py`)** — the hardcoded `metal\d+` route-layer regex is
   replaced by a matcher built from the **tech LEF**'s `TYPE ROUTING` layer names
   (e.g. nangate `metal1..metal10`, sky130 `li1`/`met1..met5`, asap7 `M1..M9`). On
   nangate45 the derived alternation is `(metal1|…|metal10)` applied with the same
   first-match-per-line `re.search` semantics, so `num_layer` is byte-identical to the old
   regex. If the tech LEF is unavailable, it falls back to the `metal\d+` regex (logged).
3. **Tap detection (`lib_db.is_tap_master`)** — kept as the cross-platform `"TAP" in name`
   substring (already matches `TAPCELL_X1`, sky130 `…tapvpwrvgnd…`, asap7 `TAPCELL_ASAP7…`),
   plus an overridable extra-pattern hook. Identical on nangate45.
4. **`V_nom` (`metadata.py`)** — precedence unchanged (argv > config `V_nom` > liberty
   `nom_voltage` > `1.10`). With the injected nangate liberty the value is identical to the
   golden.

### `compute_feature_stats.py` — output schema

`reports/features_stats.json`:
```json
{
  "design": "<name>", "platform": "<plat>", "spef_present": true,
  "features": {
    "metadata":      {"status":"ok","rows":1, "num_cells":N,"num_nets":N,"num_ios":N},
    "nodes_gate":    {"status":"ok","rows":N, "cell_area":{min,mean,p50,p90,p95,p99,max}, "cell_power":{...}},
    "nodes_net":     {"status":"ok","rows":N, "fanout":{...}, "hpwl_um":{...}},
    "nodes_iopin":   {"status":"ok","rows":N},
    "nodes_pin":     {"status":"ok","rows":N, "sum_pin_cap_fF":{...}},
    "edges_gate_pin":{"status":"ok","rows":N},
    "edges_pin_net": {"status":"ok","rows":N},
    "edges_iopin_net":{"status":"ok","rows":N}
  }
}
```
A CSV that is missing/empty records `{"status":"skipped","reason":...}`. Pure stdlib (csv +
statistics + json); no pandas/numpy.

### `tools/run_features_batch.sh` — backfill driver

Iterates a design list (explicit, or auto-discovered designs with a collected
`backend/RUN_*/{final,results}/6_final.def`), runs `run_features.sh` per design with a
concurrency cap (`set -uo pipefail`, MAX_JOBS default 4, modulo-barrier). Per-design logs
and a `features_backfill.jsonl` roll-up land under `design_cases/_batch/logs_features_<ts>/`.

## Documentation & integration

- **SKILL.md** — add a `13c` sub-step after the label `13b` block: `run_features.sh` usage,
  the `features/` output dir, env knobs, platform-support note, pointer to the new
  reference. Add `features/` (and `labels/`) to the project-layout tree, and a Resource-Map
  bullet for the new reference.
- **`references/feature-extraction.md`** (new) — the eight tables + columns + join keys,
  inputs & per-platform resolution (incl. the `6_final.def` source + `DEF_FILE` override
  and SPEF-optional degradation), why pin/net typing reads liberty, env knobs, batch
  backfill, scope notes (per-design only, typical corner, cell-origin HPWL approximation).
- **`references/workflow.md`** — add `## Phase 7b: Dataset Extraction (labels + features)`
  (closes the gap the label stage left — neither stage is in workflow.md today).
- **CLAUDE.md** — layout touch: `scripts/extract/features/`, `design_cases/<d>/features/`,
  `tools/run_features_batch.sh`, and a Where-to-Find-X row for the new reference (do the
  same for the label reference, which was missed).
- **`init_project.py`** — add `features/` to the created project layout.
- **`tests/conftest.py`** — add a `FEATURES_DIR` sys.path entry (mirrors `LABELS_DIR`).

## Validation & execution plan

1. **Regression (the gate):** a pytest test runs each refactored worker against the
   committed `feature_test_v2/input/ac97_top/{5_route.def,6_final.spef,constraint.sdc,
   config.mk}` + the bundled `NangateOpenCellLibrary_typical.lib`/`.tech.lef`, and asserts
   byte-for-byte equality with `feature_test_v2/output/ac97_top/*.csv` (md5-verified golden,
   confirmed deterministic on 2026-05-29). Any drift fails the build.
2. **Unit tests** for the parameterized paths: liberty-derived cell-type map on a synthetic
   sky130-style liberty; tech-LEF-derived layer counting on `met1`/`metal1`; SPEF-absent
   degradation; stats roller percentiles/skip handling.
3. **Live validation:** run `run_features.sh` on a completed nangate45 design from
   `6_final.def`, confirm all eight CSVs populate and `graph_id`+keys join to the existing
   `labels/*.csv`. Note: `6_final.def` ≠ the fixture's `5_route.def`, so row counts differ
   from the golden — that is expected (fill cells / final routing); the regression proves
   *refactor* equivalence, the live run proves *integration*.
4. **Subset backfill** via `tools/run_features_batch.sh`; review the roll-up for parse
   failures / skips.

## Out of scope (deferred)

- Corpus-wide feature aggregation (concat across designs / parquet).
- Knowledge-SQLite ingest of feature stats.
- Dashboard surfacing of feature distributions.
- A real DEF/LEF/Liberty grammar parser (the hand-rolled regex parsers are kept).
- True pin-level geometry: `get_pin_abs_pos_um` remains a cell-origin stub, so HPWL and
  pin x/y-std are cell-origin approximations (documented, not a blocker).

## Risks / open items

- **R1 — DEF source semantics.** Live runs read `6_final.def` (post-fill, final routing),
  not the route-stage `5_route.def` the v2 scratch outputs used; `num_layer`/`hpwl` will
  differ from the scratch outputs. Mitigated by the `DEF_FILE` override and by extracting
  features and labels from the *same* `6_final.def` (internal consistency is what matters).
- **R2 — regex parsers** are tuned to ORFS `write_def`/`write_spef` formatting; a different
  emitter could mis-parse. Mitigated by a row-count sanity field in the stats JSON
  (`nodes_gate` rows vs DEF `COMPONENTS`).
- **R3 — liberty injection** must thread the resolved path into `lib_db`; a silent miss
  zeros all area/power/cap. The loader logs an explicit "liberty not found" warning.
- **R4 — off-nangate classifier degradation** is now handled (parameterized), but the
  liberty-derived cell-type IDs are not comparable across platforms — `platform` is
  recorded in the stats JSON so consumers filter per platform.
