"""sky130hs GDS-lost-DEF-geometry defect chain (failure-patterns.md #33).

ORFS's sky130hs.lyt shipped LEGACY KLayout lefdef reader options (wrong
datatypes, no produce-special-routing), so def2stream silently dropped every
DEF-derived shape from 6_final.gds: magic then extracted a PORTLESS top subckt
and Netgen reported a false "top pin mismatch" on 100% of sky130hs designs
(all LVS-clean on sky130hd). Two independent guards:

* tools/patch_sky130hs_lyt.py — ports sky130hd's modern <lefdef> block
  (keeping the hs layer-map); idempotent; --check gate for CI/bootstrap.
* scripts/flow/_spice_top_ports.sh — the run_netgen_lvs.sh guard: a portless
  top-level extraction is an infra ERROR (json status "error"), never a
  mismatch verdict the learner would ingest as a design symptom.
"""
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_FLOW = os.path.join(os.path.dirname(_HERE), "scripts", "flow")
_TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_HERE))), "tools")
PATCH = os.path.join(_TOOLS, "patch_sky130hs_lyt.py")
PORTS = os.path.join(_FLOW, "_spice_top_ports.sh")

LEGACY_HS = """<technology>
 <name>sky130</name>
 <reader-options>
  <lefdef>
   <produce-via-geometry>true</produce-via-geometry>
   <produce-pins>true</produce-pins>
   <produce-routing>true</produce-routing>
   <layer-map>layer_map('met2.drawing : 69/20';'met3.drawing : 70/20')</layer-map>
   <routing-suffix>.drawing</routing-suffix>
   <routing-datatype>0</routing-datatype>
   <pins-suffix>.pin</pins-suffix>
   <pins-datatype>2</pins-datatype>
   <via-geometry-datatype>0</via-geometry-datatype>
  </lefdef>
 </reader-options>
</technology>
"""

MODERN_HD = """<technology>
 <name>sky130A</name>
 <reader-options>
  <lefdef>
   <read-all-layers>true</read-all-layers>
   <layer-map>layer_map('met2.drawing : 69/20';'HD_ONLY.marker : 99/0')</layer-map>
   <produce-via-geometry>true</produce-via-geometry>
   <special-via_geometry-suffix-string>.drawing</special-via_geometry-suffix-string>
   <special-via_geometry-datatype-string>44</special-via_geometry-datatype-string>
   <produce-pins>true</produce-pins>
   <special-pins-suffix-string>.pin</special-pins-suffix-string>
   <special-pins-datatype-string>16</special-pins-datatype-string>
   <produce-routing>true</produce-routing>
   <routing-suffix-string>.drawing</routing-suffix-string>
   <routing-datatype-string>20</routing-datatype-string>
   <produce-special-routing>true</produce-special-routing>
  </lefdef>
 </reader-options>
</technology>
"""


def _mk_flow(tmp_path, hs_text=LEGACY_HS):
    for plat, text in (("sky130hd", MODERN_HD), ("sky130hs", hs_text)):
        d = tmp_path / "platforms" / plat
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{plat}.lyt").write_text(text, encoding="utf-8")
    return tmp_path


def _run_patch(flow, *args):
    env = dict(os.environ, FLOW_DIR=str(flow))
    return subprocess.run([sys.executable, PATCH, *args],
                          capture_output=True, text=True, env=env)


def test_patch_ports_modern_options_and_keeps_hs_layer_map(tmp_path):
    flow = _mk_flow(tmp_path)
    r = _run_patch(flow)
    assert r.returncode == 0, r.stderr
    hs = (flow / "platforms" / "sky130hs" / "sky130hs.lyt").read_text()
    # modern options in, legacy names gone
    assert "<routing-datatype-string>20</routing-datatype-string>" in hs
    assert "<produce-special-routing>true</produce-special-routing>" in hs
    assert "<special-pins-datatype-string>16</special-pins-datatype-string>" in hs
    assert "<routing-datatype>0</routing-datatype>" not in hs
    assert "<pins-datatype>2</pins-datatype>" not in hs
    # the hs layer map is preserved (NOT replaced by hd's)
    assert "met3.drawing : 70/20" in hs
    assert "HD_ONLY.marker" not in hs
    # backup created
    assert (flow / "platforms" / "sky130hs" / "sky130hs.lyt.orig").read_text() \
        == LEGACY_HS


def test_patch_is_idempotent_and_check_mode_gates(tmp_path):
    flow = _mk_flow(tmp_path)
    assert _run_patch(flow, "--check").returncode == 2      # unpatched -> 2
    assert _run_patch(flow).returncode == 0                 # patch
    assert _run_patch(flow, "--check").returncode == 0      # patched -> 0
    once = (flow / "platforms" / "sky130hs" / "sky130hs.lyt").read_text()
    r2 = _run_patch(flow)                                   # second run: no-op
    assert r2.returncode == 0 and "already patched" in r2.stdout
    assert (flow / "platforms" / "sky130hs" / "sky130hs.lyt").read_text() == once


def _ports(spice_path, top):
    r = subprocess.run(["bash", PORTS, str(spice_path), top],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return int(r.stdout.strip())


def test_top_ports_counts_and_flags_portless(tmp_path):
    sp = tmp_path / "extracted.spice"
    sp.write_text(
        ".subckt sky130_fd_sc_hs__nand2_1 VNB VPB VPWR VGND B Y A\n"
        ".ends\n"
        ".subckt Control_logic a b\n"
        "+ c d[0]\n"
        "X0 a b c d[0] sky130_fd_sc_hs__nand2_1\n"
        ".ends\n", encoding="utf-8")
    assert _ports(sp, "Control_logic") == 4          # continuation counted
    assert _ports(sp, "sky130_fd_sc_hs__nand2_1") == 7
    portless = tmp_path / "portless.spice"
    portless.write_text(".subckt Control_logic\n.ends\n", encoding="utf-8")
    assert _ports(portless, "Control_logic") == 0    # the #33 alarm condition
    assert _ports(portless, "no_such_cell") == 0
