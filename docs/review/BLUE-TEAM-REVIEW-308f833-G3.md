# 蓝军对抗性代码评审 · G3 组（前端 / 需求一致性 / 可访问性 / 生命周期 / 性能 / 前端错误处理 / 文档一致性）

- **基线**：commit `308f833` + 工作区未提交改动（README.md / README_EN.md / app.py / src/sheetsage_pt.py / static/index.html）。审查对象为磁盘现状。
- **审查组**：G3 前端/需求一致性/可访问性/生命周期。
- **选定维度**：1 需求一致性（含本次新增需求①②③④逐条验收）；18 可访问性与国际化；13 资源与生命周期（前端）；6 性能（前端）；4 错误处理（前端）；17 文档一致性（前端行为相关）。
- **主战场**：`static/index.html`（3032 行，内联 `<script>` 94,792 字符，`node --check` 语法通过）、`dsh-plugin/lib/client.js`、`dsh-plugin/src/*.mjs`。
- **实测手段**：Python 提取内联脚本 → `node --check`；`grep -c/-n` 统计；浏览器实测（`http://127.0.0.1:3081/?token=...`，同源 iframe 内只读操作：切页签、点 ID 徽标、开「⋯ 更多」菜单观察 5s 轮询重建、读 console、读 DOM/状态；另用页面自身 fetch 只读端点 `/lab-api/batch/status`、`/lab-api/history/active`）。全程未点击任何计算/改数据按钮，未修改仓库文件。
- **环境事实（影响多条判定）**：实测时正在运行的网关是**旧版 app.py**（`/api/batch/status` 返回体里没有 `queue`/`queued_hist` 字段），而 `static/index.html` 每次请求现读磁盘是**新版**——磁盘代码与运行时后端存在版本错位，需重启网关才对齐。

---

## 一、缺陷总表（按严重度排序）

| 编号 | 级别 | 一句话 | 维度 | 位置（文件:行号） |
|---|---|---|---|---|
| B1 | 🔴 | dsh 侧栏状态卡 `StatusCard` 在 rail 判定为真时 `return null` 跳过 2 个 Hook，React #300 崩溃，实测 console 已出现「slot entry crashed in 'sidebar.footer.action'」，页签+状态卡整槽挂掉 | 13/6 | dsh-plugin/lib/client.js:103-111（`if (rail) return null`）对比 127-133（`useState/useEffect` 在其后） |
| B2 | 🔴 | 换声轮询把 `rvcPolls` 声明成 `Set` 却调 `Map` 才有的 `.get/.set`——提交成功也弹「提交失败：rvcPolls.set is not a function」，完成/失败 toast 永不触发，定时器每 5s 翻倍泄漏（指数级） | 4/13/1 | static/index.html:2453、2522、2532、2554-2557 |
| B3 | 🔴 | 音色制作轮询 `rvcTrainPolls` 同款 Set/Map 混用：提交训练成功后误报「提交失败」，定时器 10s 翻倍泄漏，回前台补轮询的 `forEach((_t,rid)=>…)` 在 Set 上拿不到 key 形同虚设 | 4/13/1 | static/index.html:2729、2877、2886、2895-2897、2937 |
| B4 | 🔴 | 表单持久化依赖的 `currentConfig()/applyConfig()` 全文件**未定义**（boot IIFE 与 2984/2989 引用），页面加载即抛 ReferenceError 被 `catch {}` 吞掉——歌词/曲风「切页不丢」的承诺实际从未生效 | 1/4/17 | static/index.html:2977-3002（引用点 2984、2989；定义缺失） |
| B5 | 🟠 | 历史行「⋯ 更多」菜单/错误展开/选中文本被 5s 队列轮询整体重建 DOM 冲掉：实测打开菜单 6.5 秒后自动关闭（menuStillOpenAfter6s=false，99 行全量重建） | 6/13/1 | static/index.html:1982-1987（histQueuePoll）→ 2004 renderHistory → 2034 `el.innerHTML=""` |
| B6 | 🟠 | pollJob 轮询里 `catch {}`（1651）吞掉一切网络/JSON 错误且**不重启循环**：生成中一次网络抖动/网关重启 → 前台任务状态永久冻结在最后一次渲染，页面继续显示「生成中」而不再向服务端求证（谎报进行中） | 4/13/1 | static/index.html:1629-1653 |
| B7 | 🟠 | 「转谱中→回填历史乐谱→提交」链路里 `abcChords` 勾选框状态与提交值一致性缺口：勾选框不在表单持久化白名单（正则不含 `abcChords`，2993/2999 也不匹配），iframe 重载后回到默认不勾；而「回填历史带和弦谱」不改勾选框，用户以为勾选丢失=纯旋律、其实谱是带和弦的 full 谱（页面另有 `[cot]` 标注可对冲）| 1/18 | static/index.html:1820（提交读框）、1884-1895（回填不动框）、2993（持久化正则不含 abcChords） |
| B8 | 🟠 | 队列面板「这一批 2 首」需求④：代码正确（按 qid 分组+`queued_hist` 单列），**但运行时网关未重启**，`/api/batch/status` 无 `queue` 字段（实测）→ 当前磁盘版前端 `q && q.id` 不成立 → queueBar 整体空白，需求④在真实运行环境**未生效**；且用户此刻正在跑的这批（旧版提交、条目无 `qid`）重启后 cur 仍取 `state["qid"]` 兜底末条——实测旧 state 条目全部无 qid（items[-1].qid=undefined→`or _new_id()` 只保护新提交），旧批次会被归成 0 条的「这一批」 | 1/17 | static/index.html:1949-1966 + app.py:1822-1835（磁盘版）；实测：运行中 /lab-api/batch/status 无 queue/queued_hist 字段 |
| B9 | 🟠 | 需求③ ID 徽标：`navigator.clipboard.writeText` 在非安全上下文（http://局域网IP 访问 :7863/:3081）不存在 → 走 `execCommand("copy")`，该路径在 textarea `opacity:0` 且 `position:fixed` 下 Chrome 可复制、但**焦点被抢/无用户手势的调用（如脚本化点击）会被拒**——代码捕获不到拒绝（execCommand 返回 false 不抛错），`try` 判不住失败 → 弹「已复制」谎报成功 | 18/4 | static/index.html:968-987（fallbackCopy 不看 execCommand 返回值） |
| B10 | 🟠 | 转谱 20 分钟轮询循环 `while(...) await fetch(...)`（1834-1840）无 `!rr.ok` 分支、无 AbortController、异常即整体终止：中途一次 502/网络抖动 → 循环被外层 catch 打断，弹「提取失败」——但服务端任务其实还在跑并会成功入库（谎报失败；用户重传浪费 GPU） | 4/13/1 | static/index.html:1833-1867 |
| B11 | 🟠 | 文档一致性：README/README_EN 承诺「队列按一次提交记身份，面板只显示这一批」「每个任务唯一 ID 点一下即复制」「档位自动配对」——前两条在运行时（旧后端）不成立（见 B8），第三条的复制失败谎报见 B9；README_EN:33 把勾选框文案标成英文 UI 语境（页面本身全中文，无 i18n），中英文档描述的行为与实测环境不符 | 17/18 | README.md:51/195 附近、README_EN.md:33/91、static/index.html 全文（无任何 en 资源） |
| B12 | 🟠 | 渲染合并去重不覆盖「同 id 双来源」全部情形：`seen` 以 history list 为底，`extra` 内部**不再互相去重**（2011-2012）——批处理条目（isQueue）与 `/api/history/active` 条目同 id 时会渲染两行（队列行+活跃行）。当前实测 active 只有 1 条生成中任务且该 id 同时在 batch items（3a9a running）——历史 list 里 3a9a 尚不存在（未落盘），故**此刻队列行与活跃行同 id 各渲染一行**：实测样本 `20260926_170851_3a9a | histrow#0` 与 `nonrow:status` 是两处不同容器未成双行，但 `20260926_170851_9558`（pending，仅 batch items 有）与 active 无冲突；一旦 active 与 batch items 同 id 且 list 无该 id（服务重启后 output meta 被标 error 清除前窗口/新提交瞬间），必现两行 | 1/6 | static/index.html:2009-2012 + 1971-1999 |
| B13 | 🟡 | 监听器审计：document 级 click 监听器仅 1 处（968，模块顶层，渲染函数外）——重复渲染**不会**重复注册（已实测证伪）；其余 document 监听 4 处均一次性（968/2935/2992/2998）。修复方向：保持顶层注册即可 | 13 | static/index.html:968、2935、2992、2998 |
| B14 | 🟡 | 捕获阶段拦截已实测成立：探针 `.tid` 点击后行 onclick `rowFired=0`、toast「已复制 ID」出现——挡住了行点击，无缺陷；仅 `preventDefault` 对 a 类徽标无意义（样式 span，可忽略） | 1 | static/index.html:968-978（实测通过） |
| B15 | 🟡 | 空 `catch {}` 共 26 处（985,999,1016,1020,1036,1087,1130,1225,1227,1263,1276,1395,1398,1598,1651,1683,1988,2000,2202,2274,2465,2510,2553,2725,2894,2931）；其中 1651/1988/2000/2553 属「轮询吞错不重试」高危组，其余多为尽力而为可接受。修复方向：轮询类 catch 至少要 `setTimeout` 续轮 + 连续 N 次失败降级提示 | 4 | static/index.html 上述行号 |
| B16 | 🟡 | 无超时 fetch：仅 4 处用 `fetchT`（2790/2812/2829/2857），其余 60+ 处裸 `fetch`；提交类按钮有 disabled/finally 配对（go 无 finally 但 catch 里 setGenerating(false) 兜住；queue/extract/rvcGo/trainGo 均 finally 复位），未发现「按钮永久禁用」死路；但 `pollJob`/转谱轮询挂死即 B6/B10 | 4 | static/index.html:861-869 vs 全文 fetch( 调用点 |
| B17 | 🟡 | 新增控件可访问性：勾选框有 `<label for="abcChords">` 正确绑定、键盘可聚焦 ✓；`.tid` 徽标是 `span[title]`，非 button、不可键盘聚焦、点击是唯一交互方式，SR 只读出一串数字；信息本身未只靠颜色传达（文本可见）✓。修复方向：tid 改 `<button type=button>` 或加 `tabindex=0 role=button` | 18 | static/index.html:607-610、963-966 |
| B18 | 🟡 | 点击目标尺寸：`.tid` padding 0 5px、字号 10.5px，命中区约 5×14px，远小于 24px 指南；历史行的整行点击域很大，可对冲。全页 aria-* 出现 0 次（grep 计数），标签页切换用原生 button 尚可 | 18 | static/index.html:369-371(.tid CSS)、2050-2084 |
| B19 | 🟡 | `escapeHtml` 覆盖审计：idTag 内 `data-id`/文本双转义 ✓；renderHistory/queueBar/RVC/train 各插值点抽查均经 escapeHtml 或已是数字；`j.name`（1755 状态文本用 textContent ✓）；未见裸插值新增点。遗留风险低 | 4/18 | static/index.html:963-966、1957、2050-2084 |
| B20 | 🟡 | `cot_suggested` 缺失时前端兜底猜测 `$("abcChords").checked ? "full" : "melody"`（1845-1846）与后端 `_resolve_cot` 按**谱面实际内容**判定（app.py:539-552）口径不同：若勾了但识别失败无和弦（`wantedChords` 分支只是提示），或没勾但谱里其实混进和弦记号（旧谱回填/手贴），提交时后端会纠偏并 toast「已改用…」——与「我明明选了 X」观感冲突，但**行为对用户有利**（谱优先）。属提示话术问题非逻辑错 | 1 | static/index.html:1845-1853 + app.py:530-552 |
| B21 | 🟡 | 定时器清理全景：顶层 `setInterval(refreshHealth,15000)`、`refreshModelVerify,60000`、`syncFromParent 800ms`、client.js 的 5s 状态轮询/8s favicon XHR/10s iframe 看门狗均**无页面级清理路径**（单页应用可接受，但 client.js favicon 的 useEffect deps=[] 且无 return clearInterval，组件卸载即泄漏；8s 同步 XHR 阻塞 UI 线程，慢网关时拖死整个 dsh 界面） | 13/6 | static/index.html:3028-3029、1090；dsh-plugin/lib/client.js:327-343、84（84 有 cleanup 97 ✓）、377-399（有 cleanup ✓） |
| B22 | 🟡 | 性能量级：历史页一次渲染 = 3 个 fetch（generate/list 全量 99 条 + batch/status + history/active）+ 99 行 DOM 全量 innerHTML 重建（实测 rowsBefore/After=99）；队列运行中每 5s 一轮（fetchExtraTasks 的 setInterval 回调→renderHistory→再发 3 fetch），另有 syncFromParent 800ms 同步读父文档计算样式。单机 5s 重建可忍受，但「N 行 × 重建 + 选择丢失」见 B5 | 6 | static/index.html:2004-2035、1982-1987、1057-1091 |
| B23 | 🟡 | 换声/训练列表的完成 toast 依赖被 B2/B3 崩溃的分支，即便不崩溃，`pollRvc` 404（重启后 rid 不存在）时 `r.ok` false → 整段跳过但**继续挂定时器**（2553-2557），永不自清 → 服务重启后旧任务轮询永久空转。修复方向：404 连续 3 次即 clearInterval + 行标「状态未知」 | 13/4 | static/index.html:2513-2557、2854-2898 |
| B24 | 🟡 | 「一键继续」`$("batchResume").onclick`：`jfetch("/api/batch/resume")` 返回 Response 直接 `r.message`——Response 没有 message 字段，永远 toast「已恢复队列」（假成功文案兜底），与后端 `{"ok":true,"message":…}` 语义脱节 | 4 | static/index.html:2329-2338 |
| B25 | 🟡 | `idTag` 在 `$("status").innerHTML`（1570-1575/1606-1608）拼接生成中状态行：job.id 若含特殊字符已在 escapeHtml 内 ✓；但 status 行重建同样由 pollJob 每 2s 触发，正选中 ID 文本会被 `user-select:all`+重建冲掉（同 B5 家族） | 6/13 | static/index.html:1554-1576 |

---

## 二、🔴/🟠 展开

### B1（🔴 维度13/6）dsh 侧栏状态卡条件提前 return 跳过 Hook → 整个 footer 槽崩溃（实测复现）
- **证据链**：`dsh-plugin/lib/client.js:103-157`。`StatusCard` 里 Hook 顺序为 `useLabStatus()`(内部 useState+useEffect)、`react.useState(switching)`(105)，随后 **111 行 `if (rail) return null;`**，而 127-133 还有 `react.useState(vBad)` + `react.useEffect(...)`。rail（侧栏收起）由 107-109 行**读 DOM className** 得出，与 Hook 顺序无关地翻转 → React 报「Rendered fewer hooks than expected」。浏览器实测 `http://127.0.0.1:3081` console：**`slot entry crashed in 'sidebar.footer.action'` + Minified React error #300**（msgid=3/4），即用户当前环境正在发生。另：130 行 `useEffect` 依赖数组含 `s.tick`（轮询计数），每次 5s 轮询都触发模型校验 fetch——放大面。
- **触发条件**：收起/展开 dsh 侧栏（rail 值变化），或首帧 rail=true 后续 false。实测环境已在崩。
- **用户可见后果**：左下角状态卡+实验室页签整块消失/报错（error boundary 吃掉整个槽），用户失去页签导航入口，只能手改 URL hash。
- **修复方向**：把所有 Hook 提到组件顶部（`useState(vBad)`/`useEffect` 移到 `if (rail)` 之前），rail 用状态订阅 MutationObserver 而非渲染期读 DOM。

### B2/B3（🔴 维度4/13/1）Set 冒充 Map：换声/音色制作的提交误报失败 + 定时器指数泄漏
- **证据链**：`static/index.html:2453` `const rvcPolls = new Set();`，2522/2532 调 `rvcPolls.get(rid)`，2554-2557 调 `rvcPolls.has/set`；`2729` 同款 `rvcTrainPolls`。Node 复现（本机跑过）：`Set.prototype.get/set` 不存在 → `TypeError: s.set is not a function`。基线 `308f833` 同样如此（`git show` 核对），且**本次新增的 ID 徽标恰好渲染在这两条路径的列表里**（2483、2768），注释「每个任务独立轮询，互不影响」是文档谎话。
  - 具体时序：`pollRvc` 首次被 `restoreRvcActive`/提交路径调用 → try 内 `r.ok` 且状态 running → 落到 2554 `!rvcPolls.has(rid)`（Set.has 存在，恒 false→真）→ 建 `setInterval` → `rvcPolls.set(rid,t)` **抛 TypeError**，该异常不在 try 内（2554-2557 在 catch 之后）→ 沿调用栈抛回提交处 try（2592/2961）→ **误报「提交失败：rvcPolls.set is not a function」**，而任务实际已提交成功、服务端在跑。
  - 泄漏面：每 5s（换声）/10s（训练）interval 回调 → pollRvc(rid) → 因 `has` 永假 → **再建一个新 interval** 再抛 → 活跃 interval 数 1→2→4→…线性起跳每周期翻倍；同时 done/error 分支（2522/2532/2877/2886）的 `clearInterval(rvcPolls.get(rid))` 也抛（.get 不存在），在 try 内被 2553/2894 `catch {}` 吞掉 → **完成 toast、状态文本、renderHistory 全部跳过**，界面永远显示「转换中/训练中」——正是红线「谎报进行中」；定时器永不自清。
  - 2937 行 `rvcTrainPolls.forEach((_t, rid) => pollRvcTrain(rid))`：Set.forEach 回调签名是 `(value, value, set)`，`_t`/`rid` 拿到的是同一个 timer id 数字（若曾 add 过），但这里从没 add 成功过 → 空集合，回前台补轮询形同虚设（注释「回前台立即补一次」不成立）。
- **触发条件**：任何一次换声或音色训练提交（真实使用模式，页面自身流程，无需特制条件）。
- **用户可见后果**：提交后弹「提交失败」→ 用户再点一次 → 双重训练抢 GPU（后端有 _GPU_SEM 排队，但用户以为没提交）；任务完成后界面永远「训练中」，直到刷新页面；开着换声页挂机，页面越来越卡（定时器翻倍 + 每 5s×N 个 fetch）。
- **修复方向**：`new Map()` 并改用 `get/set/has/delete`；`clearInterval` 前先取值判空；done/error 的 UI 更新移到 clearInterval 之前；pollRvc 对 404 设退出条件。

### B4（🔴 维度1/4/17）表单持久化引用的 `currentConfig/applyConfig` 不存在——「切页不丢歌词」从未生效
- **证据链**：`static/index.html:2977-3002` boot IIFE 调 `applyConfig(c)`(2984) 与 `currentConfig()`(2989)，全文件（含基线，`grep -rn currentConfig` 仅命中引用与两份历史 tmp dump）**无定义**。2989 行 `currentConfig()` 在 `save()` 内但**未被 try 包裹**（try 只包 localStorage.setItem，JSON.stringify(currentConfig()) 也在 try 内——细看：`try { localStorage.setItem(KEY, JSON.stringify(currentConfig())); } catch (e) {}` ——在 try 内，抛错即被吞）；2984 恢复路径 `if (c && (c.style||c.lyrics)) applyConfig(c)` 也在外层 `try…catch(e){}`（2980-2986）里被吞。input/change 监听（2992-3000）每次编辑触发 save → 每次静默抛 ReferenceError。运行时佐证：console 层面被吞无报错——**注释与 README「切 dsh 对话页再切回（iframe 重载）也不丢歌词/曲风/参数」是纯谎报**（README.md 能力表同样承诺表单恢复语义）。
- **触发条件**：任何时刻在创作页输入内容后离开再回来（iframe 重载即触发）。
- **用户可见后果**：辛苦写的歌词/Style/参数在切页/刷新后**全部丢失**，与文档承诺相反；无报错、无提示（静默）。这是用户日常最高频操作。
- **修复方向**：要么实现 currentConfig/applyConfig（收集 style/lyrics/abc/cot/seed/… 各字段），要么删掉整段死代码并收回文档承诺；顺手把 `abcChords` 纳入持久化（见 B7）。

### B5（🟠 维度6/13/1）5 秒队列轮询整体重建历史 DOM，冲掉展开菜单/错误全文/选中文本
- **证据链**：`fetchExtraTasks` 1982-1987 在 `b.running` 时挂 `histQueuePoll=setInterval(renderHistory, 5000)`；`renderHistory` 2034 `el.innerHTML=""` 后逐行重建；「⋯ 更多」菜单状态只活在 `moreMenu.style.display`（2168-2172），错误展开状态活在 `errLine.dataset.open`（2125-2135），**没有任何状态在重建前后被序列化/恢复**（对比 2732-2738 训练列表有 `_lastTrainKey` 脏检查防抖，历史列表没有）。**浏览器实测**：history 页签点第一行 `[data-more]` → `opened1=true`；等 6.5s → `menuStillOpenAfter6s=false`，rowsBefore=rowsAfter=99（整表重建），queueBarText=""（旧后端无 queue 字段，见 B8）。
- **触发条件**：队列运行中停留在任务管理页做任何需要两步的操作（开更多菜单→点删除/回填；展开错误全文→复制错误串）。
- **用户可见后果**：菜单在鼠标移过去的路上自动关闭、展开的报错文本缩回省略号、文本选区被清空——批量跑几十分钟时用户全程与轮询抢 UI；严重时误点落在重建后的另一行上。
- **修复方向**：队列轮询改用 diff/键控更新（学 `_lastTrainKey` 脏检查，或只更新状态列文本节点），或检测到 `document.querySelector('.h-more[style*=""]')` 打开态时跳过本轮重建。

### B6（🟠 维度4/13）生成进度轮询一次失败即永久断线，前台谎报「生成中」
- **证据链**：`pollJob` 1631-1652：`try { fetch /generate/current … } catch {}`——catch 内**没有任何 `pollJob()` 续轮**；正常路径靠成功后再调 `pollJob()`（1637）。一次 `fetch` reject（网络抖动、网关重启瞬间、502 HTML 使 `.json()` 抛）即整链终止。此后 `#status` 保持最后一次渲染的「服务端生成中（刷新页面不会丢失任务）… 104分31秒」不动（**冻结即谎报**——定时器数字停走，任务实际可能早已 done/error）。恢复途径只有手动刷新页面（`resumeRunningJob` 只在 boot 跑）。对照：`app.py:1465-1479` 重启后会清扫 orphan，但前端已不问。
- **触发条件**：用户挂机生成期间 WiFi 瞬断/网关看门狗重启/显存回收导致的一次慢响应——本项目场景（挂机几十分钟）里属常见事件。
- **修复方向**：catch 里 `pollJob()` 续轮 + 连续失败计数 >N 时把状态行改为「连接中断，正在重试…」（诚实降级），`visibilitychange` 回前台时（2935 现在只喂 train）同时补 `pollJob()/refreshHealth()`。

### B7（🟠 维度1/18）「提取时带和弦」勾选框与真实提交/回填的一致性缝隙
- **证据链**：提交点 1820 `form.append("melody_only", $("abcChords").checked ? "false":"true")` ✓ 代码正确。缝隙一：**持久化正则 `/^(style|lyrics|abc|cot|seed|cfg|steps|gender|bpm|abc_|semantic_|genCount|model|taskName)$/`（2993）不匹配 `abcChords`**——即便 B4 修好，勾选状态仍然 iframe 重载后归零（默认不勾），而 `abcStatus` 文本、`cot` 下拉都是重载前状态，用户认知「我勾了和弦」与下次提交实际值背离。缝隙二：历史乐谱回填（1884-1895）把 `cot` 拨到 `rec.analysis.cot_suggested`，**不回写勾选框**——带和弦的旧谱回填后 cot=full，但框仍不勾，此时用户再点「提取」想做二次转谱，默认按纯旋律提，界面两处（下拉 full / 框未勾）传达矛盾信息。缝隙三：转谱 20 分钟等待期间勾选框未被禁用（1814-1868 只禁 `extractAbc`），中途改框会让完成回调 1845-1850 用**新**框值兜底猜测与告警文案（`wantedChords`），与**实际提交时**用旧框值转出的谱面可能互相矛盾。
- **触发条件**：挂机转谱 + 切页/中途改勾选/回填旧谱的组合，长任务场景可达性高。
- **修复方向**：持久化键加 `abcChords`；回填谱时同步 `$("abcChords").checked = (rec.analysis.cot_suggested==="full")`；轮询等待期 disabled 勾选框；完成文案改以**提交时快照值**为准。

### B8（🟠 维度1/17）需求④「这一批」计数：代码对、运行时错（后端未重启 + 旧队列无 qid 的迁移断档）
- **证据链**：磁盘版链路完整：`app.py:1727-1730` 新提交生成 `qid` 并入 items（1744）、1777 `state["qid"]=qid`、1825-1835 按 qid 切出 `cur` 与 `queued_hist`；前端 1949-1966 消费 `b.queue/b.queued_hist`。**实测运行态**：`/lab-api/batch/status` 当前返回 `{running,total,items,…}` **无 queue/queued_hist 字段**（旧版网关，`app.py` 改动需重启）→ 前端 `q=(b&&b.queue)||null` → null → queueBar 空白（`bar.innerHTML=""`），用户看不到任何「这一批」信息——误导消除了，需求④的承诺也未兑现。且正在跑的这批「0926-1708」是旧版提交（实测 items 里 58 条、含 1 running/1 pending/56 done），条目 `qid` 字段有无取决于旧代码——**旧代码无 qid**（基线 app.py 无该逻辑），重启后 `cur_qid = state.get("qid") or items[-1].qid` → 全部 undefined → cur=[] → 「这一批 0/0」而真实在跑 2 首：新一轮误导（从 58/58 过度到 0/0）。
- **触发条件**：发布后未立即重启网关（README 明说改 app.py 要重启）；或对发布前已入队的批次执行续跑/重试。
- **修复方向**：前端对 `q.id && (q.total>0 || b.running)` 才隐藏否则回退显示 `pending/running` 计数（从 items 现算）；后端 batch_status 对无 qid 旧条目按「最后一次 running/pending 连续段」兜底分组；发布说明写死「队列面板生效需重启」。

### B9（🟠 维度18/4）ID 徽标复制：非安全上下文 fallback 谎报成功
- **证据链**：`968-987`。实测 `isSecureContext:true`（127.0.0.1 路径）navigator.clipboard 存在、探针点击 → `rowFired=0`（捕获拦截成功）+ toast。**对抗面**：网关 :7863 常以 `http://192.168.x.x:7863` 访问（README 局域网用法）→ 非安全上下文 → `navigator.clipboard` undefined → `fallbackCopy` → `document.execCommand("copy")` 现代浏览器**在用户手势内基本可用但失败时返回 false 不抛**，代码 `try{ execCommand; done(); }catch{}` **不看返回值** → 复制失败也弹「已复制 ID：xxx」（谎报成功，用户报障时 clipboard 是空的）。第二面：`writeText().then(done, ()=>fallback)` 在 iframe 无焦点/权限弹窗时 reject→走 fallback→手势链已断（Promise 微任务仍在手势栈？异步后 execCommand 可能失败）→ 同上谎报。第三面：ID 徽标 `span+title`，键盘不可达（见 B17）。
- **触发条件**：局域网 IP 访问 + execCommand 被拒；或 clipboard 权限受限的 Chrome 配置。
- **修复方向**：`if (!document.execCommand("copy")) { toast("复制失败，请手动选中", "err"); }`；徽标改 button。

### B10（🟠 维度4/13）转谱结果轮询无 `!rr.ok` 分支，一次抖动即「提取失败」谎报
- **证据链**：1837-1839：`const rr = await fetch("/api/score/result?...")` → `rr.json().catch(()=>({}))` → `if (!rr.ok) throw`。`!rr.ok` 看似有——但**网络 reject（非 HTTP 错误）走外层 catch(1862)** → 「提取失败: Failed to fetch」。服务端 `_score_run`（app.py:645-669）独立线程仍在跑，成功后照常 `_score_save` 入库 + Windows toast——用户看到「提取失败」遂取消勾选重提一次 → 同一首歌两份谱、GPU 二次转谱（和弦失败重提的指引文案 24 行反而教用户这么干）。`while` 循环也没有中断条件（离开页签不暂停，可接受）与最大重试计数。
- **触发条件**：转谱期间 dsh 代理瞬抖 / 网关被其他操作重启 / WiFi 漫游。
- **修复方向**：轮询内层单独 try，连续 3 次网络失败才认败；失败文案注明「服务端可能仍在进行，稍后可在『已保存乐谱』里确认」。

### B11（🟠 维度17/18）README 中英版与运行时不一致（前端行为部分）
- **证据链**：README.md:195「勾选『提取时带和弦』即得旋律+和弦完整谱」——磁盘代码成立（B7 缝隙除外）；**README_EN.md:33** 写 "tick **提取时带和弦**"——实际 UI 文案是中文「提取时带和弦（出「旋律+和弦」完整谱）」（index.html:609），英文读者按 "extract with chords" 找控件找不到（全应用 UI 无 i18n，`grep -c aria` 与语言切换均为 0，硬中文案全在 JS 模板字符串与 HTML 里）。README「队列按一次提交记身份」「ID 点一下即复制」两条当前运行时未兑现（分别见 B8、B9）。页内 hint（24 行）「勾选几乎不多花时间；但个别和弦性质不被记号表认得时整份谱会提取失败」——与 sheetsage_pt.py:143-166 注释、app.py 行为一致 ✓（这条文档是准的）。
- **修复方向**：README_EN 标注 UI 为中文界面并原样引用控件文案；或补 i18n。

### B12（🟠 维度1/6）history 合并去重不覆盖双 extra 来源同 id
- **证据链**：2009-2012：`seen=new Set(list.map(h=>h.id)); extra.forEach(x=>{if(!seen.has(x.id)) list.push(x);})`——`seen` 只含 history，**extra 内部（batch items 与 /api/history/active）同 id 会各 push 一行**。当前实测恰好避开：running 的 3a9a 在 batch items（isQueue 行 histrow#0）且**同时**在 /api/history/active（status 徽标另一容器）——`dupInHistList=[]` 是因为 list 里此刻已有该 id（`_gen_set_job` 先写了 meta？）或 active 行被 seen 吃掉——但 2026-09-26 实测显示 histrow#0=3a9a 仅一行，属侥幸路径：生成中的任务 output meta 在 `_batch_run_worker` 起笔即写（app.py:1682），`generate/list` 有它 → 两 extra 均被 seen 挡掉。**反向情形**：RVC/训练 running 条目不走 output meta → 不在 seen → `/api/history/active` 给一行，若将来 rvc 也进 batch items（现已不）则双行；更现实的**队列被删/改名瞬间**：`/api/generate/{rid}` DELETE 后 batch items 尚存同 id（batch 状态文件独立清理）→ pending 队列行 + 已删 history 行并存一屏（实测未触发，静态成立）。删除按钮 `data-del` 只删 generate 记录不删队列条目（2317-2322 vs app.py:1915 队列删除是另一路由）——用户在任务管理页删掉队列里一首，行变灰还在（下轮轮询由 batch 状态驱动）？**队列 pending 行的删除走 `/api/generate/<id>` DELETE → 404 成功与否都 renderHistory** → 条目仍在队列 → 删除「没反应」。
- **用户可见后果**：删队列任务删不掉；边界时序下同任务两行（两行按钮目标不同，误点重试/终止打到别的行）。
- **修复方向**：合并后按 id 全局二次去重（Map by id，队列行优先）；pending/running 队列条目删除路由改调 `/api/batch/{rid}` DELETE（后端已有，前端没用）。

---

## 三、维度覆盖声明

- **1 需求一致性**：① 勾选框存在、绑定 label、`melody_only` 提交方向正确（index.html:1820 ↔ app.py:673/685 ↔ sheetsage_pt.py:143），**通过**；但持久化/回填/等待期改框三处缝隙见 B7。② 双向配对在后端 `_resolve_cot`（app.py:530-552 双向纠正+note）、前端提示 renderAbcAnalysis（1776-1786）解释到人话，**通过**；`cot_suggested` 缺失兜底与后端口径差见 B20。③ ID 可见性：idTag 覆盖进度行/完成行/历史/队列/乐谱/RVC/训练 7 处渲染点，唯一性后端 `_new_id` 磁盘查重（app.py:470-486）——**功能存在**；可访问性与 fallback 谎报见 B9/B17。④ 「这一批」计数代码已修（B8），**运行时未生效且对当前批次会产生 0/0 新误导**。渲染路径：B12 双行漏洞、B5 轮询冲 UI。
- **18 可访问性/国际化**：全文件 `aria-*`=0、`role/tabindex`=0（grep 实测）；label 绑定良好（545-599 全部 for=id）✓；新增勾选框合规 ✓；`.tid` 仅鼠标可交互（B17）、命中区 ~14px（B18）；复制在非安全上下文谎报（B9）；无任何 i18n 机制，全部文案硬编码中文于 HTML/JS 模板，README_EN 引用的控件名与英文文案在 UI 中不存在（B11）。状态信息未只靠颜色传达（图标+文字并行）✓。
- **13 生命周期**：document 级监听仅 4 处且全顶层，重复渲染不叠加（B13 已证伪「重复注册」假设；实测探针佐证）；`.tid` 捕获拦截实测挡住行点击（B14）；B2/B3 定时器泄漏翻倍是最重生命周期缺陷；B6 pollJob 断链、B10 转谱断链、B23 404 不自清；顶层 setInterval 无清理（B21）；visibilitychange 只喂训练轮询（2935-2938）；离页后轮询继续=预期（服务端托管），但**隐藏期节流+回前台不回补**导致 B6 冻结期更长。
- **6 性能**：实测一次历史渲染=3 fetch+99 行全量重建，队列期每 5s 一轮（B22）；无 N+1（列表接口单次返回全量 meta，逐条 audio/lrc 只在点击时取）；`user-select:all`+重建互冲（B25）；RVC 提交按钮 disabled/finally 配对全查：go/queue/extract/vSave/rvcGo/trainGo 均有 finally 或 catch 复位，**未发现永久禁用死路**；连点防护靠 disabled 前置 ✓，`go` 连点两次在 409 路径有友好文案 ✓。
- **4 错误处理**：空 `catch {}` 26 处清单（B15），高危 4 处（1651/1837-1662 族/2553/2894）；`fetchT` 超时仅 4 处使用（B16）；JSON 解析失败多数 `.catch(()=>({}))` 后有 `!r.ok` 分支 ✓；谎报组：B2/B3（成功报失败）、B6/B23（终态报进行中）、B9（没复制报已复制）、B24（假成功 toast）、B10（成功报失败）。
- **17 文档一致性（前端相关）**：页内 hint 与后端行为逐条核：cot 四档解释（634-640）与 `_resolve_cot` 一致 ✓；「有乐谱自动按谱」✓；「转谱失败就取消勾选重提」指引与 `_score_run` 落盘行为存在 B10 冲突 ⚠；README 承诺表（B11）；client.js「不自动重载 iframe」✓ 实测代码遵守（fails>=3 只点提示条）。

## 四、优先级路线图

- **P0（红线级，本周内）**：B2/B3（Set/Map——一行改 `new Map()` 解四害）；B1（Hook 顺序）；B4（实现或删除持久化死码，先止损文案）；B6（轮询吞错续轮）；B10（转谱轮询容错）。
- **P1（发布门槛）**：B8（发布需重启网关的显式提示 + 旧批次 qid 兜底 + 前端回退计数）；B7（abcChords 持久化/回填同步/等待期禁用）；B5（历史列表脏检查或状态保持）；B9（execCommand 返回值）；B12（双源去重 + 队列删除路由）。
- **P2（择期）**：B21 定时器清理收口、B17/B18 徽标 button 化与命中区、B11 README_EN 措辞、B24 jfetch 返回值统一、B15 空 catch 治理、B23 404 退出条件、B22/B25 渲染量控制。

## 五、做得对、值得保持

- `fetchT` 超时工具与 409 友好提示的存在；提交按钮 disabled/finally 配对总体工整；`.tid` 捕获阶段拦截的实现干净（实测一次通过），`escapeHtml` 覆盖新插值点，ID 磁盘查重（`_new_id`）设计诚实；`_resolve_cot` 双向纠正 + `cot_note` toast 让用户当场知道路线被改；LAB_BASE 反代自适配与 MutationObserver 前缀补齐考虑了双宿主；client.js 看门狗「连败 3 次只提示不自动重载」尊重用户挂机现场；训练列表 `_lastTrainKey` 脏检查正是历史列表该抄的样板；转谱失败绝不回读旧谱（sheetsage_pt.py:165-166）——宁可报错不喂假数据，这条价值观应保持。

## 六、未验证项

1. B2/B3 的指数泄漏未在运行时实测（严禁点击提交按钮）；结论基于代码路径 + Node `Set.prototype.set/get` TypeError 复现 + console 历史错误旁证。
2. http://局域网IP:7863 直访下的 clipboard 行为（环境未提供该入口凭据），B9 为语义推断 + execCommand 规范行为。
3. 重启新 app.py 后队列面板的真实渲染（未做，避免干扰正在跑的批量任务）；B8 的 0/0 迁移推导基于 items 无 qid 的旧状态文件（未直接读该文件内容验证）。
4. 标签页完全隐藏（非 dsh 内嵌）时 `histQueuePoll`/`pollJob` 的浏览器节流幅度未测。
5. AI 工作台 iframe（aiFrame）内部生命周期未审（属另一宿主产品，超出本组仓库范围）。
