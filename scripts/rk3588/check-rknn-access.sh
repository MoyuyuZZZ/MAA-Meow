#!/usr/bin/env bash
# Read-only Android inventory: no root, installs, device opens or inference.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/env.sh"
serial="${1:-${MAA_ADB_SERIAL:?set MAA_ADB_SERIAL or pass a serial}}"
adb -s "$serial" get-state >/dev/null
printf 'Android RKNN access inventory; UTC: %s\n' "$(date -u +%FT%TZ)"
# Missing paths are evidence, not failures. Transport failure still propagates.
adb -s "$serial" shell sh <<'ANDROID'
section() { printf '\n[%s]\n' "$1"; }
show_file() {
    if [ -r "$1" ]; then
        printf '%s: ' "$1"
        cat "$1"
        printf '\n'
    fi
}
section identity
id
getenforce
getprop ro.product.cpu.abilist
getprop ro.build.version.release
getprop ro.build.version.sdk
uname -r
section device_nodes
for node in /dev/rknpu /dev/rknpu0 /dev/misc/rknpu /dev/dri/card* /dev/dri/renderD*; do
    [ -e "$node" ] || [ -L "$node" ] || continue
    ls -l "$node"
    printf 'shell_access %s read=' "$node"
    if [ -r "$node" ]; then printf yes; else printf no; fi
    printf ' write='
    if [ -w "$node" ]; then printf yes; else printf no; fi
    printf '\n'
done
section kernel_interfaces
for entry in /sys/class/drm/card[0-9]* /sys/class/drm/renderD* /sys/class/misc/*rknpu* /sys/class/devfreq/*npu*; do
    [ -e "$entry" ] || continue
    ls -ld "$entry"
    readlink -f "$entry/device/driver" 2>/dev/null
    show_file "$entry/dev"
    show_file "$entry/device/uevent"
done
for entry in /sys/devices/platform/*npu*; do
    [ -e "$entry" ] || continue
    ls -ld "$entry"
    ls -ld "$entry/driver" "$entry/drm" "$entry/misc" 2>/dev/null
    show_file "$entry/uevent"
done
show_file /sys/module/rknpu/version
grep -iE 'rknpu|rockchip.*npu' /proc/misc /proc/devices /proc/modules 2>/dev/null
section runtime_libraries
for root in /vendor/lib64 /system/lib64 /system_ext/lib64 /odm/lib64 /product/lib64 /vendor/lib /system/lib; do
    [ -d "$root" ] || continue
    printf 'search_root %s\n' "$root"
    find "$root" -maxdepth 3 \( -type f -o -type l \) \( -iname '*rknn*' -o -iname '*rknpu*' \) -print 2>/dev/null
done
section runtime_services
for exe in /vendor/bin/rknn_server /system/bin/rknn_server /odm/bin/rknn_server; do
    [ -e "$exe" ] && ls -l "$exe"
done
section board_identity
for entry in /proc/device-tree/compatible /proc/device-tree/model; do
    if [ -r "$entry" ]; then
        printf '%s: ' "$entry"
        tr '\000' '\n' < "$entry"
    fi
done
section limitations
printf '%s\n' 'Filesystem visibility and mode checks only; no driver ioctl, model load or inference.' \
    'ADB shell identity does not establish app/privileged-service access.' \
    'Runtime search excludes application private data and unrelated filesystem trees.'
exit 0
ANDROID
