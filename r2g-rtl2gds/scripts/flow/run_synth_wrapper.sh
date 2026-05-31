#!/usr/bin/env bash
# Wrapper to adapt run_synth.sh to accept PROJECT_DIR
set -euo pipefail

PROJECT_DIR="${1:?usage: run_synth_wrapper.sh <project-dir>}"

# Find RTL files
RTL_FILES=("$PROJECT_DIR"/rtl/*.v)
if [ ${#RTL_FILES[@]} -eq 0 ] || [ ! -f "${RTL_FILES[0]}" ]; then
  echo "No RTL files found in $PROJECT_DIR/rtl/" >&2
  exit 1
fi

# Detect top module from largest file or config
TOP_MODULE=""
if [ -f "$PROJECT_DIR/constraints/config.mk" ]; then
  TOP_MODULE=$(grep -E "^DESIGN_NAME\s*[:?]?=" "$PROJECT_DIR/constraints/config.mk" | head -1 | sed 's/.*=\s*//' | tr -d ' ')
fi

if [ -z "$TOP_MODULE" ]; then
  # Find largest .v file and extract module name
  LARGEST_FILE=$(ls -S "${RTL_FILES[@]}" | head -1)
  TOP_MODULE=$(grep -m1 "^module" "$LARGEST_FILE" | sed 's/module\s\+//' | sed 's/\s.*//' | tr -d '(')
fi

if [ -z "$TOP_MODULE" ]; then
  echo "Could not determine top module" >&2
  exit 1
fi

echo "Top module detected: $TOP_MODULE" >&2

# Combine all RTL files for synthesis
COMBINED_RTL="$PROJECT_DIR/rtl/combined.v"
cat "${RTL_FILES[@]}" > "$COMBINED_RTL"

mkdir -p "$PROJECT_DIR/synth"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$SCRIPT_DIR/run_synth.sh" "$COMBINED_RTL" "$TOP_MODULE" "$PROJECT_DIR/synth"
