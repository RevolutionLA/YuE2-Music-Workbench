# 蓝军对抗式评审报告 — YuE2 本地整合包（基线 b9548fe）

**摘要：P0 × 1，P1 × 5，P2 × 8，P3 × 6。**

---

## P0（致命）

### P0-1 服务绑定 0.0.0.0、全部端点零鉴权，且 `/api/ai/web` 向任意调用者发放 dsh Web UI 令牌【真实可达】
- 证据链：
  - `settings.py:2` — `app_host: str = "0.0.0.0"`；`app.py:1925-1929` — `uvicorn.run(app, host=settings.app_host, ...)`。整个 API 面（生成、删除历史/音色/模型、RVC 训练、模板写盘）监听所有网卡，无任何鉴权层。
  - `app.py:59-65` — `CORSMiddleware(allow_origins=["*"], allow_credentials=True, allow_methods=["*"])`：任意网页可跨域打这些接口。
  - `ai_router.py:50-99` — `GET /api/ai/web` 会拉起 dsh web（:3081），并从日志正则提取 `http://127.0.0.1:3081/?token=...` **把带 token 的 URL 原样返回给任何调用者**（`_read_token()`，L59-64；L97-98 `return {"ok": True, "url": url}`）。
- 触发场景/后果：同一 LAN 内任何主机（或浏览器里任意恶意页面经 CORS/fetch）可直接 `POST /api/ai/web` 拿到 dsh UI token → 进入 dsh Web（具备 shell 命令执行能力的 agent 工作台，仅靠审批 UI 挡板）；也可直接 `DELETE /api/history/clear`、`POST /rvc/train` 打满 GPU/磁盘。
- 建议：绑定 127.0.0.1（或加共享密钥中间件）；`/api/ai/web` 不回传完整 token URL；CORS 收紧为同源。

## P1（高）

### P1-1 dsh 工具桥子进程 `import app` 触发孤儿清理，会把正在运行的生成任务改判为"中断"【真实可达】
- 证据链：`dsh-plugin/src/bridge.mjs:14-16` — 每次工具调用 spawn `python -c "...import ai_tools..."`；`ai_tools.py:42/55/82` 等在函数体内 `import app as gw`；`app.py:1882` — `_orphan_cleanup_on_startup()` 在**模块导入时**执行，把 `output/*.json` 与 `rvc/trains/*/job.json` 中所有 `status=="running"` 改写为 `error "服务重启，任务中断"`（app.py:1844-1879）。
- 触发场景：生成进行中，用户在 AI 工作台调任意工具（哪怕 `tool_get_progress`）→ 桥子进程导入 app.py → 落盘 meta 被改成 error；同时 `_gen_run` 完成后又 `job.update(status="done")` 覆盖——两个写者竞态，历史记录状态随机错乱。
- 另：`ai_tools.py:61` `r = gw.generate_start(payload)`，而 `generate_start` 是 `async def`（app.py:870）——同步子进程里拿到的是 coroutine，随后 `r["job"]`（L62）抛 `TypeError`，**AI 侧生成工具根本不可用**。
- 建议：把孤儿清理移入 lifespan/`__main__`；桥进程改为 HTTP 调用网关而非 import。

### P1-2 `generate_start` 检查-启动竞态（TOCTOU），可并发启动多条生成链【真实可达】
- 证据链：`app.py:871-873` — 先 `_gen_get_job()` 判 running，但 `_gen_set_job(job)` 要等新线程跑到 `_chain_runner` 内部（L926）才设置；检查与置位之间无锁。
- 触发场景：连发两个请求 → 两个 `_chain_runner` 都通过检查 → 各自排队等 `_GPU_SEM`，依次把引擎来回重启，任务 meta 互相覆盖。
- 建议：在 `_GEN_LOCK` 内完成 check-and-set（先置占位 job 再放线程）。

### P1-3 乐谱接口路径穿越：任意 `.json` 读取/删除【真实可达】
- 证据链：`app.py:361-365` — `scores_get`：`p = SCORES_DIR / f"{score_id}.json"` 直接拼接，**无 basename 防护**；`app.py:369-373` — `scores_delete` 同样可 `p.unlink()`。
- 触发场景：`GET /api/scores/..%2F..%2Fdata%2Frecords%2Fhistory` → 读任意 json；`DELETE /api/scores/..%2F..%2Ftemplates` → 删任意 .json。
- 建议：`score_id = os.path.basename(score_id)`，并校验 resolve 后仍在 `SCORES_DIR` 内。

### P1-4 `generate_stop` 与批量 worker / 生成线程的无锁竞态：已终止任务可被"复活"【真实可达】
- 证据链：`app.py:843-866` — stop 在 `_batch_snapshot()` 后用 `_batch_store(state)` 整份回写，读-改-写非原子；期间批量 worker 写入的 done/error 会被陈旧快照覆盖回 running。stop 写 `job["status"]="cancelled"`（L850-853）后 `_gen_set_job(None)`，而 `_gen_run` 线程仍持同一 job dict 引用并在完成时 `job.update(status="done")`（L803-810）——取消的任务最终显示"完成"。
- 建议：stop 用 `_BATCH_LOCK` 内读改写；取消后 worker 侧检查 cancel 事件再落盘。

### P1-5 `ai_router.py /api/ai/web` 以明文拼字符串方式经 PowerShell 启动进程，命令注入面 + 密钥泄露到进程命令行【真实可达】
- 证据链：`ai_router.py:78-90` — `cmd = 'cmd /c set DEEPSEEK_API_KEY=' + (ai_lab.ai_key() or "") + '... node ... web --port 3081 ...'`，随后经 PowerShell `Win32_Process Create`。整串经 shell 二次解析，仅对 `'` 转义——key 或路径中出现 `%`、`"`、`&`、`^` 即可截断/注入。密钥同时进入 WMI 进程命令行，本机任意进程可见。
- 建议：改 `subprocess.Popen([...], env={...})` 传 env 字典。

## P2（中）

### P2-1 模型切换把未净化的 `path` 写进 `server.json`【真实可达，影响限于引擎加载】
- `app.py:509/535` — 写入的是原始 `path`，引擎按 cwd 解析可指向 `cpp/` 外任意 gguf。建议写入净化后的相对路径并校验 resolve 前缀。

### P2-2 watchdog 多实例与误杀风险【真实可达】
- `watchdog.py:70-95` — 无单实例锁（`_watchdog.pid` 从不被代码读写）；`find_listener_pid()` 用 `":7863" in line` 子串匹配可能误命中；RESTART_COOLDOWN=30s 若网关冷启动 >30s 会进入 kill-循环。建议 pid 文件互斥、匹配行尾端口、按 /health 就绪判定。

### P2-3 `_gen_run` CPU fallback 完成后无条件切回 CUDA，与并发模式切换互踩【真实可达】
- `app.py:817-822` — 无锁覆盖 `backend` 状态；`_engine_state_write` 本身也是无锁 read-modify-write（L157-164）。

### P2-4 score 临时文件依赖 CWD，且强杀后残留【真实可达】
- `app.py:322-323` — `tmp_dir = Path("tmp/score")` 相对 CWD；watchdog 强杀时残留；`_SCORE_JOBS` 纯内存，重启后前端轮询不恢复。

### P2-5 批量 worker "先标 running 再等 GPU 闸门"的状态欺骗【真实可达】
- `app.py:1042-1045` 先置 `running`+写盘，L1070 才 `with _GPU_SEM`；长排队期间显示假"生成中"，与 batch_stop 语义冲突。建议 acquire 之后再置 running。

### P2-6 mcp_server 跳过删除确认【真实可达】
- `mcp_server.py:66/72` — 直接传 `_confirmed=True` 绕过确认装饰器；非审批型 MCP 客户端接入即静默删除。建议走两段式。

### P2-7 `ai_lab` 与 `ai_router` 双实现漂移；`settings.DATA_DIR` 不存在【真实可达（潜伏）】
- `ai_lab.py:24` — settings 无 `DATA_DIR`，相对路径 `"data"`；`ai_lab.chat_turn` 死代码但仍在维护；双份工具 schema 已见 `count: 1-5` vs app.py `min(20)` 漂移。

### P2-8 死代码 / 未接线模块【抽样确认】
- `audiocpp.py`、`chunking.py` 全项目无 import；`sheetsage_pt.py` 的 `unload()` 调用包在裸 `except: pass` 中，接口漂移被静默吞掉，显存卸载可能没发生。

## P3（低）

- **P3-1** `asr.py` 资源清理到位；✅
- **P3-2** `denoise.py:82-133` — `torch.load` 未 `weights_only=True`（本地固定 ckpt，理论风险）。
- **P3-3** `voices.py:42-46/104-107` — `index.json` 读改写无锁；`app.py` 模板 `_load_templates/save`（L1811-1815）无锁。
- **P3-4** XSS 抽查：动态值基本过 `escapeHtml`；未发现可达注入点；`client.js`、`ui-panel.mjs` 无 innerHTML/eval。**XSS 维度：未发现可达漏洞**。
- **P3-5** `app.py:1361/1618` 上传整体 `await file.read()` 进内存再判 200MB，可致内存峰值；建议流式写盘限额。
- **P3-6** 根目录工程卫生：运行时垃圾文件混入版本根；legacy 漂移未清理（抽样）。

## 逐维度覆盖表

| 维度 | 结论 | 关键证据 |
|---|---|---|
| 1 并发/线程安全 | **不合格** | P1-2、P1-4、P2-2/3、P3-3；正面：`_GPU_SEM` 与 `_RVC_LOCK/_BATCH_LOCK` 基本正确，但读-改-写落盘多处非原子 |
| 2 错误处理与资源泄漏 | 基本合格，局部缺陷 | asr/denoise 清理到位；P2-4、P2-8；子进程均有 timeout ✅ |
| 3 安全 | **不合格** | P0-1、P1-3、P1-5、P2-1、P2-6；子进程列表参数普遍 ✅；XSS 未发现可达点 |
| 4 稳定性 | 中等 | 三重懒清理兜底 ✅；但被 P1-1 误触发；`_SCORE_JOBS` 重启丢失；watchdog 冷却期风险 |
| 5 可维护性 | 有明显漂移 | P2-7、P2-8 |

**P0：1 项；P1：5 项。** 建议修复顺序：P0-1 → P1-1 → P1-3 → P1-2/P1-4 → P1-5。
