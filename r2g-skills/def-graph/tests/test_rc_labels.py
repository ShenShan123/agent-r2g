"""RC parasitic-label attachment across graph views b-f (the Y side).

A tiny hand-designed dataset drives the real graph builder and asserts the
per-view attachment rule (references/label-extraction.md "RC parasitic labels"):
  ground cap: net node (b/c) -> broadcast to pin nodes (d/e) -> dropped (f)
  coupling  : net<->net (b/c) -> driver-pin<->driver-pin (d/e) -> dropped (f)
  resistance: pin<->pin same-net (b/d/e) -> dropped (c/f)
Also pins the log-domain labels and the coupling/resistance edge-type separation.
"""
import csv
import math
import os
import sys

import pytest

_GRAPH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "scripts", "extract", "graph")
if _GRAPH not in sys.path:
    sys.path.insert(0, _GRAPH)

try:
    import torch  # noqa: F401
    HAVE_TORCH = True
except Exception:
    HAVE_TORCH = False

tensor = pytest.mark.skipif(not HAVE_TORCH, reason="torch/torch_geometric not installed")

DZ = "tiny"
GC_N0, GC_N1 = math.log1p(10.0), math.log1p(20.0)
CP = math.log1p(5.0)


def _wr(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


@pytest.fixture
def rc_case(tmp_path):
    """Two INV gates, two signal nets (n0,n1), one iopin (out0 on n0).
    n0 pins: g0/ZN(drv), g1/A, out0(port). n1 pins: g1/ZN(drv), g0/A."""
    feat = tmp_path / "features"
    lab = tmp_path / "labels"
    feat.mkdir(); lab.mkdir()
    _wr(feat / "nodes_gate.csv",
        ["graph_id", "inst_name", "master", "cell_type_id", "cell_area", "cell_power",
         "x_um", "y_um", "orientation_id", "placement_status_id"],
        [[DZ, "g0", "INV_X1", 1, 1.0, 0.1, 0, 0, 0, 1],
         [DZ, "g1", "INV_X1", 1, 1.0, 0.1, 5, 0, 0, 1]])
    _wr(feat / "nodes_net.csv",
        ["graph_id", "net_name", "net_type_id", "fanout", "pin_count", "num_drivers",
         "num_sinks", "connects_macro_flag", "num_layer", "hpwl_um"],
        [[DZ, "n0", 0, 2, 3, 1, 2, 0, 1, 5.0], [DZ, "n1", 0, 1, 2, 1, 1, 0, 1, 5.0]])
    _wr(feat / "nodes_iopin.csv",
        ["graph_id", "iopin_name", "net_name", "net_type_id", "pin_x_um", "pin_y_um",
         "nearest_tap_distance_um", "pin_direction_id"],
        [[DZ, "out0", "n0", 0, 10, 0, 1.0, 1]])
    _wr(feat / "nodes_pin.csv",
        ["graph_id", "inst_name", "pin_name", "pin_type_id", "sum_pin_cap_fF"],
        [[DZ, "g0", "ZN", 2, 0.0], [DZ, "g0", "A", 1, 1.0],
         [DZ, "g1", "ZN", 2, 0.0], [DZ, "g1", "A", 1, 1.0]])
    _wr(feat / "edges_gate_pin.csv", ["graph_id", "inst_name", "pin_name"],
        [[DZ, "g0", "ZN"], [DZ, "g0", "A"], [DZ, "g1", "ZN"], [DZ, "g1", "A"]])
    _wr(feat / "edges_pin_net.csv",
        ["graph_id", "inst_name", "pin_name", "net_name", "net_type_id"],
        [[DZ, "g0", "ZN", "n0", 0], [DZ, "g1", "A", "n0", 0],
         [DZ, "g1", "ZN", "n1", 0], [DZ, "g0", "A", "n1", 0]])
    _wr(feat / "edges_iopin_net.csv", ["graph_id", "iopin_name", "net_name", "net_type_id"],
        [[DZ, "out0", "n0", 0]])
    _wr(feat / "metadata.csv",
        ["graph_id", "num_cells", "num_nets", "num_ios", "avg_fanout", "die_width",
         "die_height", "core_area", "dbu_unit", "PLACE_DENSITY", "CORE_UTILIZATION",
         "ABC_AREA", "C_total", "tracks_per_layer", "V_nom", "freq_Hz", "tracks_detail"],
        [[DZ, 2, 2, 1, 1.5, 10, 10, 100, 2000, 0.6, 40, 0, 30, 200, 1.1, 1000000, "m1:200"]])
    # minimal valid tool-label CSVs
    _wr(lab / "wirelength.csv", ["Design", "Net", "NetType", "WireLength_um", "label", "mask_wl"],
        [[DZ, "n0", "SIGNAL", 5.0, math.log1p(5.0), "true"],
         [DZ, "n1", "SIGNAL", 5.0, math.log1p(5.0), "true"]])
    _wr(lab / "cell_congestion.csv", ["Design", "Cell", "cell_congestion", "label", "label_raw"],
        [[DZ, "g0", 0.5, 0.5, 0.25], [DZ, "g1", 0.5, 0.5, 0.25]])
    _wr(lab / "ir_drop.csv", ["Design", "Cell", "IR_Drop_mV", "label", "P95_mV", "has_irdrop"],
        [[DZ, "g0", 1.0, 0.1, 2.0, "true"]])
    _wr(lab / "timing_features.csv", ["Design", "Cell", "Path_Delay_ns", "label", "in_sta_path"],
        [[DZ, "g0", 0.3, 0.2, "true"]])
    # RC labels
    _wr(lab / "net_ground_cap.csv", ["Design", "Net", "ground_cap_fF", "label"],
        [[DZ, "n0", 10.0, GC_N0], [DZ, "n1", 20.0, GC_N1]])
    _wr(lab / "coupling_cap.csv", ["Design", "Net1", "Net2", "coupling_cap_fF", "label"],
        [[DZ, "n0", "n1", 5.0, CP]])
    _wr(lab / "equiv_res.csv",
        ["Design", "Net", "Inst1", "Pin1", "Inst2", "Pin2", "equiv_res_ohm", "label"],
        [[DZ, "n0", "g0", "ZN", "g1", "A", 100.0, math.log1p(100.0)],
         [DZ, "n0", "g0", "ZN", "PIN", "out0", 50.0, math.log1p(50.0)],
         [DZ, "n0", "g1", "A", "PIN", "out0", 150.0, math.log1p(150.0)],
         [DZ, "n1", "g1", "ZN", "g0", "A", 200.0, math.log1p(200.0)]])
    _wr(lab / "net_driver.csv", ["Design", "Net", "DrvInst", "DrvPin"],
        [[DZ, "n0", "g0", "ZN"], [DZ, "n1", "g1", "ZN"]])
    return str(feat), str(lab)


def _build(variant, feat, lab):
    import build_graphs as bg
    import graph_lib as gl
    views7 = gl.build_feature_views(feat, DZ)
    label_dfs = gl.load_label_cache(lab)
    rc = gl.load_rc_label_cache(lab)
    return bg.BUILDERS[variant](views7, label_dfs, DZ, DZ, 0, feat, rc=rc)


def _counts(data):
    return (int((data.rc_edge_type == 0).sum()), int((data.rc_edge_type == 1).sum()))


@tensor
@pytest.mark.parametrize("v", list("bcdef"))
def test_uniform_rc_schema(rc_case, v):
    d = _build(v, *rc_case)
    assert d.y.shape[1] == 6, "y widened to 6 (y5 = ground cap)"
    assert hasattr(d, "rc_edge_index") and hasattr(d, "rc_edge_type") and hasattr(d, "rc_edge_y")
    assert d.rc_edge_y.shape[1] == 3  # [type, coupling_label, res_label]
    assert d.rc_edge_index.shape[1] == d.rc_edge_type.numel() == d.rc_edge_y.shape[0]


@tensor
def test_ground_cap_on_net_nodes_bc(rc_case):
    for v in ("b", "c"):
        d = _build(v, *rc_case)
        nt = d.x[:, 0].long()
        gc = {d.node_name[i]: float(d.y[i, 5]) for i in (nt == 1).nonzero().view(-1).tolist()}
        assert gc["n0"] == pytest.approx(GC_N0) and gc["n1"] == pytest.approx(GC_N1)
        # pins/other nodes have no ground cap
        assert all(math.isnan(float(d.y[i, 5])) for i in (nt == 3).nonzero().view(-1).tolist())


@tensor
def test_ground_cap_broadcast_to_pins_de(rc_case):
    for v in ("d", "e"):
        d = _build(v, *rc_case)
        nt = d.x[:, 0].long()
        pv = {d.node_name[i]: float(d.y[i, 5]) for i in (nt == 3).nonzero().view(-1).tolist()}
        assert pv["g0/ZN"] == pytest.approx(GC_N0) and pv["g1/A"] == pytest.approx(GC_N0)
        assert pv["g1/ZN"] == pytest.approx(GC_N1) and pv["g0/A"] == pytest.approx(GC_N1)


@tensor
def test_ground_cap_dropped_in_f(rc_case):
    d = _build("f", *rc_case)
    assert bool(torch.isnan(d.y[:, 5]).all())


@tensor
@pytest.mark.parametrize("v,coupling,resistance", [
    ("b", 2, 8), ("c", 2, 0), ("d", 2, 8), ("e", 2, 8), ("f", 0, 0)])
def test_parasitic_edge_counts(rc_case, v, coupling, resistance):
    d = _build(v, *rc_case)
    assert _counts(d) == (coupling, resistance)


@tensor
def test_coupling_endpoints_and_labels(rc_case):
    # b/c: coupling connects the two NET nodes; d/e: the two DRIVER pins.
    for v, expect in (("b", {"n0", "n1"}), ("c", {"n0", "n1"}),
                      ("d", {"g0/ZN", "g1/ZN"}), ("e", {"g0/ZN", "g1/ZN"})):
        d = _build(v, *rc_case)
        ci = (d.rc_edge_type == 0).nonzero().view(-1)[0].item()
        u, w = d.rc_edge_index[0, ci].item(), d.rc_edge_index[1, ci].item()
        assert {d.node_name[u], d.node_name[w]} == expect
        assert float(d.rc_edge_y[ci, 1]) == pytest.approx(CP)   # coupling label
        assert math.isnan(float(d.rc_edge_y[ci, 2]))            # not a resistance row


@tensor
def test_resistance_is_intra_net_pinpair(rc_case):
    # every resistance edge in b joins two pins/iopins that share a net
    d = _build("b", *rc_case)
    ri = (d.rc_edge_type == 1).nonzero().view(-1).tolist()
    assert ri, "b has resistance edges"
    for k in ri:
        assert math.isnan(float(d.rc_edge_y[k, 1]))            # not a coupling row
        assert not math.isnan(float(d.rc_edge_y[k, 2]))        # has a resistance label
