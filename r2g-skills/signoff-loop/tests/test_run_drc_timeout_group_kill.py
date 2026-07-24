"""DRC timeout must terminate the COMPLETE checker process tree (RMD2-P0-01).

Three-platform pilot 2026-07-24: Nangate45 SHA-256 hit the 7200s DRC budget,
GNU `timeout` and the shell wrapper exited, but KLayout survived with PPID=1 at
~99% CPU and `tee` held the output pipe open — run_drc.sh, fix_signoff.sh, the
engineer loop, and the Pilot all blocked until an operator SIGKILLed the orphan.

run_drc.sh now starts the checker in its own session/process group via
r2g_bounded_run (_bounded_run.sh), writes output DIRECTLY to the run-local log
(no `timeout | tee` pipeline), and on expiry delivers TERM → grace → KILL to the
whole group, verifying no descendant survives before the verdict is written.

Harness mirrors test_run_drc_checker_only.py: hermetic scripts/flow copy, fake
ORFS, stub KLAYOUT_CMD — here a stub that IGNORES SIGTERM and spawns a
TERM-ignoring grandchild (the exact orphan shape of the pilot incident).
"""
import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]

PLATFORM = "nangate45"
DESIGN = "demo"


def _make_exec(path: Path, text: str):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _setup(tmp_path, stub_body: str):
    skill = tmp_path / "skill"
    (skill / "scripts").mkdir(parents=True)
    shutil.copytree(SKILL / "scripts" / "flow", skill / "scripts" / "flow")
    (skill / "knowledge").mkdir()
    (skill / "references").mkdir()

    orfs = tmp_path / "orfs"
    flow = orfs / "flow"
    pdir = flow / "platforms" / PLATFORM
    (pdir / "drc").mkdir(parents=True)
    (flow / "scripts").mkdir(parents=True)
    (flow / "Makefile").write_text("# fake ORFS Makefile — must never be executed\n")
    (pdir / "config.mk").write_text(
        "export KLAYOUT_DRC_FILE = $(PLATFORM_DIR)/drc/FreePDK45.lydrc\n")
    (pdir / "drc" / "FreePDK45.lydrc").write_text(
        "FEOL    = true\nBEOL    = true\nANTENNA = true\nOFFGRID = true\n")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    _make_exec(bindir / "klayout", stub_body)

    proj = tmp_path / "proj"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text(f"export DESIGN_NAME = {DESIGN}\n")
    run = proj / "backend" / "RUN_A"
    (run / "results").mkdir(parents=True)
    for name in ("6_final.gds", "6_final.def", "6_final.v", "6_final.sdc", "5_route.odb"):
        (run / "results" / name).write_text(f"content-of-{name}")
    return skill, orfs, proj, bindir


def _run_drc(tmp_path, skill, orfs, proj, bindir, extra_env=None):
    env = dict(
        os.environ,
        ORFS_ROOT=str(orfs),
        KLAYOUT_CMD=str(bindir / "klayout"),
        PATH=f"{bindir}:{os.environ['PATH']}",
        R2G_JOURNAL_DB=str(tmp_path / "journal.sqlite"),
    )
    env.pop("R2G_ENV_FILE", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(skill / "scripts" / "flow" / "run_drc.sh"), str(proj), PLATFORM],
        env=env, capture_output=True, text=True, timeout=120)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# A checker that ignores TERM and spawns a TERM-ignoring grandchild: only a
# process-GROUP kill can remove this tree. The parent loops (a lone foreground
# `sleep` would die to TERM and let the parent exit by itself); the grandchild
# execs a TERM-ignoring sleep (ignored dispositions survive exec) — the exact
# PPID=1 orphan shape from the pilot.
STUCK_STUB = """#!/usr/bin/env bash
if [[ "${1:-}" == "-v" ]]; then echo "KLayout 0.0.stub"; exit 0; fi
trap '' TERM
( trap '' TERM; echo $BASHPID > "{TMP}/child.pid"; exec sleep 300 ) &
echo $$ > "{TMP}/parent.pid"
echo "processing FreePDK45.lydrc:131"
while :; do sleep 5; done
"""

QUIET_STUB = """#!/usr/bin/env bash
if [[ "${1:-}" == "-v" ]]; then echo "KLayout 0.0.stub"; exit 0; fi
trap '' TERM
echo $$ > "{TMP}/parent.pid"
while :; do sleep 5; done
"""


def test_term_ignoring_tree_fully_reaped_and_stuck_recorded(tmp_path):
    """Acceptance 1–3: the whole TERM-ignoring tree is removed after the grace
    period, run_drc.sh RETURNS (the old tee pipe hung forever), and the verdict
    is status=stuck / exit_code=124 with run + GDS provenance."""
    skill, orfs, proj, bindir = _setup(
        tmp_path, STUCK_STUB.replace("{TMP}", str(tmp_path)))
    t0 = time.monotonic()
    r = _run_drc(tmp_path, skill, orfs, proj, bindir,
                 extra_env={"DRC_TIMEOUT": "2", "DRC_KILL_GRACE": "2"})
    elapsed = time.monotonic() - t0
    assert r.returncode == 124, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert elapsed < 60, f"run_drc.sh took {elapsed:.0f}s — supervisor did not bound the run"

    # Acceptance 1+2: NO surviving checker process — parent and the deep
    # TERM-ignoring grandchild are both gone.
    for name in ("parent.pid", "child.pid"):
        pidfile = tmp_path / name
        assert pidfile.is_file(), f"stub never wrote {name} — harness broken"
        pid = int(pidfile.read_text().strip())
        # KILL delivery is asynchronous; give the kernel a moment.
        for _ in range(20):
            if not _alive(pid):
                break
            time.sleep(0.5)
        assert not _alive(pid), f"{name}={pid} survived run_drc.sh (RMD2-P0-01)"

    # Acceptance 3: stuck verdict with correct provenance.
    result = json.loads((proj / "drc" / "drc_result.json").read_text())
    assert result["status"] == "stuck", result
    assert result["exit_code"] == 124
    assert result["stuck_at_rule"].endswith("lydrc:131")
    assert result["run_tag"] == "RUN_A"
    import hashlib
    gds = (proj / "backend" / "RUN_A" / "results" / "6_final.gds").read_bytes()
    assert result["gds_sha256"] == hashlib.sha256(gds).hexdigest()

    # The checker log was written directly (no tee pipeline) and preserved.
    assert "FreePDK45.lydrc:131" in (proj / "drc" / "6_drc.log").read_text()


def test_timeout_without_rule_ref_reports_timeout(tmp_path):
    """A TERM-ignoring checker that never names a rule still yields a bounded
    timeout verdict (never a hang, never a fabricated stuck rule)."""
    skill, orfs, proj, bindir = _setup(
        tmp_path, QUIET_STUB.replace("{TMP}", str(tmp_path)))
    r = _run_drc(tmp_path, skill, orfs, proj, bindir,
                 extra_env={"DRC_TIMEOUT": "2", "DRC_KILL_GRACE": "2"})
    assert r.returncode == 124, r.stderr
    pid = int((tmp_path / "parent.pid").read_text().strip())
    for _ in range(20):
        if not _alive(pid):
            break
        time.sleep(0.5)
    assert not _alive(pid), "checker survived timeout"
    result = json.loads((proj / "drc" / "drc_result.json").read_text())
    assert result["status"] == "timeout"
    assert result["reason"] == "drc_timeout"
    assert result["exit_code"] == 124
    assert "stuck_at_rule" not in result
