---
description: Resume the Nangate45 RTL→GDS sign-off campaign in parallel waves, hunt r2g-rtl2gds skill bugs, and prove the engineer-learning-loop is closed (DRC/LVS clean + best Fmax + promoted recipes).
argument-hint: "[overrides, e.g. WAVE_MAX=24 WORKERS=3 NUM_CORES=4 PLATFORM=nangate45]"
---

# /r2g-debug — Drive, debug, and PROVE the r2g-rtl2gds learning loop

You are debugging the `r2g-rtl2gds` skill by running a **real, parallel, wave-batched
Nangate45 sign-off campaign** over the RTL designs in this project, and using that campaign
as the test harness that surfaces skill bugs and proves the closed learning loop works.

**Mission (do all of these — they are one connected goal, not a menu):**
1. Resume the designs in this project through the **Nangate45 sign-off flow** using the
   *newest* version of the skill (the canonical `r2g-rtl2gds/` tree, freshly symlink-deployed).
2. **Batch the RTL designs into waves** and run them **in parallel to fully use the CPUs**
   (respecting the shared-host hard rule below — do not oversubscribe).
3. For every design: ensure **DRC and LVS pass**, and **search for the best Fmax**.
4. **Find bugs in the skill.** A campaign that runs without surfacing/fixing a real defect is
   suspicious — the loop is only as honest as its weakest writer. Treat every `fail` row, every
   misclassification, and every honesty-gate miss as a lead.
5. Prove the **engineer-learning-loop is well closed**: the skill is *actually learning* from
   **both failure and success** trajectories of the iterative fix actions — not just shipping
   machinery. The two memory DBs must record divergent action trajectories per fix attempt
   (including abandoned/failed ones: negative learning).
6. **New/successful solutions get promoted** (recipe `shadow → candidate → promoted`).
7. Prove the **effectiveness and robustness** of the skill end-to-end with evidence, not claims.

User-supplied overrides for this run (may be empty): **$ARGUMENTS**
Apply any `KEY=value` pairs above as environment overrides for the wave driver
(`WAVE_MAX`, `WORKERS`, `NUM_CORES`, `PLATFORM`). If empty, use the defaults below.

---

## Ground truth — read these first, they OVERRIDE your priors

- `CLAUDE.md` → **"The Closed Learning Loop"** and **"Honesty invariants"** — the contract you
  are verifying. Re-read the honesty invariants; they are the pass/fail criteria for "the loop
  is closed."
- `r2g-rtl2gds/SKILL.md` — workflow, hard rules, env knobs (`PLACE_FAST`, `ROUTE_FAST`, Fmax step 5a).
- `r2g-rtl2gds/knowledge/README.md` — DB schema, CLI, the full numbered invariants.
- `r2g-rtl2gds/references/engineer-loop.md` — the autonomous driver, escalation, A/B lifecycle.
- `r2g-rtl2gds/references/failure-patterns.md` → **"Learning-Loop Closure Failures"** — the known
  ways the loop silently lies (identical A/B arms, stale `judged`, perimeter vs cell-area die, …).

## Step 0 — Situational awareness (run, then summarize state before acting)

```bash
cd /proj/workarea/user5/agent-r2g
git log --oneline -5
git status -s | head
# Free cores: this host is SHARED (user4 finesim often pins ~80/96). Size to what's free.
nproc; uptime
# Campaign ledger + pending count + honesty snapshot (the alarm panel):
LEDGER=design_cases/_batch/campaign.jsonl
KDB=r2g-rtl2gds/knowledge/knowledge.sqlite
python3 r2g-rtl2gds/scripts/loop/engineer_loop.py status --ledger "$LEDGER" 2>/dev/null | tail -20
sqlite3 "$KDB" "
  SELECT 'fail='||(SELECT COUNT(*) FROM runs WHERE orfs_status='fail')
     ||' fe='||(SELECT COUNT(DISTINCT run_id) FROM failure_events WHERE signature LIKE 'orfs-fail-%')
     ||' partial='||(SELECT COUNT(*) FROM runs WHERE orfs_status='partial')
     ||' ab_trials='||(SELECT COUNT(*) FROM ab_trials)
     ||' fix_ev='||(SELECT COUNT(*) FROM fix_events)
     ||' cand='||(SELECT COUNT(*) FROM recipe_status WHERE status='candidate')
     ||' promo='||(SELECT COUNT(*) FROM recipe_status WHERE status='promoted');"
# Per-platform promotions (the 2026-06-24 'arms identical' alarm hides HERE, not in ab_trials):
sqlite3 "$KDB" "SELECT platform, status, COUNT(*) FROM recipe_status GROUP BY platform, status ORDER BY platform, status;"
```

Report what you see in plain language: how many pending, is honesty internally consistent, is
`promoted` growing **per-platform** or flat. Decide the wave plan from this, not from assumptions.

## Step 1 — Deploy the NEWEST skill as a symlink (non-negotiable)

A stale deployed skill is the single most expensive failure mode in this repo (the 2026-06-08
defect): the harness loads `.claude/skills/r2g-rtl2gds/`, **not** the canonical tree. A `cp`
goes silently stale while you edit the canonical skill. Force a symlink deploy:

```bash
bash r2g-rtl2gds/install.sh --project . --link --force
readlink .claude/skills/r2g-rtl2gds   # MUST resolve to the canonical r2g-rtl2gds/ tree
bash r2g-rtl2gds/scripts/flow/check_env.sh   # nangate45 + tools must be green
```

If `check_env.sh` is not green for the tools you need (yosys/openroad/ORFS, and for sign-off
magic/netgen via `references/env.local.sh`), fix the environment *before* running flows — a
flow that aborts on a missing tool teaches the loop a lie.

## Step 2 — Run the campaign in parallel waves (Fmax → flow → A/B per wave)

**Hard rule (shared host):** keep `WORKERS × NUM_CORES ≤ free cores`. Default to the
good-neighbour pool `WORKERS=3 NUM_CORES=4` (≈12 cores) when finesim is loaded; scale UP toward
`WORKERS=8 NUM_CORES=12` only when `nproc`/`uptime` show the host is yours. Live-retune the
*next* wave with **no restart** by writing `tools/_nangate45_resume_logs/pool.env`
(the driver re-sources it each wave). Apply $ARGUMENTS overrides here.

The existing batch driver already loops waves until `pending=0`, emits a `WAVE_DONE` summary, and
appends an honesty snapshot per wave. **Launch it in the background and monitor** — do not block:

```bash
# Apply overrides from $ARGUMENTS (example): echo 'WORKERS=3' 'NUM_CORES=4' 'WAVE_MAX=24' > tools/_nangate45_resume_logs/pool.env
WAVE_MAX=${WAVE_MAX:-24} WORKERS=${WORKERS:-3} NUM_CORES=${NUM_CORES:-4} \
  setsid bash tools/nangate45_resume_waves.sh >/dev/null 2>&1 &
echo "driver pgid: $!"   # record the PGID — to stop a wave campaign you must kill the GROUP
```

**Wire Fmax search into each wave.** The batch driver runs `engineer_loop run` (flow + fix +
ingest + learn + A/B). The skill's Fmax search is a *separate pre-pass* that proxy-searches the
fastest closing period per design and stamps its SDC, and **must run BEFORE `run`** on the same
wave prefix. If the driver in use does not already interleave it, drive the per-wave sequence
yourself (or extend the driver) so each wave is:

```bash
EL=r2g-rtl2gds/scripts/loop/engineer_loop.py
python3 "$EL" fmax-drain --ledger "$LEDGER" --platform "${PLATFORM:-nangate45}" \
        --max "${WAVE_MAX:-24}" --workers "${WORKERS:-3}"   # best Fmax → SDC, same prefix as run
python3 "$EL" run        --ledger "$LEDGER" --max "${WAVE_MAX:-24}" --workers "${WORKERS:-3}"
python3 "$EL" ab-drain   --ledger "$LEDGER" --workers "${WORKERS:-3}"   # judge pending A/B candidates
```

`--max N` makes `fmax-drain` and `run` pick the **same** first-N-pending prefix, so Fmax
characterization and sign-off interleave per wave instead of front-loading all of them.

While waves run, **`kill -9 -<PGID>` the process GROUP** (not just the python) if you must stop —
`run_orfs.sh` wraps stages in `setsid timeout`, so killing the driver alone orphans the make/openroad
tree. If a single huge design (79–152K cells, LVS at ~99% CPU for hours) tail-blocks a wave, that is
*legit super-linear extraction*, not a hang — only kill it if it truly blocks progress, and log that
you did (no silent caps).

## Step 3 — Hunt skill bugs (this is the point, not a side effect)

After every wave, interrogate the DBs. Each of these is a *lead*, and several map to documented
patterns — chase them down rather than papering over:

- **`fail` rows without a `failure_event`** → the learner is blind to a whole backend-failure class.
  `count(runs WHERE orfs_status='fail')` MUST equal the count carrying an `orfs-fail-%` event.
- **Misclassified aborts** (e.g. early synth abort filed as `unseen_crash`; a match-then-writer-crash
  LVS filed as `fail`; FLW-0024 die-too-small filed as place divergence). Diagnose the *true* reason
  from the stage log via `references/failure-patterns.md` before believing the status.
- **`ab_trials` grows but `promoted` is flat for a whole platform** → the 2026-06-24 "arms are
  identical" alarm (subtler than empty `ab_trials`). Verify a trial's `metrics_json` shows the two
  arms genuinely diverging (different `is_success`/`outcome_score`/`fix_iters`), not wall-clock noise.
- **`fail`/`partial` rows exist but `ab_trials` is empty** → the loop is inert and lying; treat it
  exactly like an empty `heuristics.json`.
- **Fmax `status='error'`** vs honest `unconstrained`/`inconclusive` — an error that should have been
  a fallback (null floorplan slack → fall back to post-place) is a bug.

**When you find a real bug, fix it the project way** (see `CLAUDE.md` → "When You Fix a Bug"):
1. Find the existing bucket in `references/failure-patterns.md`/`lessons-learned.md`; append a sub-section.
2. Fix the offending `scripts/` file to detect + self-heal or emit a clear HINT. **Prefer editing
   existing scripts over adding new ones.**
3. Add/extend a **TDD test** that fails before and passes after; keep the pytest suite green.
4. Re-validate on the triggering design, **ingest** (`knowledge/ingest_run.py`), re-run
   `learn_heuristics.py`/`mine_rules.py` if a new rule is implied.
5. Reconcile any rows the bug mislabeled — but touch only the **latest-ingested row per project**;
   old `fail` + new `pass` must coexist (never clobber history).
6. **Commit** with a `feat(skill):`/`fix(skill):` prefix (the commit log is the long-term record).

## Step 4 — Prove the loop is CLOSED (evidence, not assertion)

The loop is "closed" only when ALL of these hold — show the SQL/output for each:

- **Honesty 5/5:** `python3 r2g-rtl2gds/knowledge/honesty.py --db r2g-rtl2gds/knowledge/knowledge.sqlite`
  passes over the **real committed store**.
- **Failure learning:** `fix_events`/`fix_trajectories` captured fix attempts — including
  `abandoned`/`failed` ones (negative learning), not just successes.
- **Success learning + promotion:** at least one recipe transitioned `candidate → promoted`
  **on the platform under test (per-platform `promo` grew)**, backed by an `ab_trials` row whose
  arms genuinely diverged (arm A control loses / arm B forced-recipe wins).
- **Cross-design transfer:** a recipe learned on one design/class applies to another (symptom-keyed,
  not family-named) — evidence in `lessons`/`symptoms` or a promotion spanning classes.
- **DRC/LVS + Fmax:** clean/clean sign-off counts grew this campaign, and Fmax is recorded
  (realistic GHz or an honest `unconstrained`/`inconclusive`, never a silent `error`).

If any of these fail, that failure **is** the next bug to fix — loop back to Step 3. Do not declare
victory on the strength of machinery existing; the A/B arms must have *executed, diverged, and
promoted*.

## Step 5 — Record durable learnings (don't let the session evaporate)

- Update `r2g-rtl2gds/references/` (failure-patterns / lessons-learned) and any
  `docs/superpowers/{plans,specs}` touched, with a **dated note (commit hash + superseded
  invariants)** — not just code+tests. Keep `CLAUDE.md`'s "no per-run results here" rule.
- Update the operator memory index for this campaign's outcome (promotions gained, bugs fixed,
  honesty state) so the next session resumes from truth.
- Keep all changes on a branch off `main`; commit per fix; **only push/PR when the user asks.**

## Guardrails (hard rules — violating one corrupts the campaign or the host)

- Never run two configs with the same `DESIGN_NAME` + `FLOW_VARIANT` concurrently (the driver derives
  `FLOW_VARIANT` from the project-dir basename — keep names unique).
- Never set `PLACE_DENSITY_LB_ADDON` below `0.10` (placer divergence is irrecoverable).
- For >100K-cell designs, never run multiple LVS jobs concurrently (3–5 GB RAM each → 2–3× wall time).
- `WORKERS × NUM_CORES ≤ free cores` — the default grabs `nproc` (96) per flow; N flows oversubscribe N×.
- **Ingest after EVERY flow** — clean, failed, or partial. A failed run never ingested teaches nothing.
- **Escalate to the user before** attempting CDC, multi-clock, DFT, or signoff-quality closure —
  the loop NEVER blocks on unknowns; they go to the `escalations` queue.
