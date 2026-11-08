# OCR 模型 RKNN 适配性评估与 rec 验证

2026-11-08。开发 VM：<user>@<dev-vm>，~/maa-rk3588，dev。
前半部分为只读与静态分析；后半部分记录用户确认后的日志统计、rec 转换、模拟器精度和设备探针结果。
前置结论见 [RKNN 预检](RKNN-PREFLIGHT.md)：官方 MobileNet 已在 ADB shell 中完成真实推理。

## MaaCore 的 Android OCR 实现

数据来自 MAA v6.21.4（提交 52d0b2acaca116585b02e1e6a9da303460f81a24）
src/MaaCore/Config/Miscellaneous/OcrPackNcnn.cpp，只读获取：

- Android 端用 ncnn 在 CPU 上运行（关闭 Vulkan 与 FP16，2–4 线程，绑大核）。
  另有 libonnxruntime.so，用于 resource/onnx 下的 4 个非 OCR 模型。
- rec：先按高度 48 等比缩放，宽度上限 320，右侧用灰色 127 填充，因此输入**固定为 1×3×48×320**；
  归一化 (x−127.5)/127.5，BGR 通道顺序；输出做 CTC 贪心解码。
- det（DBNet）：长边缩到 960 以内，宽高各自按 32 取整对齐，所以**输入尺寸随 ROI 变化**；
  后处理包括阈值、轮廓、Clipper 外扩，再透视裁切，逐行送入 rec。
- 调用分两种模式：without_det 时整张 ROI 只跑一次 rec；否则跑 det→rec 流水线。
  源码注释记录过 det 对缩放比例和框的松紧很敏感（曾导致误识）。

部署的 ncnn 由上游用 pnnx 从同一份 ONNX 转换而来（rec inputshape [1,3,48,320]，
det [1,3,640,640]，均为 FP32，上游记录与 onnxruntime 对比 cos=1.0），
所以 ONNX 可以作为 RKNN 转换的源模型和精度参考。

## 模型清单与静态检查

复现：

~~~bash
source "$HOME/.config/maa-dev/proxy.sh"
python3 scripts/rk3588/inspect-ocr-onnx.py
~~~

脚本按 scripts/rk3588/ocr-onnx.lock.json 下载上游 ONNX，核对 Git blob SHA-1 和 SHA-256；
用不依赖第三方包的最小 protobuf 解析器读取 opset、输入输出形状和算子统计，
并逐个对照 RKNN-Toolkit2 2.3.2 的 ONNX 算子支持表（同样锁定）。
报告位于忽略目录 .cache/rk3588/ocr-eval/onnx-inspect.json 和 inspect.log。
**算子在表中列出不代表一定在 NPU 上执行，**是否回退 CPU 要看实际转换日志。

| 模型 | 来源 | opset | 输入 | 输出 | 不支持算子 | 支持表备注 |
| --- | --- | --- | --- | --- | --- | --- |
| PaddleOCR det（中文 WordOcr） | ch_PP-OCRv3_det | 11 | N×3×H×W 动态 | N×1×H×W | 无 | Resize 仅 nearest/bilinear |
| PaddleOCR rec（中文，6902 类） | 上游训练产物（version.txt 为训练日志），SVTR-LCNet 结构 | 9 | N×3×48×W | N×T×6902 | 无 | Slice/Softmax 要求 batch 1 |
| PaddleCharOCR det | en_PP-OCRv3_det | 11 | 同上 | 同上 | 无 | 同上 |
| PaddleCharOCR rec（97 类，与 YoStarEN 相同） | en_PP-OCRv3_rec | 9 | N×3×48×W | N×T×97 | 无 | 同上 |
| operators_det | PyTorch（YOLO 类） | 17 | 1×3×640×640 | 1×5×8400 | 无 | Resize/Slice/Softmax |
| deploy_direction_cls | PyTorch | 14 | 1×3×96×96 | 1×4 | 无 | LogSoftmax 要求 batch 1 |
| skill_ready_cls | PyTorch | 17 | batch×3×64×64 | batch×3 | 无 | — |
| BlackFlow_corridor_net | PyTorch | 17 | batch×3×40×160 | batch | 无 | — |

rec 算子：Conv/BN/HardSwish 分解（Clip、Div、Mul）、MatMul+Softmax 注意力、
分解形式的 LayerNorm（ReduceMean、Sub、Pow、Sqrt、Div），还有 paddle2onnx
处理动态维度产生的 Shape/Cast/Slice/Squeeze。中文 rec 有 23 个 Cast。
外服 JP/KR/txwy 的 rec 与中文 rec 结构相同，只是字表不同，本轮未单独锁定。

## 适配性判断

| 对象 | 判断 | 依据 | 主要风险 |
| --- | --- | --- | --- |
| rec | **高** | MaaCore 已固定 1×3×48×320，可直接作为静态形状转换；算子全部在表中；rknn_model_zoo v2.3.2 的 PPOCR-Rec（同类 SVTR-LCNet）在 RK3588 上以 fp（不量化）转换 | opset 9 中由动态维度产生的 Shape/Cast 能否在固定形状后常量折叠；分解 LayerNorm 和注意力在 FP16 下的精度；model zoo 注释提到部分 Add 节点可能要回退 CPU 才能保精度 |
| det | **中** | 算子全部在表中；model zoo 的 PPOCR-Det 以 i8 量化在 RK3588 上运行 | 输入尺寸随 ROI 变化：需要预设一组动态形状（2.3.2 每个输入最多 512 组），或改用固定尺寸填充，而后者会偏离 CPU 预处理、可能影响框精度；i8 量化需要游戏截图做校准，FP16 更稳但更慢 |
| 4 个非 OCR 模型 | 转换上高 | 静态形状（batch 固定为 1 即可），算子简单 | 调用频率较低，不在当前 OCR 优先的范围内 |

**结论：rec 是首选验证对象，det 放在第二步。**

## 运行身份与集成前提

- 生产实例中，MaaCore 所在进程是 com.aliothmoon.maameow:shizuku_service，用户为 shell
  （只读 ps 确认；同时还有 shizuku_server 进程，build type 为 user）。
  这与 ADB shell 探针的 uid 2000 相同，节点访问结论**很可能**可以沿用；
  但该进程由 Shizuku 的 app_process 启动，从 App 库目录加载 librknnrt.so
  的链接器命名空间等问题仍需在实际进程中验证。
- OcrPackNcnn 编译在预编译的 libMaaCore.so 里。要换用 RKNN 后端，必须从源码构建 MaaCore
  （新增 RKNN rec 路径，保留 ncnn 回退），并在 APK 中附带 librknnrt.so。
  这属于修改 Core 和部署 APK，须先另行说明范围并获得确认。
- librknnrt.so 采用 Rockchip 专有版权声明（rknn-toolkit2 仓库 LICENSE），
  不是开源许可。随 APK 再分发前需要确认条款；当前只在独立探针中临时使用。

## 真实任务日志统计

2026-11-08 用户提供生产实例的 debug 日志包（maa_logs_20261108_113534.zip，
放在 VM 的 maa-log/，已加入本地 .git/info/exclude，不提交；解压到忽略目录）。
包信息：MaaMeow 0.25.3 release，Core v6.21.4，Shizuku（uid 2000），后台模式 P720。
统计脚本 scripts/rk3588/analyze-ocr-log.py 只输出数字，不输出识别文本：

~~~bash
python3 scripts/rk3588/analyze-ocr-log.py <解压目录>/asst.bak.log <解压目录>/asst.log
~~~

覆盖 2026-11-04 至 11-08 的日常任务，共 117 次任务链，墙钟合计约 4.8 小时，OCR 28,074 次：

| 调用类型 | 次数 | 平均 / p50 / p90（ms） | 合计（s） |
| --- | --- | --- | --- |
| WordOcr rec（中文，without_det） | 16,375 | 26.3 / 26 / 30 | 431.3 |
| CharOcr rec | 6,496 | 23.3 / 22 / 27 | 151.7 |
| WordOcr det+rec 流水线 | 5,828 | 107.9 / 60 / 228 | 628.8 |
| CharOcr det+rec 流水线 | 27 | 210.6 / 55 / 820 | 5.7 |

流水线每次平均 2.8 个框（p50 为 1，p90 为 7）。cost 是 MaaCore 记录的整次调用耗时，包含前后处理。

| 任务链 | 次数 | OCR 占墙钟时间 | 构成 |
| --- | --- | --- | --- |
| OperBox（干员识别） | 10 | 52.6% | 几乎全是 rec |
| StartUp | 29 | 14.9% | 全是流水线 |
| Depot | 10 | 9.6% | 几乎全是 rec |
| Recruit | 13 | 8.7% | 流水线 |
| Mall | 10 | 7.1% | 流水线 |
| Infrast | 22 | 4.6% | rec 与流水线各半 |
| Fight | 13 | 1.9% | 混合 |
| Award | 10 | 0.4% | 流水线 |
| **合计** | 117 | **7.0%** | rec 568 s，流水线 626 s |

任务链墙钟时间包含动画和等待。日常任务里 OCR 平均占比不高，但 OperBox、Depot 这类
rec 密集任务占比明显更高。日志不含集成战略、自动战斗作业等复杂任务。

## rec 转换与精度验证

### 转换环境

scripts/rk3588/setup-rknn-toolkit.sh 全部装在 .cache/rk3588/ 下（约 1.6 GiB），
不改系统 Python、PATH 或 ~/.local/bin：

- uv 0.13.6，官方 release 资产 SHA-256 f29c3e94…3645，两处来源核对一致；
  由 uv 管理的 CPython 3.12.16，安装时加 --no-bin。
  首次试装未加该参数，uv 新建了 ~/.local/bin/python3.12 链接；已确认是新建的，
  只删除了这个链接和空的 bin 目录。
- 依赖：scripts/rk3588/rknn-toolkit-requirements.in 按官方 requirements_cp312-2.3.2.txt 约束，
  锁定为带哈希的 rknn-toolkit-requirements.txt（--exclude-newer 2025-06-30，
  rknn-toolkit2 2.3.2 的 SHA-256 为 504d7f…41ef）。
  torch 用 PyTorch CPU 索引的 2.4.0+cpu 直链 wheel（带哈希），以免下载 CUDA 包。
  opencv-python 依赖系统 libGL（需要 sudo），所以通过 rknn-toolkit-overrides.txt
  换成提供同一 cv2 模块的 opencv-python-headless 4.11.0.86。
- 安装后 `from rknn.api import RKNN` 成功。

### 转换

scripts/rk3588/convert-ocr-rec.py：固定 1×3×48×320；mean/std 设为 127.5，等价于 MaaCore 的
(x−127.5)/127.5，因此设备端直接输入 uint8 NHWC BGR；FP16，不量化。

| 模型 | RKNN 大小 | 层分配 |
| --- | --- | --- |
| PaddleOCR rec（中文） | 6,600,440 bytes | 除输入、输出和末尾一个 Reshape 外全部在 NPU；注意力融合为 exSDPAttention，LayerNorm 融合为 exNorm |
| PaddleCharOCR rec | 5,468,534 bytes | 同上 |

opset 9 中由动态维度产生的 Cast/Shape 已在固定形状后常量折叠。日志里有一行与调试输出交错的
`E RKNN: Unkown op target: 0`，构建和导出均成功，含义未查明，暂视为非致命。

### 测试数据

scripts/rk3588/compare-ocr-rec.py 使用日志包中的 8 张 1280×720 截图：
- 中文：用 det ONNX 按 MaaCore 的预处理找出文本行，透视裁切后得到 321 行。
  后处理是近似实现，只用于生成测试行。
- 字符：按日志里每帧 CharOcr 记录的绝对 ROI 裁出 91 行，日志同时给出 MaaCore（ncnn）的识别文本。

rec 预处理与 OcrPackNcnn::recognize_line 一致（高度 48，宽度上限 320，右侧灰 127 填充）。
裁切图和逐行结果只保存在 .cache/rk3588/ocr-rknn/。

### 精度结果

| 对比 | 中文 rec（321 行） | 字符 rec（91 行） |
| --- | --- | --- |
| x86 模拟器 vs onnxruntime：文本一致 | 319/321 | 91/91 |
| 模拟器输出余弦相似度最小值 | 0.999989 | 1.000000 |
| **设备 NPU vs onnxruntime：文本一致** | **316/321** | **91/91** |
| 设备逐时间步 argmax 一致率（最小 / 平均） | 0.975 / 0.9996 | 1.0 / 1.0 |
| 设备完整输出余弦（前 8 行最小值） | 0.999823 | 1.000000 |
| 设备 vs MaaCore ncnn 日志 | — | 91/91 |

中文的 5 处差异只来自 2 个不同的小区域，各在多张截图中重复出现：
26×23 的 `%`→`w`（onnxruntime 置信度 0.446）和 28×13 的 `-6`→`-c`（0.598）。
两者都是低置信度的单字符结果，很可能不是有效文字。这次用的 321 行里，onnxruntime
置信度低于 0.6 的非空行有 55 行。check-rknn-rec-result.py 的判定标准是“设备不差于模拟器”，
因此按该标准中文记为 FAIL（设备 316，模拟器 319）；有效文本行没有发现差异。

## 设备端 rec 探针

范围经用户确认后执行一次：scripts/rk3588/run-rknn-rec-once.sh --confirmed。
在 /data/local/tmp 下新建唯一的 maa-rknn-rec.* 目录，推送 rknn-rec 探针
（scripts/rk3588/probe-rknn-rec.c，AArch64 PIE，只依赖 libdl/libc）、Runtime 2.3.2、
两个 RKNN 模型和两份文本行输入（约 38 MB），核对 SHA-256 后以 ADB shell 身份运行，
拉回结果后只删除这些文件和该目录，并核验已删除（maa-rknn-rec.hsvSCL）。
不安装 APK，不用 root，也不改动生产服务。

| 阶段（ms） | 中文 rec 逐行 321 次 | 字符 rec 逐行 91 次 | 中文热跑首行 50 次 | 字符热跑首行 50 次 |
| --- | --- | --- | --- | --- |
| rknn_init | 14.5 | 15.0 | — | — |
| inputs_set median | 2.57 | 2.52 | — | — |
| run median（min / p90） | 27.1（24.4 / 28.4） | 21.4（19.9 / 23.0） | 27.1（25.4 / 27.9） | **9.9**（8.9 / 21.0） |
| outputs_get+argmax median | 8.09 | 0.13 | — | — |
| 合计 median | 37.8 | 24.1 | 35.8 | 12.5 |

**这组耗时受 NPU 动态调频影响，不能直接和 CPU 比较。** 只读查询宿主 devfreq：
fdab0000.npu 的调频策略为 rknpu_ondemand，可用频率 300 MHz–1 GHz，空闲时为 300 MHz。
字符热跑阶段 run 从约 21 ms 降到中位 9.9 ms，说明测量期间频率在爬升。
探针没有记录频率，所以无法确定每段测量处于哪个频率。
另外，中文模型输出 40×6902，FP16 转 float 的 outputs_get 约 8 ms，
这部分可以通过直接读取 FP16 输出或只取 argmax 来优化。

CPU 参考是日志中 MaaCore ncnn 的整次 rec 调用：中文 p50 26 ms，字符 p50 22 ms。
目前 NPU 在低频或爬频阶段并不比 CPU 快；字符模型在频率升高后 run 约 9–10 ms。
**结论：rec 在 NPU 上精度可用；速度优势需要在固定频率下复测确认，目前不能断言。**

## 下一步

1. 补充耗时测量（需确认后在设备执行）：探针先预热，测量期间同时采样
   /sys/class/devfreq/fdab0000.npu/cur_freq，并用 RKNN_FLAG_COLLECT_PERF_MASK
   取逐层耗时；分别测连续调用和间隔调用（模拟 MaaCore 真实节奏）。
   不修改宿主调频策略（属于宿主驱动配置，按约定不动）。
2. 优化输出读取：中文模型使用 FP16 输出（want_float=0），或把 argmax 留在 NPU 侧，减少约 8 ms 拷贝和转换。
3. 如果固定频率下 NPU rec 仍不明显快于 CPU，收益就主要在于减轻 CPU 负载（与游戏渲染争抢资源），
   此时需要另行评估是否值得修改 Core；det 的动态形状方案暂缓。
4. 复杂任务（集成战略、自动战斗作业等）的日志可在后续评估整体收益时再提供。
