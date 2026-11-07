#!/usr/bin/env bash
# Source this file from any working directory. Personal Gradle settings stay outside Git.
MAA_RK_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
export JAVA_HOME="$MAA_RK_ROOT/.cache/rk3588/jdk"
export ANDROID_HOME="$MAA_RK_ROOT/.cache/rk3588/android-sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export ANDROID_USER_HOME="$MAA_RK_ROOT/.cache/rk3588/android-user"
export GRADLE_USER_HOME="${GRADLE_USER_HOME:-$HOME/.gradle}"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/cmdline-tools/23.0/bin:$ANDROID_HOME/platform-tools:$PATH"
