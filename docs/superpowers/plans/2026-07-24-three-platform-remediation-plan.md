# R2G V1 Three-Platform Remediation Plan

## 1. Baseline and Scope

This plan converts the production-Agent findings in
`2026-07-24-three-platform-pilot-analysis.md` into bounded implementation and
regression work.

The tested baseline is Agent commit
`8d449b088382db7274d8cc93cc2088b9e032b2cb` on the same four fixed RTL
fixtures and three strict platforms:

- `nangate45`;
- `sky130hd`; and
- `sky130hs`.

The current measured results are:

- Nangate45: 40/49 Gate cells and 2/4 strict end-to-end fixtures;
- Sky130HD: 45/49 Gate cells and 3/4 strict end-to-end fixtures; and
- Sky130HS: 44/49 Gate cells and 3/4 strict end-to-end fixtures.

This plan covers only:

1. production Agent defects that affect autonomous execution or graph-dataset
   trust; and
2. measured EDA-tool limitations that constrain supported scale or yield.

The Pilot is an independent measurement instrument and is outside the
production Agent maintainer's remediation scope. Do not change Pilot Gate
definitions, fixture selection, scoring thresholds, or signoff policy to
improve the reported score.

## 2. Exit Criteria

This remediation round is complete only when all of the following hold:

1. A timed-out DRC checker leaves no KLayout, wrapper, pipe, or worker process
   behind and the engineer loop reaches a terminal state without operator
   intervention.
2. Every stage reused by a repair/resume run has a non-null artifact digest,
   an explicit parent run, and a verified byte-for-byte match.
3. Missing, malformed, reconstructed-only, or mismatched lineage blocks formal
   graph generation and clean-dataset publication.
4. A requested strict platform cannot complete installation while required
   DRC/LVS/antenna collateral or its post-install capability canary is missing.
5. Real DRC, LVS, route, timing, or checker-timeout failures remain blocked.
6. The unchanged three-platform campaign is rerun from fresh campaign roots
   with no manual process cleanup.

## 3. Priority Summary

| ID | Priority | Work item | Evidence status | Estimated scope |
|---|---|---|---|---|
| RMD2-P0-01 | P0 | Kill the complete DRC checker process group on timeout | Reproduced on Nangate45 SHA-256 | Small to medium |
| RMD2-P0-02 | P0 | Make repair/resume lineage digest-complete and fail closed | Reproduced on Nangate45 I2C and Sky130HS SHA-256 | Medium |
| RMD2-P1-01 | P1 | Make selected strict-platform installation fail closed | Current host passes; fresh-host guarantee remains conditional | Small |
| RMD2-LIM-01 | Limitation | Bound Nangate45 full-DRC scale and cost | Measured from 22 seconds to timeout | Policy and performance investigation |
| RMD2-DES-01 | Design case | Preserve Sky130HD GCD `m3.2` violations | Reproduced real DRC failure | Investigation only |
| RMD2-DES-02 | Design case | Preserve Sky130HS SHA-256 non-closure | Reproduced bounded route/DRC/LVS failure | Investigation only |

Only the first three rows are production-Agent code changes. The limitation and
design cases must not be "fixed" by weakening decks, constraints, or Gates.

## 4. RMD2-P0-01: Complete DRC Timeout Termination

### Evidence and root cause

Nangate45 SHA-256 ran KLayout under:

```text
timeout --signal=TERM --kill-after=60 7200 bash .../klayout.sh ...
```

At timeout:

- GNU `timeout` exited;
- the shell wrapper exited;
- KLayout survived as a 99%-CPU process with `PPID=1`;
- `tee` retained the pipe;
- `run_drc.sh` could not return; and
- the engineer loop could not record or escalate the stuck result.

The surviving process had to be killed externally. Only then did the Agent
write `status=stuck`, `exit_code=124`, and terminal state `escalated`.

`run_drc.sh` supervises a shell wrapper rather than the complete checker
process group. The wrapper starts KLayout as a child without replacing itself,
so killing the monitored shell does not reliably kill every descendant.

### Implementation

Use an explicit process-group supervisor:

1. Resolve `KLAYOUT_CMD`, the frozen GDS, and the DRC deck before launch.
2. Start the checker in a new session/process group and record its process-group
   ID.
3. Redirect checker output directly to the run-local DRC log. Do not depend on
   a `timeout | tee` pipeline whose pipe can remain open after the monitored
   wrapper dies.
4. On timeout, send `TERM` to the complete process group.
5. After the grace period, send `KILL` to the same process group.
6. `wait` for cleanup and verify that no descendant remains before writing the
   final report.
7. Preserve exit code 124 and the existing stuck-rule diagnosis.
8. Add an `EXIT`, `INT`, and `TERM` cleanup trap so cancellation cannot orphan
   the checker.

The simplest platform-independent path is to execute `KLAYOUT_CMD` directly.
The current ORFS `klayout.sh` wrapper only checks the command and prints its
version; `run_drc.sh` already resolves and records that version itself. If a
wrapper must remain, it must use `exec "$KLAYOUT_CMD" "$@"` or be supervised as
part of an explicitly killed process group.

A small reusable bounded-process helper is acceptable if LVS or future
checkers will share it. Do not add a large scheduler abstraction for one call.

### Files

Required:

- `r2g-skills/signoff-loop/scripts/flow/run_drc.sh`
- a new timeout/process-tree regression test under
  `r2g-skills/signoff-loop/tests/`

Conditional:

- a small shared checker supervisor under
  `r2g-skills/signoff-loop/scripts/flow/`, if reused by DRC and LVS

### Acceptance tests

1. A fake checker that ignores `TERM` and spawns a child is fully removed after
   the grace period.
2. No process with the test checker command remains after `run_drc.sh` returns.
3. The result is `status=stuck`, `exit_code=124`, with the correct run and GDS
   digest.
4. `fix_signoff.sh` records `stop_stuck` and the engineer loop reaches
   `escalated` without operator action.
5. A normal clean checker still reports clean and preserves its output log.
6. A checker returning violations still reports fail rather than timeout.
7. Rerunning Nangate45 SHA-256 terminates within the configured budget plus
   cleanup grace period.

## 5. RMD2-P0-02: Digest-Complete Repair/Resume Lineage

### Evidence and root cause

Nangate45 I2C was repaired from route. The new run reused synth, floorplan,
place, and CTS from its parent run. `resume_meta.json` recorded:

```json
"synth": {
  "artifact": "1_synth.v",
  "sha256": null,
  "parent_run": "RUN_2026-07-23_23-29-37_2475714_1207"
}
```

The active ORFS flow produces `1_synth.odb` and `1_2_yosys.v`, not
`1_synth.v`. The canonical stage map in `run_orfs.sh` is therefore wrong.

`signoff_gate.py` accepts the recorded parent when its stage ledger is clean,
even when the recorded artifact digest is null. It also does not rehash the
consumed artifact and compare it with the recorded digest. The gate consequently
reported complete recorded lineage and allowed graph generation.

The current Nangate45 result is 40/49 Gate cells and 2/4 strict E2E fixtures.
I2C fails `FLOW`, and its downstream `SIGNOFF`, `FLOW2GRAPH`, `GRAPH`, and
`PUBLISH` cells receive no passing credit. The production graph path
nevertheless created technically valid graph artifacts from
provenance-incomplete input. After the Agent fix, those cells may pass only when
the repaired run carries complete verified lineage; no existing failure should
be relabeled or bypassed to recover the score.

### Implementation

#### 5.1 Define one versioned stage-artifact contract

Use canonical ORFS artifacts:

- synth: `1_synth.odb`;
- floorplan: `2_floorplan.odb`;
- place: `3_place.odb`;
- CTS: `4_cts.odb`;
- route: `5_route.odb`; and
- finish: `6_final.odb`, plus final DEF and GDS publication fingerprints.

Keep this mapping in one versioned contract rather than duplicating file names
across shell and Python code.

#### 5.2 Record full-run stage evidence

After each stage succeeds, record:

- stage name and status;
- run tag;
- canonical artifact path;
- artifact size and SHA-256;
- platform, design, and flow variant; and
- timestamp and toolchain fingerprint.

A versioned `stage_artifact_manifest.json` or equivalent append-only schema is
preferred over adding unstructured fields to log text.

#### 5.3 Record resume evidence before downstream mutation

Before cleaning and rerunning the selected stage:

1. resolve each reused stage from one explicit parent run;
2. verify that the parent stage completed successfully;
3. hash the exact artifact currently being consumed;
4. compare it with the parent's recorded stage digest;
5. write the parent run and digest into the new run's lineage manifest; and
6. stop before rerun if any stage is missing, null, ambiguous, or mismatched.

Do not silently fall back to the newest sibling run for strict V1 publication.
Legacy reconstructed lineage may be retained only as a non-clean,
operator-review state.

#### 5.4 Verify lineage at graph gating

`signoff_gate.py` must independently verify:

- all six canonical stages are represented locally or through recorded parent
  lineage;
- every reused stage has a non-empty valid SHA-256;
- each parent run exists and has a successful matching stage record;
- the artifact bytes still match the recorded digest;
- design, platform, and flow variant match across the lineage; and
- no cycle, ambiguous parent, or cross-project reference exists.

Any failure must set `orfs.status=incomplete`, add a blocker, and prevent
`b/c/d/e/f_graph.pt` publication.

The graph manifest should include a lineage-manifest digest or root digest so
the final dataset records exactly which implementation generation it used.

### Files

Required:

- `r2g-skills/signoff-loop/scripts/flow/run_orfs.sh`
- `r2g-skills/def-graph/scripts/flow/signoff_gate.py`
- `r2g-skills/signoff-loop/tests/` for stage-manifest and resume tests
- `r2g-skills/def-graph/tests/` for fail-closed lineage tests

Potentially required if the graph manifest does not already preserve the gate
evidence digest:

- `r2g-skills/def-graph/scripts/flow/run_graphs.sh`
- the graph-manifest writer used by `run_graphs.sh`

### Acceptance tests

1. A route-only repair with valid parent digests passes lineage validation.
2. `sha256=null` blocks graph generation.
3. A nonexistent canonical artifact blocks the resume before route reruns.
4. Mutating one reused ODB after lineage recording blocks graph generation.
5. A parent run from another design, platform, flow variant, or project is
   rejected.
6. A lineage cycle or ambiguous parent is rejected.
7. A legacy reconstructed-only lineage cannot produce a strict clean graph.
8. A valid repair produces one graph generation whose manifest contains the
   verified lineage digest.
9. Nangate45 I2C becomes eligible for strict graph publication only after the
   repaired route run has a complete verified lineage; no signoff threshold is
   relaxed.

## 6. RMD2-P1-01: Fail-Closed Strict-Platform Installation

### Evidence and current protection

The active server now passes strict capability checks on all three platforms.
The runtime ENV Gate would reject missing collateral before a campaign starts.
Therefore this item did not inflate the current clean dataset.

However, `install_platform_rules.sh` still runs Nangate45 DRC, LVS, and antenna
installers as best-effort commands and converts installer failures into hints.
A fresh-host setup can appear to complete before all collateral for a requested
strict platform is available.

### Implementation

1. Add an explicit selected-platform or strict-platform list to `eda-install`.
2. For every selected strict platform, treat a missing installer, non-zero
   installer result, missing output file, or failed canary as a fatal setup
   error.
3. Keep best-effort behavior only for unselected optional platforms.
4. Run `platform_capability.py --strict` after installation using the exact
   resolved ORFS, PDK, KLayout, Magic, and Netgen environment.
5. Save the capability result and collateral digests in the installation
   manifest.
6. Make repeated installation idempotent.

### Files

- `r2g-skills/eda-install/scripts/setup/install_platform_rules.sh`
- the `eda-install` bootstrap/entry script that selects tiers or platforms
- `r2g-skills/signoff-loop/scripts/flow/platform_capability.py`, only if its
  machine-readable output needs extension
- focused `eda-install` and capability tests

### Acceptance tests

1. Removing `FreePDK45.lylvs` makes a selected Nangate45 installation fail.
2. Removing the Nangate45 antenna model makes installation fail.
3. Reintroducing both files and rerunning installation passes and is
   idempotent.
4. A broken Sky130HS `.lyt` fails its required geometry postcondition.
5. All three selected platforms pass strict capability checks in a fresh
   temporary ORFS checkout.

## 7. Tool and Design Limitations to Preserve

### 7.1 RMD2-LIM-01: Nangate45 full-DRC scalability

Measured full KLayout DRC times ranged from approximately 22 seconds for GCD to
more than two hours for SHA-256. I2C required approximately 87 minutes per full
check, and its legal antenna repair therefore required another full 87-minute
verification.

This is a checker performance limitation, not evidence that physical stages
were rerun. The immediate V1 action is:

- enforce the configured checker and total-design budgets correctly;
- report timeout as `stuck/incomplete`;
- preserve design size and checker wall time in the run manifest;
- publish scale-stratified throughput in experiments; and
- investigate deck optimization separately.

Do not substitute BEOL-only DRC for full DRC in strict publication and do not
raise timeouts without recording the changed resource budget.

### 7.2 RMD2-DES-01: Sky130HD GCD `m3.2`

The six minimum-met3 spacing violations are reproducible real design results.
Keep GCD as a fixed negative physical-design case. A route or density
intervention may be evaluated, but it must clear the unchanged full deck
without relaxing clock, area, or checks.

### 7.3 RMD2-DES-02: Sky130HS SHA-256 non-closure

After bounded `density_relief`, SHA-256 still had 32 route violations, eight
DRC violations, and an LVS mismatch. The Agent stopped with
`catalog_exhausted`, which is the correct bounded behavior.

Keep this as a repair-coverage case. Any new Recipe must be tested through a
controlled A/B trial and must not be promoted unless all strict signoff
dimensions remain non-regressive.

## 8. Implementation Order

1. Add the failing timeout process-tree regression.
2. Implement complete process-group cleanup in `run_drc.sh`.
3. Add failing null, tampered, foreign-parent, and legacy-lineage graph-gate
   tests.
4. Implement the canonical stage-artifact manifest and strict resume lineage.
5. Make strict-platform installation fail closed.
6. Run focused unit tests and small canaries on all three platforms.
7. Rerun the same fixed three-platform campaign from fresh roots.
8. Archive scorecards, clean indexes, process-cleanup evidence, lineage
   manifests, artifact digests, wall times, and toolchain fingerprints.

## 9. Files That Should and Should Not Change

### Production Agent files expected to change

- `r2g-skills/signoff-loop/scripts/flow/run_drc.sh`
- `r2g-skills/signoff-loop/scripts/flow/run_orfs.sh`
- `r2g-skills/def-graph/scripts/flow/signoff_gate.py`
- `r2g-skills/eda-install/scripts/setup/install_platform_rules.sh`
- corresponding signoff-loop, def-graph, and eda-install tests
- possibly the graph-manifest writer if it does not yet store a lineage digest

### Files that should remain unchanged for this remediation

- Pilot registry, fixtures, Gate definitions, and score thresholds
- DRC and LVS rule semantics
- strict signoff acceptance thresholds
- graph feature and label schema
- Fmax targets selected by the fixed campaign

## 10. Expected Outcome

These changes are not expected to make every design clean. They should:

- remove the only observed need for manual process intervention;
- ensure no graph is produced from digest-incomplete resume lineage;
- make fresh strict-platform installation reliable; and
- leave genuine DRC, route, LVS, and scalability failures visible.

After remediation, score improvement is valid only when produced by the same
fixed fixtures and unchanged strict policy. A lower score caused by newly
fail-closed provenance is acceptable until the corresponding evidence is made
complete.
