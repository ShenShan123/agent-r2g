#!/usr/bin/env python3
"""Inline recipe A/B planner + judge (engineer-loop spec §5.4).

plan_trial(): pick matched designs from run_violations history (same symptom,
decision-8 relaxation, CHEAPEST first — Phase-0 small-design-first), and define
the two arms. The ORCHESTRATOR executes arms as ordinary ledger entries with
distinct FLOW_VARIANT project dirs; this module never runs flows.

judge(): honest verdict — arm B must be a USABLE signed-off result AND better
(cheaper wall-clock, or equal-cost with fewer fix iters). Both-fail or crashed
arm -> inconclusive, NEVER a win (inherits eval_heuristics invariant 11).
"""
from __future__ import annotations

import datetime as _dt
import json

import recipe_lifecycle

N_DESIGNS_DEFAULT = 2     # min matched designs per trial (spec §5.4)


def _now() -> str:
    return _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def plan_trial(conn, *, symptom_id: str, design_class: str, platform: str,
               strategy: str, n_designs: int = N_DESIGNS_DEFAULT) -> dict | None:
    """Returns {designs, arm_a, arm_b, match_level} or None if no match."""
    def _q(extra_sql: str, params: tuple) -> list[dict]:
        cur = conn.execute(
            "SELECT r.design_name, r.project_path, r.cell_count "
            "FROM run_violations v JOIN runs r USING(run_id) "
            f"WHERE v.symptom_id=? {extra_sql} "
            "GROUP BY r.design_name ORDER BY MIN(r.cell_count)",
            (symptom_id, *params))
        return [dict(zip(("design_name", "project_path", "cell_count"), x))
                for x in cur.fetchall()]

    for extra, params, level in (
            ("AND r.design_class=? AND r.platform=?", (design_class, platform),
             "exact"),
            ("AND r.platform=?", (platform,), "pooled_class"),
            ("", (), "pooled_platform")):
        designs = _q(extra, params)
        if len(designs) >= n_designs:
            return {
                "designs": designs[:n_designs],
                "match_level": level,
                "arm_a": {"exclude_strategy": strategy},
                "arm_b": {"rank_first_strategy": strategy},
                "key": {"symptom_id": symptom_id, "design_class": design_class,
                        "platform": platform, "strategy": strategy},
            }
    return None


def judge(arm_a: dict | None, arm_b: dict | None) -> str:
    """arm dicts: {is_success: bool, wall_s: float|None, fix_iters: int|None}.
    None = the arm crashed / produced no judgeable result."""
    if arm_a is None or arm_b is None:
        return "inconclusive"
    if not arm_b.get("is_success"):
        return "inconclusive" if not arm_a.get("is_success") else "loss"
    if not arm_a.get("is_success"):
        return "win"                      # B usable where A was not
    wa, wb = arm_a.get("wall_s"), arm_b.get("wall_s")
    if wa is not None and wb is not None and wb < wa * 0.98:
        return "win"
    ia, ib = arm_a.get("fix_iters"), arm_b.get("fix_iters")
    if ia is not None and ib is not None and ib < ia:
        return "win"
    if wa is not None and wb is not None and wb > wa * 1.02:
        return "loss"
    return "inconclusive"


def record_trial(conn, *, key: dict, verdict: str, arm_a_run_id: str | None,
                 arm_b_run_id: str | None, metrics: dict,
                 match_level: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO ab_trials (symptom_id, design_class, platform, strategy, "
        "arm_a_run_id, arm_b_run_id, verdict, metrics_json, match_level, ts) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (key["symptom_id"], key["design_class"], key["platform"],
         key["strategy"], arm_a_run_id, arm_b_run_id, verdict,
         json.dumps(metrics, sort_keys=True), match_level, _now()))
    conn.commit()
    tid = cur.lastrowid
    if verdict == "win":
        recipe_lifecycle.promote(conn, evidence=f"ab_trial:{tid}", **key)
    else:
        recipe_lifecycle.demote(conn, reason=f"ab_{verdict}:{tid}", **key)
    return tid


def auto_demote_on_regression(conn, *, key: dict, window: int = 2) -> bool:
    """Spec §7: a PROMOTED recipe with `window` consecutive live regressions on
    its symptom is auto-demoted + escalated. Counts recent fix_events for this
    strategy+symptom; returns True if demoted."""
    rows = conn.execute(
        "SELECT verdict FROM fix_events WHERE symptom_id=? AND strategy=? "
        "ORDER BY fix_event_id DESC LIMIT ?",
        (key["symptom_id"], key["strategy"], window)).fetchall()
    if len(rows) == window and all(r[0] == "regression" for r in rows):
        recipe_lifecycle.demote(conn, reason="repeated_regression", **key)
        import escalations
        escalations.open_escalation(
            conn, design=f"recipe:{key['strategy']}", project_path="",
            run_id=None, reason="repeated_regression",
            symptom_id=key["symptom_id"],
            notes=json.dumps(key, sort_keys=True))
        return True
    return False
