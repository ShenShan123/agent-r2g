#!/usr/bin/env bash
# run_stage.sh — unified teaching stage runner (TEACHING_POLICY §4)
#
#   bash run_stage.sh <stage> <design> [rtl_path]
#       <stage>    = 1 | 2 | 3 | 4
#       <design>   = design name (used for cases/<design> and design_cases/<design>)
#       [rtl_path] = original RTL dir (Stage 1 first run only)
#
# Env:
#   TEACHING_ROOT   dir containing TEACHING_POLICY.md (default: auto-detected upward)
#   REPO_ROOT       agent-r2g repo root (default: 3 levels up from this script)
#   AGENT_BACKEND   e.g. "codex/gpt-5.5" (recorded into the ledger)
#   DRY_RUN=1       print the flow commands instead of running EDA tools
#                   (ledger writes still happen so you can inspect them)
#
# What this script guarantees, regardless of tool outcome:
#   * every flow step it runs gets ONE ledger record (via append_ledger.py)
#   * the ledger is written by THIS script, never by the agent (policy §2.9)
#   * paths recorded are normalized by append_ledger.py
#
# WHERE TO PLUG IN: lines marked  # >>> FLOW  call the real SKILL flow scripts.
# They already match SKILL.md's documented signatures; adjust only if your
# repo's script names differ.

set -uo pipefail

# ─── resolve roots ───────────────────────────────────────────────────────────
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SELF/../../.." && pwd)}"     # scripts/teaching -> repo
SKILL_DIR="$REPO_ROOT/r2g-rtl2gds"
LEDGER="$SELF/../ledger/append_ledger.py"

detect_teaching_root() {
  local d="${TEACHING_ROOT:-$PWD}"
  while [ "$d" != "/" ]; do
    [ -f "$d/TEACHING_POLICY.md" ] && { echo "$d"; return 0; }
    d="$(dirname "$d")"
  done
  echo "${TEACHING_ROOT:-$PWD}"
}
TEACHING_ROOT="$(detect_teaching_root)"

STAGE="${1:?usage: run_stage.sh <stage> <design> [rtl_path]}"
DESIGN="${2:?usage: run_stage.sh <stage> <design> [rtl_path]}"
RTL_PATH="${3:-}"

PROJECT_DIR="$SKILL_DIR/design_cases/$DESIGN"          # SKILL.md工程目录
CASE_DIR="$TEACHING_ROOT/cases/$DESIGN"                # 教学产物目录
mkdir -p "$CASE_DIR"

DRY_RUN="${DRY_RUN:-0}"

log()  { printf '[run_stage] %s\n' "$*" >&2; }
die()  { printf '[run_stage][ERROR] %s\n' "$*" >&2; exit 1; }

# ─── ledger helper: record one flow step ─────────────────────────────────────
# usage: ledger_record <stage_str> <step> <cmd> <inputs_glob> <outputs_glob> <start> <end> <rc>
ledger_record() {
  local stage_str="$1" step="$2" cmd="$3" in_glob="$4" out_glob="$5" start="$6" end="$7" rc="$8"
  python3 "$LEDGER" \
    --teaching-root "$TEACHING_ROOT" \
    --repo-root     "$REPO_ROOT" \
    --design        "$DESIGN" \
    --stage         "$stage_str" \
    --step          "$step" \
    --command       "$cmd" \
    --inputs-glob   "$in_glob" \
    --outputs-glob  "$out_glob" \
    --start-ts      "$start" \
    --end-ts        "$end" \
    --exit-code     "$rc" \
    --triggered-by  "flow_script" \
    ${AGENT_BACKEND:+--agent-backend "$AGENT_BACKEND"} \
    >/dev/null || log "WARNING: ledger append failed for step=$step"
}

now() { date -u +%FT%TZ; }

# run a flow command, time it, and record it to the ledger
# usage: run_step <stage_str> <step> <inputs_glob> <outputs_glob> -- <command...>
run_step() {
  local stage_str="$1" step="$2" in_glob="$3" out_glob="$4"; shift 4
  [ "$1" = "--" ] && shift
  local start end rc; start="$(now)"
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY_RUN step=$step: $*"
    rc=0
  else
    "$@"; rc=$?
  fi
  end="$(now)"
  ledger_record "$stage_str" "$step" "$*" "$in_glob" "$out_glob" "$start" "$end" "$rc"
  return $rc
}

# ─── stages ──────────────────────────────────────────────────────────────────
stage1() {
  log "Stage 1: RTL -> lint -> sim -> synth ($DESIGN)"
  [ -n "$RTL_PATH" ] || log "no rtl_path given; assuming project already initialized"
  # >>> FLOW: init + prepare inputs (init_project copies layout; you populate rtl/tb)
  if [ -n "$RTL_PATH" ] && [ "$DRY_RUN" != "1" ]; then
    python3 "$SKILL_DIR/scripts/project/init_project.py" "$DESIGN" || true
  fi
  run_step stage1 lint \
    "$PROJECT_DIR/rtl/*.v" "$PROJECT_DIR/lint/lint.log" -- \
    bash "$SKILL_DIR/scripts/flow/run_lint.sh" "$PROJECT_DIR"            # >>> FLOW
  run_step stage1 simulation \
    "$PROJECT_DIR/tb/*.v" "$PROJECT_DIR/sim/sim.log" -- \
    bash "$SKILL_DIR/scripts/flow/run_sim.sh" "$PROJECT_DIR"             # >>> FLOW
  run_step stage1 synthesis \
    "$PROJECT_DIR/rtl/*.v,$PROJECT_DIR/constraints/config.mk" \
    "$PROJECT_DIR/synth/synth_output.v,$PROJECT_DIR/synth/synth.log" -- \
    bash "$SKILL_DIR/scripts/flow/run_synth.sh" "$PROJECT_DIR"           # >>> FLOW
  log "Stage 1 flow done. Agent: write STAGE1 report + CASE_STATE per policy §5/§7."
}

stage2() {
  log "Stage 2: synth -> ORFS -> GDS/DEF/ODB ($DESIGN)"
  run_step stage2 orfs_backend \
    "$PROJECT_DIR/constraints/config.mk,$PROJECT_DIR/constraints/constraint.sdc" \
    "$PROJECT_DIR/backend/RUN_*/final/*,$PROJECT_DIR/backend/RUN_*/results/*" -- \
    bash "$SKILL_DIR/scripts/flow/run_orfs.sh" "$PROJECT_DIR"            # >>> FLOW
  run_step stage2 timing_check \
    "$PROJECT_DIR/reports/ppa.json" "$PROJECT_DIR/reports/timing_check.json" -- \
    python3 "$SKILL_DIR/scripts/reports/check_timing.py" "$PROJECT_DIR" # >>> FLOW
  log "Stage 2 flow done. Handle timing tier per SKILL.md 5b; write STAGE2 report."
}

stage3() {
  log "Stage 3: post-GDS DRC/LVS/RCX ($DESIGN)"
  run_step stage3 drc_klayout \
    "$PROJECT_DIR/backend/RUN_*/final/*.gds" "$PROJECT_DIR/drc/*" -- \
    bash "$SKILL_DIR/scripts/flow/run_drc.sh" "$PROJECT_DIR" nangate45  # >>> FLOW
  run_step stage3 lvs_klayout \
    "$PROJECT_DIR/backend/RUN_*/final/*.gds" "$PROJECT_DIR/lvs/*" -- \
    bash "$SKILL_DIR/scripts/flow/run_lvs.sh" "$PROJECT_DIR" nangate45  # >>> FLOW
  run_step stage3 rcx_openrcx \
    "$PROJECT_DIR/backend/RUN_*/final/*.odb" "$PROJECT_DIR/rcx/6_final.spef" -- \
    bash "$SKILL_DIR/scripts/flow/run_rcx.sh" "$PROJECT_DIR" nangate45  # >>> FLOW
  log "Stage 3 flow done. Write STAGE3 report with DRC/LVS/RCX sub-statuses."
}

stage4() {
  log "Stage 4: labels (Part A) + graph features (Part B) ($DESIGN)"
  mkdir -p "$CASE_DIR/stage4_labels" "$CASE_DIR/stage4_features"

  LABEL_ROOT="$SKILL_DIR/scripts/extract/labels"
  FEATURE_ROOT="$SKILL_DIR/scripts/extract/features"
  LABELS_OUT="$CASE_DIR/stage4_labels"

  # Resolve real artifact paths from CASE_STATE.md (def_path / odb_path / spef_path).
  cs="$CASE_DIR/CASE_STATE.md"
  get_cs() { [ -f "$cs" ] && sed -n "s/^$1:[[:space:]]*//p" "$cs" | tail -1; }
  DEF_PATH="${DEF_PATH:-$(get_cs def_path)}"
  ODB_PATH="${ODB_PATH:-$(get_cs odb_path)}"

  # --- resolve nangate45 liberty (R2G_LIB_FILES) -------------------------------
  # Without liberty, timing has no cell delays (slack -> INF) and IR-drop has no
  # current data (IR drop -> 0). These are REAL degradations of the inputs, not
  # the label algorithms failing — so we locate the liberty and pass it through.
  resolve_liberty() {
    [ -n "${R2G_LIB_FILES:-}" ] && { echo "$R2G_LIB_FILES"; return; }
    local cands=(
      "$REPO_ROOT/OpenROAD-flow-scripts/flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib"
      "${ORFS_ROOT:-}/flow/platforms/nangate45/lib/NangateOpenCellLibrary_typical.lib"
    )
    local c
    for c in "${cands[@]}"; do [ -f "$c" ] && { echo "$c"; return; }; done
    # last resort: search the repo tree
    c="$(find "$REPO_ROOT" -name 'NangateOpenCellLibrary_typical.lib' 2>/dev/null | head -1)"
    [ -n "$c" ] && { echo "$c"; return; }
    echo ""   # not found
  }
  LIB_FILES="$(resolve_liberty)"
  if [ -z "$LIB_FILES" ]; then
    log "WARNING: nangate45 liberty not found. timing slack will be INF and IR-drop 0."
    log "         Set R2G_LIB_FILES=/path/to/NangateOpenCellLibrary_typical.lib to fix."
  fi

  # --- parse clock port + period from the design SDC ---------------------------
  # timing.tcl only auto-detects ports named clk/clock; designs whose clock is
  # named e.g. CK, PCLK, S_APB_PCLK need CLOCK_PORT passed explicitly or slack
  # stays INF. Parse it (and the period) from the design's constraint.sdc.
  SDC_FILE="${SDC_FILE:-$PROJECT_DIR/constraints/constraint.sdc}"
  CLOCK_PORT=""; CLOCK_PERIOD=""
  if [ -f "$SDC_FILE" ]; then
    # ports: collect every [get_ports <name>] target on create_clock lines
    CLOCK_PORT="$(grep -E 'create_clock' "$SDC_FILE" \
      | sed -n 's/.*get_ports[[:space:]]*[[{]*\([A-Za-z0-9_]\+\).*/\1/p' \
      | sort -u | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
    # period: first -period value
    CLOCK_PERIOD="$(grep -E 'create_clock' "$SDC_FILE" \
      | sed -n 's/.*-period[[:space:]]\+\([0-9.]\+\).*/\1/p' | head -1)"
  fi
  [ -n "$CLOCK_PORT" ] && log "clock port(s): $CLOCK_PORT  period: ${CLOCK_PERIOD:-<unset>}" \
    || log "no clock found in SDC (combinational design? slack INF is then correct)"

  SUPPLY_VOLTAGE="${SUPPLY_VOLTAGE:-1.1}"   # nangate45 rail

  # The four label scripts FORCE the canonical basename, so even if these paths
  # were wrong-named the output still lands correctly. We pass canonical names
  # anyway for clarity.  Each call goes through run_step -> ledger.
  run_step stage4 label_wirelength \
    "$DEF_PATH" "$LABELS_OUT/wirelength.csv" -- \
    python3 "$LABEL_ROOT/extract_wirelength.py" "$DEF_PATH" "$LABELS_OUT/wirelength.csv" "$DESIGN"   # >>> FLOW

  run_step stage4 label_congestion \
    "$DEF_PATH" "$LABELS_OUT/cell_congestion.csv" -- \
    env ${TECH_LEF:+TECH_LEF="$TECH_LEF"} \
    python3 "$LABEL_ROOT/extract_congestion.py" "$DEF_PATH" "$LABELS_OUT/cell_congestion.csv" "$DESIGN"  # >>> FLOW

  run_step stage4 label_timing \
    "$ODB_PATH,$DEF_PATH" "$LABELS_OUT/timing_features.csv" -- \
    env OUTPUT_CSV="$LABELS_OUT/timing_features.csv" ODB_FILE="$ODB_PATH" \
        DEF_FILE="$DEF_PATH" DESIGN_NAME="$DESIGN" \
        ${LIB_FILES:+R2G_LIB_FILES="$LIB_FILES"} \
        ${CLOCK_PORT:+CLOCK_PORT="$CLOCK_PORT"} \
        ${CLOCK_PERIOD:+CLOCK_PERIOD="$CLOCK_PERIOD"} \
        openroad "$LABEL_ROOT/extract_timing.tcl"                       # >>> FLOW

  run_step stage4 label_irdrop \
    "$ODB_PATH,$DEF_PATH" "$LABELS_OUT/ir_drop.csv" -- \
    env OUTPUT_RPT="$LABELS_OUT/ir_drop.csv" ODB_FILE="$ODB_PATH" \
        DEF_FILE="$DEF_PATH" DESIGN_NAME="$DESIGN" \
        SUPPLY_VOLTAGE="$SUPPLY_VOLTAGE" \
        ${LIB_FILES:+R2G_LIB_FILES="$LIB_FILES"} \
        ${IRDROP_THRESHOLD_MV:+IRDROP_THRESHOLD_MV="$IRDROP_THRESHOLD_MV"} \
        openroad "$LABEL_ROOT/extract_irdrop.tcl"                       # >>> FLOW

  # --- Part B: graph features ---------------------------------------------
  # Feature scripts live in $FEATURE_ROOT with their own input/<case> &
  # output/<case> layout (see case_paths.py) and are hardcoded to nangate45.
  # Steps: (1) stage 5_route.def / 6_final.spef / constraint.sdc / config.mk
  # into <feature_tool_root>/input/$DESIGN/; (2) copy nangate liberty + tech LEF
  # into <feature_tool_root>/input/; (3) run the feature extraction; (4) copy
  # the 8 CSVs into $CASE_DIR/stage4_features/.
  #
  # ENTRY POINT PENDING CONFIRMATION: if $FEATURE_ROOT has a unified runner
  # (e.g. run_all.py) wire it here; otherwise run metadata.py / nodes_*.py /
  # edges_*.py per their real signatures. Do NOT invent an entry point.
  #
  #   run_step stage4 feature_extract \
  #     "<feature_tool_root>/input/$DESIGN/*" "<feature_tool_root>/output/$DESIGN/*.csv" -- \
  #     python3 "$FEATURE_ROOT/<entrypoint>" "$DESIGN"
  log "Part A (labels) done via run_step. Part B entry point needs confirming — see comments."
}

case "$STAGE" in
  1) stage1 ;;
  2) stage2 ;;
  3) stage3 ;;
  4) stage4 ;;
  *) die "invalid stage: $STAGE (expected 1|2|3|4)" ;;
esac

log "done. ledger at: $TEACHING_ROOT/run_ledger.jsonl"
