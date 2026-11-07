# 环境与 CPU 基线验收

日期：2026-11-07。此记录区分已验证项与待完成项，不代表设备运行验收。

| 项目 | 结果 |
| --- | --- |
| 执行主机 | Ubuntu 26.04.1 LTS，用户 moyu，主机 <dev-vm> |
| SSH | <VM_HOST>；备用 VPN <VM_VPN_HOST>；主机指纹由用户核验 |
| 项目目录 | ~/maa-rk3588 |
| 磁盘 | sda 64 GiB；用户将根 LV 扩到 55 GiB，ext4 约 54 GiB；扩容后可用约 44 GiB |
| 内存 | 约 7.2 GiB RAM，4 GiB swap |
| 初始仓库 | 目标目录为空；未覆盖既有修改 |
| 基线 | 9b985bc596dcbe39402febf5074a127a166d1b4e；Fork 与上游 main 经 API 核对一致 |
| Git 历史 | 经 SSH 导入完整 main Git bundle，整包 SHA-256 与 git bundle verify 通过 |
| 分支 | dev 开发；main 保持同步基线 |
| origin | https://github.com/MoyuyuZZZ/MAA-Meow.git |
| upstream | https://github.com/Aliothmoon/MAA-Meow.git；push URL 为 DISABLED |
| 认证 | VM gh 登录 MoyuyuZZZ，对 Fork 有写权限；仓库级 Git 凭证助手使用 gh |
| 提交身份 | 经用户确认设置在仓库级，不改全局配置 |
| 推送 | 未推送；push.default=nothing；须用户确认后显式推送 origin/dev |
| reDroid | <REDROID_ADB_SERIAL> TCP 可达；尚未进行 Android 运行验收 |
| SDK 下载 | command-line tools 23.0 已下载并通过官方 SHA-1 校验 |
| 工具链安装 | 进行中；以最终构建记录为准 |
| CPU APK | 尚未构建完成 |
| 修改范围 | 代理约定、开发/验收文档、工具链锁定与 ARM64 CPU 构建脚本 |
| 本轮边界 | 未修改截图/触控/任务链路或 Core；未部署 APK；未操作 fnOS 或生产 reDroid |

## 验证记录

- 新增 Python / Bash 脚本的语法检查已通过。
- 下载固定 JDK、SDK 和 Core，校验值来自各自官方元数据。
- 原生取帧调用约 1–2 ms 为用户已有测量，本轮未复测，不作端到端延迟结论。
- 最小后续任务见 [开发计划](DEVELOPMENT.md#后续最小验证)。

## 待补记录

工具实际版本、SDK 包修订、构建命令及日志、APK 路径/大小/SHA-256/ABI/签名检查。
