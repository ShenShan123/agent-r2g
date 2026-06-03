# LVS Failure Causes — Corpus Analysis (2026-06-03)

What causes LVS failures in the r2g-rtl2gds corpus, grounded in the knowledge store
(`r2g-rtl2gds/knowledge/runs.sqlite`) and the per-design `reports/lvs.json` produced by
`scripts/extract/extract_lvs.py`. **Headline: 604 / 690 designs with LVS data are clean
(87.5%); the non-clean remainder is overwhelmingly KLayout tooling limits, not real layout
defects.**

## LVS status distribution

| Status | Count | Meaning |
|--------|-------|---------|
| `clean` | 604 | Netlists match |
| `incomplete` | 43 | Extraction started, **no verdict** under the runtime cap |
| `skipped` | 17 | Platform ships **no `.lylvs` rule deck** (e.g. asap7) |
| `fail` | 9 | Comparer ran to a verdict: "Netlists don't match" |
| `crash` | 7 | KLayout C++ **SIGSEGV** mid-run |
| `clean_algorithmic` | 7 | Comparer graph-isomorphism **false-fail** (layout actually clean) |
| `unknown` | 3 | Status undeterminable from log/lvsdb |

## The 9 genuine `fail` verdicts, sub-classified

`extract_lvs.py::classify_lvs_mismatch` reads the `6_lvs.lvsdb` and counts three signals —
genuine **net mismatches**, same-circuit **instance swaps**, and **ambiguous-group**
warnings — then labels the failure conservatively (a benign label is only applied when there
are **zero** genuine net deltas; any "is not matching any net" string forces
`real_connectivity`):

| Design | cells | Class | net_mm / swaps / ambig | count |
|--------|-------|-------|------------------------|-------|
| verilog_ethernet_axis_baser_rx_64 | 3,568 | **symmetric_matcher** | 0 / 2 / 20 | 2 |
| iccad2017_unit5_G | 14,406 | **symmetric_matcher** | 0 / 2 / 34 | 2 |
| blake2s_core | 21,854 | **symmetric_matcher** | 0 / 4 / 48 | 4 |
| wb2axip_axi2axilite | 3,752 | **real_connectivity** | 1 / 1 / 16 | 3 |
| aes_core | 30,496 | generic | 16 / 4 / 44 | 36 |
| vlsi_axi_slave | 2,257 | generic | 80 / 7 / 20 | 185 |
| iccad2017_unit5_F | 16,429 | generic | 128 / 38 / 49 | 292 |
| iccad2015_unit08_in1 | 62,050 | (no lvsdb) | — | mismatch verdict, ran 2.9 h, no db written |
| biriscv_core | 68,008 | (no lvsdb) | — | mismatch verdict, ran 2.2 h, no db written |

## Root causes, ranked

1. **KLayout-0.30.7 symmetric-matcher limitation (3 of 9 `fail`, plus all 7
   `clean_algorithmic`).** Signature: *zero* genuine net deltas — only interchangeable-instance
   swaps inside "ambiguous groups." This is symmetric/parallel logic (NAND/NOR trees, register
   files, replicated datapaths) the comparer cannot disambiguate. **The layout is correct; the
   matcher just cannot prove it.** Empirically confirmed earlier (commit `11cebfb` campaign):
   raising the comparer budget (`LVS_MAX_DEPTH` / `LVS_MAX_BRANCH_COMPLEXITY`) removes the
   "Maximum depth exhausted" warning but does **not** resolve the mismatches.

2. **Genuine netlist disagreements ("generic", 3–5 designs).** Real net/pin deltas (16–292).
   These need per-design triage — could be true structural mismatches or CDL/rule-deck
   artifacts. The two no-lvsdb cases (biriscv_core, iccad2015_unit08_in1) belong here: they
   reached a "Netlists don't match" verdict after 2–3 h but never wrote a database to classify.

3. **One real-connectivity defect candidate** (wb2axip_axi2axilite): one net "is not matching
   any net" — flagged as a potential true defect, not benign.

## The bulk of non-clean is tooling, not layout

The 9 `fail` are dwarfed by **43 `incomplete` + 7 `crash`**:

- **`incomplete` (43)** are large designs (≈230K–245K cells; e.g. `ip_complete_64` 244K,
  `axis_fifo` 242K) whose deep-mode netlist comparison does not finish under the runtime cap.
  A **performance limit, not a mismatch** (mirrors the DRC FEOL-hang story).
- **`crash` (7)** are KLayout C++ SIGSEGVs (`sort_circuit` / `gen_log_entry` / `ruby_run_node`)
  — a **KLayout-0.30.7 bug**, fixed by upgrading to ≥ 0.30.10.
- **`skipped` (17)** are platforms with no bundled LVS rule (e.g. asap7) — a **capability
  gap**, not a failure.

## Why most LVS failures are NOT back-end-flow-fixable

Unlike DRC antenna violations — where the back-end router can insert real diodes — **LVS
mismatches are not artifacts of placement/routing.** The netlist topology comes from
synthesis/RTL, so re-routing cannot change it:

| Cause | Remedy | Back-end fixable? |
|-------|--------|-------------------|
| symmetric-matcher | newer KLayout, or manual `same_nets`/`same_circuits` seeding | No |
| crash | KLayout upgrade (≥0.30.10) | No |
| incomplete | longer cap / faster host / hierarchical LVS | No (resource) |
| generic | per-design netlist triage (some may trace to CDL generation) | Rarely |
| skipped | bundle/author a `.lylvs` for the platform | N/A (capability) |

So `diagnose_signoff_fix.py` reports these as **honest, specifically-labeled residuals**
(`lvs_symmetric_matcher_residual`, `lvs_real_connectivity_mismatch`,
`klayout_cpp_crash_needs_upgrade`, …) rather than spawning doomed re-runs.

## Cross-check: the DRC antenna fix does not create LVS failures

The 2026-06-02 antenna-DRC work inserts physical `ANTENNA_X1` diodes. These do **not** appear
in the schematic CDL, but the bundled `FreePDK45.lylvs` flattens the physical-only cell
(`Flatten layout cell (no schematic): ANTENNA_X1`), so they are not counted as
schematic-missing devices. Verified: `stream_register` stays **LVS CLEAN** with a diode
inserted. Always re-run LVS after a DRC antenna fix to refresh the report.

## How to reproduce this analysis

```bash
DB=r2g-rtl2gds/knowledge/runs.sqlite
# status distribution
sqlite3 "$DB" "SELECT lvs_status, COUNT(*) FROM runs WHERE lvs_status!='' GROUP BY lvs_status;"
# per-fail classification is in each design's reports/lvs.json:
#   mismatch_class, net_mismatches, circuit_swaps, ambiguous_groups
```

See also `references/signoff-fixing.md` "LVS" + "Residual taxonomy", and
`references/failure-patterns.md` "LVS symmetric-matcher residual".
