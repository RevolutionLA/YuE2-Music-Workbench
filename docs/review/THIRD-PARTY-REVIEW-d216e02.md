# 第三方复核报告 — 基线 d216e02 未提交改动

复核人：第三方审计员（只读子代理，未采信蓝军/开发文档，逐条独立核证；本文件由主代理代为落盘）。

## 一、对蓝军条目的 verdict 表

| 蓝军条目 | verdict | 证据 |
|---|---|---|
| A-1 GET analyze query 过长 414/静默失败 | 属实 | index.html:1634/1636、app.py:528；URI 编码膨胀 3-9 倍，超请求行上限成立，catch 静默 |
| A-2 Q: 正则不匹配 `Q:1/4=120` | 属实 | app.py:481；`1/4` 含 `/` 非纯数字，必不匹配 |
| A-3 in_music 不在 X: 处重置 | 属实 | app.py:500-514；多 tune/头部行污染成立 |
| A-4 embed 模式 dock 遮挡 iframe 底部 | 属实 | index.html:455-456、379-385；底部约 50px 永久遮挡 |
| B-1 批量页不走 styleWithGender/不读 bpm | 属实 | index.html:1818-1840 vs 944-956 |
| B-2 自动保存白名单缺 bpm | 属实（偏轻） | index.html:2806/2812 均无 bpm |
| B-3 play() 被自动播放策略拒后无提示 | 属实 | index.html:1331；轮询路径无 transient activation |
| C-1 _REPAIR_STATE 锁外写 | 属实（建议 P2） | app.py:361-402；单 worker 撕裂窗口窄 |
| C-2 旧乐谱 analysis=None 兜底 | 不成立（无缺陷） | 1674-1675、1712-1713 兜底完整 |
| D-2 取色器注入 | 不成立（无缺陷） | applyAccent 1030-1050 无注入路径 |
| E-1 Favicon busy 每帧 toDataURL | 属实 | index.html:1105/1120；小时级挂机前台高频序列化 |
| F-1 applyTheme 不重算 applyAccent | 属实且更严重 | client.js:256-261 跨 iframe 直接写 data-theme，嵌入场景（主使用路径）默认必现 |
| F-2 dock pointer | 不成立（无缺陷） | setPointerCapture 核过 |
| A-5 / B-4 引用顺序、localStorage | 不成立（无缺陷） | 核过 |

小结：13 条正面发现全部属实（无夸大；仅 C-1 建议降 P2）；6 条"未发现问题"复核确认。

## 二、新发现（蓝军/开发均漏掉）

- **N-1 · P2 · 修复路径"断点续传"承诺失效**：app.py:386-387 修复时把 `.part` 一并删除，而文案与 download_models.py:73-77 的续传机制都依赖 `.part`。损坏 4GB GGUF 时从零重下。建议只 unlink 主文件。
- **N-2 · P2 · 跨 iframe 主题切换强调色失联**：client.js:256-261 每 5s 直接写 iframe 的 data-theme，iframe 内 applyAccent 不会被触发。dsh 内嵌（主使用场景）深浅切换必现旧派生色。需 postMessage 通知或去内联化。
- **N-3 · P3 · client.js postMessage 无 origin 校验**：client.js:130-138。低危（仅 UI 高亮），建议校验 origin。
- **N-4 · P3 · verify 双通道轮询**：index.html:1207 修复期 5s 递归 + 2844 60s interval 并存。轻微重叠，无功能错误。
- **N-5 · P3 · StatusCard verify 每 5s 一次**：client.js:96-101 依赖 [s.alive, s.tick]（tick 每 5s +1），请求放大 12 倍；代价 stat 级可接受。另核实 MusicStudio 每 5s 重渲染不导致 iframe 重载/闪烁（同类型节点复用、ref 稳定）。
- **N-6 · P3 · Favicon busy 重入重置 t0**：当前无触发路径，备注级。
- **N-7 · P3 · bStart 双击竞态依赖后端 409 兜底**：核过，非缺陷。
- 其余自查项排除：播放按钮手势路径正常；$("bpm") 元素常驻 DOM 无 NPE；休止符 z/Z 不被误提取、和弦 [CEG] 计入音域语义可接受。

## 三、蓝军覆盖度稽核

1. dsh 插件侧（client.js）几乎未审（N-3/N-5/N-2 全漏）；
2. 前后端行为契约（repair 文案 vs .part 删除，N-1）漏掉；
3. 嵌入主场景系统性推演缺失（A-4/F-1 定级低估）；
4. 未覆盖：iframe 看门狗重载丢批量页字段（bLyrics/bStyle 不在持久化白名单，B-2 只查了 bpm）、上传流式路径、模板导入校验。

## 四、未验证项声明

- 无 shell 环境，未跑 git diff；行号基于当前工作区，可能有 ±漂移。
- 414 阈值、自动播放瞬态激活时长、React createElement 复用行为均为规范推断，未实测。

**汇总**：verdict 属实 13 / 不成立(无缺陷确认) 6 / 夸大 0；新发现 N-1~N-7（P2×2、P3×5）；覆盖缺口 4 项。
