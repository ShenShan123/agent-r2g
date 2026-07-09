#!/usr/bin/env bash
# Tier: klayout (optional) — GDS viewer + the nangate45/asap7/gf180/ihp DRC & LVS
# rule engine.
#
# Installed into its OWN conda env (default 'klayout', NOT the shared 'eda' env):
# klayout drags in heavy Qt/Ruby deps that CONFLICT with magic/netgen/iverilog when
# solved in a shared env. klayout is OPTIONAL and a **system** klayout (from the
# distro, often NEWER than conda's) satisfies this tier — so this tier prefers an
# existing klayout and FAILS SOFT (HINT + exit 0) rather than blocking the bootstrap.
#
# KNOWN UPSTREAM LIMITATION (verified 2026-07-09): the litex-hub klayout recipe pins
# `openssl 1.1` while its `ruby` dep needs openssl 3.x — unsatisfiable in a modern
# conda base — and conda-forge ships NO klayout package. On such hosts the conda
# install cannot succeed; use the distro package instead:
#     dnf install klayout      # RHEL/Fedora
#     apt install klayout      # Debian/Ubuntu
# The flow uses whatever KLAYOUT_CMD resolves to (system klayout is fully supported).
set -uo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/../flow/_env.sh" 1>&2
# shellcheck source=/dev/null
source "$HERE/_setup_lib.sh"
setup_parse "$@"

# A klayout found anywhere (incl. /usr/bin) satisfies this optional tier.
if [[ "$FORCE" != "1" && -n "${KLAYOUT_CMD:-}" ]]; then
  log "klayout already satisfied ($KLAYOUT_CMD)"; exit 0
fi

# Dedicated env — do NOT solve klayout's Qt/Ruby against the shared toolchain env.
CONDA_ENV="${R2G_KLAYOUT_ENV:-klayout}"
if conda_env_install klayout; then
  log "klayout installed into conda env '$CONDA_ENV' — run write_env_local.sh to pin the path"
  exit 0
fi

# Optional tier: never block the bootstrap on a klayout solve failure.
hint "conda klayout install failed — klayout is OPTIONAL, skipping."
hint "The litex-hub recipe is frequently unsatisfiable in a modern conda base"
hint "(openssl 1.1 vs ruby's openssl 3.x) and conda-forge ships no klayout."
hint "Prefer a system klayout: 'dnf install klayout' or 'apt install klayout'"
hint "(usually newer than conda's); the flow uses whatever KLAYOUT_CMD resolves to."
exit 0
