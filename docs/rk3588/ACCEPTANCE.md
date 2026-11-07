# 环境与 CPU 基线验收

日期：2026-11-07。环境初始化、ARM64 CPU 基线构建及静态验收通过。
未进行设备安装或应用运行验收。

| 项目 | 结果 |
| --- | --- |
| 主机与目录 | <user>@<dev-vm>，Ubuntu 26.04.1 LTS x86_64；~/maa-rk3588 |
| SSH | <VM_HOST>；备用 VPN <VM_VPN_HOST>；用户核验了主机指纹 |
| 容量 | sda 64 GiB；用户扩根 LV 至 55 GiB，ext4 显示约 54 GiB；验收时剩余约 37 GiB |
| 实际占用 | 项目、工具、依赖缓存和产物合计约 7.9 GiB；本轮无需增加虚拟磁盘 |
| 内存 | 约 7.2 GiB RAM、4 GiB swap；构建限制 2 个 worker、3 GiB JVM 堆 |
| 初始状态 | 目标项目目录为空；未覆盖已有修改 |
| main 基线 | 9b985bc596dcbe39402febf5074a127a166d1b4e；Fork/上游经 API 核对一致 |
| Git 历史 | 完整 main bundle 经 SSH 导入；整包 SHA-256 与 git bundle verify 通过 |
| 分支 | 在 dev 开发；main 保持同步基线 |
| origin | https://github.com/MoyuyuZZZ/MAA-Meow.git |
| upstream | https://github.com/Aliothmoon/MAA-Meow.git；push URL 为 DISABLED |
| 认证与身份 | VM gh 登录 MoyuyuZZZ，有 Fork 写权限；用户确认的 Git 身份仅设置于本仓库 |
| 推送保护 | push.default=nothing；未推送，须用户确认后仅推送 origin/dev |
| reDroid | ADB 已连接 <REDROID_ADB_SERIAL>；arm64-v8a，Android 13 / API 33 |
| 工具链 | Temurin 25.0.5+8；Gradle 9.8.2；NDK 29.0.14206865；CMake 3.22.1 |
| SDK | 平台 android-37.0 r2；AGP 实际使用 Build Tools 36.0.0；platform-tools 37.0.1 |
| SDK 安装器 | command-line tools 23.0；实际 Android CLI 1.1.16717390 |
| MaaCore | 官方预编译 v6.21.4，SHA-256 通过；未获取或修改 Core 源码 |
| OCR 资源 | 8 组 ncnn bin/param；原始资源共 9573 文件、262.7 MiB |
| 构建结果 | :app:assembleDebug 成功，21 分 43 秒，62 个任务执行；退出码 0 |

## APK 产物

- 路径：`~/maa-rk3588/app/build/outputs/apk/debug/app-debug.apk`
- 大小：296,140,678 bytes（282.4 MiB）。
- 构建源码提交：`1ecd22b770fc8eece897df30e4970c1c2ef1a71e`。
  此记录及 Build Tools 锁定补充在后续提交中，不改变已验收 APK。
- versionCode：980；versionName：1ecd22b7。
- 包名：`com.aliothmoon.maameow`；minSdk 28、targetSdk 36、compileSdk 37。
- 仅 `arm64-v8a`；11 个原生库的 ELF 机器类型均为 AArch64。
- ZIP CRC 检查通过；APK 内有 8 个 ncnn bin、8 个 ncnn param。
- `apksigner verify` 通过，使用 Android Debug 证书和 APK v2 签名。
- SHA-256：`8405201212c711938d9b4a33b56c82c6f80f1b623e7df69a3375a92fb24b38c3`。

包名沿用上游，不能直接安装到正式 MaaMeow 所在实例。
部署前需先说明隔离方式和影响范围，并经用户确认。

## 复现与证据

构建命令：`bash scripts/rk3588/build-cpu.sh`。
工具准备见 [开发计划](DEVELOPMENT.md)，固定版本与校验值见
`scripts/rk3588/toolchain.lock.json`。

本机忽略目录中保留：
- `.cache/rk3588/setup.log`：工具与 Core 准备过程。
- `.cache/rk3588/sdk-installed.txt`：SDK 实际安装清单（另有早期安装的 Build Tools 37.0.0，基线使用 36.0.0）。
- `.cache/rk3588/build-01.log`、`build-01.exit`：首次构建输出与退出码。
- `.cache/rk3588/apk-verification.json`、`apk-signature.txt`：静态验收和签名证据。

上游 i18n 检查通过，有 10 条非阻塞翻译警告；另有 Gradle 弃用提示、
libjnidispatch.so 无法剥离符号的打包提示。未修改相关上游代码。
API 下载入口与完整缓存绕过了本轮 github.com 直连超时；
清空缓存后的环境重建仍需要可用的依赖网络；后续代理复核见下节。

## 下载代理复核

2026-11-07 在同一开发 VM 复核用户已配置的下载代理：

- 用户级隧道服务处于 active；每次下载命令前显式 source 个人环境文件。
- 使用此前下载缓慢的官方 Gradle 9.8.2 ZIP，限量读取前 8 MiB：
  HTTP 206，8,388,608 bytes，22.664188 秒，370,126 bytes/s（约 361 KiB/s）。
  这是单次分段下载结果，不能视为全包持续速度或稳定性保证；未重新进行直连测速。
- gh API 认证为 MoyuyuZZZ；origin 的 HTTPS git ls-remote 成功，
  main 仍为原基线，远端未发现 dev；未测试写入或推送。
- 初始化和构建统一默认使用个人 ~/.gradle，并尊重 GRADLE_USER_HOME。
  原项目内 Gradle 缓存约 2.1 GiB 已迁移到个人目录，未覆盖已有个人配置。
  JDK/Android SDK 仍使用项目内固定版本；根文件系统剩余约 36 GiB。
- 使用离线 Gradle help 检查缓存与项目配置，11 秒成功，5 个任务中 1 个执行、4 个已最新。
  临时初始化检查确认 JVM 的 HTTP/HTTPS 代理与 nonProxyHosts 属性
  均与个人配置一致，只输出通过标记，不输出属性值。
  该检查没有验证 Gradle 在线依赖下载；实际下载验证由上面的 curl 完成。
- Shell 语法、Python 语法及 git diff --check 通过。
  本轮只调整环境入口和说明，未重新构建或改写已验收 APK。
- 个人网络配置和凭证未加入 Git；未修改路由、TLS 校验、代理监听、
  ADB/SSH 或生产实例。完整 CPU 构建已完成，没有待恢复的下载任务。

本机检查日志：.cache/rk3588/proxy-gradle-check.log（忽略，不提交）。

## 边界与下一步

- 未新增 NPU/GPU 推理后端，保留 Android 本地截图、触控、任务执行及 CPU 基线。
- 未安装 APK、未操作 fnOS 驱动/相册运行库、未重启或重建生产 reDroid。
- 未运行设备端 OCR、功能或延迟测试。取帧调用约 1–2 ms 是用户已有测量，
  本轮未复测，也不代表完整画面端到端延迟。
- 下一阶段先只读确认 Android 内 RKNN 设备节点、动态库和 ABI 可见性；
  再为隔离的单图 OCR 探针说明修改/部署范围，对比 CPU 与 RKNN 结果及分阶段耗时。
