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

交互使用 java、adb 等工具时，先执行 `source scripts/rk3588/env.sh`。

JDK、Android SDK 和下载缓存装在已忽略的 `.cache/rk3588/`，不需要 sudo。
Gradle 默认使用个人 `~/.gradle`，也支持显式设置 `GRADLE_USER_HOME`；
bootstrap 与构建必须使用同一个 Gradle 用户目录。个人代理配置只保留在该目录。
JDK、SDK command-line tools、MaaCore 版本及下载校验值在
`scripts/rk3588/toolchain.lock.json`；Gradle 版本和 SHA-256 由原有 Wrapper 固定。
SDK 包及修订由锁文件固定，并通过 source.properties 核对；安装记录在
`.cache/rk3588/sdk-installed.txt`。API 37 的平台包名为 `platforms/android-37.0`。
命令行工具 23 使用 [Android CLI](https://developer.android.com/tools/agents/android-cli)，
脚本传入 `--no-metrics`，实际 CLI 版本另记在 `.cache/rk3588/android-cli-version.txt`。
Gradle 从官方发布 API 获取，校验后填入正常 Wrapper 缓存，不修改上游 Wrapper。
可用 `--tools-only` 暂不准备 Core 资源。依赖仓库仍需联网，未启用全量离线依赖锁定，
也不保证 APK 位级一致；保留下载缓存和安装记录以便复核。

构建仅 ARM64 Debug，2 个 Gradle worker，3 GiB JVM 堆，关闭本地 LTO。
APK 位于 `app/build/outputs/apk/debug/`。不使用正式签名或遥测配置。
Debug 包沿用原 applicationId；**不要直接安装到正式 MaaMeow 所在实例**。
安装前另行说明隔离方式和影响，并获得用户同意。
bootstrap 中的 Core “deploy” 仅指向仓库写入忽略的资源/so，不涉及 ADB。
重复执行会按上游脚本重建这些生成目录；请勿在其中保存手工修改。

MaaCore 使用官方预编译包，不拉取或修改其源码。
Android OCR 继续使用上游 ncnn 模型；本轮不增加 RKNN、Vulkan 或其他计算后端。
工具下载中断后可重新执行 bootstrap，已通过校验的完整缓存会复用。

## 下载网络

VM 下载前，在当前 shell 显式加载用户提供的环境文件，再运行命令：

```bash
source "$HOME/.config/maa-dev/proxy.sh"
python3 scripts/rk3588/bootstrap.py
bash scripts/rk3588/build-cpu.sh
```

curl、gh 和下载子进程继承本次 shell 的代理环境；不能依赖另一终端的变量。
Gradle JVM 使用个人 Gradle 用户目录中的 gradle.properties，不能仅依赖
HTTP_PROXY/HTTPS_PROXY。若覆盖 GRADLE_USER_HOME，需自行在对应目录准备个人配置。
项目不保存代理地址、认证信息或个人配置文件，也不自动 source 用户脚本。

代理仅用于下载；ADB、SSH、fnOS 和 reDroid 继续使用原有局域网连接。
保持 TLS 校验，不改变默认路由、开启全局 TUN 或公开代理监听端口。

## 后续最小验证

1. 只读可见性检查已完成：renderD128 对应 RKNPU，驱动 0.9.8；
   系统库搜索未发现 RKNN Runtime。检查及独立探针见 [RKNN 预检](RKNN-PREFLIGHT.md)。
   独立探针已通过官方 Runtime 2.3.2 加载及节点打开测试。
   2026-11-08 官方 MobileNet V1（RK3588）单次推理验证通过：Top-1 正确，热推理 run 约 5.2 ms；
   仅限 ADB shell 环境，不是性能验收。入口见 build-rknn-probe.sh 与 run-rknn-infer-once.sh。
2. 在独立测试包/实例内，以一张固定 OCR 图片验证模型转换、加载和单次推理；
   先说明新代码和部署范围，再实施。静态适配性评估见 [OCR RKNN 评估](OCR-RKNN-ASSESSMENT.md)：
   rec 固定为 48×320、算子全部在支持表中，优先验证；det 输入动态，放在后面。
   2026-11-08 rec 已完成转换（setup-rknn-toolkit.sh、convert-ocr-rec.py）、模拟器对比和设备探针
   （run-rknn-rec-once.sh）：字符 91/91，中文 316/321（差异均为低置信度单字符）。
   带频率采样的复测（run-rknn-rec-bench.sh）显示：宿主 rknpu_ondemand 下 NPU 大多停在 300 MHz，
   此时 rec 与 CPU 相当（中文 FP16 输出合计 33.6 ms，CPU 26 ms）；字符模型升到 1 GHz 后 run 为 9.7 ms。
   瓶颈在卷积主干。后续方向见评估文档的“下一步”。
3. 对同一图片记录 CPU/NPU 文本结果、预处理、推理、后处理及总耗时，
   冷/热启动分开；保留 CPU 回退。仅在 NPU 路径不可用时评估 GPU。
4. 单独记录截图采集和整条任务延迟。已有取帧调用 1–2 ms
   不作为端到端延迟或 NPU 性能证据。

## 容量

本轮单 ABI、预编译 Core 的环境预计使用约 15–25 GiB（含工具、缓存和产物），
64 GiB 虚拟磁盘足够。根 LV 已由用户扩为 55 GiB，ext4 显示约 54 GiB。
后续完整 Core 编译、多版本 NDK 或多工作树应先检查实际占用，再申请扩容。
