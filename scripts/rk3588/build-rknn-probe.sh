#!/usr/bin/env bash
# Build on the VM only. This script never connects to or deploys onto Android.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/env.sh"
cd "$MAA_RK_ROOT"
python3 - <<'PY'
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path("scripts/rk3588").resolve()))
from bootstrap import download
lock = json.loads(Path("scripts/rk3588/rknn-runtime.lock.json").read_text())
download(lock, Path(".cache/rk3588/rknn-probe"))
PY
python3 scripts/rk3588/prepare-rknn-mobilenet.py
ndk_package="$(python3 - <<'PY'
import json
from pathlib import Path
lock = json.loads(Path("scripts/rk3588/toolchain.lock.json").read_text())
packages = [key for key in lock["sdk_packages"] if key.startswith("ndk/")]
if len(packages) != 1:
    raise SystemExit("Expected one pinned NDK")
print(packages[0])
PY
)"
llvm_bin="$ANDROID_HOME/$ndk_package/toolchains/llvm/prebuilt/linux-x86_64/bin"
output="$MAA_RK_ROOT/.cache/rk3588/rknn-probe"
infer="$MAA_RK_ROOT/.cache/rk3588/rknn-infer"
cflags=(-std=c11 -Wall -Wextra -Werror -O2 -fPIE -pie -Wl,-z,relro,-z,now)
"$llvm_bin/aarch64-linux-android28-clang" "${cflags[@]}" \
  scripts/rk3588/probe-rknn-access.c -ldl -o "$output/rknn-access"
"$llvm_bin/aarch64-linux-android28-clang" "${cflags[@]}" -isystem "$infer/official" \
  scripts/rk3588/probe-rknn-infer.c -ldl -lm -o "$infer/rknn-infer"
"$llvm_bin/llvm-readelf" -h -l -d "$output/rknn-access" > "$output/probe-elf.txt"
"$llvm_bin/llvm-readelf" -h -d "$output/librknnrt.so" > "$output/runtime-elf.txt"
"$llvm_bin/llvm-readelf" -h -l -d "$infer/rknn-infer" > "$infer/probe-elf.txt"
sha256sum "$output/rknn-access" "$output/librknnrt.so" "$infer/rknn-infer" \
  "$infer/official/mobilenet_v1.rknn" "$infer/dog_224x224.rgb" "$infer/cat_224x224.rgb"
printf '%s\n' 'Built only; no Android deployment or execution performed.'
