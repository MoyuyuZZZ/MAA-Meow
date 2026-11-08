#!/usr/bin/env bash
# Device step: run only after the user approves the listed scope.
# Pushes the probe, pinned runtime, official model and two RGB inputs into a fresh
# /data/local/tmp/maa-rknn-infer.* directory, runs once as the ADB shell user, then
# removes exactly those files. No install, root, system writes or global library path.
set -euo pipefail
if [[ "${1:-}" != "--confirmed" ]]; then
    printf '%s\n' "Usage: $0 --confirmed [serial] (deploys temporary files; see docs/rk3588/RKNN-PREFLIGHT.md)"
    exit 2
fi
source "$(dirname -- "${BASH_SOURCE[0]}")/env.sh"
cd "$MAA_RK_ROOT"
serial="${2:-${MAA_ADB_SERIAL:?set MAA_ADB_SERIAL or pass a serial}}"
warm_runs=20
base=.cache/rk3588/rknn-infer
files=(
    "$base/rknn-infer"
    .cache/rk3588/rknn-probe/librknnrt.so
    "$base/official/mobilenet_v1.rknn"
    "$base/dog_224x224.rgb"
    "$base/cat_224x224.rgb"
)
for file in "${files[@]}"; do
    [[ -f "$file" ]] || { printf 'Missing %s; run build-rknn-probe.sh first.\n' "$file"; exit 1; }
done
run=1
while [[ -e "$base/device-run-$(printf %02d "$run").log" ]]; do run=$((run + 1)); done
tag="$base/device-run-$(printf %02d "$run")"
adb -s "$serial" get-state >/dev/null
[[ "$(adb -s "$serial" shell -n getprop ro.product.cpu.abi | tr -d '\r')" == arm64-v8a ]] || exit 1
adb -s "$serial" shell -n df -k /data/local/tmp > "$tag.df.txt"
device_tmp="$(adb -s "$serial" shell -n mktemp -d /data/local/tmp/maa-rknn-infer.XXXXXX | tr -d '\r')"
if [[ ! "$device_tmp" =~ ^/data/local/tmp/maa-rknn-infer\.[A-Za-z0-9]+$ ]]; then
    printf '%s\n' 'Unexpected temporary path; stop without deploying.'
    exit 1
fi
printf '%s\n' "$device_tmp" > "$tag.temp-path.txt"
remote=()
for file in "${files[@]}"; do remote+=("$device_tmp/$(basename "$file")"); done
cleanup() {
    adb -s "$serial" shell -n rm -f "${remote[@]}" || true
    adb -s "$serial" shell -n rmdir "$device_tmp" || true
    if adb -s "$serial" shell -n "[ -e $device_tmp ]"; then
        printf 'cleanup=FAIL %s still exists\n' "$device_tmp" | tee -a "$tag.cleanup.txt"
    else
        printf 'cleanup=PASS %s removed\n' "$device_tmp" | tee -a "$tag.cleanup.txt"
    fi
}
trap cleanup EXIT
for i in "${!files[@]}"; do adb -s "$serial" push "${files[$i]}" "${remote[$i]}"; done
adb -s "$serial" shell -n sha256sum "${remote[@]}" > "$tag.checksums.txt"
python3 - "$tag.checksums.txt" "${files[@]}" <<'PY'
import hashlib
from pathlib import Path
import sys
lines = Path(sys.argv[1]).read_text().splitlines()
local = sys.argv[2:]
if len(lines) != len(local):
    raise SystemExit("Unexpected device checksum output")
for path, line in zip(local, lines):
    with open(path, "rb") as stream:
        if line.split()[0] != hashlib.file_digest(stream, "sha256").hexdigest():
            raise SystemExit(f"Device checksum mismatch: {path}")
print("Device file SHA-256 checks: PASS")
PY
adb -s "$serial" shell -n chmod 700 "${remote[0]}"
set +e
timeout 60 adb -s "$serial" shell -n "${remote[0]}" "${remote[1]}" "${remote[2]}" "$warm_runs" \
    "${remote[3]}" "${remote[4]}" > "$tag.log" 2>&1
status=$?
set -e
printf '%s\n' "$status" > "$tag.exit"
cat "$tag.log"
printf 'probe_exit=%s\n' "$status"
python3 scripts/rk3588/check-rknn-infer-log.py "$tag.log" | tee "$tag.check.txt"
