#!/usr/bin/env bash
# Read-only CPU load sampler for a running MaaMeow task (VM side; nothing is pushed to Android).
# Every second the device shell prints /proc/stat CPU lines, MaaCore (shizuku_service) process and
# thread stats, the game process stat, CPU/NPU frequencies and thermal zones; every 10 s it dumps
# all visible process stats. Output goes to .cache/rk3588/cpu-sampling/session-NN/raw.txt.
# Usage: sample-cpu-load.sh [max_seconds] [serial]; stop early by killing the recorded PID.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/env.sh"
cd "$MAA_RK_ROOT"
max_seconds="${1:-14400}"
serial="${2:-${MAA_ADB_SERIAL:?set MAA_ADB_SERIAL or pass a serial}}"
base=.cache/rk3588/cpu-sampling
mkdir -p "$base"
run=1
while [[ -e "$base/session-$(printf %02d "$run")" ]]; do run=$((run + 1)); done
out="$base/session-$(printf %02d "$run")"
mkdir -p "$out"
adb -s "$serial" get-state >/dev/null
printf 'vm_start_utc=%s max_seconds=%s\n' "$(date -u +%FT%TZ)" "$max_seconds" > "$out/meta.txt"
loop='
echo "S $$ $(getconf CLK_TCK) $(date "+%Y-%m-%d %H:%M:%S %z")"
n=0
while :; do
  echo "T $(date +%s) $(cat /proc/uptime)"
  grep "^cpu" /proc/stat
  m=$(pidof com.aliothmoon.maameow:shizuku_service)
  g=$(pidof com.hypergryph.arknights)
  [ -n "$m" ] && { echo "P maacore $m"; cat /proc/$m/stat; cat /proc/$m/task/*/stat 2>/dev/null | sed "s/^/TH /"; }
  [ -n "$g" ] && { echo "P game $g"; cat /proc/$g/stat; }
  echo "F $(cat /sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq /sys/devices/system/cpu/cpufreq/policy4/scaling_cur_freq /sys/devices/system/cpu/cpufreq/policy6/scaling_cur_freq /sys/class/devfreq/fdab0000.npu/cur_freq | tr "\n" " ")"
  echo "Z $(cat /sys/class/thermal/thermal_zone*/temp | tr "\n" " ")"
  if [ $((n % 10)) -eq 0 ]; then cat /proc/[0-9]*/stat 2>/dev/null | sed "s/^/A /"; fi
  n=$((n + 1))
  sleep 1
done'
printf '%s\n' "$$" > "$out/pid"
printf 'Sampling into %s (pid %s, max %ss); stop with: kill %s\n' "$out" "$$" "$max_seconds" "$$"
timeout "$max_seconds" adb -s "$serial" shell -n "$loop" > "$out/raw.txt" 2> "$out/stderr.txt" &
child=$!
# Forward a stop request to timeout/adb; closing the adb shell ends the device-side loop.
trap 'kill -TERM "$child" 2>/dev/null' TERM INT HUP
code=0
wait "$child" || code=$?
# A trapped signal interrupts the first wait; wait again until adb has actually exited.
while kill -0 "$child" 2>/dev/null; do wait "$child" || code=$?; done
printf 'vm_end_utc=%s exit=%s\n' "$(date -u +%FT%TZ)" "$code" >> "$out/meta.txt"
rm -f "$out/pid"
