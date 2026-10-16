# 蓝军审查报告 — YuE2 音乐工作台 @ d08c518 (master) · G3 测试有效性与发布诚实度

- 被审对象：`E:\AI\10AIMusic\Yue\yuE-2\yuE-2`，评审基线 = tag `v10.16.0` / commit `d08c518`（附注标签 taggerdate 2026-10-16 12:03:44 +0800）。
- 审查方式：**变异测试（在只读 worktree 副本里破坏代码后跑全量测试）+ 静态核对 + 只读 GET 接口 + 落盘数据普查**。主工作树**一个字节都没改**（见文末「纪律自证」）。
- 变异载体：`git worktree add --detach C:\Users\59221\AppData\Local\Temp\qoder\wt-mut d08c518`，每轮变异后用 `git checkout -- .` 还原；用毕 `git worktree remove --force`，`git worktree list` 现只剩主树。
- 范围红线：工作树里另一轮会话的未提交在制品（`app.py` 的 `_score_save(title=...)`、`src/abc_edit.py`、`tests/test_abc_edit.py`、`static/index.html` 两处 `s.title`）**不作为缺陷**，评审本体一律读 `git show d08c518:<path>` 的导出副本（`C:\Users\59221\AppData\Local\Temp\revG3\`）。本报告唯一一次用到这批改制品，是证明「534」这个测试数属于工作树而不属于标签（T-3）。

## 环境类假失败基线（先记账，后面对比只用「新增」失败）

worktree 缺仓库内未入库的 `py312/`、`runtime/`、models，因此基线就有 26 条环境类失败：

```
$ cd wt-mut && py312/python.exe -m unittest discover -s tests
Ran 497 tests in 52.172s
FAILED (failures=23, errors=3, skipped=1)
```

26 条全部集中在 `test_server_sources.py`（TestConvertFromServerSource / TestTrainFromServerSource）与 `test_review_fixes.py`（TestRvcSubmitGate / TestRvcTrainCheck / TestTrainClean / TestRvcSerialQueue），报错统一是 `{"detail":"rvc python 环境缺失（py312/python.exe）"}` → 环境类，不是产品缺陷。台账里每一行都是**与该 26 条集合做 diff 后的「新增失败」**。

对照：主工作树全量（同一 py312）实测 `Ran 534 tests … OK`（未改任何文件）。

---

## 一、变异测试台账（本报告骨架）

「原值→新值」均为 d08c518 那一版代码里的真实文本。结论口径：**已锁** = 至少一条测试因该变异变红；**未锁** = 全部 497 条照常通过（即这项修复没有测试保护）。

| 编号 | 改了哪一行（文件:行） | 原值 → 新值 | 变红的测试（全名） | 红几条 | 结论 |
| --- | --- | --- | --- | --- | --- |
| M1 | `static/index.html:5485` | `return (curTab === "scoreSing") ? ($("abc")\|\|{value:""}).value : "";` → `return ($("abc")\|\|{value:""}).value;`（七里香那条修复改回旧行为） | `test_page_contract.PageIndependence.test_generate_page_never_carries_a_score` | 1 | **已锁**（仅 1 条静态断言） |
| M2 | `app.py:2975` | `str(t.get("cot") or "full")` → `str(t.get("cot") or "off")` | `test_submit_paths_agree.TestSubmitPathsAgree.test_batch_cot_default_matches_single` | 1 | **已锁**（文本级） |
| M2b | `app.py:2975` | 保留被 grep 的那串，改语义：`_resolve_cot(str(t.get("cot") or "full") if abc_t else "off", abc_t)` | — | **0** | **未锁**（行为层没锁，只锁了字面量） |
| M3 | `app.py:895-896` | `note = ("规划 CoT 收到无法识别的值 %r…" % …) if mode else ""` → `note = ""`（`_resolve_cot` 恢复静默兜底） | `TestRewritesLeaveATrace.test_unrecognized_cot_says_so`、`test_two_notes_are_composed_not_dropped` | 2 | **已锁**（真函数调用，功能级） |
| M4 | `static/index.html:5394` | `if (fillIfEmpty($("style"), job.style)) filled.push("曲风");` → `$("style").value = job.style; filled.push("曲风");`（刷新恢复无条件覆写） | `TestRefreshDoesNotClobberInput.test_no_unconditional_overwrite` | 1 | **已锁**（枚举式） |
| M20b | `static/index.html:5403` 处新增 | 新增 `$("taskName").value = job.task_name \|\| ""; $("gender").value = "clear male vocal";`（对没被枚举的格子无条件覆写） | — | **0** | **未锁**（枚举漏项，见 T-7） |
| M5 | `app.py:2548` | `"cot_note": str(cot_note or "")[:400],` → `"cot_note": "",`（单首任务记录不留痕） | `TestRewritesLeaveATrace.test_cot_note_reaches_the_record_and_the_api` | 1 | **已锁**（文本级） |
| M7 | `app.py:2889` | `"cot_note": str(nxt.get("cot_note") or "")[:400],` → `"cot_note": "",`（队列跑完写 meta 时丢痕） | `test_cot_note_reaches_the_record_and_the_api` | 1 | **已锁**（文本级） |
| M18 | `app.py:2992` | 批量条目 `"cot_note": cot_note,` → `"cot_note": "",` | — | **0** | **未锁** |
| M16 | `app.py:2977-2980` | 删掉批量入口的 `_, ending_note = _ensure_song_ending(lyrics)` 与它的拼接（批量不再说"补了 [Outro]"） | — | **0** | **未锁** |
| M17 | `app.py:2985-2987` | `style, tempo_note = _style_yield_to_abc(style, abc_t)` 保留、但删掉 `if tempo_note:` 的说明拼接 | — | **0** | **未锁** |
| M8 | `static/index.html:5966` | ⑪ 历史条目里那行 `${h.cot_note ? \`…🛈 …\` : ""}` 整段删掉 | `TestRewritesLeaveATrace.test_frontend_shows_the_note_in_history` | 1 | **已锁**（文本级） |
| M8b | `static/index.html:5966` | 保留 `h.cot_note` 字面量、只把条件改成永假：`${false && h.cot_note ? \`<div class="h-sub"…` | — | **0** | **未锁**（⑪ 那半条链路只锁了字符串存在，见 T-1） |
| M9 | `static/index.html:5421` | `if (…) runningSeedActual = p.seed;` → 恢复 `$("seed").value = String(p.seed);`（种子重新固化） | `PageIndependence.test_random_seed_does_not_become_a_standing_choice` | 1 | **已锁** |
| M10 | `app.py:1406-1409` | `_unified_task` 的透传白名单去掉 `"cot_note"` | `test_cot_note_reaches_the_record_and_the_api`（`assertEqual(out.get("cot_note"), …)`） | 1 | **已锁**（功能级） |
| M11 | `static/index.html:5687` | `if (curTab === "scoreSing") $("cot").value = want;` → 去掉守卫（③ 又替 ②⑤ 改档位） | `PageIndependence.test_score_page_never_writes_generation_cot` | 1 | **已锁**（整句文本匹配，见 T-9） |
| M12 | `app.py:728-730` | `"loaded_vae": (… + "（" + …get("loaded_vae_from") + "）")` → `"loaded_vae": _engine_state_read().get("loaded_vae", "")`（⑬ 不再报 vae 来源） | — | **0** | **未锁**（见 T-6） |
| M13 | `static/index.html:4073` | `b.textContent = wordZh(v);` → `b.innerHTML = "<b>" + escapeHtml(wordZh(v)) + "</b><i class=eq>" + escapeHtml(v) + "</i>";`（词芯片重新挂 `<i>`，英文重新可见） | — | **0** | **未锁**（见 T-1/T-9） |
| M19 | `app.py:4299` | `v = float(dflt if raw is None or raw == "" else raw)` → `float(0.75 if raw is None …)`（Form(None) 那套缺省被参数层吞掉） | — | **0**（注：覆盖该入口的 8 条 TestRvcSubmitGate 用例在本副本属环境类失败，见 T-8 的补充判据） | **未锁**（判定同时依据用例普查：`tests/*.py` 里对 `_rvc_convert_params` 的 6 处调用全部传显式数值，无一处传 None） |
| M2R | 同 M2（工具复验，防止目录事故污染结论） | 同 M2 | `test_batch_cot_default_matches_single` | 1 | 复现一致 |

**台账结论**：20 组变异覆盖本轮 4 处点名的修复 + 6 处我认为最关键的链路。**四处点名修复都有测试变红（4/4 已锁）**；但 6 处关键链路里 **7 项（M2b / M8b / M12 / M13 / M16 / M17 / M18 / M19 / M20b）零条变红 = 未被锁住**，其中 5 项恰好是 CHANGELOG 用「每一个 / 统一 / 报出 / 恒定」这种全称口径承诺的东西。已锁的里面又有 5 项（M1 / M2 / M4 / M5 / M7 / M8 / M11）只靠**字符串字面量**，属于「文本锁」而非「行为锁」。

---

## 二、逐条发现

### 测试有效性

- **T-1 [P1][测试未锁][static/index.html:5966 / tests/test_submit_paths_agree.py:87]** 「⑪ 的历史条目直接显示这条说明」只锁到「`h.cot_note` 这串字符在文件里出现过」，说明本身能不能画出来没有任何测试。
  证据：M8（整段删除）红 1 条；M8b（保留字符串、条件改永假）**红 0 条**。`test_frontend_shows_the_note_in_history` 全文只有一句 `self.assertIn("h.cot_note", html)`，而 `html = _read(INDEX)` 连注释都没剥。
  反例尝试：①是否存在另一条测试真的渲染 ⑪ 或断言 `/api/tasks/unified` 的返回里带 `cot_note`？普查 `tests/*.py` 里 `cot_note` 只出现 3 处（`test_lyrics_ending.py:93` 断言的是**接口响应**、`test_submit_paths_agree.py:76/78` 是 grep、`:81` 是直接调 `_unified_task`），`/api/tasks/unified` 的端点级用例在 `test_audio_separate.py:242-433`，但 9 处里没有一处喂过 `cot_note`。→ 未推翻。

- **T-2 [P1][测试未锁][app.py:2985-2992]** 批量那条留痕链（条目 `cot_note`、节奏让步说明、收尾段说明）**整体没有测试**，本轮「后端替用户改的每一个主意都要留痕」只锁住了单首那半条 + 队列卡的两个字符串。
  证据：M16（批量不再算 `_ensure_song_ending` 的说明）红 0；M17（批量不再拼 `tempo_note`）红 0；M18（批量条目不带 `cot_note`）红 0。`tests/test_submit_paths_agree.py:33` 对批量只 grep 了一句 `assertIn("_style_yield_to_abc", self.batch)` —— 调用还在、说明丢了，它照样绿。
  反例尝试：`test_batch_also_strips_tempo_from_style` 是否会因 M17 变红？不会，它只看那个名字在不在 4200 字符窗口里（`:28`）。→ 未推翻。

- **T-3 [P1][声称未兑现][CHANGELOG.md:74 / README.md:327 / README_EN.md:173]** 「全量 → **534 项通过**」不是 d08c518 的测试数，标签上真实是 **497 项**；534 里混进了另一轮会话**未提交**的 37 条 `tests/test_abc_edit.py`。
  证据（三条独立口径互相对上）：
  1. worktree@ d08c518：`Ran 497 tests`；逐模块 loader 计数与 `grep -c "def test_"` 完全相等（497=497，说明没有动态生成的用例，497 就是标签的真实数）。
  2. 主工作树（dirty）：`Ran 534 tests … OK`；逐模块只有 `test_abc_edit` 不同：worktree 21 / 主树 **58**，差 **37**。
  3. `git worktree add v10.14.1` 后同样普查：标签真实 **482** 条，而 CHANGELOG 自称 519 —— **519 − 482 = 37**，与 M 那一版的差额一模一样：同一个未提交的 37 条被连着两个版本计进了发布数。
  新增 12 条 `test_submit_paths_agree.py` 是真的（loader 计 12）。
  反例尝试：会不会是「worktree 缺 py312 导致 37 条根本没被收集」？→ 已推翻该解释：loader 在缺环境的情况下仍然收集 497 条（收集阶段不碰 py312），且逐文件 `def test_` 计数与 loader 一致；37 条的差额精确落在唯一有源码差异的 `test_abc_edit.py` 上。

- **T-4 [P2][测试未锁][app.py:2975]** 「批量与单首缺省统一成 full」只有字符串锁，没有行为锁：把语义改回去而保留那串字面量，全量测试毫无反应。
  证据：M2 红 1（`test_batch_cot_default_matches_single` 断言的是 `assertIn('str(t.get("cot") or "full")', self.batch)`）；M2b **红 0**（`str(t.get("cot") or "full") if abc_t else "off"` 里那串字面量还在，断言照过，而缺省行为已经退回 off）。`tests/` 里没有任何用例 POST `/api/batch/start` 且省略 `cot` 去断言 `payload.cot == "full"`。
  反例尝试：`assertRegex`/其它文件是否补了行为层？`grep -rn '_resolve_cot' tests/*.py` 只命中 `test_review_fixes.py:203-220`（直接调纯函数，不经过批量入口）与 `test_submit_paths_agree.py:60-69`（同样是纯函数）。→ 未推翻。

- **T-5 [P3][断言强度][tests/test_page_contract.py:347-349 / test_submit_paths_agree.py:117 / test_design_system.py:1008-1011,1034]** 本轮新增断言里有一批「把实现细节当契约」与「必然成立的 grep」，两类风险同时存在：
  - 整句匹配：`assertIn('if (curTab === "scoreSing") $("cot").value = want;', self.code)` —— 加个大括号、把 `want` 改个名、换成 `if (isScoreSingTab())`，行为不变但测试碎（M11 反过来也证明了它只是文本比较）。
  - 窗口位置断言：`test_undo_still_guarded_for_lyrics` 用 `self.body[:i][-260:]` 断言 `pushLyricUndo` 前 260 字符里出现某串 —— 任何排版改动都能把它挤出去。
  - 只禁一种拼法：`test_word_chips_never_change_size` 禁的是 `'"<i>" + escapeHtml(v)'`（双引号），改用 `'<i>' + …` 单引号即绕过 —— M13 实测红 0，芯片重新挂上 `<i>` 且英文重新参与排版，测试全绿。
  - 未剥注释的 `assertNotIn`：`test_word_lists_are_always_expanded` 在 `self.src`（整个 index.html，含注释）上禁 `MULTI_LIMIT`/`multiOpen`；而本轮代码注释里已经写了「以前按 642px 精算…」这种历史说明——将来任何人**在注释里提一句被删的名字**，这条测试就红，与行为无关。同一仓库里 `PageIndependence` 特意先剥行注释（`test_page_contract.py:317`），两个文件的口径不一致。
  - `assertIn("const list = pool;", self.src)` 把一句赋值语句的写法当契约（改成 `const list = [...pool]` 行为不变即碎）。
  反例尝试：这些断言有没有对应的行为测试兜底？`tests/` 全程没有 JS 执行环境，本轮 4 处前端修复（M1/M4/M9/M11）**都只有静态文本断言**，无一条是真跑 DOM。→ 未推翻。

### 发布文档诚实度（CHANGELOG `[v10.16.0]` 逐句 + README 那一行）

| 声称（文档行） | 代码落点 | 判定 |
| --- | --- | --- |
| 「修法是提交口只认本页：`abcForSubmit()` **只在 ③ 返回谱内容，②⑤ 恒返回空串**」（CHANGELOG:26-27） | `static/index.html:5485` 返回条件是 `curTab === "scoreSing"`，按 `tests/test_page_contract.py:24-31` 的页签表 `scoreSing` = **⑤**、`score` = ③；③ 页根本没有提交口 | **说错（T-6）**：正确说法是「只在 ⑤ 带谱，②③ 恒交空谱」。文档把按谱生成页与提取谱页写反，而这正是本轮的招牌修复 |
| 「② 与 ③⑤ 共用同一块 `#abcBlock`」（CHANGELOG:22） | `static/index.html:3429,3449` 与 `index.html:5478` 自己的注释都写「③ 与 ⑤ 共用 #abcBlock」；`moveInto($("abcBlock"), scoreMode ? "abcSlotSing" : "abcSlotScore")`，② 期间谱框停在 ③ 的槽里（不可见但 `$("abc")` 读得到） | 描述**不精确**（机制成立：同一份 DOM + `_FORM_IDS` 含 `abc`，见 `index.html:9243`） |
| 「后端替用户改的每一个主意，都要留得下痕迹」+「`cot_note` 随 `_make_job` 入档、经 `_unified_task` 透传给队列卡、批量跑完写进产物 meta，⑪ 直接显示」（CHANGELOG:30-34） | 三处入档确在：`app.py:2548`（单首记录）、`app.py:2992`（批量条目）、`app.py:2889`（跑完写 meta）、`app.py:1406-1409`（透传）、`static/index.html:5966`（⑪ 显示） | 代码做了，但「每一个」只有单首那半条被锁：T-1、T-2。**另有第四类后端替用户做的主意没进这条链**：显存不足自动降级 CPU（`app.py:2273-2303`）只写进 meta 与实时进度行（`index.html:5275`），`_unified_task` 白名单不含 `cpu_fallback/gpu_error` → ⑪ 事后查不到（T-11） |
| 「收到无法识别的 cot 值也不再当 full 算了」（CHANGELOG:35） | `app.py:883` 起，note 计算在 895-896 | **兑现且被功能级测试锁住**（M3 红 2，真调 `_resolve_cot`） |
| 「批量那条路缺省原来是 off…缺省统一成 full」（CHANGELOG:36-37） | `git show v10.14.1:app.py` 第 2924 行确为 `or "off"`，d08c518 为 `or "full"`（`app.py:2975`） | 事实成立；锁定强度见 T-4 |
| 「RVC 的 index_rate/protect/rms_mix_rate 改成 None 让参数层做主」（CHANGELOG:38-39） | `app.py:5470-5472` 三个都是 `float \| None = Form(None)`，参数层缺省在 `app.py:4297-4303`（0.5/0.33/0.25） | 事实成立；但 None 这条路径零覆盖（M19 红 0 + 普查：闸门 6 处调用全传显式数）→ 见 T-8 |
| 「刷新页面不再盖掉你写过的东西」（CHANGELOG:41-44，README:327「never overwrites what you typed」） | 只填空白：`index.html:5386-5400`（style/lyrics/abc）+ 档位一律不动（5407）；**同一函数里仍无条件覆写** `$("gender")`（5411）、`$("cfg")`（5423）、`$("steps")`（5424），三者都在 `_FORM_IDS`（9243）里，会被自动存回 localStorage | **夸大（T-7）**；M20b 再补两条覆写仍红 0 |
| 「⑬ 看得见当前真正加载的是哪套权重 / 报出 vae 来源」（CHANGELOG:66-70，README:327） | 写入 `app.py:1700-1731`（`vae_from` 同目录/公共退回），读出 `app.py:727-730`，标签 `index.html:8302`「当前主模型 / 当前 vae」 | **行为成立**（只读 GET 实测：`{"loaded_main":"yue2-q4.gguf","loaded_vae":"yue2-vae-f16.gguf（同目录）"}`，HTTP 200）；**但测试没锁**（T-6） |
| 「删掉每页的「本页任务」条：234 行删除、0 行保留」（CHANGELOG:62） | `git show --stat f9f7879` = `static/index.html 189 ---` + `tests/test_page_contract.py 45 ---`，234 deletions(-)、0 insertions | 数字**属实但不诚实**：被删的 45 行正是 v10.14.1 用来钉住这个组件的 3 条契约测试（`test_mod_strip_kinds_match_the_backend` / `test_mod_strip_is_moved_not_duplicated` / `test_mod_strip_names_are_declared`） |
| 「页面契约测试补：… **任务条组件不得复活**」（CHANGELOG:77-78） | `grep -rn "modTasks\|modEnter\|MOD_KINDS\|本页任务" tests/` = **0 命中** | **写了没做（T-10）**：这一句声称的测试在 d08c518 里不存在，且是在同一次提交里被删掉的 |
| 「`#styleBox` 必须恰好两个（拆框后不许退回共用）」（CHANGELOG:77） | `index.html` 里 `id="styleBox"` 出现 **1** 次（1857），第二份叫 `#styleBoxAsm`/`#styleAsm`（1712）；测试 `test_page_contract.py:132-137` 断言的是「#styleBox 1 个 + #style 1 个 + #styleAsm 1 个 + ① 槽内不得有 #style」 | 说法与实现**不一致**（P3，见 T-12 一并计） |
| 「13 页逐页真点过一遍，并真实跑完一首歌验证档位改写留痕那条链路」（CHANGELOG:79） | `runtime/output/*.json` 共 163 份，`grep -rl "cot_note"` = **0 份**；最新一份 meta 的 mtime 是 10-16 10:03；当前网关进程 StartTime = **11:43:23**，而留痕提交 ac9bc19 = 11:56、tag = 12:03，`app.py:8892 reload=False`；`runtime/data/batch_state.json` 里 16 条带 `cot_note` 的条目其 meta 全无 `cot_note`（这些 `cot_note` 来自 v10.14.1 就存在的批量入队逻辑 `git show v10.14.1:app.py` 第 2935 行，不能当本版证据） | **找不到凭据（T-9）**：重启之后没有任何一份生成产物，「跑完一首歌验证留痕」既无 artifact 也无日志 |
| v10.14.1 的「明暗两套 13 页无障碍不达标项归零」（README:328 仍在表里） | 该结论依赖一次性外部测量：`scripts/` 里没有无头 Chrome/CDP 扫描器（`ls scripts/` 只有下载模型/gtcrn/局域网 bat/ps1），`docs/` 也没有报告；仓库内只有 `test_design_system.py:307-388` 的**静态色板核算**（只覆盖 ⑫ preset 底色与 hover） | 见 T-13：不可复核 + 本轮改了布局 CSS，回归风险真实存在 |

- **T-6 [P2][测试未锁][app.py:728-730 / tests/test_submit_paths_agree.py:91-93]** 「⑬ 报出 vae 来源」= 一句都不锁。证据：M12（把 `system_info` 里那段「（同目录/公共退回）」拼接删干净）**红 0**；测试只 `assertIn('"loaded_vae_from"', src)`，而这个字面量在 `app.py:1731`（models_switch 写状态）还有一份，删掉读的那份它照样绿。功能当前是好的（GET 实测有值），坏的是**下次谁重构 `system_info` 就没人报警**。

- **T-7 [P2][文档夸大][static/index.html:5411,5423,5424]** 「刷新页面不再盖掉你写过的东西」只做到 4 个控件。`resumeRunningJob` 仍然无条件覆写歌手性别、CFG、推理步数；性别会进 `styleWithGender($("style").value)`（`index.html:5493`）直接影响**下一次提交**的 Style 文本，而这三个控件都在 `_FORM_IDS`（9243）里、会被自动保存回 localStorage —— 与本轮自己修的「随机种子固化」是同一个形状。证据：M20b（再新增 `taskName`/`gender` 两条无条件覆写）**红 0**；`test_no_unconditional_overwrite` 只枚举 `style/lyrics/abc`（`test_submit_paths_agree.py:105-107`）。
  反例尝试：①会不会这三个控件不在表单持久化清单里（盖了也不留）？→ 已核对 9243 行，在清单内，未推翻。②测试的 3000 字符窗口是否把函数尾巴截掉了（那样"没断言"可以怪窗口）？→ 用大括号配对量过：函数体 2213 字符，窗口 3000，**完整覆盖**，所以确实是对 gender/cfg/steps 三处**没有断言**，未推翻。

- **T-8 [P3][测试未锁][app.py:4299 / tests/test_submit_paths_agree.py:41-53]** 「Form 改成 None 让参数层做主」没有行为锁：M19 把参数层 None 缺省改回 0.75 红 0。现存用例（`test_review_fixes.py:1943-2035`、`test_server_sources.py:223,317`）全部给显式数值，没有一条 POST multipart 时省略 `index_rate` 再断言落到 0.5；唯一的 0.5 断言（`test_review_fixes.py:2954`）走的是 `/recommend` 体检接口，与提交闸门是两套代码。诚实标注：这 8 条 RVC 用例在变异副本里属 26 条环境类失败之一，无法用「变红/不变红」判定，故本条同时给出**用例普查**作为判据。

- **T-9 [P2][未证实][CHANGELOG:79]** 「真实跑完一首歌验证档位改写留痕那条链路」在 `runtime/` 里找不到落点：163 份 meta 无一份含 `cot_note`、网关 11:43:23 重启后 0 份生成产物、`batch_state.json` running=False。不能证实也不能证伪 → 按「声称无凭据」记。

- **T-10 [P1][写了没做][CHANGELOG:77-78]** 声称「页面契约测试补……任务条组件不得复活」，`tests/` 里 0 处相关断言；被删的 3 条同类断言（`git show --stat f9f7879`）在同一版本消失。这不是措辞问题，是**发布说明承诺了不存在的保护**。
  反例尝试：会不会「不得复活」由 `test_no_cross_page_fillers_left`（`test_page_contract.py:320-326`）间接覆盖？→ 该名单是 `LabStore/handoffAudio/…/playAndRestore`，与 `mod*` 组件无关；且 `grep -rn "mod[A-Za-z]" tests/*.py` 在 d08c518 无任何命中。未推翻。

- **T-11 [P3][文档口径超出实现][app.py:1406-1409 / app.py:2273-2303]** 「后端替用户改的每一个主意都要留痕」段落自己把范围限定为 `_resolve_cot`/节奏让步/收尾段三处（CHANGELOG:31），但标题是「每一个」；显存不足自动切 CPU 后端这件事同样改了用户的执行条件，写进了 meta 却不在 `_unified_task` 白名单里，⑪ 事后查不到 —— 用户只会在当时那一行进度文案里看到一次（`index.html:5275`）。属边界性夸大。

- **T-12 [P3][文档不精确 + 行锚偏移][CHANGELOG:34,37,39,77]** 三处 `app.py` 行锚全部**偏 5 行**：`app.py:2553`（实为 `_make_job` 的 `cot_note` = 2548；2553 是 `"chain_total"`）、`app.py:2980`（实为批量缺省 full = 2975；2980 是收尾说明拼接）、`app.py:5475`（实为 `index_rate: float | None = Form(None)` = 5470）。同一版里前端两处行锚是准的（`index.html:5484`、`5966`、`5386`）。另 `#styleBox 必须恰好两个` 与实现（`#styleBox` 1 个 + `#styleBoxAsm` 1 个）不符。
  反例尝试：会不会行锚是按主工作树（含在制品）数的？→ 已核对：在制品只在 `src/abc_edit.py`/`_score_save` 附近，主树 app.py 的这几处行号同样不是 2553/2980/5475。未推翻。

- **T-13 [P3][声称不可复核][README.md:328 / CHANGELOG v10.14.1 段]** 「明暗两套 13 页无障碍不达标项归零」依赖一个没进仓库的一次性测量：`scripts/` 内无扫描器、`docs/` 内无报告、`tests/` 内只有 ⑫ preset 色板的静态对比度核算。本轮又动了布局 CSS（`align-items: stretch`、每栏 `.pane-body` 自滚、`select[multiple]` 高度、侧栏 `nowrap`），这些正是「命中区/空洞」类指标的来源，**回归了也不会有任何测试报警**。
  反例尝试（主动找能让这条不成立的证据）：本轮是否新增了低于 AA 的小字？→ `git diff v10.14.1 d08c518 -- static/index.html | grep '^+.*var(--dim)'` **0 处**，且 `var(--dim)` 总数由 11 降到 9。未推翻本轮有实测回归；因此本条只定 P3（可复核性缺陷，不是已知实测回归）。

- **T-14 [P2][测试未锁][static/index.html:4414-4416]** 招牌修复的同一族里还有一处漏网：①「我的预设」回填会把预设里的档位写进 ②⑤ 共用的 `#cot`、把谱写进共用的 `#abc`。`#myPresetChips` 在 `index.html:1765`，位于 `<section id="tab-style">`（1684-1841）内，而 `setSel($("cot"), t.cot)` / `$("abc").value = t.abc` 没有任何按页守卫；`test_page_contract.py:306` 立的判据是「**没有任何页面的代码可以写另一个页面的控件**」，本轮新增的守卫测试只覆盖了 ③ 的两处（`:347-349`）。与 CHANGELOG:27-28「别的页面没有资格替它改」的普适表述冲突。
  反例尝试：①这是用户显式点击、且按钮 `title` 自己写着「点击回填（含歌词/参数）」，属预期功能不是"自动带内容"；②`#scoreLoadBtn`（8010 `$("cot").value = sug`）在 ② 里因 `#scoreSection hidden` 不可达，只在 ⑤ 触发，属正当。→ 两条都成立，故本条从 P1 降到 P2，只算「文档把话说满了 + 该处无守卫/无测试」。

- **T-15 [P3][状态行说谎][static/index.html:5386-5390,5403]** `fillIfEmpty(el, v)` 在「控件空白且 v 也空白」时仍然 `return true` 并 `filled.push("乐谱")`：② 提交的 `abc` 恒为空 → 刷新后状态行会写「接着显示在跑的那一首：曲风、乐谱」，而它一格都没填。声称「填了哪几格都写进状态行」（CHANGELOG:43-44）在这种情况下是假的。测试只断言赋值方式，不看这条文案。

### 版本与发布纪律（README.md:330-335 那四项）

| 清单项 | 判定 | 一手证据 |
| --- | --- | --- |
| 1 全量测试绿 | **做了，但对外数字错**：主树实测 `Ran 534 tests … OK`（我亲手跑，未改文件）；标签 d08c518 的真实口径是 **497** | 见 T-3 的三口径普查 |
| 2 重启网关后跑冒烟（三件） | **不能证实**：进程 StartTime 11:43:23 早于 ac9bc19(11:56) 与 tag(12:03)，`reload=False`（`app.py:8892`）；重启后 `runtime/output/` 0 份新产物（最新 meta mtime 10:03）；追加排队 / 停止取消 / 超限 400 / 跨站 403 四项没有任何落盘或日志记录 | `Get-Process -Id 23144 StartTime`、`ls -lt runtime/output` |
| 3 README 中英文 + `docs/PROMOTION.md` 同步 | **README 做了**（`README.md:327`、`README_EN.md:173` 各加一行）；**PROMOTION.md 本轮 0 改动**（`git diff --stat v10.14.1 d08c518 -- docs/PROMOTION.md` 为空），它的写法是「版本号见 CHANGELOG 顶部条目」（`docs/PROMOTION.md:5,83`），不承载行为细节 → 按规矩算满足；缺陷是把 v10.16.0 插到了版本表**时序错位**的位置（`README.md:327` 夹在 v10.13.0 与 v10.14.1 之间；`README_EN.md:173` 同样插在 v10.12.0 之后，而 v10.13.0 行仍排在 v10.14.0 之后 = 176 行） | 见行号 |
| 4 打附注标签并推标签 | 标签**已打**（`v10.16.0` objecttype=tag，12:03:44）；**未推**：`git ls-remote --tags origin` 最新只到 `v10.9.2`/`v2.0.0`，`git rev-list --count origin/master..master` = **15** 个提交未推。按指示：代理未监听不计缺陷，只算未完成 —— 但「版本的唯一真源是 git 附注标签」（CHANGELOG:9、README:306）这句话目前只在本地成立 | `git for-each-ref`、`git ls-remote` |
| 附带：版本号显示与运行代码脱钩 | `app.py:640-651` 的 `_version_from_changelog()` 每次请求读 `CHANGELOG.md` 首条 → ⑬ 现在显示 v10.16.0，**不能**据此说线上跑的是那一版（README:336 自己就警告过这条，本轮仍然踩在警告上） | `curl /api/system/info` 返回 `"version":"v10.16.0"` 而进程 11:43:23 启动 |

---

## 三、纪律自证

- `git worktree list` 现在只有主树：`E:/AI/10AIMusic/Yue/yuE-2/yuE-2 d08c518 [master]`（`wt-mut`、`wt-141`、`wt-14` 三个临时副本已 `remove --force` + `prune`）。
- 主工作树未被我改动：审查开始与结束时 `git status --short` 都是同一组 4 个 M（`app.py`、`src/abc_edit.py`、`static/index.html`、`tests/test_abc_edit.py`）+ 2 个 ?? 评审文档，`git diff --stat` 前后同为 `4 files changed, 730 insertions(+), 67 deletions(-)`（= 另一轮会话的在制品）。
- 未改 `tests/`（变异只发生在临时 worktree，每轮 `git checkout -- .` 还原，副本最终整体删除）；未重启网关；未调用任何 POST/写库接口，只做过 `GET /api/system/info`（HTTP 200）与本地文件普查；未打标签、未推送、未改写历史。
- 一处工具事故如实记录：中途在主工作树跑全量测试后，`%TEMP%\qoder\rev` 被测试的临时目录清理逻辑带走（导出副本丢失），已改用 `%TEMP%\revG3` 重导并重跑 M2 复验（M2R 结果一致）。**这是事故，不是缺陷**。

## 四、给裁定员的一句话

本轮四处点名的修复在测试里都「有一盏灯」，但只有 ②/③ 那条跨页泄漏与 `_resolve_cot` 的留痕是**行为级**的；其余是**字符串级**——把实现改回旧行为而不触碰那几个字面量，测试全绿（M2b/M8b/M12/M13/M16/M17/M18/M20b 共 8 组零红）。发布说明里最硬的两处不诚实是「534 项」把另一轮会话未提交的 37 条测试计进了本版（标签真实 497），以及「任务条组件不得复活」这句承诺的测试在同一次提交里被删掉了。
