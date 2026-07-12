"""Regression tests for run_orfs.sh stage-timeout process-tree reaping.

failure-patterns #40 (2026-07-12): run_orfs.sh wrapped each ORFS stage in
`setsid timeout --signal=TERM --kill-after=60 $ORFS_TIMEOUT bash -c "<make> <stage>"`.
GNU `timeout` group-kills its whole child tree (fork the command into a NEW process
group, signal `-pgid`) ONLY when it is not already a process-group leader. The `setsid`
made timeout a session/group leader, so `setpgid(0,0)` failed and timeout fell back to
signaling only its direct `bash -c` child. On a stage that actually hit ORFS_TIMEOUT
(a 143K-cell design's ~2h KLayout DRC), the deep tool grandchild (klayout/openroad) was
orphaned (reparented to init) and KEPT RUNNING — holding the stdout pipe open so `tee`
never saw EOF, hanging run_orfs.sh and freezing the whole campaign for 6+ hours behind
one design. Fix: drop `setsid` so timeout becomes the new group's leader and reaps the
whole tree on expiry.

These tests (1) statically assert the fix stays applied, and (2) behaviorally prove
plain `timeout` reaps a grandchild tree while `setsid timeout` orphans it — the exact
mechanism. Grandchildren are CPU spinners (`yes`) with a unique marker so survivors are
countable and killable without matching the test's own processes.
"""
import os
import signal
import subprocess
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RUN_ORFS = REPO / "r2g-skills" / "signoff-loop" / "scripts" / "flow" / "run_orfs.sh"


def test_run_orfs_does_not_wrap_timeout_in_setsid():
    """Static guard: the stage runner must not re-introduce a `setsid timeout` COMMAND
    (which defeats timeout's group-kill and orphans deep tool subprocesses). Only actual
    command lines count — a comment mentioning it is fine."""
    import re
    code_lines = [
        ln for ln in RUN_ORFS.read_text().splitlines()
        if not ln.lstrip().startswith("#")
    ]
    offenders = [ln for ln in code_lines if re.match(r"\s*setsid\s+timeout\b", ln)]
    assert not offenders, (
        f"run_orfs.sh invokes `setsid timeout` again ({offenders}) — this defeats the "
        "process-group kill and re-opens the #40 orphaned-DRC hang"
    )
    # And it must still bound the stage with timeout + kill-after.
    assert "timeout --signal=TERM --kill-after=60" in RUN_ORFS.read_text()


def _count_and_reap(marker: str) -> int:
    # pgrep excludes its own PID; run via a list (no shell) so nothing else carries the marker.
    r = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True)
    pids = [int(x) for x in r.stdout.split() if x.strip()]
    for p in pids:
        try:
            os.kill(p, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    return len(pids)


def _run_stage_pattern(use_setsid: bool, marker: str) -> int:
    """Run the run_orfs.sh stage-timeout pattern (optionally with the old `setsid`) over a
    command that backgrounds two grandchild processes, then count survivors after the timeout
    fires. `yes {marker} >/dev/null` are cheap, findable, long-lived grandchildren of timeout."""
    inner = f"yes {marker} >/dev/null & yes {marker} >/dev/null & wait"
    cmd = ["timeout", "--signal=TERM", "--kill-after=1", "1", "bash", "-c", inner]
    if use_setsid:
        cmd = ["setsid"] + cmd
    subprocess.run(cmd, capture_output=True)  # blocks until timeout (~1s) + kill-after (~1s)
    survivors = 1
    for _ in range(30):  # poll up to 3s, reaping as we go so nothing leaks
        time.sleep(0.1)
        survivors = _count_and_reap(marker)
        if survivors == 0:
            break
    return survivors


def test_plain_timeout_reaps_the_whole_stage_tree():
    """The FIX property: plain `timeout` forks the command into a new process group and
    group-kills it, so no grandchild orphans and survives to hold the output pipe open (the
    #40 hang). A regression that re-wraps timeout so the tree is NOT reaped fails here."""
    marker = "R2G_REAP_FIX_MARKER_XZ42"
    _count_and_reap(marker)  # clean any leftovers
    survivors = _run_stage_pattern(use_setsid=False, marker=marker)
    assert survivors == 0, f"plain timeout left {survivors} orphaned grandchild(ren)"
