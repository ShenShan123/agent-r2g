#!/usr/bin/env python3
"""Reconcile dead orfs_status='partial' rows from their per-project stage logs.

Most historical `runs` rows carry orfs_status='partial' only because their
backend/RUN_*/stage_log.jsonl was incomplete at ingest time. This one-time
pass re-reads each project's *latest* stage log and re-derives the status with
the very same helpers ingest_run uses (`_read_stage_log` + `_derive_orfs_status`),
then UPDATEs the row only when the freshly-derived value differs.

It also keeps the `failure_events` projection consistent with the reconciled
status: the live ingest path emits an `orfs-fail-<stage>` event for every 'fail'
run, but a direct orfs_status UPDATE bypasses that, so without this the learner /
escalation / search_failures (which read `failure_events`, not `runs.orfs_status`)
stay blind to a reconciled failure. See `_reconcile_orfs_failure_event`.

Properties:
  * Read-from-stage-log only — never invents a status; uses the faithful
    ingest_run derivation, so the corpus the learner sees matches reality.
  * Idempotent — re-running changes nothing once rows (and their failure_events)
    are reconciled.
  * Reversible — main() copies the DB to <db>.bak (shutil.copy2) before writing
    and prints a before/after orfs_status histogram.

Usage:
  repair_run_status.py --db knowledge/knowledge.sqlite [--cases-root design_cases]
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

import ingest_run
import knowledge_db


def _find_latest_stage_log(project: Path) -> Optional[Path]:
    """Locate the most-recently-modified backend/RUN_*/stage_log.jsonl, falling
    back to the legacy flat backend/stage_log.jsonl — mirrors ingest_run."""
    backend = project / "backend"
    if backend.is_dir():
        run_dirs = sorted(
            (d for d in backend.iterdir()
             if d.is_dir() and d.name.startswith("RUN_")),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        for rd in run_dirs:
            candidate = rd / "stage_log.jsonl"
            if candidate.exists():
                return candidate
    legacy = backend / "stage_log.jsonl"
    if legacy.exists():
        return legacy
    return None


def _resolve_project(project_path: Optional[str], cases_root: Path) -> Optional[Path]:
    """Find the project dir for a runs row.

    Prefer the stored absolute project_path; if it has since moved, fall back to
    <cases_root>/<basename>. Returns None when neither exists.
    """
    if project_path:
        p = Path(project_path)
        if p.is_dir():
            return p
        relocated = cases_root / p.name
        if relocated.is_dir():
            return relocated
    return None


def _reconcile_orfs_failure_event(
    conn: sqlite3.Connection,
    run_id: str,
    status: str | None,
    fail_stage: str | None,
    run_dir: Path | None,
) -> None:
    """Make the run's `orfs-fail-<stage>` failure_event match its orfs_status.

    The live ingest path (ingest_run.py) inserts this event whenever a run is
    'fail'; the learner, escalation drain, and search_failures all read the
    `failure_events` table, NOT `runs.orfs_status`. So a status reconciled here
    must carry the same event or the failure stays invisible — the dual-write
    consistency bug this function closes.

    Idempotent and scoped: it owns only `orfs-fail-%` signatures (diagnosis
    events such as `synthesis_errors` for the same run are untouched). When a
    RUN dir is available, the tool's `[ERROR XXX-0000]` code is folded into the
    signature and the line into the detail, exactly as ingest does; when the
    project has since moved away (historical rows) the bare `orfs-fail-<stage>`
    event is still backfilled from the columns, with a null detail (honest — we
    no longer have the flow.log).
    """
    conn.row_factory = sqlite3.Row
    existing = conn.execute(
        "SELECT id, signature FROM failure_events "
        "WHERE run_id = ? AND signature LIKE 'orfs-fail-%'",
        (run_id,),
    ).fetchall()

    if status == "fail" and fail_stage:
        err_code, err_line = ingest_run._orfs_fail_detail(run_dir)
        sig = f"orfs-fail-{fail_stage}" + (f"-{err_code}" if err_code else "")
        # Already the right single event -> leave it (preserves any detail).
        if len(existing) == 1 and existing[0]["signature"] == sig:
            return
        for ev in existing:
            conn.execute("DELETE FROM failure_events WHERE id = ?", (ev["id"],))
        conn.execute(
            "INSERT INTO failure_events (run_id, stage, signature, detail) "
            "VALUES (?, ?, ?, ?)",
            (run_id, fail_stage, sig, err_line),
        )
    else:
        # No longer a backend failure -> drop any stale orfs-fail event.
        for ev in existing:
            conn.execute("DELETE FROM failure_events WHERE id = ?", (ev["id"],))


def repair(cases_root: Path | str, conn: sqlite3.Connection) -> int:
    """Re-derive orfs_status for every runs row from its latest stage log and
    keep the `failure_events` projection consistent with it.

    Returns the number of rows whose orfs_status (or orfs_fail_stage) changed.
    The failure_events reconciliation runs for *every* row regardless of whether
    the status changed this pass, so rows flipped to 'fail' by an earlier repair
    (before failure_events were maintained here) get their event backfilled.
    """
    cases_root = Path(cases_root)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT run_id, project_path, orfs_status, orfs_fail_stage FROM runs"
    ).fetchall()

    changed = 0
    for row in rows:
        run_id = row["run_id"]
        status = row["orfs_status"]
        fail_stage = row["orfs_fail_stage"]
        run_dir: Path | None = None

        project = _resolve_project(row["project_path"], cases_root)
        if project is not None:
            stage_log_path = _find_latest_stage_log(project)
            if stage_log_path is not None:
                run_dir = stage_log_path.parent  # RUN_* dir holds flow.log
                stages = ingest_run._read_stage_log(stage_log_path)
                new_status, new_fail_stage = ingest_run._derive_orfs_status(stages)
                # 'unknown' means the stage log was empty/unparseable — never
                # downgrade a row to it; the original value is at least as
                # informative.
                if new_status != "unknown" and (
                    new_status != status or new_fail_stage != fail_stage
                ):
                    conn.execute(
                        "UPDATE runs SET orfs_status = ?, orfs_fail_stage = ? "
                        "WHERE run_id = ?",
                        (new_status, new_fail_stage, run_id),
                    )
                    status, fail_stage = new_status, new_fail_stage
                    changed += 1

        # Keep failure_events in lock-step with the row's (possibly updated)
        # status — backfills the historical fails an earlier repair left blind.
        _reconcile_orfs_failure_event(conn, run_id, status, fail_stage, run_dir)

    conn.commit()
    return changed


def _status_histogram(conn: sqlite3.Connection) -> Counter:
    return Counter(
        (r[0] if r[0] is not None else "NULL")
        for r in conn.execute("SELECT orfs_status FROM runs")
    )


def _format_histogram(hist: Counter) -> str:
    lines = []
    for status, n in sorted(hist.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"  {status:<10} {n}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Repair dead orfs_status='partial' runs rows from stage logs.")
    parser.add_argument("--db", required=True,
                        help="Path to knowledge/knowledge.sqlite.")
    parser.add_argument("--cases-root", default="design_cases",
                        help="Root holding the project dirs (relocation fallback).")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"error: DB not found: {db_path}", file=sys.stderr)
        return 1

    # Reversible: back up before we touch a single row.
    bak_path = Path(str(db_path) + ".bak")
    shutil.copy2(db_path, bak_path)
    print(f"backed up {db_path} -> {bak_path}")

    # busy_timeout-armed connection (knowledge_db.connect) so the tool tolerates a
    # transient lock from a concurrent ingest instead of aborting mid-reconcile.
    conn = knowledge_db.connect(db_path)
    try:
        before = _status_histogram(conn)
        print("orfs_status (before):")
        print(_format_histogram(before))

        changed = repair(args.cases_root, conn)

        after = _status_histogram(conn)
        print(f"\nrepaired {changed} row(s).")
        print("orfs_status (after):")
        print(_format_histogram(after))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
