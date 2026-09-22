# ADJUDICATION — 基线 d216e02 未提交改动（app.py / static/index.html / dsh-plugin/lib/client.js）

裁定标准（P1=主路径功能失败/数据丢失且无兜底；P2=主路径可感知但自愈/有降级，或明显资源浪费/视觉缺陷；P3=观感/极边缘）。
适用前提：本地单用户 127.0.0.1 工具、单进程 uvicorn、dsh iframe 嵌入为主要使用方式。

## 一、共同前提核查

1. **414/请求行上限（A-1 前提）成立**：本环境未装 httptools，uvicorn 走 h11；`h11/_connection.py:58` DEFAULT_MAX_INCOMPLETE_EVENT_SIZE = 16KB（请求行+头）。前端截 20000 字符 URL 编码后必超 → 请求被拒且 catch 静默。
2. **dsh 嵌入为主场景成立**：client.js apply() 默认抢焦点切到音乐工作台 iframe（/lab/?embed=1）。
3. 例外：A-4"必遮挡"不成立——main 已有 `padding: 24px 28px 104px`，104px > dock 高约 52px，无遮挡。

## 二、逐条裁定

| 编号 | 原级 | 最终裁定 | 理由 |
|---|---|---|---|
| A-1 | P2 | 采纳 P2 | >16KB 请求行被 h11 拒且静默，中长乐谱摘要必失效 |
| A-2 | P2 | 采纳 P2 | 正则不匹配 `Q:1/4=120`；且本项目自产 ABC 就是该格式（checkpoints/notation_sheetsage2.py:1133） |
| A-3 | P2 | 采纳，改级 P3 | 仅影响摘要精度，多 tune 是边缘输入 |
| A-4 | P2 | **驳回** | main 的 104px 底部 padding 已预留，无遮挡 |
| B-1 | P1 | 采纳，改级 P2 | BPM 语义批量页失效属实，属结果不一致非主路径失败 |
| B-2 | P2 | 采纳，改级 P3 | 有 beforeunload 兜底 |
| B-3 | P2 | 采纳，改级 P3 | 已有 toast+常驻横幅告知完成 |
| C-1 | P1 | 采纳，改级 P2 | 单 worker 部署，最坏读端点闪一帧中间态，自愈 |
| E-1 | P1 | 采纳，改级 P2 | 纯 CPU 浪费无功能失败 |
| F-1 | P1 | 采纳，改级 P2 | 纯视觉不协调；跨 iframe 直写 data-theme 绕过 applyTheme 属实 |
| N-1 | P2 | 采纳 P2 | 修复删 .part 与续传机制矛盾，4GB 从零重下 |
| N-2 | P2 | 采纳 P2 | 与 F-1 同根 |
| N-3 | P3 | 采纳延后 | 顺手加 origin 校验即可 |
| N-4/N-5/N-6 | P3 | 不修/备注 | 无争议 |

## 三、修复优先级

**本批必修（5 项）**：A-1（analyze 改 POST）、A-2（Q: 正则）、N-1（repair 保留 .part）、B-1（批量页 BPM）、F-1+N-2（主题色联动，applyTheme 重算 + postMessage）。
**顺手修（低成本）**：C-1 写侧收锁、E-1 busy 节流、B-2 白名单加 bpm、N-3 origin 校验。
**不修**：A-4（驳回）、N-4/N-6、其余 P3。

统计：采纳 4 / 改级 7 / 驳回 1。
