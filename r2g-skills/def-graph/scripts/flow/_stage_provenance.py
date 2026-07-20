#!/usr/bin/env python3
"""Content-based freshness for the feature (X) and label (Y) stages.

Post-consolidation audit P0-R8 (failure-patterns.md #52). `run_graphs.sh`'s
`needs_stage()` decided whether to reuse an existing features/ or labels/ dir by
comparing CSV and stats-marker MTIMES against the DEF's mtime. Mtimes are not
identity:

  * a content-preserving `touch` (or any restore/`cp -p`/rsync that preserves
    timestamps) makes stale X/Y look current;
  * a DEF written with an older mtime than the CSVs ALWAYS reads fresh;
  * a same-second DEF/CSV pair reads fresh (`-ot` is strict);
  * a schema/extractor change is invisible — the CSVs are "newer than the DEF"
    but were produced by different code against a different contract.

The audit reproduced the first case on the real `picorv32_core`: it edited the
DEF's DIEAREA, restored the original mtime, and `run_graphs.sh` reused the old
feature/label dirs and wrote an `ok` manifest over a layout they did not
describe. Only the independent verifier caught the stale geometry binding.

The fix carries identity instead of inferring it. Each stage stamps what it was
built FROM into its stats JSON (the stage-completion marker, written last), and
the graph stage reuses a stage only when that record still matches the DEF it is
about to graph, byte for byte.

The DEF digest itself is NOT reimplemented here — `signoff_gate._def_fingerprint`
already streams a sha256 for the same purpose, and this skill's standing rule is
one copy of shared logic (the techlib lesson: a worker-local patch fixes one
consumer and silently leaves the others wrong).

Usage:
  _stage_provenance.py stamp --stats <stats.json> --def <def> --stage <name>
                            [--run-dir <d>] [--flow-variant <v>] [--platform <p>]
  _stage_provenance.py check --stats <stats.json> --def <def> [--stage <name>]

`check` exits 0 when the recorded provenance matches (reuse is safe) and 1
otherwise, printing the reason to stderr. Absent provenance is UNVERIFIABLE and
therefore stale — fail-closed, like the signoff and license gates. Operators who
need the pre-2026-07-20 behavior can set R2G_STAGE_FRESHNESS=mtime, which makes
`check` fall back to the caller's mtime comparison for legacy dirs only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from signoff_gate import _def_fingerprint  # noqa: E402  (one copy of the digest)

# Bump when the X/Y CSV contract changes in a way that makes an existing
# features/ or labels/ dir unusable by the current builders — a schema change is
# exactly the staleness an mtime comparison cannot see. Recorded in the stamp and
# compared on reuse.
STAGE_SCHEMA_VERSION = 1

PROV_KEY = "provenance"


def build_provenance(def_path, stage, run_dir="", flow_variant="", platform=""):
    """The record a stage stamps into its completion marker."""
    return {
        "stage": stage,
        "stage_schema_version": STAGE_SCHEMA_VERSION,
        "def_fingerprint": _def_fingerprint(def_path),
        # Which backend run/variant produced that DEF (P0-N3's resolver already
        # picks it); recorded so a dataset's identity chain is inspectable
        # without re-deriving it from directory ordering.
        "run_dir": os.path.realpath(run_dir) if run_dir else None,
        "run_tag": os.path.basename(os.path.realpath(run_dir)) if run_dir else None,
        "flow_variant": flow_variant or None,
        "platform": platform or None,
    }


def stamp(stats_path, prov):
    """Merge `prov` into an existing stats JSON, atomically.

    The stats JSON is the stage-completion marker (written LAST by run_*.sh), so
    stamping it keeps ONE marker rather than adding a second file that could
    disagree with it.
    """
    try:
        with open(stats_path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    doc[PROV_KEY] = prov
    tmp = stats_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    os.replace(tmp, stats_path)
    return doc


def freshness(stats_path, def_path):
    """Is the stage recorded in `stats_path` valid for `def_path`? -> (ok, reason).

    Pure, so the tests can drive every branch without running an extractor.
    """
    if not os.path.isfile(stats_path):
        return False, "no stage-completion marker"
    try:
        with open(stats_path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception as e:  # noqa: BLE001
        return False, f"unreadable stage marker ({e})"
    prov = (doc or {}).get(PROV_KEY)
    if not isinstance(prov, dict):
        return False, ("stage marker carries no provenance — built before content-based "
                       "freshness (P0-R8); cannot be verified against this DEF")
    if prov.get("stage_schema_version") != STAGE_SCHEMA_VERSION:
        return False, (f"stage_schema_version={prov.get('stage_schema_version')!r} != "
                       f"{STAGE_SCHEMA_VERSION} — extracted against a different X/Y contract")
    rec = prov.get("def_fingerprint") or {}
    rec_sha = rec.get("sha256")
    if not rec_sha:
        return False, "recorded DEF fingerprint has no sha256 — unverifiable"
    cur = _def_fingerprint(def_path) or {}
    cur_sha = cur.get("sha256")
    if not cur_sha:
        return False, f"current DEF unreadable: {def_path}"
    if rec_sha != cur_sha:
        return False, (f"DEF content changed since extraction "
                       f"(recorded {rec_sha[:12]}… != current {cur_sha[:12]}…)")
    return True, f"DEF {cur_sha[:12]}… unchanged"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("stamp", help="record what a stage was built from")
    s.add_argument("--stats", required=True)
    s.add_argument("--def", dest="def_path", required=True)
    s.add_argument("--stage", required=True)
    s.add_argument("--run-dir", default="")
    s.add_argument("--flow-variant", default="")
    s.add_argument("--platform", default="")

    c = sub.add_parser("check", help="is a recorded stage reusable for this DEF?")
    c.add_argument("--stats", required=True)
    c.add_argument("--def", dest="def_path", required=True)
    c.add_argument("--stage", default="")

    a = ap.parse_args(argv)
    if a.cmd == "stamp":
        # Never let a provenance-stamp failure fail a stage that otherwise
        # succeeded — an unstamped marker degrades to "unverifiable" on the next
        # run (re-extract), which is the safe direction.
        try:
            stamp(a.stats, build_provenance(a.def_path, a.stage, a.run_dir,
                                            a.flow_variant, a.platform))
        except Exception as e:  # noqa: BLE001
            print(f"WARNING: could not stamp stage provenance into {a.stats}: {e}",
                  file=sys.stderr)
        return 0

    ok, reason = freshness(a.stats, a.def_path)
    if not ok:
        print(f"  {a.stage or 'stage'} not reusable: {reason}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
