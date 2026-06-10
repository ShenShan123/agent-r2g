"""Flow scripts journal commands/summaries/bugs into the journal DB (spec §5.2)."""
import json
import os
import subprocess
from pathlib import Path

import journal_db

SKILL = Path(__file__).resolve().parents[1]


def test_run_orfs_stage_journals_action_and_summary(tmp_path):
    """_journal_stage <stage> <status> <elapsed> <log> appends a tool_invoke
    action + a log summary; on failure also a tool_bugs row."""
    db = tmp_path / "journal.sqlite"
    proj = tmp_path / "proj"
    (proj / "backend").mkdir(parents=True)
    log = proj / "backend" / "5_route.log"
    log.write_text("[ERROR DRT-0085] cannot fix\nSignal 11 received\n")
    env = dict(os.environ, R2G_JOURNAL_DB=str(db))
    r = subprocess.run(
        ["bash", "-c",
         f'R2G_SOURCE_ONLY=1 source "{SKILL}/scripts/flow/run_orfs.sh"; '
         f'PROJECT_DIR="{proj}"; PLATFORM=nangate45; '
         f'_journal_stage route fail 42 "{log}"'],
        capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    c = journal_db.connect(db)
    act = c.execute("SELECT action_type, payload_json FROM actions").fetchone()
    assert act[0] == "tool_invoke"
    assert json.loads(act[1])["stage"] == "route"
    assert c.execute("SELECT COUNT(*) FROM log_summaries").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM tool_bugs").fetchone()[0] == 1


def test_fix_signoff_journals_each_knob_delta(tmp_path):
    """fix_signoff.sh's _journal_knob_deltas splits a config_edits dict into one
    config_knob_delta action per knob (spec: each knob INDIVIDUALLY)."""
    db = tmp_path / "journal.sqlite"
    proj = tmp_path / "proj"
    proj.mkdir()
    env = dict(os.environ, R2G_JOURNAL_DB=str(db))
    edits = json.dumps({"SKIP_ANTENNA_REPAIR": "1",
                        "MAX_REPAIR_ANTENNAS_ITER_DRT": "10"})
    r = subprocess.run(
        ["bash", "-c",
         f'R2G_SOURCE_ONLY=1 source "{SKILL}/scripts/flow/fix_signoff.sh"; '
         f'PROJECT_DIR="{proj}"; FIX_SESSION_ID=abcd1234abcd1234; '
         f"_journal_knob_deltas '{edits}' antenna_diode_repair"],
        capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    c = journal_db.connect(db)
    rows = c.execute("SELECT action_type, fix_session_id, payload_json "
                     "FROM actions ORDER BY action_id").fetchall()
    assert len(rows) == 2
    assert all(t == "config_knob_delta" for t, _, _ in rows)
    assert all(s == "abcd1234abcd1234" for _, s, _ in rows)
    knobs = {json.loads(p)["knob"] for _, _, p in rows}
    assert knobs == {"SKIP_ANTENNA_REPAIR", "MAX_REPAIR_ANTENNAS_ITER_DRT"}
