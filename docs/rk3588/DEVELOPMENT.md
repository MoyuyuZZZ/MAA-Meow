# RK3588 / reDroid 开发

本轮以 Ubuntu VM 的 `~/maa-rk3588` 为开发目录，
在 `dev` 保留 MaaMeow 原有 CPU 后端、Android 本地截图、触控和任务执行。
上游构建说明见 [BUILDING](../zh-cn/develop/BUILDING.md)，本文件仅补充本 Fork 的流程。

## 构建

在 Linux x86_64 主机安装 Git、Python 3.12+、curl、unzip 后：

```bash
python3 scripts/rk3588/bootstrap.py
bash scripts/rk3588/build-cpu.sh
```

工具链装在已忽略的 `.cache/rk3588/`，不需要 sudo。
JDK、SDK command-line tools、MaaCore 版本及下载校验值在
`scripts/rk3588/toolchain.lock.json`；Gradle 版本和 SHA-256 由原有 Wrapper 固定。
SDK 的已安装修订记录在 `.cache/rk3588/sdk-installed.txt`。
SDK 包名中 platform-tools 与 platform 修订可能更新，需保留该记录和缓存；
这不是位级一致构建的保证。依赖仓库仍需联网，未启用全量离线依赖锁定。

构建仅 ARM64 Debug，2 个 Gradle worker，3 GiB JVM 堆，关闭本地 LTO。
APK 位于 `app/build/outputs/apk/debug/`。不使用正式签名或遥测配置。
Debug 包沿用原 applicationId；**不要直接安装到正式 MaaMeow 所在实例**。
安装前另行说明隔离方式和影响，并获得用户同意。
bootstrap 中的 Core “deploy” 仅指向仓库写入忽略的资源/so，不涉及 ADB。

MaaCore 使用官方预编译包，不拉取或修改其源码。
Android OCR 继续使用上游 ncnn 模型；本轮不增加 RKNN、Vulkan 或其他计算后端。
工具下载中断后可重新执行 bootstrap，已通过校验的完整缓存会复用。

## 后续最小验证

1. 只读确认 reDroid Android ABI/API、RKNN 设备节点和动态库可见性，
   区分 fnOS 相册正在使用 NPU 与 Android 内可调用 NPU。
2. 在独立测试包/实例内，以一张固定 OCR 图片验证模型转换、加载和单次推理；
   先说明新代码和部署范围，再实施。
3. 对同一图片记录 CPU/NPU 文本结果、预处理、推理、后处理及总耗时，
   冷/热启动分开；保留 CPU 回退。仅在 NPU 路径不可用时评估 GPU。
4. 单独记录截图采集和整条任务延迟。已有取帧调用 1–2 ms
   不作为端到端延迟或 NPU 性能证据。

## 容量

本轮单 ABI、预编译 Core 的环境预计使用约 15–25 GiB（含工具、缓存和产物），
64 GiB 虚拟磁盘足够。根 LV 已由用户扩为 55 GiB，ext4 显示约 54 GiB。
后续完整 Core 编译、多版本 NDK 或多工作树应先检查实际占用，再申请扩容。
