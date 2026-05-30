"""Tests for techlib.liberty — verbatim copy of features/lib_db.py (Task 3).

Proves that ``techlib.liberty`` is behaviorally identical to the untouched
``lib_db`` oracle and that the documented behaviors hold:

  * DB equivalence on nangate45 + sky130hd (full dict ==).
  * Classifier/getter equivalence (get_cell_area, get_cell_power, get_pin_cap_fF,
    get_pin_direction, classify_pin_type, direction_id, infer_net_type_id,
    is_tap_master) with guards against vacuous passes.
  * .lib.gz decompression works on asap7 + gf180 (non-empty cells dict, equals
    lib_db).
  * tap patterns: with R2G_PLATFORM=gf180, is_tap_master recognises gf180-style
    names and matches lib_db.
  * "no liberty" warning: load_liberty_db([]) emits the WARN to stderr and
    returns a DB with empty sources['lib'] / cells — matching lib_db.

Tech lib paths are resolved from $ORFS_ROOT first, then the literal machine-local
fallback below. Tests SKIP (never fail) when the file is absent, so the suite runs
on a bare checkout.
"""
from __future__ import annotations

import glob
import os

import pytest

from techlib import liberty

# Untouched oracle — imported as a plain top-level module via the FEATURES_DIR
# sys.path entry that conftest.py installs.
import lib_db


# --------------------------------------------------------------------------- #
# Path resolution — ORFS root first, machine-local fallback.                  #
# --------------------------------------------------------------------------- #
def _platforms_dir() -> str | None:
    """Return the ORFS platforms directory, or None if not present.

    Prefers $ORFS_ROOT/flow/platforms; falls back to the literal path below
    which is machine-local. Returns None when neither exists — tests SKIP,
    not fail.
    """
    candidates: list[str] = []
    orfs_root = os.environ.get("ORFS_ROOT")
    if orfs_root:
        candidates.append(os.path.join(orfs_root, "flow", "platforms"))
    # Machine-local fallback for this dev box; absent elsewhere -> tests SKIP, not fail.
    candidates.append("/proj/workarea/user5/OpenROAD-flow-scripts/flow/platforms")
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


def _lib_path(platform: str) -> str | None:
    """Return the primary liberty path for a platform, or None if absent."""
    pdir = _platforms_dir()
    if not pdir:
        return None
    literal: dict[str, str] = {
        "nangate45": "nangate45/lib/NangateOpenCellLibrary_typical.lib",
        "sky130hd": "sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib",
    }
    if platform in literal:
        path = os.path.join(pdir, literal[platform])
        return path if os.path.isfile(path) else None
    return None


def _gz_lib_path(platform: str) -> str | None:
    """Return a single .lib.gz path for asap7 or gf180, or None if absent.

    Picks deterministically (sorted-first) so tests are reproducible.
    """
    pdir = _platforms_dir()
    if not pdir:
        return None
    if platform == "asap7":
        # prefer NLDM TT corner
        matches = sorted(glob.glob(os.path.join(pdir, "asap7", "lib", "NLDM", "*_TT_*.lib.gz")))
        if not matches:
            matches = sorted(glob.glob(os.path.join(pdir, "asap7", "lib", "**", "*.lib.gz"),
                                        recursive=True))
        return matches[0] if matches else None
    if platform == "gf180":
        # prefer tt 5v00 corner; fall back to any
        matches = sorted(glob.glob(os.path.join(pdir, "gf180", "lib", "*tt*5v00*.lib.gz")))
        if not matches:
            matches = sorted(glob.glob(os.path.join(pdir, "gf180", "lib", "*.lib.gz")))
        return matches[0] if matches else None
    return None


def _lib_or_skip(platform: str) -> str:
    path = _lib_path(platform)
    if not path:
        pytest.skip(f"liberty absent for {platform} (machine-local ORFS platforms)")
    return path


def _gz_or_skip(platform: str) -> str:
    path = _gz_lib_path(platform)
    if not path:
        pytest.skip(f".lib.gz absent for {platform} (machine-local ORFS platforms)")
    return path


# --------------------------------------------------------------------------- #
# DB equivalence — full dict comparison.                                       #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("platform", ["nangate45", "sky130hd"])
def test_db_equivalence(platform):
    """liberty.load_liberty_db([path]) == lib_db.load_liberty_db([path]) exactly."""
    path = _lib_or_skip(platform)
    db_new = liberty.load_liberty_db([path])
    db_old = lib_db.load_liberty_db([path])
    assert db_new == db_old, f"{platform}: full DB dict differs between techlib.liberty and lib_db"
    # Guard: a real std-cell lib must have many cells.
    assert len(db_new["cells"]) > 10, f"{platform}: suspiciously few cells ({len(db_new['cells'])})"


# --------------------------------------------------------------------------- #
# Classifier / getter equivalence.                                             #
# --------------------------------------------------------------------------- #
def _sample_cells_pins(db: dict, n_cells: int = 10) -> list[tuple[str, str]]:
    """Return up to n_cells (cell_name, pin_name) pairs from a liberty DB."""
    result: list[tuple[str, str]] = []
    for _key, cell in list(db["cells"].items())[:n_cells]:
        cname = cell["name"]
        for pin_name in list(cell["pins"].keys())[:3]:
            result.append((cname, pin_name))
    return result


@pytest.mark.parametrize("platform", ["nangate45", "sky130hd"])
def test_getters_equivalence(platform):
    """get_cell_area/power and get_pin_cap_fF/direction match between modules."""
    path = _lib_or_skip(platform)
    db_new = liberty.load_liberty_db([path])
    db_old = lib_db.load_liberty_db([path])

    samples = _sample_cells_pins(db_old)
    assert len(samples) > 0, "No cells sampled from the oracle DB"

    for cname, pname in samples:
        assert liberty.get_cell_area(cname, db_new) == lib_db.get_cell_area(cname, db_old), \
            f"get_cell_area mismatch for {cname}"
        assert liberty.get_cell_power(cname, db_new) == lib_db.get_cell_power(cname, db_old), \
            f"get_cell_power mismatch for {cname}"
        assert liberty.get_pin_cap_fF(cname, pname, db_new) == lib_db.get_pin_cap_fF(cname, pname, db_old), \
            f"get_pin_cap_fF mismatch for {cname}/{pname}"
        assert liberty.get_pin_direction(cname, pname, db_new) == lib_db.get_pin_direction(cname, pname, db_old), \
            f"get_pin_direction mismatch for {cname}/{pname}"
        assert liberty.classify_pin_type(cname, pname, db_new) == lib_db.classify_pin_type(cname, pname, db_old), \
            f"classify_pin_type mismatch for {cname}/{pname}"

    # Guard: real std-cell areas must be positive.
    first_cell = list(db_old["cells"].values())[0]["name"]
    area = liberty.get_cell_area(first_cell, db_new)
    assert area > 0.0, f"First cell {first_cell!r} has zero area — lib parse likely failed"


def test_classifiers_equivalence():
    """direction_id, infer_net_type_id, is_tap_master match between modules.

    These are pure-logic classifiers (no liberty file needed), so this test runs
    unconditionally — even on a bare checkout without ORFS platforms.
    """
    # direction_id — exhaustive on valid values
    for s in ["INPUT", "OUTPUT", "INOUT", "FEEDTHRU", "input", "output", "", None, "UNKNOWN"]:
        assert liberty.direction_id(s) == lib_db.direction_id(s), f"direction_id mismatch for {s!r}"

    # infer_net_type_id — representative samples
    cases = [
        ("VDD", "POWER", False),
        ("VSS", "GROUND", False),
        ("clk", "", False),
        ("clk_core", "", True),
        ("reset_n", "", False),
        ("scan_en", "", False),
        ("data_out", "", False),
        ("", "", False),
    ]
    for net_name, net_use, is_clock in cases:
        assert liberty.infer_net_type_id(net_name, net_use, is_clock) == \
               lib_db.infer_net_type_id(net_name, net_use, is_clock), \
               f"infer_net_type_id mismatch for ({net_name!r}, {net_use!r}, {is_clock})"

    # is_tap_master — without platform env override (relies on "TAP" pattern)
    tap_names = ["TAPCELL_X1", "sky130_fd_sc_hd__tapvpwrvgnd_1", "TAPCELL_ASAP7_75t_L"]
    non_tap_names = ["INV_X1", "DFF_X1", "AND2_X1"]
    for name in tap_names:
        assert liberty.is_tap_master(name) == lib_db.is_tap_master(name), \
               f"is_tap_master mismatch for tap cell {name!r}"
        assert liberty.is_tap_master(name) is True, \
               f"Expected {name!r} to be recognised as tap cell"
    for name in non_tap_names:
        assert liberty.is_tap_master(name) == lib_db.is_tap_master(name), \
               f"is_tap_master mismatch for non-tap cell {name!r}"
        # Symmetric negative: a non-tap master must be False in BOTH modules, so a
        # both-True regression in the tap-pattern list can't slip through.
        assert liberty.is_tap_master(name) is False, \
               f"Expected {name!r} NOT to be recognised as tap cell"


# --------------------------------------------------------------------------- #
# .lib.gz decompression (asap7 + gf180).                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("platform", ["asap7", "gf180"])
def test_gz_lib_parses(platform):
    """.lib.gz loads to a non-empty cells dict and equals lib_db."""
    path = _gz_or_skip(platform)
    db_new = liberty.load_liberty_db([path])
    db_old = lib_db.load_liberty_db([path])
    assert db_new == db_old, f"{platform}: .lib.gz DB differs between modules"
    # asap7/gf180 are full std-cell libs; >10 catches a truncated/partial decompress.
    assert len(db_new["cells"]) > 10, \
        f"{platform}: .lib.gz parse returned only {len(db_new['cells'])} cells (truncated?)"
    # Guard: sources must be populated
    assert path in db_new["sources"]["lib"], f"{platform}: .lib.gz path missing from sources"


# --------------------------------------------------------------------------- #
# Tap-pattern: gf180 FILLTIE / ENDCAP recognition.                            #
# --------------------------------------------------------------------------- #
def test_tap_patterns_gf180(monkeypatch):
    """With R2G_PLATFORM=gf180, FILLTIE/ENDCAP names are recognised as tap masters."""
    monkeypatch.setenv("R2G_PLATFORM", "gf180")
    gf180_tap_names = [
        "gf180mcu_fd_sc_mcu7t5v0__filltie",
        "gf180mcu_fd_sc_mcu7t5v0__endcap",
        "FILLTIE_X1",
        "ENDCAP_EDGE",
    ]
    for name in gf180_tap_names:
        result_new = liberty.is_tap_master(name)
        result_old = lib_db.is_tap_master(name)
        assert result_new == result_old, \
               f"is_tap_master mismatch for gf180 name {name!r} (new={result_new}, old={result_old})"
        assert result_new is True, \
               f"Expected gf180 name {name!r} to be recognised as tap master"


# --------------------------------------------------------------------------- #
# "no liberty" warning emitted + empty DB returned.                            #
# --------------------------------------------------------------------------- #
def test_no_liberty_warning(capsys):
    """load_liberty_db([]) emits the WARN to stderr and returns an empty DB."""
    db_new = liberty.load_liberty_db([])
    captured = capsys.readouterr()
    assert "WARN" in captured.err, f"Expected WARN on stderr, got: {captured.err!r}"
    assert db_new["sources"]["lib"] == [], \
           f"sources['lib'] should be empty, got {db_new['sources']['lib']!r}"
    assert db_new["cells"] == {}, \
           f"cells should be empty, got {db_new['cells']!r}"

    # Matches lib_db exactly.
    db_old = lib_db.load_liberty_db([])
    assert db_new == db_old, "load_liberty_db([]) result differs from lib_db"


def test_no_liberty_warning_none_input(capsys, monkeypatch):
    """load_liberty_db() with no args (fallback to empty env var) emits WARN."""
    monkeypatch.delenv("R2G_LIB_FILES", raising=False)
    db_new = liberty.load_liberty_db()
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert db_new["cells"] == {}
