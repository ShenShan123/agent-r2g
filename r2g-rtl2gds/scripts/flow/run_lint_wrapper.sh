#!/usr/bin/env bash
# Wrapper to adapt run_lint.sh to accept PROJECT_DIR
set -euo pipefail

PROJECT_DIR="${1:?usage: run_lint_wrapper.sh <project-dir>}"

# Find all RTL files
RTL_FILES=("$PROJECT_DIR"/rtl/*.v)
if [ ${#RTL_FILES[@]} -eq 0 ] || [ ! -f "${RTL_FILES[0]}" ]; then
  echo "No RTL files found in $PROJECT_DIR/rtl/" >&2
  exit 1
fi

# Run lint on all RTL files
mkdir -p "$PROJECT_DIR/lint"
LOG_FILE="$PROJECT_DIR/lint/lint.log"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$SCRIPT_DIR/run_lint.sh" "${RTL_FILES[@]}" "$LOG_FILE"
