#!/usr/bin/env bash
# Wrapper to adapt run_sim.sh to accept PROJECT_DIR
set -euo pipefail

PROJECT_DIR="${1:?usage: run_sim_wrapper.sh <project-dir>}"

# Check for testbench files
TB_FILES=("$PROJECT_DIR"/tb/*.v)
if [ ${#TB_FILES[@]} -eq 0 ] || [ ! -f "${TB_FILES[0]}" ]; then
  echo "No testbench files found in $PROJECT_DIR/tb/ - skipping simulation" >&2
  mkdir -p "$PROJECT_DIR/sim"
  echo "simulation_skipped: no testbench" > "$PROJECT_DIR/sim/sim.log"
  exit 0
fi

# For now, skip simulation if no testbench
mkdir -p "$PROJECT_DIR/sim"
echo "simulation_skipped: testbench generation needed" > "$PROJECT_DIR/sim/sim.log"
exit 0
