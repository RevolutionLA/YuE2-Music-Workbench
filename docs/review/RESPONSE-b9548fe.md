# 整改与复验记录 — YuE2（基线 b9548fe）

依据：`BLUE-TEAM-REVIEW-b9548fe.md`（蓝军）、`THIRD-PARTY-REVIEW-b9548fe.md`（第三方）、`ADJUDICATION-b9548fe.md`（中立裁定）。

## 整改安全说明

用户当时有生成任务正在运行。凡需重启网关才生效的修改，均只落盘、**未重启任何服务/进程**，不影响运行中的任务；生效时机由用户在任务空闲后自行重启（启动 bat / watchdog 拉起均可）。

## 已修（重启后生效）

| 裁定编号 | 修复内容 | 位置 |
|---|---|---|
| T-1（P1） | `/api/rvc/convert/{rid}` 线程参数补全（原 3/10 参，线程静默崩溃、任务永久 running） | app.py rvc_convert_by_rid |
| P1-1（P1） | `generate_current` 懒清理加 `_ORPHAN_SWEEP_DONE` 一次性标志，前端轮询不再把运行中换声/批量任务误判"中断" | app.py |
| P1-2（P2） | `generate_start` check-and-set 原子化（`_GEN_LOCK` 内占位 job），消除 TOCTOU；占位 job 在 stop/_chain_runner finally 中正确清理，不产生 output/None.json | app.py |
| P1-3（P2） | scores 读/删接口路径穿越防护：basename + resolve 前缀校验（Windows 反斜杠形式） | app.py scores_get/scores_delete |
| P1-1 async 部分（P1） | ai_tools 同步调用 async 端点崩溃：`tool_generate`、`tool_rvc_convert` 改 `asyncio.run(...)` | ai_tools.py |
| P2-6+T-4（P2） | mcp_server 不再传 `_confirmed=True`，走两段式确认 | mcp_server.py |
| P2-2+T-3（P2） | watchdog：单实例 pid 互斥（_watchdog.pid）、端口匹配改行尾精确匹配（:78630 不再误命中）、kill 前排除自身 | watchdog.py |
| P1-5（P2） | dsh web 拉起改 `subprocess.Popen` + env 字典，去除 cmd/PowerShell 字符串拼接（注入面 + 密钥上命令行） | ai_router.py |
| T-5（P2） | `/api/score/status` 权重缺失时返回 ready:false 而非 500 | app.py score_status |

## 推迟项（理由）

- **P0-1（已部分整改）**：用户确认**仅本机使用**，已实施：`settings.py app_host` 改 `127.0.0.1`（不再暴露局域网）；CORS `allow_origins` 由 `*` 收紧为本机 7863/3081 白名单。重启生效。`/api/ai/web` 的 token URL 回传风险随绑定收紧降级为可接受（仅本机进程可达）。若日后需要 LAN 访问，须改回 `0.0.0.0` 并配套加鉴权中间件。
- P1-4/T-9/P2-5（stop/取消语义统一）：裁定为 P2/P3 显示语义问题，涉锁结构重构，推迟到下次专门迭代。
- P2-3/P2-4/P2-7/P2-8/P3-*：低危/卫生/死代码类，推迟（含 audiocpp.py、chunking.py 死代码清理——需先确认编译网关 main.pyd 无内嵌依赖）。
- T-6（argv 40KB 疑超限）、P1-3 实际可达性：裁定要求实测，未实测，维持"高疑点/待验证"。

## 复验记录

- `py_compile` app.py / ai_tools.py / ai_router.py / mcp_server.py / watchdog.py 全部通过。
- 回归脚本 `tmp/_regression_check.py` 通过（REGRESSION_OK）：路径净化（反斜杠/点点/编码穿越均被拦在 SCORES_DIR 内，正常 id 不受影响）+ watchdog 端口行尾匹配（:7863 命中、:78630 不命中）。
- 未验证：网关重启后的端到端行为（需等生成任务空闲，由用户执行重启后观察）。

## 需用户执行

1. 任务空闲后重启网关使上述修复生效（重启会触发启动孤儿清理，属预期行为）。
2. 确认 P0-1 部署环境，决定绑定/鉴权方案。
3. 若 `/api/ai/web` 在用，重启后留意 dsh web 拉起是否正常（启动方式已改造）。
