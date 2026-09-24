# THIRD-PARTY REVIEW — 蓝军报告（基线 e32bbb5）逐条复核

审计人：第三方子代理（独立审计，不信任蓝军报告与文档，只认代码）。

## 一、蓝军结论逐条验证

**A-1 · 训练中试听必失败 → 成立**
- 保存结构确认：`runtime/rvc/train/utils.py:166-174` torch.save `{"model", "iteration", "optimizer", "learning_rate"}`，无 `weight/config/sr/f0/version`。
- 推理侧确认：`infer/cli.py:96-99` `weight.get("emb_g.weight")` 为 None 即 `raise ValueError`；即使绕过，`infer/vc/modules.py:174-176` 还要 `self.cpt["config"][-1]` → KeyError。
- 触发链确认：`app.py:2340-2341` 选 `ckpts[-1]`（`logs/<name>/G_*.pth`）直接传给 cli.py → 子进程必抛。
- 反例尝试（成品优先分支）：训练中成品尚未导出（导出在 worker 步骤 5.5），`_rvc_latest_export` 返回 None → 无逃逸路径。**P1 成立**。
- 佐证（蓝军未提）：`train.py:591-621` 证实 `-sw 0` 落盘名是 `G_{global_step}.pth`，训练中途周期性保存，"训练中点试听"场景真实存在。

**A-2 · merge 未设 cwd，融合大概率失败 → 成立**
- `process_ckpt.py:290` `torch.save(opt, "assets/weights/%s.pth" % name)` 相对路径；`app.py:1902-1903` 无 `cwd` 参数（对比 preview/训练都传了）。
- 前端在 assets 下无 `weights/` 目录 → `torch.save` 抛 FileNotFoundError → 被裸 except 吞掉，exit 0。`out.is_file()` 恒 False → 恒 500。**成立**。

**A-3 · f0 传 int 恒为 0 → 成立（且比蓝军说的更实）**
- `app.py:1888` 传 int；`process_ckpt.py:285` `opt["f0"] = 1 if f0 == i18n("是") else 0`。`1 == "是"`（int vs str）恒 False，与 locale 无关——蓝军"未验证项 3"其实可静态闭环。**产物 f0 恒 0 成立**。
- 后果链确认：modules.py:177-189 走 nono 类，`load_state_dict(..., strict=False)` 静默丢 f0 权重键。

**A-4 · alpha 解析无兜底 → 成立**（alpha=null → TypeError → 500；alpha="abc" → ValueError → 500。前端已校验但 API 直调可触 500。P2 合理）

**B-1 · python -c `%` 拼接可注入 → 成立，但缓解论述需修正**
- 拼接点确认（merge/check/_meta），`a`/`b` 仅 basename，单引号不滤。**注入缺陷成立**。
- **但蓝军的缓解论述错误**：app.py:100-109 CORS 只放行 `127.0.0.1/localhost:7863、3081` 四个 origin，"任意网页可经 CORS 打接口"不成立；merge 是 `Content-Type: application/json` 的 POST，会触发预检并被拒。实际攻击面是本机进程/本地用户。降 P2 更妥，但注入缺陷本身成立。

**B-2 · torch.load pickle → 成立**，py312 site-packages 无 torch，无法静态确认 ≥2.6，蓝军标记未验证正确。

**C-1 · 预览锁 TOCTOU → 成立**（locked()+with 非原子；global 冗余）。
**C-2 · resume 并发竞态 → 成立**（检查+置位不在同一锁窗口）。
**C-3/C-4 → 成立**（job.json 非原子写；读半截静默 `{}`）。

**D 契约核对**：preview/merge 返回结构与前端一致 ✔。

**D-1 · active 回显旧任务轰炸 → 成立**（刷新页面旧 done/error 逐个弹 toast、横幅被覆盖）。

**F-1 · resume 可重跑 done 任务 → 成立**（重名 409 检查被绕过，extract_small_model 直接覆盖成品）。
**F-2/F-3/F-4/F-5/F-6 → 成立**（F-4：worker 写入字段确认无 preview_url；F-6：check 两次独立 torch.load）。

## 二、蓝军漏掉的新缺陷（审计新增）

1. **N-1 · P1 · 续跑期间试听静默播放旧成品**——resume 重跑时旧成品已在 weights，preview 的 mtime 比较（app.py:2337-2338）会让"训练中试听"静默播放旧成品——用户以为听到新训练进展，实为旧模型（且不报错）。蓝军完全未提。
2. **N-2 · P2 · `_rvc_latest_export` 的 glob `{name}*.pth` 过宽**（app.py:2502）：名为 `voc` 的任务会匹配到无关成品 `vocal_x.pth`（提交重名检查同款模式，双向放大误伤/误选）。
3. **N-3 · P2 · merge 脚本里 `'融合 %s x %s'` 用 `a`/`b` 文件名拼接**——含单引号文件名同样注入（与 B-1 同源，蓝军只列了路径参数）。另 alpha 为 bool 时 `float(True)=1.0` 静默通过。
4. **N-4 · P2 · check 无并发锁**：多次连点最多同时 2N 个 800MB 加载进程（蓝军 F-6 只提资源浪费未提并发叠加）。
5. **N-5 · P3 · 训练超时公式与自述实测矛盾**：`epochs*240+3600`（app.py:2489）vs 注释自述"实测一轮可达 1055~1311s"（app.py:2437）——大素材+慢机下会误杀健康训练。蓝军 E 维度漏了。
6. **N-6 · P3 · preview 文案误导**：`G_2333333.pth` 的 stem split 显示的是 step 非轮次，"第 N 轮检查点"文案不准。

## 三、覆盖度稽核

| 维度 | 蓝军处理 | 评价 |
|---|---|---|
| A 正确性 | 4 条，均有代码链 | 好，A-3 机制可静态闭环却留未验证 |
| B 安全 | B-1/B-2 | B-1 的 CORS 缓解论述**错误**（CORS 白名单 app.py:102-105） |
| C 可靠性 | 锁/竞态较全 | 漏 N-5 训练 timeout 误杀 |
| D 契约 | 全对 | 漏 N-1 试听语义污染 |
| E 运行中任务兼容 | 覆盖 GPU/内存 | 偏浅，未审 timeout 公式、logs 清理与 preview 交互（N-1） |
| F 增量 | 6 条 | 漏 N-2 glob 过宽、N-6 文案 |
| 跳过项 | `rvc/audio/{rid}` part 白名单（第三方抽查无恙 ✔）、`_stream_upload_to` 大小限制、client.js 其他 XSS 面（escapeHtml 已确认使用 ✔） | |

## 四、结论

- **蓝军 P1 三条（A-1/A-2/A-3）全部独立复现成立**，B-1 注入成立但严重度应降 P2（CORS 白名单挡住跨站路径）。
- 蓝军未验证项 3（i18n "是"）可静态闭环：`1 == "是"` 恒 False，与 locale 无关。
- 新增缺陷 6 条，其中 **N-1（P1：续跑期间试听静默播放旧成品）** 和 **N-5（timeout 公式与实测矛盾，会误杀 8 小时级训练）** 值得优先修。
- 修复建议优先级：A-2（加 `cwd=str(RVC_DIR)`，一行）→ A-1（preview 前先 extract_small_model 到临时 pth）→ A-3（f0 参数适配官方 merge 的 i18n 判断）→ B-1（sys.argv 传参杜绝拼接）。
