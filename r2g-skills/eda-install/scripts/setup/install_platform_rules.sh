#!/usr/bin/env bash
# Helper (not a tier): materialize platform DRC/LVS/antenna rule decks into the
# ORFS checkout. Upstream ORFS ships no LVS rule for nangate45 and no antenna
# model in its tech LEF, so `make lvs` silently skips and repair_antennas is inert.
# This dispatches to the repo's idempotent, backup-aware nangate45 rule installers
# when they are reachable (they live in the agent-r2g repo `tools/`, not in the
# installed skill).
#
# Selection model (RMD2-P1-01, three-platform pilot 2026-07-24):
#   * SELECTED strict platforms (R2G_STRICT_PLATFORMS env or `--platforms a,b`)
#     are FAIL-CLOSED: a missing installer, a non-zero installer, a failed
#     postcondition/canary, or a post-install `platform_capability.py --strict`
#     failure is a FATAL setup error — a fresh host can no longer "finish"
#     setup while requested strict-signoff collateral is absent. The capability
#     verdict + collateral digests are saved to references/install_manifest.json.
#   * UNSELECTED platforms keep the old best-effort behavior (HINT, no failure)
#     — with the standing RMD-P0-04 exception: a PRESENT-but-broken sky130hs
#     .lyt repair always fails setup, because every sky130hs LVS verdict
#     downstream of a legacy .lyt is invalid.
# Repeated installation is idempotent: the underlying installers are
# backup-aware no-ops when already applied, and the manifest is rewritten.
set -uo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/../flow/_env.sh" 1>&2
# shellcheck source=/dev/null
source "$HERE/_setup_lib.sh"
setup_parse "$@"

# ---- strict-platform selection ------------------------------------------------
STRICT_PLATFORMS="${R2G_STRICT_PLATFORMS:-}"
_i=0
while [[ $_i -lt ${#SETUP_REST[@]} ]]; do
  case "${SETUP_REST[$_i]}" in
    --platforms)   STRICT_PLATFORMS="${SETUP_REST[$((_i+1))]:-}"; _i=$((_i+2)) ;;
    --platforms=*) STRICT_PLATFORMS="${SETUP_REST[$_i]#--platforms=}"; _i=$((_i+1)) ;;
    *)             _i=$((_i+1)) ;;
  esac
done
STRICT_PLATFORMS="${STRICT_PLATFORMS//,/ }"
is_selected() { [[ " $STRICT_PLATFORMS " == *" $1 "* ]]; }
[[ -n "$STRICT_PLATFORMS" ]] && log "strict (fail-closed) platforms: $STRICT_PLATFORMS"

# req_or_hint SELECTED_P MSG — die when platform is strict-selected, else hint.
req_or_hint() {
  local p="$1"; shift
  if is_selected "$p"; then die "$* (platform '$p' is strict-selected — fail closed, RMD2-P1-01)"; fi
  hint "$*"
}

# eda-install/scripts/setup → r2g-skills (../../..) → repo root (../../../..)
_repo="$(cd -- "$HERE/../../../.." 2>/dev/null && pwd || true)"
_found=0
declare -A _NG45_DONE=()
for _base in "${R2G_TOOLS_DIR:-}" "$_repo/tools"; do
  [[ -z "$_base" || ! -d "$_base" ]] && continue
  for _rule in install_nangate45_lvs.sh install_nangate45_drc.sh install_nangate45_antenna.sh; do
    if [[ -f "$_base/$_rule" ]]; then
      log "nangate45 rules: $_rule"
      if run bash "$_base/$_rule"; then
        _NG45_DONE["$_rule"]=1
      else
        req_or_hint nangate45 "$_rule returned non-zero (deck left unchanged)"
      fi
      _found=1
    fi
  done
  # sky130hs klayout lefdef repair (failure-patterns.md #33 / RMD-P0-04): this
  # ORFS ships sky130hs.lyt with LEGACY lefdef reader options, so def2stream
  # maps every DEF-derived shape to unmappable legacy layer numbers (portless
  # magic extraction -> every Netgen LVS a false top-pin mismatch). Idempotent;
  # backs up .orig. Unlike the other rule installers this one is a REQUIRED,
  # verified postcondition when the checkout ships sky130hs: the three-platform
  # pilot proved a best-effort hint lets an unpatched .lyt silently invalidate
  # every sky130hs LVS while ENV stays green.
  if [[ -f "$_base/patch_sky130hs_lyt.py" ]]; then
    log "sky130hs lyt lefdef patch: patch_sky130hs_lyt.py"
    run python3 "$_base/patch_sky130hs_lyt.py" \
      || req_or_hint sky130hs "patch_sky130hs_lyt.py returned non-zero (lyt unchanged)"
    _found=1
    if [[ "$DRY" != "1" && -f "${FLOW_DIR:-}/platforms/sky130hs/sky130hs.lyt" ]]; then
      if ! python3 "$_base/patch_sky130hs_lyt.py" --check; then
        _SKY130HS_POSTCOND_FAIL=1
        hint "sky130hs.lyt POSTCONDITION FAILED: legacy lefdef options still live — sky130hs GDS/LVS unusable until repaired (RMD-P0-04)"
      elif [[ -f "$_base/sky130hs_gds_canary.py" ]]; then
        # Geometry canary: prove the DEF->GDS import path end-to-end (a green
        # --check trusts option NAMES; the canary checks actual layer numbers).
        # klayout absent -> rc 3 -> soft hint (the merge can't run either) —
        # unless sky130hs is strict-selected, where an UNVERIFIABLE
        # postcondition is as fatal as a failed one (RMD2-P1-01).
        python3 "$_base/sky130hs_gds_canary.py" --flow-dir "${FLOW_DIR}" 1>&2
        _canary_rc=$?
        if [[ "$_canary_rc" == "2" ]]; then
          _SKY130HS_POSTCOND_FAIL=1
          hint "sky130hs GDS geometry canary FAILED: DEF geometry lands on unmappable layers (RMD-P0-04)"
        elif [[ "$_canary_rc" != "0" ]]; then
          req_or_hint sky130hs "sky130hs GDS geometry canary could not run (rc=$_canary_rc, klayout missing?) — postcondition unverified"
        fi
      fi
    fi
  fi
  [[ "$_found" == "1" ]] && break
done

if [[ "$_found" != "1" ]]; then
  if [[ -n "$STRICT_PLATFORMS" ]]; then
    die "platform rule installers not found (expected in the agent-r2g repo tools/ — set R2G_TOOLS_DIR) but strict platform(s) '$STRICT_PLATFORMS' were selected — fail closed (RMD2-P1-01)"
  fi
  hint "platform rule installers not found (expected in the agent-r2g repo tools/ — set R2G_TOOLS_DIR); nangate45 LVS/antenna decks + sky130hs lyt unchanged"
fi

# Fail-closed installer completeness for a strict-selected nangate45: all three
# rule installers must have RUN AND SUCCEEDED, not merely "some were found".
if is_selected nangate45 && [[ "$DRY" != "1" ]]; then
  for _rule in install_nangate45_lvs.sh install_nangate45_drc.sh install_nangate45_antenna.sh; do
    [[ -n "${_NG45_DONE[$_rule]:-}" ]] \
      || die "nangate45 is strict-selected but $_rule was not found/executed (set R2G_TOOLS_DIR) — fail closed (RMD2-P1-01)"
  done
fi

# Fail-closed exit (RMD-P0-04): a broken sky130hs GDS-import postcondition must
# FAIL setup, not degrade to a hint — every sky130hs LVS verdict downstream of a
# legacy .lyt is invalid, and evidence produced before the repair must be
# regenerated from finish.
if [[ "${_SKY130HS_POSTCOND_FAIL:-0}" == "1" ]]; then
  die "sky130hs .lyt repair postcondition failed — fix the patch (tools/patch_sky130hs_lyt.py), then regenerate all sky130hs GDS/LVS evidence from finish"
fi

# ---- post-install strict capability gate + installation manifest --------------
# (RMD2-P1-01): verify, with the exact resolved ORFS/PDK/tool environment, that
# every SELECTED platform is strict_signoff_ready — and persist the capability
# verdict + collateral digests so the installation is auditable. The manifest is
# written even when the gate fails (the failure is the evidence).
if [[ -n "$STRICT_PLATFORMS" && "$DRY" != "1" ]]; then
  _cap_py="$HERE/../../../signoff-loop/scripts/flow/platform_capability.py"
  _cap_cmd=(python3 "$_cap_py")
  # Test seam / operator override — recorded in the manifest when used.
  # shellcheck disable=SC2206
  [[ -n "${R2G_CAPABILITY_CMD:-}" ]] && _cap_cmd=(${R2G_CAPABILITY_CMD})
  [[ -f "$_cap_py" || -n "${R2G_CAPABILITY_CMD:-}" ]] \
    || die "platform_capability.py not found at $_cap_py — cannot verify strict platform(s) '$STRICT_PLATFORMS' (fail closed)"
  _plat_args=()
  for _p in $STRICT_PLATFORMS; do _plat_args+=(--platform "$_p"); done
  _cap_out="$(mktemp)"
  _cap_rc=0
  "${_cap_cmd[@]}" --flow-dir "${FLOW_DIR:-}" "${_plat_args[@]}" --strict \
    --out "$_cap_out" 1>&2 || _cap_rc=$?
  _manifest="$HERE/../../references/install_manifest.json"
  python3 - "$_manifest" "$_cap_out" "$_cap_rc" "$STRICT_PLATFORMS" \
    "${FLOW_DIR:-}" "${R2G_CAPABILITY_CMD:-}" <<'PY' || hint "install manifest write failed"
import glob, hashlib, json, os, sys, time
manifest, cap_out, cap_rc, platforms, flow_dir, cap_override = sys.argv[1:7]
try:
    with open(cap_out, encoding="utf-8") as f:
        capability = json.load(f)
except Exception:
    capability = None

def _sha(p):
    try:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None

collateral = {}
for p in platforms.split():
    pdir = os.path.join(flow_dir, "platforms", p)
    files = sorted(glob.glob(os.path.join(pdir, "drc", "*"))
                   + glob.glob(os.path.join(pdir, "lvs", "*"))
                   + glob.glob(os.path.join(pdir, "*.lyt"))
                   + glob.glob(os.path.join(pdir, "*.lylvs")))
    collateral[p] = {os.path.relpath(f, pdir): _sha(f)
                     for f in files if os.path.isfile(f)}
doc = {"ts": int(time.time()),
       "strict_platforms": platforms.split(),
       "strict_capability_rc": int(cap_rc),
       "strict_ready": int(cap_rc) == 0,
       "capability": capability,
       "collateral_sha256": collateral}
if cap_override:
    doc["capability_cmd_overridden"] = cap_override
tmp = manifest + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(doc, f, indent=1)
os.replace(tmp, manifest)
PY
  rm -f "$_cap_out"
  if [[ "$_cap_rc" != "0" ]]; then
    die "post-install strict capability check FAILED for selected platform(s) '$STRICT_PLATFORMS' (rc=$_cap_rc) — installation is incomplete; see references/install_manifest.json (RMD2-P1-01)"
  fi
  log "strict capability verified for: $STRICT_PLATFORMS (manifest: $_manifest)"
fi
