#!/usr/bin/env bash
# Device step: run only after the user approves the listed scope.
# Pushes the rec probe, pinned runtime, word/char rec RKNN models and their text-line canvases
# into a fresh /data/local/tmp/maa-rknn-bench.* directory and runs a fixed matrix of benchmark
# configurations as the ADB shell user (NPU devfreq is only read, never changed). Pulls timing
# CSVs, result files and per-layer reports, then removes exactly that directory's files.
set -euo pipefail
if [[ "${1:-}" != "--confirmed" ]]; then
    printf '%s\n' "Usage: $0 --confirmed [serial] (deploys temporary files; see docs/rk3588/OCR-RKNN-ASSESSMENT.md)"
    exit 2
fi
source "$(dirname -- "${BASH_SOURCE[0]}")/env.sh"
cd "$MAA_RK_ROOT"
serial="${2:-${MAA_ADB_SERIAL:?set MAA_ADB_SERIAL or pass a serial}}"
ocr=.cache/rk3588/ocr-rknn
files=(
    .cache/rk3588/rknn-infer/rknn-rec
    .cache/rk3588/rknn-probe/librknnrt.so
    "$ocr/PaddleOCR_rec-rk3588-fp16-48x320.rknn"
    "$ocr/PaddleCharOCR_rec-rk3588-fp16-48x320.rknn"
    "$ocr/canvases-word.u8"
    "$ocr/canvases-char.u8"
)
for file in "${files[@]}"; do
    [[ -f "$file" ]] || { printf 'Missing %s; run the build/convert/compare steps first.\n' "$file"; exit 1; }
done
# name|kind|probe options|pull result file
configs=(
    "float-i0|word|-w 30|1"
    "fp16-i0|word|-w 30 -f|1"
    "fp16-i50|word|-w 30 -f -i 50 -n 60|0"
    "fp16-i200|word|-w 30 -f -i 200 -n 60|0"
    "fp16-i1000|word|-w 30 -f -i 1000 -n 20|0"
    "perf|word|-w 10 -f -n 5|0"
    "float-i0|char|-w 30|1"
    "fp16-i0|char|-w 30 -f|1"
    "fp16-i50|char|-w 30 -f -i 50 -n 60|0"
    "fp16-i200|char|-w 30 -f -i 200 -n 60|0"
    "fp16-i1000|char|-w 30 -f -i 1000 -n 20|0"
    "perf|char|-w 10 -f -n 5|0"
)
run=1
while [[ -e "$ocr/bench-$(printf %02d "$run")" ]]; do run=$((run + 1)); done
out="$ocr/bench-$(printf %02d "$run")"
mkdir -p "$out"
adb -s "$serial" get-state >/dev/null
[[ "$(adb -s "$serial" shell -n getprop ro.product.cpu.abi | tr -d '\r')" == arm64-v8a ]] || exit 1
device_tmp="$(adb -s "$serial" shell -n mktemp -d /data/local/tmp/maa-rknn-bench.XXXXXX | tr -d '\r')"
if [[ ! "$device_tmp" =~ ^/data/local/tmp/maa-rknn-bench\.[A-Za-z0-9]+$ ]]; then
    printf '%s\n' 'Unexpected temporary path; stop without deploying.'
    exit 1
fi
printf '%s\n' "$device_tmp" > "$out/temp-path.txt"
remote=()
for file in "${files[@]}"; do remote+=("$device_tmp/$(basename "$file")"); done
cleanup() {
    adb -s "$serial" shell -n "rm -f $device_tmp/*" || true
    adb -s "$serial" shell -n rmdir "$device_tmp" || true
    if adb -s "$serial" shell -n "[ -e $device_tmp ]"; then
        printf 'cleanup=FAIL %s still exists\n' "$device_tmp" | tee -a "$out/cleanup.txt"
    else
        printf 'cleanup=PASS %s removed\n' "$device_tmp" | tee -a "$out/cleanup.txt"
    fi
}
trap cleanup EXIT
for i in "${!files[@]}"; do adb -s "$serial" push "${files[$i]}" "${remote[$i]}" >/dev/null; done
adb -s "$serial" shell -n sha256sum "${remote[@]}" > "$out/checksums.txt"
python3 - "$out/checksums.txt" "${files[@]}" <<'PY'
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
status=0
for config in "${configs[@]}"; do
    IFS='|' read -r name kind options pull <<< "$config"
    if [[ "$kind" == word ]]; then model="${remote[2]}"; canvases="${remote[4]}"; else model="${remote[3]}"; canvases="${remote[5]}"; fi
    tag="$kind-$name"
    args="$options -t $device_tmp/$tag.csv"
    [[ "$pull" == 1 ]] && args="$args -o $device_tmp/$tag.bin"
    [[ "$name" == perf ]] && args="$args -p $device_tmp/$tag.perf.txt"
    sleep 5  # let the ondemand governor settle between configurations
    set +e
    # shellcheck disable=SC2086
    timeout 180 adb -s "$serial" shell -n "${remote[0]}" $args "${remote[1]}" "$model" "$canvases" > "$out/$tag.log" 2>&1
    code=$?
    set -e
    printf '%s\n' "$code" > "$out/$tag.exit"
    printf '[%s] exit=%s %s\n' "$tag" "$code" "$(grep -E '^lines stage=(run|total) n' "$out/$tag.log" | tr '\n' ' ')"
    [[ "$code" == 0 ]] || { status=1; continue; }
    adb -s "$serial" pull "$device_tmp/$tag.csv" "$out/$tag.csv" >/dev/null
    [[ "$name" == perf ]] && adb -s "$serial" pull "$device_tmp/$tag.perf.txt" "$out/$tag.perf.txt" >/dev/null
    if [[ "$pull" == 1 ]]; then
        adb -s "$serial" pull "$device_tmp/$tag.bin" "$out/$tag.bin" >/dev/null
        .cache/rk3588/rknn-venv/bin/python scripts/rk3588/check-rknn-rec-result.py "$kind" "$out/$tag.bin" \
            > "$out/$tag.check.txt" || status=1
        tail -1 "$out/$tag.check.txt"
    fi
done
exit "$status"
