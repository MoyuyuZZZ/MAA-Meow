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
"$llvm_bin/aarch64-linux-android28-clang" -std=c11 -Wall -Wextra -Werror -O2 \
  -fPIE -pie -Wl,-z,relro,-z,now \
  scripts/rk3588/probe-rknn-access.c -ldl -o "$output/rknn-access"
"$llvm_bin/llvm-readelf" -h -l -d "$output/rknn-access" > "$output/probe-elf.txt"
"$llvm_bin/llvm-readelf" -h -d "$output/librknnrt.so" > "$output/runtime-elf.txt"
sha256sum "$output/rknn-access" "$output/librknnrt.so"
printf '%s\n' 'Built only; no Android deployment or execution performed.'
