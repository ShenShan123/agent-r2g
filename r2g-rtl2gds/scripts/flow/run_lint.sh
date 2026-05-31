#!/usr/bin/env bash
set -euo pipefail

# usage: run_lint.sh <rtl-file|project-dir> [log-file]
#   project-dir mode (passed by run_stage.sh): finds rtl/*.v, writes lint/lint.log
ARG1="${1:-}"
LOG_FILE="${2:-}"

if [[ -z "$ARG1" ]]; then
  echo "usage: run_lint.sh <rtl-file|project-dir> [log-file]" >&2
  exit 1
fi

# ─── project-dir mode (run_stage.sh contract) ─────────────────────────────────
if [[ -d "$ARG1" ]]; then
  PROJ="$ARG1"
  RTL_FILES=($(ls "$PROJ"/rtl/*.v 2>/dev/null || true))
  LOG_FILE="${LOG_FILE:-$PROJ/lint/lint.log}"
  if [[ ${#RTL_FILES[@]} -eq 0 ]]; then
    echo "No .v files in $PROJ/rtl/" >&2
    exit 1
  fi
  mkdir -p "$(dirname "$LOG_FILE")"
  INCLUDE_ARGS="-I$PROJ/rtl"
  LINT_STATUS=0
  if command -v verilator >/dev/null 2>&1; then
    verilator --lint-only -Wno-fatal $INCLUDE_ARGS "${RTL_FILES[@]}" >"$LOG_FILE" 2>&1 || LINT_STATUS=$?
  elif command -v iverilog >/dev/null 2>&1; then
    iverilog -t null $INCLUDE_ARGS "${RTL_FILES[@]}" >"$LOG_FILE" 2>&1 || LINT_STATUS=$?
  else
    echo "No lint-capable tool found (need verilator or iverilog)" >"$LOG_FILE"
    exit 2
  fi
  if [[ $LINT_STATUS -eq 0 ]]; then
    echo "lint_ok" >>"$LOG_FILE"
  else
    echo "lint_failed (exit code $LINT_STATUS)" >>"$LOG_FILE"
  fi
  exit $LINT_STATUS
fi

# ─── legacy single-file mode ──────────────────────────────────────────────────
RTL_FILE="$ARG1"
LOG_FILE="${LOG_FILE:-lint.log}"

mkdir -p "$(dirname "$LOG_FILE")"
RTL_DIR="$(dirname "$RTL_FILE")"
INCLUDE_ARGS="-I$RTL_DIR"
LINT_STATUS=0
if command -v verilator >/dev/null 2>&1; then
  verilator --lint-only -Wno-fatal $INCLUDE_ARGS "$RTL_FILE" >"$LOG_FILE" 2>&1 || LINT_STATUS=$?
elif command -v iverilog >/dev/null 2>&1; then
  iverilog -t null $INCLUDE_ARGS "$RTL_FILE" >"$LOG_FILE" 2>&1 || LINT_STATUS=$?
else
  echo "No lint-capable tool found (need verilator or iverilog)" >"$LOG_FILE"
  exit 2
fi
if [[ $LINT_STATUS -eq 0 ]]; then
  echo "lint_ok" >>"$LOG_FILE"
else
  echo "lint_failed (exit code $LINT_STATUS)" >>"$LOG_FILE"
fi
exit $LINT_STATUS
