#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/env.sh"
cd "$MAA_RK_ROOT"
test -x "$JAVA_HOME/bin/java" || { echo "Run python3 scripts/rk3588/bootstrap.py first"; exit 1; }
python3 - <<'PY'
import json
from pathlib import Path
lock = json.loads(Path("scripts/rk3588/toolchain.lock.json").read_text())
version = Path(".maaversion")
if not version.is_file() or version.read_text().strip() != lock["core"]["tag"]:
    raise SystemExit("Core version differs from lock; run bootstrap.py first.")
PY
# Keep the original application ID. This script only builds; never installs.
env -u SENTRY_DSN -u KEYSTORE_PATH -u KEYSTORE_PASSWORD -u KEY_ALIAS -u KEY_PASSWORD \
  bash ./gradlew --no-daemon --max-workers=2 \
  -Dorg.gradle.jvmargs="-Xmx3g -Dfile.encoding=UTF-8" \
  -Dorg.gradle.parallel=false -Pkotlin.compiler.execution.strategy=in-process \
  -Pmaa.abi=arm64-v8a -Pmaa.nativeLto=false :app:assembleDebug
sha256sum app/build/outputs/apk/debug/*.apk
