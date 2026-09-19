# 第三方独立审计报告 — YuE2（基线 b9548fe）

审计人：第三方（不采信蓝军报告，全部结论基于亲自读码）
未验证项见文末声明。

## A. 蓝军报告逐条复核

| 编号 | 结论 | 证据与修正 |
|---|---|---|
| **P0-1** 0.0.0.0 + 零鉴权 + token 回传 | **属实** | `settings.py:2`；`app.py:1924-1929`；`app.py:59-65` CORS `*`+credentials；`ai_router.py:50-99` token URL 回传任意调用者（L74、L97-98）。全 API 面无鉴权中间件。 |
| **P1-1** 桥子进程 import app 触发孤儿清理；generate_start async 被同步调用 | **属实** | `dsh-plugin/src/tools.mjs:81-83`；`bridge.mjs:14-16`；`ai_tools.py:42/55/68/82/119/131/138`；`app.py:1882`（L1844-1879）。`ai_tools.py:61` 对 async `generate_start`（app.py:870）同步调用 → `r["job"]`（L62）TypeError → 桥路径下 tool_generate 必然失败。**补充（蓝军未提）**：`ai_tools.py:98` 对 async `rvc_convert_by_rid`（app.py:1683）同类崩溃。 |
| **P1-2** generate_start TOCTOU | **属实** | `app.py:871-873` 检查无锁；`_gen_set_job` 最早在 `_chain_runner` 内 `_GPU_SEM` 获得后（L913→926）；`_GEN_LOCK`（L583/596）不保护 check-and-set。 |
| **P1-3** scores 路径穿越 | **部分属实（漏洞真实，利用姿势需修正）** | `app.py:361-365/369-373` 无防护属实。但 Starlette `scope["path"]` 是 %解码后的，`..%2F` 解码后不再匹配 `{score_id}` 的 `[^/]+`——**%2F 形式打不进**。真实可达形式是**反斜杠**：`GET /api/scores/..\..\data\records\history`（`\` 不分段，Windows 下穿越成立）。 |
| **P1-4** stop 竞态"复活" | **部分属实** | ① 批量快照回写竞态属实（L858-864 vs L1079-1088）。② 单发：`_gen_run` 在 L769/775/789 均检查 `_CANCEL_EVENT`，取消后多数路径提前 return；仅"检查后写盘前"窄窗口可复活，蓝军说成必然偏重。 |
| **P1-5** PowerShell 注入 + 密钥上命令行 | **部分属实，定级偏高（改 P2）** | `ai_router.py:78-90` 属实；但注入输入源是本机环境变量/固定路径，远程可达性低；密钥泄露属实。 |
| **P2-1** models_switch 写未净化 path | **属实（低危）** | `app.py:509/513/535`。 |
| **P2-2** watchdog 多实例/误匹配/冷却 | **属实** | `watchdog.py:36-47` 子串匹配；全文无 pid 文件读写；`RESTART_COOLDOWN=30`（L25）。 |
| **P2-3** CPU fallback 无锁切回 | **属实** | `app.py:817-822`；`_engine_state_write` L157-164 非原子。 |
| **P2-4** score tmp 相对 CWD / 内存任务表 | **属实** | `app.py:322`、`_SCORE_JOBS` L264。 |
| **P2-5** 先标 running 再等闸门 | **属实** | `app.py:1042-1045` vs L1070。 |
| **P2-6** mcp_server 绕过确认 | **属实** | `mcp_server.py:66/72`；`ai_tools.py:22-33`。 |
| **P2-7** 双实现漂移 / DATA_DIR | **属实** | settings 无 `DATA_DIR`；`ai_lab.py:24`；schema 漂移 `tools.mjs:19` count 1-5 vs `app.py:878` min(20)；`ai_lab.chat_turn`（L106-193）无人调用。 |
| **P2-8** 死代码 / unload 裸 except | **部分属实** | audiocpp.py、chunking.py 零引用属实。但 `sheetsage_pt.unload()`（L126-140）**真实存在且实现基本正确**，蓝军"卸载可能没发生"言过其实；仅 `app.py:746-749` 裸 except 掩盖未来漂移属实。 |
| P3-1~P3-6 | 抽查 | P3-5 属实（L317-321/1361-1365/1618-1622）；P3-4 XSS 结论与我方一致；P3-2/3 未逐条验证。 |

## B. 新缺陷清单（蓝军未发现，T-*）

### T-1【P1·功能性】`/api/rvc/convert/{rid}` 线程参数不足，换声转发必崩、任务永久 running
- `app.py:1723`：`threading.Thread(target=_rvc_convert_worker, args=(rid2, job, src))` 只传 3 参；`_rvc_convert_worker`（L1277-1279）要求 10 参 → 线程启动即 TypeError，daemon 静默死亡，`_RVC_JOBS[rid2]` 永远 running。对比正确调用点 L1384-1388。**该端点自引入即完全不可用。**

### T-2【P1·并发】`/api/generate/current` 懒清理把正在运行的换声/批量 meta 改判"中断"
- `app.py:934-949`：只要 `_GEN_JOB is None` 就把 output/*.json 所有 running 改 error——包括 RVC 换声与批量任务。前端常态轮询即触发，无需 AI 工作台参与。与 P1-1 同族，但触发者就是前端自身轮询。

### T-3【P2·稳定性】watchdog 在用户主动停服后无限复活网关，且可能击杀健康进程
- watchdog 无退出条件（L10 自述）；用户关服 → 3 次探测失败 → taskkill /F + 重新拉起，**服务关不掉**。`find_listener_pid` 在网络抖动时可击杀健康网关，打断 60-90 分钟生成任务。

### T-4【P2·安全】AI 工作台删除确认仅靠提示词，可被对话内容绕过
- `ai_router.py:145-146` confirm=True 仅拼提示语；LLM 可自行在 args 放 `_confirmed:true`（`needs_confirm` 包装器 `kw.pop("_confirmed")` 恰好消费），诱导即绕过二次确认。与 P2-6 同根。

### T-5【P2·稳定性】`/api/score/status` 权重缺失时 500 而非 ready:false
- `app.py:278-282`：`resolve_checkpoint()`（sheetsage_pt.py:83-85）找不到权重直接 raise，`or` 短路救不了 → 新装环境常态 500。

### T-6【P2·稳定性·高疑点】dsh runner 经 argv 传 ~40KB task，疑似超 Windows 32767 字符上限
- `ai_router.py:140-148` 拼 SYSTEM_PROMPT+历史；L163-166 以 `["node", runner, task]` argv 传子进程。超长 spawn 会失败/截断。未实测 dsh 是否内部改走 stdin。

### T-7【P3·可移植性】会话目录相对 CWD；`ai_prompt.py:12` 硬编码绝对路径 `E:/AI/10AIMusic/Yue/YuE2-skills`
- `ai_lab.py:24` 相对 `"data"`；换机后技能提示词静默退化（L47-50 except 吞掉）。

### T-8【P3·并发】`sheetsage_pt.get_model()` 非线程安全
- `sheetsage_pt.py:88-123` 无锁 check-then-load；并发 `/api/score` 双次加载模型进显存。

### T-9【P3·并发】批量/单发 stop 取消语义不一致
- `batch_stop`（L1166-1177）不 set `_CANCEL_EVENT`、不杀引擎；worker `_CANCEL_EVENT.clear()`（L1031）会清掉用户刚设的取消。

## C. 蓝军覆盖度稽核

1. **T-2 漏报主触发面**——蓝军只归因 AI 桥，未发现前端轮询即触发。
2. **T-1 diff 新增功能性缺陷完全没查**。
3. **T-4 确认机制可绕过**——蓝军只报 MCP 侧（P2-6）。
4. **T-3 watchdog 与停服意图冲突**。
5. **T-6 / T-5** ai_router chat 链路与 sheetsage 错误路径完全未触及。
6. 蓝军"错误处理/资源泄漏基本合格"结论偏乐观：score_status 未捕获异常、T-1 静默线程死亡均属该维度漏网。
- 抽查：`sheetsage_pt.py` 总体尚可；`ui-panel.mjs` 反代实现干净；`client.js` 无注入点。

## 未验证项声明

1. 无 shell 权限，未能运行 `git diff HEAD` 逐行比对未提交修改。
2. `main.pyd` 编译网关原生路由的鉴权行为未验证；P0-1 仅对 app.py 扩展路由与 ai_router 实证。
3. `denoise.py`（P3-2）、`voices.py`（P3-3）未逐行复核。
4. T-6 未实测 dsh 是否改走 stdin。
5. P1-3 结论基于 Starlette 路由语义推理 + 代码证据，未实际发请求验证。
