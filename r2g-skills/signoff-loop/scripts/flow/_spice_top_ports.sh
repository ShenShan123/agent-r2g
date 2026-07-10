#!/usr/bin/env bash
set -euo pipefail

# usage: _spice_top_ports.sh <extracted.spice> <top_cell_name>
# Prints the port count of the named top-level .subckt (continuation `+` lines
# included). 0 means a PORTLESS extraction — on a routed design that is the
# GDS-lost-DEF-geometry infra defect (failure-patterns.md #33), never a real
# LVS result. Shared helper so the guard in run_netgen_lvs.sh is testable.

SPICE="${1:?extracted.spice path required}"
TOP="${2:?top cell name required}"

awk -v d="$TOP" '
  tolower($1)==".subckt" && $2==d {grab=1; n+=NF-2; next}
  grab && /^\+/ {n+=NF-1; next}
  grab {exit}
  END {print n+0}' "$SPICE"
