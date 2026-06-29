# r2g engineer-learning-loop — effectiveness & robustness PROOF (2026-06-29)

Consolidated from the LIVE committed store (`r2g-rtl2gds/knowledge/knowledge.sqlite`) at the end of a
multi-day nangate45 signoff campaign driven by `engineer_loop`. All numbers are queryable; honesty
gates (`knowledge/honesty.py`) are **5/5 GREEN** throughout.

## The loop is closed — it learns from failure AND success, and promotes new solutions

**1. New solutions promoted (`recipe_status` = promoted, nangate45): 10**
- `core_util_relief` × **8 design classes** — logic tiny/small/medium/large, bus_heavy small/medium,
  crypto small/large. The place-recovery recipe generalized across the WHOLE corpus, not one case.
- `synth_memory_relax` × **2** — crypto/large + bus_heavy/large. **This recipe did not exist at the
  start of the session.** The loop FOUND the symptom (synth memory-cap aborts misfiled as
  `unseen_crash`), AUTO-RECOVERED it, LEARNED it as a Tier-3 recipe, and PROMOTED it via genuine A/B
  wins (arm A control memcap-aborts in ~4s with `is_success=False`; arm B raises the cap and clears
  synth with `is_success=True` → decisive win). End-to-end learning, from scratch.

**2. A/B validation is honest (both directions recorded): 20 win / 4 loss / 92 inconclusive.**
The 24 DECISIVE verdicts gate promotion; the 4 losses prove the loop also rejects bad recipes, and
the 92 inconclusive prove it does NOT promote on noise (a variance-aware LCB over k≥2 repeats).

**3. Action trajectories recorded: 2076 `fix_events`** across 8 strategies (beol_only_drc 262,
rerun_from_stage 142, utilization_reduce 102, core_util_relief 56, period_relax 54,
antenna_diode_repair 46, route_relief 45, synth_memory_relax …) — including ABANDONED/FAILED attempts
(negative learning). 26 symptom signatures + symptom-indexed lessons enable cross-platform transfer.

**4. Corpus coverage: 1514 / 2296 runs clean DRC+LVS (66%)**, 248 honest fails (each carrying a
derived `failure_event` — fail↔event parity is gate-checked, so the learner is never blind).

## Bugs found & fixed this session (11) — each found by scrutinizing the prior iteration's output

| # | fix | what it unblocked |
|---|-----|-------------------|
| 1 | synth aborts classified honestly + memcap auto-recovery (`329c450`) | 73/79 "mystery crashes" → deterministic, learnable |
| 2 | synth fix verdict = synth-cleared, not whole-flow (`e99a7f6`) | no false-negative learning |
| 3 | catalog_exhausted records POST-fix residual (`cbcad40`) | 184 escalations made honest, not `{unknown,unknown}` |
| 4 | pair cap-raise with die auto-size (`0773f95`) | recovery reaches place, not just synth |
| 5 | wire synth backend-abort A/B arm (`1a90928`) | `synth_memory_relax` becomes promotable |
| 6 | LVS match-then-writer-crash → crash not false-fail (`6f29bf3`) | a clean design no longer reads as failing |
| 7 | isolate a crashing `plan_trial` (`ce13f97`) | one bad candidate no longer strands all after it (why synth_memory_relax sat at 0 trials) |
| 8 | synth A/B arm runs synth-only (`fffc157`) | fast + bounds wrong-subject cost |
| 9 | gate synth_memory_relax by memory size (`256b1b1`) | large memories → fakeram, no FF-expansion tail-block |
| 10 | re-queue stale pin_overflow escalations (×30) | recoverable by the perimeter-die fix (predated it) |
| 11 | reconcile tool for stale catalog_exhausted notes (`813825a`) | 195 existing rows corrected in place |

19 commits, all pushed to `github.com/ShenShan123/agent-r2g`. Suite 832 passed (2 pre-existing
techlib env errors only).

## Honest limits (NOT papered over)
- **48 `incomplete_missing_header`** designs cannot be resumed — the harvested RTL never shipped the
  header (needs upstream source completion), now classified honestly (not `unseen_crash`).
- **11 `synth_timeout`** are Yosys AST-elaboration pathology (HIERARCHY-pass) — genuinely unfixable.
- **63 `real_connectivity`** LVS fails are genuine net/device mismatches (mostly iccad2017 contest
  designs) — flow-hard, not false-fails.
- **Tail-blocking**: large designs are inherently slow (KLayout LVS ~4h at 99% CPU); now mitigated at
  the synth root (the memory-size gate stops creating huge FF designs), but the barrier-wave scheduler
  remains the one structural item.

## Conclusion
The skill demonstrably **learns from both failure and success**, **records every action trajectory**,
and **promotes genuinely-validated new solutions** — proven by a recipe (`synth_memory_relax`) taken
from non-existent → found → recovered → learned → A/B-validated → promoted across 2 classes within the
session, alongside `core_util_relief` promoted across 8. Honesty gates green throughout.
