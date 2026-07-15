# R2G Recipe Lifecycle Audit

Date: 2026-07-14  
Repository: `/home/yangao/r2g-skills` (audit author's checkout; fixes applied in
`/proj/workarea/user5/agent-r2g`)  
Commit tested: `385b44c4771dcf45c225590fb0d783b2dad51c8d`  
Probe artifacts:

> **RESOLUTION 2026-07-14 (this repo, HEAD `8fbba2a`+): all five findings independently
> re-confirmed against the live tree and FIXED TDD.** Fast index (detail in the per-finding
> "Resolution" blocks and `signoff-loop/references/failure-patterns.md` #48, Patterns 17-21):
>
> | Finding | Verified | Fix | Guard test |
> |---|---|---|---|
> | P0-1 win w/ incomplete provenance promotes | ✅ | `judge_recipe` excludes rows with `provenance_complete==false` (absent=legacy, countable) | `test_ab_runner.py::test_incomplete_provenance_win_does_not_promote` |
> | P0-2 missing `recipe_status` = promoted | ✅ | `filter_promoted` fails closed (`default=UNROSTERED`) + `ensure_rostered` coverage + atomic heuristics write | `test_recipe_lifecycle.py`, `test_learn_recipes_indexed.py::test_learn_rosters_every_recipe_key` |
> | P0-3 arms inherit post-repair config | ✅ | `_reset_arm_config_baseline` strips the auto-block from each arm; stamps `baseline_config_sha` | `test_plan_arms_isolation.py::test_plan_arms_resets_arm_config_to_pre_recipe_baseline` |
> | P1-1 `mean_outcome_score` order-dependent | ✅ | latest-ingested run per path (`ROW_NUMBER() … ORDER BY ingested_at DESC, run_id DESC`) | `test_learn_recipes_indexed.py::test_mean_outcome_score_is_deterministic_latest_run` |
> | P1-2 route can't reorder learned recipes | ✅ (documented single-strategy) | doc + self-announcing guard warns if the route catalog grows >1 strategy | `test_route_ab_loop.py::test_route_live_path_is_single_strategy_and_guards_growth` |
>
> Non-disruptive by construction: the committed `knowledge.sqlite` has **0** decisive trials
> explicitly `provenance_complete=false` and **0** unrostered concrete recipe keys, so P0-1/P0-2
> move no existing verdict; the store was not hand-mutated and the 5 HARD honesty gates stay green.
> Full suite: **853 passed, 2 skipped**.

- Probe script: `/home/yangao/r2g-skills/tools/audit_recipe_lifecycle.py`
- Probe output: `/home/yangao/r2g-skills/tools/audit_recipe_lifecycle_results.json`

The current checkout was already up to date with `origin/main` before testing. The production knowledge store was not modified by this audit; all probes used temporary SQLite databases and temporary project directories.

## Executive Summary

The audit reproduced two P0 fail-open behaviors, one A/B experimental-design risk, one deterministic-learning bug, and one live-route-ranking gap.

P0-1 is confirmed: a decisive A/B `win` with `provenance_complete=false` still promotes the recipe. The code warns, but promotion logic counts the unverifiable win.

P0-2 is confirmed: if candidate enqueue fails after heuristics are written, the missing `recipe_status` row is treated as `promoted`, so live execution can trust an unvalidated recipe.

P0-3 is a confirmed mechanical risk: A/B arms are copied from the current subject directory. If the subject is already a post-repair design, both arms initially inherit that repaired config, so arm A is not a clean pre-recipe baseline. Real historical subjects in the current knowledge DB point to paths that are not present on this server, so this was reproduced synthetically rather than on an old live subject.

P1-1 is confirmed: multiple `runs` rows with the same `project_path` and different `outcome_score` values collapse to a single dict entry. The selected value changes with insertion order and is not the arithmetic mean.

P1-2 is partially confirmed as a live integration gap: the ranking core can reorder strategies, but the live `--check route` path does not load indexed recipes and the route catalog currently emits only one strategy, `route_relief`. Therefore learned ranking cannot currently change live route execution order between two route recipes.

## P0-1: Incomplete-Provenance Win Still Promotes

### Probe

The probe created a candidate recipe, then recorded an A/B trial with:

- `verdict="win"`
- `arm_a_run_id=None`
- `arm_b_run_id=None`
- empty input metrics, forcing the code to stamp `provenance_complete=false`

### Observed Result

The recipe was promoted:

```json
{
  "observed_status": {
    "status": "promoted",
    "provenance": "ab_corpus:1w0l"
  },
  "trial_metrics": {
    "provenance_complete": false
  }
}
```

The code also printed the expected warning:

```text
WARNING: decisive A/B trial for probe_recipe (probe_sym_p0_1) lacks distinct arm run_ids ...
```

### Root Cause

`ab_runner.record_trial()` stamps `provenance_complete=false` and prints a warning for decisive unverifiable trials, but it still writes the trial and immediately calls `judge_recipe()` (`ab_runner.py:370-406`). `judge_recipe()` counts all `win` and `loss` rows and does not filter on `metrics_json.provenance_complete` (`ab_runner.py:406+`).

### Recommendation

Do not allow incomplete-provenance decisive evidence to drive lifecycle transitions. Reasonable fixes:

- Convert `win`/`loss` with incomplete provenance into `inconclusive` before insert.
- Or keep the raw verdict, but make `judge_recipe()` count only rows where `metrics_json.provenance_complete=true`.
- Add a regression test that inserts a `win` with missing or identical arm run IDs and asserts that `recipe_status` remains `candidate`.

**Resolution (2026-07-14):** took option 2 — `judge_recipe` (`ab_runner.py`) now counts a decisive
row ONLY when its `metrics_json.provenance_complete` is not explicitly `false` (absent key =
legacy pre-#45 trial, grandfathered as countable). `record_trial` still writes the honest row +
warning. Chosen because the committed store has 0 explicit-`false` rows, so no historical verdict
moves; treating "absent" as incomplete would instead un-count 59 wins + 18 losses and regress the
store. Tests: `test_incomplete_provenance_{win_does_not_promote,loss_does_not_demote}`.

## P0-2: Missing recipe_status Row Is Fail-Open

### Probe

The probe simulated this sequence:

1. Heuristics contain a learned strategy.
2. Candidate enqueue fails or is skipped, so `recipe_status` has no row for that key.
3. Live filtering asks whether the strategy is promoted.

### Observed Result

The strategy was treated as promoted:

```json
{
  "observed_get_status": "promoted",
  "observed_filter_kept": ["enqueue_failed_recipe"],
  "recipe_status_rows": 0
}
```

### Root Cause

`recipe_lifecycle.py` defines absent rows as promoted:

- The file-level contract says `Absent row = promoted` (`recipe_lifecycle.py:12-13`).
- `GRANDFATHERED = "promoted"` (`recipe_lifecycle.py:20`).
- `get_status()` returns `GRANDFATHERED` when no row exists (`recipe_lifecycle.py:109-114`).

This is understandable for legacy pre-lifecycle recipes, but it also makes a newly learned recipe fail open if `diff_and_enqueue()` writes heuristics successfully but candidate enqueue does not complete.

### Recommendation

Separate legacy grandfathering from newly generated recipes.

Options:

- Add a migration or allowlist for true legacy recipe keys that are allowed to be absent-promoted.
- For current-generation heuristics, require an explicit lifecycle row before live promotion.
- Add a learner health gate: after `learn_heuristics.learn()`, verify every concrete non-rollup recipe key has a `recipe_status` row or an explicit legacy marker.
- If enqueue fails, make live filtering fail closed for that generation instead of silently trusting the recipe.

**Resolution (2026-07-14):** three layers, all in `recipe_lifecycle.py` + `learn_heuristics.py`.
(1) `get_status` gained a `default` param; `filter_promoted` (the LEARNED indexed-recipe consumer)
passes `default=UNROSTERED` so an absent row FAILS CLOSED. The STATIC cold-start path
(`_annotate_live_gates`) keeps the `promoted` default — grandfathering is load-bearing there (a
baseline strategy must run on a novel symptom), so a blanket flip would have killed the fix loop.
(2) `learn()` writes heuristics atomically (tmp+rename) and calls new `ensure_rostered()` after
`diff_and_enqueue`, rostering any still-missing key as a CANDIDATE (never fabricated promoted).
(3) `unrostered_keys()` is the coverage invariant (0 on the committed store). Safe because all 120
concrete keys already have rows, so the fail-closed flip changes nothing live — only the crash case.

## P0-3: A/B Arms Can Inherit Post-Repair Config

### Probe

The probe built a synthetic subject whose original baseline was:

```make
export CORE_UTILIZATION = 25
```

Then it simulated a post-repair subject already containing:

```make
export CORE_UTILIZATION = 17
```

It enqueued `density_relief`, planned A/B arms, and compared arm A, arm B initial config, and the subject config.

### Observed Result

Both arms initially inherited the subject's post-repair config:

```json
{
  "arm_a_inherits_post_fix_config": true,
  "arm_b_inherits_post_fix_config_before_apply": true,
  "synthetic_appended_arms": 2
}
```

### Root Cause

`plan_arms_for_candidates()` creates A/B arm directories using `shutil.copytree(src, dst, ...)` from the current subject project directory (`engineer_loop.py:1441-1462`). The copy excludes backend/signoff artifacts, but it does not reconstruct the pre-repair config baseline. Therefore, if the chosen subject directory is already repaired, arm A is also repaired before the trial begins.

This can make the control arm too strong or make both arms partially treated, which weakens the causal interpretation of an A/B verdict.

### Recommendation

Make A/B arms start from a recorded pre-recipe baseline:

- Store a baseline `config.mk` snapshot or config hash before applying each strategy.
- When planning arms, reconstruct both A and B from the same baseline snapshot.
- Apply the candidate recipe only to arm B.
- Record `baseline_config_hash`, `arm_a_config_hash`, and `arm_b_config_hash` in the ledger or `ab_trials.metrics_json`.
- Add an assertion that arm A does not contain the candidate's config delta at trial start.

**Resolution (2026-07-14):** `engineer_loop._reset_arm_config_baseline(dst)` strips the
`# >>> r2g signoff-fix (auto) >>>` block (canonical markers imported from
`diagnose_signoff_fix`, so strip and apply can't drift) from each arm's `config.mk` right after the
copytree, restoring the human-authored PRE-recipe baseline; each arm then re-derives its own edits
at fix time. Every `ab_arm` ledger entry records `baseline_config_sha`. The synthetic probe used a
BARE `export` — the REAL fixer writes `config_edits` strategies (density_relief/route_relief/
antenna) INTO the auto-block, so the strip fixes the live manifestation. Documented limitation:
place/synth backend-abort relief writes bare exports (not the block), but those subjects self-limit
as A/B subjects. Test: `test_plan_arms_resets_arm_config_to_pre_recipe_baseline`.

## P1-1: mean_outcome_score Uses One Arbitrary Row per project_path

### Probe

The probe inserted two runs with the same `project_path` but different `outcome_score` values, then rebuilt heuristics twice with reversed insertion order:

- Case A: `0.1`, then `0.9`
- Case B: `0.9`, then `0.1`

### Observed Result

The learned `mean_outcome_score` changed with row order:

```json
{
  "observed_order_0_1_then_0_9": 0.9,
  "observed_order_0_9_then_0_1": 0.1,
  "arithmetic_mean_should_be": 0.5
}
```

### Root Cause

`learn_heuristics.py` builds:

```python
score_of = {r[0]: r[1] for r in conn2.execute(
    "SELECT project_path, outcome_score FROM runs ..."
)}
```

This collapses multiple rows for the same `project_path` into one dict entry (`learn_heuristics.py:542-545`). The subsequent recipe projection appends only that selected score (`learn_heuristics.py:448-470`) and then computes a mean over the remaining one-value list (`learn_heuristics.py:475-478`).

### Recommendation

Make the aggregation explicit and deterministic.

Possible choices:

- If the intended signal is the latest run, query with `ROW_NUMBER() OVER (PARTITION BY project_path ORDER BY julianday(ingested_at) DESC, run_id DESC)` and document "latest outcome_score".
- If the intended signal is project-level average quality, use `AVG(outcome_score) GROUP BY project_path`.
- If instability should be reflected, carry both `mean_outcome_score` and `n_outcome_scores`.

Add a regression test with two rows for the same `project_path` and verify the documented aggregation result.

**Resolution (2026-07-14):** took the "latest run" semantics (`ROW_NUMBER() OVER (PARTITION BY
project_path ORDER BY julianday(ingested_at) DESC, run_id DESC)`, `rn=1`) — consistent with the
codebase's "latest-ingested row per project is canonical" rule (ingest/repair already touch only
that row). `julianday` parses both the `Z` and numeric-offset `ingested_at` regimes; `run_id DESC`
breaks ties. `mean_outcome_score` is an advisory ranking tiebreaker, so it self-heals on the next
`learn()`. Test: `test_mean_outcome_score_is_deterministic_latest_run` (inserts both orders → 0.9
either way).

## P1-2: Learned Ranking Does Not Currently Reorder Live Route Execution

### Probe

The probe first verified that the ranking core itself can reorder two synthetic strategies:

```json
{
  "ranking_core_order": ["route_relief", "density_relief"]
}
```

However, the live route plan without manual recipe injection produced:

```json
{
  "live_route_plan_without_recipe_lookup": ["route_relief"],
  "route_catalog_strategy_count": 1
}
```

### Root Cause

There are two separate issues:

1. In `diagnose_signoff_fix.main`, the `--check route` path explicitly sets `recipes=None` and does not call `load_indexed_recipe()` (`diagnose_signoff_fix.py:865-871`). That means learned indexed recipes are not loaded for route live ranking.
2. `_route_strategies()` currently returns a single static candidate, `route_relief` (`diagnose_signoff_fix.py:204-214`). With only one live strategy, there is no execution order to reorder.

The core ranker is not the failing part. The live route path lacks a multi-strategy learned-ranking integration point.

### Recommendation

If route symptoms are expected to support multiple learned recipes:

- Define a canonical route symptom lookup, likely `check=orfs_stage`, `class=route`, and load indexed recipes for that key.
- Add at least two route-relevant strategy IDs to the live route catalog or a route recipe catalog.
- Run lifecycle filtering on route recipes before live use, just like DRC/LVS.
- Add a live diagnosis regression test where two route strategies are present and the learned ranking changes the first auto-applied strategy.

If route is intentionally single-strategy for now, document that P1-2 is not applicable to live route ordering until the route catalog grows beyond `route_relief`.

**Resolution (2026-07-14):** took the "intentional single-strategy" option — route_relief is the
SOLE live route fix and is deliberately NOT lifecycle-stripped (demoting the only route fix would
leave route failures unfixable), and learned route ranking rides the learner→heuristics
`check=orfs_stage/class=route` path, not this live reader. Documented that intent in
`diagnose_signoff_fix.main` and added a SELF-ANNOUNCING guard: the live route path now WARNS loudly
the moment `_route_strategies` emits >1 strategy, at which point indexed ranking + lifecycle
filtering must be wired here like drc/lvs. So the gap cannot silently persist once the catalog
grows. Test: `test_route_live_path_is_single_strategy_and_guards_growth`.

## Suggested Priority

1. Fix P0-1 first: incomplete-provenance decisive trials should not promote.
2. Fix P0-2 next: newly learned recipes should fail closed if lifecycle status is missing.
3. Fix or explicitly constrain P0-3 before using A/B results as strong causal evidence.
4. Fix P1-1 to make recipe ranking reproducible.
5. Clarify P1-2's intended scope: either add live route recipe ranking or document route as a single-strategy path.

## Current Git State Notes

Before this audit, the repository already had runtime modifications in:

- `r2g-skills/signoff-loop/knowledge/heuristics.json`
- `r2g-skills/signoff-loop/knowledge/knowledge.sqlite`

This audit added:

- `tools/audit_recipe_lifecycle.py`
- `tools/audit_recipe_lifecycle_results.json`
- `docs/2026-07-14-recipe-lifecycle-audit.md`

The audit did not revert or modify the pre-existing runtime knowledge changes.
