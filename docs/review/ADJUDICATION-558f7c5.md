# 三方对抗式评审报告 — 基线 558f7c5

> ⚠️ **降级模式声明**：本次评审因宿主 `task` 子代理工具 scope 参数故障，无法派出独立子代理，
> 由**同一个 agent 串行扮演蓝军 / 第三方 / 中立裁定**三角色。独立性已显著削弱，
> 本报告不构成"独立第三方验证"，仅为同模型不同角色的结构化对抗审查。

## 结论摘要

前次评审（基线 b9548fe，见同目录 BLUE-TEAM-REVIEW-b9548fe.md）的 **P0 × 1 已全部修复，P1 × 5 已修复 4 项**，
残留 1 项（P1-1 半修复）+ 部分 P2 隐患。本轮无新增 P0/P1。

## 已验证修复（第三方逐条复核通过）

| 旧编号 | 结论 | 当前证据 |
|---|---|---|
| P0-1 服务绑 0.0.0.0 + CORS 全开 | ✅ 已修 | settings.py:2 `app_host="127.0.0.1"`；app.py:97-100 CORS 白名单仅本机两个端口 |
| P1-2 generate_start TOCTOU | ✅ 已修 | app.py:957-963 `_GEN_LOCK` 内 check-and-set，先占位再放线程 |
| P1-3 乐谱接口路径穿越 | ✅ 已修 | app.py:438/447 `basename` + `resolve()` 前缀校验 |
| P1-5 cmd/PowerShell 字符串拼密钥 | ✅ 已修 | ai_router.py:78-92 改 `env` 字典 + 列表参数 Popen，注释明确说明动机 |
| P1-1 后半：`asyncio.run` 调协程 | ✅ 已修 | ai_tools.py:62 `asyncio.run(gw.generate_start(payload))` |

## 仍存在的问题（本轮裁定清单）

### A1（P1，残留）孤儿清理在模块导入时执行，dsh 桥子进程仍会误判任务中断
- 证据链：app.py:2000 `_orphan_cleanup_on_startup()` 模块级执行；ai_tools.py:42/56/69/84… 共 8 处 `import app as gw`。
- 后果：生成进行中调用任意 AI 工具 → 桥子进程 import app → output/*.json 中 `running` 被改写为 `error`，与真实完成态竞态。
- 裁定：**采纳，立即修**——清理逻辑移到 `__main__`/启动入口，模块导入不再触发。

### A2（P1-4 残留，未完全验证）stop 与批量 worker 的读改写竞态
- 证据链：app.py:946 附近 stop 路径调用 `_batch_store(state)`；`_BATCH_LOCK` 存在（app.py:1112-1122）但 946 行上下文是否持锁未逐行验证。
- 裁定：**采纳为验证+修复项**，整改时一并处理。

### A3（P2 组，择要）仍存在的已知隐患
1. watchdog.py 无单实例锁，`_watchdog.pid` 从不被代码读写（watchdog.py:70-95）。
2. 死代码：`audiocpp.py`、`chunking.py` 全项目无 import；根目录运行时垃圾文件（`_push3.log`、`_watchdog.pid`）混入工程根。
3. `_SCORE_JOBS` 纯内存，重启丢失；score 临时目录相对 CWD。
4. mcp_server.py 直接 `_confirmed=True` 绕过删除确认。
5. 上传接口整体 `await file.read()` 进内存再判 200MB 限额。

### 未验证项
- P1-4 中 `generate_stop` 全链路（stop → worker 复活）的完整时序未实测（需真实生成任务）。
- rvc/、checkpoints/ 第三方/模型代码本轮未审（范围外，沿用上轮结论）。
- XSS：沿用上轮"未发现可达注入点"结论，本轮未复扫。

## 整改优先级
A1（立即）→ A2（验证+修）→ A3-2（工程卫生，随根目录瘦身任务执行）→ 其余 A3 记录为待办。
