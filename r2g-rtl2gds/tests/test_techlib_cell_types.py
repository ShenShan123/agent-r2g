"""Tests for techlib.cell_types — the consolidated cell-type id mapping.

Behavioral equivalence to the original ``features/cell_type_map.py`` was proven during
the migration (Task 4) and is held by the byte-for-byte CSV gate
(tests/test_techlib_crossplatform.py). That oracle module was deleted in Task 9, so
these tests pin ``techlib.cell_types`` against KNOWN values:

  * Curated map preserved — UNKNOWN=95, INV_X1=0, DFF_X2=72, FAKERAM45_* keys upper-cased.
  * ``cell_type_id`` — curated hits, lowercase inputs, non-existent master -> UNKNOWN.
  * ``build_runtime_map`` determinism — two calls equal, UNKNOWN=N, macro/garbage->UNKNOWN.
  * ``resolve_cell_type_map`` strategy — nangate45 returns the curated dict; sky130hd builds runtime.
  * sky130 real masters resolve via the runtime map — differentiated ids (quote-bug fixed).

The no-file tests run unconditionally. Liberty-backed tests SKIP (never fail) when the
ORFS platforms directory is absent, so the suite runs cleanly on a bare checkout.

sky130 quote-bug — FIXED on this branch:
  sky130 liberty quotes cell names (``cell ("sky130_fd_sc_hd__...")``). Previously
  ``techlib.liberty._strip_name_token`` did not strip the surrounding ``"`` chars, so
  ``lib_db['cells']`` keys retained them and never matched the unquoted ``master.upper()``
  lookup — collapsing cell_area/power/cell_type_id to 0/UNKNOWN for every sky130 cell (a
  pre-existing bug, not introduced by the techlib migration). ``_strip_name_token`` now
  strips the quotes, so sky130 masters resolve to real, differentiated ids and non-zero
  area/power (asap7/gf180/ihp/nangate are unquoted, so the strip is a no-op there).
"""
from __future__ import annotations

import os

import pytest

from techlib import cell_types


# ---------------------------------------------------------------------------
# Path resolution helpers — ORFS root first, machine-local fallback.
# ---------------------------------------------------------------------------

def _platforms_dir() -> str | None:
    candidates: list[str] = []
    orfs_root = os.environ.get("ORFS_ROOT")
    if orfs_root:
        candidates.append(os.path.join(orfs_root, "flow", "platforms"))
    # Machine-local fallback; absent elsewhere -> tests SKIP, not fail.
    candidates.append("/proj/workarea/user5/OpenROAD-flow-scripts/flow/platforms")
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


def _sky130hd_lib() -> str | None:
    pdir = _platforms_dir()
    if not pdir:
        return None
    path = os.path.join(pdir, "sky130hd", "lib", "sky130_fd_sc_hd__tt_025C_1v80.lib")
    return path if os.path.isfile(path) else None


def _sky130hd_lib_or_skip() -> str:
    p = _sky130hd_lib()
    if not p:
        pytest.skip("sky130hd liberty absent (machine-local ORFS platforms)")
    return p


# ---------------------------------------------------------------------------
# 1. Curated map preserved (no files needed)
# ---------------------------------------------------------------------------

def test_curated_map_pinned_anchors():
    """The curated nangate45 map carries its known anchor ids.

    These are the durable id contract the feature dataset depends on (a reshuffle
    would silently relabel every nangate45 cell across the corpus).
    """
    m = cell_types.NANGATE45_CELL_TYPE_MAPPING
    assert m["INV_X1"] == 0
    assert m["DFF_X2"] == 72
    assert m["FAKERAM45_512X64"] == 113
    assert m["UNKNOWN"] == 95
    # A real curated map is large (>90 std-cell entries + macros).
    assert len(m) > 90, f"curated map shrank unexpectedly ({len(m)} entries)"


def test_unknown_is_95():
    """UNKNOWN = 95 in the curated map."""
    assert cell_types.NANGATE45_CELL_TYPE_MAPPING["UNKNOWN"] == 95


def test_fakeram45_keys_upper_cased():
    """FAKERAM45_* keys are upper-cased and present in the curated map."""
    expected_keys = [
        "FAKERAM45_512X64",
        "FAKERAM45_64X96",
        "FAKERAM45_256X32",
        "FAKERAM45_32X64",
        "FAKERAM45_64X32",
        "FAKERAM45_256X96",
        "FAKERAM45_64X15",
        "FAKERAM45_64X7",
    ]
    for key in expected_keys:
        assert key in cell_types.NANGATE45_CELL_TYPE_MAPPING, \
            f"FAKERAM45 key {key!r} missing from techlib.cell_types curated map"
        # Must be upper-cased (no lowercase variant present)
        assert key == key.upper(), f"Key {key!r} is not fully upper-cased"


def test_complete_cell_type_mapping_alias():
    """COMPLETE_CELL_TYPE_MAPPING is the same object as NANGATE45_CELL_TYPE_MAPPING."""
    assert cell_types.COMPLETE_CELL_TYPE_MAPPING is cell_types.NANGATE45_CELL_TYPE_MAPPING


# ---------------------------------------------------------------------------
# 2. cell_type_id equivalence (no files needed)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("master,expected", [
    # Curated hits
    ("INV_X1", 0),
    ("DFF_X2", 72),
    ("FAKERAM45_512X64", 113),
    ("UNKNOWN", 95),
    # Lowercase — must resolve after .strip().upper()
    ("inv_x1", 0),
    ("dff_x2", 72),
    ("fakeram45_512x64", 113),
    # Whitespace padding — must resolve after .strip().upper()
    ("  INV_X1  ", 0),
    # Non-existent master -> UNKNOWN = 95
    ("TOTALLY_NONEXISTENT_CELL", 95),
    # Empty / None -> UNKNOWN = 95
    ("", 95),
    (None, 95),
])
def test_cell_type_id_pinned(master, expected):
    """cell_type_id resolves to the KNOWN expected id (strip+upper normalization)."""
    m_new = cell_types.cell_type_id(master, cell_types.NANGATE45_CELL_TYPE_MAPPING)
    assert m_new == expected, \
        f"cell_type_id({master!r}): expected {expected}, got {m_new}"


# ---------------------------------------------------------------------------
# 3. build_runtime_map determinism + equivalence (needs sky130hd liberty)
# ---------------------------------------------------------------------------

def test_build_runtime_map_sc_none_deterministic():
    """build_runtime_map(db, sc=None) is deterministic; UNKNOWN == num cells."""
    lib = _sky130hd_lib_or_skip()
    from techlib import liberty
    db = liberty.load_liberty_db([lib])

    map_new_1 = cell_types.build_runtime_map(db, sc_lib_paths=None)
    map_new_2 = cell_types.build_runtime_map(db, sc_lib_paths=None)

    assert map_new_1 == map_new_2, "build_runtime_map is non-deterministic (sc=None)"

    # UNKNOWN must equal len(all cell names)
    cells = db.get("cells", {})
    n = len(cells)
    assert map_new_1["UNKNOWN"] == n, \
        f"UNKNOWN should be {n} (num cells), got {map_new_1['UNKNOWN']}"

    # Guard: non-empty lib => real entries
    assert n > 10, f"Suspiciously few cells ({n}) in sky130hd liberty"


def test_build_runtime_map_sc_set_deterministic():
    """build_runtime_map(db, sc_lib_paths=[lib]) is deterministic; UNKNOWN == sc count."""
    lib = _sky130hd_lib_or_skip()
    from techlib import liberty
    db = liberty.load_liberty_db([lib])

    map_new_1 = cell_types.build_runtime_map(db, sc_lib_paths=[lib])
    map_new_2 = cell_types.build_runtime_map(db, sc_lib_paths=[lib])

    assert map_new_1 == map_new_2, "build_runtime_map is non-deterministic (sc=[lib])"

    # UNKNOWN must equal len(sc-filtered names)
    cells = db.get("cells", {})
    sc_names = sorted(k for k, v in cells.items() if v.get("source_lib") == lib)
    n = len(sc_names)
    assert map_new_1["UNKNOWN"] == n, \
        f"UNKNOWN should be {n} (sc-filtered cells), got {map_new_1['UNKNOWN']}"

    # A macro/garbage name must resolve to UNKNOWN
    garbage_id = cell_types.cell_type_id("TOTALLY_MADE_UP_MACRO_XY", map_new_1)
    assert garbage_id == n, \
        f"Garbage master should map to UNKNOWN={n}, got {garbage_id}"


# ---------------------------------------------------------------------------
# 4. resolve_cell_type_map strategy (needs sky130hd liberty)
# ---------------------------------------------------------------------------

def test_resolve_cell_type_map_nangate45_returns_curated():
    """resolve_cell_type_map('nangate45', ...) returns the curated dict.

    This runs unconditionally (no ORFS liberty needed): the nangate45 branch
    short-circuits to the curated map and ignores the ``lib_db`` argument, so an
    empty-dict placeholder exercises the same code path.
    """
    result_new = cell_types.resolve_cell_type_map("nangate45", {})

    assert result_new is cell_types.NANGATE45_CELL_TYPE_MAPPING, \
        "resolve_cell_type_map('nangate45') should return the curated dict"


def test_resolve_cell_type_map_sky130hd_returns_runtime():
    """resolve_cell_type_map('sky130hd', db, sc) returns a runtime map (not the curated dict)."""
    lib = _sky130hd_lib_or_skip()
    from techlib import liberty
    db = liberty.load_liberty_db([lib])

    result_new = cell_types.resolve_cell_type_map("sky130hd", db, sc_lib_paths=[lib])

    # Must NOT be the curated nangate45 dict
    assert result_new is not cell_types.NANGATE45_CELL_TYPE_MAPPING, \
        "resolve_cell_type_map('sky130hd') must return a runtime map, not the curated dict"

    # The runtime map must equal build_runtime_map directly (runtime strategy).
    expected = cell_types.build_runtime_map(db, sc_lib_paths=[lib])
    assert result_new == expected


# ---------------------------------------------------------------------------
# 5. sky130 real masters resolve via the runtime map (quote-bug FIXED; needs sky130hd liberty)
# ---------------------------------------------------------------------------


def test_sky130_masters_resolve_via_runtime_map():
    """Real sky130 masters resolve to differentiated, non-UNKNOWN ids (quote-bug fixed).

    Masters are taken straight from the parsed liberty (now quote-free uppercase keys),
    so the test never guesses cell names that might be absent from a given corner lib.
    """
    lib = _sky130hd_lib_or_skip()
    from techlib import liberty
    db = liberty.load_liberty_db([lib])

    sc_map = cell_types.resolve_cell_type_map("sky130hd", db, sc_lib_paths=[lib])
    unknown = sc_map["UNKNOWN"]

    real = [k for k in db.get("cells", {}) if k.startswith("SKY130_FD_SC_HD__")][:8]
    assert real, "no sky130 standard cells parsed from the liberty"
    ids = {m: cell_types.cell_type_id(m, sc_map) for m in real}
    # Every real master must now resolve to a real id, not UNKNOWN (the quote-bug fix).
    assert all(cid != unknown for cid in ids.values()), \
        f"some real sky130 masters still resolve to UNKNOWN={unknown}: {ids}"
    # And the ids are differentiated (not all collapsed onto one bucket).
    assert len(set(ids.values())) > 1, f"expected differentiated cell_type_ids, got {ids}"


def test_sky130_runtime_map_keys_are_quote_free():
    """Quote-bug fix evidence: liberty cell keys carry no surrounding double-quotes."""
    lib = _sky130hd_lib_or_skip()
    from techlib import liberty
    db = liberty.load_liberty_db([lib])

    cells = db.get("cells", {})
    quoted = [k for k in cells if k.startswith('"') or k.endswith('"')]
    assert not quoted, f"liberty cell keys still carry surrounding quotes: {quoted[:5]}"
    # The unquoted, uppercased master form is now a real key in the runtime map.
    sc_map = cell_types.build_runtime_map(db, sc_lib_paths=[lib])
    assert "SKY130_FD_SC_HD__INV_1" in sc_map, \
        "unquoted sky130 master key missing from runtime map (quote-strip regression)"
