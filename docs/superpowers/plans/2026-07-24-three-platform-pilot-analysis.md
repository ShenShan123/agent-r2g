# Three-Platform R2G V1 Pilot Revalidation Analysis

## 1. Scope and Experimental Integrity

This report revalidates the fixed R2G V1 Pilot on `nangate45`, `sky130hd`, and
`sky130hs` after updating the Agent to commit
`8d449b088382db7274d8cc93cc2088b9e032b2cb`.

Every campaign used:

- the same four pinned positive RTL fixtures;
- the same two negative-control fixtures;
- the same 11 Gate definitions and 49 applicable Gate cells;
- platform-specific Fmax search;
- four CPU cores per flow and one Agent worker; and
- the same graph schema and independent graph verifier.

The evaluated scorecards are:

- Nangate45:
  `/home/yangao/r2g_v1_pilot_2026_07_23_8d449b0_nangate45_run01/reports/pilot_report.md`
- Sky130HD:
  `/home/yangao/r2g_v1_pilot_2026_07_23_8d449b0_sky130hd_run01/reports/pilot_report.md`
- Sky130HS:
  `/home/yangao/r2g_v1_pilot_2026_07_23_8d449b0_sky130hs_run01/reports/pilot_report.md`

The comparison baseline is taken from the current archived JSON scorecards for
commit `202573701fdc8faa882846d71f409c540c726080`, rather than from prose copied
from an earlier intermediate grading pass.

No production Agent source was modified during these campaigns. One operator
intervention must nevertheless be disclosed:

1. Nangate45 SHA-256 exceeded the 7200-second DRC timeout, but the KLayout child
   survived as an orphan process. The process had to be killed externally before
   the Agent could record `status=stuck` and continue.

This report treats the Pilot only as an external measurement instrument. Pilot
implementation and maintenance are outside scope; the findings below are
limited to production Agent behavior and underlying EDA-tool limitations.

## 2. Comparative Results

| Platform | Previous Gate cells | Current Gate cells | Change | Previous strict E2E | Current strict E2E | Total measured time |
|---|---:|---:|---:|---:|---:|---:|
| nangate45 | 41/49 (83.7%) | 40/49 (81.6%) | -1 | 2/4 | 2/4 | 5.24 h |
| sky130hd | 39/49 (79.6%) | 45/49 (91.8%) | +6 | 0/4 | 3/4 | 0.80 h |
| sky130hs | 33/49 (67.3%) | 44/49 (89.8%) | +11 | 0/4 | 3/4 | 1.18 h |

All 12 positive platform/design combinations were exercised. The front half of
the pipeline was stable across them:
`ENV`, `ACQ`, `SYNTH`, `RTL2FLOW`, and `CONSTRAINT` all received full credit.
Every fixed negative control also passed.

The current strict publications are:

- Nangate45: GCD and WBUART32;
- Sky130HD: WBUART32, I2C, and SHA-256; and
- Sky130HS: GCD, WBUART32, and I2C.

Nangate45 I2C is physically signoff-clean and its five graph views verify, but
it is excluded from the strict end-to-end count because its repair/resume
lineage is not digest-complete. Section 4.2 explains why the production graph
gate nevertheless allowed graph generation and why that behavior remains
unsafe.

The current 40/49 Gate-cell score is fail-closed for this defect. Nangate45 I2C
fails `FLOW`, and `SIGNOFF`, `FLOW2GRAPH`, `GRAPH`, and `PUBLISH` receive no
downstream passing credit. The strict E2E total remains 2/4, and I2C is not
listed in the clean dataset index. The production graph path nevertheless
generated graph artifacts from digest-incomplete lineage, so the underlying
Agent defect remains actionable.

## 3. Fixes Confirmed by Real Runs

### 3.1 Strict platform capability is now effective

The active toolchain reports `STRICT-READY` for all three platforms. Nangate45
has a usable `FreePDK45.lylvs` deck and antenna model; Sky130HD has full DRC,
Netgen LVS, antenna, timing, and RCX capability; and the Sky130HS `.lyt` repair
passes the GDS geometry canary.

This removes the previous false-green ENV behavior on this host. Missing
strict-signoff collateral is no longer the reason for current platform
failures.

### 3.2 DRC is checker-only

The current `run_drc.sh` selected a frozen `6_final.gds` from an explicit
backend run and invoked KLayout directly. Clean DRC checks did not restart
synthesis, floorplan, placement, CTS, route, or finish.

When Nangate45 I2C required antenna repair, the Agent intentionally reran only
route and finish. This is a legitimate repair rerun, not the previous
`make drc` dependency-rebuild defect.

### 3.3 Final signoff artifacts are digest-bound

Fresh DRC and LVS reports carry an explicit run tag, GDS SHA-256, DEF SHA-256,
deck/rule digest, and tool identity. Successful graph gates verified that the
selected final DEF, DRC, LVS, and route reports belonged to the same backend
generation.

Nangate45 I2C provides a concrete positive result:

- KLayout DRC changed from nine antenna violations to zero;
- three `ANTENNA_X1` cells were physically inserted;
- route and OpenROAD antenna counts were zero;
- KLayout LVS was clean; and
- DRC and LVS recorded the same run tag and GDS/DEF digests.

### 3.4 Sky130HS GDS and LVS are operational

Fresh Sky130HS layouts retained real routing and pin geometry. GCD, WBUART32,
and I2C passed Netgen LVS and strict signoff, then produced five independently
verified graph views. The previous systematic zero-port extraction failure did
not recur.

### 3.5 Dirty designs remain blocked

Sky130HD GCD retained six real `m3.2` spacing violations. Sky130HS SHA-256
retained route, DRC, and LVS failures after one bounded repair attempt.
Nangate45 SHA-256 reached a bounded DRC-stuck diagnosis. None entered the
campaign's clean dataset index.

This confirms that the score improvement came from repairing orchestration and
platform capability, not from weakening signoff or publication policy.

## 4. Remaining Cross-Platform Defects

### 4.1 P0: DRC timeout does not terminate the complete process tree

#### Evidence

Nangate45 SHA-256 ran full KLayout DRC under:

```text
timeout --signal=TERM --kill-after=60 7200 bash .../klayout.sh ...
```

At 7200 seconds the `timeout` process and shell wrapper exited, but
`/usr/bin/klayout` survived with `PPID=1` and continued consuming one CPU at
approximately 99%. The `tee` process retained the output pipe, so
`run_drc.sh`, `fix_signoff.sh`, the engineer loop, and the Pilot remained
blocked. Only an external `SIGKILL` allowed the pipeline to continue.

After cleanup, the Agent correctly produced:

- `status=stuck`;
- `exit_code=124`;
- `reason=klayout_polygon_op_no_progress`;
- `stuck_at_rule=FreePDK45.lydrc:131`; and
- terminal engineer-loop state `escalated` with
  `reason=signoff_stuck_scan`.

The diagnosis is therefore correct, but autonomous termination is not.

#### Root cause

`run_drc.sh` places GNU `timeout` around the ORFS shell wrapper. The wrapper
starts KLayout as a child without `exec`, and the timeout path does not reliably
kill the descendant process group. `--kill-after=60` kills the monitored shell,
not every surviving checker descendant.

#### Impact

One unresponsive checker can prevent the entire single-worker campaign from
reaching graph gating, grading, or an escalation record. This is a liveness
failure and makes the Nangate45 campaign operator-assisted rather than fully
autonomous.

### 4.2 P0: repair/resume lineage accepts a null digest and allows graph generation

#### Evidence

Nangate45 I2C was repaired from route. Its new run correctly recorded route and
finish locally and declared synth, floorplan, place, and CTS as reused from the
parent run. However, `resume_meta.json` contains:

```json
"synth": {
  "artifact": "1_synth.v",
  "sha256": null,
  "parent_run": "RUN_2026-07-23_23-29-37_2475714_1207"
}
```

The active ORFS results directory contains `1_synth.odb` and `1_2_yosys.v`, not
`1_synth.v`. The digest recorder therefore fingerprints a nonexistent
canonical artifact.

`signoff_gate.py` classifies a reused stage as recorded when the parent run
exists and its stage ledger is clean. It copies the digest value into the
report but does not require the value to be non-null and does not rehash the
consumed artifact. The I2C gate consequently reported:

```text
orfs.status=complete
lineage_quality=recorded
synth.sha256=null
overall status=pass
```

Graph conversion and atomic project-level publication then succeeded. The
campaign's strict six-stage evidence check rejected the generation, so I2C was
excluded from the clean dataset index. This prevented a strict E2E false
positive, but the production graph gate itself remains fail-open.

The same null synth digest appears in the repaired Sky130HS SHA-256 lineage,
although that design was independently blocked by real signoff failures.

#### Root cause

Two defects interact:

1. `run_orfs.sh::_write_resume_meta` uses an incorrect synth artifact name.
2. `signoff_gate.py::_resolve_lineage` validates parent stage status but does
   not require or verify the recorded artifact digest.

The legacy reconstructed-lineage fallback is also weaker than the strict V1
policy because it can attribute a stage to the newest clean sibling without
proving that its exact bytes were consumed.

#### Impact

A repaired layout can receive a passing graph signoff gate even when the full
upstream implementation lineage is not content-addressed. This weakens the
claim that every clean graph is provenance-complete.

### 4.3 P1: fresh-host installation is not yet guaranteed fail-closed

The current server is strict-ready, and campaign ENV checks fail closed.
However, `install_platform_rules.sh` still treats the Nangate45 LVS, DRC, and
antenna installers as best-effort commands and emits hints on failure. A fresh
installation may therefore finish its setup phase before all requested
strict-platform collateral is present, even though a later capability check
will reject the campaign.

This is no longer a data-trust defect because the runtime ENV Gate catches it,
but it remains a one-command installation and usability gap.

## 5. Platform-Specific Findings

### 5.1 Nangate45

GCD and WBUART32 are strict-clean publications. I2C demonstrates that the
installed antenna model, diode repair, KLayout DRC, and KLayout LVS can close a
medium design. Its strict publication remains blocked only by the resume
lineage defect.

SHA-256 completed ORFS and LVS but full KLayout DRC made no progress at
`FreePDK45.lydrc:131` within the two-hour budget. This is primarily a checker
scalability limitation. The Agent correctly refused repair recipes when no
violation class was available and escalated after the stuck result, but only
after the orphan process was manually removed.

### 5.2 Sky130HD

WBUART32, I2C, and SHA-256 are strict-clean publications. GCD alone retains six
reproducible `m3.2` minimum met3 spacing violations. This is a real
design/routing case, not evidence that the common signoff path is broken.

No unvalidated automatic repair should be added merely to raise the score.
Keep the case as a fixed regression fixture until a legal route intervention
clears the unchanged full deck.

### 5.3 Sky130HS

GCD, WBUART32, and I2C are strict-clean publications, confirming that the GDS
geometry and LVS repair is effective.

SHA-256 initially completed all six ORFS stages but retained routing
violations. The Agent applied a bounded `density_relief` repair from floorplan.
The new run still had 32 route violations, eight DRC violations, and an LVS
mismatch, then stopped with `catalog_exhausted`. This is preferable to an
unbounded loop and was correctly blocked from graph publication.

The latest repair run also demonstrates the shared resume-lineage issue:
synthesis was inherited with a null digest, so its strict six-stage evidence
remains incomplete.

## 6. Runtime and Scalability

Measured campaign times were approximately:

- Nangate45: 18,848 seconds (5.24 hours);
- Sky130HD: 2,893 seconds (48.2 minutes); and
- Sky130HS: 4,265 seconds (71.1 minutes).

Nangate45 signoff alone consumed 17,917 seconds. Its full KLayout DRC cost
increased sharply with design size:

- GCD: approximately 22 seconds;
- WBUART32: approximately 23.5 minutes;
- I2C: approximately 87 minutes per full check; and
- SHA-256: exceeded the 7200-second limit.

This is not the old upstream-rebuild defect: the checker stayed on one frozen
GDS and used one CPU. The V1 policy should report scale-stratified throughput
and enforce a total per-design budget, but it must not relabel a timeout,
BEOL-only result, or incomplete deck as strict clean.

## 7. Interpretation

The current runs establish substantial cross-platform capability while
preserving fail-closed dataset results:

- checker-only DRC is real;
- final signoff provenance is substantially stronger;
- all selected platforms are operational on this host;
- Sky130HS strict publication now works; and
- signoff failures remain fail-closed at the campaign clean-index boundary.

The remaining highest-value work is narrow rather than open-ended:

1. make timeout termination truly autonomous;
2. make repair/resume provenance content-complete and fail closed;
3. make selected strict-platform installation fail closed; and
4. preserve real design failures and checker scalability as measured
   limitations instead of weakening Gates.

## 8. Recommended Fix Order

1. Fix DRC process-tree termination and add a child-ignores-TERM regression.
2. Correct the canonical synth artifact and require verified digests for every
   reused stage before graph generation.
3. Make selected strict-platform rule installation fail closed on fresh hosts.
4. Rerun the same three campaigns without changing fixtures, Gate definitions,
   time budgets, or signoff policy.
