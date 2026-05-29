# Label-Extraction Stage — Design Spec

**Date:** 2026-05-28
**Status:** Approved (design), pending implementation plan
**Branch:** `feat/label-extraction-stage`

## Goal

Incorporate four new label-generating scripts (currently sitting untracked in
`extract_label/`) into the `r2g-rtl2gds` skill as a first-class post-flow stage that,
after an ORFS backend run completes, collects per-cell / per-net **regression-target
labels** plus **per-design summary statistics**. This is the data-collection backbone for
building a dataset of physical designs for ML.

These four scripts are distinct from the existing `scripts/extract/extract_*.py`
extractors: the existing ones parse tool output into *summary JSON* for the dashboard;
these produce *row-per-cell / row-per-net label tables* (CSV) whose `label` columns are
transformed regression targets.

## Scope (from brainstorming decisions)

- **Output:** per-design label CSVs **+** a per-design summary-statistics JSON. **No**
  corpus-wide aggregation script, **no** knowledge-SQLite ingest, **no** dashboard
  surfacing (all explicitly out of scope for now).
- **Platforms:** all ORFS platforms now (`nangate45`, `sky130hd`, `sky130hs`, `asap7`,
  `gf180`, `ihp-sg13g2`) — scripts must be made platform-aware, not Nangate-hardcoded.
- **Execution this session:** wire + validate on `aes_core` and `picorv32_core`, then a
  subset backfill of ~20–50 completed designs.

## The four labels

| Metric | Worker | Input | Output columns | Label transform |
|--------|--------|-------|----------------|-----------------|
| Congestion | `extract_congestion.py` | DEF + tech.lef | `Design,Cell,cell_type,cell_congestion,label` | `label = sqrt(cell_congestion)` |
| Wirelength | `extract_wirelength.py` | DEF | `Design,Net,NetType,WireLength_um,label,mask_wl` | `label = log1p(len_um)`; `mask_wl = (NetType==SIGNAL)` |
| Timing | `extract_timing.tcl` | ODB + liberty | `Design,Cell,Cell_Slack_ns,Path_Delay_ns,label,in_sta_path` | `label = log(1+path_delay)`; `path_delay = clk_period - worst_slack` (floored at 0) |
| IR drop | `extract_irdrop.tcl` | ODB (PDNSim) | `Design,Cell,X,Y,Voltage_V,IR_Drop_mV,P95_mV,label,has_irdrop` | `label = log(1 + ir_drop/P95)` |

`Design` + `Cell`/`Net` are the join keys across all four tables.

## Architecture

Mirrors the proven `run_rcx.sh` pattern (source `_env.sh`, read `DESIGN_NAME` from
`constraints/config.mk`, resolve `PLATFORM_DIR`, locate `6_final.*`, run OpenROAD).

```
scripts/flow/run_labels.sh <project-dir> [platform]         # entry point (flow stage, fail-soft)
scripts/flow/resolve_platform_paths.sh <config.mk> <plat>   # make-eval resolver, glob fallback
scripts/extract/labels/extract_congestion.py                # DEF+tech.lef -> per-cell congestion
scripts/extract/labels/extract_wirelength.py                # DEF -> per-net Manhattan WL
scripts/extract/labels/extract_timing.tcl                   # ODB+liberty -> per-cell slack/delay
scripts/extract/labels/extract_irdrop.tcl                   # ODB -> per-cell IR drop (PDNSim)
scripts/extract/labels/compute_label_stats.py               # 4 CSVs -> reports/labels_stats.json
tools/run_labels_batch.sh                                   # subset/full backfill driver
```

### `run_labels.sh` responsibilities

1. Source `_env.sh`; bail if ORFS not found.
2. Read `DESIGN_NAME` and `PLATFORM` from `<project-dir>/constraints/config.mk`
   (platform arg overrides; default `nangate45`).
3. **Locate inputs** from the *collected* backend copy (backfill targets old runs, so the
   live ORFS results dir is unreliable): search the latest
   `<project-dir>/backend/RUN_*/{final,results}/` for `6_final.odb` and `6_final.def`.
   Fall back to the live `$FLOW_DIR/results/<plat>/<design>/<variant>/` if not collected.
   If neither ODB nor DEF exists → record `status=skipped (no backend artifacts)` and exit 0.
4. **Resolve platform values** via `resolve_platform_paths.sh` → `LIB_FILES`, `TECH_LEF`,
   `SC_LEF`, `ADDITIONAL_LIBS`, `ADDITIONAL_LEFS`, `SUPPLY_VOLTAGE`.
5. **Extract clock** `CLOCK_PERIOD` and `CLOCK_PORT` from
   `<project-dir>/constraints/constraint.sdc` (parse `set clk_period` / `set clk_port_name`);
   fall back to `reports/ppa.json` then to a documented default (10.0).
6. Run the four workers into `<project-dir>/labels/{congestion,wirelength,timing,irdrop}.csv`,
   each fail-soft (its own try/skip; a missing input or tool error records a per-label status
   but never aborts the others). DEF-based workers (congestion, wirelength) need the DEF;
   ODB-based workers (timing, irdrop) prefer the ODB.
7. Run `compute_label_stats.py` → `<project-dir>/reports/labels_stats.json`.
8. Print a summary (rows per label, which succeeded/skipped) and exit.

### `resolve_platform_paths.sh` (crux — Approach A)

Primary: invoke ORFS Make with an injected dump target so Make performs the corner
expansion (handles asap7/gf180 `$(CORNER)`/`$(METAL_OPTION)`/`$(PRIMARY_VT_TAG)` correctly):

```bash
make -f "$FLOW_DIR/Makefile" DESIGN_CONFIG="$CONFIG_MK" \
  --eval='__r2g_dump: ; @printf "%s\n" \
     "LIB_FILES=$(LIB_FILES)" "TECH_LEF=$(TECH_LEF)" "SC_LEF=$(SC_LEF)" \
     "ADDITIONAL_LIBS=$(ADDITIONAL_LIBS)" "ADDITIONAL_LEFS=$(ADDITIONAL_LEFS)" \
     "PWR_NETS_VOLTAGES=$(PWR_NETS_VOLTAGES)"' \
  __r2g_dump 2>/dev/null
```

Then parse `PWR_NETS_VOLTAGES` (`VDD <v> ...`) → `SUPPLY_VOLTAGE` (first voltage; default
per platform if empty). **Fallback** if the make dump yields nothing usable: glob
`$PLATFORM_DIR/lib/*typical*.lib` / `*tt*.lib`, `$PLATFORM_DIR/lef/*tech*.lef`,
`$PLATFORM_DIR/lef/*.lef`, and a hardcoded per-platform supply-voltage map. Emits
`KEY=VALUE` lines on stdout; `run_labels.sh` reads them into env.

### Generalization changes to the workers

1. **`extract_congestion.py`** — `parse_tech_lef` currently only recognizes layers whose
   name `startswith("metal")`. Change to recognize any layer declared `TYPE ROUTING`
   (parse the `LAYER <name>` … `TYPE ROUTING ;` block, capture PITCH + DIRECTION).
   Keep `DEFAULT_LAYER_INFO` (nangate) only as a **logged** last-resort fallback when no
   routing layers parse. Everything else (GCELLGRID, COMPONENTS, NETS routing) is already
   platform-agnostic.
2. **`extract_timing.tcl`** — replace the single hardcoded `NangateOpenCellLibrary_typical.lib`
   with a loop over a resolved liberty list (`R2G_LIB_FILES` env, space-separated =
   `LIB_FILES` + `ADDITIONAL_LIBS`). Keep ODB-first (read_db) then `read_liberty` each lib.
   `CLOCK_PERIOD`/`CLOCK_PORT` already env-driven — feed real values from the SDC.
3. **`extract_irdrop.tcl`** — `SUPPLY_VOLTAGE` already env-driven; feed the resolved value
   (default per platform) instead of relying on the 1.1 fallback. Power-net search already
   tries VDD/VPWR — keep.
4. **`extract_wirelength.py`** — no change (pure DEF Manhattan).

All workers keep accepting explicit CLI args (DEF/ODB, output path, design name) so they
remain independently runnable and unit-testable.

### `compute_label_stats.py` — output schema

`reports/labels_stats.json`:
```json
{
  "design": "<name>", "platform": "<plat>",
  "labels": {
    "congestion": {"status":"ok","rows":N,"label":{"min":..,"mean":..,"p50":..,"p90":..,"p95":..,"p99":..,"max":..},
                   "cell_congestion":{...}},
    "wirelength": {"status":"ok","rows":N,"label":{...},"WireLength_um":{...},"signal_nets":N,"masked_nets":N},
    "timing":     {"status":"ok","rows":N,"label":{...},"Path_Delay_ns":{...},"in_path":N,"not_in_path":N},
    "irdrop":     {"status":"ok","rows":N,"label":{...},"IR_Drop_mV":{...},"p95_mV":..,"has_irdrop":bool}
  }
}
```
A label whose CSV is missing/empty records `{"status":"skipped","reason":...}`. Pure stdlib
(csv + statistics); no pandas/numpy dependency.

### `tools/run_labels_batch.sh` — backfill driver

Iterates a design list (explicit list, or auto-discovered designs with a collected
`backend/RUN_*/.../6_final.odb`), runs `run_labels.sh` per design with a concurrency cap
(default modest, e.g. 4 — OpenROAD STA/PDNSim are memory-light vs. KLayout LVS, but still
cap). Writes a per-design log under `design_cases/_batch/logs_labels_<ts>/` and a
roll-up `labels_backfill.jsonl` (design, per-label status, row counts). Honors the hard
rule on not over-parallelizing heavy jobs.

## Documentation & integration

- **SKILL.md** — add step "13b — Label Extraction (dataset building)" after RCX extraction:
  `run_labels.sh` usage, the `labels/` output dir, env knobs
  (`R2G_LIB_FILES`, `TECH_LEF`, `SUPPLY_VOLTAGE`, `CLOCK_PERIOD`, `ODB_FILE`, `DEF_FILE`),
  platform-support note, pointer to the new reference.
- **`references/label-extraction.md`** (new) — per-label semantics, the transforms, the
  `mask_wl`/`in_sta_path`/`has_irdrop` columns, platform handling, the make-eval resolver,
  env knobs, and how to read `labels_stats.json`.
- **CLAUDE.md** — minimal layout touch: note `scripts/extract/labels/`, the
  `design_cases/<d>/labels/` output dir, and `tools/run_labels_batch.sh`.
- **`init_project.py`** — add `labels/` to the created project layout.
- **Tests (`tests/`)** — pytest with small synthetic fixtures:
  - `extract_congestion.py`: generic `TYPE ROUTING` layer parsing (sky130-style `met1`,
    nangate `metal1`) + a tiny routed DEF → expected congestion/label.
  - `extract_wirelength.py`: tiny DEF with `*`-relative points → expected Manhattan WL,
    `mask_wl` correctness.
  - `compute_label_stats.py`: known CSVs → expected percentiles/tallies, skip handling.
  The `.tcl` workers need OpenROAD → validated empirically by the subset backfill.

## Validation & execution plan

1. Validate on `aes_core` (RUN_2026-04-12) and `picorv32_core` — both nangate45, both have
   collected `6_final.odb` + `6_final.def`. Confirm all four CSVs populate and
   `labels_stats.json` is sane.
2. If a non-nangate completed design exists, validate one (sky130hd) to exercise the
   resolver's corner handling; otherwise note nangate-only empirical coverage.
3. Subset backfill of ~20–50 completed designs via `tools/run_labels_batch.sh`; review the
   roll-up for parse failures / skips and fix edge cases.

## Out of scope (deferred)

- Corpus-wide dataset aggregation (concat across designs / parquet).
- Knowledge-SQLite ingest of label stats.
- Dashboard surfacing of label distributions.
- Multi-corner labels (only the typical/primary corner is extracted).

## Risks / open items

- `make --eval` dump cost (~1s/design) and reliance on a parseable `config.mk`; mitigated
  by the glob fallback.
- ODB liberty corner: we read the platform's typical/primary corner only.
- Designs that never reached `6_final` (floorplan/route failures) are skipped, not errored.
- Clock period must come from the design's SDC for timing labels to be meaningful; a wrong
  default silently biases `Path_Delay_ns`. Resolver reads the real SDC value.
