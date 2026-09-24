# RESPONSE-e32bbb5 — 三方对抗评审整改报告

闭环：蓝军（docs/review/BLUE-TEAM-REVIEW-e32bbb5.md）→ 第三方（THIRD-PARTY-REVIEW-e32bbb5.md）→ 裁定（ADJUDICATION-e32bbb5.md）→ 整改（本文件）。

## 整改结果（按裁定优先级）

### P0（3/3 完成）

| 编号 | 整改 | 落点 |
|---|---|---|
| A-2 融合无 cwd 恒失败 | 连同更深的发现一起解决：官方 `merge()` 按检查点格式取权重（`ckpt["model"]`）、相对路径落盘、f0 走 i18n 字符串比较——三处均与成品 pth 不匹配，直接复用必然失败。改为**内联等价插值**：兼容成品/检查点两种来源、绝对路径落盘、f0 直接写 int、`cwd=RVC_DIR` | app.py `/rvc/models/merge` |
| A-1 试听用检查点必失败 | 试听前先跑官方 `extract_small_model` 把 G_*.pth 转成推理可用结构，产物缓存到任务目录（同一检查点只转换一次），全程 CPU | app.py `/rvc/train/preview` |
| A-3 f0 恒 0 | 随内联插值一并消除（`opt['f0'] = int(c1.get('f0', 1))`） | 同 A-2 |

### P2（9/9 完成）

- **B-1/N-3 注入**：check/merge/_meta 全部改为 `sys.argv` 传参，字符串拼接注入点清零（`grep r'%s'` 仅剩基线既有且已清洗的导出脚本一处）。
- **C-1 预览锁 TOCTOU**：改 `acquire(blocking=False)` + `try/finally` 释放，冗余 `global` 移除。
- **C-2 resume 竞态**：检查 + 置 pending 全部移入 `RVC_TRAIN_LOCK` 单一原子段。
- **F-1 done 重跑覆盖成品**：resume 要求 `confirm=yes`，前端加 `window.confirm` 二次确认。
- **D-1 旧任务 toast 轰炸**：`restoreRvcTrainActive` 只对 running/pending 启动轮询。
- **F-4 刷新后播放器消失**：`/rvc/train/status` 附带 `preview_url`。
- **F-6+N-4 体检双次加载/并发叠加**：合并为单脚本一次 load + `_RVC_CHECK_LOCK` 抢锁。
- **A-4 alpha 兜底**：try/except 转 400。
- **N-2 glob 过宽**：`_rvc_latest_export` 改精确匹配 `{name}.pth/.pt`。

### P3（3 项顺手修，其余记录在案）

- 已修：C-4（job.json 原子写 `os.replace`）、N-6（"第 N 轮"文案改"step 检查点"）、preview 转换加 OMP_NUM_THREADS=1。
- 记录在案不修（本地单用户/收益低）：C-3、B-2（已实测 torch 2.7.0 ≥2.6，weights_only 默认开启，风险自然消除）、D-2、N-5（实算 14.3h 覆盖 8h 案例，仅按实机校准常数）、N-1（窗口文案已由 N-6 覆盖）。

### 裁定驳回项

- F-3（24h mtime 卡）、F-5（client.js）：无缺陷，不修。

## 评审产出

- 蓝军报告：docs/review/BLUE-TEAM-REVIEW-e32bbb5.md（4 P1 + 12 P2/P3）
- 第三方复核：docs/review/THIRD-PARTY-REVIEW-e32bbb5.md（4 条全验证成立 + 新增 6 缺陷 + 覆盖度稽核；修正 B-1 的 CORS 论述）
- 中立裁定：docs/review/ADJUDICATION-e32bbb5.md（22 条逐条裁定；实测闭环启动 cwd 链、torch 版本、i18n 比较三个共同前提）

## 验证

- `py_compile` 通过；前端 `node --check` 通过。
- 注入点残留检查：`r'%s'` 仅剩基线代码 1 处（非本次 diff，name 已 re.sub 清洗）。
- 运行时验证待网关重启后进行（不重启不影响"蛋卷"任务；所有新端点当前未生效）。
