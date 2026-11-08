#!/usr/bin/env bash
# VM only: isolated RKNN-Toolkit2 2.3.2 conversion environment under .cache/rk3588.
# Uses a pinned uv binary and a uv-managed CPython 3.12; never touches system Python,
# ~/.local/bin or Android devices. Source the personal proxy file first when downloading.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/env.sh"
cd "$MAA_RK_ROOT"
cache="$MAA_RK_ROOT/.cache/rk3588"
uv_version=0.13.6
uv_sha256=f29c3e94ffabd7e6f3ba521dba169a9b7ec12100bf3257087f0f24f6123f3645
python_version=3.12.16
archive="$cache/downloads/uv-x86_64-unknown-linux-gnu-$uv_version.tar.gz"
mkdir -p "$cache/downloads" "$cache/uv"
if ! printf '%s  %s\n' "$uv_sha256" "$archive" | sha256sum -c --status - 2>/dev/null; then
    curl --fail --location --retry 3 --connect-timeout 20 --silent --show-error -o "$archive.part" \
        "https://github.com/astral-sh/uv/releases/download/$uv_version/uv-x86_64-unknown-linux-gnu.tar.gz"
    printf '%s  %s\n' "$uv_sha256" "$archive.part" | sha256sum -c -
    mv "$archive.part" "$archive"
fi
tar -xzf "$archive" -C "$cache/uv" --strip-components=1
export UV_CACHE_DIR="$cache/uv-cache" UV_PYTHON_INSTALL_DIR="$cache/uv-python" UV_NO_CONFIG=1
export UV_PYTHON_BIN_DIR="$cache/uv-python/bin"
uv="$cache/uv/uv"
"$uv" python install --no-bin "$python_version"
python="$(UV_PYTHON_DOWNLOADS=never "$uv" python find "$python_version")"
[[ -x "$cache/rknn-venv/bin/python" ]] || "$uv" venv --python "$python" "$cache/rknn-venv"
VIRTUAL_ENV="$cache/rknn-venv" "$uv" pip sync --require-hashes --index-url https://pypi.org/simple \
    scripts/rk3588/rknn-toolkit-requirements.txt
"$cache/rknn-venv/bin/python" - <<'PY'
from importlib.metadata import version
from rknn.api import RKNN
print("rknn-toolkit2", version("rknn-toolkit2"), "import OK:", RKNN.__name__)
PY
printf '%s\n' "Use: $cache/rknn-venv/bin/python <script> (no activation or PATH changes needed)."
# Regenerate the lock after editing requirements.in (pins resolve as of the exclude-newer date):
#   uv pip compile scripts/rk3588/rknn-toolkit-requirements.in --override scripts/rk3588/rknn-toolkit-overrides.txt \
#     --python <python3.12> --python-platform x86_64-manylinux_2_28 --generate-hashes \
#     --exclude-newer 2025-06-30T00:00:00Z --index-url https://pypi.org/simple --no-header \
#     --annotation-style line -o scripts/rk3588/rknn-toolkit-requirements.txt
