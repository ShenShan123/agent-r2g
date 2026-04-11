#!/usr/bin/env bash
set -euo pipefail

TOOLS=(python3 yosys iverilog vvp openroad)
OPTIONAL=(verilator klayout gtkwave sta opensta)

STATUS=0

echo "[required]"
for tool in "${TOOLS[@]}"; do
  if command -v "$tool" >/dev/null 2>&1; then
    printf 'ok  %s -> %s\n' "$tool" "$(command -v "$tool")"
  else
    printf 'miss %s\n' "$tool"
    STATUS=1
  fi
done

echo
echo "[optional]"
for tool in "${OPTIONAL[@]}"; do
  if command -v "$tool" >/dev/null 2>&1; then
    printf 'ok  %s -> %s\n' "$tool" "$(command -v "$tool")"
  else
    printf 'miss %s\n' "$tool"
  fi
done

echo
echo "[orfs]"
ORFS_ROOT="${ORFS_ROOT:-/opt/EDA4AI/OpenROAD-flow-scripts}"
if [[ -d "$ORFS_ROOT/flow" ]]; then
  printf 'ok  ORFS -> %s\n' "$ORFS_ROOT"
else
  printf 'miss ORFS at %s\n' "$ORFS_ROOT"
  STATUS=1
fi

echo
echo "[env]"
for var in OPENROAD_EXE YOSYS_EXE KLAYOUT_CMD STA_EXE; do
  val="${!var:-}"
  if [[ -n "$val" ]]; then
    printf 'ok  %s = %s\n' "$var" "$val"
  else
    printf 'unset %s\n' "$var"
  fi
done

exit "$STATUS"
