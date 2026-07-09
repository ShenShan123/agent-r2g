#!/usr/bin/env bash
# Convenience shim → the dedicated `eda-install` sub-skill's bootstrap.
#
# The environment-setup machinery (detect → plan → install → pin env.local.sh →
# verify) lives in its own Claude Code skill at eda-install/. This one-liner keeps
# the documented `bash r2g-skills/bootstrap.sh` entry point working and forwards
# every argument through unchanged.
exec bash "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/eda-install/bootstrap.sh" "$@"
