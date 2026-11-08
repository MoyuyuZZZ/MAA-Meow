# RK3588 / reDroid 实验记录

本目录记录了在 RK3588 宿主的 reDroid 容器中，尝试用 RKNPU 加速 MaaMeow 推理的过程。
上游 MaaMeow 的文档和 README 保持原样，本目录只描述本 Fork dev 分支上的实验。

## 结论

**NPU OCR 方向已于 2026-11-08 收尾，结论是不值得做。** rec 模型在 NPU 上精度可用，但在宿主默认调频下不比 CPU 快，
实测 OCR 占用的 CPU 也很少（平均约 0.12–0.24 / 8 核），抵不上修改 MaaCore 和长期维护的成本。
完整数据和判断依据见 [OCR RKNN 评估](OCR-RKNN-ASSESSMENT.md) 开头的“结论先行”。

本分支没有修改 MaaCore 或 MaaMeow 的功能代码，也没有部署 APK；新增内容只有 scripts/rk3588/ 下的脚本、锁文件和本目录的文档。

## 文档

| 文档 | 内容 |
| --- | --- |
| [DEVELOPMENT.md](DEVELOPMENT.md) | 开发环境、ARM64 CPU 基线构建、下载代理约定、各阶段进展 |
| [ACCEPTANCE.md](ACCEPTANCE.md) | 环境与 CPU 基线 APK 的验收记录 |
| [RKNN-PREFLIGHT.md](RKNN-PREFLIGHT.md) | Android 内 NPU 可见性检查、运行库加载、官方 MobileNet 推理验证 |
| [OCR-RKNN-ASSESSMENT.md](OCR-RKNN-ASSESSMENT.md) | OCR 模型适配性、rec 转换与精度、带频率采样的耗时、真实任务的 CPU 压力实测与结论 |

## 复现入口（scripts/rk3588/）

| 阶段 | 脚本 | 是否连接设备 |
| --- | --- | --- |
| 工具链与 CPU 基线构建 | bootstrap.py、build-cpu.sh | 否 |
| NPU 可见性检查 | check-rknn-access.sh | 是，只读 |
| 探针构建 | build-rknn-probe.sh（同时准备 MobileNet 样例） | 否 |
| 运行库加载 / MobileNet 推理 | run-rknn-infer-once.sh --confirmed | 是，推送临时文件后清理 |
| OCR 模型静态检查 | inspect-ocr-onnx.py | 否 |
| 转换环境 | setup-rknn-toolkit.sh | 否 |
| rec 转换与模拟器对比 | convert-ocr-rec.py、compare-ocr-rec.py | 否 |
| 设备 rec 精度与耗时 | run-rknn-rec-once.sh、run-rknn-rec-bench.sh（--confirmed） | 是，推送临时文件后清理 |
| 日志与 CPU 统计 | analyze-ocr-log.py、sample-cpu-load.sh、analyze-cpu-samples.py、correlate-ocr-cpu.py | 采样为只读 |

设备脚本需要通过参数或 MAA_ADB_SERIAL 提供 ADB 序列号，并且必须先获得设备所有者的同意。
仓库中不写局域网地址、主机名或用户名。原始测量数据和用户日志没有提交。
