"""resume_lineage.py: digest-complete stage evidence + fail-closed resume
verification (RMD2-P0-02, three-platform pilot 2026-07-24).

The pilot found repair runs recording synth.sha256=null against a nonexistent
canonical artifact (1_synth.v), silently attributed to the newest clean-looking
sibling. run_orfs.sh now (a) appends a stage_artifact_manifest.jsonl row (path,
size, sha256, identity, toolchain) after every successful stage, and (b) BEFORE
a FROM_STAGE resume cleans anything, verifies every reused stage's workspace
artifact exists, hashes it, and matches a recorded parent digest — stopping the
resume (exit 4) on missing/unhashable/unattributable bytes.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

FLOW = Path(__file__).resolve().parents[1] / "scripts" / "flow"
RL = FLOW / "resume_lineage.py"

sys.path.insert(0, str(FLOW))
from stage_artifacts import STAGE_ARTIFACT, STAGE_CONTRACT_VERSION  # noqa: E402

STAGES = "synth floorplan place cts route finish"
REUSED = ("synth", "floorplan", "place", "cts")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _run(args, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, str(RL), *args],
                          capture_output=True, text=True, env=env, timeout=60)


def _mk_workspace(tmp_path, stages=REUSED):
    rdir = tmp_path / "orfs_results"
    rdir.mkdir(exist_ok=True)
    payloads = {}
    for s in stages:
        art = STAGE_ARTIFACT[s]
        payload = f"bytes-of-{art}".encode()
        (rdir / art).write_bytes(payload)
        payloads[s] = payload
    return rdir, payloads


def _mk_parent(tmp_path, name, payloads, *, manifest=True, stage_log=True,
               identity=("nangate45", "demo", "proj")):
    run = tmp_path / "backend" / name
    run.mkdir(parents=True)
    platform, design, variant = identity
    if stage_log:
        with open(run / "stage_log.jsonl", "w") as f:
            for s in ("synth", "floorplan", "place", "cts", "route", "finish"):
                f.write(json.dumps({"stage": s, "status": 0, "elapsed_s": 1}) + "\n")
    if manifest:
        with open(run / "stage_artifact_manifest.jsonl", "w") as f:
            for s, payload in payloads.items():
                f.write(json.dumps({
                    "schema_version": 1,
                    "stage_contract_version": STAGE_CONTRACT_VERSION,
                    "stage": s, "status": 0, "run_tag": name,
                    "artifact": STAGE_ARTIFACT[s], "sha256": _sha(payload),
                    "size": len(payload), "platform": platform,
                    "design": design, "flow_variant": variant}) + "\n")
    return run


def _verify(tmp_path, rdir, child="RUN_child", env_extra=None):
    run_dir = tmp_path / "backend" / child
    run_dir.mkdir(parents=True, exist_ok=True)
    r = _run(["verify",
              "--backend", str(tmp_path / "backend"),
              "--run-dir", str(run_dir),
              "--from-stage", "route", "--stages", STAGES,
              "--results-dir", str(rdir),
              "--reason", "test", "--no-clean", "0",
              "--platform", "nangate45", "--design", "demo",
              "--flow-variant", "proj"], env_extra)
    meta = None
    mp = run_dir / "resume_meta.json"
    if mp.is_file():
        meta = json.loads(mp.read_text())
    return r, meta


# ---- record ------------------------------------------------------------------

def test_record_writes_digest_row(tmp_path):
    rdir, payloads = _mk_workspace(tmp_path, stages=("synth",))
    run_dir = tmp_path / "backend" / "RUN_X"
    run_dir.mkdir(parents=True)
    r = _run(["record", "--run-dir", str(run_dir), "--run-tag", "RUN_X",
              "--stage", "synth", "--status", "0", "--results-dir", str(rdir),
              "--platform", "nangate45", "--design", "demo",
              "--flow-variant", "proj", "--toolchain", "orfs=@abc"])
    assert r.returncode == 0, r.stderr
    row = json.loads((run_dir / "stage_artifact_manifest.jsonl").read_text())
    assert row["stage"] == "synth"
    assert row["artifact"] == "1_synth.odb"          # the pilot's exact defect
    assert row["sha256"] == _sha(payloads["synth"])
    assert row["size"] == len(payloads["synth"])
    assert row["stage_contract_version"] == STAGE_CONTRACT_VERSION
    assert row["platform"] == "nangate45" and row["flow_variant"] == "proj"
    assert row["toolchain"] == "orfs=@abc"


def test_record_absent_artifact_is_explicit_null(tmp_path):
    rdir = tmp_path / "orfs_results"
    rdir.mkdir()
    run_dir = tmp_path / "backend" / "RUN_X"
    run_dir.mkdir(parents=True)
    r = _run(["record", "--run-dir", str(run_dir), "--run-tag", "RUN_X",
              "--stage", "synth", "--status", "0", "--results-dir", str(rdir)])
    assert r.returncode == 0
    row = json.loads((run_dir / "stage_artifact_manifest.jsonl").read_text())
    assert row["sha256"] is None and "absent" in row["note"]


# ---- verify: the happy path --------------------------------------------------

def test_verify_matching_parent_digests(tmp_path):
    rdir, payloads = _mk_workspace(tmp_path)
    _mk_parent(tmp_path, "RUN_parent", payloads)
    r, meta = _verify(tmp_path, rdir)
    assert r.returncode == 0, r.stderr
    for s in REUSED:
        entry = meta["parent_lineage"][s]
        assert entry["verified"] is True and entry["source"] == "recorded"
        assert entry["parent_run"] == "RUN_parent"
        assert entry["sha256"] == _sha(payloads[s])
        assert entry["artifact"] == STAGE_ARTIFACT[s]
    assert meta["stage_contract_version"] == STAGE_CONTRACT_VERSION
    assert "violations" not in meta


# ---- verify: fail-closed stops (plan §5.3 / acceptance 3) --------------------

def test_verify_missing_artifact_stops_resume(tmp_path):
    rdir, payloads = _mk_workspace(tmp_path)
    os.unlink(rdir / STAGE_ARTIFACT["place"])
    _mk_parent(tmp_path, "RUN_parent", payloads)
    r, meta = _verify(tmp_path, rdir)
    assert r.returncode == 4, (r.returncode, r.stderr)
    assert "MISSING" in r.stderr
    assert any("place" in v for v in meta["violations"])


def test_verify_unattributable_bytes_stop_resume(tmp_path):
    """Workspace bytes matching NO recorded parent digest must stop the resume —
    never silently fall back to 'the newest sibling looked clean'."""
    rdir, payloads = _mk_workspace(tmp_path)
    _mk_parent(tmp_path, "RUN_parent", payloads)
    (rdir / STAGE_ARTIFACT["synth"]).write_bytes(b"foreign-bytes-nobody-recorded")
    r, meta = _verify(tmp_path, rdir)
    assert r.returncode == 4, r.stderr
    assert any("matches NO" in v for v in meta["violations"])
    entry = meta["parent_lineage"]["synth"]
    assert entry["verified"] is False and entry["parent_run"] is None


def test_verify_enforce_override_records_but_proceeds(tmp_path):
    rdir, payloads = _mk_workspace(tmp_path)
    os.unlink(rdir / STAGE_ARTIFACT["cts"])
    _mk_parent(tmp_path, "RUN_parent", payloads)
    r, meta = _verify(tmp_path, rdir, env_extra={"R2G_RESUME_LINEAGE_ENFORCE": "0"})
    assert r.returncode == 0, r.stderr
    assert meta["enforced"] is False
    assert any("cts" in v for v in meta["violations"])


# ---- verify: legacy corpus degrades loudly, never silently -------------------

def test_verify_legacy_parent_is_unverified_not_fatal(tmp_path):
    """Parents that predate the stage manifest cannot be digest-attributed: the
    resume proceeds (legacy corpus stays repairable) but the lineage is recorded
    UNVERIFIED — the def-graph gate keeps such generations out of the strict
    tier."""
    rdir, payloads = _mk_workspace(tmp_path)
    _mk_parent(tmp_path, "RUN_parent", payloads, manifest=False)
    r, meta = _verify(tmp_path, rdir)
    assert r.returncode == 0, r.stderr
    assert "legacy_stage_log" in r.stderr or "WARNING" in r.stderr
    for s in REUSED:
        entry = meta["parent_lineage"][s]
        assert entry["verified"] is False
        assert entry["source"] == "legacy_stage_log"
        assert entry["parent_run"] == "RUN_parent"
        assert entry["sha256"] == _sha(payloads[s])   # consumed bytes still hashed
