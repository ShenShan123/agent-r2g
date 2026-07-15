"""Decision-8 recipe projection + monotonic generation counter."""
import json

import knowledge_db
import learn_heuristics


def _seed(conn, *, design_class="crypto/small", platform="nangate45",
          sid="s1", strategy="antenna_diode_repair", verdict="cleared"):
    conn.execute(
        "INSERT OR REPLACE INTO runs (run_id, project_path, design_name, "
        "design_family, platform, ingested_at, design_class) "
        "VALUES (?,?,?,?,?,?,?)",
        (f"r_{sid}", f"/p/{sid}", f"d_{sid}", "fam", platform,
         "2026-06-10T00:00:00Z", design_class))
    conn.execute(
        "INSERT OR IGNORE INTO fix_events (fix_session_id, project_path, "
        "design_name, platform, check_type, violation_class, iter, strategy, "
        "before_count, after_count, verdict, ts, provenance, symptom_id, "
        "signature_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, f"/p/{sid}", f"d_{sid}", platform, "drc", "antenna", 1, strategy,
         5, 0 if verdict == "cleared" else 5, verdict,
         "2026-06-10T00:00:00Z", "live", "deadbeef00000001",
         json.dumps({"check": "drc", "class": "antenna", "predicates": {}})))
    conn.commit()


def test_recipes_keyed_symptom_class_platform(tmp_path):
    db = tmp_path / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    _seed(conn)
    data = learn_heuristics.learn(db, tmp_path / "heuristics.json")
    node = data["recipes"]["deadbeef00000001"]["crypto/small"]["nangate45"]
    assert node["strategies"]["antenna_diode_repair"]["successes"] == 1


def test_star_rollups_pool_across_class_and_platform(tmp_path):
    db = tmp_path / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    _seed(conn, design_class="crypto/small", platform="nangate45", sid="s1")
    _seed(conn, design_class="logic/medium", platform="sky130hd", sid="s2")
    data = learn_heuristics.learn(db, tmp_path / "heuristics.json")
    bucket = data["recipes"]["deadbeef00000001"]
    # class rollup pools both classes for one platform-agnostic view
    assert bucket["*"]["*"]["strategies"]["antenna_diode_repair"]["attempts"] == 2
    assert bucket["crypto/small"]["*"]["strategies"][
        "antenna_diode_repair"]["attempts"] == 1


def test_learn_rosters_every_recipe_key(tmp_path):
    """P0-2 (failure-patterns #48): after learn(), EVERY concrete recipe key in the
    emitted heuristics has a recipe_status row (diff_and_enqueue + ensure_rostered), so
    filter_promoted's fail-closed default never strips a learned recipe merely because
    its lifecycle enqueue was skipped."""
    import recipe_lifecycle
    db = tmp_path / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    _seed(conn, sid="s1")
    _seed(conn, sid="s2", design_class="logic/medium", platform="sky130hd")
    data = learn_heuristics.learn(db, tmp_path / "heuristics.json")
    conn2 = knowledge_db.connect(db)
    try:
        assert recipe_lifecycle.unrostered_keys(conn2, data) == []
    finally:
        conn2.close()


def test_mean_outcome_score_is_deterministic_latest_run(tmp_path):
    """P1-1 (failure-patterns #48): two scored runs on the SAME project_path must collapse
    to the LATEST-ingested run's score deterministically — not whichever row SQLite
    happened to return last. The two runs are inserted in BOTH orders; mean_outcome_score
    is the later run's score (0.9) either way (the old bare-dict projection flipped it to
    the last-inserted 0.9 vs 0.1)."""
    def _mean_for(order):
        db = tmp_path / f"k_{order}.sqlite"
        conn = knowledge_db.connect(db)
        knowledge_db.ensure_schema(conn)
        pp = "/p/dup"
        runs = [("rA", "2026-06-10T00:00:00Z", 0.1),    # earlier
                ("rB", "2026-06-11T00:00:00Z", 0.9)]    # later -> canonical
        if order == "reversed":
            runs = list(reversed(runs))
        for rid, ing, score in runs:
            conn.execute(
                "INSERT INTO runs (run_id, project_path, design_name, design_family, "
                "platform, ingested_at, design_class, outcome_score) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (rid, pp, "d", "fam", "nangate45", ing, "crypto/small", score))
        conn.execute(
            "INSERT INTO fix_events (fix_session_id, project_path, design_name, platform, "
            "check_type, violation_class, iter, strategy, before_count, after_count, "
            "verdict, ts, provenance, symptom_id, signature_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("sess1", pp, "d", "nangate45", "drc", "antenna", 1, "antenna_diode_repair",
             5, 0, "cleared", "2026-06-11T00:00:00Z", "live", "deadbeef00000001",
             json.dumps({"check": "drc", "class": "antenna", "predicates": {}})))
        conn.commit()
        data = learn_heuristics.learn(db, tmp_path / f"h_{order}.json")
        node = data["recipes"]["deadbeef00000001"]["crypto/small"]["nangate45"]
        return node["strategies"]["antenna_diode_repair"]["mean_outcome_score"]

    assert _mean_for("forward") == 0.9
    assert _mean_for("reversed") == 0.9      # order-independent (was 0.1 under the bug)


def test_generation_increments_monotonically(tmp_path):
    db = tmp_path / "knowledge.sqlite"
    conn = knowledge_db.connect(db)
    knowledge_db.ensure_schema(conn)
    _seed(conn)
    d1 = learn_heuristics.learn(db, tmp_path / "heuristics.json")
    d2 = learn_heuristics.learn(db, tmp_path / "heuristics.json")
    assert d2["generation"] == d1["generation"] + 1
