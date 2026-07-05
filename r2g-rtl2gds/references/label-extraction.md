# Label Extraction (dataset building)

`scripts/flow/run_labels.sh <project-dir> [platform]` runs after a completed ORFS
backend and emits per-cell/per-net **regression-target** tables plus a per-design
statistics JSON. It is fail-soft: each of the four label sets is independent, and a
missing input or tool error records a per-label status without aborting the others.

## Outputs

Written to `design_cases/<design>/labels/` and `design_cases/<design>/reports/`:

| File | Rows | Columns | Label transform |
|------|------|---------|-----------------|
| `labels/congestion.csv` | per placed instance | `Design,Cell,cell_type,cell_congestion,label` | `label = sqrt(cell_congestion)` |
| `labels/wirelength.csv` | per net | `Design,Net,NetType,WireLength_um,label,mask_wl` | `label = log1p(WireLength_um)`; `mask_wl = NetType==SIGNAL` |
| `labels/timing.csv` | per placed instance | `Design,Cell,Cell_Slack_ns,Path_Delay_ns,label,in_sta_path` | `label = log(1+Path_Delay_ns)`; `Path_Delay_ns = clk_period - worst_slack` (floored at 0) |
| `labels/irdrop.csv` | per instance (fillers/tap/endcap filtered) | `Design,Cell,X,Y,Voltage_V,IR_Drop_mV,P95_mV,label,has_irdrop` | `label = log(1 + IR_Drop_mV/P95_mV)` |
| `reports/labels_stats.json` | — | per-label count + min/mean/p50/p90/p95/p99/max for `label` and the raw metric, plus mask/in_path/has_irdrop tallies | — |

`Design` + `Cell`/`Net` are the join keys across the four tables. Note that `timing`
and `congestion` are keyed on the full instance set while `irdrop` excludes
fillers/tapcells/endcaps (PDNSim instance filtering) — different label granularities
by design.

## Inputs & resolution

- **Design geometry:** the collected `backend/RUN_*/{final,results}/6_final.odb`
  (timing, IR drop) and `6_final.def` (congestion, wirelength). Falls back to the
  live ORFS results dir.
- **Platform liberty/lef/voltage:** `resolve_platform_paths.sh` (a thin shim over
  `scripts/extract/techlib/resolve.py`) asks the ORFS Makefile to expand `LIB_FILES`,
  `TECH_LEF`, `SC_LEF`, `ADDITIONAL_LIBS`, `PWR_NETS_VOLTAGES` for the design's
  `config.mk` (so asap7/gf180 corner-built variables resolve), with a platform-dir
  glob + per-platform voltage map as fallback. The per-platform voltage constants live
  in `techlib.profile` (`TechProfile.supply_voltage_str`); the congestion worker also
  consults `techlib.lef.routing_layer_info` for tech-LEF pitch/direction (with the
  nangate45 `DEFAULT_LAYER_INFO` as the fallback). Validated on all six ORFS platforms
  (nangate45, sky130hd/hs, asap7, gf180, ihp-sg13g2). `.lib.gz` liberty (asap7/gf180)
  is read directly by OpenROAD.
- **Clock period / port:** parsed from `constraints/constraint.sdc`
  (`set clk_period`, `set clk_port_name`); defaults to 10.0 / clock-name auto-detect.
  A wrong clock period biases `Path_Delay_ns` — keep the SDC accurate.

## Why timing & IR drop read liberty

Both the timing STA and PDNSim IR-drop analysis need cell timing/power models.
The OpenROAD scripts `read_db <odb>` then `read_liberty` over the resolved list.
Without liberty, PDNSim reports zero current (all `Voltage_V == supply`,
`has_irdrop=false`) — so liberty loading is mandatory, not optional. PDNSim also
requires the rail voltages (`set_pdnsim_net_voltage` for the power net = supply and
the ground net = 0), else it raises `PSM-0079`.

## Env knobs (override resolution)

| Var | Effect |
|-----|--------|
| `R2G_LIB_FILES` | space-separated liberty paths for timing/IR drop (overrides resolver) |
| `TECH_LEF` | tech LEF for congestion layer pitches |
| `SUPPLY_VOLTAGE` | nominal VDD for the IR-drop delta + PDNSim rail voltage |
| `CLOCK_PERIOD` / `CLOCK_PORT` | timing clock (overrides SDC; empty `CLOCK_PORT` = auto-detect) |
| `ODB_FILE` / `DEF_FILE` | explicit input design |
| `LABEL_TIMEOUT` | per-label timeout seconds (default 2400) |

## Batch backfill

`tools/run_labels_batch.sh [N] [design ...]` runs `run_labels.sh` across many
completed designs with a concurrency cap (`N`, default 4 — OpenROAD STA/PDNSim are
memory-light vs. KLayout LVS). With no design args it auto-discovers designs that
have a collected `6_final.odb`. Per-design logs and a `labels_backfill.jsonl`
roll-up land under `design_cases/_batch/logs_labels_<tag>/`.

## Scope notes

- Per-design only — corpus-wide aggregation, knowledge-store ingest, and dashboard
  surfacing are intentionally not wired here.
- Typical/primary corner only (no multi-corner labels). The corner follows the ORFS
  platform default (`CORNER`), e.g. BC for asap7/gf180.
- Designs that never reached `6_final` are skipped (status recorded), not errored.
- **Timing labels need a detectable clock.** The clock is re-created after
  `read_db` from the SDC `clk_port_name`, falling back to a `clk`/`clock` port-name
  match. Designs whose top-level clock port has a non-conventional name (and whose
  SDC `clk_port_name` doesn't match an actual port) get all-`not_in_path` timing
  rows (`label=0`) — honestly recorded, not an error. Purely combinational designs
  also correctly produce zero in-path rows.

## 2026-07-05 corrections (RTL2Graph integration audit)

Two label defects were fixed on this date; CSVs generated before it are wrong in
these spots (regenerate before training on them):

- `timing_features.csv`: EVERY register (bus-named cell) had `slack=INF,
  in_sta_path=false` — the STA-pin-name -> odb-component join missed on DEF
  name escaping. After the fix registers carry real slack (aes_core sky130hd:
  5/2476 -> 2476/2476 labeled).
- `wirelength.csv` + `cell_congestion.csv` on sky130*: DEF `RECT` patch groups
  were misread as route points, inflating RECT-bearing nets ~100-400x (1283/30k
  nets on aes_core) and congestion utilization past 11x. Fixed lengths are
  centerline (patch metal excluded), so RECT nets read ~0.2 um below OpenROAD's
  `report_wire_length`, which includes patches.

Full defect table: failure-patterns.md "Dataset-Extraction Silent-Value Defects".
