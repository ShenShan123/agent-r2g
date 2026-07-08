"""run_labels.sh must honor the namespaced R2G_DEF / R2G_ODB override, exactly as
run_features.sh already does.

Regression guard for the 2026-07-08 "R2G_DEF honored by features but not labels"
bug (failure-patterns.md "Dataset-Extraction Silent-Value Defects"). Before the
fix, run_labels.sh discovered the DEF/ODB ONLY from $PROJECT_DIR/backend or the
live ORFS results/ dir and ignored R2G_DEF entirely. Consequences:

  * A verification / override build with no backend SKIPPED the whole labels stage
    ("no backend artifacts") even though R2G_DEF named a perfectly good DEF.
  * Worse (the silent-value trap): when R2G_DEF WAS set and a backend also existed,
    features read the override DEF while labels read the backend DEF -- so X and Y
    keyed off DIFFERENT DEFs and the graph_id+inst_name / net_name join that the
    entire dataset rests on silently misaligned, with no error.

These tests drive the real run_labels.sh orchestrator. OpenROAD is stubbed to
/usr/bin/true so the ODB/DEF-timing + IR workers are instant no-ops: the bug is in
the DEF/ODB *discovery gate*, upstream of any worker, so worker correctness is out
of scope here (that is covered by test_corner_case_pipeline).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures"))
import corner_synth as cs  # noqa: E402

_FLOW = Path(__file__).resolve().parents[1] / "scripts" / "flow"
RUN_LABELS = _FLOW / "run_labels.sh"
TRUE = "/usr/bin/true" if os.path.exists("/usr/bin/true") else "/bin/true"


def _project(tmp_path):
    """A project dir with a config.mk but deliberately NO backend/ dir."""
    proj = tmp_path / "cornerdesign_override_verify"
    (proj / "constraints").mkdir(parents=True)
    # DESIGN_NAME is one that does NOT exist under the ORFS results/ tree, so the
    # live-results fallback finds nothing and the only DEF source is R2G_DEF.
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = cornerdesign_absent\nexport PLATFORM = nangate45\n")
    return proj


def _clean_env():
    env = dict(os.environ)
    for v in ("R2G_DEF", "R2G_ODB", "R2G_SPEF", "R2G_CONFIG"):
        env.pop(v, None)
    # Stub OpenROAD: the timing/IR workers become instant no-ops. Bound every
    # worker so a hung/mismatched tool can never stall the test.
    env["OPENROAD_EXE"] = TRUE
    env["LABEL_TIMEOUT"] = "20"
    return env


def _run_labels(proj, env):
    r = subprocess.run(
        ["bash", str(RUN_LABELS), str(proj), "nangate45"],
        env=env, capture_output=True, text=True, timeout=300)
    stats = json.loads((proj / "reports" / "labels_stats.json").read_text())
    return r, stats


def test_labels_skip_without_override_and_no_backend(tmp_path):
    """CONTROL: no R2G_DEF + no backend -> the honest 'no backend artifacts' skip.

    Pins the pre-existing fail-soft behavior so the fix does not accidentally make
    a genuinely input-less run look productive."""
    cs._write_platform(str(tmp_path))
    proj = _project(tmp_path)
    _r, stats = _run_labels(proj, _clean_env())
    assert stats["status"] == "skipped", stats
    assert stats["reason"] == "no backend artifacts", stats


def test_labels_honor_r2g_def_override_without_backend(tmp_path):
    """FIX: R2G_DEF set (+ no backend) -> the override is honored, so the labels
    stage does NOT skip for a missing backend. This is the same-DEF data-contract
    guarantee run_features.sh already provides; run_labels.sh must match it."""
    paths = cs._write_platform(str(tmp_path))
    proj = _project(tmp_path)
    env = _clean_env()
    env["R2G_DEF"] = paths["corner.def"]
    r, stats = _run_labels(proj, env)
    assert not (stats.get("status") == "skipped"
                and stats.get("reason") == "no backend artifacts"), (
        "run_labels.sh ignored R2G_DEF and skipped for a missing backend -- "
        f"the DEF-override asymmetry regressed.\nstats={stats}\nSTDERR:\n{r.stderr}")
    # The override code path must have actually executed (the NOTE marker).
    assert "overridden" in r.stderr, (
        f"expected the R2G_DEF/R2G_ODB override NOTE on stderr:\n{r.stderr}")
