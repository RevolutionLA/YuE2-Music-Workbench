# THIRD-PARTY-REVIEW — 蓝军 G2 报告逐条复核（基线 d08c518）

审计身份：第三方独立审计员（中立）。被审对象：`docs/review/BLUE-TEAM-REVIEW-d08c518-G2.md` 的 B-1…B-16 十六条结论，以及文末六条"主动撤回/不报"。

**审计方式**：静态代码复核（只读副本）＋ 只读接口实测 ＋ `data/`、`models/`、`engine_state.json`、`runtime/output/*.json` 产物 meta 扫描 ＋ 纯函数离线复跑 ＋ 看门狗日志与实测计时。**未修改主工作树任何文件、未重启网关、未启动任何 GPU 任务、未调用任何写库/推理接口**（只跑了 `/api/system/info`、`/api/tasks/unified`、`/api/history` 三个只读 GET）。

**坐标约定（重要）**：本文所有行号默认指向只读副本 `C:\Users\59221\AppData\Local\Temp\qoder\rev3b\app.py`（`git show d08c518:app.py`，8892 行）与同目录 `index.html`（9338 行，`git show d08c518:static/index.html`）。蓝军用的是**工作树**行号：`git diff -U0 d08c518 -- app.py` 的 hunk 头实测为 `@@ -790 +790,2 @@`、`@@ -794 +795,2 @@`、`@@ -798,0 +801,2 @@`、`@@ -1062 +1066 @@`、`@@ -1164,1169 @@`，即**在制品从 d08c518 第 801 行之后累计插入 5 行**（正是备忘里说的"脏树行号与标签行号偏约 5 行"）。核对结论：

- 蓝军引用的行号**没有一条落在在制品 hunk 内**（`_score_save`/`abc_edit`/`scores_list` 的 title 三处），它确实避开了；
- 但蓝军没有声明偏移，导致 **801 行之后的每个坐标对 d08c518 都大 5 行**（例：蓝军 app.py:1412 白名单 → 副本实际 1407；app.py:1733 写 state → 副本 1728；app.py:2325 读头 → 副本 2320；app.py:3908 抢锁 → 副本 3903；app.py:8414 copyfile → 副本 8409）；
- `index.html` 只有两处单行替换（5733、7990），**不产生偏移**，蓝军的前端行号与副本一致。

---

## 一、总判

| 判定 | 条数 | 编号 |
|---|---|---|
| **成立** | 15 | B-1、B-2、B-3、B-4、B-5、B-6、B-7、B-8、B-10、B-11、B-12、B-13、B-14、B-15、B-16 |
| **部分成立** | 1 | B-9（机制静态为真，"绕过 style 必填闸门"的定性偏重） |
| **不成立** | 0 | — |
| 需人工（听感/引擎内部行为） | 0 | 但 B-2/B-9/B-16 的"引擎对空值/种子如何反应"三方都拿不到，只能静态定，蓝军已如实标注 |

**级别调整（与蓝军不同处，理由见各条）**：B-5 P2→**P3**；B-8 P2→**P3**；B-9 P2→**P3**（并改为"部分成立"）；B-10 P2→**P3**。B-1/B-2/B-3/B-4 四条 P1 **维持**，其中 B-3 的实害比蓝军写的更重（它自己标了"未证实"的那半，我证实了）。

**蓝军质量一句话**：没有凑数，十六条我全部独立复现到代码行或现场数据，**没有一条编造**——产物 meta 交叉表我用自写脚本重跑，`(batch,-1,echo)` 组合数与它的 69/12/43/6 完全一致；也**没有把够不到的路径说成必错**（B-8/B-9/B-15/B-16 它自己就注明了触发条件受限）。三处需要纠偏：① B-2 把根因记在"头名大小写"上，真正让队列产物不可复现的是 `batch_start` 不做 -1 具体化（读头那一行只是死代码）；② B-8/B-9/B-10 的"普通点界面够不够得到"没算到底（我算了：够不到或极窄），三条都应降一级；③ 蓝军说"看门狗会不会击杀属推断，未证实"——这条本机日志里就有实证，而且是**已经发生过**（见第四节）。

**必须先修（本轮发版门禁内）**：B-3（事件循环抢 GPU 锁，实测已被看门狗击杀过）、B-4（一个按钮必毁一次 90 分钟算力）、B-2（队列产物永久不可复现）、B-1（⑬ 此刻正在报磁盘上不存在的模型名）。

---

## 二、逐条判定

> 格式：`B-x | 蓝军级别 → 我的判定与级别 | 一手证据 | 一句话整改`

**B-1 | P1 → 成立，P1 维持（真相＝(a) 写了不存在的名字）**
一手证据：`GET /api/system/info` 实测 `engines.loaded_main="yue2-q4.gguf"`、`audiocpp_alive:true`（副本 `sysinfo` 全文见 §3.1）；`runtime/data/engine_state.json`（166 B，mtime **2026-10-16 12:46:39**）内容与蓝军所见同构，仍写 `yue2-q4.gguf`；`cpp/server.json`（mtime 2026-10-16 00:11:55）`models[0].path="model/yue2-3b-q8_0.gguf"`；全仓库 `find -iname "*.gguf"` 只有 4 个文件，**没有任何 `yue2-q4.gguf`**；`available_models()`（副本 app.py:329-357）用 `MODEL_DIR.rglob("*.gguf")` 扫真实文件名，`models_switch` 写的是 `target.name`（真实 basename）→ **不存在别名机制**；`loaded_main` 全仓库唯一写入点＝副本 app.py:1728（`git log -S loaded_main` 显示 ac9bc19 引入，即本轮新增），三个 `_engine_state_write` 调用点（621/1728/2341）中只有 1728 写这一族；`_orphan_cleanup_on_startup`（8720-8760）只改 output meta 与训练 job，**不校正也不清空 engine_state**；`_restart_audiocpp`（445-473）是 `taskkill`+`Popen` 发完不管，**没有** `_wait_backend_ready`、不核对 `/health`（同一个文件 2287/2314 的 CPU 兜底却调了它）。另：该 state 文件在蓝军 12:01:52 之后又被写过一次（12:46:39）仍带旧名，**这就是蓝军触发链③"只改 backend 的读改写会把过期 loaded_main 原样带过去"的现场复现**。本轮自带的回归测试也拦不住：`tests/test_submit_paths_agree.py:89-93` 只做字符串包含（`assertIn('"loaded_vae_from"', src)`）。
整改：`loaded_main/loaded_vae` 改为**从 server.json 现读**（单一真源），或读时校验文件存在、不存在就显示"上次切换声明：xxx（磁盘上已无此文件）"，并给 `_restart_audiocpp` 补 `_wait_backend_ready`。

**B-2 | P1 → 成立，P1 维持（根因要改归到 batch 入口不做种子具体化）**
一手证据：读头那行＝副本 app.py:2320 `seed_actual = headers.get("X-Seed") or payload.get("seed")`；`_gen_run_once` 返回 `dict(r.headers)`＝副本 2131。用仓库自带解释器实测本机：`httpx 0.28.1`，`dict(httpx.get('http://127.0.0.1:7863/api/system/info').headers)` 键＝`['date','server','content-length','content-type','x-content-type-options','referrer-policy','content-security-policy']`，`h.get('X-Content-Type-Options')`→**None**、`h.get('x-content-type-options')`→`nosniff` → **混合大小写头名经 `dict()` 后一律取不到**，2320 恒为 None。同一栈的旁证：`app = main.app`（副本 59），`/api/music/generate` 与网关同进程同栈；`main.cp312-win_amd64.pyd` 字符串表 `X-Seed` 命中 1 次、`x-seed` 0 次。现场数据（163 份 meta 全量解析，0 份解析失败）：**done 的 130 份里 `seed_actual == params.seed` 130/130**，`seed_actual != params.seed` **0 条**（蓝军数出的"3 条不等"是 `seed_actual` 键根本不存在的 cancelled/error，不是头生效的证据）；`seed_actual == -1` 的 done 产物 **81 份**（批量 69＋单首 12，12 份单首全部是 09-14~09-21 的旧数据，早于入口具体化）。**但结论的因果要改写**：让队列产物不可复现的是副本 app.py:3001 `"seed": t.get("seed")` 原样透传 -1（批量 worker 2884-2888 的 params 白名单同样原样写进 meta），而单首在 2560-2563 就具体化了（`base_seed = payload.get("seed")` → 非负整数以外一律 `secrets.randbelow(2**31)`）；即使把头名修好，队列仍然缺一次具体化。**用户能不能复现一首歌**：② 页「生成歌曲」出的那 43+12 份里有真种子、能复现；「加入队列」出的 69 份记录里"实际种子"＝-1，**盘上无任何地方存过真种子**（⑪ 行模板不打印 seed，全页只有 ⑤ 进度条 `seed=`（index.html:5310）与 `runningSeedActual`（5421）用过这个字段，而 -1 显示出来也无意义）→ 队列成品**永久不可复现**。
整改：在 `batch_start` 组装条目时对 `seed` 走与单首同一段具体化（或修头名为 `x-seed` 并在 worker 落 meta 时回写），并把 seed 显示补进 ⑪ 行。

**B-3 | P1 → 成立，P1 维持，且实害被蓝军写轻了**
一手证据：`async def rvc_pitch_advice` 起于副本 app.py:3875，**3903 行 `with _GPU_SEM:` 就在事件循环线程里阻塞抢锁、无超时**（`threading.Semaphore` 本身没有 timeout 参数）。全文件闸门清点（副本行号，含外层函数）：2060 定义；2570 `_chain_runner`（普通 def，线程内 ✓，且 2570 的 `with` 包住整个 for 循环＝N 首连发全程持锁）；2851 `_batch_run_worker`（线程内 ✓）；5114/5162 `_rvc_convert_worker`（线程内 ✓）；5794 `_sep_worker`（线程内 ✓）；7367 acquire / 7664 release `_rvc_train_worker`（整条训练持锁）；5920 `acquire(blocking=False)` 探一眼即放 ✓。**只有 3903 在 async 里**。同函数其余重活都守规矩（3904-3905 的 `_run_vocal_separation`、3909 的 `_rvc_f0_of_audio` 都走 `run_in_threadpool`），唯独漏了这把锁；3910 行 `tgt = _rvc_f0_stats(model)` 也在事件循环里同步跑（缓存未命中时要遍历 `logs/<stem>/2b-f0nsf/*.npy` 做 numpy 统计，量级远小于锁，同一类）。进程模型：`uvicorn.run(app, ...)`（副本 8883-8889）**没有 workers 参数 → 单进程单事件循环**，一旦事件循环被占，`/api/health` 与全部状态接口一起不回话。可达性：`separate` 是 ⑨ 页勾选项（index.html:2440-2442 `<input id="rvcAnalyzeSep">`，缺省未勾；但 6553 行在判定"源音域可疑"时会**主动帮用户勾上再重测**，所以这条路会被界面自己引导到）；前端 6544 行 `fd.append("separate", doSep ? "1" : "0")`。**蓝军标"未证实"的那半我证实了**：看门狗判据在 `watchdog.py`——`CHECK_INTERVAL=10`、`HEALTH_TIMEOUT=15`、`FAIL_THRESHOLD=5`、`DEEP_PROBE_TIMEOUT=90`、`LONG_JOB_WINDOW=360`，免死清单 `LONG_JOB_GLOBS` 只认训练 job/日志/换声成品/`batch_state.json`，**不含 `output/*.json`**；一首歌 90 分钟期间 `batch_state.json` 只在条目开始时被写 → age 很快 >360s → 免死失效 → 击杀。本机日志实证：`runtime/data/logs/watchdog.log` 第 736-760 行连续 26 次短探失败＋90s 深探无应答（batch_state age 从 184s 涨到 354s），**第 762 行 `[10-16 10:09:22] 判定 网关 :7863 假死（90s 深探也无响应），执行重启…`、763 `已击杀假死进程 PID 27472`**；11:43:21 又一次击杀。
整改：把 3903 的 `with _GPU_SEM:` 整体挪进线程（`await run_in_threadpool(...)` 包住分离+抢锁那段），或改成 `acquire(blocking=False)`＋失败就回 409 提示"GPU 忙，稍后再体检"。这条与备忘"async def 里禁止做重活"是同一红线。

**B-4 | P1 → 成立，P1 维持（另加两点蓝军没写的）**
一手证据：按钮渲染条件＝副本 `index.html:5981`：`${h.status === "running" && !isRvc && !isTrain ? '<button …data-stop>⏹ 终止</button><button …data-restart>↻ 重启任务</button>' : ''}`——**没有 `isQueue` 判定**；紧接 6037 行的注释写着"进行中任务的终止/重启（仅队列条目有这两个按钮）"，**注释与条件不符**（这正是蓝军说的"契约不匹配"的本地证据）。点击处理＝6054-6066：先 `await fetch("/api/generate/stop?scope=current")` 再 `POST /api/batch/retry {id}`；后端 `batch_retry`（副本 app.py:3214-3236）只读 `_BATCH_STATE`（`runtime/data/batch_state.json`，实测 94 条 items、`running:false`），`it is None → 404 "任务不存在"`（3225-3227）。id 空间互不相通已核对：单首 id 只来自 `_make_job`（副本 2526-2552，写 output meta 于 2583）与内存 `_GEN_JOB`，全文 `_batch_write/_batch_store` 的 20 个调用点没有一处属于单首路径。行数据确实来自 `/api/generate/list`（副本 2633-2646，不过滤状态、原样吐 meta）→ 单首 running 行必然存在。**顺序确认为"先杀再 404"**：`generate_stop`（2365-2400）把 job 标 cancelled、`_gen_set_job(None)`、置 `_STOP_ONE_EVENT`、`_kill_audiocpp_now()` 强杀引擎；连发后续 take 也经 2573 `_stop_requested()` 断掉。**补充一**：↻ 按钮**没有二次确认**，而同一行的 ⏹ 有 `confirm(...)`（6040-6045）→ 一次误点即毁掉本机约 90 分钟算力且无重跑路径，直接违反本项目"破坏范围必须等于点击意图"。**补充二**：对批量条目该按钮是能用的（worker 用 `rid = nxt["id"]` 让 meta id 与队列条目 id 同一个），所以缺陷精确地是"非队列 running 行上的这个按钮必错"。
整改：渲染条件加 `isQueue`（或按 id 是否在批量状态里判定）；非队列 running 行改成"⏹ 终止 ＋ ↻ 重新提交这一首"（走 `/api/generate/start` 用记录里的原参数），并给 ↻ 加与 ⏹ 同级 confirm。

**B-5 | P2 → 成立，降级 P3**
一手证据：写入点副本 app.py:2277-2284 与 2302-2309（`job.update(cpu_fallback=True, gpu_error=…, note="显存不足，已自动切换 CPU 后端…")` 后 `_output_write_meta(job)`）；透传白名单副本 1407-1410 全文为 `("sec","queue_pos","assets","file","model","name_cn","epoch","epochs_total","src_name","bytes","strip_harmony","cot_note")`，**无 note/cpu_fallback/gpu_error**；`GET /api/tasks/unified?limit=2000` 实测 184 条，键并集 `[assets,bytes,error,file,id,kind,model,name,progress,queue_pos,sec,src_name,status,step,strip_harmony,ts]`，三键命中数 0；前端 `grep cpu_fallback|gpu_error index.html` 只有 5275 一处（⑤ 页进度文案）。降级理由（与蓝军不同的地方）：现场 **163 份 meta 里 0 份带 cpu_fallback/note/gpu_error**（触发条件确实受限——引擎自带显存调度，且 `_audiocpp_cuda_ready()` 为真时预检被跳过），且**降级这件事并非"改写用户内容"**，与本轮卖点"每一次改写都留痕"不是同一族；`note` 仍在 meta JSON 里可查，⑤ 页跑的时候也看得见。所以按实害定 P3，按"与 cot_note 同一批补齐"来修最省事。
整改：白名单加 `note`、`cpu_fallback`、`gpu_error` 三键，⑪ 行模板把 `h.note` 当第二行留痕打印出来（与 cot_note 同一段落）。

**B-6 | P2 → 成立，P2 维持（本轮卖点的真断口）**
一手证据：后端三段都通了——条目存 note（副本 app.py:2992 `"cot_note": cot_note`）、worker 落进产物 meta（副本 2889，注释自己说明这就是补断链）、统一视图白名单收了（副本 1410）。断在前端 mapper：`fetchExtraTasks` 的 batch 分支手工拼行对象（副本 `index.html:5839-5847`，对象字面量在 5841-5846，字段清单 `id/kind/status/ts/task_name/style/lyrics/cot/abc/params/error/sec/bytes/isQueue`），**`it.cot_note` 就在原地（batch_state.json 实测条目带该键）但没被带上**；第二个 mapper（history/active，副本 5859-5870）同样没有。⑪ 里 generate 类**不走** `/api/tasks/unified`（副本 5876 行 `filter((x) => x.kind === "score" || x.kind === "separate")`）→ 本次新加的透传对本页是死代码。前端 `grep -n cot_note index.html` 全文三处：5525、5567（两条 3.6 秒 toast）、5966（行渲染）。回归测试拦不住：`tests/test_submit_paths_agree.py:85-87` 只 `assertIn("h.cot_note", html)`，5966 一处就让它通过，**测不到 extra 分支丢字段**。补充（蓝军没写）：排队中的**单首**任务更糟——`generate_start` 先放 `_GEN_JOB={"id":None,...}`，真 job/meta 要等线程在 2570 抢到锁才创建，所以排在批量队列后面的单首在 ⑪ 里**整行都不存在**（history/active 那条被 `filter(j => j.id)` 滤掉），不只是看不见 note。
整改：两个 mapper 各补 `cot_note: it.cot_note || j.cot_note || ""`，并把该测试改成对 fetchExtraTasks 两个 mapper 的字段清单做断言。

**B-7 | P2 → 成立，P2 维持**
一手证据：`_output_write_meta`（副本 1931-1936）是 `write_text` 截断再写；`_output_read_meta`（1921-1929）`except Exception: return None`；写点共 12 处（2279/2306/2317/2330/2347/2388/2583/2726/2891/5326/5745/7633、7653），读点是 ⑤/⑨/⑩/⑪ 的 5 秒轮询（`generate_list` 2641 `if m:` 跳过、`tasks_unified` 1436 `if not m: continue`）。同文件已有两处按此风险修好的先例并留了注释：`_batch_write`（2795-2802，"蓝军 N-2：写一半被杀 = JSON 损坏"）、`_rvc_train_write`（6540-6547，"原子写（裁定 C-4）"）；`grep -c os.replace` 在 JSON 状态写上就是这两处（另有 1573/2042 是音频落地、6098/6451-6452 是归档与模型替换）。同病未修的还有 `_hist_write`（1291-1295）、`_engine_state_write`（375-383）、`_score_save`（795-803）、`_orphan_cleanup_on_startup` 的重写（8736）。诚实标注：现场 **163 份 meta 0 份解析失败**，蓝军据此不拔高，我也一样；维持 P2 的理由是**与项目自己已承认并修过两次的同一标准保持一致**，且修复是 3 行。
整改：`_output_write_meta`/`_hist_write`/`_engine_state_write`/`_score_save` 一并改"同目录临时文件 + `os.replace`"。

**B-8 | P2 → 成立，降级 P3**
一手证据：漏检写法＝副本 app.py:1619-1620 `for gone in items[HIST_KEEP:]: (HIST_DIR / gone.get("file", "")).unlink(missing_ok=True)`；同文件已加固的两处＝`history_clear` 1640-1646 与 `history_delete` 1660-1666，都有 `os.path.basename(...replace("\\","/"))` + `fp.parent == HIST_DIR.resolve()` + 注释"审计 S-3：history.json 可被篡改，file 字段需 containment 校验，防删任意文件"；`file` 字段唯一生成点是 1597 `fn = rid + ".wav"`，`_hist_write` 只有 1622/1647/1666 三个调用点（全量读-改-写，改一条既有记录就进入轮转分支）。降级理由（够不够得到）：**双重前置**——① 必须本机 `runtime/data/records/history.json` 被外部改过（普通点界面改不出这个字段，`_guard_local_only` 也不管内容）；② 必须条数超过 `HIST_KEEP = 1000`（副本 1278）才会进轮转；实测本机 **history.json 只有 1 条记录、file 字段全部是 `rid.wav` 形状**。所以危害是真的（越目录删除），但离"点界面就能踩"很远 → P3（同一标准的执行漏项，补三行即可，不必等）。
整改：轮转分支复用 clear/delete 那两行的 basename+containment 双闸门。

**B-9 | P2 → 部分成立，降级 P3**
一手证据（用 d08c518 副本源码抽函数离线跑，未导 `main`、未起任何服务）：`_style_yield_to_abc("88 BPM", <带 Q:1/4=88 的谱>)` → `('', 'style 里的 BPM 已让位给乐谱的 88 BPM（节奏以乐谱为准）')`；`"120 BPM 4/4"` → `('', …BPM＋拍号…)`；`"4/4"` → `('', …拍号…)`；`"Mandarin pop, 88 BPM, 4/4, warm strings"` → `('Mandarin pop, warm strings', …)`（**只剩节奏时才会被剥空**）。单首与批量两条路径都剥（副本 2507-2513 与 2983-2991），两路结果一致 → 不是两路不等价，是两路一起掏空；`generate_start` 的必填校验在**剥之前**（副本 2492-2493），剥后 `payload["style"]=""` 送引擎（2514），`_make_job` 存的也是空（2540 区）。判定为"部分成立"的理由：① 引擎对空 style 的行为**三方都拿不到**（在 pyd 里，不启动 GPU 无法证实），蓝军自己也如实标注；② "绕过必填闸门"的定性偏重——用户确实填了 style，是我们把它掏空的；③ 更常见的一半危害与节奏无关：**记录里存的是剥后的 style，用户原文（含 BPM/拍号）在 meta 里永久丢失**，只能靠 note 反推。④ 界面可达性我算到底了：② 页提交前会先过 `styleWithGender`（定义在副本 `index.html:2972`，两条入口的调用点分别是 5491 与 5541），它把人声下拉的值拼进 style 尾部（`VOICES` 见 3561，缺省 `clear female vocal`），所以只有把下拉选到「纯音乐（不写人声）」这一项、并且 style 里只写节奏、并且乐谱带 Q:/M: 时才会得到空串 → 条件很窄 → **P3**。
整改：`_style_yield_to_abc` 剥完 `if not eff.strip(): return style_original, ""`（保留原 style、只放弃让位，或反过来让位并明确报一句"style 只剩节奏，已按乐谱走"），同时让记录里另存 `style_submitted` 原值。

**B-10 | P2 → 成立，降级 P3**
一手证据：`_server_audio_copy`（副本 2011-2051）= `run_in_threadpool` + `_free_bytes(dst.parent) < _DISK_FLOOR_BYTES → 507` + `budget["used"] + size > _MAX_UPLOAD_TOTAL → 413` + `.part` 临时文件 + `os.replace`；两个阈值都是 **2 GiB**（副本 1507-1508）。而 `rvc_convert_by_rid` 是 `async def`（副本 8376）里 8409 行裸 `shutil.copyfile(src_wav, src)`，不查剩余、不计总量、无 `.part`。其余参数处理两入口一致（副本 8397-8404 与 5473-5475、5511-5514、5552-5553），差的确实只有拷贝那一段；multipart 侧走的是 `await _server_audio_copy(server_src, src)`（5528，注意它没传 budget → 只少了 413，507 与线程池都在），并且外面包着 `except BaseException: shutil.rmtree(in_dir)`（5531-5532）失败不留残目录——**by_rid 这层清理也没有**：8405-8409 的 `mkdir` + 裸 copyfile 一旦中途抛，`jobs/<rid2>/` 里就留下一份半截 `src.wav` 与空目录，蓝军没记这一半。降级理由（我量了）：本机产物口径实测 **263 个 wav，中位 40.4 MB，最大 94.2 MB**；`shutil.copyfile` 拷最大那一份到 %TEMP% 实测 **185 ms**（≈500 MB/s）→ "复制期间所有轮询停摆"的真实代价是**零点几秒**（最坏冷读也就 1s 量级），与 B-3 那种小时级钉死不是一个量级；同时单首 ≤100 MB 远够不到 2 GiB 的两道闸门，只有"盘快满时照样收"与"失败留残目录"这半条是真的。所以违反红线属实、危害很小 → **P3**。
整改：8409 换成 `await _server_audio_copy(src_wav, src)`（一行），既走线程池也走 507 闸门、还顺带拿到 `.part` 与失败清目录。

**B-11 | P3 → 成立，P3**
一手证据（同一份副本函数、复刻两条入口的拼接顺序）：`cot="bogus"`＋带和弦谱＋无收尾歌词＋style 含 BPM/拍号 → 单首 note 顺序＝"档位→节奏→收尾"（135 字），批量＝"档位→收尾→节奏"；`cot="off"` 例同样分叉（134 字）；`cot=""`、`cot="full"` 两例（我的谱形无和弦变化，长度 98 字）也分叉。`SAME_COT=True`、`SAME_STYLE=True`、**`SAME_ORDER=False`**。代码对应：单首 ending 先算但拼在末（副本 2507 算 ending、2511-2516 先 tempo 后 ending），批量 ending 先拼、tempo 后拼（副本 2983-2991）。长度差异当前无实害（批量条目存原始 note 无 `[:400]`，meta 截 400，副本 2992 vs 2889）。
整改：把三处改写的拼接顺序抽成一个函数（`_compose_note(cot_note, ending_note, tempo_note)`），两路同调用。

**B-12 | P3 → 成立，P3**
一手证据：副本 `index.html:5966` 把 `cot_note` 硬切 `String(h.cot_note).slice(0, 120)`，`title` 属性给的是**固定说明文字**（不是全文），而同一个块里 error 走 `slice(0,120)`＋"（点击查看全部）"＋`data-err` 展开（5965、6021-6031）。我实测最坏组合 135 字时**被切掉的正是末段**：`…已补 [Outro]（器乐渐出）` 之后的 `防止模型在尾声自由发挥出说话声`（15 字）读不到，页面停在半句上。
整改：`cot_note` 复用 error 那套 `data-err` 展开，`title` 放全文。

**B-13 | P3 → 成立，P3**
一手证据：`_engine_state_write`（副本 375-383）= `_engine_state_read()` → `state.update(updates)` → `write_text(...)`，**无锁、非原子**；`system_info`（副本 727-730）在同一次响应里**连读四次**同一份文件（loaded_main 一次、loaded_vae 三次：判空＋取 loaded_vae_from）。写入方实测三个点（621 `backend_mode_set` HTTP 线程、1728 `models_switch` HTTP 线程、2341 生成线程的 CPU 兜底）。后果链已核：`_engine_state_read` 吞异常返回 `{}`（368-373）→ `backend_mode()` 静默按 "cuda"（386）→ ⑬ 的"当前主模型"变空。文件只有 166 B，窗口极窄 → P3 与我一致。补充：本条与 B-1 同源，**改成"从 server.json 现读"可以一次解掉两条**。
整改：state 读写各持一把模块级锁＋原子发布；或干脆删掉这一族字段、以 server.json 为唯一真源（推荐）。

**B-14 | P3 → 成立，P3**
一手证据：副本 app.py:4314-4322——注释自己写着"页面是 step=1 的 number 输入，4、6 随手能填且能过闸门，所以在这里钳到下一个奇数（4→5、6→7）"，代码 `if fr > 2 and fr % 2 == 0: fr += 1`；结果只以 `"filter_radius": g_fr` 进 job（副本 5547 区），响应与任务卡都不说"你填的 4 被改成 5"。同文件的对照规矩确实立过：fcpe 分支（4270-4285 区）明确拒绝悄悄降级成 rmvpe 并在提交时报错。换声整族不走 `cot_note`（白名单里没有换声 note 字段），所以"同类改写一个报错、一个静默"成立。实害是平滑核变一档 → P3 与我一致。
整改：钳位时把说明拼进该任务的可见文案（或返回体里带 `filter_radius_note`），与 fcpe 的口径统一。

**B-15 | P3 → 成立，P3**
一手证据：批量白名单（副本 2996-3006）显式列 12 个键，其余丢弃；单首 `p = dict(payload)` 只 `p.pop("count", None)`（副本 2575-2576）后整份转给 `/api/music/generate`（副本 2125）。引擎字符串表只读扫描（我自己跑，单次读 0.3 MB pyd）：`voice/abc_file/abc_text/abc_max_tokens/abc_min_tokens/abc_penalty_window/abc_repetition_penalty/semantic_max_tokens/semantic_min_tokens/semantic_penalty_window/semantic_repetition_penalty` **各命中 1 次**，`X-Seed` 1 次，`lyrics is required` 1 次。与蓝军同样的诚实边界：**哪些是请求字段未证实**（要看 pydantic 模式就得启动 GPU），所以只能主张"两路对未知参数处理相反、丢弃既不 400 也不留痕"。UI 暂时踩不到已复核：两条提交路径字段集相同（副本 `index.html:5502-5512` 与 5552-5559，单首多 `count`/`task_name`）。
整改：两路统一——要么批量也用透传（白名单只用于"必填校验"），要么单首也按白名单并在遇到未知键时 400/note 说明。

**B-16 | P3 → 成立，P3**
一手证据：批量 `if not style or not lyrics: raise 400`（副本 2964-2966）；单首只验 style（2492-2493），lyrics 只做 `_limit_text`（2496-2500），`_ensure_song_ending` 对空词按设计不补（2486-2488，注释还写着"**空歌词不补（校验层会拦**，这里不该替它编内容）"）——而单首入口实际没有那层拦截，空串原样进 payload 并占 GPU 锁（2560 之后）。引擎侧证据：pyd 字符串表 `lyrics is required` 命中 1 次（说明它会在推理侧拒）。前端两条路都先挡了空词（副本 `index.html:5492` 与 5543），所以只有 API 直连/旧数据复用踩得到 → P3 与我一致（一次注定失败的提交先排队占锁，把后面条目一并推后，这个代价描述成立）。
整改：单首入口加与批量同一句 `if not lyrics: 400`，别让它排队。

---

## 三、重点组单独成节（数字可复现）

### 3.1 B-1 真相：模型状态三方对账

磁盘实际清单（`find . -iname "*.gguf"`，排除 `.git`）：

| 文件 | 字节 | mtime |
|---|---|---|
| `cpp/model/yue2-3b-q8_0.gguf` | 4,264,186,432 | 09-12 16:47 |
| `cpp/model/yue2-vae-f16.gguf` | 265,218,656 | 09-12 16:43 |
| `cpp/model/yue2-q4_k_m/yue2-3b-q4_k_m.gguf` | 2,877,774,656 | 09-14 01:38 |
| `cpp/model/yue2-q4_k_m/yue2-vae-f16.gguf` | 265,226,496 | 09-14 01:40 |

**`yue2-q4.gguf` 不在任何目录**（`ls -la cpp/model/` 只有 `sidecars/`、上面两个文件、`yue2-q4_k_m/` 目录；`runtime/models/` 只有 `faster-whisper-large-v3-turbo`）。

`runtime/data/engine_state.json`（166 B，mtime **2026-10-16 12:46:39**）：`{"backend":"cuda","loaded_main":"yue2-q4.gguf","loaded_vae":"yue2-vae-f16.gguf","loaded_vae_from":"同目录","loaded_path":"yue2-q4.gguf"}`
`cpp/server.json`（514 B，mtime 2026-10-16 00:11:55，未入库：`git ls-files --error-unmatch cpp/server.json` 报 pathspec 不匹配）：`models[0].path="model/yue2-3b-q8_0.gguf"`，`session_options={"yue2.model_gguf":"yue2-3b-q8_0.gguf","yue2.vae_gguf":"yue2-vae-f16.gguf"}`
`GET /api/system/info` 此刻实测：`engines={audiocpp_alive:true, loaded_main:"yue2-q4.gguf", loaded_vae:"yue2-vae-f16.gguf（同目录）"}`，`gpu={mode:"cuda",total_mb:6144,free_mb:985,used_mb:5159}`，version `v10.16.0`。

写入/读取清点：`grep -n loaded_main` → 读＝727（`system_info`），写＝**仅 1728（models_switch）**；`grep -rn engine_state`（排除 py312/docs/node_modules）→ **只有 app.py 碰这个文件**，启动脚本与 `watchdog.py` 都不写它，`scripts/download_models.py` 只写模型文件与 `MODEL_MANIFEST`（副本 501-506 的落地路径是 `cpp/model/yue2-q4_k_m/…`，同样不写 state）。启动流程 `_orphan_cleanup_on_startup`（8720）不校正/不清空 engine_state。⑬ 的中文标签＝"当前主模型"（副本 `index.html:8302`），且 `renderSettings` 只用 `/api/system/info`；`/api/models/current`（副本 479-490，只读 server.json）另报一套 → **两页各说一套**。

**裁定：真相＝(a)**——state 里是一个磁盘上不存在的主模型名，不是别名（`available_models()`/`models_switch` 只会写真实 basename），也不是蓝军找错目录（我在 `MODEL_DIR` 与全仓库各扫一遍）。附带把蓝军的触发链③升级为**已发生**：state 在 12:01:52→12:46:39 之间被"只改 backend"的读改写再写一次，过期 `loaded_main` 被原样带到新文件里。

### 3.2 B-2 现场数字（全部自写脚本产出，可原样重跑）

- 扫描范围 `runtime/output/*.json` **163 份**，JSON 解析失败 **0** 份。
- 按 kind 分：生成类（无 kind 键）**133**、`rvc` **28**、`train` **2**。
- 生成类状态分布：`done` **130**、`cancelled` **2**、`error` **1**。
- **done 交叉表**（batch, params.seed, seed_actual）：

| batch | params.seed | seed_actual | 条数 |
|---|---|---|---|
| True | -1 | -1 | **69** |
| False | -1 | -1 | **12**（全部 ts ≤ 2026-09-21，早于入口具体化） |
| True | 真值 | =params.seed | **6** |
| False | 真值 | =params.seed | **43** |

- `seed_actual == params.seed`：**130/130**（done 全等）；`seed_actual != params.seed`：**0 条**（蓝军说的"3 条不等"＝`seed_actual` 键缺失的 cancelled/error）。
- 头名实测：`httpx 0.28.1`，同一栈的 `X-Content-Type-Options` 经 `dict(r.headers)` 后取不到（None），小写形式才取得到（`nosniff`）→ 副本 2320 的 `headers.get("X-Seed")` **恒 None**。
- pyd 字符串表：`X-Seed` 1、`x-seed` 0。
- **复现结论**：走「生成歌曲」的歌能复现（记录里是真种子，⑤ 进度条会显示 `seed=`）；走「加入队列」的歌**不能复现**（69/69 记录里"实际种子"＝-1，真值从未在任何地方落过盘）。
- 关键背景（三方都没写的）：**盘上 163 份 meta 里没有一份带 `cot_note`/`note`/`cpu_fallback`/`gpu_error`**；生成类 ts 范围 `2026-09-14T19:34:46 → 2026-10-16T09:18:16`，而留痕代码是 `ac9bc19`（11:56）提交、`d08c518`（12:03）打点——**本轮"每一次改写都留痕"的全部产物证据为零**，B-5/B-6/B-11/B-12 只能静态定，任何验收都必须先跑一条新任务再看 meta（本轮不许起 GPU，所以我按静态判）。

### 3.3 B-3 `_GPU_SEM` 全量清点（d08c518 副本行号）

| 行 | 外层函数 | 是否 async | 是否事件循环 | 判定 |
|---|---|---|---|---|
| 2060 | 定义 `threading.Semaphore(1)` | — | — | — |
| 2570 | `_chain_runner`（@2565，普通 def，线程） | 否 | 否 | ✓（且 with 包住整个 for，N 首全程持锁） |
| 2851 | `_batch_run_worker`（@2833，线程） | 否 | 否 | ✓ |
| **3903** | **`rvc_pitch_advice`（@3875）** | **是** | **是** | **✗ 唯一违规点，acquire 无超时** |
| 5114 / 5162 | `_rvc_convert_worker`（@5088，队列线程） | 否 | 否 | ✓ |
| 5794 | `_sep_worker`（@5787，线程） | 否 | 否 | ✓ |
| 5920 | `audio_separate_submit`（async）里的 `acquire(blocking=False)` | 是 | 是 | ✓（探一眼即 release，5919 注释专门警告） |
| 7367 / 7664 | `_rvc_train_worker`（@7353，线程） | 否 | 否 | ✓（整条训练持锁） |

进程数：`uvicorn.run(app, host=…, port=…, reload=False, timeout_keep_alive=75)`（8883-8889）**无 workers → 单事件循环**。
看门狗判据：`CHECK_INTERVAL=10 / HEALTH_TIMEOUT=15 / FAIL_THRESHOLD=5 / DEEP_PROBE_TIMEOUT=90 / LONG_JOB_WINDOW=360`；免死只看 `rvc/trains/*/job.json`、`rvc/logs/*/train.log`、`rvc/jobs/*/converted.wav`、`data/batch_state.json`。日志实证：`watchdog.log` 736-761 行连续失败与 age 递增（184s→354s），**762 行 10:09:22 击杀 PID 27472**，770 行 11:43:21 再次击杀 → "等锁 → 全网关无应答 → 被看门狗杀掉 → 正在算的那首一起没了"这条链在本机已经发生过（触发源未必是 3903，但**阻塞事件循环的结局被证实是击杀而非只是慢**）。

### 3.4 B-5 / B-6 留痕可见性矩阵（状态 × 单首/批量）

`cot_note`（本轮卖点字段）：

| 状态 | 单首（非队列） | 批量（队列条目） | 行的数据来源 | 断点行（d08c518 副本） |
|---|---|---|---|---|
| queued/pending（未拿到锁） | **整行不存在**，更谈不上 note | **有行但看不见 note**（note 在 batch_state 里） | 单首：无；批量：`/api/batch/status` | `index.html:5841-5846`（mapper 丢字段）；单首另因 `_GEN_JOB` 占位 id=None 被 5859 行 `filter(j => j.id)` 滤掉 |
| running | 看得见（meta 于 2583 带 cot_note 落盘） | 正在跑那一条看得见（2891）；同队其它 pending 条目看不见 | `/api/generate/list` | — |
| done | 看得见 | 看得见 | `/api/generate/list` | — |
| error / cancelled | 看得见 | 看得见 | `/api/generate/list` | — |
| 统一视图 `/api/tasks/unified` | 字段在（白名单含 cot_note） | 字段在 | — | 但 ⑪ 不消费它的 generate 条目（`index.html:5876` 只取 score/separate）→ 对本页死代码 |

`note` / `cpu_fallback` / `gpu_error`（显存降级留痕）：

| 状态 | 单首 | 批量 | meta 里有吗 | 页面上看得见吗 | 断点 |
|---|---|---|---|---|---|
| pending | — | — | 无（尚未发生） | — | — |
| running | 有（2279/2306 写盘） | 有 | ✓ | **只有 ⑤ 页进度文案**（`index.html:5275` 读 `job.cpu_fallback`，走 `/api/generate/status`）；⑪ 行模板不打印 | 白名单 `app.py:1407-1410` 不含三键；模板 `index.html:5965-5966` 只打 cot_note |
| done | 有 | 有 | ✓ | **看不见**（⑪ 不打、unified 不带、刷新后 ⑤ 也不再提） | 同上 |
| error/cancelled | 有 | 有 | ✓ | **看不见** | 同上 |

**本轮卖点"每一次改写都留痕"的成立度**：字段链路（计算→条目→meta→统一视图→⑪ 行）在"已开跑之后"是通的（B-6 反例尝试 (b) 我复核为真：worker 确实把 note 接进 meta，2889）；但**"排队几小时那段"看不见**（批量第 2~N 首，本机 90 分钟/首，这是产品常态），且**该断口只有字符串包含测试守着**（`tests/test_submit_paths_agree.py:85-87`），所以判"部分成立 → 补一行 mapper 即可成立"。降级事件（note 一族）则**在 ⑪/统一视图整条不可见**，属于同一族的漏项（B-5）。

### 3.5 B-8 可达性量化

`HIST_KEEP = 1000`（副本 1278）；实测 `runtime/data/records/history.json` **1 条记录**、`file` 字段 0 条带路径分隔符。轮转分支（1619-1620）要同时满足"条数 >1000"且"`file` 被外部改成越目录形状"才会删到 HIST_DIR 之外 → 普通点界面**够不到**（`file` 只由服务端 1597 生成）→ 判 P3（一致性漏项，补三行）。

---

## 四、蓝军"主动撤回"六条的复核

| # | 蓝军的撤回 | 我的复核 | 撤得对不对 |
|---|---|---|---|
| 1 | `cot_note` 截 400 会截掉关键信息 → 不成立 | 我用副本函数实跑最坏组合（无法识别值＋按谱换档＋BPM/拍号双剥＋补收尾）得最长 **135 字**，另三例 134/98/98，全部 <400；真正会截的是前端 120 字（B-12） | **对**，且我把"真正被切掉的尾巴"量化了（切掉 15 字，正是最后一项的理由半句） |
| 2 | 批量条目跑完写 meta 时 cot_note 断链 → 不成立（已修） | 副本 2889 `"cot_note": str(nxt.get("cot_note") or "")[:400]`，注释自陈就是补这一断口；2992 也确认条目侧写入 | **对**（注意：现场 133 份 meta 尚无一条能验证它，因为都早于代码） |
| 3 | `final = _output_read_meta(rid) or job` 会把成功读成失败 → 不成立 | 副本 2866-2899：`job` 是就地构造的同一个 dict，`_gen_run(job, payload)`（2900）内部 `job.update(...)` 后写盘；2913 `final = _output_read_meta(rid) or job` 兜的正是 meta 读失败 → 兜底有效 | **对** |
| 4 | `_resolve_cot` 空值仍静默 → 属已裁定行为，不报 | 副本 893-897：`mode` 为空串时 `note` 保持 `""`（`% … if mode else ""`），返回 ("full","")；`tests/test_submit_paths_agree.py:64` 确有 `test_empty_cot_stays_quiet` 钉住 | **对**（前端两条路都显式带 cot，空值只能来自 API/旧数据，与"改了用户的选择"不同族） |
| 5 | ① 连发首数在「加入队列」被忽略 → 不构成缺陷 | 机制核对为真：队列只提交 1 条 task（副本 `index.html:5552-5562`，`tasks: [task]` 在 5562），`batch_start` 不读 count。蓝军给的理由是"摘要行写着连发 N 首"，这条我判**不够硬**；真正站得住的是回话文本本身——副本 app.py:3026-3027 `msg = "已加入队列（排在第 X~Y 位…）"` 或 `"已开始批量任务（共 1 首）"`，前端 5570 行把它原样显示，**界面明确报了"共 1 首"** | **撤得对，但要把理由换成 message 这一条**；折叠摘要那句可以留着当 P4 文案打磨，别当缺陷也别当免罪 |
| 6 | 暴露面本版没有恶化 | 按发布区间 `git diff v10.14.1 d08c518 -- app.py` 实测：**新增路由装饰器 0 条**（`grep -E "^\+.*@router\.(get|post|delete|put)"` 空），`_guard_local_only`/`_WRITE_METHODS` 在该 diff 中出现次数 **0**；`/api/models/repair` 仍是 POST（副本 546）；唯一 GET 写盘是 `GET /api/generate/lyrics/{rid}` 的"txt 不在就现生成"（副本 2666-2668），幂等缓存填充 | **对**（补充：本区间共 11 个 commit，蓝军所谓"本次 diff"＝v10.14.1→d08c518，与我对齐） |

---

## 五、复核用的命令与脚本（第三方可原样重跑）

```bash
# 只读副本（本文行号出处）
mkdir -p /c/Users/59221/AppData/Local/Temp/qoder/rev3b
git -C /e/AI/10AIMusic/Yue/yuE-2/yuE-2 show d08c518:app.py > /c/Users/59221/AppData/Local/Temp/qoder/rev3b/app.py
git -C /e/AI/10AIMusic/Yue/yuE-2/yuE-2 show d08c518:static/index.html > /c/Users/59221/AppData/Local/Temp/qoder/rev3b/index.html
wc -l /c/Users/59221/AppData/Local/Temp/qoder/rev3b/*    # 8892 / 9338

# 脏树偏移（证明蓝军行号 = d08c518 + 5，阈值 801）
git -C /e/AI/10AIMusic/Yue/yuE-2/yuE-2 diff -U0 app.py | grep '^@@'

# B-1：三方对账（只读）
find /e/AI/10AIMusic/Yue/yuE-2/yuE-2 -iname "*.gguf" -not -path "*/.git/*"
cat /e/AI/10AIMusic/Yue/yuE-2/yuE-2/runtime/data/engine_state.json
cat /e/AI/10AIMusic/Yue/yuE-2/yuE-2/cpp/server.json
curl -s --max-time 40 http://127.0.0.1:7863/api/system/info   # 只读 GET
grep -n "loaded_main\|_engine_state_write\|_orphan_cleanup_on_startup" /c/.../rev3b/app.py

# B-2：头名大小写 + meta 交叉表（本机 vendored 解释器）
./py312/python.exe -c "import httpx;h=dict(httpx.get('http://127.0.0.1:7863/api/system/info',timeout=40,trust_env=False).headers);print(list(h));print(h.get('X-Content-Type-Options'),h.get('x-content-type-options'))"
./py312/python.exe %TEMP%/qoder/rev3b/seedstats2.py     # 163 份 meta → done 交叉表 69/12/43/6、130/130 相等
./py312/python.exe %TEMP%/qoder/rev3b/pydscan.py        # X-Seed=1、x-seed=0、lyrics is required=1

# B-3：锁清点 + 看门狗实证
grep -n "_GPU_SEM" /c/.../rev3b/app.py
sed -n '40,70p;395,455p' /e/.../watchdog.py
grep -n "判定 网关\|免死\|深探" runtime/data/logs/watchdog.log | tail -20

# B-5/B-6：统一视图与留痕
curl -s "http://127.0.0.1:7863/api/tasks/unified?limit=2000"   # 184 条，note/cpu_fallback/cot_note 命中 0
./py312/python.exe -c "import json;d=json.load(open(r'runtime/data/batch_state.json',encoding='utf-8'));print(len(d['items']),d['running'])"

# B-9/B-11/B-12：纯函数复跑（只 exec 函数体，不 import main、不起服务）
./py312/python.exe %TEMP%/qoder/rev3b/purefn.py

# B-10：拷贝计时（写 %TEMP%，不碰产品树）
./py312/python.exe %TEMP%/qoder/rev3b/hist.py           # 94.2 MB → 185 ms；263 个 wav，中位 40.4 MB

# 撤回 6：发布区间 diff
git -C /e/... diff v10.14.1 d08c518 -- app.py | grep -E "^\+.*@router\." | wc -l    # 0
```

临时脚本（`seedstats.py`/`seedstats2.py`/`detail.py`/`hist.py`/`unified.py`/`pydscan.py`/`purefn.py`）与两份 d08c518 副本放在 `%TEMP%\qoder\rev3b\`，交付后自行清理；产品树内除本报告外未新增或修改任何文件（`git status` 仍是那四处别的会话的在制品 + 评审文档本身）。

---

## 六、给中立裁定员的三句话

1. 蓝军 G2 的十六条**没有空枪**：证据可复算（meta 交叉表我独立跑出同一组数），行号虽有 +5 偏移但没踩在制品；四条 P1 我全部维持，其中 **B-3 我把它标为"未证实"的看门狗击杀部分用本机日志证实了**，B-2 的根因需要从"头名大小写"改记到"batch 入口不做种子具体化"。
2. 我在级别上只往下动了四条（B-5/B-8/B-9/B-10 → P3），理由都是**可达性量化**：0/163 触发、1/1000 条记录门槛、默认性别后缀、94 MB 拷贝 185 ms；往上没有加级，但给 B-4 补了"↻ 无二次确认"和注释与条件不符两点、给 B-6 补了"排队中的单首整行不可见"一点。
3. 本轮验收有硬缺口：盘上 **163 份 meta 零条含 `cot_note`/`note`**，即"每一次改写都留痕"目前只有代码与纯函数证据、没有一条现场产物证据；发版门禁应在允许起一次 GPU 任务后，用 `runtime/output/<新 rid>.json` 复核 B-5/B-6/B-11/B-12 四组的实形。
