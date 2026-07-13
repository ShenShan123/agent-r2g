# Codex debug findings — audited assessment & closure (2026-07-13)

An external reviewer ("Codex") watched an r2g instance run and filed 5 **instance-testing
issues** (Part I) plus 6 **architectural learnings** cribbed from `rtl-agent-team.git` (Part II).
This document **revises** the raw findings (preserved verbatim in §"Original findings" at the
bottom) against the *actual* r2g-skills codebase as of 2026-07-12 (post `#38`–`#41`). Each is
graded, with `file:line` evidence, and — for the real ones — the minimal, in-architecture action
taken.

**Audit method.** A 5-way parallel investigation (one deep reader per Part-I finding),
cross-checked by direct reads of `run_orfs.sh`, `fix_signoff.sh`, `build_diagnosis.py`,
`ingest_run.py`, the ORFS `flow/Makefile` + `scripts/tapcell.tcl`, and the knowledge
README/`.gitignore`.

**The reviewer's vantage biases the findings.** It observes *symptoms* from outside the
codebase and infers *root causes* it cannot verify. So the audit's job is to separate the
real symptom from the guessed cause — and 4 of the 5 Part-I causes turned out to be phantom
or already-shipped. Re-implementing them would add **dead code that lies** (a diagnosis rule
that fires on a non-existent condition is worse than no rule).

Legend: **REAL** = genuine gap, fixed this pass · **ALREADY** = already implemented+tested ·
**WRONG** = the inferred cause does not exist in this checkout · **WRONG-FIX** = real concern,
proposed remedy breaks an invariant.

Net result: **1 code fix** (`build_diagnosis` `kind:none` gap) + **1 hygiene fix**
(`.gitignore` the resume-log pollution + `.gitattributes` the churning binary). Everything
else: documented no-op with evidence, so it is not re-chased. Full suites green
(signoff-loop **833 passed / 2 skipped**), honesty **5/5**.

---

## Part I — instance-testing issues

### 1. "`finish` stage re-triggers previous flows (tapcell `2_4` vs `2_3` filename mismatch)" — **WRONG**

**Claim.** After `5_route.odb`, `finish` re-ran tapcell/place/global_route, "likely" because the
ORFS Makefile expects `2_4_floorplan_tapcell.odb` but `tapcell.tcl` outputs `2_3_floorplan_tapcell.odb`.

**Evidence it is false.** In this ORFS checkout `scripts/tapcell.tcl:17` writes
`2_3_floorplan_tapcell.odb`, and `flow/Makefile:420/424` produces **and** consumes that exact name;
a repo-wide grep for `2_4_floorplan_tapcell` returns **nothing** (`2_4` is the *pdn* step,
`2_4_floorplan_pdn`). And `run_orfs.sh` invokes ORFS as **explicit per-stage `make` targets** in a
sequential loop (`run_orfs.sh:252,336-337`), so `finish` builds only the `6_*` chain
(`Makefile:599-620`: finish ← 6_report ← 6_1_fill ← `5_route.odb`); it never depends on tapcell/place/grt.
On resume `make clean_finish` removes only `6_*` (`Makefile:748-751`). The only Make mechanism that can
re-invalidate an upstream stage is a *touched shared prerequisite* (LEF/LIB/hook `.tcl` newer than an
intermediate `.odb`) — and nothing between the route and finish runs touches those. **Action: none.**
No `finish_rerun_previous_stages` / `orfs_target_output_mismatch` diagnosis rule added — it would fire on
a condition that does not occur. (ORFS-version pinning is fine general hygiene but unrelated to this
non-defect; not done here.)

### 2. "Delayed snapshot of successful route artifacts (copy only after all stages finish)" — **WRONG**

**Claim.** Route succeeded (`stage_log` status 0) but artifacts weren't saved to the RUN dir until all
stages finished, so a later hang/crash loses them.

**Evidence it is false.** ORFS writes `5_route.odb`/`5_2_route.def`/`6_final.*` **in-place** into
`$FLOW_DIR/results/$PLATFORM/$DESIGN/$VARIANT/` as each stage completes (the wrapper even reads them back
in-place at `run_orfs.sh:349-357`). There is no scratch-dir/rsync/`make clean` between stages; the
end-of-flow copy into `backend/RUN_*/` (`run_orfs.sh:523-551`) is a **redundant archive** and is reached
even when a stage fails/times out (`break` then fall-through). `finish` reading `5_route.*` never deletes
it. Every downstream consumer reads the **in-place** results dir
(`run_rcx.sh`/`run_drc.sh`/`run_lvs.sh`/`_restage_for_signoff.sh`; def-graph needs `6_final`, not `5_route`).
`stage_log.jsonl` is written incrementally, one row per completed stage (`run_orfs.sh:361`). **Action: none.**

### 3. "Antenna repair non-convergence — detect no improvement, halt, report `antenna_nonconverged`" — **ALREADY**

Shipped by #36 (2026-07-10) + hardened by #38a (2026-07-12). `fix_signoff.sh` has a per-strategy
`antenna_noimp` counter that increments only on a non-improving antenna iteration and **resets on any
improving one** (`fix_signoff.sh:440-449`); at `>=2` consecutive no-ops it promotes the verdict to
`antenna_nonconverged` (`:459-460`), writes `reports/antenna_nonconverged.json`, and halts (four
independent bounds prevent infinite retry: `MAX_ITERS=8`, the antenna `>=2` exit, the D12 `noimp>=2`
budget, and the finite diagnose strategy catalog returning `STOP`). The marker rides `signoff_gate.py`,
`ingest_run.py` (`_VERDICT_MAP`→`no_change`, negative learning), and tests
(`test_antenna_nonconverged.py`). **Action: none** — every clause the reviewer asks for is present and
tested. (Minor enrichment done under #4: `build_diagnosis` now *echoes* the marker into `run_summary`.)

### 4. "Diagnosis script reports `kind:none` even when logs show clear issues" — **REAL (narrow); FIXED**

The one genuine gap. `build_diagnosis.detect_issues()` is entirely text-log driven (17 signature rules).
A stage killed at `ORFS_TIMEOUT` (failure-patterns #40) is SIGKILLed, so `flow.log` often has no
`make: *** Error` line — every text rule misses and `main()` fell through to `kind:none`, **even though**
the same file's `build_run_summary()` already knew `signoff.orfs_status='fail'` + `orfs_fail_stage` from
`ppa.json`. So `diagnosis.json`'s `kind` (and the dashboard panel keyed on it) went blank for a real abort.

**Honesty scope.** Cosmetic, not a learner lie: `ingest_run.py` derives status from `stage_log`
independently and writes the `orfs-fail-<stage>` `failure_event` from that, building `failure_events`
solely from `diag['issues']` — never the top-level `kind`. honesty.py stayed green.

**Fix (`build_diagnosis.py`, TDD).** `main()` builds `run_summary` before the `kind` decision; on empty
`issues`, `_orfs_fallback_kind(run_summary)` emits `orfs_stage_failed` (status `fail` — subsumes the
reviewer's *route_completed_but_finish_missing*, i.e. `orfs_fail_stage='finish'`) or
`orfs_stage_incomplete` (status `partial`). It leaves `issues:[]` (presentation-only ⇒ no duplicate
failure_event). `build_run_summary` also echoes `antenna_nonconverged.json`. The other 4 proposed rules
were **rejected**: `finish_rerun_previous_stages`/`orfs_target_output_mismatch` fire on the #1 phantom;
`route_artifact_not_collected` on the #2 phantom (and status is the make exit code, already guarded by the
GDS-collection downgrade at `run_orfs.sh:558-561`). Tests + failure-patterns #42.

### 5. "Git workspace pollution — move the runtime knowledge store to a gitignored dir" — **WRONG-FIX; real pollution fixed**

**Why the proposed fix is wrong.** `knowledge.sqlite`+`heuristics.json` are **tracked on purpose**
(spec D14: "the committed binary IS the shipped, pre-trained store"; a fresh clone must behave identically
off committed knowledge — the honesty firewall). Untracking them ships an empty store; that exact
"gitignore the binary, rebuild on clone" migration was **implemented then reverted 2026-06-23** per operator
preference (README.md:457-459). The churn is **by design** — the operator commits learned deltas after
ingest/learn (README.md:442,455) — and cross-operator sharing already goes through the additive,
honesty-gated `knowledge_sync.py` NDJSON bundle (`knowledge/store/`, gitignored), **not** a git binary merge.

**The real, unrelated pollution.** `tools/_sky130hd_resume_logs/` + `tools/_sky130hs_resume_logs/`
(~370 MB of campaign wave logs + `driver.pgid`/`pool.env`, written by `campaign_resume_waves.sh`) were
**not** gitignored — same class as `design_cases/`. **Fix:** `.gitignore` now covers
`tools/_*_resume_logs/`. Added `.gitattributes` marking the churning `knowledge.sqlite` blob `binary`
(shared, zero-config — cleaner diffs / no CRLF munging); deliberately **not** `merge=ours` (that driver
needs an unshared `git config merge.ours.driver true`, so it silently no-ops on a fresh clone).
`heuristics.json` left as normal text so recipe deltas stay diff-reviewable. The two new
`docs/superpowers/plans/*.md` (this doc + the 2026-07-12 one + the filename normalization) are committed,
not ignored.

---

## Part II — architectural learnings from `rtl-agent-team.git`

These are broad structural suggestions; assessed against reality, they are **substantially already
present** in r2g's 4-sub-skill design. No code action — recorded so the mapping is explicit.

1. **Three-tier flow (Setup/Init/Work)** — PRESENT. `eda-install` (setup) → `rtl-acquire` promote /
   `init_project` (init) → `signoff-loop`+`def-graph` (work).
2. **Phase registry** — PRESENT. The pipeline is already discretely staged
   (acquire→synth-only→dedup→promote→ORFS→DRC/LVS/RCX→labels→b–f graphs→publish) with explicit
   inputs/outputs/success criteria via `stage_log.jsonl`, `signoff_gate.py`, and the graph `manifest`.
3. **Completion gates** — PRESENT and strongly enforced: `signoff_gate.py` ("a `6_final.def` alone is NOT
   sign-off", #34), manifest `status`/`label_health`/`rc_health`, the honesty invariants, and
   `tools/verify_graph_dataset.py`.
4. **Retry ladders** — SUBSTANTIALLY PRESENT: `MAX_ITERS`, the antenna consecutive-no-improvement exit,
   the D12 adaptive budget, the finite diagnose strategy catalog, the `escalations` queue, and the recipe
   `shadow→candidate→promoted/demoted` lifecycle map onto primary→fallback→escalated. No infinite loops
   (see #3/#36).
5. **Dedicated runtime state dir (`.r2g/state/`)** — the equivalent state already exists, distributed:
   `backend/RUN_*/{stage_log.jsonl,resume_meta.json}`, `reports/`, the gitignored `journal.sqlite`, and
   `knowledge/store/`. A single `.r2g/state/` is not a compelling change; the **only** genuine "pollution"
   it would have prevented is the resume-log dir, now gitignored (finding #5).
6. **Traceability/reproducibility** — LARGELY PRESENT: `resume_meta.json`, `stage_log` artifact+mtime,
   `signoff_health` in the manifest, `config_lineage`, provenance run-meta (#30). The one genuinely-absent
   piece the reviewer names — **ORFS commit/version** in the provenance — is a reasonable *future* addition
   (cheap: stamp `git -C $ORFS_ROOT rev-parse HEAD` into `resume_meta.json`), noted here, **not** done this
   pass (no observed failure depends on it).

---

## Verification

- `build_diagnosis.py`: TDD, `tests/test_build_diagnosis.py` 13 passed (6 new). End-to-end smoke: a
  route-pass / `finish`-timeout(124) project with no `make` error line now yields
  `kind:orfs_stage_failed` naming `'finish'` with `issues:[]` (was `kind:none`).
- Full signoff-loop suite: **833 passed / 2 skipped**.
- Honesty gate over the real committed store: **5/5 GREEN** (fail_event_parity 293/293; ab_trials 361).
- `.gitignore`: `git check-ignore` confirms both resume-log dirs ignored; `git status` no longer lists
  ~370 MB of wave logs.
- No change to `knowledge.sqlite`/`heuristics.json` (their pre-existing runtime deltas are the campaign's,
  left for the operator to commit — not swept into this fix).

---

## Original findings

*(verbatim, as filed 2026-07-13)*

### I. Issues Found in Today's Instance Testing

1. **finish Stage Re-triggers Previous Flows** — after `5_route.odb`, finish erroneously re-ran
   tapcell/place/global_route; "likely" a filename mismatch (Makefile expects `2_4_floorplan_tapcell.odb`,
   tapcell.tcl outputs `2_3_floorplan_tapcell.odb`). Suggestion: pin the ORFS version during eda-install,
   add a preflight check that Makefile targets match Tcl outputs.
2. **Delayed Snapshot of Successful route Artifacts** — md5/aes/blake2s: route succeeded but artifacts
   weren't immediately saved to the backend RUN dir; the wrapper copies only after all stages finish.
   Suggestion: snapshot key artifacts immediately after each stage succeeds.
3. **Antenna Repair Non-Convergence** — chacha/blake2s stuck at 1–2 residuals across rounds. Suggestion:
   detect "no improvement across multiple rounds", halt, report `antenna_nonconverged`.
4. **Diagnosis Script Misses Key Failure Modes** — `diagnosis.json` reports `kind:none` even with clear
   issues. Suggestion: add rules `route_completed_but_finish_missing`, `route_artifact_not_collected`,
   `finish_rerun_previous_stages`, `antenna_nonconverged`, `orfs_target_output_mismatch`.
5. **Git Workspace Pollution** — runtime updates to `knowledge/heuristics.json` and `knowledge.sqlite`
   dirty the workspace. Suggestion: move the runtime knowledge base to a gitignored dir, or provide a
   clean/stash script.

### II. Learnings from rtl-agent-team.git

1. Three-Tier Flow (Setup/Init/Work). 2. Phase Registry. 3. Completion Gates. 4. Retry Ladders.
5. Dedicated Runtime State Directory (`.r2g/state/`). 6. Traceability and Reproducibility (RTL source,
commit hash, ORFS version, stage artifacts, signoff verdicts, label health, graph schema).
