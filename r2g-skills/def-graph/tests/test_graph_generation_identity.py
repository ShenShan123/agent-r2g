"""Graph-generation identity: atomic publication + an explicit schema version.

Covers the 2026-07-19 post-consolidation audit's P0-R9 and P0-N7
(failure-patterns.md #52). Both are instances of the round's shared root cause —
identity inferred from file presence rather than carried as one immutable
generation stamp:

  * **P0-R9** — every {v}_graph.pt was written DIRECTLY into the live dataset
    dir and only the final JSON manifest was replaced atomically. A failure
    after the first view left a MIXED generation (new b, old c..f) under the
    previous, still-green manifest. The audit reproduced it on a real 52,574-node
    picorv32 rebuild by making the C output unwritable.
  * **P0-N7** — the published manifest identified file presence and generation
    status but never the tensor contract it was built against, so an old
    structurally-incompatible generation stayed officially `status: ok`. The
    audit's real picorv32 manifest scored 171/186 for exactly this reason: 14
    y_raw/edge_y_raw checks and one HPWL check belonged to a newer contract.

The sibling rtl-acquire skill already versions its pre-layout netlist graphs
(graph_stats.py `graph_schema_version`), so the post-layout dataset — the one
with 4 node types, a hetero/homo split and multi-slot y tensors — being
unversioned was the sharper half of the asymmetry.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

pd = pytest.importorskip("pandas")

import graph_lib as gl  # noqa: E402


def write_csv(path, header, rows):
    """Local CSV writer — `tests` is a package, so the conftest helper of the
    same name is not importable as a bare module here."""
    import csv
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)

try:  # pragma: no cover - environment probe
    import torch
    import torch_geometric  # noqa: F401
    _HAS_TORCH = True
except Exception:  # pragma: no cover
    torch = None
    _HAS_TORCH = False

pytestmark = pytest.mark.skipif(not _HAS_TORCH,
                                reason="torch/torch_geometric not installed")


def _digests(out):
    """Content digest of every published artifact in the dataset dir."""
    import hashlib
    got = {}
    for name in sorted(os.listdir(out)):
        p = os.path.join(out, name)
        if os.path.isfile(p):
            with open(p, "rb") as f:
                got[name] = hashlib.sha256(f.read()).hexdigest()
    return got


def _build(feat, lab, out, monkeypatch, variants="bcdef"):
    import build_graphs as bg
    monkeypatch.setattr(sys, "argv", [
        "build_graphs.py", "--features", str(feat), "--labels", str(lab),
        "--design", "mini", "--out-dir", str(out), "--variants", variants])
    bg.main()


# --------------------------------------------------------------------------- #
# P0-N7 — every published generation declares its tensor contract.             #
# --------------------------------------------------------------------------- #

def test_manifest_declares_a_graph_schema_version(mini_csvs, tmp_path, monkeypatch):
    feat, lab = mini_csvs
    out = tmp_path / "dataset"
    _build(feat, lab, out, monkeypatch, variants="b")
    man = json.load(open(out / "graph_manifest.json"))

    import build_graphs as bg
    assert man["graph_schema_version"] == bg.GRAPH_SCHEMA_VERSION
    # An int that ORDERS generations — the point is comparability, which the
    # pre-existing self-describing column lists (x_schema_per_type/y_schema)
    # deliberately do not provide.
    assert isinstance(man["graph_schema_version"], int)
    # A generation id makes a torn publish DETECTABLE even where it cannot be
    # made impossible (see the commit-window note in build_graphs.main).
    assert man["generation_id"] and isinstance(man["generation_id"], str)


def test_verifier_rejects_an_unversioned_legacy_generation(tmp_path):
    """A pre-P0-N7 manifest must fail ONCE, loudly, with a rebuild hint — not
    dribble out 15 mysterious tensor-field mismatches (the audit's 171/186)."""
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "..", "tools"))
    import verify_graph_dataset as vg

    legacy = {"design": "mini", "status": "ok", "graph_kind": "hetero", "variants": {}}
    ok, detail = vg.check_graph_schema_version(legacy)
    assert not ok
    assert "rebuild" in detail.lower()

    future = dict(legacy, graph_schema_version=vg.SUPPORTED_GRAPH_SCHEMA + 1)
    ok, detail = vg.check_graph_schema_version(future)
    assert not ok

    current = dict(legacy, graph_schema_version=vg.SUPPORTED_GRAPH_SCHEMA)
    ok, _ = vg.check_graph_schema_version(current)
    assert ok


# --------------------------------------------------------------------------- #
# P0-R9 — a failed rebuild leaves the previous generation byte-identical.      #
# --------------------------------------------------------------------------- #

def test_failed_rebuild_leaves_previous_generation_byte_identical(
        mini_csvs, tmp_path, monkeypatch):
    feat, lab = mini_csvs
    out = tmp_path / "dataset"

    _build(feat, lab, out, monkeypatch, variants="bcdef")
    before = _digests(out)
    assert {"b_graph.pt", "c_graph.pt", "d_graph.pt", "e_graph.pt", "f_graph.pt",
            "graph_manifest.json"} <= set(before)

    # Reproduce the audit's injection: the SECOND view written fails hard. Under
    # the pre-fix direct-write scheme this published a new b beside an old c..f.
    import build_graphs as bg
    real_save = bg._torch().save
    calls = {"n": 0}

    def flaky_save(obj, path, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError(13, "Permission denied", str(path))
        return real_save(obj, path, *a, **kw)

    monkeypatch.setattr(bg._torch(), "save", flaky_save)
    # Change the inputs so a committed rebuild would be VISIBLY different: any
    # surviving new byte in the live dir is then a published mixed generation.
    write_csv(os.path.join(feat, "nodes_gate.csv"),
              ["graph_id", "inst_name", "master", *gl.GATE_SCHEMA],
              [["mini", "g1", "INV_X1", 0, 9.0, 9.0, 99.0, 99.0, 0, 0],
               ["mini", "g2", "INV_X2", 1, 1.5, 2.5, 30.0, 40.0, 0, 0]])

    with pytest.raises(Exception):
        _build(feat, lab, out, monkeypatch, variants="bcdef")

    assert _digests(out) == before, (
        "a failed rebuild published a MIXED generation — every live artifact and "
        "the manifest must be byte-identical to the last good generation (P0-R9)")


def test_failed_rebuild_leaves_no_staging_residue(mini_csvs, tmp_path, monkeypatch):
    """Staging must not accumulate: a crashed build cleans up after itself, so the
    dataset dir never grows a litter of half-generations."""
    feat, lab = mini_csvs
    out = tmp_path / "dataset"
    _build(feat, lab, out, monkeypatch, variants="b")

    import build_graphs as bg
    monkeypatch.setattr(bg._torch(), "save",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(Exception):
        _build(feat, lab, out, monkeypatch, variants="b")

    leftovers = [n for n in os.listdir(out) if n.startswith(".")]
    assert leftovers == [], f"staging residue left behind: {leftovers}"


def test_successful_rebuild_still_publishes_every_variant(mini_csvs, tmp_path,
                                                          monkeypatch):
    """The staging commit must be transparent on the happy path — same file
    names, same live locations, manifest paths pointing at the LIVE dir."""
    feat, lab = mini_csvs
    out = tmp_path / "dataset"
    _build(feat, lab, out, monkeypatch, variants="bcdef")

    man = json.load(open(out / "graph_manifest.json"))
    assert set(man["variants"]) == set("bcdef")
    for v in "bcdef":
        assert os.path.isfile(out / f"{v}_graph.pt")
        # No staging path may leak into the published manifest.
        assert man["variants"][v]["path"] == str((out / f"{v}_graph.pt").resolve())


def test_rebuild_with_fewer_variants_still_removes_stale(mini_csvs, tmp_path,
                                                         monkeypatch):
    """Staging must not regress the full-pipeline #6 stale-cleanup invariant: the
    manifest commit-point still describes EXACTLY what is on disk."""
    feat, lab = mini_csvs
    out = tmp_path / "dataset"
    _build(feat, lab, out, monkeypatch, variants="bcdef")
    _build(feat, lab, out, monkeypatch, variants="b")

    assert os.path.isfile(out / "b_graph.pt")
    for v in "cdef":
        assert not os.path.isfile(out / f"{v}_graph.pt"), f"{v} survived a narrower rebuild"
