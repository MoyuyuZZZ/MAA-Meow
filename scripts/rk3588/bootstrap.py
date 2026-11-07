#!/usr/bin/env python3
"""Install the pinned user-space toolchain and unmodified ARM64 CPU Core."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools-only", action="store_true",
                        help="Install toolchain without populating Core assets")
    args = parser.parse_args()
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
    # cmdline-tools 23 delegates SDK management to Android CLI.
    cli = [str(sdkmanager.with_name("android")), "--no-metrics", f"--sdk={sdk}"]
    version = subprocess.check_output([*cli, "--version"], env=env, text=True)
    (CACHE / "android-cli-version.txt").write_text(version)
    run(*cli, "sdk", "install",
        *(f"{package}@{revision}" for package, revision in LOCK["sdk_packages"].items()),
        env=env)
    def revision_tuple(value):
        parts = tuple(int(part) for part in value.split("."))
        return parts + (0,) * max(0, 3 - len(parts))
    for package, expected in LOCK["sdk_packages"].items():
        props = sdk / package / "source.properties"
        if not props.is_file():
            raise SystemExit(f"Required SDK package missing: {package}")
        values = dict(line.split("=", 1) for line in props.read_text().splitlines()
                      if "=" in line and not line.lstrip().startswith("#"))
        actual = {k.strip(): v.strip() for k, v in values.items()}.get("Pkg.Revision", "")
        if revision_tuple(actual) != revision_tuple(expected):
            raise SystemExit(f"SDK revision mismatch: {package}: {actual} != {expected}")
    installed = subprocess.check_output([*cli, "sdk", "list"], env=env, text=True)
    (CACHE / "sdk-installed.txt").write_text(installed)
    # Seed the normal Wrapper cache from the same official, verified artifact.
    # This avoids a github.com redirect without modifying the upstream Wrapper.
    props = (ROOT / "gradle/wrapper/gradle-wrapper.properties").read_text()
    wrapper_url = next(line.split("=", 1)[1] for line in props.splitlines()
                       if line.startswith("distributionUrl=")).replace("\\:", ":")
    if wrapper_url != LOCK["gradle"]["wrapper_url"]:
        raise SystemExit("Wrapper URL changed; review and update the toolchain lock.")
    if "distributionSha256Sum=" + LOCK["gradle"]["sha256"] not in props.splitlines():
        raise SystemExit("Wrapper checksum differs from toolchain lock.")
    archive = download(LOCK["gradle"], CACHE / "downloads")
    value = int(hashlib.md5(wrapper_url.encode(), usedforsecurity=False).hexdigest(), 16)
    key = ""
    while value:
        value, digit = divmod(value, 36)
        key = "0123456789abcdefghijklmnopqrstuvwxyz"[digit] + key
    gradle_home = Path(os.environ.get("GRADLE_USER_HOME") or str(Path.home() / ".gradle")).expanduser()
    target = gradle_home / "wrapper/dists" / archive.name.removesuffix(".zip") / key
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(archive, target / archive.name)
    if args.tools_only:
        print("Toolchain ready; Core assets were not populated.", flush=True)
        return
    download(LOCK["core"], ROOT / ".maa-cache")
    # This populates ignored build assets, not any Android device.
    run("python3", str(ROOT / "scripts/setup_maa_core.py"),
        "--skip-download", "--tag", LOCK["core"]["tag"],
        "--abi", "arm64-v8a", cwd=ROOT)
    print("Toolchain ready. Run bash scripts/rk3588/build-cpu.sh", flush=True)

if __name__ == "__main__":
    main()
