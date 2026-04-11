#!/usr/bin/env bash
set -uo pipefail

# Re-run remaining failure cases (cfg5-10 of all families + bp_multi_top cfg2-4 + LVS retries)
MAX_JOBS="${1:-4}"
ORFS_TIMEOUT="${2:-3600}"
BASE_DIR="/data/shenshan/agent_with_openroad"
CASES_DIR="$BASE_DIR/design_cases"
SKILL_SCRIPTS_DIR="$BASE_DIR/skills/r2g-rtl2gds/scripts"
RESULTS_FILE="$BASE_DIR/rerun_remaining_results.jsonl"

source /opt/openroad_tools_env.sh

> "$RESULTS_FILE"

run_full() {
  local case_dir="$1"
  local case_name
  case_name=$(basename "$case_dir")
  local config_mk="$case_dir/constraints/config.mk"
  local platform
  platform=$(grep 'PLATFORM' "$config_mk" | head -1 | sed 's/.*=\s*//' | tr -d ' ')
  local design_name
  design_name=$(grep 'DESIGN_NAME' "$config_mk" | head -1 | sed 's/.*=\s*//' | tr -d ' ')
  local log_dir="$case_dir/batch_logs"
  mkdir -p "$log_dir"

  echo "[$(date '+%H:%M:%S')] START $case_name ($design_name)"

  local lock_dir="/tmp/orfs_locks"
  mkdir -p "$lock_dir"
  local lock_file="$lock_dir/${design_name}.lock"

  local orfs_exit=1 lvs_exit=-1 rcx_exit=-1
  (
    flock -x 200

    ORFS_TIMEOUT="$ORFS_TIMEOUT" timeout --signal=TERM --kill-after=60 "$((ORFS_TIMEOUT * 6))" \
      bash "$SKILL_SCRIPTS_DIR/run_orfs.sh" "$case_dir" "$platform" > "$log_dir/orfs.log" 2>&1
    orfs_exit=$?

    if [[ $orfs_exit -eq 0 ]]; then
      LVS_TIMEOUT=3600 timeout --signal=TERM --kill-after=60 3660 \
        bash "$SKILL_SCRIPTS_DIR/run_lvs.sh" "$case_dir" "$platform" > "$log_dir/lvs.log" 2>&1
      lvs_exit=$?

      RCX_TIMEOUT=3600 timeout --signal=TERM --kill-after=60 3660 \
        bash "$SKILL_SCRIPTS_DIR/run_rcx.sh" "$case_dir" "$platform" > "$log_dir/rcx.log" 2>&1
      rcx_exit=$?
    fi

    echo "$orfs_exit $lvs_exit $rcx_exit" > "$log_dir/exit_codes.txt"
  ) 200>"$lock_file"

  local codes
  codes=$(cat "$log_dir/exit_codes.txt" 2>/dev/null || echo "1 -1 -1")
  orfs_exit=$(echo "$codes" | awk '{print $1}')
  lvs_exit=$(echo "$codes" | awk '{print $2}')
  rcx_exit=$(echo "$codes" | awk '{print $3}')

  local result="{\"case\": \"$case_name\", \"design\": \"$design_name\", \"platform\": \"$platform\""
  if [[ "$orfs_exit" -eq 0 ]]; then
    result="$result, \"orfs\": \"pass\""
    result="$result, \"lvs\": \"$([[ $lvs_exit -eq 0 ]] && echo pass || echo "fail($lvs_exit)")\""
    result="$result, \"rcx\": \"$([[ $rcx_exit -eq 0 ]] && echo pass || echo "fail($rcx_exit)")\""
  else
    result="$result, \"orfs\": \"fail($orfs_exit)\", \"lvs\": \"skipped\", \"rcx\": \"skipped\""
  fi
  result="$result}"
  echo "$result" >> "$RESULTS_FILE"
  echo "[$(date '+%H:%M:%S')] DONE  $case_name — ORFS:$([[ $orfs_exit -eq 0 ]] && echo pass || echo "FAIL($orfs_exit)") LVS:$([[ $lvs_exit -eq 0 ]] && echo pass || echo "fail($lvs_exit)") RCX:$([[ $rcx_exit -eq 0 ]] && echo pass || echo "fail($rcx_exit)")"
}

run_lvs_only() {
  local case_dir="$1"
  local case_name
  case_name=$(basename "$case_dir")
  local config_mk="$case_dir/constraints/config.mk"
  local platform
  platform=$(grep 'PLATFORM' "$config_mk" | head -1 | sed 's/.*=\s*//' | tr -d ' ')
  local design_name
  design_name=$(grep 'DESIGN_NAME' "$config_mk" | head -1 | sed 's/.*=\s*//' | tr -d ' ')
  local log_dir="$case_dir/batch_logs"
  mkdir -p "$log_dir"

  echo "[$(date '+%H:%M:%S')] LVS-RETRY $case_name ($design_name)"

  local lock_dir="/tmp/orfs_locks"
  mkdir -p "$lock_dir"
  local lock_file="$lock_dir/${design_name}.lock"

  local lvs_exit=1
  (
    flock -x 200
    LVS_TIMEOUT=7200 timeout --signal=TERM --kill-after=60 7260 \
      bash "$SKILL_SCRIPTS_DIR/run_lvs.sh" "$case_dir" "$platform" > "$log_dir/lvs_retry.log" 2>&1
    lvs_exit=$?
    echo "$lvs_exit" > "$log_dir/lvs_retry_exit.txt"
  ) 200>"$lock_file"

  lvs_exit=$(cat "$log_dir/lvs_retry_exit.txt" 2>/dev/null || echo 1)

  local result="{\"case\": \"$case_name\", \"design\": \"$design_name\", \"platform\": \"$platform\""
  result="$result, \"orfs\": \"pass\""
  result="$result, \"lvs\": \"$([[ $lvs_exit -eq 0 ]] && echo pass || echo "fail($lvs_exit)")\""
  result="$result, \"rcx\": \"pass\"}"
  echo "$result" >> "$RESULTS_FILE"
  echo "[$(date '+%H:%M:%S')] DONE  $case_name — LVS:$([[ $lvs_exit -eq 0 ]] && echo pass || echo "FAIL($lvs_exit)")"
}

export -f run_full run_lvs_only
export ORFS_TIMEOUT SKILL_SCRIPTS_DIR RESULTS_FILE

# Build the case lists
FAMILIES=(bp_multi_top riscv32i tinyRocket aes_xcrypt ibex swerv vga_enh_top)

# LVS-only retries (ORFS already passed, LVS timed out at 1800s)
LVS_RETRY_CASES=(
  "$CASES_DIR/bp_multi_top_cfg1"
  "$CASES_DIR/swerv_cfg1"
  "$CASES_DIR/swerv_cfg2"
)

# Full ORFS runs - interleaved by cfg suffix
declare -a FULL_CASES
for suffix in 5 6 7 8 9 10 2 3 4; do
  for family in "${FAMILIES[@]}"; do
    dir="$CASES_DIR/${family}_cfg${suffix}"
    if [[ -d "$dir" ]]; then
      # Skip cases that already passed in the previous run
      case_name="${family}_cfg${suffix}"
      if grep -q "\"case\": \"$case_name\".*\"orfs\": \"pass\".*\"lvs\": \"pass\".*\"rcx\": \"pass\"" "$BASE_DIR/rerun_results.jsonl" 2>/dev/null; then
        continue
      fi
      FULL_CASES+=("$dir")
    fi
  done
done

TOTAL=$((${#LVS_RETRY_CASES[@]} + ${#FULL_CASES[@]}))
echo "================================================================"
echo "Re-run remaining: $TOTAL cases ($MAX_JOBS parallel)"
echo "  LVS retries: ${#LVS_RETRY_CASES[@]} (7200s timeout)"
echo "  Full ORFS: ${#FULL_CASES[@]} (${ORFS_TIMEOUT}s/stage, $((ORFS_TIMEOUT*6))s outer)"
echo "Results: $RESULTS_FILE"
echo "================================================================"
echo ""

# Start LVS retries first (they're quick, just LVS)
for case_dir in "${LVS_RETRY_CASES[@]}"; do
  while [[ $(jobs -r | wc -l) -ge $MAX_JOBS ]]; do sleep 5; done
  run_lvs_only "$case_dir" &
done

# Then full ORFS runs
for case_dir in "${FULL_CASES[@]}"; do
  while [[ $(jobs -r | wc -l) -ge $MAX_JOBS ]]; do sleep 5; done
  run_full "$case_dir" &
done

wait

echo ""
echo "================================================================"
echo "All done. Generating summary..."
echo "================================================================"

TOTAL_R=$(wc -l < "$RESULTS_FILE")
PASS=$(grep -c '"orfs": "pass".*"lvs": "pass".*"rcx": "pass"' "$RESULTS_FILE" || true)
echo "Results: $TOTAL_R completed, $PASS all-pass"
grep '"fail' "$RESULTS_FILE" | python3 -c "
import sys,json
for l in sys.stdin:
    r = json.loads(l)
    o,l2,rx = r.get('orfs','?'),r.get('lvs','?'),r.get('rcx','?')
    if 'fail' in o or 'fail' in l2 or 'fail' in str(rx):
        print(f'  FAIL: {r[\"case\"]}: ORFS={o}, LVS={l2}, RCX={rx}')
" 2>/dev/null || true
