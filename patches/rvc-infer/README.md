# RVC 推理侧补丁（vendored runtime 的三个文件）

`runtime/` 整目录不入库（`.gitignore:2`），本机对 `runtime/rvc/infer/` 的三处功能扩展
只存在于磁盘上。这里存的是打好补丁的成品文件，runtime 重装 / 换机器后照本目录恢复，
否则网关的 fcpe 与音高平滑会被能力探测降级（不崩，但功能消失）。

| 文件 | 改动 | 对应评审项 |
|---|---|---|
| `cli.py` | `--f0-method` choices 增加 `fcpe`；新增 `--filter-radius`（默认 3）并传给 `vc_single` | C1 / C2 |
| `vc/pipeline.py` | ① `get_f0` 新增 `filter_radius` 参数：提取后、清音插值前做 `signal.medfilt`（奇数核兜底 `radius \| 1`）；fcpe 分支本来就随 vendored 版存在；② **长音频分段处加交叉淡化**（见下） | C2 / H1 / 音质 |

### ② 段间交叉淡化（本仓库自查项，上游没有）

上游 `pipeline()` 对超过 `x_max` 秒的音频先算 `opt_ts` 切点、逐段推理，最后
`np.concatenate(audio_opt)` **硬拼**。切点虽然选在查询窗内能量最低处（多半是换气/弱音），
拼接点依然存在音量与音色的阶跃——听感就是"偶尔一声怪响 / 金属声"，每条边界都可能来一次
（3.5 分钟的歌在 6GB 卡上会被切成 3~4 段）。

补丁做法：给非末段多喂 `xf_src` 个输入样本（输出侧多出等长的 `xf_tgt` = 50ms），
拼接时在重叠区做线性交叉淡化；多出来的长度正好被淡化吸收，**总长度保持不变**
（末尾还有一次 pad/truncate 兜底），所以人声不会相对伴奏产生累积漂移。单段音频行为完全不变。
| `vc/modules.py` | `vc_single` / `vc_multi` 透传 `filter_radius` | C2 |

## 恢复步骤

1. 确认 runtime 是上游原版：`py312/python.exe runtime/rvc/infer/cli.py --help` 里
   没有 `--filter-radius` 即为未打补丁。
2. 用本目录文件**同名覆盖** `runtime/rvc/infer/` 下的对应文件：
   ```
   cp patches/rvc-infer/cli.py runtime/rvc/infer/cli.py
   cp patches/rvc-infer/vc/pipeline.py runtime/rvc/infer/vc/pipeline.py
   cp patches/rvc-infer/vc/modules.py runtime/rvc/infer/vc/modules.py
   ```
   注意：覆盖前先看 runtime 版本是否与本补丁同源（官方 RVC-Project main 分支 2024 之后的
   infer 结构）。若上游改过 `get_f0` 签名，不要整文件覆盖，按上表手工重放那几处改动。
3. fcpe 依赖：`py312/python.exe -m pip install torchfcpe`（权重随 wheel 分发，无需另下）。
4. 验证：`--help` 出现 `--filter-radius` 与 `{pm,rmvpe,fcpe}` 即恢复；
   网关侧无需改动（启动后首次提交换声会重新探测能力并缓存）。

未打补丁也能安全运行：网关探测到 CLI 不认识 `--filter-radius` 时不传该参数
（任务上留 `caps_warn`），选 fcpe 则提交时直接 400，不会让整条换声崩在队列里。
