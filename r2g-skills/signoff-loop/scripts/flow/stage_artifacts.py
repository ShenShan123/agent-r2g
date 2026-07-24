#!/usr/bin/env python3
"""The versioned canonical stage→artifact contract (RMD2-P0-02).

ONE definition of which artifact fingerprints each ORFS stage. The 2026-07-24
three-platform pilot found the previous inline map named a NONEXISTENT synth
artifact (``1_synth.v`` — the active ORFS flow produces ``1_synth.odb`` and
``1_2_yosys.v``), so every repair/resume run recorded ``synth.sha256=null`` and
the graph gate accepted provenance-incomplete lineage. Keeping the map here —
consumed by run_orfs.sh (resume_lineage.py) on the write side and by def-graph's
signoff_gate.py on the verify side — means a rename is a single, versioned
change instead of a silent shell/Python divergence.

Bump STAGE_CONTRACT_VERSION whenever a canonical artifact name changes; both
the recorder and the gate stamp/compare it, so mixed-generation evidence is
detected instead of misjudged.
"""
from __future__ import annotations

import hashlib
import os

# v1 was the incorrect 2026-07-21 map carrying synth="1_synth.v" (never
# hashable); v2 is the canonical ORFS artifact set (remediation plan §5.1).
STAGE_CONTRACT_VERSION = 2

STAGE_ARTIFACT = {
    "synth": "1_synth.odb",
    "floorplan": "2_floorplan.odb",
    "place": "3_place.odb",
    "cts": "4_cts.odb",
    "route": "5_route.odb",
    "finish": "6_final.odb",
}

# The append-only per-run evidence file run_orfs.sh writes one row per
# successful stage into (remediation plan §5.2).
STAGE_MANIFEST_NAME = "stage_artifact_manifest.jsonl"


def sha256_file(path: str) -> str | None:
    """Streamed sha256 of a file; None when unreadable."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def is_sha256(value) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(c in "0123456789abcdef" for c in value))


def load_stage_manifest(run_dir: str) -> dict:
    """{stage: last recorded row} from a run's stage_artifact_manifest.jsonl.

    Last row wins per stage (a rerun stage within one run supersedes its own
    earlier record). Returns {} when the run predates the manifest (legacy).
    """
    import json

    rows: dict = {}
    path = os.path.join(run_dir, STAGE_MANIFEST_NAME)
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                stage = rec.get("stage")
                if stage:
                    rows[str(stage)] = rec
    except OSError:
        return {}
    return rows
