# 第三方独立审计 — 基线 `308f833`（第三轮，评审对象 = RESPONSE-308f833 整改后的工作区）

- 审计身份：第三方（未参与蓝军与整改两轮），一切结论只认代码，不认报告措辞。
- 取证手段：Read/Grep、`py312/python.exe -m unittest discover -s tests`（实跑）、`node --check` 级代码阅读、只读 GET（`/api/batch/status`、`/api/history/active`）、`py312` 下的一次性 pathlib 行为探针（未落盘任何文件，未向服务发任何写请求）。
- 生效态提醒（沿用 RESPONSE 第六节，且被本次只读探测证实）：网关进程仍是旧代码（在跑批量队列里当前任务 ID `20260926_170851_3a9a` 为 4 位 hex 旧格式，新 `_new_id` 是 8 位 hex，见 `app.py:589`），以下所有后端结论针对"重启后即将生效的代码"。

## 〇、结论先行

**整改总体可信度高**：抽查的 12 个"已修"声明里 9 个确认在代码中真实落地且接线正确，33 个回归用例独立复跑全绿（0.644s），RESPONSE 对未覆盖面的自述基本诚实。但**本轮整改引入了 1 个 100% 失效的新缺陷**（B21 的 favicon 探测把 fetch 的 `x.ok` 语义用在 XMLHttpRequest 上，忙碌角标从此永远不会亮），**新后端里存在 1 个会静默丢任务的并发缺陷**（批量 worker 用过期快照整体回写状态文件，与 `batch_start` 追加竞态 → 用户收到"已加入队列"但条目消失），另外 **Y1 的 LAN 放宽把 DNS 重绑定 CSRF 请回了门内**（LAN 模式下 Origin 主机名==Host 主机名即放行，重绑定下两者天然相等，且端口不参与比较）。蓝军 G4 的编造问题在本轮 RESPONSE 中已被正确纠正（`audiocpp.py` 确为活依赖，`app.py` 顶部 import 链可见）。

## 一、验伪结果

只列我逐行查过的条目。"证据"均为当前工作区 文件:行号。

| 声称（RESPONSE 编号） | 裁定 | 证据与分析 |
|---|---|---|
| S3 守卫 `@app.middleware` 拦 Host/Origin | **确认落地，残余如实** | `app.py:201-222`。中间件在路由前执行（测试用 403/405 差值证明，`tests/test_review_fixes.py:203-208` 我复跑通过）。**`X-Forwarded-*` 全仓零引用**（grep `forwarded` 于 `app.py` 无命中），没有被误当信任依据。非 LAN 模式下 `localhost.evil.com`、带点尾巴等主机名解析均判非回环（`_host_is_loop`，`app.py:147-153`）。 |
| Y1 LAN 模式放宽 | **确认实现，但引入新绕过面** → 见二-3 | `app.py:156-171`（启动断言）、`app.py:188-198`（`_origin_allowed`）。放宽本身按声明做了；问题是放宽规则本身可被重绑定满足。 |
| Y4 安全响应头 | **确认** | `app.py:215-221`，`setdefault` 不覆盖下游已有头；CSP frame-ancestors 白名单只含回环两端口，测试 `test_security_headers_on_every_response` 锁住。 |
| S7 `_output_purge`/`_rvc_job_purge` | **确认，防御到位** | `app.py:1115-1130`：`os.path.basename` + `(".","..")` 显式拒绝 + 白名单后缀逐一试删，绝不 glob——`rid="*"` 只会尝试删 `OUTPUT_DIR/"*.wav"` 这种字面名，`is_file()` 为假。Windows 盘符探针：`os.path.basename("C:x.wav")=="x.wav"`（盘符被剥掉），备用数据流 `a.wav:evil` 拼出的名字在 NTFS 上非法、`is_file()` 假、`unlink` 异常被 `except OSError` 吞——都不构成逃逸。`_rvc_job_purge`（`app.py:1133-1146`）额外做 `d.parent != RVC_JOB_DIR.resolve()` 父目录等值校验，比声称的还严。 |
| D13 `.elrc` 进白名单 | **确认** | `app.py:1112` 含 `.elrc`；端到端用例 `test_generate_delete_clears_output_and_rvc_workspace` 实跑绿。 |
| `_limit_text` + `_MAX_*` "不再静默截断" | **主入口确认，存储入口仍有旁路** | 调用点仅 `generate_start`（`app.py:1558-1560`）与 `batch_start`（`app.py:1938-1940`）两处，两道闸门都在任何落盘/起线程之前，异常时无线程已启动——无半成品。旁路：`POST /history` 的 meta 仍 `[:600]/[:4000]` 静默截断（`app.py:985-988`）、`POST /templates` 字符串仍 `[:4000]` 静默截（`app.py:3583`），模板回填出的 abc 可能比原稿短（上限 4000 < _MAX_ABC 20000 且不留痕）。GET `/abc/analyze` 无长度闸门（`app.py:759`）。 |
| D7 `_CHORD_RE_LOOSE`/`_abc_chord_tokens` | **确认，存在记号误判边缘** | `app.py:626-634`：信息行（`X:`/`T:` 等）被滤掉再找记号，扩展记号 `C9/Fm9/G13/Cmaj9/Csus/A7alt` 全命中（测试覆盖）。边缘：宽松式把正文引号里任何 A-G 开头的单词当和弦——`"Chorus"`、`"Dog"` 这类注释/标注文字会假阳性 → 纯旋律谱被误路由 full（见二-8）。 |
| `_resolve_cot` 三条入口自洽 | **确认同一判定** | 单首 `app.py:1562`（缺省 full）、批量 `app.py:1944`（缺省 off 但带谱必纠）、转谱谱子经前端回填后仍走同一 `generate_start`。`cot=off` 带谱改判 + note 透出（`app.py:678-684`）。前端 `route = a.cot_suggested` 不再自己猜（`static/index.html` B20 侧与后端不矛盾）。 |
| S11 `models_switch` | **确认，判定正确** | `app.py:1041-1054`：先 `\→/` 归一、剥一层 `model/` 前缀，再 `Path(path).name != path` 即 400——盘符（探针实测 `Path("C:x.gguf").name=="x.gguf"` 会被拒）、`..\`、`sub/dir/` 全挡；`MODEL_DIR/safe` 是纯文件名，`is_file()` 存在性校验后才写 `server.json`，且**写盘用的就是校验值**（`app.py:1073-1077`）。小瑕疵：`path` 传非字符串（如 int）时 `.strip()` 抛 AttributeError → 500（类型未防御，不构成穿越）。 |
| Y5 `rvc_model_check` 缓存 | **确认落地，`force` 是有意留的活门** | `app.py:2178-2231`：缓存键=name，指纹=`(mtime_ns,size)`；同名替换文件必然改变 mtime_ns → 旧结论不会复用，**"缓存键只有 name 被同名替换绕过"不成立**（要绕过必须同时伪造 mtime 到纳秒和字节数一致）。`?force=1` 仍能触发 300s 子进程：GET 无 Origin 校验（S3 只管写方法），`<img src=".../check?force=1">` 每次真起 `torch.load`（`app.py:2204-2211` 的非阻塞锁只是把并发变 409，不挡串行锤）；失败不缓存 → 对坏模型的同款反复起进程。属声明过的残余，但值得写进公开面。 |
| B2/B3 `rvcPolls` 改 Map | **确认** | `static/index.html:2516-2524`：`Map` + `rvcMiss` 分离，`stopRvcPoll` 三出口清定时器；404×3 判丢失已接线（`:2625-2631`）。 |
| B4 `_FORM_IDS`/`currentConfig/applyConfig` | **确认** | `static/index.html:3083-3097`，`abcChords` 单列处理（勾选框无 `.value`），回填后 `analyzeAbcDebounced()` 重算档位提示，与 B7 一致。 |
| D3 `downloadTrack(btn, fmt)` | **确认接线** | `static/index.html:2273-2309` 轮询 lrc；后端 GET 真的回 `elrc` 字段（`app.py:1745-1747`），无逐字版本时给明确报错而非空文件。 |
| B1 hook 顺序 | **确认** | `dsh-plugin/lib/client.js:103-122`：4 处 hook（104/105/113/115）全部先于 `if (rail) return null`（`:122`），122 之后无 hook。effect 返回 cleanup（`:360`）是合法返回语义，不破坏 hook 顺序。 |
| S2 协议注册不带参转发 | **确认** | `scripts/register_protocol.bat:20-25`：注册值只有 `"%START_BAT%"`，`%1` 不进命令行，rem 里把原注入路径讲对了。 |
| S1 argv 化 | **确认（结构保证）** | 体检/融合脚本源码里只有 `sys.argv[n]`（`app.py:2193-2203, 2255-2259, 2281-2310`），无任何用户值插进字符串；`%`/f-string 拼接 grep 无残留。RESPONSE 自认该路径无回归用例，属实。 |
| S4 `gpu_held` | **确认** | `app.py:3038-3047` acquire 前置 False、成功才 True；`finally` 仅 `if gpu_held:` 释放（`app.py:3206-3208`）。acquire 前的异常（`exp_logs.mkdir` 失败等）不再污染信号量。暂停路径（`:3187-3192`）也经同一 finally。 |
| `tests/test_review_fixes.py` "33 例全绿" | **确认，无谎报** | 我独立跑：`Ran 33 tests ... OK`（0.644s）。逐条对照：用例锁的行为与代码一致；RESPONSE 第七节"哪些修复没有自动化覆盖"的自账目与我核对结果吻合。两处用例偏弱（不指控谎报）：`test_private_default_path_is_env_overridable`（`:396-402`）实际只 grep 了源码文本含 `YUE2_GSV_ROOT`，没验证行为；`test_lan_mode_still_rejects_cross_site` 没覆盖"重绑定下 Origin==Host"这一支（见二-3）。 |

## 二、新缺陷（本轮整改引入 / 新后端代码中的缺陷）

### 🟠 N-1 `client.js` B21 修复自身失效：XMLHttpRequest 没有 `.ok`，favicon 忙碌角标永不点亮
- **证据链**：`dsh-plugin/lib/client.js:334-341` `probe` 用 `new XMLHttpRequest()`，`x.onload = function () { then(x.ok ? x.responseText : null); }`。`ok` 是 fetch `Response` 的属性，**XHR 上恒为 `undefined`** → `then(null)` 恒走失败支 → `:349-357` 两级探测都拿到 null → `applyBusy(false)` 恒定。
- **触发路径**：任何任务进行中，侧栏/顶层页签 favicon 从旧版"能亮（同步 XHR）"变成"永远不亮"。这是修 A（阻塞）破 B（语义）的教科书案例；同文件 `:61,117` 的 fetch 版 `r.ok` 是对的，唯独新写的 XHR 抄错了 API。
- **修法**：`then(x.status >= 200 && x.status < 300 ? x.responseText : null)`，或直接换 fetch。加一行最小 node 断言或目视验收。

### 🟠 N-2 批量 worker 用过期快照整体回写 → 并发追加的队列条目被静默吞掉
- **证据链**：`app.py:1844` `state = _batch_snapshot()`（读文件）→ `:1847-1856` 在**锁外**改 `nxt` 并 `_batch_store(state)` 把**整份旧快照**写回。`batch_start` 追加路径（`app.py:1964-1974`）在锁内写入新条目；只要追加落在 worker"快照之后、回写之前"的窗口（每个条目翻转 running 时都有这个窗口），worker 的回写就把刚追加的条目连同 `running=True` 一起覆盖掉。
- **触发路径**：队列正在切条目时用户"追加排队"→ HTTP 响应已返回 `已加入队列（排在第 N~M 位）`（`app.py:1984`），但该条目已从状态文件消失，永远不会跑，也没有任何错误提示。CPU 生成一首几十分钟、条目翻转点不密集，窗口毫秒级——概率低但后果是**谎报成功 + 丢任务**，且 `_batch_write` 非原子（`app.py:1819-1823` 直接 `write_text`，无 tmp+`os.replace`），进程在写一半时被杀 → 状态文件 JSON 损坏 → `_batch_read` 吞异常返回空（`app.py:1803-1804`）→ **整个队列历史静默清零**。
- **修法**：`_batch_store` 改为"锁内重读→只改自己那几格→写回"（参照 `:1891-1900` 已有的正确写法——同一文件里前半段和后半段是两种水准，说明这条路径确实没人审过）；`_batch_write` 走 tmp+rename。

### 🟠 N-3 Y1 的 LAN 放宽把 DNS 重绑定 CSRF 请回门内；Origin 比对不含端口
- **证据链**：`app.py:204-210` LAN 模式下**跳过全部 Host 校验**；`_origin_allowed`（`:188-198`）放行条件为 `Origin 主机名 == Host 头主机名`。攻击：用户开了 `YUE2_ALLOW_LAN=1` 后，evil.com 做 TTL=0 DNS 重绑定解析到该局域网 IP——浏览器发出的请求 `Host: evil.com:7863`、`Origin: http://evil.com[:80/…]`，两个主机名字符串天然相等 → 全部写端点（生成/删除/改模型/训练）放行。非 LAN 模式没有这个问题，因为 Host 必须先过回环断言。另外 `:198` 不比较端口：同机上任何其它端口的网页（如路由器/打印机后台、另一个 node 服务）Origin 主机名与 Host 相同 → 天然同源豁免。
- **定级说明**：默认配置（回环）不触发，故不给 🔴；但 RESPONSE 把它标成"守卫自动从只认回环放宽为只认同源（Y1·已修）"，**措辞让读者以为 LAN 下仍是安全的**——"同源"判定用两个都由攻击者域名决定的字符串来比，等于没判。
- **修法**：LAN 模式下改为 Host 白名单比对**回真实绑定 IP + 端口**（`Origin` 解析出的 hostname:port 必须等于服务实际监听的 `ip:port`，而不是等于请求头里的 Host）；并在 LAN 告警文案里写死"开启即重新引入重绑定风险，前面必须有反代鉴权"。Y2（stderr 原文泄露绝对路径/账号名）在 LAN 下同步从"可推迟"变"必须先修"——RESPONSE 自己也列了这个前提，两边要钉死。

### 🟠 N-4 单首生成进行中提交批量队列：`_GEN_JOB` 被批量条目抢走，进度显示张冠李戴 + 队列假 running
- **证据链**：`generate_start` 检查互斥用的是 `_GEN_JOB.status=="running"`（`app.py:1566-1569`），**批量方向却无对偶检查**；`_batch_run_worker` 在 `app.py:1880-1882` **先** `_gen_set_job(job)`+写 meta、**后**才 `with _GPU_SEM` 排队。单首在跑（信号量被整条连发链持有，`app.py:1616`）时提交批量：worker 立刻把全局 current job 换成批量条目并标 `running`，实际它在信号量上干等——前端"当前任务"卡从真在跑的这首歌变成一条根本没在算的假 running，完成后 `_gen_run` 只改自己闭包里的 dict（`app.py:1472-1486`），真单首的结束状态也不再回显。
- **触发路径**：`POST /batch/start`（队列面板追加）与单首"生成"并发。只读探针实证的当前态：`running=True, current=20260926_170851_3a9a, pending=1`——生产环境正好就有排队条目。
- **修法**：worker 里把 `_gen_set_job`+meta 移到 `with _GPU_SEM:` **之后**；或给 job 加 `queued: True` 状态并让 meta 写 `queued` 而非 `running`。

### 🟡 N-5 取消后批量条目卡 `running`，下一轮 `/batch/status` 把"用户取消"改写成"服务重启，任务中断"
worker `break`（`app.py:1883-1887`）发生在条目已标 running、终态回写（`:1891-1900`）之前；`batch_stop`（`:2053-2057`）只动 pending 不动 running；`batch_status` 兜底（`:2013-2022`）把残留 running 一律标 `error: 服务重启，任务中断`——服务根本没重启。同理 output meta 在取消竞态下（`_gen_run` 提前 return，`app.py:1445-1452`，而 `generate_stop` 与它写同一 `job` 对象存在先后覆盖，`:1528-1532` vs `:1479`）可能停在 running，懒清理 `_ORPHAN_SWEEP_DONE` 又已在首次 `generate_start` 时被置 True（`app.py:1570`），无人再修。文案误导 + 幽灵"进行中"。

### 🟡 N-6 `_batch_ensure_worker` 无锁判活起线程（`app.py:1903-1907`）：并发 `batch_start`/`resume`/`retry`（FastAPI 同步路由跑在线程池）可同时通过 `not is_alive()`，起两个 worker 重复执行同一条目（GPU 信号量只保证串行，不保证不重复），第二遍还因 `state["current"]` 竞态把终态写丢。修法：判活+赋值收进 `_BATCH_LOCK`。

### 🟡 N-7 RVC 训练上传无累计上限：`rvc_train`（`app.py:3213, 3242-3248`）逐文件 200MB 闸门，但文件数不限、总量只设下限 `total < 300_000`——本机任意进程可 POST N×200MB 直到磁盘耗尽（流式落盘修复的内存问题不再，但磁盘面没关门）。同理 `history_add`/素材入库均为单文件闸门、无目录总量校验。

### 🟡 N-8 `_CHORD_RE_LOOSE` 假阳性：正文引号内以 A–G 开头的普通单词（`"Chorus"`、`"Goes home"` 截断到 `"Goes"` 不成，但 `"Chorus"` 整词命中 `[A-G][A-Za-z0-9]*`）被当和弦 → 无和弦旋律谱被判 full。修法：宽松式同样只在"引号紧跟小节线/竖线后"的位置认记号，或要求引号内整体匹配记号形态（禁止含空格的英文单词形态整体命中）。

### 🟡 N-9 `启动音乐工作台.bat:65` / `启动dsh工作台.bat:34`：把 `%cd%` 裸插进 PowerShell **单引号**字符串——路径含 `'`（如 `C:\Users\O'Brien\...`）即语法崩、`catch{exit 1}` 静默失败，主脚本继续走到"网关未就绪"警告，用户拿不到根因（非 ASCII/空格实测可过，单引号是确定性炸点）。另 dsh 重定向固定写 `_dsh_web.log`：`ai_router.py:127` 以 `open(log,"ab")` 与 bat 的 `-RedirectStandardOutput`（每次**截断**重写，且 bat:119/`ai_router.py:73` 都要 grep token）共读共写同一文件——两处拉起逻辑对同一日志的语义（append vs truncate）不一致，token 竞读可能拿到空。建议 `python -c "import os;os.environ..."` 式传参或 `psobject` 参数化，日志路径与写入模式统一。

### 🟡 N-10 `ai_router.py:96-110` `_kill_stale_listener`：tasklist 只确证"node.exe"，未确证命令行属于 dsh——任何在本机 3081 端口上跑的无关 node 服务（用户自己的 dev server）都会被 `taskkill /F /T` 连坐。修法：`wmic/Get-CimInstance` 查 CommandLine 含 `dsh` 再杀。

### 🟡 N-11 存储入口静默截断残留（详见第一节 `_limit_text` 行）：`app.py:985-988`、`:3583`——与本轮"截断改判错"的口径自相矛盾，模板 4000 上限还低于 `_MAX_ABC=20000`，回填即丢谱尾。

## 三、补审 G1 缺口：`/api/generate/start` → runner → meta 落盘 → 批量状态机（深挖结论）

链路盘点（当前代码）：`generate_start`（`app.py:1551`）→ 原子占位 `_GEN_JOB`（`:1566-1571`，TOCTOU 已修，Y7 的"响应不再造第二个 ID"确认）→ `_chain_runner` 线程（`:1611-1642`）→ 逐条 `_make_job`+`_output_write_meta`+`_gen_run` → `_gen_run`（`:1398-1503`）内存预检/显存预检/CPU 回退重试一次/meta 成败必落盘。**主链本身没有致命正确性问题**；断点边界的问题集中在"取消/并发/复位"三个角：

1. **占位符可见性**：`_GEN_JOB={"id":None,...}` 占位期间 `_output_purge` 路径有 `job.get("id")` 判空保护（`:1526-1532`），`finally` 兜底清占位（`:1638-1642`），线程内 503 会写 error——Y7 声明成立。
2. **N-2/N-4/N-5 是本轮之前从未被审的两处真并发缺陷**（批次 worker 与 HTTP 线程池共享状态文件、单首与批量共享 `_GEN_JOB` 槽），外加 `_CANCEL_EVENT` 是**全局单例**：`batch_status` 自愈路径 `:2011` 和 `batch_resume/retry` `:2087,2113` 都无条件 `clear()`——若用户在"worker 已死但单首链还在排队"的瞬间点停止又触发别的复位，取消标记会被无关子系统抹掉，连发剩余条目复活。
3. **崩溃循环无上限**：服务重启后第一次 `/batch/status` 轮询即触发 `:1997-2012` 自愈，把上次崩在中途的条目复位 pending 并**自动重跑**；若该条目本身是崩溃诱因（如超大歌词打爆内存），前端每刷一次就复活一次，没有失败计数器。建议：自愈加 `restart_attempt` 计数，≥2 次停在 error 等人工。
4. 只读探针（`GET /api/batch/status`、`GET /api/history/active`）：当前真实队列为 `running=True, 1 running + 1 pending, 56 done`，运行中任务 meta 含 `cot:"melody"` 带谱——与 `_resolve_cot` 行为一致（该结论仅证明旧后端也自洽，因为它是旧代码）；未写任何探针文件，用完即无残留。

## 四、对蓝军与整改双方的评价

- **蓝军**：G3/G2b 的条目质量经得起逐行复核（S3/S7/S11/Y5 的缺陷描述与修法都对）；G4 的编造（`audiocpp.py` 死代码）被 RESPONSE 正确翻案，我的独立复核支持翻案。**漏网**：批量 worker 的读-改-写竞态（N-2）、单首/批量共享 current-job 槽（N-4）、取消语义错位（N-5）全部在蓝军射程外——三块都属 G1 阵亡带走的部分。
- **整改方**：RESPONSE 值得肯定的三处——主动交代 G1/G2 阵亡与"新前端+旧后端"组合态；第七节自报"哪些修复只靠读代码担保"；对 G4 逐条回查而不是照单全收。**没有回避，但有夸大**：(a) "Y1 已修"的表述掩盖了 LAN 下同源判定形同虚设（N-3）；(b) B21 的"已修"在我复测下是**换了一种坏法**（N-1），而 RESPONSE 恰恰把它列进"结构可 grep 验证"的放心条目。
- **Y2 推迟理由**："本机排障需要 stderr 原文"在纯回环前提下成立，我判**可接受的推迟**；但它和 Y1 是连体条件——`YUE2_ALLOW_LAN` 开关已经存在且写进文案，任何人一旦开启，Y2 立即从"隐私瑕疵"升级为"路径/账号名向同网段泄露"，建议把"开 LAN 必须先脱敏 stderr"做成代码里的硬约束（LAN 模式下裁剪 error 字段），而不是散在两份报告的表格里。
- **D12 推迟理由**："CI 只是装饰"的论证成立（用例依赖不入库的 `py312/`，`.github/workflows` 无法真跑），我判**推迟正确**；但 33 例本地全绿说明这批测试有真实回归价值，最低成本替代是把"发版前本地跑 discover"写进 D16 的发版 checklist，避免测试沦为一次性烟花。

## 五、我没能覆盖的（诚实清单）

1. **LRC 强制对齐算法本身**（`src/lrc_align.py` 时间轴可信度/锚点吸附）——一行未读，仍是零对抗覆盖。
2. RVC 换声主链（`/rvc/convert` 全流程）、训练 preview/pause/resume 三端点的状态机（`:2781,3325,3367`）只看了 gpu_held 一处。
3. `.bat` 端到端行为（禁跑）；`Start-Process` 环境变量继承只有整改方的 A/B 探针背书，我未重复。
4. `models/repair`、`backend/mode`、引擎 `server.json` 消费侧（cpp 二进制不可读）。
5. 新后端**从未在运行时生效**（网关仍跑旧代码），N-2/N-4/N-5 是静态推理 + 既有真实状态佐证，非动态复现；重启后请以这三条为第一批冒烟项。
6. `settings.py`/`src/ports.py` 端口漂移面、前端其余 ~57 个 fetch 调用点、D16/D18/Y8 的许可闭环（发布决策，超出审计手段）。
