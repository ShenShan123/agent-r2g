"""diagnose_signoff_fix ranks strategies by learned recipes; safety unchanged."""
from __future__ import annotations
import diagnose_signoff_fix as dsf


def test_ranking_reorders_non_nangate_antenna_strategies():
    # sky130hd antenna -> catalog order is [diode_iters, density_relief].
    cfg = {"PLATFORM": "sky130hd", "CORE_UTILIZATION": "40"}
    drc = {"status": "fail", "total_violations": 10,
           "categories": {"M1_ANTENNA": {"count": 10}}}
    # Learned: density_relief is the proven winner here.
    recipes = {"strategies": {
        "antenna_density_relief": {"attempts": 8, "successes": 7, "failures": 1},
        "antenna_diode_iters":    {"attempts": 8, "successes": 1, "failures": 7},
    }, "n_sessions": 8}
    plan = dsf.build_plan(drc, {}, cfg, check="drc", recipes=recipes)
    ids = [s["id"] for s in plan["strategies"]]
    assert ids[0] == "antenna_density_relief"     # learned winner promoted
    assert "ranking" in plan and plan["ranking"][0]["strategy"] == "antenna_density_relief"


def test_cold_start_preserves_catalog_order():
    cfg = {"PLATFORM": "sky130hd", "CORE_UTILIZATION": "40"}
    drc = {"status": "fail", "total_violations": 10,
           "categories": {"M1_ANTENNA": {"count": 10}}}
    plan = dsf.build_plan(drc, {}, cfg, check="drc", recipes=None)
    ids = [s["id"] for s in plan["strategies"]]
    assert ids == ["antenna_diode_iters", "antenna_density_relief"]


def test_safety_density_addon_never_an_edit():
    # No strategy may ever edit PLACE_DENSITY_LB_ADDON (hard rule).
    cfg = {"PLATFORM": "sky130hd", "CORE_UTILIZATION": "40"}
    drc = {"status": "fail", "categories": {"M1_ANTENNA": {"count": 10}}}
    plan = dsf.build_plan(drc, {}, cfg, check="drc", recipes=None)
    for s in plan["strategies"]:
        assert "PLACE_DENSITY_LB_ADDON" not in s["config_edits"]
