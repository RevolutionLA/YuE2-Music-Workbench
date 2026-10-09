# 端到端三方对抗式审计报告(@ 533431f)

> 方法:蓝军(敌意审查)→ 第三方独立复核 → 中立裁定,三方均为同模型不同角色的静态代码审查,未启动服务(运行时依赖项已标注"需实测")。
> 覆盖维度:功能、安全性、可靠性、性能、UI/主题、测试真实性。
> 完整三方报告见仓库内 `docs/review/BLUE-TEAM-REVIEW-533431f.md`(本地)。

## 结论摘要

- 本基线 **无 P0**;唯一 **P1** 实锤为 **B-T1(MCP/dsh 的 AI 生成工具 100% 失败)**。
- 蓝军共报 14 条,其中 4 条被第三方/裁定方证伪驳回(S-2、R-2、B-T2、B-T4)——现状:路径穿越、命令注入、CSRF、subprocess 卫生等高危面已有防御与测试覆盖,质量好于典型本地工具。

---

## P1 — 功能缺陷

### B-T1 · MCP/dsh 的 AI 生成工具每次调用必失败
- **位置**:`src/ai_tools.py:62-63` + `app.py:2281`
- **证据**:`tool_generate` 取 `r["job"]["id"]`,而 `generate_start` 现在返回 `"job": None`(注释明示"响应里不再 _make_job(1):那会凭空多烧一个 ID")→ `TypeError: 'NoneType' object is not subscriptable`。MCP generate(`mcp_server.py:26-30`)与 dsh bridge(`dsh-plugin/src/bridge.mjs:14-16`)均走此函数,无绕行路径。ai_lab 路径下异常被吞成 `{"error": "TypeError..."}` 回给 LLM。
- **后果**:AI 侧(自然语言)提交生成 100% 失败,且错误文案对用户无解释性。
- **建议修复**:`job_id = (r.get("job") or {}).get("id")`,或让 `generate_start` 对非 HTTP 调用路径返回真实 job(如 `{"job": {"id": rid, ...}}`)。一行级修复。

## P2 — 性能

### P-1 · 音频端点整文件读进内存
- **位置**:`app.py:2330`(`history_audio`)、`app.py:1611` 同款
- **证据**:`Response(content=fp.read_bytes(), ...)` 把整首 wav 读入内存再返回;库内 `app.py:8362` 已有 `FileResponse` 先例,属不一致。
- **后果**:LAN 多人并发听歌时内存峰值线性叠加(触发依赖 LAN 模式,默认单机影响小)。
- **建议**:改用 `FileResponse`(自带 range 支持,顺带解决拖动进度条)。

## P3 — 安全 / 可靠性

### S-1 · LAN 模式下网关 0.0.0.0 绑定 + 全 API 无鉴权(改级 P2→P3)
- **位置**:`app.py:186-222`、`settings.py:22`
- 已有三重闸门:`YUE2_ALLOW_LAN=1` + `YUE2_LAN_HOSTS` 白名单 + 启动日志明示风险。残余风险:用户手动放行防火墙后,7863 成为零鉴权入口(可删模型/停任务/占 GPU)。
- **建议**:文档显著位置提示"LAN 模式务必配合反代/Token";或为写操作增加可选共享密钥。*(需实测:防火墙策略实际是否恒不放行 7863)*

### S-3 · history_delete/clear 的文件名无 containment 校验
- **位置**:`app.py:1614-1617`(及 :1596、:1631)
- **证据**:`(HIST_DIR / i.get("file", "")).unlink(missing_ok=True)` 直接信任 history.json 条目里的 `file` 字段(对比同函数对 rid 做了 `os.path.basename`)。正常流程文件名自产,但 history.json 被篡改即可删任意文件。
- **建议**:与 rid 同样加 `os.path.basename` + resolve 包含性检查(防御纵深,一处小改)。

### R-1 · 看门狗长任务免死窗口可能推迟击杀(改级 P2→P3)
- **位置**:`watchdog.py:60-64, 145-164`
- 免死需四条件同时成立(短探/深探失败 + 端口有监听者 + 免死文件 360s 内有 mtime);网关真死时端口无监听 → 照常重启。残余:网关挂死但训练子进程仍在写日志的窄态。
- **建议**:可接受现状;若要收紧,可将免死窗口改为"文件 mtime 新鲜 + 进程 CPU 有增量"双条件。*(需实测停机演练)*

### B-T3 · dsh token 明文落盘 `_dsh_web.log`
- **位置**:`src/ai_router.py:94-101`
- token 明文写日志文件,任何能读该文件的本地进程可取用。
- **建议**:日志里脱敏(只写端口不写 token),或用受 ACL 保护的文件。

### B-T6 · 长消息整段作 argv 传 node,超 Windows ~32K 命令行上限
- **位置**:`src/ai_router.py:260-264`
- 用户单条消息 >约 32K 字符时 CreateProcess 直接失败,报 502"dsh 内核失败",误导排查。
- **建议**:消息经临时文件/stdin 传递;或超限时前置校验并给出可读错误。

### B-T7 · 启动自动续跑训练(行为告知)
- **位置**:`app.py:8227-8323`
- 三道闸齐全(30 分钟窗口/最多 2 次/不碰他实例),但"重启网关会自动复活 GPU 训练数小时"可能违背用户预期(如半夜维护)。
- **建议**:README/设置页明示;或加环境变量 `YUE2_AUTO_RESUME=0` 开关。

### B-T5 · MCP 删除工具一轮 `confirm=true` 即真删(需实测)
- **位置**:`mcp_server.py:63-72`
- 两段式确认依赖调用方布尔参数,LLM 自己就能传 confirm=true;保护层依赖 dsh 审批 UI 是否逐次弹窗(静态不可证)。
- **建议**:实测 dsh 审批行为;若不弹,考虑对删除类工具强制二次人工确认。

## P3 — 前端 / UI

### C-T2 · 5s 队列轮询全量 fetch 1000 条历史(渲染仅 60 行,原表述已修正)
- **位置**:`static/index.html:5696-5731` + `app.py:2321`
- 队列 running 且停留历史页时,每 5s 全量拉取 `/api/generate/list`(上限 1000 条 JSON)但默认只重建 60 行 DOM。长跑任务时有持续网络/解析开销。
- **建议**:`/generate/list` 支持 `?limit=` / 增量 since 参数,轮询时只取头部。

### U-1 · 深色主题 20+ 处硬编码色是回归温床
- **位置**:`static/index.html:162-166, 514, 645-646, 913, 1367` 等
- 两套主题变量集完整、`test_design_system.py` 有对比度断言(纪律好),但散落的深色硬编码覆盖意味着新增组件漏写 dark 覆盖即出现"浅色可读、深色不可读"。
- **建议**:逐步收敛硬编码色到 CSS 变量;可在 `test_design_system.py` 加"禁止新增硬编码十六进制色"的 lint 断言。

## P3 — 测试

### F-1/T-1 · 核心业务链路零测试覆盖 + stub 假绿风险
- **位置**:`tests/test_review_fixes.py:76-88` 等
- 测试本身断言真实行为、非永绿;但 `/generate/start`、`/rvc/convert`、`/rvc/train` 被注释显式排除("绝不触发任何计算"),ffmpeg/CLI 探测被 stub,子进程真实链路无回归网。
- **建议**:增加一个可选的"冒烟层"测试(标记 skipif 无 GPU/模型),至少覆盖参数校验与任务登记路径;CI 里跑一次 pytest 确认无假绿。*(需实测跑 pytest)*

---

## 驳回记录(避免重复报)
- ~~S-2 CSRF~~:`_guard_local_only`(app.py:252-271)已拦带 Origin/Referer 的跨站写请求,且有测试覆盖。
- ~~R-2 pause 竞态~~:app.py:7841-7845 持 `RVC_TRAIN_LOCK`,terminate 有 try/except + taskkill 兜底。
- ~~B-T2 asyncio.run~~:当前两条调用链均无运行中事件循环。
- ~~B-T4 轮询只开不关~~:index.html:5666 已有 curTab 自关机制。

## 需实测项(静态无法终审)
1. B-T1 修复后的实际报错形态;2. dsh 审批 UI 是否逐次弹窗(B-T5);3. LAN 模式防火墙实际策略(S-1);4. 性能类(P-1/C-T2)压测定量;5. 全量 pytest 是否全绿。
