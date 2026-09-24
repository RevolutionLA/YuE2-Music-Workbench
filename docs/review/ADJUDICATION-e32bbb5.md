# ADJUDICATION-e32bbb5 — 中立裁定报告

裁定人：中立裁定员（全新角色，不采信任何一方自述，只认代码证据；已逐条到代码复核）。
基线：e32bbb5 工作区改动。裁定前已实读关键文件：`app.py`、`runtime/rvc/train/process_ckpt.py`、`runtime/rvc/train/utils.py`、`runtime/rvc/infer/cli.py`、`runtime/rvc/i18n/i18n.py`、locale JSON、两份启动 bat、`static/index.html`。

## 一、裁定表

| 编号 | 结论 | 裁定 | 关键证据（行号） | 整改建议一句话 |
|---|---|---|---|---|
| A-1 | G_*.pth 结构 `{"model",...}` 无 `weight/config`，cli.py 必抛 ValueError | **采纳 P1** | utils.py:166-174（保存仅4键）；cli.py:95-99（`weight.get("emb_g.weight")` None 即 raise）；app.py:2340-2341（直接选 G_*.pth） | preview 前先对 G_*.pth 跑 `extract_small_model` 产出临时成品 pth 再推理 |
| A-2 | merge 子进程无 cwd，产物写错位置，恒 500 | **采纳 P1（且从"大概率"升为确证）** | process_ckpt.py:290（相对路径 `"assets/weights/%s.pth"`）；app.py:1902-1903（无 `cwd`）；scripts/启动音乐工作台.bat:4 `cd /d %~dp0..`→仓库根，bat:53 WMI 工作目录=`%cd%`=仓库根；`<repo>/assets/weights/` **实测不存在** | `subprocess.run(..., cwd=str(RVC_DIR))` 一行修复 |
| A-3 | f0 传 int，官方 merge 判 `f0 == i18n("是")` 恒 False | **采纳 P1** | app.py:1888（int）；process_ckpt.py:285（唯一赋值点）；i18n.py:22-23 + locale JSON——`1 == "是"`/`1 == "Yes"` 均类型不等恒 False，与 locale 无关 | 子进程内用 `i18n('是')` 计算 f0 再传给 merge |
| A-4 | alpha 解析无兜底 | 采纳 P2 | app.py:1863 | try/except 转 400 |
| B-1 | python -c `%` 裸拼可注入 | **采纳缺陷、改级 P2** | 拼接点 app.py:1873-1876/1896-1900；CORS 白名单仅 4 个本机 origin（app.py:100-109）；merge POST application/json 触发预检被拒 | 脚本参数改 `sys.argv` 传参；N-3 的 info 拼接一并治理 |
| B-2 | torch.load pickle | 采纳 P3 | 本地单用户；torch 版本未实测 | 升 torch≥2.6 或评估 weights_only |
| C-1 | preview 锁 locked()+with TOCTOU | 采纳 P2 | app.py:2353-2355 | `acquire(blocking=False)` |
| C-2 | resume 并发检查竞态 | 采纳 P2 | app.py:2779-2790 | 检查+置 pending 放进同一 RVC_TRAIN_LOCK 原子段 |
| C-3 | 读半截 checkpoint 自愈竞态 | 采纳改级 P3 | 概率低自愈 | 接受 |
| C-4 | job.json 非原子写 | 采纳 P3 | app.py:2465-2470 | 写临时文件后 `os.replace` |
| D-1 | active 回显旧 done/error 任务 toast 轰炸 | 采纳 P2 | index.html:2782-2786 | 前端仅对 running/pending 启动轮询 |
| D-2 | alpha 校验重复/空格 split 不一致 | 采纳 P3 | 无实害 | 可推迟 |
| F-1 | resume 可重跑 done 任务，绕过重名检查覆盖成品 | 采纳 P2 | app.py:2774 仅拦 running/pending | resume 对 done 任务要求显式 confirm |
| F-2 | dataset_clean 目录残留决定分离意图 | 采纳 P3 | 挂靠 F-1 | 随 F-1 一并观察 |
| F-3 | done/error 24h mtime 卡 | 驳回（无缺陷） | 符合预期 | 不修 |
| F-4 | 刷新后 preview_url 丢失 | 采纳 P2 | job.json 无该字段 | status 响应附带 preview 存在性 |
| F-5 | client.js | 驳回（无缺陷） | 两方一致 | 不修 |
| F-6 | check 双次 torch.load | 采纳 P2（与 N-4 合并） | 两段脚本各 load 一次 | 单脚本一次 load 输出全部指标 |
| E-线程 | preview 未限 torch 线程抢核 | 采纳 P3 | 仅设 OPENBLAS=1 | 加 OMP_NUM_THREADS |
| N-1 | resume 期间试听静默播旧成品 | **改级 P3（部分驳回）** | resume 续训落盘的新 G_*.pth mtime 必然晚于旧成品 → 走 ckpt 分支；残留窗口仅"续跑启动后、首个新检查点落盘前" | 该窗口 tag 改"上次成品（新检查点尚未产出）" |
| N-2 | `_rvc_latest_export` glob `{name}*.pth` 过宽 | 采纳 P2 | app.py:2502 | glob 改精确匹配 |
| N-3 | 融合 info 拼接 a/b 文件名注入 | 采纳 P2（B-1 同源） | app.py:1899 | 随 B-1 改 argv 传参 |
| N-4 | check 无并发锁 | 采纳改级 P3 | 本地单用户 | 与 F-6 合并修：轻量锁+单次 load |
| N-5 | timeout 公式误杀 8h 训练 | **部分驳回 P3** | 实算 200×240+3600=51,600s≈14.3h > 自述 8h 案例；1055~1311s 是被剔除的异常轮 | 240 常数按实机校准即可，不必改架构 |
| N-6 | "第 N 轮检查点"实为 step | 采纳 P3 | app.py:2341 | 文案改"检查点 #N（step）" |

## 二、共同前提审查（双方共用的未验证假设）

1. **A-2 的"启动 cwd"假设——双方都未实测，裁定员代为实测闭环。** 启动 bat 链条：根 bat 转调 → `scripts/启动音乐工作台.bat:4` `cd /d %~dp0..`（仓库根）→ bat:53 WMI 网关工作目录=仓库根 → merge 子进程继承 cwd=仓库根；且 `<repo>/assets/weights/` 目录不存在 → torch.save 抛 FileNotFoundError → 裸 except 吞掉、exit 0 → `out.is_file()` False → **恒 500，确证成立**。
2. **A-3 的 i18n 值假设——静态可闭环。** zh_CN 返回"是"，en_US 返回"Yes"；传入 int 1 时两种 locale 都恒 False——结论成立，推理收紧为"类型不匹配，与 locale 无关"。
3. **N-1 的 mtime 假设——第三方未推演完整。** 续训新落盘检查点 mtime 必然晚于旧成品，`>=` 比较正确切回检查点分支；"静默播旧成品"只在续跑初期成立，改 P3 文案问题。
4. **N-5 未算账。** 实算 14.3h 覆盖 8h 案例；被引 1055~1311s 是异常轮。误杀仅发生在持续 >240s/轮的机器上——实测校准常数即可。
5. **仍留实测项**：py312 torch 版本（B-2）、G_*.pth 试听实际报错路径（A-1 静态链完整）、preview CPU 实测耗时。

## 三、最终整改优先级清单

- **P0**（功能不可用，均已代码确证）
  1. A-2：merge/check/_meta 子进程加 `cwd=str(RVC_DIR)`。
  2. A-1：preview 对 G_*.pth 先 `extract_small_model` 到临时成品 pth 再推理。
  3. A-3：f0 传参适配官方 merge 的 i18n 字符串判断。
- **P1**：无（B-1 的 CSRF→RCE 链不成立，降 P2）。
- **P2**：B-1+N-3（argv 传参）、C-1（blocking=False）、C-2（原子化）、F-1（done 重跑需 confirm）、D-1（只轮询 running/pending）、F-4（preview_url 恢复）、F-6+N-4（check 单次 load+锁）、A-4（alpha 兜底）、N-2（glob 收紧）。
- **P3**（择机）：C-3、C-4、B-2、D-2、N-6 文案、preview 线程限制、N-1 标注、N-5 校准。
- **不修**：F-3、F-5。
