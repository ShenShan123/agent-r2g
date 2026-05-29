# Feature-Extraction Stage — Implementation Plan

**Date:** 2026-05-29 · **Branch:** `feat/label-extraction-stage`
**Design:** [`specs/2026-05-29-feature-extraction-stage-design.md`](../specs/2026-05-29-feature-extraction-stage-design.md)

> **REQUIRED SUB-SKILL — TDD.** Every code task is red→green: write/extend the failing
> test, run it to confirm it fails, implement, run to confirm it passes. The **golden
> regression test (Task 2)** is the gate for the entire light-refactor: it asserts the
> eight refactored workers reproduce `feature_test_v2/output/ac97_top/*.csv` byte-for-byte
> on the fixture inputs. No refactor commit lands red.

## Source-material migration (`feature_test_v2/py/` → `scripts/extract/features/`)

| Source | Target | Transform |
|--------|--------|-----------|
| `case_paths.py` | `case_paths.py` | rewrite `resolve_case_paths` to build ctx from `<DEF> <out_csv> <graph_id>` argv + `R2G_*` env (drop the `input/<case>/` model) |
| `lib_db.py` | `lib_db.py` | `load_liberty_db` reads injected `R2G_LIB_FILES`; parameterize tap hook; English strings; keep all classifiers |
| `net_to_pt.py` | `cell_type_map.py` | rename; upper-case `FAKERAM45_*` keys (C9); add `build_runtime_map(lib_db)` for non-nangate platforms; `resolve_cell_type_map(platform, lib_db)` selector |
| `run_all.py` | — | dropped; replaced by `run_features.sh` (the abort-on-first-failure driver is not reused) |
| `metadata.py` | `metadata.py` | use shared `def_parse`; ctx from new resolver; English; SPEF-absent → 0 |
| `nodes_gate.py` | `nodes_gate.py` | `import cell_type_map`; shared `def_parse` |
| `nodes_net.py` | `nodes_net.py` | tech-LEF-derived layer matcher; shared `def_parse`/SDC |
| `nodes_iopin.py` | `nodes_iopin.py` | shared `def_parse`/SDC |
| `nodes_pin.py` | `nodes_pin.py` | shared `def_parse`; SPEF-absent guard |
| `edges_gate_pin.py` | `edges_gate_pin.py` | `import cell_type_map`; shared `def_parse` |
| `edges_pin_net.py` | `edges_pin_net.py` | shared `def_parse`/SDC |
| `edges_iopin_net.py` | `edges_iopin_net.py` | shared `def_parse`/SDC |
| (new) | `def_parse.py` | extract the verbatim-duplicated `parse_units`, `parse_design_name`, net-connection parser, `parse_sdc_clock_port_names`, `_strip_inline_comment` |
| (new) | `compute_feature_stats.py` | 8 CSVs → `reports/features_stats.json` (stdlib) |

## File structure

**Create**
- `r2g-rtl2gds/scripts/extract/features/{case_paths,def_parse,lib_db,cell_type_map,metadata,nodes_gate,nodes_net,nodes_iopin,nodes_pin,edges_gate_pin,edges_pin_net,edges_iopin_net,compute_feature_stats}.py`
- `r2g-rtl2gds/scripts/flow/run_features.sh`
- `tools/run_features_batch.sh`
- `r2g-rtl2gds/references/feature-extraction.md`
- `r2g-rtl2gds/tests/test_feature_regression.py` (golden gate)
- `r2g-rtl2gds/tests/test_feature_parameterization.py` (cell-type map + layer matcher)
- `r2g-rtl2gds/tests/test_compute_feature_stats.py`

**Modify**
- `r2g-rtl2gds/SKILL.md` (flow `13c`, layout tree, Resource Map)
- `CLAUDE.md` (layout lines + Where-to-Find-X)
- `r2g-rtl2gds/references/workflow.md` (Phase 7b)
- `r2g-rtl2gds/scripts/project/init_project.py` (`TEMPLATE_DIRS` += `"features"`)
- `r2g-rtl2gds/tests/conftest.py` (`FEATURES_DIR` sys.path)

## Tasks

- [ ] **Task 1 — shared modules.** Create `case_paths.py` (argv+env ctx), `def_parse.py`
  (deduped helpers), `lib_db.py` (injected liberty + parameterized tap), `cell_type_map.py`
  (nangate map verbatim + runtime map + selector). English strings throughout.
- [ ] **Task 2 — golden regression test (GATE).** `test_feature_regression.py`: skipif the
  fixture is absent; for each of the 8 workers, run as a subprocess with the fixture
  `5_route.def`/`6_final.spef`/`constraint.sdc`/`config.mk` + bundled lib/tech-lef via env,
  write to `tmp_path`, assert md5 == the golden in `feature_test_v2/output/ac97_top/`.
- [ ] **Task 3 — refactor the 8 workers** to import the shared modules and read the new
  ctx, until Task 2 is green. Iterate worker-by-worker; never commit red.
- [ ] **Task 4 — parameterization unit tests + impl.** `test_feature_parameterization.py`:
  (a) `cell_type_map.resolve_cell_type_map("nangate45", …)` returns the curated map and is
  byte-stable; (b) liberty-derived map for a synthetic sky130 liberty is deterministic +
  distinct; (c) the tech-LEF layer matcher counts `met1`/`metal1` correctly and the
  nangate alternation equals the `metal\d+` count on a sample route block.
- [ ] **Task 5 — stats roller + test.** `compute_feature_stats.py` + `test_compute_feature_stats.py`
  (percentiles, skip on missing/empty, `spef_present`, row counts).
- [ ] **Task 6 — `run_features.sh`.** Mirror `run_labels.sh`: locate `6_final.def` + SPEF,
  resolve platform paths, run the 8 workers fail-soft, roll up stats. `DEF_FILE` override.
- [ ] **Task 7 — `tools/run_features_batch.sh`.** Auto-discover + concurrency-capped backfill.
- [ ] **Task 8 — wiring.** SKILL.md, CLAUDE.md, workflow.md, init_project.py, conftest.py.
- [ ] **Task 9 — reference doc.** `references/feature-extraction.md`.
- [ ] **Task 10 — live validation.** Run on a completed nangate45 design from `6_final.def`;
  confirm 8 CSVs populate and keys join to `labels/*.csv`; capture row counts.
- [ ] **Task 11 — self-review + commit.** Run the full pytest suite; adversarial review of
  the diff; commit `feat(skill): add feature-extraction stage (run_features.sh + graph CSVs)`.

## Self-review checklist

- [ ] Golden regression byte-for-byte green; full pytest suite green.
- [ ] No 3rd-party imports (stdlib only); `python3` interpreter.
- [ ] Fail-soft: missing SPEF / missing liberty / one worker crash never aborts the others.
- [ ] `resolve_platform_paths.sh` reused unmodified; `run_labels.sh` untouched.
- [ ] nangate45 behavior unchanged; non-nangate paths exercised by unit tests.
- [ ] Docs/wiring point at real file paths; layout trees updated.
