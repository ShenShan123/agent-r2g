#!/usr/bin/env bash
set -euo pipefail

# usage: run_orfs.sh <project-dir> [platform] [flow_variant]
# Runs OpenROAD-flow-scripts backend for the given project.
# Expects <project-dir>/constraints/config.mk and constraint.sdc to exist.
# Results are collected back into <project-dir>/backend/
# Optional flow_variant (default: derived from project dir) isolates ORFS work directories.
# Set ORFS_TIMEOUT (seconds) to limit runtime (default: 7200 = 2 hours).
# Set ORFS_MAX_CPUS to limit CPU cores (default: all available).

PROJECT_DIR="${1:-}"
PLATFORM="${2:-nangate45}"
# Derive FLOW_VARIANT from project directory basename to isolate ORFS work dirs
# per project config (e.g., swerv_cfg1 vs swerv_cfg2 get separate directories).
# This prevents directory collisions when multiple configs share the same DESIGN_NAME.
if [[ -n "${3:-}" ]]; then
  FLOW_VARIANT="$3"
elif [[ -n "$PROJECT_DIR" && -d "$PROJECT_DIR" ]]; then
  FLOW_VARIANT="$(basename "$(cd "$PROJECT_DIR" && pwd)")"
else
  FLOW_VARIANT="base"
fi
FROM_STAGE="${FROM_STAGE:-}"
ORFS_ROOT="${ORFS_ROOT:-/opt/EDA4AI/OpenROAD-flow-scripts}"
FLOW_DIR="$ORFS_ROOT/flow"

if [[ -z "$PROJECT_DIR" ]]; then
  echo "usage: run_orfs.sh <project-dir> [platform]" >&2
  exit 1
fi

PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
CONFIG_MK="$PROJECT_DIR/constraints/config.mk"
SDC_FILE="$PROJECT_DIR/constraints/constraint.sdc"

if [[ ! -f "$CONFIG_MK" ]]; then
  echo "ERROR: config.mk not found at $CONFIG_MK" >&2
  exit 1
fi

if [[ ! -f "$SDC_FILE" ]]; then
  echo "ERROR: constraint.sdc not found at $SDC_FILE" >&2
  exit 1
fi

# Source environment
if [[ -f /opt/openroad_tools_env.sh ]]; then
  source /opt/openroad_tools_env.sh
fi

# Create a design directory inside ORFS for this project
DESIGN_NAME=$(grep 'DESIGN_NAME' "$CONFIG_MK" | head -1 | sed 's/.*=\s*//' | tr -d ' ')
ORFS_DESIGN_DIR="$FLOW_DIR/designs/$PLATFORM/$DESIGN_NAME"
mkdir -p "$ORFS_DESIGN_DIR"

# Copy config.mk and constraint.sdc
cp "$CONFIG_MK" "$ORFS_DESIGN_DIR/config.mk"
cp "$SDC_FILE" "$ORFS_DESIGN_DIR/constraint.sdc"

# Ensure RTL path in config.mk is absolute
# (The config.mk should already use absolute paths, but let's verify)
if grep -q 'VERILOG_FILES' "$ORFS_DESIGN_DIR/config.mk"; then
  echo "config.mk has VERILOG_FILES entry"
else
  echo "WARNING: config.mk missing VERILOG_FILES" >&2
fi

# Create a timestamp for this run
RUN_TAG="RUN_$(date +%Y-%m-%d_%H-%M-%S)"
echo "Starting ORFS run: $RUN_TAG"
echo "Design: $DESIGN_NAME"
echo "Platform: $PLATFORM"
echo "Flow variant: $FLOW_VARIANT"
echo "Config: $ORFS_DESIGN_DIR/config.mk"

# Run the ORFS flow
cd "$FLOW_DIR"

# Prevent env collision: ORFS Makefile uses SCRIPTS_DIR internally
unset SCRIPTS_DIR 2>/dev/null || true

if [[ -z "$FROM_STAGE" ]]; then
  echo "Cleaning previous ORFS state for variant=$FLOW_VARIANT ..."
  make DESIGN_CONFIG="$ORFS_DESIGN_DIR/config.mk" FLOW_VARIANT="$FLOW_VARIANT" clean_all 2>&1 | tail -5 || echo "WARNING: clean_all returned non-zero (may be first run)" >&2
else
  echo "Skipping clean_all (resuming from stage: $FROM_STAGE)"
fi

BACKEND_DIR="$PROJECT_DIR/backend/$RUN_TAG"
mkdir -p "$BACKEND_DIR"

# Timeout and CPU limit support
ORFS_TIMEOUT="${ORFS_TIMEOUT:-7200}"
MAKE_CMD="make DESIGN_CONFIG=\"$ORFS_DESIGN_DIR/config.mk\" FLOW_VARIANT=\"$FLOW_VARIANT\""

# Apply CPU core limit if specified
if [[ -n "${ORFS_MAX_CPUS:-}" ]]; then
  # Build a CPU list 0-(N-1)
  CPU_LIST="0-$((ORFS_MAX_CPUS - 1))"
  MAKE_CMD="taskset -c $CPU_LIST $MAKE_CMD"
  echo "Limiting to $ORFS_MAX_CPUS CPU cores ($CPU_LIST)"
fi

echo "Timeout: ${ORFS_TIMEOUT}s"

# Stage-by-stage execution support
ORFS_STAGES_LIST="${ORFS_STAGES:-synth floorplan place cts route finish}"

run_stage() {
  local stage="$1"
  echo ""
  echo "=== Running ORFS stage: $stage ==="
  local stage_start
  stage_start=$(date +%s)

  local STAGE_STATUS=0
  set +e +o pipefail
  # Use setsid so timeout can kill the entire process group (prevents zombie processes)
  setsid timeout --signal=TERM --kill-after=60 "$ORFS_TIMEOUT" \
    bash -c "$MAKE_CMD $stage" 2>&1 | tee -a "$BACKEND_DIR/flow.log"
  STAGE_STATUS=${PIPESTATUS[0]}
  set -e -o pipefail

  local stage_end
  stage_end=$(date +%s)
  local stage_elapsed=$((stage_end - stage_start))
  echo "{\"stage\": \"$stage\", \"status\": $STAGE_STATUS, \"elapsed_s\": $stage_elapsed}" >> "$BACKEND_DIR/stage_log.jsonl"

  if [[ $STAGE_STATUS -ne 0 ]]; then
    echo "ERROR: Stage '$stage' failed (exit code $STAGE_STATUS) after ${stage_elapsed}s" | tee -a "$BACKEND_DIR/flow.log"
    if [[ $STAGE_STATUS -eq 124 || $STAGE_STATUS -eq 137 ]]; then
      echo "  (timed out after ${ORFS_TIMEOUT}s, exit code $STAGE_STATUS)" | tee -a "$BACKEND_DIR/flow.log"
    fi
    return $STAGE_STATUS
  fi
  echo "Stage '$stage' completed in ${stage_elapsed}s"
  return 0
}

# Run stages
MAKE_STATUS=0
SKIP_STAGES=true
if [[ -z "$FROM_STAGE" ]]; then
  SKIP_STAGES=false
fi

for stage in $ORFS_STAGES_LIST; do
  if [[ "$SKIP_STAGES" == "true" ]]; then
    if [[ "$stage" == "$FROM_STAGE" ]]; then
      SKIP_STAGES=false
    else
      echo "Skipping stage: $stage (resuming from $FROM_STAGE)"
      continue
    fi
  fi

  run_stage "$stage" || { MAKE_STATUS=$?; break; }
done

# Detect routing failure and suggest recovery
if [[ $MAKE_STATUS -ne 0 ]]; then
  FAILED_STAGE=$(tail -1 "$BACKEND_DIR/stage_log.jsonl" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('stage','unknown'))" 2>/dev/null || echo "unknown")
  if [[ "$FAILED_STAGE" == "grt" || "$FAILED_STAGE" == "route" ]]; then
    echo "" | tee -a "$BACKEND_DIR/flow.log"
    echo "HINT: Routing congestion detected. Try re-running with:" | tee -a "$BACKEND_DIR/flow.log"
    echo "  1. Add to config.mk: export ROUTING_LAYER_ADJUSTMENT = 0.10" | tee -a "$BACKEND_DIR/flow.log"
    echo "  2. Resume: FROM_STAGE=route scripts/run_orfs.sh $PROJECT_DIR $PLATFORM" | tee -a "$BACKEND_DIR/flow.log"
  elif [[ "$FAILED_STAGE" == "floorplan" ]]; then
    if grep -q "PDN-0179\|Insufficient width to add straps\|Unable to repair all channels" "$BACKEND_DIR/flow.log" 2>/dev/null; then
      echo "" | tee -a "$BACKEND_DIR/flow.log"
      echo "HINT: PDN channel repair failure (PDN-0179) detected during floorplan." | tee -a "$BACKEND_DIR/flow.log"
      echo "  The design has too many cells for the current die area." | tee -a "$BACKEND_DIR/flow.log"
      echo "  Possible fixes:" | tee -a "$BACKEND_DIR/flow.log"
      echo "  1. Increase DIE_AREA/CORE_AREA by 10-20% in config.mk" | tee -a "$BACKEND_DIR/flow.log"
      echo "  2. Reduce PLACE_DENSITY in config.mk" | tee -a "$BACKEND_DIR/flow.log"
      echo "  3. Remove SYNTH_HIERARCHICAL=1 if set (reduces cell count)" | tee -a "$BACKEND_DIR/flow.log"
      echo "  4. Remove ABC_AREA=1 if set (changes cell mix)" | tee -a "$BACKEND_DIR/flow.log"
    fi
  fi
fi

# Collect results (ORFS uses FLOW_VARIANT as subdirectory)
RESULTS_DIR="$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
LOGS_DIR="$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
OBJECTS_DIR="$FLOW_DIR/objects/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"
REPORTS_DIR="$FLOW_DIR/reports/$PLATFORM/$DESIGN_NAME/$FLOW_VARIANT"

# Fallback: if variant dir doesn't exist, try without it
if [[ ! -d "$RESULTS_DIR" ]]; then
  RESULTS_DIR="$FLOW_DIR/results/$PLATFORM/$DESIGN_NAME"
  LOGS_DIR="$FLOW_DIR/logs/$PLATFORM/$DESIGN_NAME"
  OBJECTS_DIR="$FLOW_DIR/objects/$PLATFORM/$DESIGN_NAME"
  REPORTS_DIR="$FLOW_DIR/reports/$PLATFORM/$DESIGN_NAME"
fi

# Copy results to project backend directory
if [[ -d "$RESULTS_DIR" ]]; then
  cp -r "$RESULTS_DIR" "$BACKEND_DIR/results" 2>/dev/null || true
fi

if [[ -d "$LOGS_DIR" ]]; then
  cp -r "$LOGS_DIR" "$BACKEND_DIR/logs" 2>/dev/null || true
fi

if [[ -d "$REPORTS_DIR" ]]; then
  cp -r "$REPORTS_DIR" "$BACKEND_DIR/reports_orfs" 2>/dev/null || true
fi

# Copy key artifacts
GDS_FILES=$(find "$RESULTS_DIR" -name "*.gds" 2>/dev/null || true)
DEF_FILES=$(find "$RESULTS_DIR" -name "*.def" 2>/dev/null || true)
ODB_FILES=$(find "$RESULTS_DIR" -name "*.odb" 2>/dev/null || true)

mkdir -p "$BACKEND_DIR/final"

for f in $GDS_FILES; do
  cp "$f" "$BACKEND_DIR/final/" 2>/dev/null || true
done
for f in $DEF_FILES; do
  cp "$f" "$BACKEND_DIR/final/" 2>/dev/null || true
done
for f in $ODB_FILES; do
  cp "$f" "$BACKEND_DIR/final/" 2>/dev/null || true
done

# Write run metadata
cat > "$BACKEND_DIR/run-meta.json" <<METAEOF
{
  "run_tag": "$RUN_TAG",
  "design_name": "$DESIGN_NAME",
  "platform": "$PLATFORM",
  "config_mk": "$CONFIG_MK",
  "sdc_file": "$SDC_FILE",
  "make_status": $MAKE_STATUS,
  "orfs_results": "$RESULTS_DIR",
  "orfs_logs": "$LOGS_DIR"
}
METAEOF

if [[ $MAKE_STATUS -eq 0 ]]; then
  echo ""
  echo "ORFS run completed successfully: $RUN_TAG"
  echo "Results: $BACKEND_DIR"
else
  echo ""
  echo "ORFS run FAILED (exit code $MAKE_STATUS): $RUN_TAG"
  echo "Check logs: $BACKEND_DIR/flow.log"
fi

exit $MAKE_STATUS
