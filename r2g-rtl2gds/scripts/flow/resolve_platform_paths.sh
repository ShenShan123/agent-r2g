#!/usr/bin/env bash
set -euo pipefail

# usage: resolve_platform_paths.sh <config.mk> <platform>
# Emits KEY=VALUE lines on stdout:
#   LIB_FILES TECH_LEF SC_LEF ADDITIONAL_LIBS ADDITIONAL_LEFS SUPPLY_VOLTAGE
# Primary source: ORFS Makefile variable expansion (handles corner-built vars on
# asap7/gf180). Fallback: glob the platform dir + a per-platform voltage map.
# See references/label-extraction.md.

CONFIG_MK="${1:-}"
PLATFORM="${2:-nangate45}"

# Absolutize CONFIG_MK now: the Make invocation below runs after `cd "$FLOW_DIR"`,
# so a relative DESIGN_CONFIG would point at the wrong file (and silently break
# corner-built vars like asap7's LIB_FILES).
if [[ -n "$CONFIG_MK" && -f "$CONFIG_MK" ]]; then
  CONFIG_MK="$(cd "$(dirname "$CONFIG_MK")" && pwd)/$(basename "$CONFIG_MK")"
fi

# shellcheck source=/dev/null
# Redirect _env.sh's diagnostic chatter to stderr so this script's stdout stays a
# clean KEY=VALUE contract for consumers that capture it wholesale.
source "$(dirname "${BASH_SOURCE[0]}")/_env.sh" 1>&2

PLATFORM_DIR="$FLOW_DIR/platforms/$PLATFORM"

LIB_FILES=""; TECH_LEF=""; SC_LEF=""; ADDITIONAL_LIBS=""; ADDITIONAL_LEFS=""; PWR=""

# --- Primary: ask ORFS Make to expand the variables -------------------------
if [[ -n "$CONFIG_MK" && -f "$CONFIG_MK" && -f "$FLOW_DIR/Makefile" ]]; then
  # ORFS Makefile uses SCRIPTS_DIR internally; an inherited value breaks it.
  unset SCRIPTS_DIR || true
  DUMP="$(cd "$FLOW_DIR" && make -f Makefile \
      DESIGN_CONFIG="$CONFIG_MK" PLATFORM="$PLATFORM" \
      --eval='__r2g_dump: ; @printf "%s\n" "LIB_FILES=$(LIB_FILES)" "TECH_LEF=$(TECH_LEF)" "SC_LEF=$(SC_LEF)" "ADDITIONAL_LIBS=$(ADDITIONAL_LIBS)" "ADDITIONAL_LEFS=$(ADDITIONAL_LEFS)" "PWR_NETS_VOLTAGES=$(PWR_NETS_VOLTAGES)"' \
      __r2g_dump 2>/dev/null || true)"
  while IFS= read -r line; do
    case "$line" in
      LIB_FILES=*)         LIB_FILES="${line#LIB_FILES=}" ;;
      TECH_LEF=*)          TECH_LEF="${line#TECH_LEF=}" ;;
      SC_LEF=*)            SC_LEF="${line#SC_LEF=}" ;;
      ADDITIONAL_LIBS=*)   ADDITIONAL_LIBS="${line#ADDITIONAL_LIBS=}" ;;
      ADDITIONAL_LEFS=*)   ADDITIONAL_LEFS="${line#ADDITIONAL_LEFS=}" ;;
      PWR_NETS_VOLTAGES=*) PWR="${line#PWR_NETS_VOLTAGES=}" ;;
    esac
  done <<< "$DUMP"
fi

# Validate the primary LIB_FILES actually exist; else trigger the fallback.
_first_existing_lib=""
for l in $LIB_FILES; do [[ -f "$l" ]] && { _first_existing_lib="$l"; break; }; done

# --- Fallback: glob the platform dir ----------------------------------------
if [[ -z "$_first_existing_lib" ]]; then
  for pat in '*typical*.lib' '*__tt*.lib' '*_tt_*.lib' '*tt*.lib' '*.lib'; do
    found=$(ls -1 "$PLATFORM_DIR"/lib/$pat 2>/dev/null | grep -v 'fakeram' | head -1 || true)
    [[ -n "$found" ]] && { LIB_FILES="$found"; break; }
  done
fi
if [[ -z "$TECH_LEF" || ! -f "$TECH_LEF" ]]; then
  for pat in '*tech*.lef' '*.tlef' '*.tech.lef'; do
    found=$(ls -1 "$PLATFORM_DIR"/lef/$pat 2>/dev/null | head -1 || true)
    [[ -n "$found" ]] && { TECH_LEF="$found"; break; }
  done
fi

# --- Supply voltage ---------------------------------------------------------
# Parse "VDD <v> ..." from PWR_NETS_VOLTAGES; else per-platform default.
SUPPLY_VOLTAGE=""
if [[ -n "$PWR" ]]; then
  SUPPLY_VOLTAGE=$(echo "$PWR" | tr -d '"' | awk '{print $2}')
fi
case "$SUPPLY_VOLTAGE" in
  ''|*[!0-9.]*)
    case "$PLATFORM" in
      nangate45)         SUPPLY_VOLTAGE=1.1 ;;
      sky130hd|sky130hs) SUPPLY_VOLTAGE=1.8 ;;
      asap7)             SUPPLY_VOLTAGE=0.70 ;;
      gf180)             SUPPLY_VOLTAGE=5.0 ;;
      ihp-sg13g2)        SUPPLY_VOLTAGE=1.2 ;;
      *)                 SUPPLY_VOLTAGE=1.0 ;;
    esac
    ;;
esac

printf "LIB_FILES=%s\n" "$LIB_FILES"
printf "TECH_LEF=%s\n" "$TECH_LEF"
printf "SC_LEF=%s\n" "$SC_LEF"
printf "ADDITIONAL_LIBS=%s\n" "$ADDITIONAL_LIBS"
printf "ADDITIONAL_LEFS=%s\n" "$ADDITIONAL_LEFS"
printf "SUPPLY_VOLTAGE=%s\n" "$SUPPLY_VOLTAGE"
