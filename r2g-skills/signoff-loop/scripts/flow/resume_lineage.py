#!/usr/bin/env python3
"""Stage-artifact evidence for full and repair/resume runs (RMD2-P0-02).

Two subcommands, both driven by run_orfs.sh:

  record  — after a stage SUCCEEDS, append one row to the run's append-only
            stage_artifact_manifest.jsonl: stage, run tag, canonical artifact
            path + size + sha256, platform/design/flow-variant identity,
            timestamp, toolchain fingerprint (remediation plan §5.2).

  verify  — BEFORE a resume (FROM_STAGE) cleans and reruns anything: resolve
            every reused stage to one explicit parent run, hash the exact
            artifact about to be consumed from the ORFS workspace, compare it
            with the parent's recorded stage digest, and write the parent run +
            digest into the new run's resume_meta.json. Exit 4 (and stop the
            resume) when any reused stage is missing, unhashable, or matches NO
            recorded parent digest (plan §5.3) — never silently fall back.

Why: the 2026-07-24 three-platform pilot found repair runs recording
``synth.sha256=null`` against a nonexistent canonical artifact name, and the
graph gate then accepted digest-incomplete lineage. The canonical map now lives
in stage_artifacts.py (one versioned contract); this script is the only writer
of resume lineage evidence.

Legacy parents (runs that predate the stage manifest) cannot be
digest-attributed: the resume proceeds with a LOUD warning and records the
consumed bytes' digest with ``source=legacy_stage_log`` / ``verified=false`` —
the def-graph signoff gate treats such lineage as non-clean (blocked from the
strict tier) until a fully-recorded generation exists. Set
R2G_RESUME_LINEAGE_ENFORCE=0 to downgrade hard violations to recorded warnings
(operator escape hatch; the downgrade itself is recorded in resume_meta.json).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stage_artifacts import (  # noqa: E402
    STAGE_ARTIFACT,
    STAGE_CONTRACT_VERSION,
    STAGE_MANIFEST_NAME,
    is_sha256,
    load_stage_manifest,
    sha256_file,
)


def _stage_log_clean(run_dir: str, stage: str) -> bool:
    try:
        with open(os.path.join(run_dir, "stage_log.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("stage") == stage and rec.get("status") in (0, "0"):
                    return True
    except OSError:
        return False
    return False


def _siblings_newest_first(backend: str, self_run: str) -> list[str]:
    try:
        return sorted(
            (d for d in os.listdir(backend)
             if d.startswith("RUN_") and d != self_run
             and os.path.isdir(os.path.join(backend, d))),
            key=lambda d: os.path.getmtime(os.path.join(backend, d)),
            reverse=True)
    except OSError:
        return []


def cmd_record(a) -> int:
    row = {
        "schema_version": 1,
        "stage_contract_version": STAGE_CONTRACT_VERSION,
        "stage": a.stage,
        "status": int(a.status),
        "run_tag": a.run_tag,
        "platform": a.platform or None,
        "design": a.design or None,
        "flow_variant": a.flow_variant or None,
        "ts": int(time.time()),
        "toolchain": a.toolchain or None,
    }
    art = STAGE_ARTIFACT.get(a.stage)
    if art:
        apath = os.path.join(a.results_dir, art)
        row["artifact"] = art
        if os.path.isfile(apath):
            row["artifact_path"] = os.path.realpath(apath)
            try:
                row["size"] = os.stat(apath).st_size
            except OSError:
                row["size"] = None
            row["sha256"] = sha256_file(apath)
        else:
            row["artifact_path"] = None
            row["size"] = None
            row["sha256"] = None
            row["note"] = f"canonical artifact absent under {a.results_dir}"
    else:
        row["artifact"] = None
        row["note"] = f"stage {a.stage!r} has no canonical artifact in the contract"
    with open(os.path.join(a.run_dir, STAGE_MANIFEST_NAME), "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return 0


def verify_resume(backend: str, run_dir: str, from_stage: str, stages: list[str],
                  results_dir: str, reason: str, no_clean: bool,
                  platform: str, design: str, flow_variant: str,
                  enforce: bool = True) -> tuple[dict, list[str]]:
    """Build (and return) the resume_meta document + hard-violation list."""
    self_run = os.path.basename(os.path.realpath(run_dir))
    reused = []
    for s in stages:
        if s == from_stage:
            break
        reused.append(s)

    siblings = _siblings_newest_first(backend, self_run)
    sib_manifests = {d: load_stage_manifest(os.path.join(backend, d)) for d in siblings}

    lineage: dict = {}
    violations: list[str] = []
    for stage in reused:
        art = STAGE_ARTIFACT.get(stage)
        entry: dict = {"artifact": art, "sha256": None, "parent_run": None,
                       "parent_sha256": None, "source": None, "verified": False}
        lineage[stage] = entry
        if not art:
            violations.append(f"{stage}: no canonical artifact in the stage contract")
            continue
        apath = os.path.join(results_dir, art)
        if not os.path.isfile(apath):
            violations.append(
                f"{stage}: canonical artifact {art} MISSING under {results_dir} — "
                "the resume would consume a stage whose bytes do not exist")
            continue
        sha = sha256_file(apath)
        if not is_sha256(sha):
            violations.append(f"{stage}: cannot hash {apath}")
            continue
        entry["sha256"] = sha

        # Recorded parents: sibling runs whose stage manifest carries a digest
        # row for this stage. Attribution is by CONTENT — the consumed bytes
        # must equal a recorded digest, never "the newest sibling looked clean".
        recorded = [d for d in siblings
                    if is_sha256((sib_manifests[d].get(stage) or {}).get("sha256"))]
        matching = [d for d in recorded
                    if sib_manifests[d][stage]["sha256"] == sha]
        # Identity guard: a digest-matching parent recorded for a different
        # platform/design/variant is a foreign artifact, not a parent.
        def _identity_ok(d):
            row = sib_manifests[d][stage]
            for key, want in (("platform", platform), ("design", design),
                              ("flow_variant", flow_variant)):
                have = row.get(key)
                if want and have and have != want:
                    return False
            return True
        matching = [d for d in matching if _identity_ok(d)]
        if matching:
            parent = matching[0]
            entry.update(parent_run=parent,
                         parent_sha256=sib_manifests[parent][stage]["sha256"],
                         source="recorded", verified=True)
            continue
        if recorded:
            violations.append(
                f"{stage}: workspace artifact {art} sha256={sha[:12]}… matches NO "
                f"recorded parent digest ({len(recorded)} sibling run(s) carry a "
                "manifest row for this stage) — the bytes about to be reused are "
                "unattributable (plan §5.3: stop before rerun)")
            continue
        # Legacy corpus: no sibling recorded a digest for this stage. Retain the
        # old newest-clean-ledger attribution, loudly and explicitly non-clean.
        legacy = next((d for d in siblings
                       if _stage_log_clean(os.path.join(backend, d), stage)), None)
        if legacy:
            entry.update(parent_run=legacy, source="legacy_stage_log", verified=False)
            print(f"WARNING: {stage}: parent {legacy} predates the stage-artifact "
                  "manifest — lineage recorded UNVERIFIED (legacy_stage_log); the "
                  "strict dataset tier will not accept this generation (RMD2-P0-02)",
                  file=sys.stderr)
        else:
            print(f"WARNING: {stage}: no sibling run attributes this artifact — "
                  "lineage unresolved; the graph gate will read this generation "
                  "as incomplete (RMD2-P0-02)", file=sys.stderr)

    meta = {
        "from_stage": from_stage,
        "reason": reason,
        "no_clean": no_clean,
        "reused_stages": reused,
        "parent_lineage": lineage,
        "stage_contract_version": STAGE_CONTRACT_VERSION,
        "platform": platform or None,
        "design": design or None,
        "flow_variant": flow_variant or None,
        "ts": int(time.time()),
        "enforced": bool(enforce),
    }
    if violations:
        meta["violations"] = violations
    return meta, violations


def cmd_verify(a) -> int:
    enforce = os.environ.get("R2G_RESUME_LINEAGE_ENFORCE", "1") != "0"
    meta, violations = verify_resume(
        a.backend, a.run_dir, a.from_stage, a.stages.split(),
        a.results_dir, a.reason, a.no_clean == "1",
        a.platform, a.design, a.flow_variant, enforce)
    out = os.path.join(a.run_dir, "resume_meta.json")
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    os.replace(tmp, out)
    if violations:
        for v in violations:
            print(f"ERROR: resume lineage: {v}", file=sys.stderr)
        if enforce:
            print("ERROR: resume STOPPED before mutating the workspace — every "
                  "reused stage must resolve to one recorded parent digest "
                  "(RMD2-P0-02; set R2G_RESUME_LINEAGE_ENFORCE=0 only as a "
                  "recorded operator override)", file=sys.stderr)
            return 4
        print("WARNING: R2G_RESUME_LINEAGE_ENFORCE=0 — proceeding despite lineage "
              "violations (recorded in resume_meta.json)", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="append one stage-artifact manifest row")
    r.add_argument("--run-dir", required=True, help="this run's backend RUN_* dir")
    r.add_argument("--run-tag", required=True)
    r.add_argument("--stage", required=True)
    r.add_argument("--status", required=True)
    r.add_argument("--results-dir", required=True, help="ORFS workspace results dir")
    r.add_argument("--platform", default="")
    r.add_argument("--design", default="")
    r.add_argument("--flow-variant", default="")
    r.add_argument("--toolchain", default="")

    v = sub.add_parser("verify", help="verify + record reused-stage lineage before a resume")
    v.add_argument("--backend", required=True, help="<project>/backend root")
    v.add_argument("--run-dir", required=True, help="this run's backend RUN_* dir")
    v.add_argument("--from-stage", required=True)
    v.add_argument("--stages", required=True, help="space-separated ordered stage list")
    v.add_argument("--results-dir", required=True)
    v.add_argument("--reason", default="")
    v.add_argument("--no-clean", default="0")
    v.add_argument("--platform", default="")
    v.add_argument("--design", default="")
    v.add_argument("--flow-variant", default="")

    a = ap.parse_args(argv)
    if a.cmd == "record":
        return cmd_record(a)
    return cmd_verify(a)


if __name__ == "__main__":
    sys.exit(main())
