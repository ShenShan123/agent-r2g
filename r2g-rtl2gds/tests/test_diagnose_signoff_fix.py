"""Tests for diagnose_signoff_fix.py: signoff (DRC/LVS) violation→fix-plan logic."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import diagnose_signoff_fix as d

MOD = Path(__file__).resolve().parents[1] / "scripts" / "reports" / "diagnose_signoff_fix.py"


def _drc(status, count=0, cats=None):
    return {"status": status, "total_violations": count, "categories": cats or {}}


def _antenna_cats(n=7, layer="METAL7_ANTENNA"):
    return {layer: {"count": n, "description": ""}}


def test_clean_drc_yields_no_strategies():
    plan = d.build_plan(_drc("clean"), {}, {}, check="drc")
    assert plan["status"] == "clean"
    assert plan["strategies"] == []


def test_antenna_fail_yields_two_ordered_strategies_sky130hd():
    """Non-inert platform (sky130hd) gets both antenna strategies."""
    cfg = {"CORE_UTILIZATION": "10", "PLATFORM": "sky130hd"}
    plan = d.build_plan(_drc("fail", 7, _antenna_cats()), {}, cfg, check="drc")
    ids = [s["id"] for s in plan["strategies"]]
    assert ids == ["antenna_diode_iters", "antenna_density_relief"]
    assert plan["dominant_category"] == "METAL7_ANTENNA"
    # density relief computes a concrete lowered utilization
    relief = plan["strategies"][1]["config_edits"]
    assert relief["CORE_UTILIZATION"] == "5"


def test_antenna_fail_nangate45_is_immediate_residual():
    """nangate45: both diode-iters and density-relief are suppressed (inert + counterproductive)
    → immediate residual with a nangate45-specific reason, no strategies offered."""
    cfg = {"CORE_UTILIZATION": "10", "PLATFORM": "nangate45"}
    plan = d.build_plan(_drc("fail", 7, _antenna_cats()), {}, cfg, check="drc")
    assert plan["status"] == "residual"
    assert plan["strategies"] == []
    assert "nangate45 antenna repair inert" in plan["residual_reason"]
    assert plan["dominant_category"] == "METAL7_ANTENNA"


def test_applied_strategy_is_filtered_out():
    cfg = {"MAX_REPAIR_ANTENNAS_ITER_GRT": "10",
           "MAX_REPAIR_ANTENNAS_ITER_DRT": "10", "CORE_UTILIZATION": "10",
           "PLATFORM": "sky130hd"}
    plan = d.build_plan(_drc("fail", 7, _antenna_cats()), {}, cfg, check="drc")
    ids = [s["id"] for s in plan["strategies"]]
    assert "antenna_diode_iters" not in ids
    assert ids[0] == "antenna_density_relief"


def test_exhausted_antenna_is_residual():
    cfg = {"MAX_REPAIR_ANTENNAS_ITER_GRT": "10",
           "MAX_REPAIR_ANTENNAS_ITER_DRT": "10", "CORE_UTILIZATION": "5",
           "PLATFORM": "sky130hd"}
    plan = d.build_plan(_drc("fail", 7, _antenna_cats()), {}, cfg, check="drc")
    assert plan["status"] == "residual"
    assert plan["strategies"] == []


def test_non_antenna_drc_is_unhandled_residual():
    plan = d.build_plan(_drc("fail", 3, {"M2.SP.1": {"count": 3}}), {}, {}, check="drc")
    assert "non-antenna" in plan["residual_reason"]
    assert plan["strategies"] == []


def test_stuck_drc_is_out_of_scope():
    plan = d.build_plan(_drc("stuck"), {}, {}, check="drc")
    assert plan["strategies"] == []
    assert "out_of_v1_scope" in plan["residual_reason"]


def test_lvs_unknown_yields_resolve_strategy():
    plan = d.build_plan({}, {"status": "unknown", "mismatch_count": None}, {}, check="lvs")
    assert [s["id"] for s in plan["strategies"]] == ["lvs_resolve_unknown"]


def test_lvs_cpp_crash_is_residual():
    lvs = {"status": "fail", "log_info": {"errors": ["...sort_circuit::gen_log_entry SIGSEGV"]}}
    plan = d.build_plan({}, lvs, {}, check="lvs")
    assert plan["strategies"] == []
    assert "klayout_cpp_crash" in plan["residual_reason"]


def test_lvs_macro_emits_operator_only_strategy():
    lvs = {"status": "fail", "log_info": {"errors": ["Netlists don't match"]}}
    cfg = {"VERILOG_FILES": "/x/fakeram45_64x32.v /x/top.v"}
    plan = d.build_plan({}, lvs, cfg, check="lvs")
    s = plan["strategies"][0]
    assert s["id"] == "lvs_macro_cdl"
    assert s["auto_apply"] is False
    assert "operator_note" in s


# --- FIX #1: LVS crash / incomplete → residual ---

def test_lvs_status_crash_yields_residual():
    """status='crash' (from extract_lvs) → residual, klayout_cpp_crash reason, no strategies."""
    plan = d.build_plan({}, {"status": "crash"}, {}, check="lvs")
    assert plan["status"] == "residual"
    assert plan["strategies"] == []
    assert "klayout_cpp_crash" in plan["residual_reason"]


# --- LVS mismatch_class → precise honest residual (2026-06-02 triage) ---

def test_lvs_symmetric_matcher_residual():
    """mismatch_class=symmetric_matcher → honest residual, no doomed re-run strategy."""
    lvs = {"status": "fail", "log_info": {"errors": ["Netlists don't match"]},
           "mismatch_class": "symmetric_matcher"}
    plan = d.build_plan({}, lvs, {}, check="lvs")
    assert plan["status"] == "residual"
    assert plan["strategies"] == []
    assert "lvs_symmetric_matcher_residual" in plan["residual_reason"]


def test_lvs_real_connectivity_residual():
    """mismatch_class=real_connectivity → flagged as a real defect, not benign."""
    lvs = {"status": "fail", "log_info": {"errors": ["Netlists don't match"]},
           "mismatch_class": "real_connectivity"}
    plan = d.build_plan({}, lvs, {}, check="lvs")
    assert "lvs_real_connectivity_mismatch" in plan["residual_reason"]


def test_lvs_generic_mismatch_still_operator_review():
    """No mismatch_class (e.g. no lvsdb) → unchanged generic operator-review residual."""
    lvs = {"status": "fail", "log_info": {"errors": ["Netlists don't match"]}}
    plan = d.build_plan({}, lvs, {}, check="lvs")
    assert "operator review" in plan["residual_reason"]


def test_lvs_status_incomplete_yields_residual():
    """status='incomplete' (from extract_lvs) → residual, lvs incomplete reason, no strategies."""
    plan = d.build_plan({}, {"status": "incomplete"}, {}, check="lvs")
    assert plan["status"] == "residual"
    assert plan["strategies"] == []
    assert "lvs incomplete" in plan["residual_reason"]
    assert "no verdict" in plan["residual_reason"]


def test_lvs_status_unknown_still_yields_resolve_strategy():
    """Truly unknown status (uninformative log) still emits lvs_resolve_unknown (re-extract)."""
    plan = d.build_plan({}, {"status": "unknown", "mismatch_count": None}, {}, check="lvs")
    assert plan["strategies"] != []
    assert plan["strategies"][0]["id"] == "lvs_resolve_unknown"


# --- FIX #4b: nangate45 antenna immediately residual ---

def test_antenna_fail_nangate45_residual_reason_is_specific():
    """nangate45 antenna fail → residual_reason mentions 'nangate45 antenna repair inert'."""
    cfg = {"CORE_UTILIZATION": "15", "PLATFORM": "nangate45"}
    plan = d.build_plan(_drc("fail", 3, _antenna_cats()), {}, cfg, check="drc")
    assert plan["status"] == "residual"
    assert plan["strategies"] == []
    assert "nangate45 antenna repair inert" in plan["residual_reason"]
    # Must NOT use the generic exhausted message
    assert "all real-fix strategies exhausted" not in plan["residual_reason"]


def test_antenna_fail_sky130hd_still_yields_both_strategies():
    """sky130hd (non-inert platform) still gets both antenna strategies unchanged."""
    cfg = {"CORE_UTILIZATION": "30", "PLATFORM": "sky130hd"}
    plan = d.build_plan(_drc("fail", 5, _antenna_cats()), {}, cfg, check="drc")
    ids = [s["id"] for s in plan["strategies"]]
    assert ids == ["antenna_diode_iters", "antenna_density_relief"]


def test_apply_edits_round_trip():
    cfg = "export DESIGN_NAME = t\nexport CORE_UTILIZATION = 10\n"
    edits = {"MAX_REPAIR_ANTENNAS_ITER_DRT": "10", "MAX_REPAIR_ANTENNAS_ITER_GRT": "10"}
    once = d.apply_edits(cfg, edits)
    twice = d.apply_edits(once, edits)  # re-apply must not duplicate the block
    assert twice.count(d.BLOCK_START) == 1
    assert twice.count(d.BLOCK_END) == 1
    # original non-block lines are preserved (exactly once)
    assert twice.count("export DESIGN_NAME = t") == 1
    assert twice.count("export CORE_UTILIZATION = 10") == 1
    # re-parsing the result yields the edited values
    parsed = d.parse_config(twice)
    assert parsed["MAX_REPAIR_ANTENNAS_ITER_DRT"] == "10"
    assert parsed["MAX_REPAIR_ANTENNAS_ITER_GRT"] == "10"
    assert parsed["DESIGN_NAME"] == "t"


def test_parse_config_handles_assignment_forms():
    text = (
        "# a comment line\n"
        "export A = 1\n"
        "B := 2\n"
        "C ?= 3\n"
        "override export D = 4\n"
        "export A = 9\n"  # later assignment of the same var wins
    )
    cfg = d.parse_config(text)
    assert cfg["A"] == "9"
    assert cfg["B"] == "2"
    assert cfg["C"] == "3"
    assert cfg["D"] == "4"
    # comment line is not parsed as a variable
    assert "#" not in "".join(cfg.keys())


def _mk_project(tmp_path, drc=None, lvs=None, config="export DESIGN_NAME = t\nexport CORE_UTILIZATION = 10\n"):
    p = tmp_path / "proj"
    (p / "reports").mkdir(parents=True)
    (p / "constraints").mkdir(parents=True)
    if drc is not None:
        (p / "reports" / "drc.json").write_text(json.dumps(drc))
    if lvs is not None:
        (p / "reports" / "lvs.json").write_text(json.dumps(lvs))
    (p / "constraints" / "config.mk").write_text(config)
    return p


def test_apply_writes_idempotent_block(tmp_path):
    p = _mk_project(tmp_path, drc={"status": "fail", "total_violations": 7,
                                   "categories": {"METAL7_ANTENNA": {"count": 7}}})
    cfg = p / "constraints" / "config.mk"
    for _ in range(2):  # apply twice → block must not duplicate
        subprocess.run([sys.executable, str(MOD), str(p), "--check", "drc",
                        "--apply", "antenna_diode_iters"], check=True)
    text = cfg.read_text()
    assert text.count("# >>> r2g signoff-fix (auto) >>>") == 1
    assert "export MAX_REPAIR_ANTENNAS_ITER_GRT = 10" in text
    assert text.count("export DESIGN_NAME = t") == 1  # original preserved once


def test_next_prints_first_auto_strategy(tmp_path):
    p = _mk_project(tmp_path, drc={"status": "fail", "total_violations": 7,
                                   "categories": {"METAL7_ANTENNA": {"count": 7}}})
    out = subprocess.run([sys.executable, str(MOD), str(p), "--check", "drc", "--next"],
                         capture_output=True, text=True, check=True).stdout.strip()
    sid, rerun, recheck = out.split("\t")
    assert sid == "antenna_diode_iters" and rerun == "route" and recheck == "drc"


def test_next_prints_stop_when_clean(tmp_path):
    p = _mk_project(tmp_path, drc={"status": "clean", "total_violations": 0, "categories": {}})
    out = subprocess.run([sys.executable, str(MOD), str(p), "--check", "drc", "--next"],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out.startswith("STOP\tclean")


def test_apply_operator_only_strategy_errors(tmp_path):
    p = _mk_project(tmp_path, lvs={"status": "fail", "log_info": {"errors": ["don't match"]}},
                    config="export VERILOG_FILES = /x/fakeram45_64x32.v\n")
    r = subprocess.run([sys.executable, str(MOD), str(p), "--check", "lvs",
                        "--apply", "lvs_macro_cdl"], capture_output=True, text=True)
    assert r.returncode == 3 and "operator-only" in r.stderr


DRIVER = Path(__file__).resolve().parents[1] / "scripts" / "flow" / "fix_signoff.sh"


def _stub_dir(tmp_path, counts):
    """Build stub run_orfs/run_drc + a python extract that pops `counts` into drc.json."""
    sd = tmp_path / "stubs"
    sd.mkdir()
    (sd / "counts.txt").write_text("\n".join(str(c) for c in counts) + "\n")
    (sd / "run_orfs.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    (sd / "run_drc.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    extract = '''#!/usr/bin/env python3
import sys, json, pathlib
proj, out = sys.argv[1], sys.argv[2]
cf = pathlib.Path(__file__).with_name("counts.txt")
lines = cf.read_text().splitlines()
n = lines[0] if lines else "0"
cf.write_text("\\n".join(lines[1:]) + ("\\n" if lines[1:] else ""))
n = int(n or 0)
if n == 0:
    json.dump({"status":"clean","total_violations":0,"categories":{}}, open(out,"w"))
else:
    json.dump({"status":"fail","total_violations":n,"categories":{"METAL7_ANTENNA":{"count":n}}}, open(out,"w"))
'''
    (sd / "extract_drc.py").write_text(extract)
    for f in ("run_orfs.sh", "run_drc.sh", "extract_drc.py"):
        os.chmod(sd / f, 0o755)
    return sd


def _run_driver(proj, sd, max_iters=3):
    env = dict(os.environ,
               R2G_RUN_ORFS=str(sd / "run_orfs.sh"),
               R2G_RUN_DRC=str(sd / "run_drc.sh"),
               R2G_EXTRACT_DRC=str(sd / "extract_drc.py"))
    return subprocess.run(["bash", str(DRIVER), str(proj), "nangate45",
                           "--check", "drc", "--max-iters", str(max_iters)],
                          capture_output=True, text=True, env=env)


def test_driver_stops_when_cleaned(tmp_path):
    # seeded fail=7, first re-check returns 0 → cleaned in 1 applied iter
    p = _mk_project(tmp_path, drc={"status": "fail", "total_violations": 7,
                                   "categories": {"METAL7_ANTENNA": {"count": 7}}})
    sd = _stub_dir(tmp_path, counts=[0])
    r = _run_driver(p, sd)
    assert r.returncode == 0, r.stderr
    log = (p / "reports" / "fix_log.jsonl").read_text().strip().splitlines()
    assert len(log) >= 1
    assert (p / "reports" / "fix_summary.md").exists()
    final = json.loads((p / "reports" / "drc.json").read_text())
    assert final["status"] == "clean"


def test_driver_nangate45_antenna_stops_immediately(tmp_path):
    # nangate45 has NO antenna strategies (both diode-iters and density-relief suppressed).
    # The driver must: --next returns STOP immediately (iter=1) → 1 log row (stop row only).
    p = _mk_project(tmp_path,
                    drc={"status": "fail", "total_violations": 7,
                         "categories": {"METAL7_ANTENNA": {"count": 7}}},
                    config="export DESIGN_NAME = t\nexport CORE_UTILIZATION = 10\nexport PLATFORM = nangate45\n")
    sd = _stub_dir(tmp_path, counts=[7, 7, 7])
    r = _run_driver(p, sd)
    summary = (p / "reports" / "fix_summary.md").read_text()
    lines = (p / "reports" / "fix_log.jsonl").read_text().strip().splitlines()
    # Exactly 1 log row: the stop row (no strategy was ever applied)
    assert len(lines) == 1, f"expected 1 log row, got {len(lines)}: {lines}"
    row0 = json.loads(lines[0])
    assert row0["strategy"] == "none"
    assert "stop" in row0["verdict"]
    # summary table must exist
    assert "| check |" in summary
