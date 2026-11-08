# Android RKNN 预检

2026-11-07。开发 VM：<user>@<dev-vm>，~/maa-rk3588，dev。
用户已验收环境及 CPU 基线，并同意进入下一步。已完成只读可见性检查、
VM 交叉编译，以及说明范围后的独立 Android 运行库加载/节点访问验证。
2026-11-08 追加官方 MobileNet V1 小模型单次推理验证，见文末。
未安装 APK 或修改 MaaMeow/Core，未进行 OCR 推理。

## 现场结果

| 检查 | 结果 |
| --- | --- |
| Android | reDroid 13 / API 33，arm64-v8a；内核 6.18.18.c1145-trim |
| 检查身份 | ADB shell，uid 2000；SELinux 显示 Disabled |
| 板卡识别 | device-tree：Aibidu BDY-G98，rockchip,rk3588 |
| NPU 节点 | /dev/dri/renderD128，字符设备 226:128；/dev/dri/card1 为 226:1 |
| 节点映射 | sysfs 显示二者属于 fdab0000.npu，driver 为 RKNPU，compatible 为 rockchip,rk3588-rknpu |
| 驱动版本 | /sys/module/rknpu/version 为 0.9.8 |
| 文件权限 | 上述节点 mode 0777，ADB shell 的 test -r/-w 均通过 |
| 其他 DRM 节点 | card0 对应 rockchip-drm 显示驱动；不能按 renderD 编号猜测硬件 |
| Runtime 搜索 | 指定系统库目录三层内未发现名称含 rknn/rknpu 的文件或符号链接 |
| Runtime 服务文件 | /vendor/bin、/system/bin、/odm/bin 的 rknn_server 未发现 |

搜索范围为 /vendor/lib64、/system/lib64、/system_ext/lib64、/odm/lib64、
/product/lib64、/vendor/lib、/system/lib（不存在的目录跳过）。
未扫描应用私有数据、用户家目录、相册数据或完整文件系统。
只读检查没有访问 GPU/NPU 调度接口，没有执行推理或测量性能。

**结论：NPU 驱动和节点在 Android 内可见，具备继续验证的基础。**
文件权限检查不能证明实际 open、cgroup 设备访问或 ioctl 可用。
系统目录未发现运行库，不代表所有应用私有位置都不存在运行库。
尚未确认运行库/驱动/模型兼容性，也未确认 MaaMeow App 或提权服务的实际访问能力。

## 复查

在开发 VM、现有 ADB 已连接的情况下：

~~~bash
cd ~/maa-rk3588
bash scripts/rk3588/check-rknn-access.sh
~~~

脚本只读 Android 的固定节点、sysfs 和系统库目录，不执行 adb root、su、
安装、写设备、修改权限或重启。缺失路径是现场结果，不能因脚本退出 0 判断 NPU 可用。
原始报告位于忽略目录 .cache/rk3588/rknn-access-01.txt、rknn-access-02.txt。

## 已准备的独立探针

源码：scripts/rk3588/probe-rknn-access.c。
构建入口：scripts/rk3588/build-rknn-probe.sh。
运行库固定信息：scripts/rk3588/rknn-runtime.lock.json。

采用 [Rockchip 官方 Android Runtime](https://github.com/airockchip/rknn-toolkit2/tree/42aa1d426c0a9e0869b6374edba009f7208a1926/rknpu2/runtime/Android/librknn_api/arm64-v8a)
v2.3.2，提交 42aa1d426c0a9e0869b6374edba009f7208a1926。
下载使用个人代理，文件大小与官方 Git blob SHA-1 核对通过，再固定 SHA-256。
选取固定参考版本不代表已确认它与当前驱动兼容；不升级宿主驱动或相册运行库。
RKNN 的 [官方架构说明](https://github.com/airockchip/rknn-toolkit2/blob/42aa1d426c0a9e0869b6374edba009f7208a1926/README.md)
区分模型转换工具、设备 Runtime 和内核驱动，三者需分别验证。

在开发 VM 重建：

~~~bash
source "$HOME/.config/maa-dev/proxy.sh"
bash scripts/rk3588/build-rknn-probe.sh
~~~

构建脚本只生成本地产物，不包含 adb push/install/shell 部署操作。
使用固定 NDK 29.0.14206865，Android API 28 ARM64，开启 -Wall/-Wextra/-Werror。
交叉编译和 ELF 静态检查通过：探针为 AArch64 PIE，解释器 /system/bin/linker64，
只直接依赖 libdl.so、libc.so；Runtime 为 AArch64，直接依赖 liblog.so、
libm.so、libdl.so、libc.so。静态检查不代替目标系统加载验证。

VM 产物均在 .cache/rk3588/rknn-probe/，不提交二进制：

| 文件 | SHA-256 |
| --- | --- |
| rknn-access | 36c85de3bf8443a4cd25b1512fa8c92b5684dd0c13ddadb7f80e51559aea8b1d |
| librknnrt.so | 07a8398be5caed21a1998ffa313e0425bb9e76b154a8dbbc667d71722c009351 |

本地日志为 build.log、probe-elf.txt、runtime-elf.txt。

## 本次设备验证范围与结果

执行前已说明范围。在 Android 的 /data/local/tmp 下新建唯一的
maa-rknn-probe.* 临时目录，仅放入 rknn-access（9,352 bytes）和
librknnrt.so（8,387,408 bytes）。设备文件 SHA-256 与 VM 产物核对通过，
使用原 ADB shell 身份执行一次：

~~~text
<临时目录>/rknn-access <临时目录>/librknnrt.so /dev/dri/renderD128
~~~

探针读取身份及驱动版本，以 RTLD_NOW|RTLD_LOCAL 加载库，检查 rknn_init、
rknn_query、rknn_run、rknn_destroy 导出符号，再以 O_RDWR 打开并关闭 NPU 节点，
最后卸载运行库。dlopen 会执行第三方库的初始化代码；探针本身不调用任何
RKNN 函数、不发 ioctl、不加载模型或提交推理。记录输出后仅清理本次创建的临时目录。
不安装 APK，不写 /system 或 /vendor，不设置全局 LD_LIBRARY_PATH，不改变权限或生产服务。

返回 0 且出现 ACCESS_AND_LOADER_PASS，表示本次 ADB shell 环境下运行库加载、
导出符号及节点 open/close 通过。返回 10 为加载失败，11 为符号缺失，
12 为节点打开失败，13 为清理资源失败；参数错误为 2。
即使通过，仍不证明驱动 ioctl、模型运行或 App 进程可用。
实际结果为退出码 0，出现 ACCESS_AND_LOADER_PASS；runtime_load、
四个导出符号及 device_open 全部通过，uid/gid 为 2000，驱动版本 0.9.8。
探针已正常 close/dlclose，上传文件和本次创建的临时目录均已清理并核验不存在。
首次非交互传输尝试因 ADB 读取脚本标准输入仅创建了空目录，未部署/执行；
修正为 adb shell -n 后清理该空目录并完成上述唯一一次探针执行。
原始输出、退出码和设备文件校验值位于忽略目录中的 device-run-01.log、
device-run-01.exit、device-checksums.txt。

## 官方小模型推理验证

2026-11-08。用户确认范围后，以同版本官方示例模型执行了一次真实 NPU 推理。

### 固定输入

锁文件：scripts/rk3588/rknn-mobilenet.lock.json，来源均为 rknn-toolkit2 v2.3.2
同一提交，文件大小、Git blob SHA-1 与 SHA-256 均核对通过：

| 文件 | 上游路径 | SHA-256 |
| --- | --- | --- |
| mobilenet_v1.rknn | rknpu2/examples/rknn_mobilenet_demo/model/RK3588/ | 381dae3b7038a98b10f6ec9dcdbb094a49247341856fb294e692ef518218fcfb |
| dog_224x224.jpg | rknpu2/examples/rknn_mobilenet_demo/model/ | c350299c6283d5f62fecf1f845b6b3be9aafec8dff528ca09a129990f0a584b0 |
| cat_224x224.jpg | 同上 | 0ce3ef6da3424c0744385935a210e4ec8ab3a6ddda205113ab07b77f9c0e812a |
| labels.txt | rknn-toolkit2/examples/tflite/mobilenet_v1/ | 1e3ce2539ea02a81eac8ddc06bb7bfbbc7dfbd1e4e092672284410e8fd73ae57 |
| rknn_api.h | rknpu2/runtime/Android/librknn_api/include/ | c48e11a6f41b451a5fd1e4ad774ea60252d3d94f78bee9b21ea3d21b21deba9a |

官方 demo 依赖 OpenCV 解码 JPEG。为不在设备上引入 OpenCV，VM 使用固定的
Pillow 12.3.0 wheel（SHA-256 锁定，仅解压到忽略目录，不安装到系统 Python）
把图片解码为 NHWC uint8 RGB 原始数据，等价于官方 imread + BGR2RGB：

| 输入 | 字节 | SHA-256 |
| --- | --- | --- |
| dog_224x224.rgb | 150,528 | 8bbcfdab3195f67df06b2eae745b4bfe1042e5881b53cd0c3da455b7bee2e88f |
| cat_224x224.rgb | 150,528 | 04ef5309abc2da4addef7b4a5e7befdcf96ee03ce4d79d3e062d9f904e02c158 |

两种解码器的实现可能不同，像素值不保证与 OpenCV 逐位一致。

### 探针与复现

源码 scripts/rk3588/probe-rknn-infer.c；仍用 dlopen 加载指定路径的运行库，
不链接、不设置 LD_LIBRARY_PATH。流程：rknn_init（flag 0，默认核心调度）、
查询 SDK/驱动版本及输入输出属性并校验形状，每张图先推理 1 次（第一张为初始化后冷启动），
再连续 20 次热推理，统计 inputs_set/run/outputs_get 墙钟耗时，并核对热推理输出与首次一致；
最后 rknn_destroy、dlclose。Top-5 由 VM 侧 check-rknn-infer-log.py 按锁文件预期判定。

~~~bash
source "$HOME/.config/maa-dev/proxy.sh"
bash scripts/rk3588/build-rknn-probe.sh        # VM 本地：下载、解码、交叉编译
bash scripts/rk3588/run-rknn-infer-once.sh --confirmed <REDROID_ADB_SERIAL>   # 设备端，需先获用户同意
~~~

rknn-infer 为 AArch64 PIE（15,880 bytes，SHA-256
b64532bb463b81369fbccd582137626834a44c3662d40ab38324de9a661748f1），
只直接依赖 libdl.so、libm.so、libc.so；rknn-access 重建后的哈希与之前一致。

设备脚本在 /data/local/tmp 新建唯一 maa-rknn-infer.* 目录，只推送探针、
Runtime、模型和两份 RGB 输入（约 12.8 MiB），核对 SHA-256 后以 ADB shell 身份执行一次
（60 秒超时），退出时只删除这些文件和该目录并核验。不加 --confirmed 不会连接设备。
退出码：20 读文件失败，21 rknn_init 失败，22 查询失败或形状异常，23 输入/运行失败，
24 输出失败，25 热推理 Top-1 变化，13 清理失败；10/11/2 同访问探针。

### 结果

退出码 0，出现 INFERENCE_FLOW_PASS 和 MOBILENET_TOP1_CHECK=PASS；
设备文件校验通过，临时目录 maa-rknn-infer.CJsI7y 已删除并核验不存在。

- 身份 uid/gid 2000；Runtime 报告 api_version 2.3.2 (429f97ae6b@2025-04-09)，drv_version 0.9.8。
- 输入 1×224×224×3 NHWC，INT8 非对称量化（scale 0.007812，zp 0）；
  输出 1×1001 FP16，经 want_float 转为 float。

| 输入 | Top-5（类别 分数） | 判定 |
| --- | --- | --- |
| dog | 156 Shih-Tzu 0.884766；155 0.054016；205 0.003677；284 0.002974；285 0.000189 | Top-1 与官方一致；Top-5 序列与官方 README 相同 |
| cat | 283 tiger cat 0.407227；282 tabby 0.172485；286 Egyptian cat 0.155762；278 0.059113；279 0.042603 | Top-1 属家猫类 282–286（非官方预期，仅合理性检查） |

官方 README 的 dog Top-1 分数 0.928223 来自 rk3566 构建；本次 RK3588 模型为 0.884766，
差异 −0.043，属于平台/量化差异的说明范围。20 次热推理与首次输出最大绝对差均为 0，Top-1 无变化。

单次进程内墙钟耗时（ms，未做核心绑定与频率锁定，仅为本次参考）：

| 阶段 | 数值 |
| --- | --- |
| dlopen Runtime | 2.342 |
| rknn_init（4.69 MB 模型） | 11.372 |
| 冷启动首推 dog：inputs_set / run / outputs_get / 合计 | 0.376 / 5.063 / 0.038 / 5.476 |
| dog 热推理 20 次 run：min / median / max | 5.010 / 5.230 / 5.436 |
| dog 热推理合计 median | 5.346 |
| cat 热推理 20 次 run：min / median / max | 5.187 / 5.448 / 5.585 |
| cat 热推理合计 median | 5.604 |
| rknn_destroy | 9.878 |

**结论：在 reDroid ADB shell 环境中，官方 Runtime 2.3.2 与宿主 RKNPU 0.9.8 驱动能完成
模型初始化、提交推理并返回正确分类结果。**

限制：
- 只证明 ADB shell（uid 2000）可用，不代表 MaaMeow App 进程或提权服务可访问。
- 耗时为单次运行墙钟时间，未记录 NPU 负载/频率，也未排除宿主其他 NPU 任务；
  不是性能验收，也不能外推到 OCR 模型。未做同模型 CPU 对照。
- MobileNet 结果不代表 OCR 模型可以转换，或 OCR 算子都能在 NPU 上执行。

原始记录在忽略目录 .cache/rk3588/rknn-infer/，文件名为 device-run-01.*
（log、exit、check、checksums、cleanup、df、temp-path），另有 inputs.json 和 build.log。

### 下一步

准备固定图片的 OCR 探针：先只读确认 MaaCore 使用的 ncnn OCR 模型（检测/识别）结构
与输入规格，评估能否转换为 RKNN（需要 x86_64 的 RKNN-Toolkit2 及 ONNX 源模型），
然后比较 CPU/RKNN 文本结果和预处理、推理、后处理耗时。
开始前列明模型来源、转换工具、代码和设备执行范围，保持 CPU 基线与正式应用可用。
