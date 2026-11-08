# RK3588 / reDroid 开发约定

- 在 `dev` 开发；`main` 只作为同步基线。保留已有修改，不强推或删除分支。
- origin 为 MoyuyuZZZ/MAA-Meow；upstream 为 Aliothmoon/MAA-Meow。
  推送前向用户展示仓库、分支和摘要，获确认后仅推送 origin/dev；不向上游提 PR。
- 保留 Android 本地截图、触控、任务执行和 CPU 后端。
  原生取帧调用约 1–2 ms 是已有测量，不代表画面端到端延迟。
- 环境初始化及 ARM64 CPU 基线已验收；RKNN 预检及官方 MobileNet 单次推理（ADB shell）已通过。
  OCR 评估见 docs/rk3588/OCR-RKNN-ASSESSMENT.md：rec 已转为 FP16 RKNN，设备精度可用；
  复测显示 NPU 在宿主 ondemand 调频下多停在 300 MHz，此时与 CPU 相当；1 GHz 下字符 rec 合计约为 CPU 的一半。
  不修改宿主调频策略；如需固定频率对照，由用户自行在宿主上执行并恢复。
  CPU 实测（常规任务 + 自动肉鸽）显示整机不紧张，OCR 平均约 0.12–0.24 核；
  2026-11-08 用户确认收益太小，NPU OCR 方向已收尾：不接入 MaaCore、不部署 APK；工具与文档保留以便复现。
  不实现完整加速后端；优先 RKNN/NPU OCR，GPU 备选。
- 转换环境：bash scripts/rk3588/setup-rknn-toolkit.sh，之后用 .cache/rk3588/rknn-venv/bin/python。
  用户日志包放在 maa-log/（本地 exclude，不提交），只统计数字，不在提交中引用识别文本。
- MaaCore 源码按需只读获取；新 Fork、修改 Core 或部署 APK 前先说明范围。
- 不覆盖正式 MaaMeow，不修改 fnOS 驱动/相册运行库，不重启或重建生产 reDroid。
- sudo 命令由用户在自己的终端执行。不得扫描整个家目录或输出密钥、Token。
- 签名文件、真实账号配置、下载包和构建缓存不得提交；保留上游 README 和文档。
- 只做必要的构建与产物检查，不提前开发 Web 平台或重构截图链路。
- 复现入口见 `docs/rk3588/DEVELOPMENT.md`，验收见 `docs/rk3588/ACCEPTANCE.md`。

- 需要下载代理时，在当前 shell 显式 source 用户提供的环境文件；不得假设跨终端继承。
- Gradle 个人代理仅放在个人 Gradle 用户目录，不写入仓库；局域网 ADB/SSH 保持原连接。
- 不改默认路由、不启用全局 TUN、不关闭 TLS 校验、不在公网暴露代理。
- 提交内容不写局域网地址、VM 用户名和主机名；设备脚本的 ADB 序列号通过参数或 MAA_ADB_SERIAL 提供。
  真实地址、主机指纹和脱敏对照由用户离线保管（原 local/ENVIRONMENT.md，已从 VM 删除）；
  运行设备脚本前向用户确认 ADB 序列号并通过 MAA_ADB_SERIAL 提供。
- 独立 RKNN 探针与 MaaMeow 分离；设备端部署/执行前列明临时文件、命令及影响范围。
