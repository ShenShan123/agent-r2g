#!/usr/bin/env bash
set -euo pipefail

# usage: run_synth.sh <rtl-file|project-dir> [top-module] [work-dir]
#   project-dir mode (passed by run_stage.sh): finds rtl/*.v, reads top from
#   constraints/config.mk (DESIGN_NAME), writes synth/synth_output.v
ARG1="${1:-}"
ARG2="${2:-}"
ARG3="${3:-}"

if [[ -z "$ARG1" ]]; then
  echo "usage: run_synth.sh <rtl-file|project-dir> [top-module] [work-dir]" >&2
  exit 1
fi

# ─── project-dir mode (run_stage.sh contract) ─────────────────────────────────
if [[ -d "$ARG1" ]]; then
  PROJ="$ARG1"
  RTL_FILES=($(ls "$PROJ"/rtl/*.v 2>/dev/null || true))
  WORK_DIR="${ARG2:-$PROJ/synth}"
  if [[ ${#RTL_FILES[@]} -eq 0 ]]; then
    echo "No .v files in $PROJ/rtl/" >&2
    exit 1
  fi
  # Try to read DESIGN_NAME from config.mk, fall back to ARG3 or directory name
  TOP="${ARG3:-}"
  if [[ -z "$TOP" ]] && [[ -f "$PROJ/constraints/config.mk" ]]; then
    TOP=$(sed -n 's/^export DESIGN_NAME\s*[:?]=\s*//p' "$PROJ/constraints/config.mk" | tr -d ' ' | head -1)
  fi
  TOP="${TOP:-$(basename "$PROJ")}"
  mkdir -p "$WORK_DIR"
  # Build yosys script with all RTL files
  cat > "$WORK_DIR/synth.ys" <<YOSYS_EOF
YOSYS_EOF
  for f in "${RTL_FILES[@]}"; do
    echo "read_verilog -I$(dirname "$f") $f" >> "$WORK_DIR/synth.ys"
  done
  cat >> "$WORK_DIR/synth.ys" <<YOSYS_EOF
hierarchy -check -top $TOP
synth -top $TOP
stat
write_verilog "$WORK_DIR/synth_output.v"
YOSYS_EOF
  SYNTH_STATUS=0
  yosys -s "$WORK_DIR/synth.ys" >"$WORK_DIR/synth.log" 2>&1 || SYNTH_STATUS=$?
  if [[ $SYNTH_STATUS -ne 0 ]]; then
    echo "ERROR: Yosys synthesis failed (exit code $SYNTH_STATUS)" >&2
    echo "Check log: $WORK_DIR/synth.log" >&2
  fi
  exit $SYNTH_STATUS
fi

# ─── legacy mode: <rtl-file> <top-module> <work-dir> ──────────────────────────
RTL_FILE="$ARG1"
TOP="${ARG2:-}"
WORK_DIR="${ARG3:-synth}"
if [[ -z "$TOP" ]]; then
  echo "usage: run_synth.sh <rtl-file> <top-module> <work-dir>" >&2
  exit 1
fi
mkdir -p "$WORK_DIR"
if [[ -f "$RTL_FILE" ]]; then
  RTL_FILE="$(cd "$(dirname "$RTL_FILE")" && pwd)/$(basename "$RTL_FILE")"
fi
cat > "$WORK_DIR/synth.ys" <<YOSYS_EOF
read_verilog "$RTL_FILE"
hierarchy -check -top $TOP
synth -top $TOP
stat
write_verilog "$WORK_DIR/synth_output.v"
YOSYS_EOF
SYNTH_STATUS=0
yosys -s "$WORK_DIR/synth.ys" >"$WORK_DIR/synth.log" 2>&1 || SYNTH_STATUS=$?
if [[ $SYNTH_STATUS -ne 0 ]]; then
  echo "ERROR: Yosys synthesis failed (exit code $SYNTH_STATUS)" >&2
  echo "Check log: $WORK_DIR/synth.log" >&2
fi
exit $SYNTH_STATUS
