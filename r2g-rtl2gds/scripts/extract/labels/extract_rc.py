#!/usr/bin/env python3
"""Per-net RC parasitic LABELS from a SPEF -> three label CSVs (the Y side).

RC parasitics are *prediction targets*, not features -- so they live in the label
stage next to congestion/wirelength/timing/irdrop, and are annotated onto the
graph's y / parasitic-edge tensors (never x). See references/label-extraction.md
"RC parasitic labels" and graph-dataset.md.

Emits three CSVs (Design-keyed, log-domain ``label`` column like every other
label CSV; raw metric kept alongside):

  net_ground_cap.csv : Design, Net, ground_cap_fF, label
        NET-node label = Sum grounded (2-arg) *CAP entries of the net (fF).
  coupling_cap.csv   : Design, Net1, Net2, coupling_cap_fF, label
        net-PAIR edge label = Sum cross-net (3-arg) *CAP coupling between two nets (fF).
  equiv_res.csv      : Design, Net, Inst1, Pin1, Inst2, Pin2, equiv_res_ohm, label
        pin-PAIR edge label (both pins on the SAME net) = reduced tree resistance
        between the two pins (Ohm). A top-level port endpoint is encoded
        Inst="PIN", Pin=<port> (matching nodes_pin.csv's iopin encoding).
  net_driver.csv     : Design, Net, DrvInst, DrvPin
        each net's driver pin (DrvInst="PIN" for a top-level port driver). Used by
        the graph builder to place net<->net coupling edges on driver PINS in the
        views where nets are folded (d/e) and are not nodes.

Fail-soft: no SPEF (RCX not run / platform w/o rules) -> the three CSVs are written
header-only and a note is printed; the graph stage then leaves the RC y-slot /
parasitic edges empty for the design (never a crash).

Runs under base python3 (pure stdlib + techlib.spef) -- no numpy/scipy, matching
the other label workers.
"""
import csv
import math
import os
import sys

# sys.path bootstrap: make `import techlib.*` resolve when run via run_labels.sh
# (cwd is the project dir). Insert scripts/extract/ = parent of labels/.
_EXTRACT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _EXTRACT_DIR not in sys.path:
    sys.path.insert(0, _EXTRACT_DIR)

from techlib import spef as spef_mod  # noqa: E402

GROUND_CAP_CSV = "net_ground_cap.csv"
COUPLING_CAP_CSV = "coupling_cap.csv"
EQUIV_RES_CSV = "equiv_res.csv"
NET_DRIVER_CSV = "net_driver.csv"


def _log1p(v):
    # RC values are non-negative; clamp tiny negatives from float noise to 0.
    return math.log1p(v if v > 0 else 0.0)


def _write_headers_only(out_dir):
    with open(os.path.join(out_dir, GROUND_CAP_CSV), "w", newline="") as f:
        csv.writer(f).writerow(["Design", "Net", "ground_cap_fF", "label"])
    with open(os.path.join(out_dir, COUPLING_CAP_CSV), "w", newline="") as f:
        csv.writer(f).writerow(["Design", "Net1", "Net2", "coupling_cap_fF", "label"])
    with open(os.path.join(out_dir, EQUIV_RES_CSV), "w", newline="") as f:
        csv.writer(f).writerow(
            ["Design", "Net", "Inst1", "Pin1", "Inst2", "Pin2", "equiv_res_ohm", "label"])
    with open(os.path.join(out_dir, NET_DRIVER_CSV), "w", newline="") as f:
        csv.writer(f).writerow(["Design", "Net", "DrvInst", "DrvPin"])


def main():
    if len(sys.argv) < 4:
        print("usage: extract_rc.py <spef_path> <out_dir> <design_name>", file=sys.stderr)
        sys.exit(1)
    spef_path = sys.argv[1]
    out_dir = sys.argv[2]
    design = sys.argv[3]
    max_fanout = int(os.environ.get("R2G_RC_MAX_FANOUT", "0") or "0")  # 0 = uncapped
    os.makedirs(out_dir, exist_ok=True)

    if not spef_path or not os.path.isfile(spef_path):
        print(f"NOTE: no SPEF ({spef_path!r}) — RC labels empty for {design} "
              f"(RCX not run / platform has no RCX rules).")
        _write_headers_only(out_dir)
        return

    data = spef_mod.parse_spef(spef_path)
    if data is None:
        print(f"NOTE: SPEF unreadable ({spef_path!r}) — RC labels empty for {design}.")
        _write_headers_only(out_dir)
        return

    # --- ground cap (per net) ---
    n_ground = 0
    with open(os.path.join(out_dir, GROUND_CAP_CSV), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Design", "Net", "ground_cap_fF", "label"])
        for net, cap in data.net_ground_cap_ff.items():
            w.writerow([design, net, f"{cap:.6f}", f"{_log1p(cap):.9f}"])
            n_ground += 1

    # --- coupling cap (per cross-net pair) ---
    n_coupling = 0
    with open(os.path.join(out_dir, COUPLING_CAP_CSV), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Design", "Net1", "Net2", "coupling_cap_fF", "label"])
        for (net_a, net_b), cap in data.coupling_cap_ff.items():
            w.writerow([design, net_a, net_b, f"{cap:.6f}", f"{_log1p(cap):.9f}"])
            n_coupling += 1

    # --- equivalent resistance (per intra-net pin pair) ---
    n_res = 0
    n_skipped_nets = 0
    with open(os.path.join(out_dir, EQUIV_RES_CSV), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Design", "Net", "Inst1", "Pin1", "Inst2", "Pin2", "equiv_res_ohm", "label"])
        for net in data.nets:
            result = data.equiv_res_pairs(net, max_fanout=max_fanout)
            if isinstance(result, dict) and result.get("skipped"):
                n_skipped_nets += 1
                print(f"WARN: net {net!r} has {result['skipped']} pins > "
                      f"R2G_RC_MAX_FANOUT={max_fanout} — equiv-res pairs skipped", file=sys.stderr)
                continue
            for (key_a, key_b, ohm) in (result or []):
                (i1, p1), (i2, p2) = key_a, key_b
                w.writerow([design, net, i1, p1, i2, p2, f"{ohm:.6f}", f"{_log1p(ohm):.9f}"])
                n_res += 1

    # --- net driver (for placing coupling on driver pins in folded views) ---
    n_drv = 0
    with open(os.path.join(out_dir, NET_DRIVER_CSV), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Design", "Net", "DrvInst", "DrvPin"])
        for net, drv in data.net_driver.items():
            if drv is None:
                continue
            inst, pin = drv
            w.writerow([design, net, inst, pin])
            n_drv += 1

    print(f"RC labels for {design}: ground_cap nets={n_ground}, coupling pairs={n_coupling}, "
          f"equiv_res pin-pairs={n_res}, net_drivers={n_drv}"
          + (f", skipped nets={n_skipped_nets}" if n_skipped_nets else ""))


if __name__ == "__main__":
    main()
