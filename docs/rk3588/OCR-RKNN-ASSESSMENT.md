# OCR 模型 RKNN 适配性评估（静态）

2026-11-08。开发 VM：<user>@<dev-vm>，~/maa-rk3588，dev。
本轮只做只读与静态分析：没有安装转换工具，没有转换模型，也没有在设备上执行。
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

## 收益尚未量化

目前没有 MaaMeow 实际任务中 OCR 调用次数、rec/流水线比例和 CPU 耗时的数据。
MaaCore 日志会按“by OCR Rec / by OCR Pipeline, cost N ms”记录每次 OCR，
可以用一份真实任务日志统计，用来判断 rec 加速的上限，避免只用单模型耗时推断整体收益。

## 下一步（需确认后执行）

1. 在 VM 准备隔离的转换环境：RKNN-Toolkit2 2.3.2 只提供 cp36–cp312 wheel，
   VM 只有 Python 3.14，且没有 pip/venv。需要在忽略目录中引入独立的 Python 3.12，
   安装锁定版本的 rknn-toolkit2（cp312 x86_64 wheel，SHA-256
   504d7fcf6a792dfa874e25155de0afb5d6650f04a1a0d49b0886934bec4741ef）
   及官方 requirements_cp312-2.3.2.txt 中的依赖（torch ≤2.4、onnx、onnxruntime、numpy ≤1.26.4 等），
   预计占用 2–4 GiB，根分区剩余约 36 GiB。
2. 将中文 rec 转为 FP16 静态 1×3×48×320 RKNN，记录转换日志中的 CPU 回退层；
   在 x86 模拟器上用若干文本行与 onnxruntime 对比输出相似度和解码文本。
3. 设备端独立探针：同一批文本行比较 RKNN 与 CPU 参考结果，记录预处理、推理、解码耗时。
4. rec 通过后再评估 det 的动态形状方案。
