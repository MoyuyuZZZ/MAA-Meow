#!/usr/bin/env python3
"""Install the pinned user-space toolchain and unmodified ARM64 CPU Core."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / ".cache/rk3588"
LOCK = json.loads((Path(__file__).with_name("toolchain.lock.json")).read_text())

def run(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)

def download(item, directory):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / item["url"].rsplit("/", 1)[-1]
    algo = "sha256" if "sha256" in item else "sha1"
    def valid(p):
        if not p.is_file():
            return False
        with p.open("rb") as stream:
            return hashlib.file_digest(stream, algo).hexdigest() == item[algo]
    if not valid(path):
        partial = path.with_suffix(path.suffix + ".part")
        run("curl", "--fail", "--location", "--retry", "3",
            "--connect-timeout", "20", "--max-time", "1800",
            "--speed-limit", "1024", "--speed-time", "60",
            "--silent", "--show-error",
            "--header", "Accept: application/octet-stream",
            "--output", str(partial), item.get("asset_api", item["url"]))
        if not valid(partial):
            raise SystemExit(f"Checksum mismatch: {partial.name}")
        partial.replace(path)
    print(f"Verified {algo}: {path.name}", flush=True)
    return path

def main():
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise SystemExit("This lock targets a Linux x86_64 build host (ARM64 APK).")
    CACHE.mkdir(parents=True, exist_ok=True)
    jdk = CACHE / "jdk"
    if not (jdk / "bin/java").exists():
        archive = download(LOCK["jdk"], CACHE / "downloads")
        stage = CACHE / "jdk-unpack"
        stage.mkdir(exist_ok=True)
        with tarfile.open(archive) as tar:
            tar.extractall(stage, filter="data")
        roots = [p for p in stage.iterdir() if (p / "bin/java").exists()]
        if len(roots) != 1:
            raise SystemExit("Unexpected JDK archive layout")
        roots[0].rename(jdk)
    sdk = CACHE / "android-sdk"
    sdkmanager = sdk / "cmdline-tools/23.0/bin/sdkmanager"
    if not sdkmanager.exists():
        archive = download(LOCK["commandline_tools"], CACHE / "downloads")
        stage = CACHE / "sdk-unpack"
        with zipfile.ZipFile(archive) as z:
            z.extractall(stage)
        sdkmanager.parent.parent.parent.mkdir(parents=True, exist_ok=True)
        (stage / "cmdline-tools").rename(sdkmanager.parent.parent)
        for binary in sdkmanager.parent.iterdir():
            if binary.is_file():
                binary.chmod(0o755)
    env = os.environ.copy()
    env.update(JAVA_HOME=str(jdk), ANDROID_HOME=str(sdk),
               ANDROID_SDK_ROOT=str(sdk),
               ANDROID_USER_HOME=str(CACHE / "android-user"))
    env["PATH"] = str(jdk / "bin") + os.pathsep + env["PATH"]
    # Installation is part of the requested build setup. License text remains in
    # the SDK and the command output; no system-wide package manager is used.
    run(str(sdkmanager), f"--sdk_root={sdk}", "--licenses",
        input="y\n" * 100, text=True, env=env)
    run(str(sdkmanager), f"--sdk_root={sdk}", "--install",
        *LOCK["sdk_packages"], env=env)
    installed = subprocess.check_output(
        [str(sdkmanager), f"--sdk_root={sdk}", "--list_installed"], env=env, text=True)
    (CACHE / "sdk-installed.txt").write_text(installed)
    download(LOCK["core"], ROOT / ".maa-cache")
    # This populates ignored build assets, not any Android device.
    run("python3", str(ROOT / "scripts/setup_maa_core.py"),
        "--skip-download", "--tag", LOCK["core"]["tag"],
        "--abi", "arm64-v8a", cwd=ROOT)
    print("Toolchain ready. Run bash scripts/rk3588/build-cpu.sh", flush=True)

if __name__ == "__main__":
    main()
