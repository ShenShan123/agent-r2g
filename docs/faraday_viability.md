# Faraday DSP / RISC viability assessment

**Date:** 2026-04-26
**Question:** Can the Faraday DSP and RISC designs be flowed through `r2g-rtl2gds` using fakeram45 macros as substitutes for the UMC SRAM stubs?

**Verdict:** No — not without a fakeram **tiler** that doesn't exist yet in the skill. Both designs need SRAM cuts that exceed fakeram45's depth/width range, and Faraday RISC additionally has multi-clock SDC requirements.

## Faraday DSP SRAM sizes

| Stub module | Addr width | Data width | Rows × Bits | Total bits | Closest fakeram45 |
|-------------|-----------|------------|-------------|-----------|-------------------|
| CM4k | 14 | 24 | 16,384 × 24 | 393,216 | none — needs 8× `2048x32` |
| CM8k | 14 | 24 | 16,384 × 24 | 393,216 | none — needs 8× `2048x32` |
| DM8k | 14 | 16 | 16,384 × 16 | 262,144 | none — needs 8× `2048x16` |
| ECM32kx24 | 15 | 24 | 32,768 × 24 | 786,432 | none — needs 16× `2048x32` |
| EDM8k | 13 | 16 | 8,192 × 16 | 131,072 | none — needs 4× `2048x16` |
| EEPROM | 18 | 8 | 262,144 × 8 | 2,097,152 | hopelessly large — 128× `2048x8` |
| EIO2k | 11 | 16 | 2,048 × 16 | 32,768 | `2048x39` — fits exactly in depth |
| EM4K | 12 | 16 | 4,096 × 16 | 65,536 | none — needs 2× `2048x16` |
| EM8K | 13 | 16 | 8,192 × 16 | 131,072 | needs 4× `2048x16` |
| PM4k | 14 | 16 | 16,384 × 16 | 262,144 | needs 8× `2048x16` |

**Available fakeram45 sizes (depth × width):** 32×{32,64}, 64×{7,15,21,32,62,64,96,124}, 128×{32,116,256}, 256×{16,32,34,48,95,96}, 512×64, 1024×32, **2048×39 ← max depth**.

The Faraday DSP family expects depths up to 32K (and EEPROM at 256K), far beyond fakeram45's 2K. Functionally there are two paths:

1. **Tile multiple fakeram45 macros per logical SRAM.** A 32K×24 SRAM becomes 16 instances of `2048x32` with an external address-decoder + read-mux. Doable mechanically but requires generating wrapper RTL plus matching `MACRO_PLACEMENT_TCL` for each new shape. None of this tooling exists in the skill today.
2. **Inline behavioral flop-array.** Same approach used for BOOM. ECM32kx24 alone is 786K bits ≈ 786K flops. Yosys `SYNTH_MEMORY_MAX_BITS` would need to be raised to several million, and then place would have ~1M extra cells just for that one memory. Not realistic on nangate45 in any reasonable timeframe.

## Behavioral inference scaling

For reference, what behavioral inference looks like at scale:

- BOOM SmallSEBoom: 168K bits total across 17 inferred memories, each ≤32K bits — **fits** under `SYNTH_MEMORY_MAX_BITS=65536`.
- Faraday DSP single ECM32kx24: 786K bits in one memory — does not fit at any reasonable threshold without crashing place.

## Faraday RISC additional blockers

`RISC.cons` constraint shows two clocks (`SYSCLK`, `BUSCLK` both 6 ns) and `set_false_path` constraints to ten different SRAM `WEN`/`SRAM/A*` pin classes. Multi-clock CDC handling is out of MVP scope per `SKILL.md`:

> "Prefer single-clock MVP flows. Macro designs (fakeram45) are supported with proper config (see "Macro / Hard Memory Designs"). Escalate to the user before attempting CDC, multi-clock, or DFT."

## Recommendation

- **Faraday DSP**: skip. Add a future skill enhancement: `tools/gen_fakeram_tile.py` that tiles multiple `fakeram45_2048x{16,32,39}` macros to cover any depth × width and emits both wrapper RTL and `MACRO_PLACEMENT_TCL` rules. After that exists, Faraday DSP becomes tractable.
- **Faraday RISC**: skip. Even with a tiler, the multi-clock constraints push it past MVP scope. Treat as user-escalation.
- **BOOM 12 variants**: in progress. SmallSEBoom is synthesizing now using behavioral SRAM stubs (`tools/gen_openram_behavioral_stubs.py`). The other 11 BOOM variants share the same 17-stub structure — once SmallSEBoom passes, they're a parameter sweep, not new tooling.
- **Gaisler leon2**: hard skip. VHDL only.
