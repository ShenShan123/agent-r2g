"""Learning-loop identity: run identity, terminal-state diagnosis, legacy A/B evidence.

Closes the signoff-loop third of the 2026-07-19 post-consolidation audit
(failure-patterns.md #52) — P1-R1, P1-R3, and the P0-R3 operator ruling of
2026-07-20.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "knowledge"))
sys.path.insert(0, str(_ROOT / "scripts" / "reports"))

import ab_runner  # noqa: E402
import ingest_run  # noqa: E402
import build_diagnosis as bd  # noqa: E402


# --------------------------------------------------------------------------- #
# P1-R1 — two no-PPA attempts must not collapse onto one run_id.               #
# --------------------------------------------------------------------------- #

def _attempt(project: Path, tag: str, ledger: list[dict]) -> Path:
    run = project / "backend" / tag
    run.mkdir(parents=True, exist_ok=True)
    slog = run / "stage_log.jsonl"
    slog.write_text("\n".join(json.dumps(r) for r in ledger) + "\n")
    return run


_CLEAN = [{"stage": s, "status": 0, "elapsed_s": 1}
          for s in ("synth", "floorplan", "place", "cts", "route", "finish")]
_FAILED = [{"stage": "synth", "status": 0, "elapsed_s": 1},
           {"stage": "floorplan", "status": 0, "elapsed_s": 1},
           {"stage": "place", "status": 2, "elapsed_s": 1}]


def test_two_no_ppa_attempts_get_distinct_run_ids(tmp_path):
    """The audit ingested a clean wbuart32 with no ppa.json, then ingested a
    failed attempt over it: both hashed to the same id and the success vanished."""
    proj = tmp_path / "proj"
    ppa = proj / "reports" / "ppa.json"          # deliberately absent
    a = _attempt(proj, "RUN_2026-07-20_10-00-00_111_aaaa", _CLEAN)
    b = _attempt(proj, "RUN_2026-07-20_11-00-00_222_bbbb", _FAILED)

    id_a = ingest_run._compute_run_id(proj, ppa, a, a / "stage_log.jsonl")
    id_b = ingest_run._compute_run_id(proj, ppa, b, b / "stage_log.jsonl")
    assert id_a != id_b, "distinct backend attempts collapsed onto one run_id"


def test_reingesting_one_unchanged_attempt_is_idempotent(tmp_path):
    proj = tmp_path / "proj"
    ppa = proj / "reports" / "ppa.json"
    a = _attempt(proj, "RUN_2026-07-20_10-00-00_111_aaaa", _CLEAN)

    first = ingest_run._compute_run_id(proj, ppa, a, a / "stage_log.jsonl")
    second = ingest_run._compute_run_id(proj, ppa, a, a / "stage_log.jsonl")
    assert first == second


def test_same_run_dir_with_a_changed_ledger_is_a_distinct_record(tmp_path):
    """The audit's literal reproduction — the ledger is flipped to failure in
    place. A changed outcome record is a different record, not an overwrite."""
    proj = tmp_path / "proj"
    ppa = proj / "reports" / "ppa.json"
    a = _attempt(proj, "RUN_2026-07-20_10-00-00_111_aaaa", _CLEAN)
    before = ingest_run._compute_run_id(proj, ppa, a, a / "stage_log.jsonl")

    _attempt(proj, "RUN_2026-07-20_10-00-00_111_aaaa", _FAILED)   # rewrite in place
    after = ingest_run._compute_run_id(proj, ppa, a, a / "stage_log.jsonl")
    assert before != after


def test_ppa_backed_run_id_is_byte_identical_to_the_legacy_scheme(tmp_path):
    """Changing the WITH-ppa derivation would re-key every existing row in the
    committed store and orphan its projections. It must not move."""
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    ppa = proj / "reports" / "ppa.json"
    ppa.write_text("{}")
    a = _attempt(proj, "RUN_2026-07-20_10-00-00_111_aaaa", _CLEAN)

    import hashlib
    marker = str(ppa.stat().st_mtime_ns)
    h = hashlib.sha1()
    h.update(str(proj.resolve()).encode())
    h.update(b":")
    h.update(marker.encode())
    assert ingest_run._compute_run_id(proj, ppa, a, a / "stage_log.jsonl") == h.hexdigest()


# --------------------------------------------------------------------------- #
# P1-R3 — a terminal-clean ledger vetoes a superseded timing message.          #
# --------------------------------------------------------------------------- #

_INTERMEDIATE = ("=== flow.log ===\n"
                 "[INFO] repair_timing: setup violation on path u_alu/r1\n"
                 "[INFO] perform_buffer_insertion\n"
                 "[INFO] flow completed\n")


def test_clean_six_stage_run_does_not_learn_a_superseded_timing_failure(tmp_path):
    """The audit's untouched SUCCESSFUL wbuart32: six stages at status 0, clean
    DRC, no ppa.json — diagnosed kind=timing_violation from an intermediate line,
    which ingest_run writes straight into failure_events."""
    proj = tmp_path / "proj"
    _attempt(proj, "RUN_2026-07-20_10-00-00_111_aaaa", _CLEAN)

    issues = bd.detect_issues(_INTERMEDIATE, proj)
    assert not [i for i in issues if i["kind"] == "timing_violation"], issues


def test_a_terminal_timing_failure_without_ppa_stays_diagnosable(tmp_path):
    """The veto must be narrow: only a terminal-CLEAN ledger suppresses the text
    fallback, or a real failure would become undiagnosable."""
    proj = tmp_path / "proj"
    _attempt(proj, "RUN_2026-07-20_10-00-00_111_aaaa", _FAILED)

    issues = bd.detect_issues(_INTERMEDIATE, proj)
    assert [i for i in issues if i["kind"] == "timing_violation"]


def test_absent_ledger_never_silently_suppresses_a_diagnosis(tmp_path):
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    assert bd._terminal_stages_clean(proj) is False
    assert [i for i in bd.detect_issues(_INTERMEDIATE, proj)
            if i["kind"] == "timing_violation"]


def test_terminal_clean_accepts_both_int_and_string_ledger_dialects(tmp_path):
    """run_orfs.sh writes int exit codes; fixtures and legacy writers use
    'pass'/'fail' strings."""
    proj = tmp_path / "proj"
    _attempt(proj, "RUN_A", [{"stage": s, "status": "pass", "elapsed_s": 1}
                             for s in ("synth", "floorplan", "place", "cts",
                                       "route", "finish")])
    assert bd._terminal_stages_clean(proj) is True


def test_partial_flow_is_not_terminal_clean(tmp_path):
    proj = tmp_path / "proj"
    _attempt(proj, "RUN_A", [{"stage": s, "status": 0, "elapsed_s": 1}
                             for s in ("synth", "floorplan", "place")])
    assert bd._terminal_stages_clean(proj) is False


# --------------------------------------------------------------------------- #
# P0-R3 — legacy unverifiable evidence: visible, not countable (2026-07-20).   #
# --------------------------------------------------------------------------- #

def _store(tmp_path):
    import knowledge_db
    db = tmp_path / "k.sqlite"
    conn = knowledge_db.connect(str(db))
    knowledge_db.ensure_schema(conn)
    return conn


_KEY = dict(symptom_id="S1", design_class="logic/small", platform="nangate45",
            strategy="density_relief")


def _add_trial(conn, verdict, *, metrics=None, a_rid=None, b_rid=None):
    conn.execute(
        "INSERT INTO ab_trials (symptom_id, design_class, platform, strategy, "
        "verdict, metrics_json, arm_a_run_id, arm_b_run_id) VALUES (?,?,?,?,?,?,?,?)",
        (_KEY["symptom_id"], _KEY["design_class"], _KEY["platform"],
         _KEY["strategy"], verdict,
         json.dumps(metrics) if metrics is not None else None, a_rid, b_rid))
    conn.commit()


def test_legacy_unverified_win_cannot_promote(tmp_path):
    """77 decisive legacy trials in the committed store carry NULL arm run_ids —
    evidence nobody can trace to two distinct real runs."""
    import recipe_lifecycle
    conn = _store(tmp_path)
    recipe_lifecycle.enqueue_candidate(conn, provenance="test", **_KEY)
    _add_trial(conn, "win")                       # no metrics, NULL run_ids

    assert ab_runner.judge_recipe(conn, **_KEY) is None
    assert recipe_lifecycle.get_status(conn, default="", **_KEY) == "candidate"


def test_an_already_promoted_key_keeps_its_state(tmp_path):
    """Quarantine FORWARD ONLY (2026-07-20 operator ruling): the 21 keys promoted
    on legacy evidence stay promoted — the 0-flips discipline — while no legacy
    row can move a key from here on. Verified on the real store: 0 of 114 trial
    keys changed status, promoted stayed 25/25."""
    import recipe_lifecycle
    conn = _store(tmp_path)
    recipe_lifecycle.promote(conn, evidence="legacy_seed", **_KEY)
    _add_trial(conn, "win")

    assert ab_runner.judge_recipe(conn, **_KEY) is None
    assert recipe_lifecycle.get_status(conn, default="", **_KEY) == "promoted"


def test_legacy_rows_are_still_recorded_for_audit(tmp_path):
    """Record the truth, filter at the consumer — the same firewall the
    provenance filter uses. The rows must remain visible in ab_trials."""
    conn = _store(tmp_path)
    _add_trial(conn, "win")
    assert conn.execute("SELECT count(*) FROM ab_trials").fetchone()[0] == 1


def test_new_verified_evidence_still_governs_a_legacy_promoted_key(tmp_path):
    """The quarantine freezes legacy evidence, not the key: a real, traceable
    loss must still be able to demote a legacy-promoted recipe."""
    import recipe_lifecycle
    conn = _store(tmp_path)
    recipe_lifecycle.promote(conn, evidence="legacy_seed", **_KEY)
    _add_trial(conn, "win")                                     # legacy, ignored

    monkey = {}
    orig_exist, orig_owned = ab_runner._runs_exist, ab_runner._arms_owned
    ab_runner._runs_exist = lambda *a, **k: True
    ab_runner._arms_owned = lambda *a, **k: True
    try:
        _add_trial(conn, "loss", metrics={"provenance_complete": True},
                   a_rid="RID_A", b_rid="RID_B")
        assert ab_runner.judge_recipe(conn, **_KEY) == "shadow"
    finally:
        ab_runner._runs_exist, ab_runner._arms_owned = orig_exist, orig_owned
    assert recipe_lifecycle.get_status(conn, default="", **_KEY) == "shadow"
