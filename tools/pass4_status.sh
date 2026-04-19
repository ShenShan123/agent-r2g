#!/usr/bin/env bash
# Quick snapshot of the Pass 4 retry campaign.
#
# Usage:
#   bash tools/pass4_status.sh [RUN_TAG_PREFIX]
#
# Default RUN_TAG_PREFIX is "RUN_2026-04-19_01" (the Pass 4 launch window).
# Pass a shorter or newer prefix if you re-launch later.

set -u
PREFIX="${1:-RUN_2026-04-19_01}"
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/design_cases"

BUCKETS=(
  "A:ethernet/axis:verilog_ethernet_arp verilog_ethernet_axis_baser_rx_64 verilog_ethernet_axis_baser_tx_64 verilog_ethernet_eth_mac_10g verilog_ethernet_ip_complete verilog_ethernet_ip_complete_64 verilog_ethernet_udp_complete verilog_ethernet_udp_complete_64"
  "B:FIFO (place-timeout):verilog_axis_axis_ram_switch verilog_ethernet_eth_mac_1g_fifo verilog_ethernet_eth_mac_mii_fifo"
  "C:iscas89:iscas89_s1196 iscas89_s820 iscas89_s832 iscas89_s953"
  "D:synth-timeout:arm_core koios_gemm_layer"
)

snap() {
  local d="$1"
  local dir
  dir=$(ls -td "$BASE/$d/backend/$PREFIX"* 2>/dev/null | head -1)
  if [[ -z "$dir" ]]; then
    printf "  %-50s %s\n" "$d" "NOT_STARTED"
    return
  fi
  local meta="$dir/run-meta.json"
  local log="$dir/stage_log.jsonl"
  local n=0
  [[ -f "$log" ]] && n=$(wc -l < "$log" 2>/dev/null)
  local last=""
  [[ -f "$log" ]] && last=$(tail -1 "$log" 2>/dev/null)
  local state="RUNNING"
  if [[ -f "$meta" ]]; then
    local ms
    ms=$(python3 -c "import json; print(json.load(open('$meta')).get('make_status','?'))" 2>/dev/null || echo '?')
    [[ "$ms" == "0" ]] && state="PASS" || state="FAIL($ms)"
  fi
  printf "  %-50s %-10s stages=%d  %s\n" "$d" "$state" "$n" "$last"
}

total=0
pass=0
fail=0
running=0
not_started=0

for spec in "${BUCKETS[@]}"; do
  label="${spec%%:*}"
  rest="${spec#*:}"
  name="${rest%%:*}"
  cases="${rest#*:}"
  echo "=== Bucket $label ($name) ==="
  for c in $cases; do
    snap "$c"
    total=$((total + 1))
    dir=$(ls -td "$BASE/$c/backend/$PREFIX"* 2>/dev/null | head -1)
    if [[ -z "$dir" ]]; then
      not_started=$((not_started + 1))
    elif [[ -f "$dir/run-meta.json" ]]; then
      ms=$(python3 -c "import json; print(json.load(open('$dir/run-meta.json')).get('make_status','?'))" 2>/dev/null)
      if [[ "$ms" == "0" ]]; then pass=$((pass + 1)); else fail=$((fail + 1)); fi
    else
      running=$((running + 1))
    fi
  done
done

echo ""
echo "Total=$total  Pass=$pass  Fail=$fail  Running=$running  NotStarted=$not_started"
