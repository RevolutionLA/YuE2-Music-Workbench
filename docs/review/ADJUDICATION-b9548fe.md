# 中立裁定报告 — YuE2（基线 b9548fe）

裁定人：中立裁定方（独立抽查代码验证双方证据后逐条裁定）

## ① 逐条裁定表

| 编号 | 双方主张 | 最终定级 | 裁定 | 理由 |
|---|---|---|---|---|
| P0-1 | 0.0.0.0+零鉴权+CORS `*`+token 回传 | **P0（有条件）** | 采纳 | settings.py:2、app.py:59-65、ai_router.py:59-98 证据链完整。真实风险取决于部署环境（见②-2）：纯本机/可信 LAN 实为 P1，公网/不可信 LAN 为 P0。待用户确认。 |
| P1-1 | 桥子进程 import app 触发孤儿清理 + async 同步调用崩溃 | **P1** | 采纳 | bridge.mjs:14-16、ai_tools.py 多处 `import app`、app.py:1882 模块级孤儿清理均属实；async 崩溃（ai_tools.py:61、:98）核实成立，一并采纳。 |
| T-2（并入 P1-1 族） | `/generate/current` 懒清理把运行中 RVC/批量 meta 改判中断 | **P1** | 采纳（第三方新发现） | app.py:938-949 核实。触发面比蓝军所述更宽：`_gen_set_job` 在 L926 才首次调用，排队窗口内前端轮询即误伤，不止重启后。 |
| P1-2 | generate_start TOCTOU | **P2** | 改级（P1→P2） | 检查无锁属实，但 `_GPU_SEM` 串行化保护下无资源冲突，仅状态错乱。 |
| P1-3 | scores 路径穿越 | **P2** | 采纳（含第三方姿势修正+降级） | 无防护属实；`..%2F` 打不进路由，Windows 反斜杠形式可穿越；后果限于读/删任意 .json，降 P2。 |
| P1-4 | stop 竞态"复活" | **P2** | 改级（P1→P2） | 竞态属实，但 worker L1082 复核 cancelled + `_CANCEL_EVENT` 多点拦截，"复活"仅窄窗口，主为状态显示问题。 |
| P1-5 | PowerShell 拼接 + 密钥上命令行 | **P2** | 采纳第三方改级 | 属实但无远程可控输入，注入不可远程触发；密钥本机可见属实。 |
| P2-1 | models_switch 写未净化 path | **P2（低）** | 部分驳回/弱化 | 净化已存在一半（basename 校验+存在性检查），残余风险仅引擎越界加载失败，维持 P2 低。 |
| P2-2 | watchdog 多实例/误匹配/冷却 | **P2** | 采纳（与 T-3 合并整改） | 无 pid 互斥、子串匹配、冷却期风险均核实属实。 |
| P2-3 | CPU fallback 无锁切回 CUDA | **P2** | 采纳 | `_engine_state_write` 无锁 RMW 属实。 |
| P2-4 | score tmp 相对 CWD / 内存任务表 | **P2** | 采纳 | 属实。 |
| P2-5 | 先标 running 再等闸门 | **P3** | 改级（P2→P3） | 显示语义问题，与 T-2 同源，单列影响小。 |
| P2-6 | mcp_server `_confirmed=True` 绕过确认 | **P2** | 采纳（与 T-4 同根合并） | 属实。 |
| P2-7 | ai_lab/ai_router 双实现漂移 | **P3** | 改级（P2→P3） | 纯潜伏性维护问题。 |
| P2-8 | 死代码 / sheetsage unload 裸 except | **P3** | 改级+部分驳回 | **驳回蓝军"显存卸载可能没发生"**——`unload()`（sheetsage_pt.py:126-139）真实存在且实现正确；裸 except 隐患属实；死代码核实。整体 P3。 |
| P3-2 | torch.load 无 weights_only | **P3** | 采纳 | 本地固定 ckpt，理论风险。 |
| P3-3 | voices/templates 无锁 RMW | **P3** | 采纳 | 结构性成立。 |
| P3-4 | XSS 未发现可达点 | — | 双方一致，结论采纳 | 无需改动。 |
| P3-5 | 上传整体 read 进内存 | **P3** | 采纳 | 属实。 |
| P3-6 | 工程卫生 | **P3** | 采纳（抽样） | 维持。 |
| T-1 | `/rvc/convert/{rid}` 线程参数不足必崩 | **P1** | 采纳（第三方新发现） | 亲自核实：app.py:1723 仅传 3 参 vs worker 签名 10 参，线程静默死亡、任务永久 running。历史页"转发换声"自引入即坏。 |
| T-3 | watchdog 用户停服后复活网关 | **P2** | 采纳（与 P2-2 合并） | 无退出条件核实。 |
| T-4 | 删除确认仅靠提示词可绕过 | **P2** | 采纳（与 P2-6 同根） | `_confirmed` 可由 LLM args 传入被消费，核实。 |
| T-5 | score/status 权重缺失时 500 | **P2** | 采纳 | `resolve_checkpoint()` raise 则 or 短路失效，属实。 |
| T-6 | dsh runner argv 传 ~40KB task 疑超限 | **P3（需实测）** | 采纳降级 | CreateProcess 32K 限制成立，但实际长度未实测。 |
| T-7 | 会话目录相对 CWD + ai_prompt 硬编码绝对路径 | **P3** | 采纳 | 换机静默退化。 |
| T-8 | sheetsage get_model 非线程安全 | **P3** | 采纳 | 无锁 check-then-load 属实。 |
| T-9 | 批量/单发 stop 取消语义不一致 | **P3** | 采纳（与 P1-4/P2-5 一并修） | L1031 clear 会清掉用户取消，属实。 |

**最终计数：P0 × 1（待部署环境确认）、P1 × 3（P1-1、T-2、T-1）。**

## ② 共同前提与需实测项

1. **"生成任务正常运行中"前提**：凡涉及 `_orphan_cleanup_on_startup` 移位、锁结构、CORS、绑定 host 的修改均需重启网关，且**重启本身会触发孤儿清理**——正在跑的 60-90 分钟任务会被改判中断。整改必须在任务空闲窗口执行。
2. **P0-1 部署环境未验证**（双方共同盲区）：需确认 `netstat -ano | findstr 7863` 可达地址 + 是否有公网映射。公网可达则维持 P0 立即整改；纯本机可降 P2。dsh web（:3081）绑定面一并确认。
3. **需实测项**：
   - P1-3：`curl "http://127.0.0.1:7863/api/scores/..%5C..%5Cdata%5Ctest"` 与原生 `\` 形式各一次，验证是否读到 SCORES_DIR 外文件；`..%2F` 应 404。
   - T-6：构造满载 task 直接 `subprocess.run(["node", runner, task])` 观察 returncode；或查 runner.mjs 是否走 stdin。
   - P1-1/T-2：生成中调用 AI 工具/刷新前端，观察 output/*.json 是否被改判。

## ③ 整改安全分级

**可热改（不重启）**：ai_prompt.py 路径（T-7）、ai_lab.py:24 路径、denoise weights_only（P3-2）、死代码清理（P2-8）、schema 漂移对齐（P2-7 文档部分）。

**必须等任务空闲后重启生效**：P0-1（host/鉴权/CORS/token）、T-2+P1-1（孤儿清理触发面）、T-1、P1-2/P1-4/T-9（锁与取消语义）、P2-2+T-3（watchdog）、P2-6+T-4（确认机制）、P2-3/P2-4/T-5/T-8、P1-5。

**需用户确认后实施**：P0-1 最终方案（127.0.0.1 vs 鉴权中间件）。

## ④ 最终修复优先级（前 10）

1. P0-1 绑定/鉴权/CORS/token 回传 — 需重启，方案待用户确认
2. T-1 rvc_convert_by_rid 线程参数（一行修复）— 需重启
3. T-2 + P1-1 孤儿清理触发面（含懒清理）— 需重启
4. P1-1(async 部分) ai_tools 同步调 async 崩溃（含 :98）— 需重启
5. P1-3 scores basename + resolve 前缀校验（一行修复）— 需重启
6. P1-4 + T-9 + P2-5 stop/取消语义统一 — 需重启
7. P1-2 generate_start check-and-set 加锁 — 需重启
8. P2-2 + T-3 watchdog pid 锁/退出条件/就绪判定 — 需重启 watchdog
9. P1-5 + P2-6 + T-4 dsh 启动改 env 字典、确认两段式 — 需重启
10. P2-1(残余)/P2-3/P2-4 — 需重启
