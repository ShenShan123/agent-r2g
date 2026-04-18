#!/usr/bin/env bash
# One-shot progress snapshot for the currently running full sweep.
# Usage:
#   tools/sweep_status.sh           — latest sweep
#   tools/sweep_status.sh <jsonl>   — specific sweep results file
#
# Reports: wrapper PID alive?, designs run so far, pass/fail, rtl_error
# contexts captured, last 5 lines of the sweep log.

set -uo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BATCH_DIR="$BASE_DIR/design_cases/_batch"

# Pick results file: arg 1 wins; otherwise newest orfs_sweep_*.jsonl.
if [[ $# -ge 1 ]]; then
  RESULTS="$1"
else
  RESULTS="$(ls -1t "$BATCH_DIR"/orfs_sweep_*.jsonl 2>/dev/null | head -1 || true)"
fi

if [[ -z "${RESULTS:-}" || ! -f "$RESULTS" ]]; then
  echo "no sweep results file found under $BATCH_DIR" >&2
  exit 1
fi

STAMP_TAG="$(basename "$RESULTS" | sed -E 's/orfs_sweep_([^.]+)\.jsonl/\1/')"
SWEEP_LOG="$BATCH_DIR/logs/sweep_${STAMP_TAG}.log"

# Pull wrapper PID from the manifest (last block for this stamp).
WRAPPER_PID="$(awk -v r="$RESULTS" '
  $0 ~ /sweep started:/ { pid="" }
  $1=="pid" && $2=="(this" { pid=$NF }
  $0 ~ "results_file: "r { chosen=pid }
  END { print chosen }
' "$BATCH_DIR/sweep_manifest.txt" 2>/dev/null)"

echo "=== sweep progress — $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "results:  $RESULTS"
echo "log:      $SWEEP_LOG"

if [[ -n "$WRAPPER_PID" ]]; then
  if ps -p "$WRAPPER_PID" >/dev/null 2>&1; then
    ELAPSED="$(ps -p "$WRAPPER_PID" -o etime= | tr -d ' ')"
    echo "wrapper:  PID $WRAPPER_PID alive (elapsed $ELAPSED)"
  else
    echo "wrapper:  PID $WRAPPER_PID — FINISHED / NOT RUNNING"
  fi
else
  echo "wrapper:  PID unknown (no manifest match)"
fi

# Counts from the jsonl. Skips "cached" entries (those are designs already
# successful before this sweep started). grep -c always prints a number and
# exits 1 on zero matches; pipe through `head -1` + `|| true` to avoid doubling.
count_lines() { grep -c "$1" "$RESULTS" 2>/dev/null; true; }
TOTAL_LINES="$(wc -l < "$RESULTS" 2>/dev/null | head -1)"
TOTAL_LINES="${TOTAL_LINES:-0}"
CACHED="$(count_lines '"status": "cached"' | head -1)"
NO_CONFIG="$(count_lines '"status": "skip"' | head -1)"
FAILED="$(count_lines '"orfs": "fail' | head -1)"
PASSED="$(grep '"orfs": "pass"' "$RESULTS" 2>/dev/null | grep -v '"status": "cached"' | wc -l | head -1)"

echo ""
echo "results so far:"
echo "  total entries:         $TOTAL_LINES  (cached=$CACHED, no-config=$NO_CONFIG)"
echo "  fresh ORFS pass:       $PASSED"
echo "  fresh ORFS fail:       $FAILED"

# rtl_error contexts captured (today and in history).
if compgen -G "$BASE_DIR/design_cases/*/_batch/rtl_error_context.json" >/dev/null 2>&1; then
  CTX_TOTAL=$(ls -1 "$BASE_DIR"/design_cases/*/_batch/rtl_error_context.json 2>/dev/null | wc -l)
else
  CTX_TOTAL=0
fi
# Today-only contexts (mtime within the current calendar day)
if [[ $CTX_TOTAL -gt 0 ]]; then
  CTX_TODAY=$(find "$BASE_DIR"/design_cases/*/_batch/rtl_error_context.json -maxdepth 0 -daystart -mtime 0 2>/dev/null | wc -l)
else
  CTX_TODAY=0
fi

echo ""
echo "RTL error contexts:"
echo "  total (all-time):      $CTX_TOTAL"
echo "  captured today:        $CTX_TODAY"

if [[ -f "$SWEEP_LOG" ]]; then
  echo ""
  echo "--- last 5 log lines ---"
  tail -n 5 "$SWEEP_LOG"
fi

# Show currently running ORFS child processes (if any) at a glance.
RUNNING=$(pgrep -a -f 'run_orfs.sh|/make ' 2>/dev/null | wc -l)
if [[ "$RUNNING" -gt 0 ]]; then
  echo ""
  echo "--- live ORFS processes ($RUNNING) ---"
  pgrep -a -f 'run_orfs.sh' 2>/dev/null | head -8
fi
