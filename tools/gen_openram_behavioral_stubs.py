#!/usr/bin/env python3
"""Generate behavioral Verilog stubs for OpenRAM/freepdk45_sram_* modules.

The Chipyard `freepdk45_autogen_openram_sram.v` file wraps every memory
instance in an `_ext` module and instantiates an undefined
`freepdk45_sram_<ports>_<rows>x<cols>[_<gran>]` cell. Those inner modules
are not provided in the BOOM RTL set — they're expected to come from
OpenRAM-generated GDS+LEF+LIB.

For ORFS purposes (we want to reach GDS, not silicon), we don't need the
real macros. We can substitute behavioral flop-array implementations and
let Yosys's memory inference handle them. fakeram45 isn't a great fit
because (1) BOOM uses 1w1r ports that fakeram doesn't support, and
(2) several BOOM widths (40, 44, 52, 56) don't match any fakeram45 size.

Naming convention: `freepdk45_sram_<ports>_<rows>x<cols>[_<gran>]`
  ports   ∈ {"1rw0r", "1w1r"}
  rows    = depth (decimal)
  cols    = data width (decimal)
  gran    = bits per write-mask bit (optional; if absent, no wmask)

Usage:
  gen_openram_behavioral_stubs.py <input_wrapper.v> <output_stubs.v>
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

INST_RE = re.compile(r"\bfreepdk45_sram_(1rw0r|1w1r)_(\d+)x(\d+)(?:_(\d+))?\b")


def collect_unique_signatures(wrapper_path: Path) -> list[tuple[str, int, int, int | None]]:
    """Return unique (ports, rows, cols, gran) tuples referenced in the wrapper file."""
    text = wrapper_path.read_text(encoding="utf-8", errors="ignore")
    seen: set[tuple[str, int, int, int | None]] = set()
    for m in INST_RE.finditer(text):
        ports, rows, cols, gran = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
        gran_i = int(gran) if gran is not None else None
        seen.add((ports, rows, cols, gran_i))
    return sorted(seen)


def emit_1rw0r(rows: int, cols: int, gran: int | None) -> str:
    """Single-port (read/write share clock) behavioral SRAM."""
    addr_w = max(1, math.ceil(math.log2(rows))) if rows > 1 else 1
    name = f"freepdk45_sram_1rw0r_{rows}x{cols}" + (f"_{gran}" if gran is not None else "")
    if gran is None:
        # No write mask: full-word write.
        return f"""
// Behavioral stub: single-port {rows}x{cols}
module {name} (
  input                     clk0,
  input  [{addr_w - 1}:0]            addr0,
  input  [{cols - 1}:0]            din0,
  output reg [{cols - 1}:0]    dout0,
  input                     csb0,
  input                     web0
);
  reg [{cols - 1}:0] mem [0:{rows - 1}];
  always @(posedge clk0) begin
    if (~csb0) begin
      if (~web0) mem[addr0] <= din0;
      dout0 <= mem[addr0];
    end
  end
endmodule
"""
    # With write-mask: one mask bit per `gran` data bits.
    mask_w = (cols + gran - 1) // gran
    # Expand mask bit -> full-word bitmask using replicated ranges.
    expand_lines = []
    for mb in range(mask_w):
        lo = mb * gran
        hi = min(lo + gran, cols) - 1
        expand_lines.append(
            f"        if (wmask0[{mb}]) mem[addr0][{hi}:{lo}] <= din0[{hi}:{lo}];"
        )
    expand = "\n".join(expand_lines)
    return f"""
// Behavioral stub: single-port {rows}x{cols} with {mask_w}-bit write mask
module {name} (
  input                     clk0,
  input  [{addr_w - 1}:0]            addr0,
  input  [{cols - 1}:0]            din0,
  input  [{mask_w - 1}:0]            wmask0,
  output reg [{cols - 1}:0]    dout0,
  input                     csb0,
  input                     web0
);
  reg [{cols - 1}:0] mem [0:{rows - 1}];
  always @(posedge clk0) begin
    if (~csb0) begin
      if (~web0) begin
{expand}
      end
      dout0 <= mem[addr0];
    end
  end
endmodule
"""


def emit_1w1r(rows: int, cols: int, gran: int | None) -> str:
    """Dual-port: port 0 write-only, port 1 read-only. Independent clocks."""
    addr_w = max(1, math.ceil(math.log2(rows))) if rows > 1 else 1
    name = f"freepdk45_sram_1w1r_{rows}x{cols}" + (f"_{gran}" if gran is not None else "")
    if gran is None:
        return f"""
// Behavioral stub: 1w1r {rows}x{cols} (port 0 write-only, port 1 read-only)
module {name} (
  input                     clk0,
  input  [{addr_w - 1}:0]            addr0,
  input  [{cols - 1}:0]            din0,
  input                     csb0,
  input                     clk1,
  input  [{addr_w - 1}:0]            addr1,
  output reg [{cols - 1}:0]    dout1,
  input                     csb1
);
  reg [{cols - 1}:0] mem [0:{rows - 1}];
  always @(posedge clk0) if (~csb0) mem[addr0] <= din0;
  always @(posedge clk1) if (~csb1) dout1 <= mem[addr1];
endmodule
"""
    mask_w = (cols + gran - 1) // gran
    expand_lines = []
    for mb in range(mask_w):
        lo = mb * gran
        hi = min(lo + gran, cols) - 1
        expand_lines.append(
            f"      if (wmask0[{mb}]) mem[addr0][{hi}:{lo}] <= din0[{hi}:{lo}];"
        )
    expand = "\n".join(expand_lines)
    return f"""
// Behavioral stub: 1w1r {rows}x{cols} with {mask_w}-bit write mask
module {name} (
  input                     clk0,
  input  [{addr_w - 1}:0]            addr0,
  input  [{cols - 1}:0]            din0,
  input  [{mask_w - 1}:0]            wmask0,
  input                     csb0,
  input                     clk1,
  input  [{addr_w - 1}:0]            addr1,
  output reg [{cols - 1}:0]    dout1,
  input                     csb1
);
  reg [{cols - 1}:0] mem [0:{rows - 1}];
  always @(posedge clk0) if (~csb0) begin
{expand}
  end
  always @(posedge clk1) if (~csb1) dout1 <= mem[addr1];
endmodule
"""


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} <wrapper.v> <stubs_out.v>", file=sys.stderr)
        return 1
    wrapper = Path(argv[1])
    out = Path(argv[2])
    if not wrapper.is_file():
        print(f"ERROR: {wrapper} not found", file=sys.stderr)
        return 1

    sigs = collect_unique_signatures(wrapper)
    if not sigs:
        print("ERROR: no freepdk45_sram_* references found", file=sys.stderr)
        return 1

    parts = [
        "// Auto-generated behavioral stubs for freepdk45_sram_* modules.",
        f"// Source: {wrapper}",
        f"// Module count: {len(sigs)}",
        "// Yosys will infer these as memories. No real silicon equivalence.",
        "// Generator: tools/gen_openram_behavioral_stubs.py",
        "",
    ]
    for ports, rows, cols, gran in sigs:
        if ports == "1rw0r":
            parts.append(emit_1rw0r(rows, cols, gran))
        elif ports == "1w1r":
            parts.append(emit_1w1r(rows, cols, gran))
        else:
            print(f"WARNING: unknown port style {ports!r} for {rows}x{cols}", file=sys.stderr)

    out.write_text("\n".join(parts), encoding="utf-8")
    total_bits = sum(rows * cols for _, rows, cols, _ in sigs)
    print(f"Wrote {out} — {len(sigs)} stubs, {total_bits} total memory bits")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
