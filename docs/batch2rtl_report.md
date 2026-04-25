# batch2rtl — initial campaign

**Date:** 2026-04-25
**Source:** `batch2rtl/` (3 vendor sets: BOOM CPU, Faraday ASIC, Gaisler)
**Skill:** `r2g-rtl2gds`
**Platform:** `nangate45`

## Inventory

| Source | Designs | Lang | Lines (each) | Top module(s) | Macros | Tractability |
|--------|---------|------|--------------|---------------|--------|--------------|
| Faraday DMA | 1 | Verilog | 13K | `dma_top` | None — `ff_ram` is a flop array | **Easy — primary target** |
| Faraday DSP | 1 | Verilog | 35K | `t_top` | UMC SRAMs (10+ types: CM4k, EDM8k, EM4K, EM8K, PM4k, DM8k, EEPROM, EIO2k, ECM32kx24, BTBmem, RTBmem, LPS4x22) — empty stubs | Hard (need fakeram45 mapping per type) |
| Faraday RISC | 1 | Verilog | 45K | (multi-clock SYSCLK + BUSCLK) | UMC SRAMs + foundry tech cells (DCACHE_TAG, IRAM_VALID, etc.) — empty stubs | Hard (multi-clock + macro mapping) |
| Gaisler leon2 | 1 | VHDL (mostly) | n/a | n/a | n/a | **Out of scope** — VHDL only, skill is Verilog-only |
| BOOM CPU | 12 variants | Chipyard Verilog | 360K-660K | `ChipTop` (inside `TestHarness`) | OpenRAM SRAMs (17 unique sizes, separate file) | Megadesign — defer |

## Pass 1 results

### Designs attempted

| Design | ORFS | Timing | DRC | LVS | RCX | Notes |
|--------|------|--------|-----|-----|-----|-------|
| `faraday_dma` | **PASS** (868s) | clean (WNS +5.59 ns, TNS 0) | stuck on FreePDK45.lydrc:121 (env limit) | skipped (no rules in this install) | **PASS** (14784 nets, 19MB SPEF) | RTL fixup: `int` → `int_w` in `dma_ctlrf.v`. Stage timings: synth 57s / floorplan 24s / place 484s / cts 32s / route 252s / finish 66s |

### Designs deferred

- Faraday DSP, Faraday RISC: require fakeram45 macro substitution for 10+ different SRAM types and (RISC) multi-clock SDC handling. The skill currently supports macro designs end-to-end (riscv32i, swerv, bp_multi_top all validated) but each new SRAM type needs a `fakeram45_<rows>x<bits>.{lef,lib}` mapping. Defer until a per-type stub-to-fakeram lookup table is added or the user provides one.
- Gaisler leon2: VHDL — ORFS/Yosys path here is Verilog-only.
- BOOM 12 variants: Chipyard-generated megadesigns at 360K-660K lines plus 17 OpenRAM SRAM macro sizes. Comparable to `bp_multi_top` (~200K cells) but larger. Each variant would need:
  1. OpenRAM SRAM macro stubs adapted for nangate45 / fakeram45
  2. Per-stage timeouts in the `ORFS_TIMEOUT * 6` budget (16 h+)
  3. Macro placement Tcl + halo tuning
  Defer to a focused stretch run.

## Skill evolution from this campaign

1. **`scripts/project/validate_config.py`** — `check_reserved_keywords` previously only flagged reserved words in `input/output/inout` declarations. Faraday `dma_ctlrf.v` uses `wire [..] int;` (internal net), which slipped past the validator and only surfaced as `syntax error, unexpected TOK_INT` deep in `1_1_yosys_canonicalize`. The check now covers `wire`/`reg`/`logic`/`integer`/`tri`/`supply*` declarations and also catches keyword identifiers in comma-list declarations.
2. **`scripts/flow/run_drc.sh`, `scripts/flow/run_lvs.sh`** — both looked for the ORFS config at `flow/designs/<plat>/<design>/config.mk`, but `run_orfs.sh` actually places it at `flow/designs/<plat>/<design>/<variant>/config.mk` so two FLOW_VARIANTs of the same DESIGN_NAME don't collide. The signoff scripts now check the variant path first and fall back to the legacy path.
3. **`references/orfs-playbook.md`** — documented `VERILOG_INCLUDE_DIRS` (used by Faraday DMA's `\`include "DMA_DEFINE.vh"`) and the SystemVerilog-mode keyword-collision pattern.
4. **`references/failure-patterns.md`** — extended the "RTL Reserved Keywords as Identifiers" section with the wire-declaration variant and the Faraday DMA example. Added a new section "KLayout DRC Stuck on `or` (FreePDK45.lydrc, nangate45)" documenting the rule-121 hang that affected `faraday_dma` and 6 prior batch designs. Pointed users at `validate_config.py` first.

## Operational findings

- **6 stale klayout DRC zombies** (PIDs 1729210, 2078347, 2429182, 2771885, 3116630, 3471411) had been running 3-4 days at 100% CPU on the same `lydrc` rule line, dating back to the 2026-04-21/22 Pass 4 batch. Their parent `bare-timeout` invocations died but the klayout children survived because the wrapper used pre-`setsid` semantics. ORFS+RCX for those designs had already passed per `docs/batch_pass4_report.md` — only the trailing DRC processes never terminated. Killed in this session, freeing ~6 cores and ~12 GB RAM.
- **`/opt/openroad_tools_env.sh` is not present in this environment.** ORFS at `/proj/workarea/user5/OpenROAD-flow-scripts/`. The skill's `_env.sh` correctly auto-detects via `ORFS_ROOT` candidates list — no user-side change needed for this case.
- **Nangate45 LVS rules are absent from this ORFS install.** Only `sky130hd/lvs/sky130hd.lylvs` exists. The skill's `run_lvs.sh` correctly falls back to `lvs/lvs_result.json` `{status: skipped}`. The prior project at `/opt/EDA4AI/...` had a custom `FreePDK45.lylvs`; this install does not.

## Faraday DMA — fixup detail

`dma_ctlrf.v` line 536: `wire [`DMA_MAX_CHNO-1:0] int;` — `int` is an SV reserved keyword. ORFS reads with `read_verilog -defer -sv`. Renamed to `int_w` (4 occurrences, all internal to one file). No port impact, no other files reference it. Documented in `design_cases/faraday_dma/reports/rtl-notes.md`.

## Knowledge ingest

After signoff completes, the run is ingested into `skills/r2g-rtl2gds/knowledge/runs.sqlite`. The empty SQLite at the start of this session is recreated on the first ingest.
