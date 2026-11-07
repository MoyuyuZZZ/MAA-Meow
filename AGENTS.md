# RK3588 / reDroid 开发约定

- 在 `dev` 开发；`main` 只作为同步基线。保留已有修改，不强推或删除分支。
- origin 为 MoyuyuZZZ/MAA-Meow；upstream 为 Aliothmoon/MAA-Meow。
  推送前向用户展示仓库、分支和摘要，获确认后仅推送 origin/dev；不向上游提 PR。
- 保留 Android 本地截图、触控、任务执行和 CPU 后端。
  原生取帧调用约 1–2 ms 是已有测量，不代表画面端到端延迟。
- 当前阶段仅环境初始化及 ARM64 CPU 基线。后续先验证 RKNN/NPU OCR，GPU 备选。
- MaaCore 源码按需只读获取；新 Fork、修改 Core 或部署 APK 前先说明范围。
- 不覆盖正式 MaaMeow，不修改 fnOS 驱动/相册运行库，不重启或重建生产 reDroid。
- sudo 命令由用户在自己的终端执行。不得扫描整个家目录或输出密钥、Token。
- 签名文件、真实账号配置、下载包和构建缓存不得提交；保留上游 README 和文档。
- 只做必要的构建与产物检查，不提前开发 Web 平台或重构截图链路。
- 复现入口见 `docs/rk3588/DEVELOPMENT.md`，验收见 `docs/rk3588/ACCEPTANCE.md`。
