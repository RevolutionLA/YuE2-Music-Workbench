# 蓝军审查报告 — YuE2 音乐工作台 @ d08c518 (master) · G2 后端契约与状态一致性

被审对象：`app.py` / `static/index.html`，基线 tag **v10.16.0 / commit d08c518**。
审查方式：**静态代码审查 + 只读接口实测**。具体做了：

- 从 git 导出只读副本（`git show d08c518:app.py`、`d08c518:static/index.html`）逐段读，**结论全部对得上 d08c518**；行号给的是工作树文件行号（工作树 app.py 相对 d08c518 只有三处在制品 hunk：`_score_save`/`abc_edit`/`scores_list` 的 title，位于 787-790、1063、1166 附近，本文**没有引用其中任何一行**）。
- 只读 GET 实测：`/api/system/info`、`/api/tasks/unified?limit=2000`、`/api/models/current`、引擎 `:8080/health`。
- 只读产物统计：`runtime/output/*.json` 163 份 meta（其中生成类 133 份）逐条解析，做 seed / cot_note / batch 交叉表。
- 纯函数复跑：以 d08c518 副本作为 `app` 模块导入，直接调 `_resolve_cot` / `_style_yield_to_abc` / `_ensure_song_ending`，量测四条改写同时发生时 note 的长度与两条路径的差异。
- httpx 头名大小写实测（`dict(r.headers)` 的键），用来判定 `X-Seed` 取值是否成立。

**未启动任何 GPU 任务，未重启网关，未修改任何产品代码或配置。**

---

## 先说公道话（这一版确实做对的）

1. `_resolve_cot` 不再装聋作哑（app.py:887-924）。无法识别的档位把原始值写进 note，而且**三条纠正分支都通过 `_with()` 把"值不认识"那半句带上**，两件事同时发生时不会只报一条。我用 d08c518 副本实测 `cot="bogus"` + 带和弦的谱：得到 135 字的 note，两段原因都在，没有互相吃掉。
2. 留痕真的落进了记录：单首 `_make_job` 写 `"cot_note": str(cot_note or "")[:400]`（app.py:2553），批量 worker 写产物 meta 时也把队列条目里那条说明接了过来（app.py:2894）——**批量这条最容易断的链，这次接上了**；`_unified_task` 白名单也加了 `cot_note`（app.py:1412）。
3. 缺省档位统一（app.py:2980 与 2509 都是 `or "full"`），并且批量补上了与单首**同一条**"节奏以乐谱为准"（app.py:2989）。这两处是真正的"同一请求两种结果"，修得对。
4. `[Outro]` 的追加只挂在 `_gen_run` 这个唯一出口（app.py:2254），说明在入口算、正文在出口补——**存盘的仍是用户原词**，回填/LRC 对齐/重跑都以原词为准。这个设计比在入口改 payload 干净。
5. RVC 三个 `Form(None)`（app.py:5475-5477）修的是传输层盖掉参数层评审缺省这一整类问题；`rvc_convert_by_rid`（app.py:8407-8409）用 `payload.get("separate_vocal", "auto")` 这种字符串缺省与 multipart 对齐，两个入口口径一致。
6. `models_switch` 把"退回公共 vae"这件事从不可见变成可见（app.py:1700-1737，`vae_from` 进 state 也进用户消息），方向是对的（问题在实现，见 B-1）。
7. `tests/test_submit_paths_agree.py` 把"两条入口一致 / 改了必须留痕"钉成了回归用例（含 `_unified_task` 透传、Form 缺省不许写死数值），这一层的意识是对的。
8. 暴露面这一版**没有变差**：`_guard_local_only`（app.py:253-278）不在本次 diff 内，没有新增 GET 型变更接口（`/api/models/repair` 仍是 POST，app.py:546），`models_switch` 的路径校验是 basename + `relative_to` containment 双保险（app.py:1689/1696）。

下面是仍然站得住的问题。

---

## 一览（按严重度）

| 编号 | 级别 | 一句话 | 主坐标 |
|---|---|---|---|
| B-1 | P1 | ⑬ 报的"当前主模型"是过期声明，本机此刻报的是磁盘上不存在的 `yue2-q4.gguf` | app.py:1733 / 727 |
| B-2 | P1 | `X-Seed` 取值恒为 None，队列产物的"实际种子"全是 -1，无法复现 | app.py:2325 / 3002 |
| B-3 | P1 | `async def` 里阻塞式抢 `_GPU_SEM`，等锁期间整个网关假死（可达小时级） | app.py:3908 |
| B-4 | P1 | ⑪ 的「↻ 重启任务」对单首任务必然 404，且它已先把那首终止掉 | static/index.html:5981 / app.py:3230 |
| B-5 | P2 | 显存降级 CPU 的留痕在 meta 里，却没进统一视图，跑完后页面不再提 | app.py:1412 |
| B-6 | P2 | 排队中的批量条目仍看不到 `cot_note`：前端 mapper 丢字段，后端新增透传对本页是死路 | static/index.html:5839 |
| B-7 | P2 | `_output_write_meta` 非原子，同文件另外两处早改成 `os.replace` | app.py:1936 |
| B-8 | P2 | `history_add` 轮转删除漏了 containment，同文件另外两处都有 | app.py:1625 |
| B-9 | P2 | `_style_yield_to_abc` 能把 style 剥成空串，绕过"style 必填"闸门 | app.py:2446 |
| B-10 | P2 | 送去换声的整曲拷贝在事件循环里裸 `copyfile`，且绕过 507/413 闸门 | app.py:8414 |
| B-11 | P3 | 同一请求在两条路拼出的 `cot_note` 语序不同 | app.py:2516 / 2989 |
| B-12 | P3 | ⑪ 只显示 `cot_note` 前 120 字且不能展开，最后一段被切成半句 | static/index.html:5966 |
| B-13 | P3 | engine_state 无锁读改写 + `system_info` 四次独立读同一份状态 | app.py:379 / 727 |
| B-14 | P3 | 换声 `filter_radius` 偶数→奇数静默改写，没进任何 note | app.py:4326 |
| B-15 | P3 | 批量走字段白名单、单首全透传：白名单外参数在批量入口静默消失 | app.py:3002 / 2580 |
| B-16 | P3 | 空歌词：批量 400、单首放行并占住 GPU 锁后被引擎拒 | app.py:2498 / 2967 |

---

## 缺陷清单

- **B-1 [P1][状态一致性][app.py:1733]** 新增的 `loaded_main/loaded_vae` 报的是"上一次切换成功写入的声明"，不是"现在加载着什么"；**本机此刻就在报一个磁盘上不存在的主模型**。
  - **触发链**：任何让 `cpp/server.json` 与 engine_state 不同步的事后变化都会踩到——① `models_switch` 里 `_write_server_json` 成功、`_restart_audiocpp` 抛异常（Popen 失败/exe 不在位），app.py:1733 的写入整个跳过，server.json 已经是新模型而 state 还是旧的；② `_restart_audiocpp`（app.py:445-474）是 `taskkill` + `Popen` 的**发完不管**，没有 `_wait_backend_ready`、没有核对 `/health`，引擎加载失败（gguf 损坏/显存不足/缺 sidecar）也照样报"已加载"；③ 手工编辑 server.json、`scripts/download_models.py` 重下、看门狗重启网关——这些都不碰 engine_state，而**启动流程里没有任何一处会校正或清空它**；④ 模型文件后来被删/改名，state 里的名字继续报。
  - **一手证据（现场实测，非推断）**：
    - `GET /api/system/info` → `engines.loaded_main = "yue2-q4.gguf"`、`loaded_vae = "yue2-vae-f16.gguf（同目录）"`，同时 `audiocpp_alive: true`；
    - `GET /api/models/current` → `{"path": "yue2-3b-q8_0.gguf"}`；`cpp/server.json` → `models[0].path = "model/yue2-3b-q8_0.gguf"`、`session_options.yue2.model_gguf = "yue2-3b-q8_0.gguf"`；
    - `runtime/data/engine_state.json` → `{"backend":"cuda","loaded_main":"yue2-q4.gguf","loaded_vae":"yue2-vae-f16.gguf","loaded_vae_from":"同目录","loaded_path":"yue2-q4.gguf"}`，mtime 2026-10-16 12:01:52；server.json mtime 2026-10-16 00:11:55（**server.json 被改过，state 里还是更早那次切换留下的声明**；12:01:52 这次写盘来自两个"只改 backend"的读改写入口之一——app.py:621 的 `backend_mode_set` 或 app.py:2346 的降级兜底，按定义它们会把过期的 `loaded_main` 原样带过去）；
    - `find cpp/model -iname "*.gguf"` 只有 `yue2-3b-q8_0.gguf`、`yue2-vae-f16.gguf`、`yue2-q4_k_m/yue2-3b-q4_k_m.gguf`、`yue2-q4_k_m/yue2-vae-f16.gguf`——**`yue2-q4.gguf` 这个文件不存在**；
    - 引擎 `:8080/health` → `{"status":"ok","backend":"cuda","models":1}`，即它按 server.json 载了一套权重，而 ⑬ 页 app.py:727 把另一套（且磁盘上没有的）当"当前主模型"报给用户（static/index.html:8302 的中文标签就是"当前主模型"）。
  - **反例尝试**：(a) 猜 ⑬ 会同时显示两个来源从而自曝矛盾——查 `renderSettings` 只用 `/api/system/info`，`models/current` 在 ⑤ 页只做下拉框回显（static/index.html:5082），**两页各说一套、同一屏内看不出冲突**；(b) 猜 loaded_main 的语义只是"最后一次切换的目标"——代码注释自己写的是"现在到底加载着哪一套权重"（app.py:725），前端标签写的是"当前主模型"，语义就是当前态，未推翻；(c) 猜启动时会清 state 或从 server.json 重建——`_orphan_cleanup_on_startup`（app.py:8725）只改 output meta 的 running 状态，全仓库 `loaded_main` 的写入点只有 app.py:1733 一处（grep 结果），未推翻。**已试图推翻、未推翻。**

- **B-2 [P1][两路不等价 + 留痕为空][app.py:2325]** `seed_actual = headers.get("X-Seed") or payload.get("seed")` 里那个头**从来没被读到过**（httpx 把响应头名统一小写），于是"实际种子"永远只是提交值的回显；单首路径靠入口本地把 -1 具体化成真数而侥幸正确，**批量路径原样透传 -1，结果每一条队列产物的记录里"实际种子"都是 -1，无法复现**。
  - **触发链**：② 页 Seed 框默认就是 `-1`（static/index.html:1985），`numOrNull("seed")` 原样送出 -1（static/index.html:2963）；点「生成歌曲」→ app.py:2565-2567 把非负整数以外的值换成 `secrets.randbelow(2**31)`，引擎收到真数，记录里 `params.seed` 也是真数；点「加入队列」→ app.py:3002 `"seed": t.get("seed")` 存 -1，worker 的 params 白名单（app.py:2884-2888）把 -1 原样写进产物 meta 并原样送引擎；`_gen_run` 想从 `X-Seed` 补回真值，但那句取值恒为 None（见下方一手证据）。
  - **一手证据**：
    - 实测（本机 vendored `py312/python.exe`，httpx 0.28.1）：`dict(httpx.get('http://127.0.0.1:7863/api/system/info').headers).keys()` = `['date','server','content-length','content-type','x-content-type-options','referrer-policy','content-security-policy']`；`h.get('X-Content-Type-Options')` → **None**，`h.get('x-content-type-options')` → `nosniff`。被小写化的是 **Starlette/uvicorn 发的混合大小写头**，与引擎发 `X-Seed` 完全同一套栈（`X-Seed` 字面量确实在 `main.cp312-win_amd64.pyd` 字符串表里，实测 `grep -c` 命中 1 次）。`_gen_run_once` 返回的是 `dict(r.headers)`（app.py:2136），普通 dict 区分大小写。
    - 现场数据（133 份生成类 meta 交叉表 `(batch, params.seed==-1, seed_actual==params.seed)`）：`(True, True, True) 69`、`(False, True, True) 12`、`(False, False, True) 43`、`(True, False, True) 6`——**`seed_actual` 与提交值不相等的只有 3 条，且都是 cancelled/error（没进成功分支）**。也就是说：全部成功产物里，`seed_actual` 恒等于提交值，头一次都没起作用；其中 81 条把 -1 当"实际用的种子"存了下来。
    - 前端把这个字段当"真实种子"展示：`applyFinishedJob` → `"生成完成 · … · seed=" + job.seed_actual`（static/index.html:5309）；而 app.py:2565 上方的注释（以及 static/index.html:5416-5421）写明了具体化 -1 的目的就是"历史记录存下引擎实际用的种子，回填时才能复现同一结果"——**批量这条路这个目的没达到**。
  - **反例尝试**：(a) 猜引擎根本不回 `X-Seed`，那就不算 app 的错——即便如此，`seed_actual` 仍是回显，队列产物照样无法复现，结论不变；(b) 猜 `-1` 在两条路都被同一处理——静态比对 app.py:2565 与 3002，只有单首做具体化，未推翻；(c) 猜 3 条 `seed_actual != params.seed` 是头生效的证据——逐条打开看是 cancelled/error（走的是 `job.update(status=...)` 分支，压根没执行那行），**不构成反例**。另注：上一轮审查文档 `docs/review/BLUE-TEAM-REVIEW-308f833-G4.md:34` 写过"`X-Seed` 头 95/96 meta 实测有效"，那个判据正是拿 `seed_actual == params.seed` 比的——单首路径两边本来就相等，**该结论被本文的实测推翻**。

- **B-3 [P1][async 里做重活 / 全网关假死][app.py:3908]** `async def rvc_pitch_advice` 在事件循环线程里直接 `with _GPU_SEM:` 抢全局 GPU 串行闸门；闸门的持有者是"整条连发链/整个训练/整条队列"，等待没有超时——**引擎体检一次可以把整个网关钉住几十分钟到几小时**。
  - **触发链**：⑨ 页音域体检勾上"先做人声分离"（`separate=1`）→ 进入 app.py:3908 的 `with _GPU_SEM:`（该 async 端点起于 app.py:3880）→ 此刻只要有一首生成在跑（`_chain_runner` 是 `with _GPU_SEM:` 包住整个 for 循环，app.py:2575，N 首 × 本机实测约 90 分钟/首），或有一个音色训练在跑（app.py:7372 `acquire()` 到 app.py:7669 才 release，整条训练流程持锁），或批量队列还有 pending（批量 worker 在 app.py:2856 持同一把锁）——**事件循环线程就地阻塞**，此后 `/api/health`、所有状态轮询、所有页面全部无响应，直到锁被释放。这是本项目已写进备忘的硬红线（app.py:637-638 那段注释自己就在解释"写成 async def 会把整个网关钉住（3081 假死）"）。
  - **一手证据**：全文件 `_GPU_SEM` 使用点实测清点——app.py:2575（`_chain_runner`，线程内 ✓）、2856（`_batch_run_worker`，线程内 ✓）、5119/5167（RVC 队列循环，线程内 ✓）、5799（`_sep_worker`，线程内 ✓）、7372/7669（训练 worker，线程内 ✓）、5925（`acquire(blocking=False)` 探一眼就放 ✓ 且注释专门警告不许吃许可）；**只有 3908 这一处在 `async def`（app.py:3880 起）里阻塞式抢锁**。同函数里作者对真正的重活是守规矩的（app.py:3909-3910 `_run_vocal_separation`、app.py:3914 `_rvc_f0_of_audio` 都走 `run_in_threadpool`），唯独漏了这把锁；顺带 app.py:3915 的 `tgt = _rvc_f0_stats(model)` 也在事件循环里同步跑（缓存未命中时要遍历 `logs/<stem>/2b-f0nsf/*.npy` 并做 numpy 统计，app.py:3463 起），量级远小于锁，但同一类。
  - **反例尝试**：(a) 猜 FastAPI 会把 async 路由丢线程池——不会，只有普通 `def` 路由才进线程池（本文件的 `system_info`、`batch_start`、`generate_stop` 都是普通 def，注释 app.py:637-638 还专门解释过为什么必须这么写），`async def` 一律在事件循环上跑；(b) 猜 `_GPU_SEM` 平时是空的、抢不到也只等几秒——持有者定义就是"整条链/整条训练"（见上列代码位置），6GB 卡上一首 90 分钟是产品自己的实测口径（app.py:2856 那行注释与 ⑤ 页文案），未推翻；(c) 猜这条路径只有 API 直连才走——`separate` 是 ⑨ 页面上的勾选项（本文不改代码，未实测点击，但 Form 参数与前端控件同名，标 **触发点为 UI 可见选项**）。**看门狗会不会因为 `/health` 长时间无应答而击杀网关属推断，未证实**（没查看门狗判据），但"整网关假死直到前一个任务算完"这一条只需静态成立。

- **B-4 [P1][契约不匹配 + 一用就毁][static/index.html:5981]** ⑪ 任务管理给**所有** running 行渲染「↻ 重启任务」，而它唯一的动作是"先终止当前这条、再调只认批量队列的 `/api/batch/retry`"——对单首生成（非队列）任务**必然 404**，于是每次点击都是"亲手掐掉正在算的那一首，然后告诉你重启失败"。
  - **触发链**：② 页点「生成歌曲」（这条路径永不写批量状态文件）→ ⑪ 卡片显示 running（数据来自 `/api/generate/list` 的 running meta，app.py:2639 不过滤状态）→ 用户看着进度条点「↻ 重启任务」→ `POST /api/generate/stop?scope=current`（app.py:2371）把 job 标 cancelled、`_gen_set_job(None)`、置 `_STOP_ONE_EVENT`、**强杀引擎进程**（app.py:2399 → `_kill_audiocpp_now`）→ 紧接着 `POST /api/batch/retry {id}` → app.py:3228 `it = next(...)` 在批量状态里找不到这个 id → 404「任务不存在」→ 前端抛错只 toast 一句，已经中断的那首**没有任何重跑路径**（连发的后续 take 也被 `_STOP_ONE_EVENT` 停掉，app.py:2578）。
  - **一手证据**：按钮渲染条件（static/index.html:5981）`${h.status === "running" && !isRvc && !isTrain ? '<button …data-stop>⏹ 终止</button><button …data-restart>↻ 重启任务</button>' : ''}`——**没有 `isQueue` 判定**；点击处理（static/index.html:6054-6066）注释写明"先终止当前执行…再把该条目复位为 pending 重新入队"，调的就是 `/api/batch/retry`；`batch_retry`（app.py:3223-3230）只读 `_BATCH_STATE`，找不到即 404。两条路的 id 空间互不相通：单首 id 来自 `_make_job`→`_new_id()`，只进 output meta 与内存 `_GEN_JOB`（app.py:2531、2587-2589），批量条目才进 `_BATCH_STATE.items`（app.py:2992）。
  - **反例尝试**：(a) 猜单首连发的 job 也会被登记进批量状态——`_make_job`/`_chain_runner` 全文没有任何 `_batch_write`/`_batch_store` 调用（grep 核对写入点只有 batch_start/resume/retry/stop/worker 五处），未推翻；(b) 猜队列里跑着的行才显示这个按钮——条件里只有 `status/kind`，`isQueue` 只用于别的渲染分支，未推翻；(c) 猜 stop 之后 retry 失败也不伤数据——伤的是"正在算的这一次"（本机 90 分钟的算力 + 引擎被强杀），音频从未落盘，**这就是"一用就错"**。级别按 P1 记（特定路径必错）；按"点了就废掉一次生成"也可辩为 P0，留给裁定员。

- **B-5 [P2][留痕缺失][app.py:1412]** 显存不足自动降级 CPU 这件事，**meta 里有、统一视图没有、任务跑完之后页面上也不再提**——一条慢 5-10 倍、峰值音质都不同的 CPU 成品，在 ⑪ 与队列卡上与 GPU 成品完全同相。
  - **触发链**：生成前显存预检不足（app.py:2277-2284）或引擎报显存类错误（app.py:2304-2311）→ `job.update(cpu_fallback=True, gpu_error=…, note="显存不足，已自动切换 CPU 后端…")` 并 `_output_write_meta(job)` → 任务正常完成 → ⑪/队列卡按 `_unified_task` 白名单取字段，白名单里没有 `note`/`cpu_fallback`/`gpu_error` → 前端模板也不读这三个键 → 只剩 ⑤ 页"正在生成时"那一行进度文案（static/index.html:5275）会提一次，刷新后/完成后无处可查。
  - **一手证据**：写入点 app.py:2279-2284、2306-2311（键名 `cpu_fallback`/`gpu_error`/`note`）；透传白名单 app.py:1412-1414 全文为 `("sec","queue_pos","assets","file","model","name_cn","epoch","epochs_total","src_name","bytes","strip_harmony","cot_note")`；实测 `GET /api/tasks/unified?limit=2000` 的 184 条里，键并集为 `[assets, bytes, error, file, id, kind, model, name, progress, queue_pos, sec, src_name, status, step, strip_harmony, ts]`，**`note`/`cpu_fallback` 命中数 0**；前端 `grep -n "cpu_fallback\|gpu_error" static/index.html` 只有 5275 一处（⑤ 页进度文案），⑪ 的行模板（static/index.html:5966 附近）不读。这正是本次发版给 `cot_note` 补的那一类断口，只是补在了 note 上、漏在了降级上。
  - **反例尝试**：(a) 猜 ⑪ 的行数据来自 `/api/generate/list` 的全字段 meta，所以 note 字段自然带上——确实带上（`generate_list` 原样吐 meta，app.py:2639-2646），但**行模板不打印 `h.note`**，字段在 JSON 里不等于在页面上；(b) 猜现场没人遇到过降级——`grep -c cpu_fallback runtime/output/*.json` 实测 163 份 meta 里 **0 份**带该键，说明触发条件确实受限（引擎自带显存调度 + `_audiocpp_cuda_ready` 会跳过预检），据此定 **P2 而不是 P1**；(c) 猜 app.py:2346 那句兜底切回 CUDA 会写进任务记录——它只写 engine_state，不写 job，事后同样查不到这次到底用哪种后端跑完的。

- **B-6 [P2][链路只修了一半][static/index.html:5846]** `cot_note` 的三处后端透传都做了，但 ⑪ 页**排队中/刚提交**那一段仍然看不见它：前端把 `/api/batch/status` 的条目重新映射成行对象时丢了这个字段，而后端为 ⑪ 加的 `_unified_task` 透传对本页是一条死路。
  - **触发链**：② 页「加入队列」提交 5 首（本机每首约 90 分钟）→ 第 1 首开跑后写产物 meta，于是第 1 首的行能从 `/api/generate/list` 拿到 `cot_note` 并显示（static/index.html:5966）→ 第 2~5 首只有队列状态文件里的条目，走 `fetchExtraTasks` 的 batch 分支（static/index.html:5812 起）→ 该分支手工挑字段拼行对象（static/index.html:5839-5847：`id/kind/status/ts/task_name/style/lyrics/cot/abc/params/error/sec/bytes/isQueue`），**`it.cot_note` 就在原地但没被带上** → 排队几小时里用户看不到"这一首当时被换了档位/剥了 BPM/补了 [Outro]"。
  - **一手证据**：后端确实发了——`batch_start` 把 note 存进条目（app.py:2997 `"cot_note": cot_note`），`batch_status` 原样返回 `state["items"]`（app.py:3123），`_unified_task` 白名单也收了（app.py:1414）；前端确实没接——`grep -n cot_note static/index.html` 全文只有三处：5525（toast，3.6 秒）、5567（toast，3.6 秒）、5966（行渲染）；另外 ⑪ 页拿 `/api/tasks/unified` 只取 `kind === "score" || kind === "separate"`（static/index.html:5875-5878），**generate 类根本不走这个源**，所以本次新加的 `cot_note` 透传对 ⑪ 是死代码，只对第三方消费者有意义。
  - **反例尝试**：(a) 猜任务开跑后 meta 立刻带上 note，排队窗口很短——本机批量是串行 90 分钟/首，第 5 首要等 6 小时，窗口就是产品的常态；(b) 猜 `tests/test_submit_paths_agree.py:85` 那条 `test_frontend_shows_the_note_in_history` 会拦住这个漏——它只断言 `assertIn("h.cot_note", html)`，一个字符串包含判定，**测不到 extra 分支丢字段**（这条用例本身给 P3 级建议：改成对 fetchExtraTasks 两个 mapper 的字段清单做断言）；(c) 已试图推翻、未推翻。

- **B-7 [P2][持久化原子性][app.py:1936]** `_output_write_meta` 是"截断再写"的普通 `write_text`，而同一个文件里 `_batch_write`、`_rvc_train_write` 都因为同类问题改成了"临时文件 + `os.replace`"；产物 meta 是**被轮询读得最多、又被反复重写**的那一份。
  - **触发链**：任务开始/降级/完成各写一次（app.py:2284、2311、2335），`generate_stop` 也写（app.py:2393），启动清扫逐条重写（app.py:8740、2628）；与此同时 ⑤/⑨/⑩/⑪ 各页每 5 秒轮询 `tasks_unified`/`generate_list`/`history_active`，三者都经 `_output_read_meta`（app.py:1926-1934，`except Exception: return None`）。落在写窗口里的读 → 返回 None → `generate_list` 直接 `if m:` 跳过（app.py:2646）、`tasks_unified` `if not m: continue`（app.py:1441）→ **running 卡片整行消失一个轮询周期**；若进程在 `write_text` 中途被看门狗杀掉（本项目有看门狗），meta 永久半截 → 这条任务在 ⑪/历史里从此不存在（style/lyrics/cot/params/cot_note 全丢），而 `.wav` 还在盘上占地方，`_output_extra_assets`/`_output_purge` 又靠 meta 登记产物名，删除时留孤儿。
  - **一手证据**：对照同一文件里已修好的两个写法——app.py:2804-2805（注释直接写"蓝军 N-2：写一半被杀 = JSON 损坏 = 下次 _batch_read 吞异常返回空 = 整个队列历史静默清零。临时文件 + os.replace 才是原子发布"）与 app.py:6549-6551（"原子写（裁定 C-4）"）；`_output_write_meta`（app.py:1936-1940）仍是 `(_output_meta_path(meta["id"])).write_text(...)`。`_hist_write`（app.py:1297）、`_engine_state_write`（app.py:379）、`_score_save`（app.py:803）同病。
  - **反例尝试**：(a) 现场扫 163 份 meta，**0 份解析失败**——说明"已经坏掉"没有证据，本文不据此拔高级别，只按"窗口必然存在 + 同文件已承认该风险并已修两处"定 **P2**；(b) 猜 Windows 的共享模式会让并发读写根本读不到半截——CPython 在 Windows 上以 `SH_DENYNO` 打开文件，读方确实能拿到截断中的内容，反例不成立；(c) 猜 `_batch_run_worker` 末尾 `final = _output_read_meta(rid) or job`（app.py:2918）会被拖死 → 那里的 `job` 是 `_gen_run` 就地 update 过的同一个字典对象，兜底有效，**这一处我原本当缺陷、复核后撤回**，只保留"done 标记依赖同一对象、meta 读失败时 batch 条目仍能正确标 done"这一事实。

- **B-8 [P2][暴露面纵深][app.py:1625]** `history_add` 的超额轮转直接 `unlink(HIST_DIR / gone["file"])`，**没有 basename、没有 containment**；同一个文件里 `history_clear`/`history_delete` 两处都按"审计 S-3"补了双保险，只有这一条漏了。
  - **触发链**：`records/history.json` 里某条记录的 `file` 字段被写成 `"..\\..\\..\\target.wav"`、绝对路径或 `C:\\...`（手改、外部工具写坏、老版本遗留都算）→ 用户下一次入库满 HIST_KEEP → 第 1624 行 `for gone in items[HIST_KEEP:]: (HIST_DIR / gone.get("file","")).unlink(missing_ok=True)` → `Path("…/records") / "C:\\x\\y"` 在 Windows 上直接得到 `C:\x\y`（pathlib 遇绝对路径/带盘符会替换整个路径），`..` 也照样穿透 → **删掉历史目录之外的任意文件**。
  - **一手证据**：漏检的写法和已加固的写法并排可见——app.py:1625（无校验）对比 app.py:1646-1651 与 1664-1669，两处都有 `fn = os.path.basename(str(i.get("file","")).replace("\\","/"))` + `fp.parent == HIST_DIR.resolve()` + 注释"审计 S-3：history.json 可被篡改，file 字段需 containment 校验，防删任意文件"。产品自己的威胁模型已经承认 history.json 可被篡改，因此这是**同一标准的执行漏项**而不是新主张。
  - **反例尝试**：(a) 猜 `file` 字段只能由服务端生成、外部写不进来——`history_add` 自己确实只写 `rid + ".wav"`（app.py:1607），但 `_hist_read`/`_hist_write` 是全量读-改-写，任何一条既有记录被改动就进入轮转分支；(b) 猜有 `_guard_local_only` 挡着篡改来源——该中间件只校验 Host/Origin（app.py:253-278），不校验文件内容，且 history.json 本身是本机可写文件；(c) 定级 P2 而非 P1：触发需要本机文件被改过（不是"特定路径必错"），但危害是越目录删除，**已试图推翻、未推翻**。附带同类漏项：`_sep_out_dir`（app.py:5689-5727）与 `_output_purge`（app.py:1798）、`_server_audio_path`（app.py:1970 起）都有 basename+containment/字符集双闸门，**这几处是合格的**，本文不报。

- **B-9 [P2][fallback 兜底过头][app.py:2446]** `_style_yield_to_abc` 把 style 里的 BPM/拍号剥干净之后，**不检查剥完还剩不剩东西**，于是一整条"style 必填"的闸门可以在送引擎前被自己掏空。
  - **触发链**：用户在 ⑤/② 只把 style 写成节奏（例如装配台产物退化成 `"88 BPM"`、或用户就填了 `"120 BPM 4/4"`），乐谱框里带 `Q:1/4=88`/`M:4/4` → `generate_start`（app.py:2497-2500）先验过 style 非空、`_limit_text` 也过 → `_style_yield_to_abc` 命中两条正则、`eff = re.sub(r"[,\s]+$","",…).strip()` 得到 **空串** → `payload["style"] = ""` 送引擎，`_make_job` 里 `"style": style[:_MAX_STYLE]` 也存成空串（app.py:2546），title 退化成 rid（app.py:2319）。批量入口同样（app.py:2989 + 2999）。
  - **一手证据（d08c518 副本纯函数实跑）**：输入 `style="88 BPM"`、`abc="X:1\nM:4/4\nQ:1/4=88\nK:C\nC2 D2 E4 |\n"` → `_resolve_cot` 出 melody、`_style_yield_to_abc` 返回 `('', …BPM 已让位…)`；**两条路径都得到空 style**（`SAME_STYLE: True`，style=`''`），说明这不是两路不一致，而是两路一起把 style 掏空。
  - **反例尝试**：(a) 猜引擎对空 style 有兜底 → 引擎在二进制里，我只证实 `main…pyd` 字符串表里有 `lyrics is required`，**style 为空的行为未证实**，故本条只主张"闸门被绕过 + 记录里存了空 style"这一可静态确认的事实；(b) 猜 note 已经说明了这件事 → note 只说"BPM 已让位给乐谱的 88 BPM"，不说"你的 style 已经空了"；(c) 触发需要 style 里只有节奏信息，条件受限 → **P2**。

- **B-10 [P2][async 里做重活 + 闸门旁路][app.py:8414]** 同一件"从服务器素材库拷一首整曲当换声源"的事，两个入口是两套实现：multipart 入口用带 507/413 闸门且 `run_in_threadpool` 的 `_server_audio_copy`，`POST /api/rvc/convert/{rid}` 入口用事件循环里的裸 `shutil.copyfile`，且**不查磁盘剩余、不计入单次请求总量上限**。
  - **触发链**：⑪/历史页点「🎙 送去换声」→ `rvc_convert_by_rid`（async def，app.py:8381）→ app.py:8414 `shutil.copyfile(src_wav, src)` 在事件循环里复制一首 wav（本机产物口径：44.1k/16bit/立体声 5 分钟约 50MB，长曲上百 MB）→ 复制期间所有轮询停摆；同时这次复制不经过 `_server_audio_copy` 里的 `_free_bytes(dst.parent) < _DISK_FLOOR_BYTES` → 507 与 `_MAX_UPLOAD_TOTAL` → 413 两道闸门（app.py:2016-2055 的注释自己写着"放在事件循环里会把整个网关冻住""服务器复制与上传走同一道闸门"），磁盘快满时照样收。
  - **一手证据**：`_server_audio_copy`（app.py:2016-2055，注释自己写着"放在事件循环里会把整个网关冻住""服务器复制与上传走同一道闸门"）与 `shutil.copyfile`（app.py:8414）的差集；`rvc_convert` 走的是 `await _server_audio_copy(server_src, src)`（app.py:5533）。两个入口对 `separate_vocal/gate/strip_harmony/_rvc_convert_params` 的处理是一致的（app.py:8403-8409 与 5516-5519），差的就是拷贝这一段；`rvc_convert_by_rid` 登记的 `src_size` 也证明它拷的是整曲（app.py:8431）。
  - **反例尝试**：(a) 猜源文件一定很小 → 生成产物按产品口径是整曲，且 `src_size` 会进任务卡（app.py:8431），未推翻；(b) 猜 `sf.info` 那一行才是重活 → `sf.info` 只读头，确实便宜，但 `copyfile` 不便宜；(c) 触发需要用户先有一首已生成产物再点送去换声，且磁盘/负载决定痛感 → **P2**。

- **B-11 [P3][两路不等价][app.py:2513]** 同一条请求在两条入口得到的 **`cot_note` 文本顺序不同**：单首是"档位→节奏→收尾"，批量是"档位→收尾→节奏"；两者都截到 400 字，因此**同输入在两条路存下不同的字符串**。
  - **触发链 + 一手证据（d08c518 副本纯函数实跑，复刻两条入口的拼接顺序）**：四组用例（`bogus`+带和弦谱、`off`+无和弦谱、空 cot、`full`）跑下来 `SAME_COT: True`、`SAME_STYLE: True`、`len(note)` 两边相同（135/134/131/131），但 **`SAME_ORDER: False` 四例全中**；对应代码顺序——单首 `_ensure_song_ending` 在前但**拼接在末**（app.py:2507 算 ending、2513 算 tempo、2516-2520 先 tempo 后 ending），批量 ending 先拼、tempo 后拼（app.py:2983-2991）。
  - **影响**：⑪ 只显示前 120 字（B-12），两条路保留下来的那一段不一样；跨入口对账（"这两首参数一模一样为什么记录不同"）会多出一种假差异。
  - **反例尝试**：(a) 猜只有一条路能同时产生三段 note → 实测四例都产生三段，未推翻；(b) 猜批量白名单里存的是原始 note 未截断（app.py:2997 无 `[:400]`）而 meta 截 400（app.py:2894）——这条也确实成立，但实测最长 135 字，**长度差异当前无实害**，故并入本条不单列；级别 P3（工程一致性）。

- **B-12 [P3][留痕读不全][static/index.html:5966]** ⑪ 行内把 `cot_note` 硬切成前 120 字，且**没有像 error 那样的"点击展开全部"**，`title` 属性给的是固定说明文字而不是全文。
  - **一手证据**：`${h.cot_note ? `<div class="h-sub" … title="服务端在送引擎前对你提交的内容做的调整（…）">🛈 ${escapeHtml(String(h.cot_note).slice(0, 120))}</div>` : ""}`；对比同一行块里 error 的处理 `… …${String(h.error).length > 120 ? " …（点击查看全部）" : ""}` + `data-err` 展开（app 端 `_unified_task` 也截 500，前端仍给了展开）。实测三改写叠加时 note 长 131-135 字，**被切掉的正好是最后一项（补 [Outro] 那句）**，页面停在一个读不完的半句上。
  - **反例尝试**：(a) 猜 400 字截断才是主风险 → 实测最长 135 字（最坏组合也远在 400 内），**这一条我不成立，主动撤回**；真正会读到截断的是前端这 120 字；(b) 单改写场景（最常见）note 只有 30-70 字，不受影响 → 定 P3。

- **B-13 [P3][并发写同一状态文件][app.py:379]** `_engine_state_write` 是无锁的 read-modify-write + 非原子 `write_text`，而 `system_info` 在同一次响应里**分四次**读这份文件；两侧都可能拿到拼接错位的组合。
  - **一手证据**：app.py:375-383（`state = _engine_state_read(); state.update(updates); _ENGINE_STATE.write_text(...)`，没有任何锁）；读方 app.py:727-730 连着四次 `_engine_state_read()`——`loaded_main` 一次、`loaded_vae` 三次（判空 + 取 `loaded_vae_from`）。并发场景：`models_switch`（app.py:1733）正在写，而 `_gen_run` 的收尾兜底（app.py:2346）也在写 → 后写的把先写的整份覆盖（`backend` 或 `loaded_*` 整族丢一半）；`system_info` 的四次读跨在写入窗口两侧 → 一次响应里"主模型来自 A、vae 来自 B"。写盘中途被杀 → `_engine_state_read` 吞异常返回 `{}`（app.py:368-373），后果是 `backend_mode()` 静默按 "cuda" 办（app.py:386）、⑬ 的"当前主模型"变空。
  - **反例尝试**：(a) 猜 engine_state 只有一个写入者 → 实测三处（621/1733/2346），其中 2346 在生成线程、621 在 HTTP 线程、1733 在 HTTP 线程，未推翻；(b) 猜 `system_info` 会缓存这份 state → 没有缓存，四次独立读；(c) 实测量级小（166 字节），触发窗口窄，**P3**。

- **B-14 [P3][静默改写没留痕][app.py:4326]** 换声的"音高平滑半径"偶数被悄悄加 1（4→5、6→7），**没有进任何 note**；同一个文件在 4282 行自己立的规矩是"不许悄悄降级：那是'点 A 得 B'"。
  - **一手证据**：app.py:4322-4326（注释写着"页面是 step=1 的 number 输入，4、6 随手能填且能过闸门，所以在这里钳到下一个奇数"）→ `"filter_radius": filter_radius` 进 job（app.py:5552）只存最终值，`rvc_convert` 的响应/任务卡都不说"你填的 4 被改成 5"；对比 app.py:4275-4285 的 fcpe 分支明确拒绝悄悄降级成 rmvpe 并在提交时报错。
  - **触发条件与级别**：页面滑杆/数字框 step=1，填偶数完全可能；对听感的影响是平滑核变化一档，**实害很小且不改档位语义** → P3（工程一致性：同类改写一个报错、一个静默）。
  - **反例尝试**：(a) 猜 `filter_radius` 也在 `cot_note` 体系里 → 该体系只覆盖生成入口的三处改写，换声完全不走（换声没有 note 字段，`_unified_task` 里换声条目也不带）；(b) 猜偶数会被 400 拦下 → 注释明说"能过闸门"，故只钳不改报；未推翻。

- **B-15 [P3][两路不等价 / 契约][app.py:3002]** 批量入口用**字段白名单**重拼送引擎的 payload，单首入口把**客户端整个 payload**（仅去 `count`）原样转发；白名单之外的一切参数在批量这一路静默丢弃，既不报错也不留痕。
  - **一手证据**：批量——app.py:2998-3006 显式列举 `lyrics/style/cot/abc/model/seed/cfg_scale/num_inference_steps/abc_temperature/abc_top_p/abc_top_k/semantic_temperature/semantic_top_p/semantic_top_k`，**其余键被丢弃**；单首——app.py:2580-2581 `p = dict(payload)` 只 `p.pop("count", None)`，其余全转发给 `/api/music/generate`（app.py:2130）。引擎侧字段面（只读扫 `main.cp312-win_amd64.pyd` 字符串表）里还可见 `voice`、`abc_file`、`abc_text`、`abc_max_tokens`、`abc_min_tokens`、`abc_penalty_window`、`abc_repetition_penalty`、`semantic_max_tokens`、`semantic_min_tokens`、`semantic_penalty_window`、`semantic_repetition_penalty` 等键名。
  - **诚实标注**：**这些键名哪些真是 `/api/music/generate` 的请求字段，我未证实**（不启动 GPU 任务就看不到它的 pydantic 模式），因此不据此报"某个具体参数会被丢"。本文只主张可静态确认的那一半：**两条入口对未知参数的处理方式相反**（一路生效、一路丢弃），且丢弃既无 400 也无 note。前端目前不发送白名单外的参数（static/index.html:5502-5512 与 5555-5560 字段集相同，仅单首多 `count`/`task_name`），所以 UI 用户暂时踩不到 → **P3**。
  - **反例尝试**：(a) 猜单首也会丢 → 没有丢；(b) 猜批量白名单是刻意的安全收口 → 有可能（`task_name`/`name` 分离确实是有意的），但收口应当**报错或在 note 里说明**，而不是静默；建议裁定为"要么两路同白名单，要么两路同透传"。

- **B-16 [P3][两路不等价][app.py:2498]** 空歌词在两条入口的待遇相反：批量整批 400（app.py:2967 `if not style or not lyrics`），单首只要求 style（app.py:2498），空歌词会被照单收下并占住 GPU 闸门，直到引擎把它判掉。
  - **一手证据**：单首路径对 lyrics 只做 `_limit_text`（app.py:2500）与 `_ensure_song_ending`（空词按设计不补，app.py:2487-2488，且有测试 `test_empty_lyrics_gets_no_fake_ending` 钉住"不许替空词编内容"）→ 空串原样进 payload；而 app.py:2488 那行注释自己写着"空歌词不补（**校验层会拦**，这里不该替它编内容）"，单首入口实际没有那层拦截；引擎字符串表里确实有 `lyrics is required`，说明它会在推理侧拒。前端两条路都先挡了空词（static/index.html:5496 与 5543），所以 UI 用户踩不到，**API 直连/旧数据复用才踩得到**。
  - **影响**：一次注定失败的提交会先排队等 GPU 锁（app.py:2575）再失败，把队列里后面的条目一并推后 → 实害小、条件受限 → **P3**（修法与批量对齐：入口直接 400，别占锁）。
  - **反例尝试**：(b) 猜"纯器乐"是合法用法、批量那条 `not lyrics` 才是 bug——产品口径不支持（引擎有 `lyrics is required`），且 ② 页文案把歌词列为必需项；两边应统一，无论统一到哪一侧。

---

## 未成立 / 已主动撤回的怀疑（留给裁定员核对）

1. **`cot_note` 截 400 字会截掉关键信息** — 不成立。以 d08c518 副本实跑 `_resolve_cot` + `_style_yield_to_abc` + `_ensure_song_ending` 的最坏组合（无法识别值 + 按谱换档 + BPM/拍号双剥 + 补收尾段），note 最长 **135 字**，离 400 还有三倍余量。真正会截的是前端那 120 字（B-12）。
2. **批量条目跑完写 meta 时 `cot_note` 断链** — 不成立，本次已修好（app.py:2894），我按"链已接上"处理。
3. **`_batch_run_worker` 的 `final = _output_read_meta(rid) or job` 会在 meta 读到半截时把成功标成失败** — 不成立。`job` 就是 `_gen_run` 就地 `update` 过的同一个对象，兜底有效。
4. **`_resolve_cot` 对空值仍静默** — 属于已裁定的行为（app.py:894-903 的注释 + `test_empty_cot_stays_quiet`）：前端两条路都显式带 `cot`，空值只可能来自直接调 API；且空值→full 与两条入口的缺省一致，不构成"改了用户的选择"。不报。
5. **① 连发首数在「加入队列」被忽略（batch 不读 `count`）** — 不构成缺陷：② 页把两件事写在同一行摘要上了（static/index.html:7889-7896 `"连发 " + n + " 首 · 队列走后台"`），且按钮文案随 n 变成「生成歌曲 ×N」，用户看得见。
6. **无 body 的 POST/DELETE 是简单请求、`_guard_local_only` 只校验 Host 不校验 Origin** — 这条既有限制**本版没有恶化**：中间件与 `_WRITE_METHODS` 不在本次 diff 内；本次没有新增 GET 型变更接口（扫过全部 `@router.get` 名字，只有 status/list/audio/export 类）；`/api/models/repair`（下载 + 改盘）仍是 POST。唯一 GET 写盘是 `GET /api/generate/lyrics/{rid}` 的"txt 不在就现生成一份"（app.py:2672），属幂等缓存填充，不当缺陷报。

---

## 附：本次实测用到的只读命令（复核可按原样重跑）

```bash
# 只读副本
git -C /e/AI/10AIMusic/Yue/yuE-2/yuE-2 show d08c518:app.py > /tmp/rev/app.py
# 三条只读 GET（网关已在跑，未启动任何 GPU 任务）
curl -s http://127.0.0.1:7863/api/system/info      # → engines.loaded_main = "yue2-q4.gguf"
curl -s http://127.0.0.1:7863/api/models/current   # → {"path":"yue2-3b-q8_0.gguf"}
curl -s "http://127.0.0.1:7863/api/tasks/unified?limit=2000"   # → 184 条，note/cpu_fallback/cot 键数均为 0
# 头名大小写（B-2 的关键判据，用仓库自带解释器）
./py312/python.exe -c "import httpx;h=dict(httpx.get('http://127.0.0.1:7863/api/system/info',timeout=30).headers);print(list(h));print(h.get('X-Content-Type-Options'), h.get('x-content-type-options'))"
# 产物 meta 交叉表（B-2/B-5 的现场数据）：133 份生成类，batch&seed==-1&seed_actual==seed 组合计数
# 引擎字段面（只读字符串表）：./py312/python.exe -c "d=open('main.cp312-win_amd64.pyd','rb').read();print(d.count(b'X-Seed'))"
```

临时脚本与只读副本曾放在 `%TEMP%\qoder\rev\`（`an.py`/`probe.py`/`seed2.py`/`seed3.py`/`ln*.py`/`vf*.py` + 两份 d08c518 导出），**审查结束已全部删除**；产品树内除本报告外未新增或修改任何文件（`git status` 里的 `app.py`/`src/abc_edit.py`/`static/index.html`/`tests/test_abc_edit.py` 四处改动是另一轮会话的在制品，本次未触碰、未据其定任何缺陷）。
