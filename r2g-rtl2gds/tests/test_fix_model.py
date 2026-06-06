"""Unit tests for the pure fix-strategy ranking model."""
from __future__ import annotations
import pytest
import fix_model as fxm


STATIC = ["antenna_diode_repair", "antenna_density_relief", "lvs_macro_cdl"]


def test_cold_start_returns_static_order():
    ranked = fxm.rank_strategies(None, STATIC)
    assert [r["strategy"] for r in ranked] == STATIC
    assert all(r["provenance"] == "cold-start" for r in ranked)
    assert all(r["attempts"] == 0 for r in ranked)


def test_proven_winner_outranks_untried_outranks_loser():
    entry = {"strategies": {
        "antenna_density_relief": {"attempts": 11, "successes": 9, "failures": 2},
        "lvs_macro_cdl":          {"attempts": 3,  "successes": 0, "failures": 3},
    }, "n_sessions": 14}
    ranked = fxm.rank_strategies(entry, STATIC)
    order = [r["strategy"] for r in ranked]
    # winner (9/11) first, untried diode_repair (0.5 prior) middle, loser (0/3) last
    assert order[0] == "antenna_density_relief"
    assert order[1] == "antenna_diode_repair"      # untried -> neutral prior 0.5
    assert order[2] == "lvs_macro_cdl"             # proven loser, but still present
    assert ranked[2]["score"] < 0.5 < ranked[0]["score"]


def test_smoothing_tames_single_lucky_win():
    entry = {"strategies": {"antenna_diode_repair": {"attempts": 1, "successes": 1, "failures": 0}},
             "n_sessions": 1}
    ranked = fxm.rank_strategies(entry, STATIC)
    win = next(r for r in ranked if r["strategy"] == "antenna_diode_repair")
    # 1/1 -> (1+1)/(1+2)=0.667, only just above the 0.5 untried prior.
    assert win["score"] == pytest.approx(2/3, abs=1e-6)


def test_evidence_and_provenance_surfaced():
    entry = {"strategies": {"antenna_density_relief": {"attempts": 6, "successes": 5,
             "failures": 1, "median_reduction_pct": 0.97}}, "n_sessions": 6}
    ranked = fxm.rank_strategies(entry, STATIC)
    top = next(r for r in ranked if r["strategy"] == "antenna_density_relief")
    assert top["provenance"].startswith("learned(n=6")
    assert top["successes"] == 5 and top["failures"] == 1
    assert "median_reduction_pct" in top


def test_never_drops_a_static_strategy():
    entry = {"strategies": {"antenna_diode_repair": {"attempts": 2, "successes": 2, "failures": 0}},
             "n_sessions": 2}
    ranked = fxm.rank_strategies(entry, STATIC)
    assert set(r["strategy"] for r in ranked) == set(STATIC)
