"""Backend-run variant selection guard (failure-patterns.md #52; 2026-07-19 audit P0-N3).

All three stage runners accept `flow_variant` as their third argument, but it was
forwarded ONLY to the live-ORFS-results fallback — run selection itself took the
first reverse-sorted RUN_* holding a final DEF. So on a project carrying two
complete backend runs,

    run_graphs.sh <project> nangate45 variant_a

returned 0 while publishing variant_b's layout. That is a dataset-identity
failure of the same silent class as #30 (wrong-platform manifest): the row counts
look right, so nothing downstream notices.

Sibling of test_platform_provenance.py — same authority idea (build provenance in
run-meta.json is what the artifact IS), same one-shared-copy rule.
"""
import json
import os
import subprocess

_FLOW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "scripts", "flow")
_SEL = os.path.join(_FLOW, "_select_run.sh")


def _sh(backend_dir, want=""):
    r = subprocess.run(["bash", _SEL, str(backend_dir), want],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return [ln for ln in r.stdout.split() if ln], r.stderr


def _backend(tmp_path, runs):
    """runs: {run_name: flow_variant or None (= no run-meta.json)}"""
    backend = tmp_path / "backend"
    for name, variant in runs.items():
        rd = backend / name
        (rd / "results").mkdir(parents=True)
        (rd / "results" / "6_final.def").write_text("DESIGN d ;\n", encoding="utf-8")
        if variant is not None:
            (rd / "run-meta.json").write_text(
                json.dumps({"run_tag": name, "platform": "nangate45",
                            "flow_variant": variant}), encoding="utf-8")
    return backend


def test_explicit_variant_selects_that_variants_run(tmp_path):
    """The report's exact reproduction: RUN_A=variant_a, RUN_Z=variant_b. Reverse
    sort puts RUN_Z first, so only a variant-aware selector picks RUN_A."""
    backend = _backend(tmp_path, {"RUN_A": "variant_a", "RUN_Z": "variant_b"})
    picked, _ = _sh(backend, "variant_a")
    assert [os.path.basename(p) for p in picked] == ["RUN_A"]
    picked, _ = _sh(backend, "variant_b")
    assert [os.path.basename(p) for p in picked] == ["RUN_Z"]


def test_no_variant_preserves_legacy_reverse_sort(tmp_path):
    """No variant requested => byte-identical to the old `ls -d RUN_* | sort -r`,
    so every existing caller and every built corpus is untouched."""
    backend = _backend(tmp_path, {"RUN_A": "variant_a", "RUN_Z": "variant_b"})
    picked, err = _sh(backend, "")
    assert [os.path.basename(p) for p in picked] == ["RUN_Z", "RUN_A"]
    assert err == ""


def test_unmatched_variant_selects_nothing_loudly(tmp_path):
    """Fail CLOSED: better to publish nothing than another variant's layout."""
    backend = _backend(tmp_path, {"RUN_A": "variant_a"})
    picked, err = _sh(backend, "variant_q")
    assert picked == []
    assert "no backend run matches flow_variant=variant_q" in err


def test_unrecorded_run_is_excluded_when_a_variant_is_requested(tmp_path):
    """A run whose identity was never recorded cannot satisfy an EXPLICIT request;
    accepting it would reintroduce exactly the bug this guard exists to stop."""
    backend = _backend(tmp_path, {"RUN_A": "variant_a", "RUN_M": None})
    picked, err = _sh(backend, "variant_a")
    assert [os.path.basename(p) for p in picked] == ["RUN_A"]
    assert "no recorded flow_variant" in err and "RUN_M" in err


def test_unrecorded_runs_still_usable_without_a_variant(tmp_path):
    """Legacy projects with no run-meta.json keep working on the default path."""
    backend = _backend(tmp_path, {"RUN_A": None, "RUN_Z": None})
    picked, err = _sh(backend, "")
    assert [os.path.basename(p) for p in picked] == ["RUN_Z", "RUN_A"]
    assert err == ""


def test_missing_backend_dir_is_quiet(tmp_path):
    picked, err = _sh(tmp_path / "nope", "")
    assert picked == [] and err == ""


def test_selector_wired_once_into_all_three_stage_scripts():
    """One shared copy, per the techlib lesson: a worker-local patch fixes one
    consumer and silently leaves the others wrong. Each runner must call the
    helper and must not re-inline the raw RUN_* glob."""
    for script in ("run_labels.sh", "run_features.sh", "run_graphs.sh"):
        src = open(os.path.join(_FLOW, script), encoding="utf-8").read()
        assert "_select_run.sh" in src, f"{script} lost the #52 variant guard"
        assert 'ls -d "$BACKEND_DIR"/RUN_*' not in src, \
            f"{script} re-inlined the unfiltered run glob"


def test_all_three_runners_forward_the_variant_to_the_selector():
    """run_graphs.sh forwards its variant arg to run_features/run_labels, so the
    three stages must agree on WHICH run they are describing."""
    for script in ("run_labels.sh", "run_features.sh", "run_graphs.sh"):
        src = open(os.path.join(_FLOW, script), encoding="utf-8").read()
        calls = [ln for ln in src.splitlines() if "_select_run.sh" in ln]
        # Assert the call sites EXIST before asserting their shape — an empty
        # loop body is a vacuous pass, which is how this test first "passed"
        # against the unfixed runners.
        assert calls, f"{script}: no _select_run.sh call site"
        for line in calls:
            assert "FLOW_VARIANT_ARG" in line, \
                f"{script}: selector called without the requested variant"
