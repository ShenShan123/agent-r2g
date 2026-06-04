# Plan — Absorbing OpenSpace Levers into `r2g-rtl2gds` (2026-06-03)

> **Origin.** Produced by a multi-agent analysis of `/proj/workarea/user5/OpenSpace`
> (HKUDS OpenSpace, a self-evolving *skill framework* for general agents) mapped onto the
> `r2g-rtl2gds` EDA skill. Five OpenSpace levers were profiled, each mapped onto r2g's
> current architecture by a roadmap synthesizer and stress-tested by an adversarial skeptic.
> The skeptic rejected most code-level ports as cargo-culting a general-agent marketplace into
> a deterministic, signoff-gated, single-skill flow. What survives is **three defensible wins**,
> the first of which is a verified latent defect, not a port.
>
> **Status:** APPROVED for implementation (all three wins). Rejected levers are intentionally
> *not* catalogued here (operator decision 2026-06-03).

---

## The meta-finding

OpenSpace's transferable spine is *"build self-improvement and observability as deterministic,
read-only projections over your own structured outcomes, gated by exactly-once and
structural-admission discipline."* **r2g already implements EDA-shaped versions of nearly all of
it** — BM25 failure search (`knowledge/search_failures.py`), the rule-screen→confirm proposal loop
(`ingest_run` → `analyze_execution`, never auto-applied), quality monitoring
(`monitor_health.py`, explicitly "OpenSpace-inspired"), content-hash idempotent ingest, and a
`config_lineage` diff chain. So the high-value move is **not** porting OpenSpace; it is using its
observability lens to find and fix where r2g's own loop is broken.

## Verified findings (independently confirmed against `knowledge/runs.sqlite`, 2026-06-03)

| Fact | Evidence |
|---|---|
| Learning loop is **inert** | `heuristics.json` = `"families": {}`; `suggest_config`'s learned-override never fires |
| Zero learnable runs | **747/750** runs `orfs_status='partial'`, 3 `unknown`, **0 `pass`**; `_is_success()` gates on `pass` |
| Root cause | `ingest_run.py::_derive_orfs_status` (~line 96) requires **all 6** stage names in `stage_log.jsonl`; most runs lack a complete stage log |
| Discarded signal | **607** LVS-clean, **417** DRC-clean (+264 `clean_beol`), **699** RCX-complete — real outcomes never learned |
| Family fragmentation | `infer_family` collapses **309** designs onto `split('_')[0]` junk prefixes |
| Write-only records | `config_lineage` (76 rows) and `monitor_health.py` — grep-confirmed **no script reads them** |

---

## Win 1 — Repair the dead learning loop  🟢 highest value · effort S–M

**Goal:** make `heuristics.json` non-empty so `suggest_config`'s learned override fires, by
learning from the signoff signal that already exists. This is the unlock; Wins 2–3 have nothing to
operate on until it lands.

**Branch:** `fix/dead-learning-loop`

**Changes**
- `r2g-rtl2gds/knowledge/ingest_run.py` — in `_derive_orfs_status`, treat a run that produced a
  final GDS/ODB but has an incomplete `stage_log.jsonl` as `pass` for signoff-learning purposes
  (do **not** fabricate stage rows; derive `pass` from the presence of the final artifact +
  clean signoff). Re-ingest is idempotent, so this reclassifies historical rows on re-run.
- `r2g-rtl2gds/knowledge/learn_heuristics.py` — alternatively/additionally, allow learning from
  DRC/LVS/RCX-clean signal **independent** of full 6-stage completeness, so the 747 partials with
  real clean signoff stop being silently excluded by `_is_success`.
- `r2g-rtl2gds/knowledge/families.json` — **conservatively** curate mappings/patterns for the
  high-population families (`top`/`axis`/`axi`/`axil`/`eth`/`i2c`/`spi`/`uart`/`udp`) so
  `infer_family` stops fragmenting the corpus. Keep `split('_')[0]` as the fallback for truly
  unmapped names.
- `r2g-rtl2gds/tests/` — add coverage: reclassification of a partial-with-final-GDS run to
  learnable; `families.json` curation maps the intended designs and nothing else; a learned median
  never violates a safety clamp.

**Guardrails (non-negotiable)**
- Keep `MIN_SUCCESSFUL ≥ 3`.
- Keep the design-type safety clamps as a **hard post-filter** on any learned median:
  `PLACE_DENSITY_LB_ADDON` floor **0.10**, `bus_heavy → 15`, large-design safety flags
  (per `CLAUDE.md` hard rules). Reclassifying partials must never push `suggest_config` below
  these floors — validate by diffing `suggest_config` output before/after on a representative
  design and asserting no clamp is violated.
- Curate `families.json` by **floorplan/congestion behavior**, not name tokens. Merging
  `axi_crossbar` with `axi_uart` would pollute per-family medians.
- **REJECTED here:** the `infer_family` fuzzy/Jaccard near-neighbor fallback — name-token
  similarity ≠ design-behavior similarity, and silent near-neighbor borrowing of safety-critical
  medians is exactly what the hard rules forbid. Exact mapping + honest coarse fallback is safer.

**Done when:** re-ingest + `learn_heuristics.py` produces a non-empty `families` block with
≥3-success families, all clamps hold, and tests pass.

---

## Win 2 — Read-only observability projection  🟢 cheap · zero correctness risk · effort M

**Goal:** absorb OpenSpace's one genuinely transferable idea — *"observability as a pure read-only
projection over the system's own persisted records, no new instrumentation"* — by surfacing the
currently write-only `config_lineage`, `runs.sqlite`, and `monitor_health.py` in the existing
static dashboard. This is the diagnostic that would have *screamed* "747/750 partial, heuristics
empty" on every build.

**Branch:** `feat/knowledge-observability` (after Win 1, so the panels render live data)

**Changes**
- `r2g-rtl2gds/scripts/reports/build_lineage_view.py` (NEW) — read-only projection over
  `config_lineage` + `runs` → per-design/platform config-tuning provenance chains (previous→current
  with `diff_json` and the resulting `orfs/timing/drc/lvs` outcome deltas). BFS over
  `previous_run_id`/`current_run_id`. Emits JSON for the dashboard.
- `r2g-rtl2gds/scripts/dashboard/generate_multi_project_dashboard.py` (MODIFY) — add:
  - a **"Knowledge health"** strip: total runs, %partial/unknown, learnable family/platform pairs
    (≥3 success), `heuristics.json` populated yes/no — sourced from `runs.sqlite` +
    `monitor_health.py`.
  - a **"Tuning provenance"** panel rendering `build_lineage_view.py` output.
- `r2g-rtl2gds/tests/test_build_lineage_view.py` (NEW) — golden test: pure-read, deterministic
  over a fixture DB.
- `r2g-rtl2gds/SKILL.md` (MODIFY) — document the two panels in the "Generate the dashboard" step.

**Guardrails**
- Open the DB **read-only**: `sqlite3.connect("file:...?mode=ro", uri=True)`. The projection writes
  only JSON for the dashboard.
- **Strictly descriptive** ("what config changed → what outcome"). State an explicit invariant in
  `SKILL.md` that it is **never** wired into `suggest_config` as an auto-tuner.
- Label the config-variant lineage as a **loose single-parent diff chain**, not a true DAG.

**Done when:** a dashboard build shows the health strip + provenance panel from live data, the
projection is read-only and deterministic, and tests pass.

---

## Win 3 — Heuristics payoff A/B harness  🟡 conditional on Win 1 · effort M

**Goal:** close the open loop by **proving** `suggest_config`'s learned config actually beats the
naive `params_by_size` baseline at equal-or-better signoff. Absorbs the *architecture* of
OpenSpace's GDPVal harness (frozen unit set + paired arms + per-unit cost + independent quality +
deterministic diff) — **not** its code (no tokens, no LLM rubric, no payment cliff).

**Branch:** `feat/heuristics-payoff-eval` (dedicated; only meaningful once Win 1 makes
`heuristics.json` non-empty)

**Changes**
- `r2g-rtl2gds/knowledge/eval_set.json` (NEW) — frozen, version-pinned list of representative
  `design_name`+`platform` pairs, **one per dominant family** (keep tiny; hours per run).
- `r2g-rtl2gds/knowledge/suggest_config.py` (MODIFY) — add a `--no-learned` flag (or
  `use_learned=False`) that cleanly bypasses the learned-override block, so the *same* recommender
  emits both arms. The **only** difference between arms must be config provenance.
- `r2g-rtl2gds/knowledge/eval_heuristics.py` (NEW) — emit naive vs learned `config.mk` per eval-set
  design; after the operator runs both arms via `batch_flow.sh`, join arm-A vs arm-B by
  `design_name`+`platform`, compute per-stage wall-clock/CPU-hours/peak-RAM deltas (reuse
  `build_run_compare.py`'s `(a-b)/a*100`) plus a signoff quality-delta block. Persist
  `eval_results.jsonl` incrementally + a pure-aggregate `eval_summary.json`.
- (optional) `r2g-rtl2gds/schema.sql` + `ingest_run.py` — add nullable `eval_arm`
  (`naive|learned|NULL`) column so paired runs self-identify.
- (optional) dashboard card for `eval_summary.json`.

**Guardrails**
- **Cost = CPU-hours / peak-RAM**, not tokens (no LLM in the inner loop). Prefer CPU-hours over
  wall-clock; run on the **quiesced 96-core host**; median of repeats if noisy.
- **Quality = signoff pass/fail on structured JSON** (`orfs pass` ∧ DRC clean ∧ LVS clean/symmetric
  ∧ RCX complete) + violation counts. No LLM evaluator, no 0.6 payment cliff.
- Report a win **only** when cost drops **AND** quality is held-or-improved; explicitly flag any
  "cheaper but signoff regressed" design.
- The harness must **attribute which knob changed** — if the safety clamps fire identically in both
  arms, a real win can look like a no-op.
- `eval_summary.json` is a deterministic re-aggregate over `eval_results.jsonl`, **never**
  hand-computed.

**Done when:** running the frozen set yields an `eval_summary.json` whose headline (% CPU-hours
saved at held-or-better signoff) is reproducible from the jsonl, on a quiesced host.

---

## Sequencing

Order by **data-dependency**, not just `(value × fit) / effort` — Wins 2 and 3 operate on
heuristics/lineage data that is empty or dead until Win 1.

1. **`fix/dead-learning-loop`** (Win 1) — ship first as one focused PR. Re-run ingest +
   `learn_heuristics`, confirm non-empty heuristics + ≥3-success families, validate clamps, add
   tests. **This is THE unlock.**
2. **`feat/knowledge-observability`** (Win 2) — wire the read-only projections + health strip now
   that there is live data to render.
3. **`feat/heuristics-payoff-eval`** (Win 3) — isolated dedicated branch; preconditions are
   non-empty heuristics (Win 1) and a quiesced host.

## Post-implementation (per repo convention)
- Update `r2g-rtl2gds/knowledge/README.md` (loop is now live; document the health/lineage
  projections and the payoff eval).
- Update `CLAUDE.md` knowledge-store note (supersede "Phase-2 only, no version DAG yet" framing as
  appropriate) and the "When You Fix a Bug" workflow if touched.
- Ingest into the knowledge store and re-run `learn_heuristics.py`; record the fix in
  `references/failure-patterns.md` / `lessons-learned.md` and update memory
  `project_dead_learning_loop`.
