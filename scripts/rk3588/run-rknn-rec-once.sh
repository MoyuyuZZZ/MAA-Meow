#!/usr/bin/env bash
# Device step: run only after the user approves the listed scope.
# Pushes the rec probe, pinned runtime, the converted word/char rec RKNN models and their
# text-line canvases into a fresh /data/local/tmp/maa-rknn-rec.* directory, runs each model
# once as the ADB shell user, pulls the result files, then removes exactly that directory's
# files. No install, root, system writes or global library path.
set -euo pipefail
if [[ "${1:-}" != "--confirmed" ]]; then
    printf '%s\n' "Usage: $0 --confirmed [serial] (deploys temporary files; see docs/rk3588/OCR-RKNN-ASSESSMENT.md)"
    exit 2
fi
source "$(dirname -- "${BASH_SOURCE[0]}")/env.sh"
cd "$MAA_RK_ROOT"
serial="${2:-${MAA_ADB_SERIAL:?set MAA_ADB_SERIAL or pass a serial}}"
warm_runs=50
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
run=1
while [[ -e "$ocr/device-run-$(printf %02d "$run").log" ]]; do run=$((run + 1)); done
tag="$ocr/device-run-$(printf %02d "$run")"
adb -s "$serial" get-state >/dev/null
[[ "$(adb -s "$serial" shell -n getprop ro.product.cpu.abi | tr -d '\r')" == arm64-v8a ]] || exit 1
device_tmp="$(adb -s "$serial" shell -n mktemp -d /data/local/tmp/maa-rknn-rec.XXXXXX | tr -d '\r')"
if [[ ! "$device_tmp" =~ ^/data/local/tmp/maa-rknn-rec\.[A-Za-z0-9]+$ ]]; then
    printf '%s\n' 'Unexpected temporary path; stop without deploying.'
    exit 1
fi
printf '%s\n' "$device_tmp" > "$tag.temp-path.txt"
remote=()
for file in "${files[@]}"; do remote+=("$device_tmp/$(basename "$file")"); done
results=("$device_tmp/result-word.bin" "$device_tmp/result-char.bin")
cleanup() {
    adb -s "$serial" shell -n rm -f "${remote[@]}" "${results[@]}" || true
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
status=0
for kind in word char; do
    if [[ "$kind" == word ]]; then model="${remote[2]}"; canvases="${remote[4]}"; result="${results[0]}"
    else model="${remote[3]}"; canvases="${remote[5]}"; result="${results[1]}"; fi
    set +e
    timeout 120 adb -s "$serial" shell -n "${remote[0]}" -w "$warm_runs" -o "$result" "${remote[1]}" "$model" "$canvases" \
        > "$tag.$kind.log" 2>&1
    code=$?
    set -e
    printf '%s\n' "$code" > "$tag.$kind.exit"
    printf '[%s] probe_exit=%s\n' "$kind" "$code"
    cat "$tag.$kind.log"
    if [[ "$code" == 0 ]]; then
        adb -s "$serial" pull "$result" "$tag.result-$kind.bin" >/dev/null
        .cache/rk3588/rknn-venv/bin/python scripts/rk3588/check-rknn-rec-result.py "$kind" "$tag.result-$kind.bin" \
            | tee "$tag.$kind.check.txt" || status=1
    else
        status=1
    fi
done
cat "$tag".word.log "$tag".char.log > "$tag.log"
exit "$status"
