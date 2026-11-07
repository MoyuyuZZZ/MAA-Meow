# Android RKNN 预检

2026-11-07。开发 VM：<user>@<dev-vm>，~/maa-rk3588，dev。
用户已验收环境及 CPU 基线，并同意进入下一步。已完成只读可见性检查、
VM 交叉编译，以及说明范围后的独立 Android 运行库加载/节点访问验证。
未加载模型或执行推理，未安装 APK 或修改 MaaMeow/Core。

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

后续先用同版本官方小模型验证一次真实推理；通过后再准备固定图片的 OCR 探针，
比较 CPU/RKNN 输出与预处理、推理、后处理耗时。
开始前列明模型、代码和设备执行范围，保持 CPU 基线与正式应用可用。
