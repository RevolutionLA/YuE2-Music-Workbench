# 蓝军敌意评审报告 — 基线 d216e02 未提交改动

评审范围：`app.py`、`static/index.html`、`dsh-plugin/lib/client.js`。
（本报告由蓝军子代理产出，主代理代为落盘；蓝军为只读探索代理，结论附证据链。）

---

## A. 正确性

**A-1 · P2 · 正确性**
- 证据：`static/index.html:1634` — `fetch("/api/abc/analyze?abc=" + encodeURIComponent(abc.slice(0, 20000)))`，GET query；`app.py:528` 截断 200_000。
- 后果：前端截 20,000 字符后 URL 编码可膨胀至 ~60KB，超出 uvicorn 默认请求行上限（~8KB–16KB）。用户粘贴中长乐谱时分析静默失败（`catch {}` 吞掉，index.html:1636），摘要框永远空白，无任何提示。
- 建议：改 POST JSON body；或前端截断降至 ~4KB 并提示。

**A-2 · P2 · 正确性**
- 证据：`app.py:481` — `re.search(r"^Q:\s*(\d+)\s*=\s*(\d+)", abc, re.M)`。
- 后果：合法 ABC 常见 `Q:1/4=120` 或 `Q: "Allegro" 1/4=120` 均不匹配（`1/4` 非纯数字），BPM 恒为空；无错误但功能名不副实。
- 建议：正则放宽为 `^Q:\s*(?:[^=\n]*?)?(\d+)\s*/\s*(\d+)\s*=\s*(\d+)` 并取最后一组。

**A-3 · P2 · 正确性**
- 证据：`app.py:504` — `in_music` 置 True 后对**文件剩余所有行**做音高提取，包括 `P:`、`N:`、备注行、多 tune 文件（X:2）的信息头。
- 后果：多 tune 拼接文件会跨 tune 混算音域，摘要不准；以数字/`|`/`%` 开头且含音名的行会污染统计。不会崩。
- 建议：检测到下一个 `X:` 时重置 `in_music=False`；过滤 `P:`/`N:`。

**A-4 · P2 · 正确性（embed/iframe 场景）**
- 证据：`static/index.html:455-456` — `html.embed .side { display:none }` 但 `.dock` 在 embed 模式仍 `position:fixed; bottom:0`，iframe 高度 100%（client.js:293）。
- 后果：嵌入时 dock 永久遮挡 iframe 底部约 50px，遮挡生成结果区/状态条，属于实际 UI 回归。
- 建议：embed 模式下 `.dock` 改 `position:static` 或给主区加底部 padding。

**A-5 · 未发现问题（JS 语法/引用）**：核查了 `$` 定义顺序、`Favicon` 常量定义先于 `Favicon.set` 调用、`analyzeAbcDebounced`/`refreshModelVerify` 定义先于调用点。无 ReferenceError。

## B. 兼容性 / 回归

**B-1 · P1 · 回归**
- 证据：`static/index.html:1827-1833` — 批量页 `bStart` 手工拼接 `fullStyle`，**没有调用 `styleWithGender`，也没有读 `$("bpm")`**；创作页走 `styleWithGender` 会附加 BPM。
- 后果：同一套 BPM 覆盖语义在批量页失效——创作页设了 BPM，切批量页用相同 Style 生成节奏不同。两条 style 拼接路径分叉的行为不一致回归。
- 建议：批量页复用 `styleWithGender`（或加独立 `bBpm` 输入），至少提示 BPM 不生效。

**B-2 · P2 · 表单持久化白名单缺 bpm**
- 证据：`static/index.html:2806` — 自动保存白名单正则不含 `bpm`；保存本身用 `currentConfig()` 会带上 bpm，但"只改 BPM 不动其它字段"时依赖 `beforeunload` 兜底，iframe 被移除场景部分浏览器不触发 unload。
- 建议：2806 正则加入 `|bpm`。

**B-3 · P2 · 自动播放策略**
- 证据：`static/index.html:1331` — `p.play().catch(() => {})`；无用户手势路径（轮询恢复生成结果时调 `loadAudio`）会被浏览器自动播放策略拒绝，catch 静默。
- 后果：功能不崩（dockPlay 已 enable），但用户可能不知道音频已就绪未播放。
- 建议：play 失败时 toast 提示"已就绪，点击播放"。

**B-4 · 未发现问题（localStorage 键冲突）**：新键 `la_accent`、`la_theme`、`yue2_compose_form` 互不冲突；dsh-plugin/client.js 未使用 localStorage。

## C. 并发 / 状态

**C-1 · P1 · 竞态**
- 证据：`app.py:323,364,384,396` — `_REPAIR_STATE` 写侧部分在锁外（384 message 更新、396 finally 汇总），读侧两个端点均无锁。
- 后果：最坏读到撕裂的字段组合，前端可能闪一帧"模型缺件"错误状态后被 60s 轮询纠正；多 worker 部署下 `_REPAIR_STATE` 进程私有，running 标志失效，可并发启动两个修复线程同时 unlink + 下载同一文件。单进程单 worker 下非崩溃级。
- 建议：写侧全部收进锁内；文档注明仅支持单 worker。

**C-2 · P2 · 旧乐谱记录无 analysis 字段**
- 证据：`app.py:591-596` — 服务重启后从磁盘读旧记录，`rec.get("analysis")` 为 None；前端 1438 行 `renderAbcAnalysis(rj.analysis)` 判空安全，1474 行 `if (!rec.analysis) analyzeAbcDebounced()` 已兜底。
- 后果：已核对，兜底路径完整，**未发现实际缺陷**（防御已到位）。

## D. 安全

**D-1 · P2 · /api/abc/analyze 无限制调用**
- 证据：GET 端点、无鉴权、服务绑定 127.0.0.1。正则计算量随输入线性，200KB 上限内无 ReDoS（模式无嵌套量词）。
- 后果：本地服务风险低；但与其它 /api 端点同等暴露，属既有基线而非新增风险。
- 建议：与现有端点保持一致即可，不单独处理。

**D-2 · 未发现问题（取色器注入）**：`<input type="color">` 返回值恒为 `#rrggbb`，`applyAccent` 仅写入 `style.setProperty` 与 localStorage，无 innerHTML 注入路径。

## E. 资源

**E-1 · P1 · Favicon busy 动画每帧 toDataURL**
- 证据：`static/index.html:1057-1128` — busy 模式 `requestAnimationFrame` 循环，每帧 `cv.toDataURL("image/png")` 并赋给 `link.href`，触发浏览器解析 64×64 PNG dataURL。挂机长任务（生成 60-90 分钟）+ 标签页前台时持续高频执行；后台标签 rAF 会被节流，代价可控但前台仍浪费。
- 后果：前台标签页 CPU 占用无谓升高（实测级别的每帧序列化）。生成任务动辄小时级。
- 建议：busy 动画降频至 ~4fps（setInterval 250ms 或 rAF 内节流）。

**E-2 · P2 · refreshModelVerify 轮询放大**
- 证据：`static/index.html:2845` — `setInterval(refreshModelVerify, 60000)`，每次全量 stat 7 个文件（其中两个 2-4GB GGUF 仅 stat 不读内容，代价小）。
- 后果：可接受，未发现实际问题。

## F. UI 逻辑

**F-1 · P1 · 主题色在明暗切换后不重算派生色**
- 证据：`static/index.html:999-1006` — `applyTheme` 只写 `data-theme`，不调 `applyAccent`；`applyAccent` 读取切换时的 `dark` 标志派生色。用户先在浅色模式选了主题色（派生为 light 版本），切深色后 `--acc-soft`/`--acc-chip-*` 等仍是浅色派生值（CSS 里 dark 块的静态值被内联 style 覆盖）。
- 后果：深色模式下强调色的透明度/辉光偏浅色调，视觉不协调。
- 建议：`applyTheme` 内在设置 `data-theme` 后调用 `applyAccent(当前色)`；`applyAccent` 已读 `root.dataset.theme`，顺序对了即可。

**F-2 · 未发现问题（dock 轨道 pointer 事件）**：`setPointerCapture` 保证拖出轨道仍能收 pointerup；embed 模式下 dock 仍渲染（见 A-4 的遮挡问题，属布局而非事件缺陷）。

---

## 汇总

| 级别 | 数量 | 编号 |
|---|---|---|
| P0 | 0 | — |
| P1 | 4 | B-1、C-1、E-1、F-1 |
| P2 | 7 | A-1、A-2、A-3、A-4、B-2、B-3、D-1(可接受) |
| 未发现问题 | 6 | A-5、B-4、C-2、D-2、E-2、F-2 |
