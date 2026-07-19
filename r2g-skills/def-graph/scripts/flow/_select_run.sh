#!/usr/bin/env bash
set -euo pipefail

# usage: _select_run.sh <backend_dir> [requested_flow_variant]
# Prints candidate backend RUN_* dirs on stdout, newest-first, one per line.
# The caller keeps its own "first one that actually holds a 6_final.def" logic —
# this helper only decides WHICH runs are ELIGIBLE.
#
# Variant guard (failure-patterns.md #52; 2026-07-19 audit P0-N3): every caller
# used to pick the first reverse-sorted RUN_* holding a final DEF and IGNORE the
# flow_variant argument entirely — it was forwarded only to the live-ORFS-results
# fallback. So `run_graphs.sh <proj> nangate45 variant_a` on a project holding
# both variant_a and variant_b runs returned 0 while silently publishing
# variant_b's layout. That is a dataset-identity failure: labels and graphs get
# attributed to an experiment arm they did not come from, and it is invisible in
# the manifest's row counts (the same silent-value class as #30).
#
# With no requested variant the output is byte-identical to the old
# `ls -d RUN_* | sort -r` — the historical path is untouched.
#
# When a variant IS requested we fail CLOSED: a run is eligible only if its
# run-meta.json records exactly that flow_variant. A run with no readable
# run-meta.json is NOT eligible (it is announced on stderr) — we cannot honor an
# explicit request against a run whose identity was never recorded, and silently
# accepting it would reintroduce the very bug this guard exists to stop.
#
# Shared by run_labels.sh / run_features.sh / run_graphs.sh — one copy, per the
# techlib lesson: a worker-local patch fixes one consumer and silently leaves
# the others wrong. (Sibling of _provenance.sh, which does the same for platform.)

BACKEND_DIR="${1:-}"
WANT="${2:-}"

[[ -d "$BACKEND_DIR" ]] || exit 0

_all=()
while IFS= read -r d; do [[ -n "$d" ]] && _all+=("$d"); done < <(
  ls -d "$BACKEND_DIR"/RUN_* 2>/dev/null | sort -r)

((${#_all[@]})) || exit 0

if [[ -z "$WANT" ]]; then
  printf '%s\n' "${_all[@]}"
  exit 0
fi

_kept=() _unrecorded=() _other=()
for run in "${_all[@]}"; do
  _v=""
  if [[ -f "$run/run-meta.json" ]]; then
    _v=$(sed -n 's/.*"flow_variant"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
         "$run/run-meta.json" | head -1)
  fi
  if [[ -z "$_v" ]]; then
    _unrecorded+=("$(basename "$run")")
  elif [[ "$_v" == "$WANT" ]]; then
    _kept+=("$run")
  else
    _other+=("$(basename "$run"):$_v")
  fi
done

if ((${#_unrecorded[@]})); then
  echo "WARNING: flow_variant=$WANT requested; ignoring ${#_unrecorded[@]} run(s)" \
       "with no recorded flow_variant (${_unrecorded[*]}) — cannot honor an explicit" \
       "variant against an unrecorded run (failure-patterns.md #52)" >&2
fi

if ((${#_kept[@]} == 0)); then
  echo "ERROR: no backend run matches flow_variant=$WANT under $BACKEND_DIR" \
       "${_other[*]+(saw: ${_other[*]})}" >&2
  exit 0
fi

printf '%s\n' "${_kept[@]}"
