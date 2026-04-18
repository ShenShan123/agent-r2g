#!/usr/bin/env bash
# Full sweep over all design_cases/* — delegates to batch_orfs_only.sh whose
# per-case "make_status: 0" check already skips anything with a successful
# ORFS run. Only designs with no prior success (or only failed RUN_* dirs)
# actually execute.
#
# Append-only data policy (enforced here):
#   * RESULTS_FILE points to a dated, per-sweep JSONL so we never clobber
#     historical orfs_results.jsonl.
#   * A manifest.txt is appended (never truncated) recording who/when/PID.
#   * batch_orfs_only.sh's own RESULTS_FILE is `touch`ed, not overwritten.
#
# Usage:
#   tools/run_full_sweep.sh [max_parallel] [orfs_timeout_seconds]
# or (recommended) via nohup:
#   nohup tools/run_full_sweep.sh 4 3600 > /path/to/sweep.out 2>&1 &

set -uo pipefail

MAX_JOBS="${1:-4}"
ORFS_TIMEOUT="${2:-3600}"

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CASES_DIR="$BASE_DIR/design_cases"
BATCH_DIR="$CASES_DIR/_batch"
mkdir -p "$BATCH_DIR"

STAMP="$(date +%Y%m%dT%H%M%S)"
DATE_TAG="$(date +%Y%m%d)"

# Per-sweep jsonl and log — one per launch, never reused.
export RESULTS_FILE="$BATCH_DIR/orfs_sweep_${DATE_TAG}_${STAMP}.jsonl"
SWEEP_LOG="$BATCH_DIR/logs/sweep_${DATE_TAG}_${STAMP}.log"
MANIFEST="$BATCH_DIR/sweep_manifest.txt"
mkdir -p "$BATCH_DIR/logs"

# Pre-flight accounting: which designs will actually run vs. be skipped?
cd "$CASES_DIR"
total=0
will_skip=0
will_run_list=()
for cfg in */constraints/config.mk; do
  total=$((total + 1))
  case_dir="$(dirname "$(dirname "$cfg")")"
  name="$(basename "$case_dir")"
  success=0
  for meta in "$case_dir"/backend/RUN_*/run-meta.json; do
    [[ -f "$meta" ]] || continue
    if grep -q '"make_status": 0' "$meta" 2>/dev/null; then
      success=1
      break
    fi
  done
  if [[ $success -eq 1 ]]; then
    will_skip=$((will_skip + 1))
  else
    will_run_list+=("$name")
  fi
done
cd - >/dev/null

# Append a manifest entry (never truncated) so every sweep leaves a paper trail.
{
  echo "===== sweep started: $(date '+%Y-%m-%d %H:%M:%S %Z') ====="
  echo "  pid (this wrapper): $$"
  echo "  MAX_JOBS=$MAX_JOBS  ORFS_TIMEOUT=${ORFS_TIMEOUT}s"
  echo "  results_file: $RESULTS_FILE"
  echo "  sweep_log:    $SWEEP_LOG"
  echo "  total configs: $total"
  echo "  will skip (prior ORFS success): $will_skip"
  echo "  will run (no prior success):    ${#will_run_list[@]}"
  printf '    - %s\n' "${will_run_list[@]}"
  echo ""
} >> "$MANIFEST"

# Also mirror the manifest entry into stdout so nohup's log gets it.
tail -n $(( ${#will_run_list[@]} + 10 )) "$MANIFEST"

# Delegate to batch_orfs_only.sh for the actual work. Piping to tee keeps the
# console log and the sweep log both fresh.
cd "$BASE_DIR"
bash tools/batch_orfs_only.sh "$MAX_JOBS" "$ORFS_TIMEOUT" 2>&1 | tee -a "$SWEEP_LOG"
exit_code="${PIPESTATUS[0]}"

{
  echo "===== sweep ended: $(date '+%Y-%m-%d %H:%M:%S %Z') exit=$exit_code ====="
  echo ""
} >> "$MANIFEST"

exit "$exit_code"
