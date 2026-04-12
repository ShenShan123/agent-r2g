#!/usr/bin/env bash
set -euo pipefail

# usage: run_drc.sh <project-dir> [platform] [flow_variant]
# Runs KLayout DRC on a completed ORFS backend run.
# Expects a successful backend run with GDS output.
# Results are collected into <project-dir>/drc/

PROJECT_DIR="${1:-}"
PLATFORM="${2:-nangate45}"
# Derive FLOW_VARIANT from project directory basename (matching run_orfs.sh logic)
if [[ -n "${3:-}" ]]; then
  FLOW_VARIANT="$3"
elif [[ -n "$PROJECT_DIR" && -d "$PROJECT_DIR" ]]; then
  FLOW_VARIANT="$(basename "$(cd "$PROJECT_DIR" && pwd)")"
else
  FLOW_VARIANT="base"
fi
ORFS_ROOT="${ORFS_ROOT:-/opt/EDA4AI/OpenROAD-flow-scripts}"
FLOW_DIR="$ORFS_ROOT/flow"

if [[ -z "$PROJECT_DIR" ]]; then
  echo "usage: run_drc.sh <project-dir> [platform]" >&2
  exit 1
fi

PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
CONFIG_MK="$PROJECT_DIR/constraints/config.mk"

if [[ ! -f "$CONFIG_MK" ]]; then
  echo "ERROR: config.mk not found at $CONFIG_MK" >&2
  exit 1
fi

# Source environment
if [[ -f /opt/openroad_tools_env.sh ]]; then
  source /opt/openroad_tools_env.sh
fi

DESIGN_NAME=$(grep 'DESIGN_NAME' "$CONFIG_MK" | head -1 | sed 's/.*=\s*//' | tr -d ' ')

# Verify GDS exists from a prior ORFS run
RESULTS_DIR="$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
if [[ ! -d "$RESULTS_DIR" ]]; then
  RESULTS_DIR="$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME"
fi

GDS_FILE=$(find "$RESULTS_DIR" -name "6_final.gds" 2>/dev/null | head -1)
if [[ -z "$GDS_FILE" ]]; then
  echo "ERROR: No 6_final.gds found in $RESULTS_DIR" >&2
  echo "Run the ORFS backend first: run_orfs.sh <project-dir>" >&2
  exit 1
fi

echo "Running DRC for design: $DESIGN_NAME"
echo "Platform: $PLATFORM"
echo "GDS: $GDS_FILE"

# Run DRC via ORFS Makefile
ORFS_DESIGN_DIR="$FLOW_DIR/designs/$PLATFORM/$DESIGN_NAME"
ORFS_CONFIG="$ORFS_DESIGN_DIR/config.mk"

if [[ ! -f "$ORFS_CONFIG" ]]; then
  echo "ERROR: ORFS config not found at $ORFS_CONFIG" >&2
  echo "Run the ORFS backend first: run_orfs.sh <project-dir>" >&2
  exit 1
fi

cd "$FLOW_DIR"

# Prevent env collision: ORFS Makefile uses SCRIPTS_DIR internally
unset SCRIPTS_DIR 2>/dev/null || true

DRC_TIMEOUT="${DRC_TIMEOUT:-7200}"
echo "Timeout: ${DRC_TIMEOUT}s"

DRC_STATUS=0
set +e +o pipefail
setsid timeout --signal=TERM --kill-after=60 "$DRC_TIMEOUT" \
  make DESIGN_CONFIG="$ORFS_CONFIG" FLOW_VARIANT="$FLOW_VARIANT" drc 2>&1 | tee /tmp/drc_run_$$.log
DRC_STATUS=${PIPESTATUS[0]}
set -e -o pipefail
if [[ $DRC_STATUS -eq 124 ]]; then
  echo "ERROR: DRC timed out after ${DRC_TIMEOUT}s" >&2
fi

# Collect results
DRC_DIR="$PROJECT_DIR/drc"
mkdir -p "$DRC_DIR"
cp /tmp/drc_run_$$.log "$DRC_DIR/drc_run.log" 2>/dev/null || true
rm -f /tmp/drc_run_$$.log

REPORTS_DIR="$FLOW_DIR/reports/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
if [[ ! -d "$REPORTS_DIR" ]]; then
  REPORTS_DIR="$FLOW_DIR/reports/$PLATFORM/$DESIGN_NAME"
fi

LOGS_DIR="$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
if [[ ! -d "$LOGS_DIR" ]]; then
  LOGS_DIR="$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME"
fi

# Copy DRC artifacts
if [[ -f "$REPORTS_DIR/6_drc.lyrdb" ]]; then
  cp "$REPORTS_DIR/6_drc.lyrdb" "$DRC_DIR/" 2>/dev/null || true
fi
if [[ -f "$REPORTS_DIR/6_drc_count.rpt" ]]; then
  cp "$REPORTS_DIR/6_drc_count.rpt" "$DRC_DIR/" 2>/dev/null || true
fi
if [[ -f "$LOGS_DIR/6_drc.log" ]]; then
  cp "$LOGS_DIR/6_drc.log" "$DRC_DIR/" 2>/dev/null || true
fi

# Also copy to latest backend run
BACKEND_DIR="$PROJECT_DIR/backend"
if [[ -d "$BACKEND_DIR" ]]; then
  LATEST_RUN=$(ls -d "$BACKEND_DIR"/RUN_* 2>/dev/null | sort | tail -1)
  if [[ -n "$LATEST_RUN" ]]; then
    mkdir -p "$LATEST_RUN/drc"
    cp "$DRC_DIR"/* "$LATEST_RUN/drc/" 2>/dev/null || true
  fi
fi

# Report results
if [[ -f "$DRC_DIR/6_drc_count.rpt" ]]; then
  COUNT=$(cat "$DRC_DIR/6_drc_count.rpt" 2>/dev/null | tr -d '[:space:]')
  echo ""
  echo "DRC completed: $COUNT violations found"
  if [[ "$COUNT" == "0" ]]; then
    echo "DRC CLEAN"
  else
    echo "DRC FAILED — review $DRC_DIR/6_drc.lyrdb for details"
  fi
else
  echo ""
  echo "DRC completed but no count report found"
fi

echo "Results: $DRC_DIR"
exit $DRC_STATUS
