# r2g-rtl2gds — Learning-Loop-Closure Audit & Fixes

**Date:** 2026-06-23
**Trigger:** Resume the nangate45 signoff campaign + verify the engineer-learning-loop is
*actually* learning from failures and **promoting** good recipes.
**Method:** adversarial multi-agent audit (6 finders → 2-lens verify → synthesis) over the
recently-churned loop surfaces (`df01923` fail-closed gate, `d29abae` FLW-0024 recovery,
`f922b02` A/B planner, `d194ed4`/`27ac77f` knowledge_sync + revert).
**Status:** 8 of 9 confirmed bugs FIXED (TDD, suite 730→743 green); 1 DEFERRED (#9b, below).
Changes UNCOMMITTED on `main` (operator commits when ready).

---

## Verdict before fixes: **DEGRADED** (the loop lied by omission)

The loop honestly recorded failures (`135 fail runs == 135 orfs-fail events`) and *ran* A/B
trials (`ab_trials=24`), but was **structurally unable to PROMOTE an entire class of
genuinely-good recipes** — every nangate45 signoff recipe. Across 8 campaign waves
(2026-06-20→23) `fix_events` grew 705→825 and `ab_trials` 14→22, but **`promoted` stayed flat
at 2 — both sky130hd**. No nangate45 recipe ever promoted. That is the precise meaning of
"machinery shipped but the loop is inert" the CLAUDE.md Gate-A invariant warns about.

### Root-cause chain (each bug compounds the next)
1. **#1 (CRITICAL)** — A/B arms did *byte-identical* work. `plan_arms_for_candidates`
   copied each arm dir ignoring only `backend/`+`*.gds`, so a signoff arm inherited the
   subject's **clean** `reports/drc.json`; `process_one` read that stale verdict and
   short-circuited to `_mark_clean` **before the fixer ran** → arm A's `R2G_FIX_EXCLUDE` and
   arm B's `R2G_FIX_RANK_FIRST` never took effect. DB proof: every nangate45 antenna trial had
   both arms `is_success=true, outcome_score=0.8833` identical, differing only in `wall_s`.
   sky130hd promoted only because its subjects were *failures* (no clean report to copy).
2. **#2** — with arms identical, `judge_repeated` tied on success and fell to a flat **±2%
   raw wall-clock** tiebreak with no variance gate → trial 15 "win", trial 16 "loss" on <12s
   of jitter. Pure noise decided the lifecycle.
3. **#9** — even a real win couldn't accumulate: `design_class` is in the lifecycle key but
   isn't stable — an FLW-0024 place-abort re-ingest has `cell_count=NULL` → size band flips
   `logic/unknown`↔`unknown/unknown` → the candidate *respawns* under the new key while the old
   verdict strands. Gate B never compounded.
4. **#8** — candidates with <2 resolvable subjects were `continue`'d silently forever.
5. Plus **#3** (junk `unknown` arm rows clobbered passing arms → false losses), **#6**
   (FLW-0024 resize recorded ZERO fix_events → unlearnable), **#4/#5** (fail-open exit gate on
   a missing report), **#7** (stale doc).

---

## Fixes applied (TDD; tests in `tests/test_loop_closure_audit_fixes.py` + extensions)

| # | Sev | File | Fix | Test |
| - | --- | ---- | --- | ---- |
| 1 | critical | `scripts/loop/engineer_loop.py` | copytree also ignores `reports`; `process_one` never short-circuits a `kind=='ab_arm'` to clean → arms diverge | `test_signoff_ab_arm_always_runs_fix_despite_clean_report`, `test_plan_arms_copytree_excludes_reports` |
| 2 | high | `knowledge/ab_runner.py` | success-tie wall-clock tiebreak is variance-aware (combined-stderr; `<2` repeats → inconclusive); new `ucb()` | `test_judge_subnoise_wall_tie_is_inconclusive`, `_single_sample_*`, `_robust_cost_*`, `_success_rate_win_*` |
| 3 | high | `engineer_loop.py` | `_ingest` skips no-backend+no-ppa (no junk `unknown` row); route arm escalates `route_arm_incomplete`; judge skips all-None pairs; new `_has_backend_run()` | `test_ingest_skips_*`, `test_route_arm_with_no_backend_escalates_*` |
| 4/5 | medium | `scripts/flow/fix_signoff.sh` | exit gate scoped to the requested check; missing/unreadable active report → residual (rc=2) | `test_missing_required_report_is_not_clean`, `test_unreadable_required_report_*` |
| 6 | high | `engineer_loop.py` | FLW-0024 resize records a `fix_log` row (`core_util_relief`/`orfs_stage`/`place`) → fix_event → trajectory → recipe, making the recovery **VISIBLE to learning** (`process_one` returns terminal status; new `_record_resize_fix()`). **Scope (per review):** *learnable, not A/B-promoted* — the resize is sticky/hard-coded and its A/B control is hard to reconstruct, so place-recipe **promotion is deferred with #9b**. | `test_process_one_resizes_*` (extended), `test_resize_that_does_not_recover_records_no_change` |
| 8 | high | `engineer_loop.py` | unvalidatable candidate logs + opens idempotent `unvalidatable_insufficient_subjects` escalation (NOT demoted — terminal) | `test_unvalidatable_candidate_opens_escalation` |
| 9a | high | `knowledge/ingest_run.py` | design_class size band pinned from prior non-null `cell_count` when this run is NULL; stored `cell_count` stays honestly NULL | `test_reingest_with_null_cellcount_keeps_stable_design_class` |
| 7 | low | `SKILL.md` | doc: knowledge_sync `status` is not the CI gate; `honesty.py` is | — |
| — | — | `knowledge/escalations.py` | added REASONS `route_arm_incomplete`, `unvalidatable_insufficient_subjects` | (covered by #3/#8) |

**Honesty after fixes:** all 5 gates green on the real store (`fail_runs=135==events`,
`ab_trials=24`, no event-on-nonfail, derivable). No honesty invariant touched.

### Post-review hardening (adversarial diff review → 2 blockers fixed before launch)

A 6-agent adversarial review of the diff returned NO-GO; both blockers were correctness
regressions a green suite could not catch, now fixed:

- **BLOCKER #2 fixed (ab_runner.py `judge_repeated`):** the variance-aware tiebreak treated
  `se==0` as "no confidence" and returned `inconclusive` — but zero variance is MAXIMAL
  confidence (a deterministic delta). A robustly-cheaper, jitter-free arm B was demoted to the
  TERMINAL `shadow` state. Fixed: floor the bound at `1% of the combined mean`, so a
  deterministic substantial cost win decides while a trivial delta stays inconclusive.
- **BLOCKER #6 resolved by honest downscoping (not a code lie):** #6 makes the resize
  *learnable* (fix_event recorded) — the audit's actual stated bug — but the place-resize is
  **not A/B-promotable** (sticky hard-coded config; control hard to reconstruct; promotion moot).
  The docstring/comments/tests/plan were corrected to claim only "visible to learning"; place-A/B
  promotion is deferred with #9b. No overclaim remains.
- **Review nits also fixed:** removed dead `ucb()`; corrected `judge_repeated` docstring;
  `judge_finished_trials` now marks an all-None pair `judged` before `continue` (no unbounded
  per-drain re-scan); `learn_heuristics._design_class_by_project` now resolves a deterministic,
  most-recent **non-`unknown`** size band per project (`ORDER BY ingested_at DESC`) so the split
  bands the wipe created don't nondeterministically strand a verdict (complements #9a).

**Known residuals (documented, acceptable for the nangate45 campaign):**
- Multi-strategy DRC symptoms (sky130/gf180/ihp antenna with >1 candidate strategy) tend to land
  `inconclusive`→`shadow` because arm A's single-strategy EXCLUDE lets diagnose pick an
  alternative, so both arms clean at near-equal cost. nangate45 antenna is single-strategy
  (`antenna_diode_repair`), so arm A genuinely fails → arm B wins → promotes. To promote a
  multi-strategy recipe, arm A must EXCLUDE the whole catalog (future work).
- For cost-differentiated recipes, set `R2G_AB_REPEATS=3` so the k≥2 cost tiebreak reliably fires.

---

## DEFERRED — #9b: drop `design_class` from the A/B lifecycle key (needs migration)

**Why deferred:** the structurally-correct fix is to key `recipe_status`/`ab_trials`/lifecycle
on `(symptom_id, platform, strategy)` only (keep `design_class` as `ab_trials.match_level`
descriptive metadata) — the skill's own "fixes transfer by SYMPTOM not family" philosophy. But
it changes a PRIMARY KEY shape on the **committed** knowledge store and needs (a) a one-time
migration merging existing split rows (most-evidenced status wins), (b) caller updates in
`recipe_lifecycle.filter_promoted`/`get_status`, `diagnose_signoff_fix.load_indexed_recipe`,
`learn_heuristics` (emit a class-pooled lifecycle view), (c) tests. Too risky to rush before a
long unattended campaign. **#9a (this pass) removes the dominant trigger** (NULL-cell_count
flip), and **#1 makes antenna/route win on the success-rate path** (B signs off, A does not) —
no cross-class accumulation needed to promote. #9b remains a robustness improvement (heals
existing split rows; survives a legitimate small→medium size reclassification).

**Follow-up plan (own branch, when scheduled):** migrate rows → change keys → update 4 callers
→ regression test (re-ingest class flip must NOT respawn a 2nd candidate) → run honesty.py.

---

## Live-drain findings (2026-06-23/24 — the fixes RUNNING on the real campaign)

The resumed campaign reached its wave-1 candidate drain and ran the flipped recipes' A/B arms.
What the live run PROVED, and an honest nuance:

**PROVEN live (independent of any verdict):**
- **#1 works** — each A/B arm's own `reports/fix_log.jsonl` now contains fixer rows (the fixer
  RAN), instead of the old inherited-clean short-circuit. The arms are no longer identical-by-bug.
- **#3 works** — route_relief / lvs_resolve / core_util arms that produced no backend were
  ESCALATED (8 each), not ingested as junk `orfs_status='unknown'` rows; the judge skips them, so
  they record no false `loss` (the exact bug #3 poisoning is gone).
- **DBs record fix trajectories** — `fix_events` grew 825→951 as wave-1 designs were fixed; clean
  designs 221→253. Honesty gates green throughout.

**Honest nuance — why the flipped nangate45 recipes did NOT promote (correct, not a bug):**
- **antenna_diode_repair** routes correctly to `--check both` (drc), but on a FRESH arm flow the
  subjects produce NO antenna violation (`fix_log` strategy `'none'` on both arms) — nangate45
  antennas are cleared by the flow's built-in router repair (`MAX_REPAIR_ANTENNAS_ITER_DRT`), so
  the diode recipe is REDUNDANT and excluding it changes nothing → honest `inconclusive`. (The
  audit's premise that arm A would *fail* assumed the violation reproduces; it doesn't.)
- **period_relax** symptoms are not even in the `symptoms` table (`symptom=None`) → `_symptom_check`
  returns `both`, and a TIMING recipe is a no-op on a DRC/LVS fix arm → identical arms →
  `inconclusive`. Also `is_success` does NOT gate on timing, so period_relax has no success-rate
  win path either.

**NEW deferred follow-up (A/B harness coverage gap):** the inline A/B arm router (`_symptom_check`
+ `_process_backend_ab_arm`) only properly EXERCISES a recipe whose strategy is applied by the
routed check — i.e. **DRC/LVS** (`--check both`) and **route** (backend arm). **Timing**
(`period_relax`) and **place** (`core_util_relief`, #6) recipes route to `--check both` and are
inert in A/B, so they can never earn a verdict that reflects their actual effect. To make
timing/place recipes promotable, extend `_symptom_check` + add apply-then-flow arm branches
(mirroring route_relief) — bundle with #6 and #9b. The DRC/LVS/route promotion path is verified
working (the two committed sky130hd promotions); it is now unblocked for nangate45 too — a
genuinely-beneficial nangate45 recipe (e.g. route_relief rescuing a congested design) WILL promote
as the campaign's 352 remaining designs surface one.

## Operator reconciliation done this session (data, not code)

The nangate45 antenna_diode_repair + period_relax recipe shadows were demoted by the
**now-known-contaminated** identical-arm trials (bug #1). `diff_and_enqueue` will not re-enqueue
a recipe that already has a `recipe_status` row, so they would stay shadow forever. Per the
audit's #1 fix-assessment ("re-drain after the fix; do NOT rewrite old ab_trials rows"), the
contaminated **shadows were flipped back to `candidate`** (a `recipe_status` current-state
edit; the immutable `ab_trials` history is untouched) so the **fixed** harness re-validates them
in the resumed campaign. EXECUTE+VERIFY: confirm `ab_trials` gains rows with arm A `is_success`
≠ arm B, and a nangate45 recipe transitions `candidate → promoted`.
