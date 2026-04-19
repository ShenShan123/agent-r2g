# ORFS Batch Pass 3 Report

**Date:** 2026-04-19
**Task:** Retry the 34 remaining failures from Pass 2, improve the r2g-rtl2gds skill
**Platform:** `nangate45`
**Skill:** `r2g-rtl2gds`

## Executive Summary

| Metric | Pass 2 (prior) | Pass 3 (this session) | Combined |
|--------|----------------|----------------------|----------|
| Total designs | 495 | 34 retried | 495 |
| ORFS pass | 461 (93.1%) | 15 of 34 (44.1%) | **476 (96.2%)** |
| ORFS fail | 34 | 19 | 19 |

**Rescued 15 of 34 previously-failed designs** through config fixes, missing-header recovery, and stage resumption. The remaining 19 failures are either genuinely intractable at current timeout budgets or missing source RTL.

## Fixes Applied and Results

### Group A: Route-Stage Resume (7/7 passed)

Used `FROM_STAGE=route` with `ORFS_TIMEOUT=14400` to resume designs that had completed through CTS but timed out during routing.

| Design | Elapsed | Result |
|--------|---------|--------|
| verilog_axis_axis_async_fifo_adapter | 10911s | PASS |
| verilog_axis_axis_fifo | 11111s | PASS |
| verilog_axis_axis_fifo_adapter | 10794s | PASS |
| verilog_axis_axis_frame_length_adjust_fifo | 10979s | PASS |
| wbscope_axil | 1149s | PASS |
| wbscope_wishbone | 1148s | PASS |
| zipcpu_wbdmac | 4458s | PASS |

### Group B: Wrong Top Module Fix (2/3 passed)

`design_meta.json` had picked tiny leaf modules for multi-module RTL files. Fixed `DESIGN_NAME` and `current_design` in config/SDC.

| Design | Old Top | Correct Top | Clock | Result |
|--------|---------|-------------|-------|--------|
| large_mac1 | `Fadder_` | `mac1` | `clk` | PASS |
| large_mac2 | `Fadder_` | `mac2` | `clk` | PASS |
| koios_lenet | `cast_ap_fixed_...` | `myproject` | `ap_clk` | FAIL (synth timeout/killed — HLS 180K-line file) |

### Group C: Missing Include Files (6/6 headers found, 6/6 passed ORFS)

Reconstructed 6 header files from upstream open-source repos:

| Design | Missing Header | Source Repo | Result |
|--------|---------------|-------------|--------|
| biriscv_core | `biriscv_defs.v` (524 lines) | ultraembedded/biriscv | PASS |
| oc_i2c_master_top | `i2c_master_defines.v`, `timescale.v` | olofk/i2c | PASS |
| prince_core | `prince_round_functions.vh` (250 lines) | secworks/prince | PASS (after config fix) |
| prince_wrapper | `prince_round_functions.vh` | secworks/prince | PASS (after config fix) |
| r8051_core | `instruction.v` (246 lines) | risclite/R8051 | PASS |
| uriscv_core | `uriscv_defs.v` (202 lines) | ultraembedded/core_uriscv | PASS |

prince_core and prince_wrapper initially failed with FLW-0024 (place density > 1.0) on 120x120 die. Fixed by switching to `CORE_UTILIZATION=20` (later tuned to 10 by sweep).

### Group D: clog2_test (unfixable)

`simple_op` = `assign out = in;` — synthesizes to ~2.4 um² (essentially zero cells). Cannot go through P&R. Documented as known limitation.

### Group E: Synthesis/Place Timeouts at 4h (0/13 passed)

All 13 designs exceed the 14400s per-stage timeout. Root cause is `SYNTH_MEMORY_MAX_BITS=131072` forcing Yosys to expand memories into flop arrays, which explodes synthesis time and cell count.

| Design | Timeout Stage | Notes |
|--------|--------------|-------|
| arm_core | place (14400s) | ABC mapping alone took ~2h |
| koios_gemm_layer | place (14400s) | Large CNN layer |
| verilog_ethernet_arp | synth (14400s) | Ethernet MAC with deep FIFOs |
| verilog_ethernet_axis_baser_rx_64 | synth (14400s) | |
| verilog_ethernet_axis_baser_tx_64 | synth (14400s) | |
| verilog_ethernet_eth_mac_10g | synth (14400s) | |
| verilog_ethernet_ip_complete | synth (14400s) | |
| verilog_ethernet_ip_complete_64 | synth (14400s) | |
| verilog_ethernet_udp_complete | synth (14400s) | |
| verilog_ethernet_udp_complete_64 | synth (14400s) | |
| verilog_ethernet_eth_mac_1g_fifo | place (14400s) | Place-stage resume also failed |
| verilog_ethernet_eth_mac_mii_fifo | place (14400s) | Place-stage resume also failed |
| verilog_axis_axis_ram_switch | place (14400s) | Place-stage resume also failed |

### Group F: Missing RTL (4 designs)

| Design | Issue |
|--------|-------|
| iscas89_s1196 | Only DFF helper present; actual netlist missing |
| iscas89_s820 | Only DFF helper present; actual netlist missing |
| iscas89_s832 | Only DFF helper present; actual netlist missing |
| iscas89_s953 | Only DFF helper present; actual netlist missing |

## Skill Improvements Delivered

### 1. `tools/setup_rtl_designs.py` — Top Module Validation

Added `validate_top_module()` that detects when `design_meta.json` picks a tiny leaf module for multi-module RTL files (HLS-generated, VTR benchmarks). Heuristic: if the selected module is <10% the size of the largest module in a file with 5+ modules, pick the module matching the filename stem, with most ports, or last in file.

### 2. `tools/fix_orfs_failures.py` — Wrong Top Module Fixer

Added `apply_wrong_top_fix()` handler that:
- Scans RTL for all module definitions
- Compares selected top against largest/last/stem-matching modules
- Updates `DESIGN_NAME` in config.mk and `current_design` in SDC
- Auto-detects HLS clock (`ap_clk` for `myproject`)
- Integrated into `apply_other()` dispatch: FLW-0024 and PDN-0179 now try wrong-top fix before density/PDN fixes

Also added: RTL-error detector with log context extraction, structural preservation checker.

### 3. `skills/r2g-rtl2gds/references/failure-patterns.md`

Added two new patterns:
- **Wrong top module selected** — symptoms, root cause, action, known cases
- **Zero-logic design** — wire-only designs that can't go through P&R

### 4. `tools/retry_failures.sh`

Batch retry script with:
- FROM_STAGE resume support for route/place-stage timeouts
- Pipe-separated argument passing (fixed xargs word-splitting bug)
- Per-design JSONL result logging

### 5. Additional Batch Tooling

- `tools/check_structural_preservation.py` — B-threshold structural rules for RTL auto-fix
- `tools/run_full_sweep.sh` — batch sweep with skip-existing
- `tools/run_two_designs.sh` — ORFS+LVS+RCX for named design lists
- `tools/sweep_status.sh` — one-shot progress snapshot

### 6. CLAUDE.md / SKILL.md Updates

- Documented wrong-top-module and zero-logic pitfalls
- Added batch failure tooling references

## Commits

- `affc6d7` — feat: add wrong-top-module detection and batch retry tooling
- `cea9b86` — feat: RTL-error detector, structural preservation, and batch sweep tooling

Both pushed to `origin/main`.

## Remaining 19 Failures — Analysis and Next Steps

### Synthesis Timeout Designs (13) — Possible Approaches

These designs all use `SYNTH_MEMORY_MAX_BITS=131072` which forces Yosys to expand memories into flip-flop arrays. This creates two problems:
1. Synthesis time explodes (>4h for large FIFO designs)
2. Cell count explodes, making placement slow

**Option A: Raise timeout to 8h (`ORFS_TIMEOUT=28800`)**
- Pros: No config changes needed, may rescue arm_core (was at 1h58m when killed)
- Cons: Expensive; ethernet designs may still not converge
- Expected rescue: ~2-3 designs

**Option B: Use `SYNTH_MEMORY_MAX_BITS=4096` (default) and accept memory inference limits**
- Pros: Fast synthesis, original batch already passed these at 4096
- Cons: Large memories get rejected by Yosys, flow fails earlier
- Expected rescue: 0 (these designs failed originally because of this limit)

**Option C: Integrate fakeram45 macros for FIFO/memory designs**
- Pros: Correct approach — memories become hard macros, synthesis is fast, area is realistic
- Cons: Requires per-design LEF/LIB/CDL setup, macro placement TCL
- Expected rescue: 10-13 designs
- Effort: Medium — validated macro flow exists for riscv32i/tinyRocket/swerv/bp_multi_top

**Option D: Use `SYNTH_MEMORY_MAX_BITS=16384` (compromise)**
- Pros: Smaller memories expand to flops (fast), larger ones get rejected (clear error)
- Cons: Designs with 16K-128K memories still fail
- Expected rescue: 3-5 designs (those with memories in the 4K-16K range)

**Recommended:** Option C for ethernet FIFO designs (they have real memories that should be macros), Option A for arm_core/koios_gemm_layer (close to converging).

### Missing ISCAS89 RTL (4)

Source netlists were at `/home/yuany/work/hdl-benchmarks-min/iscas89/verilog/` — a path on another machine. Need to obtain `s1196.v`, `s820.v`, `s832.v`, `s953.v` from the original hdl-benchmarks-min repo.

### koios_lenet (1)

HLS-generated 180K-line LeNet design. Yosys synthesis alone takes >4h. Options:
- Run with `ORFS_TIMEOUT=28800` (8h) on a dedicated machine
- Pre-synthesize with Vivado HLS output and feed gate-level netlist to ORFS

### clog2_test (1)

Zero-logic design. Not fixable — skip permanently.

## Overall Progress

| Pass | Designs | Cumulative Pass | Rate |
|------|---------|----------------|------|
| Pass 1 | 495 | 402 | 81.2% |
| Pass 2 | 93 retried | 461 | 93.1% |
| Pass 3 | 34 retried | **476** | **96.2%** |
