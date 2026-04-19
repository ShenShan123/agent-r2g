# ORFS Batch Pass 4 Report (in progress)

**Date:** 2026-04-19
**Task:** Retry the 19 remaining failures from Pass 3, continue evolving the r2g-rtl2gds skill
**Platform:** `nangate45`
**Skill:** `r2g-rtl2gds`

## Pass 3 → Pass 4 Audit

Re-examined the 19 cases recorded as remaining failures. Several conclusions changed the strategy:

- Many cases had `stage_log.jsonl` with a single successful `synth` entry and no downstream stages. Investigation showed these were not actual timeouts — they came from an earlier synth-only test invocation (`ORFS_STAGES=synth`). A full re-run should have always been tried first.
- The 4 iscas89 cases (`s1196`, `s820`, `s832`, `s953`) were reported in Pass 3 as "missing source netlists", but the `iscas89_*_rewritten.v` files do exist and synth completes in ~7s. They only needed a full flow run.
- `arm_core`: synth succeeded at 2863s but floorplan hit the default 3600s cap — needs `ORFS_TIMEOUT=14400`.
- `koios_gemm_layer`: synth itself timed out at 3600s deep in Yosys DFF optimization — needs `ORFS_TIMEOUT=14400`.
- Three FIFO designs (`axis_ram_switch`, `eth_mac_1g_fifo`, `eth_mac_mii_fifo`) completed synth+floorplan but hit 3600s during global placement. Root cause: `SYNTH_MEMORY_MAX_BITS=131072` lets 4 KB x 11-bit FIFOs expand into ~45K flops, which in turn inflates placement. Lowering the memory budget to 32768 was tried and reverted — it causes Yosys to reject the memory entirely and fails synth earlier. Correct fix: longer place timeout.
- `koios_lenet` (227K-line HLS LeNet, 117 modules): genuine megadesign; synth alone takes >4h even with all budgets raised. Needs dedicated 8h+ run or pre-synthesized netlist. Documented under new "HLS megadesign" failure pattern.
- `clog2_test`: documented permanent skip (zero-logic).

## Pass 4 Retry Buckets

Sized by failure stage as reported in `backend/RUN_*/stage_log.jsonl`:

| Bucket | Designs | Policy |
|--------|---------|--------|
| A: ethernet/axis synth-succeeded | 8 | full re-run, `ORFS_TIMEOUT=7200` |
| B: FIFO place-timeout | 3 | full re-run, `ORFS_TIMEOUT=14400` (memory budget unchanged) |
| C: iscas89 (tiny) | 4 | full re-run, `ORFS_TIMEOUT=3600` |
| D: synth-timeout (arm_core, koios_gemm_layer) | 2 | full re-run, `ORFS_TIMEOUT=14400` |
| E: HLS megadesign | 1 (koios_lenet) | skipped — 8h+ budget required |
| F: zero-logic | 1 (clog2_test) | permanent skip |

Driver: `tools/retry_pass4.sh` (3-way parallel). Log: `design_cases/_batch/retry_pass4.jsonl`.

## Skill Evolution Delivered

### 1. `scripts/flow/run_orfs.sh` — FROM_STAGE validation guard

Previous behavior: if a caller passed a value to `FROM_STAGE` that didn't match any entry in `ORFS_STAGES_LIST` (e.g., a timeout seconds value accidentally routed through the wrong positional arg), the for-stage loop silently skipped every stage and exited 0. This produced ghost "passes" in batch retries — designs reported as passing without any stages actually running.

Added a guard that validates `FROM_STAGE` against `ORFS_STAGES_LIST` and exits 2 with a clear error if it doesn't match. Tested.

### 2. `tools/fix_orfs_failures.py` — stage-aware timeout fix

`apply_timeout_fix` now:
- Reads the most recent `stage_log.jsonl` via a new `_last_timed_out_stage(case)` helper to determine which stage actually timed out.
- Never lowers `SYNTH_MEMORY_MAX_BITS`, even for place-stage timeouts. Shrinking the budget below the largest inferred memory (e.g., 4096×11 = 45 Kbit FIFO RAMs) causes Yosys to reject the memory and fails synth — strictly worse than a place-stage timeout.
- Returns `caller_must_raise_timeout_to: 14400` in its result so downstream tooling can choose the appropriate `ORFS_TIMEOUT`.

### 3. `references/failure-patterns.md` — expanded timeout + HLS patterns

- Stage-timeout section now lists per-stage actions (synth vs place vs route) and points readers to `stage_log.jsonl` as the authoritative source of which stage failed.
- New "HLS megadesign" pattern covering the 100K+ line Vivado-HLS / Bambu class of designs (symptoms: 100+ modules, Yosys stuck in AST/OPT, synth >2h; action: 8h timeout or pre-synthesized netlist; known case: `koios_lenet`).

### 4. `CLAUDE.md` — pitfall documentation

Added three cautionary bullets:
- HLS megadesign class and how to budget for it.
- Don't lower `SYNTH_MEMORY_MAX_BITS` to fix a place-stage timeout.
- `ORFS_TIMEOUT` is per-stage, not total; always read `stage_log.jsonl` before choosing a fix.

### 5. `tools/retry_pass4.sh`

Four-bucket retry runner with pipe-separated arg parsing, parallel 3-way execution, and per-design JSONL logging.

## Results (in progress)

| Case | Result | Elapsed | Notes |
|------|--------|---------|-------|
| verilog_ethernet_axis_baser_rx_64 | PASS | 572s | Full flow clean |
| verilog_ethernet_eth_mac_10g | PASS | 972s | Full flow clean |
| verilog_ethernet_axis_baser_tx_64 | FAIL(2) | 5464s (killed early) | Yosys lfsr AST derivation taking >90 min; killed at ~91 min yosys time. Needs 14400s recovery budget. |
| verilog_ethernet_arp | PASS | 10026s | Dense routing design; place alone took 52 min, route 92 min |
| verilog_ethernet_ip_complete | RUNNING | 2h+ | In detailed route |
| verilog_ethernet_ip_complete_64 | RUNNING | ~80 min | In cts after 65 min place |
| verilog_ethernet_udp_complete | RUNNING | just started | |
| (12 others) | NOT_STARTED | | Queued |

Key confirmation from Bucket A results so far: the theory that these designs just needed a full-flow re-run (not config changes) was correct for 3/4 completed. The outlier is `axis_baser_tx_64` — its genvar-parameterized lfsr pattern needs more than 2h of Yosys front-end budget and will be handled by `pass4_recover_timeouts.sh` with 14400s.

## Overall Progress

| Pass | Cumulative Pass | Rate |
|------|-----------------|------|
| Pass 1 | 402 | 81.2% |
| Pass 2 | 461 | 93.1% |
| Pass 3 | 476 | 96.2% |
| Pass 4 | (pending) | (pending) |
