#!/usr/bin/env bash
# Helper (not a tier): materialize platform DRC/LVS/antenna rule decks into the
# ORFS checkout. Upstream ORFS ships no LVS rule for nangate45 and no antenna
# model in its tech LEF, so `make lvs` silently skips and repair_antennas is inert.
# This dispatches to the repo's idempotent, backup-aware nangate45 rule installers
# when they are reachable (they live in the agent-r2g repo `tools/`, not in the
# installed skill). Best-effort: a missing installer prints a HINT, never fails.
set -uo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/../flow/_env.sh" 1>&2
# shellcheck source=/dev/null
source "$HERE/_setup_lib.sh"
setup_parse "$@"

# eda-install/scripts/setup → r2g-skills (../../..) → repo root (../../../..)
_repo="$(cd -- "$HERE/../../../.." 2>/dev/null && pwd || true)"
_found=0
for _base in "${R2G_TOOLS_DIR:-}" "$_repo/tools"; do
  [[ -z "$_base" || ! -d "$_base" ]] && continue
  for _rule in install_nangate45_lvs.sh install_nangate45_drc.sh install_nangate45_antenna.sh; do
    if [[ -f "$_base/$_rule" ]]; then
      log "nangate45 rules: $_rule"
      run bash "$_base/$_rule" || hint "$_rule returned non-zero (deck left unchanged)"
      _found=1
    fi
  done
  # sky130hs klayout lefdef repair (failure-patterns.md #33): this ORFS ships
  # sky130hs.lyt with LEGACY lefdef reader options, so def2stream silently drops
  # ALL DEF geometry from the merged GDS (portless magic extraction -> every
  # Netgen LVS a false top-pin mismatch). Idempotent; backs up .orig.
  if [[ -f "$_base/patch_sky130hs_lyt.py" ]]; then
    log "sky130hs lyt lefdef patch: patch_sky130hs_lyt.py"
    run python3 "$_base/patch_sky130hs_lyt.py" || hint "patch_sky130hs_lyt.py returned non-zero (lyt unchanged)"
    _found=1
  fi
  [[ "$_found" == "1" ]] && break
done

if [[ "$_found" != "1" ]]; then
  hint "platform rule installers not found (expected in the agent-r2g repo tools/ — set R2G_TOOLS_DIR); nangate45 LVS/antenna decks + sky130hs lyt unchanged"
fi
