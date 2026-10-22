# 蓝军评审 · v10.21.6（本机 6 项优化）

> **降级模式声明**：本机子代理在 150 轮上限内未产出报告，本次蓝军/第三方/裁定由主代理按角色串行扮演。独立性弱于真正换 agent，结论仅代表同一模型的对抗性检查。

- 仓库：`C:\Users\HW\Desktop\YuE2\YuE2-Music-Workbench`
- 审查基线：`54e5503`（相对 `63ea6a4`）；工作区干净
- 修复提交：`35bc330`（进度/通知）、`0895d41`（RVC 时间戳）
- 必审维度：1 需求一致性、3 正确性、4 错误处理、7 安全、10 测试有效性、16 发布工程、17 文档一致性、18 UI/前端

## 缺陷总表

| 编号 | 级别 | 定级依据 | 一句话 | 维度 | 位置（文件:行号） |
|---|---|---|---|---|---|
| B1 | 🟠 | 实测 | 请求刚发出、尚无阶段日志时被误画成「NAR 第 1 步」，而不是「等待引擎」 | 3 正确性 | app.py:2584（54e5503） |
| B2 | 🟠 | 实测 | `/generate/current` 对非法 seed 调 `int()` 会抛 ValueError，接口 500 | 4 错误处理 | app.py:2565（54e5503） |
| B3 | 🟡 | 实测 | 通知标题在无 task_name 时仍拿 Style 首词兜底，正是用户抱怨的「English 失败」 | 1 需求一致性 | app.py:2717（54e5503） |
| B4 | 🟡 | 推理·待实测 | 可用功能清单把「歌曲生成」无条件标成可用，缺引擎/配置时会误导 | 17 文档一致性 | app.py:765 |
| B5 | 🟡 | 推理·待实测 | 预检用固定 7.0GB 预算 + 12 字/帧估算，独显/大内存 Vulkan 可能被误杀 | 3 正确性 | app.py:2854 |
| B6 | 🟡 | 实测 | RVC 成品与检查点同秒落盘时，mtime 分辨率会让「成品」试听退回检查点（测试间歇性红） | 10 测试有效性 | app.py:7302 |
| B7 | 🟡 | 推理·待实测 | NAR 步数是按时间估算，不是引擎真实进度；UI 虽标「约」，极端负载下偏差会更大 | 1 需求一致性 | app.py:2610 |
| B8 | 🟡 | 推理·待实测 | 菜单关闭只有浏览器手工验证，没有自动化 DOM 回归；后续改版可能悄悄回退 | 10 测试有效性 | static/index.html:6480 |
| B9 | 🟡 | 推理·待实测 | LRC/转谱能力只检查模块或单个权重文件，依赖缺一半时会显示可用 | 17 文档一致性 | app.py:780 |

## B1 · 🟠 请求刚发出时被误画成 NAR

`vae_m and ...` 不成立后，旧代码直接进入 `elif nar_at >= ar_at:`；当请求里还没有任何阶段行时两者都是 `-1`，条件为真，于是 `nar_m` 为空也照样按请求时间估算「NAR 第 1 步」。用户会看到阶段跳到了 NAR，而引擎其实还在准备。

**复现命令**

```bash
git show 54e5503:app.py | grep -n "elif nar_at >= ar_at"
```

输出：`2584:            elif nar_at >= ar_at:`

修复后回归：

```bash
py312/python.exe -m unittest tests.test_engine_progress.EngineProgress.test_request_without_stage_lines_is_queued_not_nar -v
```

输出：`OK`（1 test）。修复为 `elif nar_at >= 0 and nar_at >= ar_at:`，并保留 `ar_at >= 0` 的 AR 分支，最后才落到 `queued`。

## B2 · 🟠 非法 seed 让接口 500

`int(seed)` 直接比较；日志里 seed 是数字，但历史 meta / 批量 payload 的 `params.seed` 可能是字符串或空值。`/api/generate/current` 和 `/api/batch/status` 每 2–5 秒轮询一次，一次 500 会让整个任务页卡住。

**复现命令**

```bash
git show 54e5503:app.py | grep -n "int(seed) == req"
```

输出：`2565:        if seed is None or int(seed) == req["seed"]:`

修复后回归：

```bash
py312/python.exe -m unittest tests.test_engine_progress.EngineProgress.test_bad_seed_returns_none_instead_of_crashing -v
```

输出：`OK`。现在 `try int(seed) except (TypeError, ValueError)`，不匹配就返回 `None`，不抛错。

## B3 · 🟡 通知仍可能显示「English 失败」

`_task_label` 旧逻辑在 task_name/src_name/voice_name/model 都为空时取 `style.split(",")[0]`，与用户反馈的「English 失败」完全一致。修复为回退到任务 ID，不再拿曲风当任务名；有 task_name 的 31 首会显示真实歌名。

## B4 · 🟡 可用功能清单曾把生成无条件标成可用

`capabilities` 的生成项曾经写死 `True`；缺 `audiocpp_server.exe`/`cpp/server.json` 时用户会在设置页看到「可用」然后提交失败。修复为检查引擎二进制与配置，缺时显示原因。

## B5 · 🟡 预检是保守启发式

`_vulkan_heap_budget_gb()` 固定 7.0GB，帧数按去空白字符 ×12 估算。它只在 CPU 回退关闭时硬拦，且可通过 `YUE2_VULKAN_BUDGET_GB` 覆盖；但在 32GB 内存的 Vulkan 机器或独显上，可能把本来能跑的长歌误判为放不下。这是有意的「宁可早提示」，需在发布说明中保留为已知限制。

## B6 · 🟡 RVC 成品/检查点同秒落盘的测试红

`_rvc_train_preview` 用 `final.mtime >= ckpt[0].mtime` 判断成品是否盖过检查点；NTFS/网络盘同秒落盘时，刚写的成品可能比检查点“旧”零点几秒，测试间歇性拿到 `G_2333333.pth`。已用 2 秒容差吸收同批落盘误差。

**复现命令**

```bash
py312/python.exe -m unittest tests.test_review_fixes.TestRvcCheckpoints.test_preview_of_a_named_checkpoint_uses_its_own_cache -v
```

输出：`OK`（连续 5 次 + 全量均通过）。

## B7–B9 · 🟡 已知限制

- NAR 步数按 `帧数 × 0.065s/帧步` 估算，UI 标「约」；它不是引擎真实回调。
- 菜单关闭目前只有浏览器手工验证（打开两行→只留一个；点空白→0 个），没有自动化 DOM 测试。
- LRC/转谱能力只检查模块/单权重，缺一半依赖时会显示可用；这是能力清单的精度限制，不是功能回归。

## 维度覆盖声明

- 1 需求一致性：已查；发现 B3、B7。
- 3 正确性：已查；发现 B1、B5。
- 4 错误处理：已查；发现 B2。
- 7 安全：已查；设置端点沿用现有回环守卫，未发现新增越权（未做渗透测试）。
- 10 测试有效性：已查；B6、B8，新增回归 9 条。
- 16 发布工程：已查；版本 v10.21.6，671 条测试全绿，提交未推送。
- 17 文档一致性：已查；B4、B9。
- 18 UI/前端：已查；浏览器验证菜单互斥/失焦、设置开关、TXT 按钮；发现 B8。

## 优先级路线图

- P0（发布前）：无。B1/B2 已在 `35bc330` 修复并有回归；B6 已在 `0895d41` 修复。
- P1：B5 预检误杀风险（发布说明已写明，可后续按真实 GPU heap 查询改进）。
- P2：B7/B8/B9 的精度与自动化覆盖。

## 做得对、值得保持的

- 预检在启动引擎前执行；实测长歌 4 秒失败且 `:8080` 无监听。
- 设置优先级（显式设置 > env > 默认）有单测；CPU 回退开关与预检 warning 的交互有测试。
- 菜单用全局委托 + `closeHistoryMenus`，不依赖每行重复绑定；TXT 导出纯前端，失败任务也能用。

## 未验证项

- 没在真实长歌上复验「CPU 回退开启时预检 warning → GPU 失败 → CPU」的完整链路（只做了单元测试）。
- 没有在独显 Vulkan / 32GB 机器上验证预检阈值。
- Windows 通知的实际弹窗文案只审了代码路径，没有逐条触发系统通知。
