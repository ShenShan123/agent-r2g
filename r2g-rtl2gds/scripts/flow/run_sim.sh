#!/usr/bin/env bash
set -euo pipefail

# usage: run_sim.sh <rtl-file|project-dir> [tb-file] [work-dir]
#   project-dir mode (passed by run_stage.sh): finds rtl/*.v, tb/*.v, writes sim/sim.log
ARG1="${1:-}"
ARG2="${2:-}"
ARG3="${3:-}"

if [[ -z "$ARG1" ]]; then
  echo "usage: run_sim.sh <rtl-file|project-dir> [tb-file] [work-dir]" >&2
  exit 1
fi

# ─── project-dir mode (run_stage.sh contract) ─────────────────────────────────
if [[ -d "$ARG1" ]]; then
  PROJ="$ARG1"
  RTL_FILES=($(ls "$PROJ"/rtl/*.v 2>/dev/null || true))
  TB_FILES=($(ls "$PROJ"/tb/*.v 2>/dev/null || true))
  WORK_DIR="${ARG2:-$PROJ/sim}"
  if [[ ${#RTL_FILES[@]} -eq 0 ]]; then
    echo "No .v files in $PROJ/rtl/" >&2
    exit 1
  fi
  if [[ ${#TB_FILES[@]} -eq 0 ]]; then
    echo "No .v files in $PROJ/tb/" >&2
    exit 1
  fi
  mkdir -p "$WORK_DIR"
  COMPILE_STATUS=0
  iverilog -o "$WORK_DIR/sim.out" -I"$PROJ/rtl" -I"$PROJ/tb" "${RTL_FILES[@]}" "${TB_FILES[@]}" >"$WORK_DIR/compile.log" 2>&1 || COMPILE_STATUS=$?
  if [[ $COMPILE_STATUS -ne 0 ]]; then
    echo "ERROR: iverilog compilation failed (exit code $COMPILE_STATUS)" >&2
    echo "Check log: $WORK_DIR/compile.log" >&2
    exit $COMPILE_STATUS
  fi
  SIM_STATUS=0
  (cd "$WORK_DIR" && vvp ./sim.out > sim.log 2>&1) || SIM_STATUS=$?
  if [[ $SIM_STATUS -ne 0 ]]; then
    echo "ERROR: Simulation failed (exit code $SIM_STATUS)" >&2
    echo "Check log: $WORK_DIR/sim.log" >&2
    exit $SIM_STATUS
  fi
  echo "simulation_ok" >> "$WORK_DIR/sim.log"
  exit 0
fi

# ─── legacy mode: <rtl-file> <tb-file> <work-dir> ─────────────────────────────
RTL_FILE="$ARG1"
TB_FILE="${ARG2:-}"
WORK_DIR="${ARG3:-sim}"
if [[ -z "$TB_FILE" ]]; then
  echo "usage: run_sim.sh <rtl-file> <tb-file> <work-dir>" >&2
  exit 1
fi
mkdir -p "$WORK_DIR"
COMPILE_STATUS=0
iverilog -o "$WORK_DIR/sim.out" "$RTL_FILE" "$TB_FILE" >"$WORK_DIR/compile.log" 2>&1 || COMPILE_STATUS=$?
if [[ $COMPILE_STATUS -ne 0 ]]; then
  echo "ERROR: iverilog compilation failed (exit code $COMPILE_STATUS)" >&2
  echo "Check log: $WORK_DIR/compile.log" >&2
  exit $COMPILE_STATUS
fi
SIM_STATUS=0
(cd "$WORK_DIR" && vvp ./sim.out > sim.log 2>&1) || SIM_STATUS=$?
if [[ $SIM_STATUS -ne 0 ]]; then
  echo "ERROR: Simulation failed (exit code $SIM_STATUS)" >&2
  echo "Check log: $WORK_DIR/sim.log" >&2
  exit $SIM_STATUS
fi
echo "simulation_ok" >> "$WORK_DIR/sim.log"
exit 0
