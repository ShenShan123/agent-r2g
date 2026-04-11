#!/usr/bin/env bash
set -euo pipefail

# Validation script for swerv/bp_multi_top bug fixes (2026-04-03)
# Validates:
#   1. bp_multi_top ORFS with increased die area (cfg3,cfg4,cfg6,cfg10)
#   2. swerv LVS with auto-timeout scaling (cfg1,cfg5,cfg7,cfg8)
#   3. bp_multi_top LVS with auto-timeout scaling (all 10 configs)

SCRIPTS_DIR="/data/shenshan/agent_with_openroad/skills/r2g-rtl2gds/scripts"
CASES_DIR="/data/shenshan/agent_with_openroad/design_cases"
LOG_DIR="/data/shenshan/agent_with_openroad/validation_2026-04-03"
mkdir -p "$LOG_DIR"

source /opt/openroad_tools_env.sh

echo "=== Validation started: $(date) ===" | tee "$LOG_DIR/validation.log"

# Helper: run a command and log result
run_and_log() {
  local label="$1"
  local log_file="$LOG_DIR/${label}.log"
  shift
  echo "[$(date +%H:%M:%S)] START $label" | tee -a "$LOG_DIR/validation.log"
  local start_ts=$(date +%s)
  local status=0
  "$@" > "$log_file" 2>&1 || status=$?
  local end_ts=$(date +%s)
  local elapsed=$((end_ts - start_ts))
  echo "[$(date +%H:%M:%S)] DONE  $label — exit=$status elapsed=${elapsed}s" | tee -a "$LOG_DIR/validation.log"
  echo "$label exit=$status elapsed=${elapsed}s" >> "$LOG_DIR/results.txt"
  return $status
}

# ============================================================
# Phase 1: bp_multi_top ORFS (2 at a time) + swerv LVS (2 at a time)
# ============================================================
echo "" | tee -a "$LOG_DIR/validation.log"
echo "=== Phase 1: bp_multi_top ORFS + swerv LVS ===" | tee -a "$LOG_DIR/validation.log"

# --- Batch 1a: bp_multi_top ORFS cfg3 + cfg10, swerv LVS cfg5 + cfg7 ---
run_and_log "bp_orfs_cfg3"  "$SCRIPTS_DIR/run_orfs.sh" "$CASES_DIR/bp_multi_top_cfg3"  nangate45 &
PID_ORFS3=$!
run_and_log "bp_orfs_cfg10" "$SCRIPTS_DIR/run_orfs.sh" "$CASES_DIR/bp_multi_top_cfg10" nangate45 &
PID_ORFS10=$!
run_and_log "sw_lvs_cfg5"   "$SCRIPTS_DIR/run_lvs.sh"  "$CASES_DIR/swerv_cfg5"         nangate45 &
PID_LVS5=$!
run_and_log "sw_lvs_cfg7"   "$SCRIPTS_DIR/run_lvs.sh"  "$CASES_DIR/swerv_cfg7"         nangate45 &
PID_LVS7=$!

# Wait for swerv LVS first (they're faster ~56 min vs ~2h for ORFS)
wait $PID_LVS5 || true
wait $PID_LVS7 || true

# --- Batch 1b: swerv LVS cfg1 + cfg8 (while ORFS still running) ---
run_and_log "sw_lvs_cfg1"   "$SCRIPTS_DIR/run_lvs.sh"  "$CASES_DIR/swerv_cfg1"         nangate45 &
PID_LVS1=$!
run_and_log "sw_lvs_cfg8"   "$SCRIPTS_DIR/run_lvs.sh"  "$CASES_DIR/swerv_cfg8"         nangate45 &
PID_LVS8=$!

wait $PID_LVS1 || true
wait $PID_LVS8 || true

# Wait for ORFS batch 1a to finish
wait $PID_ORFS3 || true
wait $PID_ORFS10 || true

# --- Batch 1c: bp_multi_top ORFS cfg4 + cfg6 ---
run_and_log "bp_orfs_cfg4"  "$SCRIPTS_DIR/run_orfs.sh" "$CASES_DIR/bp_multi_top_cfg4"  nangate45 &
PID_ORFS4=$!
run_and_log "bp_orfs_cfg6"  "$SCRIPTS_DIR/run_orfs.sh" "$CASES_DIR/bp_multi_top_cfg6"  nangate45 &
PID_ORFS6=$!

wait $PID_ORFS4 || true
wait $PID_ORFS6 || true

echo "" | tee -a "$LOG_DIR/validation.log"
echo "=== Phase 1 complete: $(date) ===" | tee -a "$LOG_DIR/validation.log"

# ============================================================
# Phase 2: bp_multi_top LVS for all 10 configs (2 at a time)
# ============================================================
echo "" | tee -a "$LOG_DIR/validation.log"
echo "=== Phase 2: bp_multi_top LVS (all 10 configs) ===" | tee -a "$LOG_DIR/validation.log"

for batch_start in 1 3 5 7 9; do
  batch_end=$((batch_start + 1))
  cfg_a="bp_multi_top_cfg${batch_start}"
  cfg_b="bp_multi_top_cfg${batch_end}"

  run_and_log "bp_lvs_cfg${batch_start}" "$SCRIPTS_DIR/run_lvs.sh" "$CASES_DIR/$cfg_a" nangate45 &
  PID_A=$!
  run_and_log "bp_lvs_cfg${batch_end}"   "$SCRIPTS_DIR/run_lvs.sh" "$CASES_DIR/$cfg_b" nangate45 &
  PID_B=$!

  wait $PID_A || true
  wait $PID_B || true
done

# ============================================================
# Summary
# ============================================================
echo "" | tee -a "$LOG_DIR/validation.log"
echo "=== Validation complete: $(date) ===" | tee -a "$LOG_DIR/validation.log"
echo "" | tee -a "$LOG_DIR/validation.log"
echo "--- Results Summary ---" | tee -a "$LOG_DIR/validation.log"
cat "$LOG_DIR/results.txt" | tee -a "$LOG_DIR/validation.log"

# Count passes/failures
TOTAL=$(wc -l < "$LOG_DIR/results.txt")
PASS=$(grep -c "exit=0" "$LOG_DIR/results.txt" || true)
FAIL=$((TOTAL - PASS))
echo "" | tee -a "$LOG_DIR/validation.log"
echo "TOTAL=$TOTAL PASS=$PASS FAIL=$FAIL" | tee -a "$LOG_DIR/validation.log"

if [[ $FAIL -eq 0 ]]; then
  echo "ALL VALIDATIONS PASSED" | tee -a "$LOG_DIR/validation.log"
else
  echo "SOME VALIDATIONS FAILED — check individual logs in $LOG_DIR/" | tee -a "$LOG_DIR/validation.log"
fi
