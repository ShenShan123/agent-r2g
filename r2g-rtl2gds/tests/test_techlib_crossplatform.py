"""Techlib-restructure safety gate — cross-platform CSV regression scaffold (Task 0).

The upcoming `techlib` restructure consolidates per-platform logic in the feature/
label extractors. Its safety contract is **byte-for-byte identical CSV output**
before/after on two pinned designs that exercise two different PDKs:

  * aes_core (nangate45) — V_nom 1.10, metal1..metal10, 61 distinct curated
    cell_type_ids.
  * cordic  (sky130hd)  — V_nom 1.80, li1/met1..met5, cell_type_id collapses to a
    single value (428) for every gate row. That collapse is the CURRENT baseline
    behavior and is intentionally PRESERVED by the gate, not "fixed".

This module is SCAFFOLDING. It:
  1. Regenerates both stages for both designs into a temp dir via the pinned
     helper `tools/regen_extract_baseline.sh` (same pinned DEFs as the baseline,
     so the auto-find DEF ambiguity — the cordic nangate trap — is removed).
  2. Asserts every produced CSV is byte-identical (md5) to the committed baseline
     under $R2G_TECHLIB_BASELINE (default /tmp/techlib_baseline).
  3. Skips cleanly when the baseline dir is absent (design_cases/ + the baseline
     are gitignored / machine-local), so CI without the corpus still imports and
     runs this file green.

Later tasks (Task 10) add real cross-platform PDK-value assertions on top of this
scaffold; for now the contract is purely "regenerated == baseline, byte for byte".

--- Recorded baseline anchors (sanity references; verified at capture time) ---
  cordic metadata: num_cells=6508 num_nets=1454 num_ios=107 dbu=1000
    tracks_per_layer=li1:1125|met1:1294|met2:956|met3:646|met4:478|met5:128 V_nom=1.80
  cordic nodes_net col 'num_layer' distinct set = {0,2,3,4,5}
  cordic nodes_gate col 'cell_type_id' == 428 for every row (6508 rows)
  aes_core metadata: nangate45, V_nom=1.10, dbu=2000, metal1..metal10
  aes_core nodes_gate 'cell_type_id' distinct count = 61 (curated, varied)
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "tools" / "regen_extract_baseline.sh"
BASELINE_DIR = Path(os.environ.get("R2G_TECHLIB_BASELINE", "/tmp/techlib_baseline"))

# Designs and the CSV sub-trees the gate covers. Per-design subdirs of CSVs.
DESIGNS = ("aes_core", "cordic")
CSV_SUBDIRS = ("features", "labels")


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel_csvs(root: Path) -> dict[str, Path]:
    """Map 'subdir/name.csv' -> abs path for every CSV under root/{features,labels}."""
    out: dict[str, Path] = {}
    for sub in CSV_SUBDIRS:
        d = root / sub
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.csv")):
            out[f"{sub}/{p.name}"] = p
    return out


def _baseline_present() -> bool:
    if not BASELINE_DIR.is_dir():
        return False
    # Require at least one design dir that actually has CSVs captured.
    for d in DESIGNS:
        if _rel_csvs(BASELINE_DIR / d):
            return True
    return False


pytestmark = pytest.mark.skipif(
    not _baseline_present(),
    reason=(
        f"techlib baseline absent at {BASELINE_DIR} "
        "(design_cases/ + baseline are machine-local; "
        "run tools/regen_extract_baseline.sh to capture it)"
    ),
)


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory) -> Path:
    """Regenerate both stages for both designs into a fresh temp dir via the helper.

    Uses the SAME pinned DEFs as the committed baseline, so a byte diff reflects a
    real change in extractor output — not input drift.
    """
    if not HELPER.exists():
        pytest.skip(f"helper script missing: {HELPER}")
    out = tmp_path_factory.mktemp("techlib_current")
    proc = subprocess.run(
        ["bash", str(HELPER), str(out)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        # Surface helper output so a regen failure is debuggable, not a silent skip.
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        pytest.fail(f"regen_extract_baseline.sh failed (rc={proc.returncode})")
    return out


@pytest.mark.parametrize("design", DESIGNS)
def test_regenerated_matches_baseline(regenerated: Path, design: str):
    """Every baseline CSV must be reproduced byte-identically by a fresh regen."""
    base = _rel_csvs(BASELINE_DIR / design)
    if not base:
        pytest.skip(f"no baseline CSVs for {design} under {BASELINE_DIR}")
    cur = _rel_csvs(regenerated / design)

    missing = sorted(set(base) - set(cur))
    assert not missing, f"{design}: regenerated set is missing CSV(s): {missing}"

    # A refactor that renames or adds a CSV must also trip the gate, not slip
    # through just because every *baseline* file still matches.
    extra = sorted(set(cur) - set(base))
    assert not extra, f"{design}: regenerated has unexpected new CSV(s): {extra}"

    mismatches = []
    for rel, base_path in base.items():
        cur_path = cur[rel]
        b, c = _md5(base_path), _md5(cur_path)
        if b != c:
            mismatches.append(f"{rel}: baseline {b} != current {c}")
    assert not mismatches, f"{design}: CSV byte-mismatch vs baseline:\n" + "\n".join(mismatches)


@pytest.mark.parametrize("design", DESIGNS)
def test_md5sums_file_recorded(design: str):
    """Each baseline design dir carries an MD5SUMS manifest matching its CSVs."""
    design_dir = BASELINE_DIR / design
    manifest = design_dir / "MD5SUMS"
    if not manifest.exists():
        pytest.skip(f"no MD5SUMS for {design} (baseline partial)")
    recorded = {}
    for line in manifest.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        digest, rel = line.split(None, 1)
        recorded[rel] = digest
    actual = {rel: _md5(p) for rel, p in _rel_csvs(design_dir).items()}
    assert recorded == actual, (
        f"{design}: MD5SUMS manifest disagrees with on-disk CSVs "
        f"(recorded={recorded}, actual={actual})"
    )
