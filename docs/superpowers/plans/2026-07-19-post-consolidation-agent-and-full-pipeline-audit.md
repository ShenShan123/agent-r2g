# 2026-07-19 Post-Consolidation Agent and Full-Pipeline Audit

Date: 2026-07-19  
Repository: `/home/yangao/r2g-skills`  
Remote: `https://github.com/ShenShan123/r2g-skills.git`  
Commit tested: `136bb7dd19f9f0a62351fae40b322467ab1a8d35`  
Synchronization: `HEAD == origin/main`

## Executive Verdict

The large module consolidation is structurally healthy: the repository fast-forwarded
cleanly, no live caller still references a deleted module, and all three upstream test
suites pass. The refactor successfully folded:

- `journal_action.py` and `summarize_log.py` into `journal_db.py`;
- `query_knowledge.py` into `knowledge_db.py`;
- `reconcile_ab_verdicts.py` into `ab_runner.py`;
- `monitor_health.py` and `trace_provenance.py` into `observe.py`.

The consolidation did not, however, close the evidence chain across Agent learning and
dataset publication. At the current commit this audit confirms:

- **20 implementation defects**: 14 P0 and 6 P1;
- **4 explicit policy/contract decisions** that remain unresolved;
- **23 historical adversarial conditions now protected**, demonstrating substantial
  progress since the 2026-07-16 audit;
- one real `picorv32_core` positive-control rebuild that can produce graphs passing
  `295/295` verifier checks when a complete, coherent label set is available.

Eight implementation defects were newly isolated during this post-consolidation audit
and its real-instance follow-up. The other twelve are current reproductions or unchanged
residuals from the prior audit.
This report does **not** claim that the consolidation introduced the defects; no such
causal claim is justified without a pre/post bisect.

## Validation Performed

### Repository and Upstream Tests

```text
signoff-loop: 929 passed, 1 skipped
rtl-acquire:    80 passed
def-graph:     378 passed, 61 skipped
```

The graph tests were run with `/home/yangao/.conda/envs/gnn_env/bin/python`; the base
OpenROAD Python does not provide the graph-test dependencies. That interpreter choice is
an environment detail, not an Agent defect.

### Adversarial Harnesses

The prior 11-probe Agent/full-pipeline harness was rerun against the new commit:

```text
11/11 conditions reproduced, 0 harness errors
```

The older 2026-07-16 regression harnesses were also rerun. They confirmed that most
historical defects remain fixed while source trust, report binding, and graph atomicity
still reproduce. A new five-probe post-consolidation harness was then run three times:

```text
NEW-P0-1 parked Recipe stale-trial promotion:  REPRODUCED
NEW-P0-2 mutable synth configuration:           REPRODUCED
NEW-P0-3 explicit flow_variant ignored:         REPRODUCED
NEW-P1-1 cross-domain observe evidence:          REPRODUCED
NEW-P1-2 mixed-timezone J4 ordering:             REPRODUCED
summary: 5/5 reproduced, 0 harness errors
```

Artifacts:

- `tools/audit_post_consolidation_2026_07_19.py`;
- `tools/audit_post_consolidation_2026_07_19_results.json`;
- `/tmp/r2g_audit_2026_07_19_recheck.json`;
- `/tmp/r2g_agent_logic_2026_07_19_recheck.json`;
- `/tmp/r2g_full_pipeline_gnn_2026_07_19_recheck.json`.

All synthetic projects and databases were isolated from the production knowledge store.

### Production Store Integrity Snapshot

`tools/check_db_integrity.py` reported `0 alarm, 4 warn, 12 pass`. The knowledge-side
honesty invariants passed, including `280/280` failed runs carrying failure events. The
warnings were journal/telemetry coverage gaps:

- seven dangling run IDs on live historical audit projects were classified unexplained;
- `24/24` A/B-trial symptoms had no `ab_launch` journal action;
- `6/6` promoted-Recipe symptoms had no `promote` journal action;
- `27/27` open symptom escalations had no `escalate` journal action.

These observations are not counted as four additional implementation defects: the
journal is advisory, several rows predate the current consolidated writer, and causality
cannot be reconstructed from the snapshot alone. They should be rechecked after one
fresh post-consolidation live campaign; any newly created missing action is then a current
writer defect rather than legacy telemetry debt.

## Newly Confirmed Defects

### P0-N1: Parking a Non-Divergent Recipe Does Not Invalidate Stale Trials

#### Reproduction

A legacy `lvs_resolve_unknown` candidate began at `status_version=7`.
`park_nondivergent()` changed its lifecycle state from `candidate` to `parked`, but left
the version at 7. A subsequently recorded, ownership-valid-looking winning trial then
promoted the parked Recipe:

```json
{
  "before": ["candidate", 7],
  "after_park": ["parked", 7],
  "after_trial": ["promoted", 8, "ab_corpus:1w0l"],
  "trial_provenance_complete": true
}
```

#### Root Cause

`recipe_lifecycle.py:237-253` performs a direct SQL `UPDATE` instead of using the common
lifecycle transition helper, so it does not increment `status_version`. In addition,
`ab_runner.record_trial()` and `judge_recipe()` do not reject evidence for a currently
`parked` Recipe.

#### Impact

A plan created before parking remains apparently current and can return a deliberately
non-divergent strategy to the live promoted pool.

#### Recommendation

- Route parking through the common versioned transition function.
- Revalidate lifecycle state and version at both trial recording and judgment.
- Permit lifecycle transitions from A/B evidence only for an eligible `candidate`
  generation; store late evidence historically but do not apply it.
- Add a regression for candidate -> parked -> late win.

### P0-N2: Synth-Only Configuration Inputs Are Mutable After Proof

#### Reproduction

A parameterized RTL source was synth-qualified with:

```text
VERILOG_TOP_PARAMS = {WIDTH 8}
```

The RTL bytes and their source digest were left unchanged, but the synth project's
`config.mk` was changed to `WIDTH 16` before promotion. Production promotion returned:

```json
{
  "status": "promoted",
  "source_bytes_verified": true,
  "synth_proven_parameter": "WIDTH=8",
  "promoted_parameter": "WIDTH=16"
}
```

#### Root Cause

`promote_candidates.py:261-321` re-parses the mutable synth project configuration at
promotion time. The source manifest covers RTL bytes only; it does not freeze top
parameters, frontend selection, memory settings, include search order, defines, or the
exact configuration digest.

#### Impact

The full flow can elaborate a different circuit from the synth-only qualified circuit
while positively claiming source-byte verification.

#### Recommendation

- Define a typed compilation-input manifest containing RTL/header bytes, include order,
  preprocessor defines, top parameters, frontend/version, and synthesis switches.
- Persist it at synth time and use only the frozen manifest during promotion.
- Digest the normalized configuration and include it in candidate, promoted-project,
  ORFS-run, and graph provenance.
- Reject promotion when any required compilation input is absent or changed.

### P0-N3: Explicit `flow_variant` Does Not Select the Requested Backend Run

#### Reproduction

One project contained two complete backend runs:

```text
RUN_A -> flow_variant=variant_a
RUN_Z -> flow_variant=variant_b
```

The command explicitly requested `variant_a`:

```text
run_graphs.sh <project> nangate45 variant_a
```

It returned zero but selected:

```text
<project>/backend/RUN_Z/results/6_final.def
```

#### Root Cause

`run_graphs.sh:123-139` selects the first lexicographically reverse-sorted `RUN_*`
directory containing a final DEF. `FLOW_VARIANT_ARG` is forwarded only if feature or
label extraction must fall back to the live ORFS results tree; it is not used to filter
backend run metadata. `run_features.sh` and `run_labels.sh` contain the same selection
pattern.

#### Impact

Labels and graphs can be published for a different flow variant than the caller
requested. This is a dataset identity and experiment-isolation failure.

#### Recommendation

- Resolve the authoritative run through `run-meta.json` using exact design, platform,
  flow variant, and terminal status.
- Fail on zero or multiple matches unless the caller supplies an exact `run_id`.
- Record `run_id` and `flow_variant` in feature, label, signoff, and graph manifests.
- Make all three graph-stage wrappers call one shared run resolver.

### P0-N4: Failed Label Extraction Can Publish a Fresh Marker Over Stale CSVs

#### Real-Project Reproduction

An isolated copy of `design_cases/picorv32_core` was forced to re-extract labels. The
local OpenROAD executable could not read the copied ODB and both timing and IR-drop
extractors failed with an ODB schema-version error. The schema mismatch is an
environment trigger, not the Agent defect.

The Agent-side behavior was:

1. `run_soft()` swallowed both non-zero extractor exits;
2. `run_labels.sh` returned zero;
3. a new `reports/labels_stats.json` completion marker was written;
4. the old `timing_features.csv` remained in place;
5. the missing `ir_drop.csv` later caused `build_graphs.py` and
   `verify_graph_dataset.py` to raise uncaught `FileNotFoundError` exceptions.

The timestamps prove the stale-success surface:

```text
selected DEF:          2026-07-19 10:46:50
timing_features.csv:   2026-07-14 13:13:35   (extractor had just failed)
labels_stats.json:     2026-07-19 10:47:10   (new completion marker)
```

After an old, plausible IR-drop CSV was restored solely to continue the experiment,
the graph builder and verifier accepted the set and eventually reported `295/295`.
That positive result demonstrates semantic plausibility, but it does not prove that the
labels came from the current extraction attempt.

#### Root Cause

`run_labels.sh:171-180` deliberately makes every extractor fail-soft, does not stage or
quarantine its prior output, and unconditionally writes the aggregate marker at
`run_labels.sh:236`. `graph_lib.py:225-238` then requires every label file by existence
and raises on absence. Neither path carries an extraction-attempt ID or input digest.

#### Impact

- If an old CSV survives, a failed extraction can silently publish stale labels.
- If no old CSV survives, graph generation crashes instead of producing a structured
  blocked/skipped manifest.
- A toolchain failure can be misread as a graph-builder or design failure and can feed
  misleading Agent diagnosis.

#### Recommendation

- Build labels in a new staging generation; never write into the active label set.
- Delete or quarantine a stage's prior target before launch.
- Give every label family an explicit `ok`, `unavailable`, or `failed` status, input
  digests, tool version, log digest, and attempt ID.
- Define required versus optional label families. A required failure blocks publication;
  an optional failure produces explicit NaNs and an unavailable flag.
- Publish the completion marker only after validation of the whole staged generation.
- Make graph builder and verifier report structured failures rather than traceback.

### P1-N1: Consolidated Provenance Queries Mix Recipe Domains

#### Reproduction

`observe.solution_origin()` was queried for:

```text
(S-CROSS-DOMAIN, logic/small, nangate45, density_relief)
```

It returned the correct `promoted` status but also returned a `sky130hd/cpu-large` loss
and a `sky130hd` repair episode.

#### Root Cause

`observe.py:127-160` scopes `recipe_status` by the full key, but scopes `ab_trials` and
`fix_trajectories` only by `symptom_id + strategy`. `bug_solutions()` similarly chooses
the latest lifecycle row across design classes and platforms.

#### Impact

This does not directly change live ranking, but it corrupts operator/Agent explanations,
audit evidence, and any future automation that consumes the canonical trace API.

#### Recommendation

- Scope all Recipe evidence queries by the full lifecycle key.
- For intentionally pooled evidence, return it in a separate `transfer_evidence` field
  with source domain and match-level weights.
- Never use a latest-row-across-domains status as the status of a concrete Recipe key.

### P1-N2: J4 Integrity Classification Misorders Mixed-Timezone Timestamps

#### Reproduction

Two actions represented these real instants:

```text
resolving action: 2026-07-18T10:00:00+08:00 = 02:00Z
dangling action:  2026-07-18T03:00:00Z       = 03:00Z (newer)
```

J4 classified the newer dangling action as old re-ingest residue and reported zero
unexplained live dangles.

#### Root Cause

`check_db_integrity.py:185-203` uses SQL `MAX(a.ts)` and Python string comparison. The
knowledge documentation already recognizes mixed `Z` and offset timestamp regimes and
requires instant-aware ordering elsewhere.

#### Impact

A current journal writer failure can be hidden as benign residue, weakening exactly the
diagnostic that the latest commit added to distinguish actionable dangles.

#### Recommendation

- Use SQLite `julianday(a.ts)` for grouping and ordering.
- Return both original timestamp and normalized epoch for diagnostics.
- Add mixed `Z`, `+08:00`, and equal-instant regression cases.

## Previously Confirmed Defects Still Present

The following twelve current defects were re-executed or remain in untouched production
paths with their previous reproductions still applicable.

### P0-R1: Explicit ORFS Failure Can Enter the Success Learner

**Evidence.** A copied production row for `rv32i_csr/nangate45` has
`orfs_status=fail`, `fail_stage=synth`, but clean project-level DRC/LVS/RCX. Rebuilding
the learner counted the whole cohort as `19/19` successes even though it contained this
explicit failure and another partial run.

**Root cause and impact.** `knowledge_db.is_success()` accepts its relaxed success path
when any positive signoff field is present, but does not veto `orfs_status='fail'`.
Stale or run-unbound signoff can therefore teach the learner that a failed backend run
was successful and inflate Recipe confidence.

**Fix and acceptance.** Make an explicit ORFS failure an unconditional veto; allow the
relaxed path only with run-bound positive signoff. Add a test in which clean DRC/LVS/RCX
coexists with `orfs_status=fail` and verify that both `is_success()` and the rebuilt
learner count it as failure.

### P0-R2: A/B Ownership Uses Only an Eight-Character Strategy Prefix

**Evidence.** `density_relief` occurs in 44 lifecycle keys across nine symptoms and 22
domains. Two real arm runs belonging to one design-class key were recorded against a
different real key with the same strategy; `_arms_owned()` accepted them,
`provenance_complete` became true, and the unrelated candidate was promoted.

**Root cause and impact.** `ab_runner._arms_owned()` infers ownership from an arm
directory name containing `strategy[:8]`, arm role, platform, and a shared tail. It does
not join the runs to a durable plan containing the full Recipe key. Valid runs from a
different experiment can therefore provide false causal evidence.

**Fix and acceptance.** Persist an `ab_trial_plan` with trial UUID, full Recipe key,
Recipe version/effect hash, subject, platform, expected arm paths and roles; bind each
run to that plan. A regression using real but foreign arms with the same strategy must
record the trial for audit but set provenance false and prohibit promotion.

### P0-R3: Legacy Unverifiable A/B Wins Can Still Promote

**Evidence.** The copied production store contains 77 decisive legacy trials: all have
NULL arm IDs and no `provenance_complete` field. They affect 42 Recipe keys, and 21
currently promoted keys rely only on this legacy evidence. Calling `judge_recipe()` on
one real candidate with four such wins promoted it.

**Root cause and impact.** `ab_runner.judge_recipe()` treats an explicitly false
provenance flag as uncountable but returns true when the field is absent. This preserves
legacy behavior at the cost of allowing untraceable evidence to change current
lifecycle state.

**Fix and acceptance.** Migrate rows to explicit `verified`, `reconstructed`, or
`legacy_unverified` states. Unverified rows may remain visible but must not cause new
promotion or demotion. Preserve existing deployments through an expiring
`legacy_promoted` state and revalidate lazily instead of launching an immediate global
A/B campaign.

### P0-R4: Partial Source Manifests Can Claim Full Verification

**Evidence.** A real five-file successful `eth_rxethmac` candidate was supplied with a
manifest containing only one of those files. Promotion still returned
`source_bytes_verified=true` and `rtl_file_count=5`.

**Root cause and impact.** `promote_candidates.py` builds a manifest lookup, then checks
only files for which a digest is present. It never requires the canonical `rtl_files`
set to equal the manifest set. Four unverified files can therefore change while the
promotion result claims complete source verification.

**Fix and acceptance.** Require exact canonical path-set equality, one valid digest per
required file, no duplicate entries, and an explicit file type. A test with five RTL
files and a one-file manifest must block promotion and report the four missing entries.

### P0-R5: Include Headers Are Not Frozen or Vendored

**Evidence.** Four of eight successful July-16 candidates depended on headers absent
from `rtl_files`, including `ethmac_defines.v` and `timescale.v`. The promoted Ethernet
project did not copy those headers into its local `rtl/` tree and retained the external
source directory in `VERILOG_INCLUDE_DIRS`.

**Root cause and impact.** `expand_candidates.py` creates `source_manifest` from
`source_files` only. `vendor_rtl()` copies only `rtl_files`, while promotion carries
external include directories into the full-flow configuration. A header can change
after synth-only and cause ORFS to elaborate a circuit different from the qualified one.

**Fix and acceptance.** Capture the frontend's complete dependency closure, including
headers, packages, generated files, include order, defines and tool version; digest and
vendor it with stable relative paths. After promotion, the project must synthesize with
external source access disabled and retain the same normalized compilation digest.

### P0-R6: Legacy Candidates Without Source Manifests Auto-Promote

**Evidence.** The real legacy `picorv32_core` candidate had no source manifest. Current
promotion returned `status=promoted` with `source_bytes_verified=false`; all nine
examined successful local candidates from July 14-17 had the same missing proof.

**Root cause and impact.** When no manifest exists, `promote_candidates.py` records the
false verification flag but continues into project creation and vendoring. The safety
state is descriptive rather than an enforcement gate, so an automatic campaign can
publish a design whose synth-proven source bytes cannot be reconstructed.

**Fix and acceptance.** Block automatic promotion and request re-expansion by default.
Provide a separately logged operator-only legacy override for recovery. Tests must show
that ordinary promotion stops before project creation, while the explicit override
retains `source_bytes_verified=false` in every downstream manifest.

### P0-R7: Project-Level Signoff Reports Are Not Bound to the Selected DEF

**Evidence.** Two real `wbuart32` runs produced different DEF digests: R1
`d6426fae...` and R2 `cc2da796...`. The gate accepted the R2 DEF using project-level R1
reports and returned `pass_with_caveats` because it checked only that R2's DEF path was
inside the selected R2 directory.

**Root cause and impact.** `signoff_gate.evaluate()` reads DRC/LVS/route/antenna/timing
from `<project>/reports`; `_check_binding()` binds the DEF to `run_dir`, not those
reports to the DEF or run. Clean results from one run can therefore certify another
layout and contaminate graph labels.

**Fix and acceptance.** Give every report a common provenance envelope containing
backend run ID and DEF/GDS/netlist digests, and require exact agreement in the gate. A
test that swaps clean reports between same-design, same-platform runs must fail closed.

### P0-R8: Feature/Label Freshness Is mtime-Based, Not Content-Based

**Evidence.** The real `picorv32_core` DEF `DIEAREA` was modified while its original
mtime was restored. `run_graphs.sh` reused the old feature and label directories,
returned zero, and wrote an `ok` manifest. The independent verifier detected the stale
geometry/HPWL binding; forced feature extraction removed the mismatch.

**Root cause and impact.** `run_graphs.sh:needs_stage()` compares only CSV/marker mtimes
against the DEF mtime. Content-preserving timestamps, copied artifacts, and schema
changes bypass the check, allowing stale X/Y data to be attached to a new layout.

**Fix and acceptance.** Record content digests, extraction schema/tool version and run
ID in feature/label stage manifests; reuse only on an exact manifest match. A regression
that changes DEF bytes while restoring size and mtime must trigger re-extraction or
block publication.

### P0-R9: Graph Publication Is Not an Atomic Multi-File Transaction

**Evidence.** During a real 52,574-node `picorv32` rebuild, C output was made
unwritable. B changed from `cdf4add3...` to `6ccf2713...`, C-F remained from the old
generation, and the previous green manifest `9dd2a0c1...` survived.

**Root cause and impact.** `build_graphs.py` writes each variant directly into the live
dataset directory and replaces only the final JSON manifest atomically. A failure after
the first write exposes a mixed generation while the old manifest still claims a
coherent successful dataset.

**Fix and acceptance.** Write all graphs, stats and manifests under a new generation
directory, run the verifier there, then atomically switch one active pointer. Injecting
a failure on any variant must leave every active graph and the active manifest
byte-identical to the previous generation.

### P1-R1: No-PPA Re-Ingests Collapse Distinct Backend Attempts

**Evidence.** A clean real `wbuart32` project without `reports/ppa.json` was ingested.
After its copied route ledger was changed to failure, it was ingested again. Both
attempts received run ID `477f90b1...`; only the failed row remained, so the earlier
success disappeared from history.

**Root cause and impact.** `ingest_run._compute_run_id()` hashes the resolved project
path plus `ppa.json` mtime. Without that file, the marker is empty for every attempt and
the later ingest overwrites the same database identity. Learning statistics, lineage
and recovery analysis can therefore lose real attempts.

**Fix and acceptance.** Use the authoritative backend run/attempt ID plus immutable
artifact digests for run identity, and keep a separate idempotency key for re-ingesting
the same attempt. Two no-PPA attempts must create distinct rows; ingesting one unchanged
attempt twice must remain idempotent.

### P1-R2: Repair-Cycle Detection Ignores Quantitative Progress

**Evidence.** Two copied-store runs for the real `wbuart32` subject used the same
`M1_SPACING` violation class but counts of 100 and 10. `_global_repair_state()` produced
identical fingerprints and `_detect_repair_cycle()` declared a cycle.

**Root cause and impact.** The fingerprint stores only the set of nonzero DRC classes,
plus coarse DRC/LVS/timing states; it discards violation counts and severity. A useful
90% reduction can therefore be mistaken for no progress and stop a repair campaign
prematurely.

**Fix and acceptance.** Include normalized count/severity vectors and declare a cycle
only after no material Pareto improvement under documented tolerances. `100 -> 10`
must count as progress, while revisiting an unchanged or materially equivalent state
must still trigger cycle detection.

### P1-R3: Diagnosis Can Learn Superseded Intermediate Timing Failures

**Evidence.** The untouched successful `wbuart32` run has all six backend stages at
status zero and clean DRC, but no `ppa.json`. `build_diagnosis.py` nevertheless returned
`kind=timing_violation` from an intermediate message in the combined log.

**Root cause and impact.** When PPA is absent, log parsing scans timing phrases without
first allowing the terminal stage ledger and final reports to veto superseded messages.
A resolved intermediate violation can become a current failure event and teach an
irrelevant repair strategy.

**Fix and acceptance.** Make terminal structured reports and stage status authoritative;
limit text fallback to the actual failed stage and retain earlier messages as trace-only
context. A six-stage clean run containing an intermediate violation string must produce
no timing failure, while a terminal timing failure without PPA must remain diagnosable.

## Unresolved Policy and Contract Decisions

These behaviors are reproducible, but current code/tests encode them deliberately. They
should be decided explicitly rather than reported as accidental implementation bugs.

1. **Changed evidence retains an old promotion.** Decide whether Recipe identity is only
   the intervention effect or also includes learned applicability/confidence. Effect or
   applicability changes require a version hash and revalidation.
2. **One subject can promote before a two-subject plan finishes.** Either document one
   subject as the real threshold or commit lifecycle changes only after the planned
   cohort is terminal.
3. **Antenna non-convergence is a caveat, not a blocker.** Decide whether official data
   means signoff-clean or intentionally includes dirty samples with a distinct class.
4. **Finish-only ledgers can count as complete without resume lineage.** Require either
   all six current-run stages or a recursively verified resume manifest.

## Historical Protections Verified

The current code correctly rejected or handled the following earlier failure modes:

- same run ID in both A/B arms;
- foreign runs whose paths do not match an owned arm pair;
- insertion-order-dependent tied verdicts;
- explicit confounding configuration changes;
- target fixes that introduce global signoff regressions;
- cross-platform/class live-regression demotion;
- Recipe lifecycle/content changes between planning and execution;
- same-prefix arm-directory collision during planning;
- stale arm versions after ordinary promote/demote transitions;
- no-effect Recipes entering A/B;
- failed reruns revived by stale mapped-netlist artifacts;
- explicitly rejected designs becoming publish-eligible;
- stale signoff restaging;
- archive path traversal and external symlink discovery;
- colliding ORFS run tags and leaked timeout descendants;
- silent bundle truncation;
- graph variant residue from a previous generation;
- quality-statistics schema mismatch;
- unconstrained sequential designs promoted under accidental virtual clocks;
- PPL failure misdiagnosis;
- unknown-license designs becoming publish-eligible.

## Real-Instance Follow-up Addendum

The implementation defects above were subsequently exercised against copied real
`wbuart32` and `picorv32` projects, copied production knowledge stores, and relocated
corpora. This follow-up confirmed two additional defects. It did not modify the
production knowledge store or the checked-out Agent implementation.

Detailed evidence and repair-value analysis are recorded in
`docs/superpowers/plans/2026-07-19-real-instance-fix-value-assessment.md`.

### P0-N7: Published Graphs Have No Explicit Schema-Version Contract

#### Reproduction

An existing real `picorv32` graph manifest reported `status: ok` but contained neither
`schema_version` nor `graph_schema_version`. The current verifier accepted only
`171/186` checks for that generation because fourteen `y_raw`/`edge_y_raw` fields and
one HPWL-consistency check did not match the current graph contract. Rebuilding the
same design with a coherent current label set reached `295/295`, but the newly written
manifest still carried no graph schema version.

#### Root Cause

Graph publication records file presence and generation status, but it does not identify
the feature/label schema against which the graph was built. Consumers and validators
therefore cannot distinguish a current graph from an older, structurally incompatible
generation without probing individual tensor fields.

#### Impact

An old graph can remain officially `ok` after the expected tensor contract changes.
Training code may silently mix incompatible generations or fail only after loading part
of a dataset, undermining the claim that publication produces a stable ML corpus.

#### Recommendation

Add an explicit, immutable graph-schema version to every graph and publication manifest.
Make the verifier and dataset loader reject unsupported versions, and define an explicit
migration policy: rebuild old generations, run a versioned converter, or quarantine them
from the official dataset index. Schema version and generation ID should be part of the
publication identity rather than optional metadata.

### P1-N6: Relocated Corpora Cannot Reliably Re-Promote Vendored RTL

#### Reproduction

In a relocated 495-design corpus, all 495 metadata records still referenced inaccessible
source paths under another user's home directory, even though every corpus entry had a
local vendored `rtl/` directory. Re-promoting the real `picorv32` entry attempted to read
the stale external path and terminated with an uncaught `PermissionError` instead of
using the local copy or recording a structured candidate failure.

#### Root Cause

Candidate metadata treats acquisition-time absolute source paths as authoritative after
the corpus has been copied. Promotion does not consistently resolve paths relative to a
declared corpus root, prefer vendored RTL, or convert filesystem access failures into a
normal pipeline outcome.

#### Impact

An otherwise self-contained corpus is not portable across users, servers, mounts, or
archive locations. A routine relocation can block retries and automation even though all
required RTL bytes are present locally.

#### Recommendation

Store corpus-relative paths plus a declared corpus-root identity, and prefer the local
vendored RTL tree during promotion. Validate source resolution before starting ORFS and
classify missing or inaccessible legacy paths as structured acquisition/promotion
failures. Provide a migration or repair command for existing metadata with stale absolute
paths.

## Recommended Fix Order

### Phase 1: Protect Learning and Promotion

1. Add the explicit ORFS-failure veto.
2. Introduce durable A/B trial plans and quarantine unverifiable legacy verdicts.
3. Version every lifecycle transition, including parking, and reject late evidence for
   ineligible lifecycle states.
4. Replace path/PPA-mtime run identity with backend-run and artifact identity.

### Phase 2: Freeze the Complete Design Identity

1. Create one typed compilation-input manifest covering sources, headers, parameters,
   defines, frontend, and synthesis switches.
2. Resolve graph runs by exact run ID/variant, never directory ordering.
3. Bind every signoff report and extracted dataset stage to content digests.

### Phase 3: Make Dataset Generations Transactional

1. Stage features, labels, graphs, stats, and manifests by generation ID.
2. Treat extractor failures explicitly; never reuse undeclared stale CSVs.
3. Run the independent verifier against staging.
4. Atomically publish the complete generation.

### Phase 4: Improve Learning Stability and Observability

1. Scope canonical trace queries by full Recipe key.
2. normalize all timestamp comparisons by instant.
3. incorporate quantitative progress into cycle detection.
4. make final-state reports authoritative in diagnosis.

## Acceptance Criteria

A future fix should include automated tests proving:

1. explicit ORFS failure can never be learnable success;
2. only runs joined to one exact durable A/B plan can affect promotion;
3. parking increments lifecycle version and late wins cannot re-promote it;
4. unverified legacy trials and candidates cannot auto-promote;
5. any compilation-input change blocks promotion;
6. an explicit variant selects exactly that variant's run;
7. failed extraction cannot leave a fresh marker over stale outputs;
8. every report, X/Y stage, and graph carries matching run/artifact digests;
9. a graph generation is atomic and verifier-gated;
10. every published graph declares a supported schema version, and incompatible legacy
    generations are rejected, migrated, or quarantined;
11. a relocated corpus promotes from its vendored RTL without depending on inaccessible
    acquisition-time absolute paths;
12. distinct no-PPA attempts retain distinct identities;
13. provenance queries never mix exact and transfer domains silently;
14. the upstream suites and a real `picorv32_core` rebuild remain green.

## Final Assessment

The consolidation is a useful reduction in code surface and did not break the tested
entry points. The remaining risk is not primarily module count. It is that several
trust decisions still infer identity from mutable paths, filenames, timestamps, or
plausible file presence.

The next architectural milestone should be one immutable generation identity carried
through:

```text
compilation inputs
-> synth candidate
-> promoted project
-> backend run and signoff reports
-> feature/label generation
-> graph files and publication manifest
```

The same principle should govern Agent learning: one Recipe version, one durable A/B
plan, two exactly owned runs, one explicit evidence domain, and one versioned lifecycle
transition. That would remove several current bugs together instead of adding another
local guard for each symptom.
