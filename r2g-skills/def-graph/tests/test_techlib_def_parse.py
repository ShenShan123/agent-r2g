"""Tests for techlib.def_parse — the consolidated DEF/SDC parser (Task 1).

The load-bearing part is the ``route_segments`` iterator: it is the dedup target
for the ``*``-relative coordinate-chain walk that the wirelength extractor
(``parse_def_wirelength``) and the congestion extractor (``extract_grid_demand``)
currently re-implement independently. These tests:

  1. Pin the synthetic ``*``-relative / trailing-token / single-point / leading-``*``
     edge cases directly.
  2. Prove byte-for-result CORRESPONDENCE on two REAL DEFs, one per platform family
     (aescore sky130 + cordic nangate45 — the parsers are platform-sensitive):
     per-net Manhattan wirelength recomputed via ``route_segments`` must equal
     ``parse_def_wirelength`` exactly, and the per-route-line segment sequence must
     equal what congestion's regex+walk produces (copied inline here — the extractor
     itself is NOT modified).

The real-DEF tests RESOLVE their inputs (see ``_resolve_def``) rather than pinning a
timestamped campaign run, and skip only when neither a machine-local reference DEF nor
any built design_cases/ run exists — so the suite still runs on a bare checkout without
going silently inert on a machine that has plenty of DEFs.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from techlib import def_parse

# Untouched wirelength extractor (imported via the LABELS_DIR sys.path entry in
# conftest) — the correspondence oracle for route_segments.
import extract_wirelength as ewl


REPO_ROOT = Path(__file__).resolve().parents[3]

# Reference DEFs must be RESOLVED, never hardcoded to a timestamped run dir.
# A backend RUN_<timestamp>/ is campaign output: it is wiped, re-run and re-dated
# constantly, so a pinned path rots into a permanent `pytest.skip` and the guard goes
# inert with the suite still green. That is exactly what happened here -- both pins
# (aes_core RUN_2026-04-12, cordic RUN_2026-05-17) had been deleted, so all four
# real-DEF correspondence tests silently skipped on a machine carrying 3858 usable
# 6_final.def files (2026-07-19 /r2g-debug Step 5c). "Never trust a SKIP as a pass."
#
# Resolution order:
#   1. the purpose-built machine-local reference DEFs (stable, one per platform family,
#      not campaign output -- these are the Step 5c anchors)
#   2. any built design_cases/ run, newest first (final/ is the modern layout, results/
#      the legacy one)
# Only a checkout with NEITHER skips.
_VERIFY_DIR = Path("/proj/workarea/user5/rtl2graph_verify")


def _newest_campaign_def():
    """Newest design_cases/ 6_final.def, or None. Sorted by mtime, not by name:
    RUN_ dirs are timestamped but designs are not comparable lexically."""
    cands = [*REPO_ROOT.glob("design_cases/*/backend/RUN_*/final/6_final.def"),
             *REPO_ROOT.glob("design_cases/*/backend/RUN_*/results/6_final.def")]
    cands = [p for p in cands if p.is_file()]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def _resolve_def(reference_name):
    ref = _VERIFY_DIR / reference_name
    return ref if ref.is_file() else _newest_campaign_def()


# cordic_ng45 = nangate45, aescore_sky = sky130 -- two platform families, because the
# parsers are platform-sensitive (layer names, PITCH direction, name escaping).
AES_DEF = _resolve_def("aescore_sky_5_route.def")
CORDIC_DEF = _resolve_def("cordic_ng45_5_route.def")


# --------------------------------------------------------------------------- #
# Synthetic *-relative edge cases.                                            #
# --------------------------------------------------------------------------- #
def test_simple_two_point_chain():
    segs = list(def_parse.route_segments("+ ROUTED met1 ( 100 200 ) ( 300 200 )"))
    assert segs == [(100, 200, 300, 200)]


def test_star_carries_previous_coordinate():
    # ( * 400 ) keeps x=100; ( 500 * ) keeps y=400.
    segs = list(def_parse.route_segments("+ ROUTED met1 ( 100 200 ) ( * 400 ) ( 500 * )"))
    assert segs == [
        (100, 200, 100, 400),
        (100, 400, 500, 400),
    ]


def test_multi_segment_chain():
    line = "NEW met2 ( 0 0 ) ( 0 1000 ) ( 2000 1000 ) ( 2000 3000 )"
    segs = list(def_parse.route_segments(line))
    assert segs == [
        (0, 0, 0, 1000),
        (0, 1000, 2000, 1000),
        (2000, 1000, 2000, 3000),
    ]


def test_trailing_via_or_layer_token_ignored():
    # The 3rd token inside ( ... ) (a via name) must be ignored; only x,y used.
    line = "+ ROUTED met1 ( 100 200 ) ( 300 200 via12_0 ) ( * 600 M2_M1_via )"
    segs = list(def_parse.route_segments(line))
    assert segs == [
        (100, 200, 300, 200),
        (300, 200, 300, 600),
    ]


def test_single_point_yields_nothing():
    assert list(def_parse.route_segments("+ ROUTED met1 ( 100 200 )")) == []


def test_no_points_yields_nothing():
    assert list(def_parse.route_segments("+ ROUTED met1")) == []


def test_leading_star_chain_skipped():
    # First point is ( * 400 ): wirelength's int('*') raises -> whole line skipped.
    assert list(def_parse.route_segments("+ ROUTED met1 ( * 400 ) ( 500 600 )")) == []
    assert list(def_parse.route_segments("+ ROUTED met1 ( 100 * ) ( 500 600 )")) == []


def test_mid_chain_bad_token_carries_previous_wirelength_semantics():
    """A non-``*`` token that fails int() carries the previous coord forward.

    Pins the chosen (wirelength) semantics on the ONE point where the two
    originals diverge: wirelength does ``try: int(...) except ValueError: pass``
    (carry previous, still emit a segment), whereas congestion's
    extract_grid_demand ``continue``s (drops the point, advances). route_segments
    follows wirelength. The bad x-token here keeps x=100; only y advances.
    """
    line = "+ ROUTED met1 ( 100 200 ) ( BOGUS 400 ) ( 700 400 )"
    segs = list(def_parse.route_segments(line))
    assert segs == [
        (100, 200, 100, 400),   # x carried forward (BOGUS != '*' but int() fails)
        (100, 400, 700, 400),
    ]
    # Sanity: had we followed congestion (drop the point), the first segment
    # would instead jump straight to (100,200,700,400) on the next valid point.
    assert segs[0] != (100, 200, 700, 400)


def test_iter_route_segments_flattens():
    lines = [
        "+ ROUTED met1 ( 0 0 ) ( 0 100 )",
        "NEW met1 ( 50 50 ) ( 50 200 )",
    ]
    assert list(def_parse.iter_route_segments(lines)) == [
        (0, 0, 0, 100),
        (50, 50, 50, 200),
    ]


# RECT patch groups (2026-07-05 fix): `RECT ( dx1 dy1 dx2 dy2 )` offsets are
# patch metal, not routing points. Before the fix the first two offsets were
# read as an absolute point, adding a phantom segment (measured 1168 um vs
# OpenROAD's 3.29 um on a real aes_core sky130hd net).
def test_rect_patch_group_is_not_a_point():
    # Real shape from ORFS sky130hd write_def: point, then a RECT patch.
    line = "NEW li1 ( 154690 172550 ) RECT ( -70 -85 70 415 )"
    assert list(def_parse.route_segments(line)) == []


def test_rect_between_points_does_not_break_the_chain():
    line = "+ ROUTED met1 ( 100 200 ) RECT ( -70 -85 70 85 ) ( 300 200 ) ( * 600 )"
    assert list(def_parse.route_segments(line)) == [
        (100, 200, 300, 200),
        (300, 200, 300, 600),
    ]


def test_rect_only_strips_four_integer_groups():
    # A net literally named RECT followed by a normal 2-int point must survive.
    line = "+ ROUTED met1 ( 0 0 ) RECT ( 10 20 ) ( 40 0 )"
    assert list(def_parse.route_segments(line)) == [
        (0, 0, 10, 20),
        (10, 20, 40, 0),
    ]


# --------------------------------------------------------------------------- #
# Correspondence helpers — recompute the two consumers from route_segments    #
# and (for congestion) from an inline copy of its regex+walk.                 #
# --------------------------------------------------------------------------- #
def _wirelength_via_route_segments(def_file):
    """Per-net Manhattan DBU length using ONLY techlib.route_segments.

    Mirrors parse_def_wirelength's NETS-section scan + dbu division so the result
    is directly comparable, but the per-line coordinate walk comes from the
    consolidated iterator and the dbu comes from the real techlib.parse_units.
    """
    db_units = float(def_parse.parse_units(def_file))
    with open(def_file, "r") as f:
        lines = f.readlines()

    net_start = re.compile(r"^\s*-\s+(\S+)")
    wl = {}
    current = None
    in_nets = False
    for raw in lines:
        line = raw.strip()
        if line.startswith("NETS") and not line.startswith("END NETS") and not line.startswith("SPECIALNETS"):
            in_nets = True
            continue
        if line.startswith("END NETS"):
            in_nets = False
            continue
        if not in_nets:
            continue
        if line.startswith(";"):
            continue
        m = net_start.match(line)
        if m:
            current = m.group(1)
            wl[current] = 0.0
        if current and ("ROUTED" in line or "NEW" in line):
            for x1, y1, x2, y2 in def_parse.route_segments(line):
                wl[current] += abs(x2 - x1) + abs(y2 - y1)
    for net in wl:
        wl[net] = wl[net] / db_units
    return wl


# Inline copy of congestion's point regex + *-chain walk (extract_grid_demand),
# emitting the (x1,y1,x2,y2) sequence per route line. Copied verbatim from
# scripts/extract/labels/extract_congestion.py so we compare against the real
# behavior without importing/modifying that module.
_CONG_POINT_RE = re.compile(r"\(\s*([^\s\)]+)\s+([^\s\)]+)(?:\s+[^\)]*)?\s*\)")


def _strip_rect_patches(line):
    """Drop ``RECT ( dx dy dx dy )`` patch groups — independently of def_parse.

    Written as a token scan rather than def_parse's single regex so this stays a real
    second opinion: if ``_ROUTE_RECT_RE`` ever mis-anchors, this disagrees and the
    correspondence test fails.

    This models the CURRENT contract. The pre-2026-07-05 oracle below had no RECT
    handling at all, so it read the patch offsets as an absolute routing point — the
    defect that inflated RECT-bearing nets ~100-400x. sky130hd DEFs carry RECT groups;
    nangate45 DEFs do not, which is why the divergence only ever showed on sky130.
    """
    toks = line.split()
    out, i = [], 0
    while i < len(toks):
        if toks[i] == "RECT" and i + 5 < len(toks) and toks[i + 1] == "(":
            body = toks[i + 2:i + 6]
            closes = toks[i + 6] == ")" if i + 6 < len(toks) else False
            if closes and all(re.fullmatch(r"-?\d+", t) for t in body):
                i += 7          # skip RECT ( a b c d )
                continue
        out.append(toks[i])
        i += 1
    return " ".join(out)


def _congestion_segments_for_line(line):
    points = _CONG_POINT_RE.findall(_strip_rect_patches(line))
    if len(points) < 2:
        return []
    out = []
    curr_x = None
    curr_y = None
    for x_str, y_str in points:
        if curr_x is None or curr_y is None:
            if x_str == "*" or y_str == "*":
                continue
            try:
                curr_x = int(x_str)
                curr_y = int(y_str)
            except ValueError:
                curr_x = None
                curr_y = None
            continue
        next_x = curr_x
        next_y = curr_y
        if x_str != "*":
            try:
                next_x = int(x_str)
            except ValueError:
                continue
        if y_str != "*":
            try:
                next_y = int(y_str)
            except ValueError:
                continue
        out.append((curr_x, curr_y, next_x, next_y))
        curr_x = next_x
        curr_y = next_y
    return out


def _iter_def_route_lines(def_file):
    """Yield every NETS-section route line the congestion walker would process.

    Matches extract_grid_demand's line gate exactly:
    inside NETS, line has 'ROUTED'/'NEW' or starts with '+'.
    """
    with open(def_file, "r") as f:
        in_nets = False
        for raw in f:
            line = raw.strip()
            if line.startswith("NETS") and not line.startswith("END NETS") and not line.startswith("SPECIALNETS"):
                in_nets = True
                continue
            if line.startswith("END NETS"):
                in_nets = False
                continue
            if not in_nets:
                continue
            if "ROUTED" not in line and "NEW" not in line and not line.startswith("+"):
                continue
            yield line


# --------------------------------------------------------------------------- #
# Correspondence on REAL DEFs.                                                #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "def_path",
    [
        pytest.param(AES_DEF, id="aescore-sky130"),
        pytest.param(CORDIC_DEF, id="cordic-nangate45"),
    ],
)
def test_route_segments_matches_wirelength_per_net(def_path):
    """route_segments reproduces parse_def_wirelength's per-net length, exactly."""
    if def_path is None or not def_path.is_file():
        pytest.skip("no reference DEF and no built design_cases/ run (machine-local)")

    expected_wl, _net_types, _name = ewl.parse_def_wirelength(str(def_path))
    actual_wl = _wirelength_via_route_segments(str(def_path))

    assert set(actual_wl) == set(expected_wl), (
        f"net set differs ({def_path.name}): "
        f"missing={sorted(set(expected_wl) - set(actual_wl))[:5]} "
        f"extra={sorted(set(actual_wl) - set(expected_wl))[:5]}"
    )
    mismatches = [
        (net, expected_wl[net], actual_wl[net])
        for net in expected_wl
        if actual_wl[net] != expected_wl[net]
    ]
    assert not mismatches, (
        f"{def_path.name}: {len(mismatches)} net(s) differ from "
        f"parse_def_wirelength, e.g. {mismatches[:5]}"
    )
    # Guard against a vacuous pass on an empty/unrouted DEF.
    assert sum(1 for v in expected_wl.values() if v > 0) > 0, "no routed nets in DEF"


@pytest.mark.parametrize(
    "def_path",
    [
        pytest.param(AES_DEF, id="aescore-sky130"),
        pytest.param(CORDIC_DEF, id="cordic-nangate45"),
    ],
)
def test_route_segments_matches_congestion_segment_sequence(def_path):
    """Per-route-line segment sequence equals congestion's regex+walk output."""
    if def_path is None or not def_path.is_file():
        pytest.skip("no reference DEF and no built design_cases/ run (machine-local)")

    lines_seen = 0
    seg_lines = 0
    for line in _iter_def_route_lines(def_path):
        lines_seen += 1
        expected = _congestion_segments_for_line(line)
        actual = list(def_parse.route_segments(line))
        assert actual == expected, (
            f"{def_path.name}: route-segment sequence differs on line:\n"
            f"  line     = {line!r}\n"
            f"  expected = {expected}\n"
            f"  actual   = {actual}"
        )
        if actual:
            seg_lines += 1

    assert lines_seen > 0, "no NETS route lines scanned (DEF gate / path wrong?)"
    assert seg_lines > 0, "no route line produced any segment (vacuous pass?)"


def test_parse_nets_use_on_dash_line(tmp_path):
    """ORFS emits `+ USE` ON the `-` net line for single-line nets (28,679/
    30,345 on aes_core sky130hd); scanning only continuation lines made `use`
    an artifact of line-wrapping (2026-07-05 fix, failure-patterns #9)."""
    d = tmp_path / "t.def"
    d.write_text(
        "DESIGN t ;\nUNITS DISTANCE MICRONS 1000 ;\n"
        "NETS 3 ;\n"
        "- clk ( u1 CK ) ( u2 CK ) + USE CLOCK ;\n"
        "- n1 ( u1 A )\n  ( u2 X ) + USE SIGNAL ;\n"
        "- n2 ( u1 B ) ;\n"
        "END NETS\n")
    nets = def_parse.parse_nets(str(d))
    assert nets["clk"]["use"] == "CLOCK"          # dash-line USE (was '')
    assert nets["n1"]["use"] == "SIGNAL"          # continuation-line USE still works
    assert nets["n2"]["use"] == ""
    assert nets["clk"]["conns"] == [("u1", "CK"), ("u2", "CK")]
