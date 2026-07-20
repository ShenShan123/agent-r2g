"""Content-based stage freshness (P0-R8) + report<->run binding (P0-R7).

Both from the 2026-07-19 post-consolidation audit (failure-patterns.md #52), and
both instances of its shared root cause — identity inferred from mutable paths,
mtimes, and file presence instead of carried:

  * **P0-R8** — `run_graphs.sh needs_stage()` compared feature/label mtimes
    against the DEF's. The audit edited the real picorv32_core DEF's DIEAREA,
    restored the original mtime, and watched run_graphs.sh reuse the stale
    feature/label dirs and write an `ok` manifest for a layout they did not
    describe. Only the independent verifier noticed.

  * **P0-R7** — `signoff_gate` read DRC/LVS verdicts from the PROJECT-level
    reports dir while binding only the DEF to the run dir. Two real wbuart32 runs
    with different DEF digests (R1 d6426fae…, R2 cc2da796…) let R1's clean bundle
    certify R2's layout for `pass_with_caveats`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

_FLOW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "scripts", "flow")
sys.path.insert(0, _FLOW)

import signoff_gate as sg  # noqa: E402
import _stage_provenance as sp  # noqa: E402


# --------------------------------------------------------------------------- #
# P0-R8 — a stage is reusable only for the DEF it was extracted from.          #
# --------------------------------------------------------------------------- #

@pytest.fixture()
def staged(tmp_path):
    """A DEF plus a stamped stage marker that currently matches it."""
    d = tmp_path / "6_final.def"
    d.write_text("DESIGN x ;\nDIEAREA ( 0 0 ) ( 100 100 ) ;\nEND DESIGN\n")
    stats = tmp_path / "features_stats.json"
    stats.write_text(json.dumps({"design": "x", "features": {}}))
    sp.stamp(str(stats), sp.build_provenance(str(d), "features"))
    return d, stats


def test_matching_def_is_reusable(staged):
    d, stats = staged
    ok, reason = sp.freshness(str(stats), str(d))
    assert ok, reason


def test_content_change_with_mtime_restored_is_stale(staged):
    """The audit's exact reproduction: DEF bytes change, mtime is put back."""
    d, stats = staged
    st = os.stat(d)
    d.write_text("DESIGN x ;\nDIEAREA ( 0 0 ) ( 999 999 ) ;\nEND DESIGN\n")
    os.utime(d, (st.st_atime, st.st_mtime))          # restore the original mtime
    assert os.stat(d).st_mtime == st.st_mtime        # the mtime check WOULD pass

    ok, reason = sp.freshness(str(stats), str(d))
    assert not ok
    assert "content changed" in reason


def test_marker_without_provenance_is_unverifiable(tmp_path):
    """A pre-P0-R8 marker cannot be verified, so it is stale — fail-closed."""
    d = tmp_path / "6_final.def"
    d.write_text("DESIGN x ;\nEND DESIGN\n")
    stats = tmp_path / "labels_stats.json"
    stats.write_text(json.dumps({"design": "x", "labels": {}}))   # legacy: no provenance

    ok, reason = sp.freshness(str(stats), str(d))
    assert not ok
    assert "no provenance" in reason


def test_stage_schema_bump_invalidates_a_stamped_marker(staged, monkeypatch):
    """An extractor-contract change is staleness an mtime comparison cannot see."""
    d, stats = staged
    monkeypatch.setattr(sp, "STAGE_SCHEMA_VERSION", sp.STAGE_SCHEMA_VERSION + 1)
    ok, reason = sp.freshness(str(stats), str(d))
    assert not ok
    assert "different X/Y contract" in reason


def test_stamp_preserves_the_existing_stats_body(staged):
    """Stamping must MERGE into the completion marker, never replace it — the
    stats gates read the same file."""
    d, stats = staged
    doc = json.loads(stats.read_text())
    assert doc["design"] == "x" and "features" in doc
    assert doc["provenance"]["def_fingerprint"]["sha256"]
    assert doc["provenance"]["stage"] == "features"


def test_cli_check_exit_codes(staged):
    d, stats = staged
    cli = os.path.join(_FLOW, "_stage_provenance.py")
    fresh = subprocess.run([sys.executable, cli, "check", "--stats", str(stats),
                            "--def", str(d)], capture_output=True)
    assert fresh.returncode == 0

    d.write_text("DESIGN x ;\nDIEAREA ( 0 0 ) ( 5 5 ) ;\nEND DESIGN\n")
    stale = subprocess.run([sys.executable, cli, "check", "--stats", str(stats),
                            "--def", str(d)], capture_output=True)
    assert stale.returncode == 1
    assert b"not reusable" in stale.stderr


# --------------------------------------------------------------------------- #
# P0-R7 — clean reports from one run must not certify another run's layout.    #
# --------------------------------------------------------------------------- #

def _project(tmp_path, *, report_run, selected_run, prov_source="restage_marker"):
    """A project with two backend runs whose reports name `report_run`."""
    proj = tmp_path / "proj"
    reports = proj / "reports"
    reports.mkdir(parents=True)
    for r in (report_run, selected_run):
        (proj / "backend" / r / "final").mkdir(parents=True, exist_ok=True)
    prov = {"run_tag": report_run, "run_dir": str(proj / "backend" / report_run),
            "source": prov_source}
    (reports / "drc.json").write_text(json.dumps(
        {"status": "clean", "total_violations": 0, "provenance": prov}))
    (reports / "lvs.json").write_text(json.dumps(
        {"status": "clean", "mismatch_count": 0, "provenance": prov}))
    return proj, str(proj / "backend" / selected_run)


def test_reports_from_a_foreign_run_block_the_gate(tmp_path):
    proj, sel = _project(tmp_path, report_run="RUN_A", selected_run="RUN_Z")
    rb = sg._check_report_binding(str(proj / "reports"), sel)
    assert rb["status"] == "foreign"
    assert "RUN_A" in rb["detail"]

    verdict = sg.evaluate(str(proj), sel,
                          def_path=str(proj / "backend" / "RUN_Z" / "final" / "x.def"))
    assert "report_binding" in verdict["blockers"]
    assert verdict["status"] == "dirty"


def test_reports_from_the_selected_run_bind(tmp_path):
    proj, sel = _project(tmp_path, report_run="RUN_Z", selected_run="RUN_Z")
    rb = sg._check_report_binding(str(proj / "reports"), sel)
    assert rb["status"] == "bound"
    assert rb["reports"]["drc.json"] == "RUN_Z"

    verdict = sg.evaluate(str(proj), sel)
    assert "report_binding" not in verdict["blockers"]


def test_unattributed_legacy_reports_are_a_caveat_not_a_blocker(tmp_path):
    """Every pre-2026-07-20 report is unstamped; re-running DRC/LVS corpus-wide to
    acquire attribution costs hours per design. They must stay passable and
    self-heal on the next signoff run."""
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    for r in ("RUN_A", "RUN_Z"):                    # ambiguity needs >1 run
        (proj / "backend" / r).mkdir(parents=True)
    (proj / "reports" / "drc.json").write_text(json.dumps(
        {"status": "clean", "total_violations": 0}))          # legacy: no provenance

    sel = str(proj / "backend" / "RUN_Z")
    rb = sg._check_report_binding(str(proj / "reports"), sel)
    assert rb["status"] == "unknown"

    verdict = sg.evaluate(str(proj), sel)
    assert "report_binding" not in verdict["blockers"]
    assert "report_binding=unknown" in verdict["caveats"]


def test_single_run_project_needs_no_attribution(tmp_path):
    """With one backend run there is no OTHER run the verdicts could describe, so
    an unattributed report is not ambiguous. Flagging it would put every existing
    clean single-run design into pass_with_caveats and drown the real caveats."""
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "backend" / "RUN_Z").mkdir(parents=True)
    (proj / "reports" / "drc.json").write_text(json.dumps(
        {"status": "clean", "total_violations": 0}))

    sel = str(proj / "backend" / "RUN_Z")
    assert sg._check_report_binding(str(proj / "reports"), sel)["status"] == "bound"
    # (route/timing caveats are unrelated to this fixture's minimal report set)
    verdict = sg.evaluate(str(proj), sel)
    assert not [c for c in verdict["caveats"] if c.startswith("report_binding")]
    assert "report_binding" not in verdict["blockers"]


def test_a_guessed_attribution_is_recorded_as_weak(tmp_path):
    proj, sel = _project(tmp_path, report_run="RUN_Z", selected_run="RUN_Z",
                         prov_source="latest_run")
    verdict = sg.evaluate(str(proj), sel)
    assert "report_binding=weak" in verdict["caveats"]
    assert "report_binding" not in verdict["blockers"]


def test_route_backend_run_binds_without_the_envelope(tmp_path):
    """route.json has recorded `backend_run` since before the envelope existed —
    honor it so pre-P0-R7 route reports still bind."""
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "backend" / "RUN_Z").mkdir(parents=True)
    (proj / "reports" / "route.json").write_text(json.dumps(
        {"status": "clean", "backend_run": "RUN_A"}))

    rb = sg._check_report_binding(str(proj / "reports"), str(proj / "backend" / "RUN_Z"))
    assert rb["status"] == "foreign"


# --------------------------------------------------------------------------- #
# Writer side: the envelope the extractors stamp.                              #
# --------------------------------------------------------------------------- #

def test_report_io_prefers_the_restage_marker_over_the_newest_run(tmp_path):
    """The restage marker names the run whose artifacts the tool ACTUALLY judged,
    which is not always the newest run (full-pipeline Issue 7)."""
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "signoff-loop", "scripts", "extract"))
    import report_io

    proj = tmp_path / "proj"
    for r in ("RUN_A", "RUN_Z"):
        (proj / "backend" / r).mkdir(parents=True)
    (proj / "backend" / "RUN_A" / ".r2g_restaged").write_text("RUN_A\n")

    prov = report_io.run_provenance(proj)
    assert prov["run_tag"] == "RUN_A"          # NOT RUN_Z, the newest
    assert prov["source"] == "restage_marker"

    # With no marker at all the newest run is a labelled guess, never silent.
    (proj / "backend" / "RUN_A" / ".r2g_restaged").unlink()
    assert report_io.run_provenance(proj)["source"] == "latest_run"

    # An explicitly resolved run always wins.
    explicit = report_io.run_provenance(proj, proj / "backend" / "RUN_Z")
    assert explicit["run_tag"] == "RUN_Z" and explicit["source"] == "explicit"
