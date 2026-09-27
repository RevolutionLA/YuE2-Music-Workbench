# 净化链守卫补丁（runtime/ 与 py312/ 两个不入库目录的文件）

`runtime/`（`.gitignore:2`）与 `py312/`（嵌入式 Python）整目录不入库。2026-09-27
「蛋卷」训练事故（legacy VR 净化模型在 CUDA 上吐 NaN→数字静音，一路骗到预处理才以
"没有可用样本"收口）落了两处**运行侧**修复，只存在于磁盘上。这里存的是打好补丁的
成品文件，runtime / py312 重装或换机器后照本目录恢复。

| 文件 | 落位 | 改动 | 性质 |
|---|---|---|---|
| `preprocess.py` | `runtime/rvc/train/preprocess.py` | `pipeline()` 里切片器一片没切出（整段静音/过短）时新增 `idx1 == 0` 守卫：打印"[数据切分][跳过] 切不出任何片段"并 `return False`，不再在 `norm_write` 上抛 `UnboundLocalError`（老行为把 13 条素材的真原因全遮成同一个栈） | 诊断性修复（不改正常路径行为） |
| `sitecustomize.py` | `py312/Lib/site-packages/sitecustomize.py` | 新增 `_install_cudnn_off()`：仅当环境变量 `PYMSS_DISABLE_CUDNN=1` 时 `torch.backends.cudnn.enabled = False`，绕开 VR 多 band 卷积 NaN（本机实测 GPU 峰值与 CPU 一致 0.2962，且 20s 素材 50.4s→11.3s 更快） | 事故主修复；**该文件另含既有的水印横幅过滤 `_install()`，覆盖时必须两份都在** |
| `repro_nan_trace.py` | 不落位（诊断工具） | 逐层前向报出 BaseNet 里第一个吐 NaN 的层（本机为 dec3），并打印最终产物峰值。用法见文件头 | 复现/复验工具，不用拷进 runtime |

app.py 侧的配套守卫（`_pymss_env_cudnn_off()` 只喂 legacy VR 子进程、选中声部静音判定、
整批静音换 CPU 重跑、落档复测 min_peak、resume 复用前复验峰值）随 git 库走，不在本目录。

## 恢复步骤

1. 确认现状：`py312/python.exe -c "import sitecustomize, inspect; print('_install_cudnn_off' in dir(sitecustomize))"`
   输出 `False` 即 py312 侧未打补丁；`runtime/rvc/train/preprocess.py` 第 109 行附近
   没有 `idx1 == 0` 即 runtime 侧未打补丁。
2. 同名覆盖（覆盖前确认 runtime 与上游 RVC-Project main 同源；若上游改过
   `pipeline()` 结构，不要整文件覆盖，按上表手工重放那一处守卫）：
   ```
   cp patches/rvc-clean-guards/preprocess.py runtime/rvc/train/preprocess.py
   cp patches/rvc-clean-guards/sitecustomize.py py312/Lib/site-packages/sitecustomize.py
   ```
3. 验证（用任意一条干声，或 `tmp` 里现造的静音 wav）：
   - cuDNN 开关：`PYMSS_DISABLE_CUDNN=1 py312/python.exe -m pymss.cli infer UVR-DeNoise -i <dir> -o <out> --device cuda`
     跑完产物峰值应 ≈ 0.29（数字静音=0.0）；去掉环境变量则分钟级复现 NaN→0（本机复现口径）。
   - 空切片守卫：对 30s 静音 wav 跑 `runtime/rvc/train/preprocess.py`（PYTHONPATH 指到
     `runtime/rvc`），日志出现"[数据切分][跳过] 切不出任何片段"，进程不抛 `UnboundLocalError`。

未打补丁也能跑：app.py 的静音守卫（落档复测 + 全失败报错）兜得住事故本体；
本目录两个文件恢复的是"诊断精度"与"GPU 直用性能"（不关 cuDNN 时 legacy VR 会整批
静音→自动降级 CPU 重跑，能出结果但慢）。
