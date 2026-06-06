"""check_timing.py --journal appends a timing fix_event line to fix_log.jsonl."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
CHECK_TIMING = SKILL / "scripts" / "reports" / "check_timing.py"


def test_journal_appends_timing_fix_event(tmp_path):
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    before = proj / "reports" / "before.json"
    after = proj / "reports" / "after.json"
    before.write_text(json.dumps({"tier": "moderate", "wns_ns": -3.0,
                                  "clock_period_ns": 10.0}))
    after.write_text(json.dumps({"tier": "clean", "wns_ns": 0.1,
                                 "clock_period_ns": 13.0}))
    subprocess.run(["python3", str(CHECK_TIMING), "--journal",
                    "--project", str(proj), "--before", str(before),
                    "--after", str(after), "--strategy", "period_relax"], check=True)
    rows = [json.loads(l) for l in (proj / "reports" / "fix_log.jsonl").read_text().splitlines() if l.strip()]
    assert len(rows) == 1
    r = rows[0]
    assert r["check"] == "timing" and r["strategy"] == "period_relax"
    assert r["violation_class"] == "moderate"     # the before tier
    assert r["verdict"] == "cleared"              # after tier == clean
    assert r["fix_session_id"]
