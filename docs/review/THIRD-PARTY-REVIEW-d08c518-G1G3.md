# THIRD-PARTY-REVIEW — 蓝军 G1/G3 报告逐条复核（基线 d08c518）

> **审计方式**：第三方独立复核。只读代码复核（一律对 `git show d08c518:<path>` 的导出副本行号）＋ 只读 GET（`/api/system/info`、`/api/tasks/unified`、`/api/history`）＋ 只读落盘普查（python 扫 JSON 键名）＋ **变异试验在一次性 worktree 里做**（`git worktree add --detach …wt-audit d08c518`，用完 `remove --force`）。
> **未做**：没有修改主工作树任何文件、没有改 `tests/`、没有重启网关、没有调用任何会启动 GPU 任务或写库的接口。
> **被复核对象**：`docs/review/BLUE-TEAM-REVIEW-d08c518-G1.md`（F-1…F-15）、`docs/review/BLUE-TEAM-REVIEW-d08c518-G3.md`（T-1…T-15 + 变异台账 + 发布文档逐句表）。
> 行号锚点文件：`static/index.html`（9338 行）与 `app.py`（8892 行）的 **d08c518 版**。本报告所有行号已核对为标签版；工作树那两处 `s.title`（5733/7990）与 `_score_save(title=…)` 属另一轮在制品，蓝军两份报告均未据此报缺陷，本次复核也未采用。

---

## 一、总判

**编号条目共 30 条（G1 15 条 + G3 15 条）**，另有 G3 表格中 4 条未编号的发布文档判定。

| 判定 | 条数 | 条目 |
| --- | --- | --- |
| **成立** | **27** | F-1…F-10、F-12…F-14；T-1…T-11、T-13…T-15 |
| **部分成立** | **2** | F-11（事实对、缺陷不成立）、T-12（现象对、归因错） |
| **不成立** | **1** | F-15（把注释读成承诺） |
| 需人工 | 1（属 G3 发布清单第 4 项） | 远端不可达，无法证实标签是否已推 |

**蓝军整体质量一句话**：没有凑数编造——29/30 条的技术事实我都复现了，且 G1 的"公道话"第 6、10 条我独立复核也成立（全量 249 个 `$("id")` 引用 0 悬空、`mod*`/「本页任务」在 index.html 里 0 命中）；但 G1 有 **1 条把注释歧义当缺陷**（F-15）、**1 条自认不可达仍挂编号**（F-11），G3 有 **1 条归因错误**（T-12 说行锚"不是按主工作树数的"，实测恰恰**就是**脏树行号）与 **1 条变异判据失效**（M19/T-8 在"被测用例本身已经红"的副本上判"未锁"）。两处都是方法问题而非造假。

**我自己新增/修正 4 点**（见第五节）：T-8 需要换判据才成立；T-9 的性质是"留痕代码从未在生效进程里跑过"而不是"没留证据"；F-1 有一条一行的改法（后端已回传 `src_name`）且刷新页面可恢复任务卡；CHANGELOG:26 把 ③/⑤ 写反这件事在 G3 表里被错标成 "T-6"（与 vae 那条撞号）。

---

## 二、六个重点复核（命令 + 原始输出）

### 2.1 T-3「534 是脏树数字，标签上真实 497」→ **成立，且我做成了"标签上 497 全绿"**

三种互相独立的口径：

```
# (1) 源码文本计数（不执行任何测试）
$ for f in $(git ls-tree --name-only d08c518 tests/); do git show d08c518:$f; done | grep -c "^\s*def test_"
497
$ cat tests/*.py | grep -c "^\s*def test_"          # 主工作树（含在制品）
534
# 逐模块差值：只有 test_abc_edit.py 不同 —— 标签 21 条 / 工作树 58 条，差 37

# (2) unittest loader 真实收集数（在一次性 worktree 里，用主树 py312）
$ cd …wt-audit && /e/…/py312/python.exe -c "…discover('tests')…"
COLLECTED_AT_D08C518 = 497

# (3) 在 worktree 里真跑全量
$ python.exe -m unittest discover -s tests
Ran 497 tests in 52.499s
FAILED (failures=23, errors=3, skipped=1)          ← 与蓝军数字完全一致
```

第 (3) 步的 26 条红我逐条看过，报错统一是 `{"detail":"rvc python 环境缺失（py312/python.exe）"}` 与 `FileNotFoundError: …\runtime\rvc\tools\pymss\resources\model_catalog.json`——都是 worktree 缺未入库目录。**我给这两样打了补丁后重跑**（只在临时 worktree 里加文件，主树没动）：

```
$ mkdir py312 && : > py312/python.exe            # 只为满足 RVC_PY.is_file()（app.py:3255）
$ cp <主树>/runtime/rvc/tools/pymss/resources/model_catalog.json 到 worktree 同路径
$ python.exe -m unittest discover -s tests
Ran 497 tests in 52.530s
OK (skipped=1)
```

**结论**：
- v10.16.0 标签上真实测试数 = **497**；**全绿成立**（497 跑通、1 条 skip，前提是环境里有 `py312/python.exe` 与未入库的 vendored model catalog）。
- CHANGELOG:74「全量 → **534 项通过**（v10.14.1 的 519 → 534）」、README.md:327「534 条测试全绿」、README_EN.md:173「534 tests green」三处都是**脏工作树数字**：其中 37 条来自另一轮会话未提交的 `tests/test_abc_edit.py`（21→58）。
- 同一错误连着两个版本：`git show v10.14.1:…` 口径下标签真实 **482** 条，而 CHANGELOG v10.14.1 段自称 **519**，差额同样是 37。
- 同一轮新增的 `tests/test_submit_paths_agree.py` 12 条是真的（grep 12 / loader 12）。
- **算不算不实**：算。数字本身没被夸大能力，但把别人未提交的测试计进本版发布数，读者据此以为本版回归保护变强了 15 条，实际本版只多 12 条。**级别我同意蓝军 P1**（发布诚实度，非产品缺陷）。
- 附带发现（不改判）：在干净 worktree 里 `python -m unittest tests.test_submit_paths_agree` **单跑会 ImportError**（`app.py:45 import main`，而 `main` 的替身模块由 `test_review_fixes.py:31-37` 安装，按 discover 字母序才先执行）。主树里有编译好的 `main.cp312-win_amd64.pyd` 所以单跑正常。这条不是缺陷，但**任何审计者在 worktree 里单跑这两个契约文件会误判成代码坏了**，值得写进复核手册。

### 2.2 T-9「163 份产物 meta 零 `cot_note`、重启后 0 产物 →『真实跑完一首歌验证留痕』无凭据」→ **成立，性质比蓝军说的更硬**

```
$ ./py312/python.exe  # 只读扫 JSON 键名
total json meta: 163
files containing key cot_note: 0 non-empty: 0
has any key containing cot: ['cot']
max mtime: 2026-10-16 10:03:13.010565
$ powershell "(Get-Process -Id 23144).StartTime"      # 23144 = 7863 的 LISTENING 进程
2026年10月16日 11:43:23
$ git log --format="%h %ad" -2 d08c518
ac9bc19 2026-10-16 11:56:06 +0800        ← 留痕代码的提交
d08c518 2026-10-16 12:03:35 +0800        ← 附注标签
$ curl -s http://127.0.0.1:7863/api/tasks/unified?limit=500 → tasks returned: 184 / with non-empty cot_note: 0
$ ./py312/python.exe（runtime/data/batch_state.json）→ items 94，带非空 cot_note 16 条，
   这 16 条的 rid 全部在 runtime/output/ 有 meta 文件，而这些 meta 里【都没有】cot_note 键
```

代码侧核对（这是蓝军没做的一步）：单首 `_make_job` 把 `cot_note` 放进 job 字典（`app.py:2548`），队列 worker 跑完写产物 meta 时也放了（`app.py:2889` + `_output_write_meta`）——**也就是说，只要这条链路真的在带新代码的进程上跑完一首歌，落盘 meta 里必然出现 `cot_note` 键**。而：

- 网关进程 11:43:23 启动，早于 ac9bc19（11:56）与 tag（12:03）；`app.py` 里 `reload=False`（**d08c518 版是 8887 行，蓝军写 8892 是脏树行号**），所以线上跑的仍是旧代码；
- 最新一份 meta 的 mtime 是 10:03:13，重启之后 0 份生成产物；
- 163 份 meta 里 0 份含该键。

**性质判定**：不是"做了但没留下证据"，而是**"能产生证据的那段代码，在这台机器上从未被执行过"**。所以 CHANGELOG:79 那句「真实跑完一首歌验证档位改写留痕那条链路」即便作者真跑过一首，验的也不是本版这条链路（要么跑在重启前的旧进程上，要么跑在标签之前）。判 **成立 P2**，与蓝军同级别，但把"不能证实也不能证伪"改为"该声称所指的验证在此机器上不可能成立"。
**整改**：重启网关 → 跑一首会触发改写的提交（如带纯旋律谱 + `cot=full`）→ 现场出示 `runtime/output/<rid>.json` 里的 `cot_note` 非空，再把它写进 CHANGELOG。

### 2.3 T-10「'任务条不得复活'的测试在同一次提交被删」→ **成立 P1**

```
$ git show --numstat f9f7879
0	189	static/index.html
0	45	tests/test_page_contract.py        ← 纯删除，0 行新增
$ git show f9f7879 -- tests/test_page_contract.py | grep "^-"
-    def test_mod_strip_kinds_match_the_backend(self):
-    def test_mod_strip_is_moved_not_duplicated(self):
-    def test_mod_strip_names_are_declared(self):
$ # d08c518 全量 tests/ 里搜 modTasks|modEnter|MOD_KINDS|本页任务|modPaint|modTimer|modLoad
0 命中
$ sed -n '78p' CHANGELOG.md
`#styleBox` 必须恰好两个（拆框后不许退回共用）、任务条组件不得复活。
```

CHANGELOG:78 把"任务条组件不得复活"写成本轮"页面契约测试补"的其中一条，而 d08c518 的 `tests/` 里**一条相关断言都不存在**，且是在 f9f7879（本轮）里被连着 45 行一起删掉的。CHANGELOG:62「234 行删除、0 行保留」数字属实（189+45=234），但没交代被删的 45 行就是上一版钉这个组件的测试。判 **成立 P1**（发布说明承诺了不存在的保护）。
**整改**：要么把这句从 CHANGELOG 删掉，要么补一条真断言（`assertNotIn` 那批禁名字里加 `modEnter/modTasks/MOD_KINDS`，并放进 `PageIndependence.test_no_cross_page_fillers_left` 的名单）。

### 2.4 F-1「⑨ 用『服务器素材』提交，POST 成功后读 `f.name` 必抛」→ **成立 P0**（触发链与行号逐条复核）

```js
6876  const f = $("rvcFile").files && $("rvcFile").files[0];   // 没选文件 → FileList(0) 是【真值】，[0] 是 undefined
6877  const srcId = ($("rvcSrcPick") || {}).value || "";
6878  if (!f && !srcId) { …return; }                            // 只选素材：放行
6885  if (f && srcId)  { …return; }                            // f 是 undefined：放行
6904  if (srcId) fd.append("source_id", srcId); else fd.append("file", f);
6913  const r = await fetch("/api/rvc/convert", { method:"POST", body: fd });   ← GPU 任务从这里已经交出去
6918  rvcJobs.unshift({ id: d.id, model, src_name: f.name, …    ← TypeError
6920  renderRvcList();   6926  pollRvc(d.id);                   ← 都走不到
6929  $("rvcStatus").textContent = "提交失败：" + friendlyErr(e);
```

- 后端在 6913 那次调用里就把任务入队了：`app.py:5558-5562` `_RVC_JOBS[rid]=job` + `_rvc_submit(...)`，`source_id` 是与上传等价的正式入口（`app.py:5481-5504` 的 docstring 与 400 校验都说明"只带 source_id"是受支持的正常路径）。前端抛错与任务入队之间**没有任何回滚**。
- `friendlyErr`（2902-2915）对这句 TypeError 无匹配分支 → 落到 `return m.slice(0,200)`，用户在状态条看到原文 `提交失败：Cannot read properties of undefined (reading 'name')`；`finally`（6930）把按钮重新启用 → **再点一次就是第二条换声任务**（同一首歌跑两遍换声）。
- 反证搜索：⑦ 训练用的是 `files.length`（7669/7712/7744）、⑩ 分离有 `if (!f) return`（4860/7140/8835），全文件只有 ⑨ 这一处直接 `f.name`。**已试图推翻、未推翻。**
- 两点校正蓝军：(a) 这条不是"完全查不到"——刷新页面时 `restoreRvcActive()`（6411-6423）会从 `/api/rvc/active` 把在跑的任务捞回列表并接上轮询，所以丢的是"本会话内的反馈"，不永久丢数据；(b) 改法比蓝军写的更省——后端**已经**算好了这个名字：`app.py:5548-5549` `src_name = 上传文件名 / "服务器素材 <spec>"`，而 6918 的对象字面量里 `...(d.job || {})` 展开在 `src_name: f.name` **之后**，所以**删掉 `src_name: f.name` 这一段**即可，`d.job.src_name` 会自己覆盖。（`tests/test_server_sources.py:242` 已经钉过服务端这份 src_name，改完测试不会红。）
- 无可测性说明：`tests/` 里没有 JS 执行环境，全量测试也不会因这行崩而变红（我在 worktree 里 grep：`tests/*.py` 没有一处引用 `rvcJobs`/前端 `f.name`）。所以这条**只能靠静态阅读 + 手工点击复核**；我的判定来自代码路径逐行走读，不是来自"测试红了"。
- **级别：P0 认同**（一用必错、成功报成失败、诱导重复提交烧 GPU）。这是 30 条里唯一必须在下一次发版前修的。

### 2.5 「页面之间自动带内容」这一族（F-2 / F-3 / F-5 / T-7 / T-14）逐条核

硬规矩口径（项目记忆 `yue2-page-independence-rule` + `tests/test_page_contract.py:306`）：**没有任何页面的代码可以写另一个页面的控件**；且界面显示必须等于真实提交。

| 条 | 触发顺序（人在哪页做什么 → 在哪页看到什么 → 实际交出去什么） | 我的判定 |
| --- | --- | --- |
| **F-2** | 全新浏览器（无 localStorage）→ `loadPresets()`→`initAssembler()`（4879/4715）把 ① 独占的 `#gender` 落成 `clear female vocal`（4719）、从 ① 示范文本 `#styleAsm`（1714，含 `expressive female voice` 与 `88 BPM`）反解出 BPM=88（4744-4750）→ 人直接进 ② 写 `Japanese city pop, smooth male vocal, 100 BPM` → 点「生成歌曲」→ `styleWithGender`（2972）先把用户写的 ` male vocal` 抹掉（2975 + `VOICES.forEach` 2979-2981，六个候选全在删除表里）、把 `100 BPM` 改成 `88 BPM`（2987）、末尾拼上 `, clear female vocal`（2990）→ **真交出去的 style 与他写的那句不是同一句**。全文件读 `#gender` 的 15 处（2973/3927/4006/4207/4409/4439/4461/4717-4747/5411/7836）没有一处在 ②⑤ 上把值显示给用户；`#gender`(1798)、`#bpm`(1816) 都在 `#tab-style`(1684-1841) 里 | **成立，P1**。`styleWithGender` 只有两个调用点，全是 ②⑤ 提交口（5491/5541）。我在 worktree 里把 2990 的拼接改成 `if (false)`，342 条静态契约测试**全绿**（MUT-E）→ 这件事没有任何测试保护。蓝军自判 P1、备注"全新浏览器第一次用就会错可判 P0"——我认为按本项目"成品不是自己要的歌"的历史事故口径，**这条就是 P0 候补**，但保守判 P1 可以接受 |
| **F-3 / T-14** | 人在 ① → 点 `★ 某预设`（4376）→ `restoreMyPreset`（4403）**无条件**写 ②⑤ 的 `#cot`（4414）、②⑤ 共用歌词框 `#lyrics`（4415，整段替换、无 confirm）、③⑤ 的谱框 `#abc`（4416，并触发 `analyzeAbcDebounced()`）→ 切到 ⑤：`switchTab`(3460) 按这份谱重放锁档 → 提交时 `abcForSubmit()` 在 ⑤ 为真**把这份谱交出去**。反方向：人在 ① 点「💾 存为我的预设」（4428）→ 4434-4438 把 `$("lyrics")`/`$("cot")`/`$("abc")` 的**当前残留值**一起 `POST /api/templates`（4443）→ 这条预设跨会话带回另一首歌的词与谱 | **成立**。级别我从 P1 降到 **P2**，理由三条：① 它是用户在 ① 上显式点一颗写着"点击回填（含歌词/参数）"的芯片（4375），属"用户主动应用的配方"，不是"页面自己飘过去"；② 落点（歌词框/谱框/档位）在用户切过去时**都看得见**，与七里香那次"看不见却被提交"不同形；③ 谱框有撤销的对面（`#scoreClear`）。真正没打折的是两点：`title` 只披露"含歌词/参数"、**没披露还带乐谱**（而乐谱才决定唱哪首歌），以及**保存方向把别的页残留打包进服务端预设**。我把 4416 整行改成永不执行（MUT-F），342 条测试**全绿** → 无任何保护 |
| **F-5** | 人在 ③ 转一份纯旋律谱 → `renderAbcAnalysis` 头部（5584）调 `updateParamSummary()` → 用户点 ②：`#paramSum`（1973，折叠标题，②⑤ 均可见）打出「…谱要 melody，档位选了 full…」（4680-4681）→ 但 `abcForSubmit()` 在 ② 恒交 `""`（5485），后端 `_resolve_cot` 空谱原样放行（`app.py:901`）→ **摘要在替一份根本不在请求里的谱说话** | **成立**，级别 P1 → **P2**。`updateParamSummary`（4664）里 `cot` 取的是 ②⑤ 共用 `#cot`（对），只有 `hasAbc`(4668)/`route`(4669) 两行不分页。提交内容是对的、坏的是"护栏③ 说真话"这件事本身，与用户定的"界面显示必须等于真实提交"直接冲突，所以我没降到 P3 |
| **T-7** | CHANGELOG:41-44 + README:327 说「刷新页面不再盖掉你写过的东西 / never overwrites what you typed」；实际 `resumeRunningJob` 仍**无条件**覆写 `#gender`（5411）、`#cfg`（5423）、`#steps`（5424），三者都在 `_FORM_IDS`（9243）里、会被 400ms 防抖存回 localStorage | **成立 P2**。这条我不用变异就能定死：函数体从 `async function resumeRunningJob` 起算 2874 字符，测试窗口切 3000 字符，**三行都在窗口内**，而 `tests/test_submit_paths_agree.py` 里 `$("gender")`、`$("cfg")`、`$("steps")` 三个字符串**一次都没出现**（用例只枚举 `style/lyrics/abc`，`:105-107`）。我在窗口内再加一条 `$("taskName").value = job.task_name || ""`（MUT-G），342 条全绿 |

### 2.6 其余条目的逐条判定见第三节表格；这里记我额外复现的变异（全部在一次性 worktree）

台账口径：基线 = 同一 worktree 同一份 342 条静态/契约用例，改一条 → 跑一次 → `git checkout -- .` 还原。

| 我的编号 | 改了哪一行 | 原值 → 新值 | 结果 | 对应蓝军条目 |
| --- | --- | --- | --- | --- |
| MUT-A | `static/index.html:5966` | `${h.cot_note ?` → `${false && h.cot_note ?`（⑪ 那行永不画，字面量留着） | **0 红** | T-1 成立 |
| MUT-B | `app.py:2992` | `"cot_note": cot_note,` → `"cot_note": "",`（批量条目丢痕） | **0 红** | T-2 成立 |
| MUT-D | `app.py:2977` | `_, ending_note = _ensure_song_ending(lyrics)` → `("","")`（批量不再说"补了 [Outro]"） | **0 红** | T-2 成立 |
| MUT-C | `app.py:2975` | `… or "full")` → `… or "full") if abc_t else "off")`（字面量保留、缺省退回 off） | **0 红** | T-4 成立 |
| MUT-E | `index.html:2990` | 性别拼接改成永不执行 | **0 红** | F-2 无保护（蓝军没测，我补） |
| MUT-F | `index.html:4416` | 预设回填谱写入改成永不做 | **0 红** | F-3 / T-14 无保护 |
| MUT-G | `index.html:5408` 后新增 | 多一条无条件覆写 `#taskName` | **0 红** | T-7 成立 |
| MUT-H（对照） | `index.html:5485` | 去掉 `curTab === "scoreSing"` 判定（退回七里香行为） | **1 红**：`test_generate_page_never_carries_a_score` | 招牌修复确实有锁 |
| MUT-J | `index.html:4073` | `b.textContent = wordZh(v)` → `b.innerHTML = '<i>' + …`（单引号绕过 grep） | `test_word_chips_never_change_size` **不红** | T-5 成立（禁法只一条） |
| MUT-K | `index.html` 两处 | `const list = pool;` → `const list = [...pool];`（行为等价） | `test_word_lists_are_always_expanded` **红 1** | T-5 成立（实现细节当契约） |
| MUT-M | `app.py:728-730` | 删掉 `（loaded_vae_from）` 那段拼接，只留 `loaded_vae` | 274 条 **0 红** | T-6 成立 |
| **MUT-L** | `app.py:4299` | `dflt if raw is None` → `0.75 if raw is None` | **红 12 条**（`protect须在 0.0-0.5 之间，收到 0.75`） | ⚠ **推翻蓝军 M19 的"红 0"** |
| **MUT-L2** | `app.py:4297-4299` | 只在范围内换缺省：index_rate 0.5→0.75、rms_mix_rate 0.25→1.0 | 254 条 **0 红** | ✅ T-8 结论仍成立，但必须用这条做判据 |

---

## 三、逐条判定表

### G1（前端功能正确性）

| 编号 | 蓝军级别 → 我的判定与级别 | 一手证据（d08c518 版文件:行 + 片段） | 一句话整改 |
| --- | --- | --- | --- |
| F-1 | P0 → **成立 P0** | `index.html:6876/6904/6913/6918/6929`；后端 `app.py:5558-5562` 已入队、`app.py:5548-5549` 已算好 `src_name` | 删掉 6918 的 `src_name: f.name`，让 `...(d.job)` 覆盖（或 `f ? f.name : ""`） |
| F-2 | P1 → **成立 P1（我承认它够 P0）** | `index.html:2972-2991`（读 `#gender`/`#bpm`）、调用点仅 `5491/5541`、`4719/4744-4750` 默认值来源、`1798/1816` 在 `#tab-style`(1684-1841) 内；MUT-E 0 红 | `styleWithGender` 改成只吃本页可见字段：②⑤ 要么把性别/BPM 显示成可编辑格，要么一律不碰用户写的 style |
| F-3 | P1 → **成立 P2**（用户显式点击 + 落点可见，故降级；未披露"带谱"仍算违反） | `index.html:4414/4415/4416` 三行无守卫；`4434-4438` 保存侧把 `lyrics/cot/abc` 打包 `POST /api/templates`(4443)；MUT-F 0 红 | 预设只存 ① 自己的 12 项（`1712-1825`）， lyrics/cot/abc 既不读也不写；芯片 `title` 若保留必须逐字段写清影响哪几页 |
| F-4 | P1 → **成立 P2** | `index.html:5611-5612` 空内容分支只清 `$("abcAnalysis").textContent` 就 return；`lastAbcAnalysis` 唯一置空点 5583；`switchTab:3460` 用陈旧值重放；`5601` 改 `#cot`；`app.py:901` 空谱原样放行 | 空内容分支里补 `renderAbcAnalysis(null)`（等价于 `scoreClear` 8017-8022 那条好路） |
| F-5 | P1 → **成立 P2** | `index.html:4668-4669` 读全局 `#abc` 与 `lastAbcAnalysis`，无 `curTab` 判定；`5485` ② 恒交空谱；`1973` 摘要在 ②⑤ 都可见 | `hasAbc/route` 两项在 `curTab !== "scoreSing"` 时按"无谱"计算，与 `abcForSubmit()` 同一条判断 |
| F-6 | P2 → **成立 P2** | `index.html:5423-5424` 裸覆写 `$("cfg")/$("steps")`，同函数 5386-5390 用 `fillIfEmpty`、5404-5407 对 `#cot` 一律不动、5416-5421 seed 不回写 | 与 seed 同法：只记进一个展示变量（"这一首用的是 32/1.0"），不回写输入框 |
| F-7 | P2 → **成立 P2** | `index.html:5409-5411`（反解性别写 ① 的 `#gender`）、`5403`（`fillIfEmpty($("abc"), job.abc)`） | 删掉 5411（修好 F-2 后它本就无意义）；5403 只在 `curTab` 是 ③⑤ 时填 |
| F-8 | P3 → **成立 P3** | `index.html:5386-5390` `el.value = v || ""; return true;`（v 为空也报"填了"）；`app.py:2549` ② 任务的 abc 恒为空串 | `return !!(v || "").trim()` 之前判一下"来源值非空" |
| F-9 | P2 → **成立 P2** | `index.html:7996-7999` `#scoreSrc.onchange` 只 toggle+focus+`loadScorePicker()`；`#scorePick` 无 onchange；只有 `8003-8008` 那颗按钮才 `$("abc").value = rec.abc` | `#scorePick.onchange` 里直接载入（或提交前比对"下拉选中的 ≠ 框里的"就拦一次） |
| F-10 | P2 → **成立 P3**（无错误提交，纯标签与行为不符） | `index.html:9055` `mk("↩ 改曲风重跑", () => switchTab("style"), "primary")`；`9032-9054` 其余四颗都带 rid 干活；① 的框是 `#styleAsm`(1714)，与刚提交那句无关（`1855-1861`） | 文案改成「去 ① 重新装配曲风」，或直接 `switchTab("sing")`（② 的 `#style` 还留着那句） |
| F-11 | P3 → **部分成立（事实对、缺陷不成立）** | `index.html:8010` `if (sug) $("cot").value = sug;` 确实无守卫；但 `#scoreLoadBtn`(1962) 在 `#scoreSection`(1948) 内，`3452` `ss.hidden = !scoreMode` + CSS `109` `[hidden]{display:none !important}` → 唯一可达时 `curTab` 必为 `scoreSing`，加守卫等于死代码 | 不必改代码；要改的是把 `tests/test_page_contract.py:347-349` 那两条"整句文本比对"换成按 DOM 归属判定，否则这条永远只是纸面一致性 |
| F-12 | P3 → **成立 P3** | `index.html:5966` `String(h.cot_note).slice(0, 120)`，同一行 `title` 是固定说明文字不含本体；上一行 error 有 `data-err` 点击展开，这行没有；`app.py:2548/2889/2992` 允许 400 字，`896-909` 最多拼四条 | 加 `data-note` 展开（与 error 行同一套），或直接不截 |
| F-13 | P3 → **成立 P3** | `index.html:5211` `$("go").innerHTML = … "生成歌曲"`；⑤ 专用文案只由 `7892-7897` 的 `updateBatchSummary()` 刷 | `setGenerating(false)` 后补一次 `updateBatchSummary()` |
| F-14 | P3 → **成立 P3** | `index.html:1516` `#tab-style .pane-out #style {…}` 永不匹配（`#style` 在 1859，属 `#tab-sing` 的 `#styleBox`/`#styleSlotGen` 1857）；① 真正生效的是 `819-821` `flex:none; min-height:108px; max-height:240px`；`#tab-style` 作用域内只有 861/862/1516 三条 | 删掉 1511-1517 整块（含那条已失效的注释），或把选择器换成 `#styleAsm` |
| F-15 | P3 → **不成立（把注释读成了承诺）** | `index.html:9333-9335`：注释主语是"首屏归零"（`scrollRestoration=manual` + 最后 `requestAnimationFrame(scrollPageTop)`），"这里补一次"读作"补一次归零"比读作"补一次 switchTab"更贴上下文；且蓝军关心的 `#batchSum`(2018)/`#modelNow`(1975)/`#scoreModeTag`(1949) 全在 `#genForm`/`#scoreSection` 里，① 落地时它们随 `#tab-sing` 隐藏，用户要到它们必须先 `switchTab` → 届时 `3457/3461` 已重算；`#paramSum` 更是开机就 `4884` 算过 | 撤回该条；如嫌注释歧义，只把 9333 那句改成一个逗号都说清"补一次归零" |

### G3（测试有效性与发布诚实度）

| 编号 | 蓝军级别 → 我的判定与级别 | 一手证据 | 一句话整改 |
| --- | --- | --- | --- |
| T-1 | P1 → **成立 P1** | MUT-A 0 红；`test_submit_paths_agree.py:85-87` 全文只有 `assertIn("h.cot_note", html)` | 让用例真解析那段模板（抽出模板字符串跑一遍条件渲染），或直接断 `/api/tasks/unified` 回包字段 |
| T-2 | P1 → **成立 P1** | MUT-B/D 各 0 红；`:32-34` 对批量只 `assertIn("_style_yield_to_abc", self.batch)` | 补一条"批量入队带纯旋律谱 + 无收尾段歌词 → 队条目 `cot_note` 同时含两条说明"的行为用例 |
| T-3 | P1 → **成立 P1（数字我复现并做硬）** | 见 2.1：grep=497、loader=497、worktree 打环境补丁后 `Ran 497 … OK (skipped=1)`；差值 37 精确落在 `test_abc_edit.py`（21→58）；v10.14.1 同病（482 vs 519） | 发版数字必须在"只含本提交内容"的口径下取（clean worktree + 环境清单），或改写成"标签口径 497 项全绿" |
| T-4 | P2 → **成立 P2** | MUT-C（保留字面量、缺省退回 off）0 红；`tests/` 里无一条 POST `/api/batch/start` 且省略 `cot` 断言落地 full | 用 TestClient 真发一次不带 `cot` 的批量请求，断 `payload.cot == "full"` |
| T-5 | P3 → **成立 P3** | MUT-J（单引号 `<i>`）不红、MUT-K（`[...pool]`）红 1；`test_submit_paths_agree.py:117` 的 `[-260:]` 窗口断言；`test_page_contract.py:317` 剥行注释、`test_design_system.py:1009-1011` 不剥（当前 `index.html` 里 `MULTI_LIMIT/multiOpen` 0 命中，所以这条只是将来会咬人的脆弱） | 把"实现细节型"断言降级为行为断言，两个文件的剥注释口径统一 |
| T-6 | P2 → **成立 P2** | MUT-M 0 红；`assertIn('"loaded_vae_from"', src)` 的字面量在 `app.py:1731`（写侧）还有一份；只读 GET 实测 `loaded_vae: "yue2-vae-f16.gguf（同目录）"` | 用例改成断 `system_info()` 返回的字符串里带来源，而不是断源码里有那个键名 |
| T-7 | P2 → **成立 P2** | 见 2.5 最后一行（函数体 2874 字符 / 窗口 3000 / 三个控件在 `_FORM_IDS` 9243 / 测试不提它们 / MUT-G 0 红） | 把 `resumeRunningJob` 里的裸覆写改成白名单式"这些格子永不写"，用例枚举同一份白名单 |
| T-8 | P3 → **成立 P3，但蓝军证据作废、判据换成我的** | 蓝军 M19 记"红 0"是在那 12 条本来就红的副本里量的。我在补齐 `py312/python.exe` 桩 + vendored catalog 后真跑：MUT-L（越界 0.75）**红 12 条**（附带原因，`protect须在 0.0-0.5 之间`），MUT-L2（只在范围内换 0.5→0.75、0.25→1.0）**0 红** → 结论（无行为锁）成立 | 补一条 multipart 省略 `index_rate/protect/rms_mix_rate` 的用例，断言落到 0.5/0.33/0.25 |
| T-9 | P2 → **成立 P2（性质判定加强）** | 见 2.2：163 份 meta 0 份含 `cot_note`、最新 mtime 10:03:13、进程 11:43:23 早于 ac9bc19(11:56)/tag(12:03)、`reload=False`(8887)、184 条统一任务 0 条带 note、`app.py:2548/2889` 说明该键一旦出现必进 meta | 重启后真跑一次并出示产物，或把这句从 CHANGELOG 删掉 |
| T-10 | P1 → **成立 P1** | 见 2.3：`f9f7879` = `0 新增 / 45 删除`，三条 `test_mod_strip_*` 被删，d08c518 的 `tests/` 对 `modTasks/modEnter/MOD_KINDS/本页任务` 0 命中，CHANGELOG:78 却写了这句 | 见 2.3 整改 |
| T-11 | P3 → **成立 P3** | `app.py:2273-2303` 写 `cpu_fallback/gpu_error/note`，`_unified_task`(1390) 的 out 只有 kind/id/name/status/step/progress/ts(+1408-1409 白名单) 里没有这三项；前端只有 `5275` 一行进度文案用过 `job.cpu_fallback` | 把 `cpu_fallback` 并进同一条 `cot_note`（"本次因显存不足改用 CPU 后端"），比再加一个白名单键更省 |
| T-12 | P3 → **部分成立（现象真、归因假）** | 对 d08c518：`cot_note` 在 2548（CHANGELOG:34 写 2553）、批量缺省 full 在 2975（写 2980）、`index_rate … Form(None)` 在 5470（写 5475）→ 各偏 5 行。但蓝军"已核对：在制品只在 `_score_save` 附近，主树这几处行号同样不是 2553/2980/5475"是**错的**：脏树里 grep 得 2553 / 2980 / 5475 / `reload=False` 8892，**恰好就是这三个数**。`#styleBox 必须恰好两个` 与实现不符属实（1857 一份 `#styleBox` + 1712 一份 `#styleBoxAsm`，测试断的是"各 1 个"，`test_page_contract.py:132-137`） | 发布文档的行锚必须从"将要打标签的那棵树"数（本轮和 534 是同一个根因：文档是对着脏树写的）；蓝军该把这条并进 T-3 而不是另立一个错因 |
| T-13 | P3 → **成立 P3** | `ls scripts/` 只有 `download_models.py / gtcrn.py / register_protocol.bat / 各启动与局域网 bat/ps1`；全仓（排除 dsh-plugin）搜 `captureScreenshot|9222|--headless` **0 命中**；仓库内只有 `test_design_system.py` 的静态色板核算 | 要么把无头扫描脚本入库并能一键复跑，要么在 README 把"归零"标注成一次性人工测量 |
| T-14 | P2 → **成立 P2（与 F-3 同一处，属重复计数）** | `index.html:1765 #myPresetChips` 在 `<section id="tab-style">`(1684-1841) 内；`4414/4416` 无守卫；`test_page_contract.py:347-349` 只钉了 ③ 的两处；MUT-F 0 红 | 同 F-3；另建议裁定员合并 F-3/T-14，避免同一缺陷计两次 |
| T-15 | P3 → **成立 P3（与 F-8 同一处，重复计数）** | `5386-5390` + `5403` | 同 F-8；建议与 F-8 合并 |

### G3 未编号的发布文档判定（表格那几行，我单独复核定性）

| 蓝军那行 | 我的判定 | 证据 |
| --- | --- | --- |
| CHANGELOG:26「`abcForSubmit()` 只在 **③** 返回谱内容，②⑤ 恒返回空串」 | **成立（文档把两页写反）**。但蓝军把它标成 "（T-6）" 与 vae 那条撞号，应独立编号 | `index.html:5485` 条件是 `curTab === "scoreSing"`；页签表 `tests/test_page_contract.py:24-31` 与导航 `1647` 都说明 `scoreSing` = **⑤ 歌词+style+乐谱生成歌曲**，`score` = ③ 提取乐谱；③ 没有提交口 |
| CHANGELOG:22「② 与 ③⑤ 共用同一块 `#abcBlock`」 | **成立（描述不精确）** | `3449`/`5478` 均写"③ 与 ⑤ 共用"；`moveInto($("abcBlock"), scoreMode ? "abcSlotSing" : "abcSlotScore")`，② 期间谱框停在 ③ 的槽 |
| CHANGELOG:62「234 行删除、0 行保留」 | **数字属实、口径不诚实**（与 T-10 同源） | `git show --numstat f9f7879`：index.html `0/189`、tests `0/45` |
| 发布清单 1 全量测试绿 | 见 T-3（做了，但对外数字错） | — |
| 发布清单 2 重启后冒烟 | **需人工/不能证实**：重启后 0 产物，四项无落盘 | 见 2.2 |
| 发布清单 3 README/PROMOTION 同步 | **成立（README 时序错位；PROMOTION 本轮 0 改动按它自己的"版本号见 CHANGELOG 顶部条目"可算满足）** | `git diff --stat v10.14.1 d08c518 -- docs/PROMOTION.md` 空；README.md 里 v10.16.0 行(327)夹在 v10.13.0 与 v10.14.1 之间，README_EN.md:173 同样插在 v10.12.0 之后 |
| 发布清单 4 推标签 | **需人工**：本地 `v10.16.0` 是附注标签、`git rev-list --count origin/master..master` = 15；`git ls-remote` 走 127.0.0.1:7890 代理连不上（`Failed to connect to github.com:443 over proxy`），远端状态我这边测不了 | 与蓝军一致：代理未监听不计缺陷 |

---

## 四、按我的级别汇总（只列有级别的）

- **P0**：F-1（⑨ 服务器素材必崩）＋ F-2（① 的 gender/bpm 改写 ②⑤ 提交，P0 候补）
- **P1**：T-3、T-10（发布诚实度）；F-3/F-4/F-5 我判 P2（蓝军 P1）
- **P2**：F-3、F-4、F-5、F-6、F-7、F-9、T-1、T-2、T-4、T-6、T-7、T-9、T-14
- **P3**：F-8、F-10、F-12、F-13、F-14、T-5、T-8、T-11、T-12（部分）、T-13、T-15
- **不成立/非缺陷**：F-15、F-11（作为缺陷）；G1 的 F-11/F-15 保留为"信息级/编辑级"备注

**只允许修三处再发版**，我的排序：
1. **F-1**（删 `src_name: f.name`，一字节风险、一行改法）；
2. **F-2**（②⑤ 不再吃 ① 的 `#gender`/`#bpm`——这一改顺带把 F-7、T-7 里"刷新改性别"和 F-3 的一半影响一起消掉，并且它是唯一"用户听到的不是自己要的歌"级别的活 bug）；
3. **T-3 + T-10 一起改文档**（把 534→497、把"任务条组件不得复活"这句要么删要么补成真断言）；F-4/F-5 作为第 4、5 位（同族两行，成本极低：`5611` 补 `renderAbcAnalysis(null)`、`4668-4669` 加 `curTab` 判定）。

---

## 五、我对蓝军的两处纠错与四条补充

1. **纠错（G3/T-12 归因）**：那三个行锚不是"数错了 5 行"，是**照脏工作树数的**（2553/2980/5475/8892 在主树全部命中，`reload=False` 在 d08c518 是 8887）。这与 T-3 的 534 是**同一个根因**：本轮发布文档是对着未提交的工作树写的。蓝军的"反例尝试已核对、未推翻"这一步没做对。
2. **纠错（G3/M19-T8 判据）**：在"覆盖用例本身已因缺 py312 而红"的副本里判"未锁"，等于没测。补桩后我的 MUT-L 反而红 12 条（附带原因），所以必须用范围内的 MUT-L2 才得出"0 红"。结论不变，证据要换。
3. **补充（F-1 的改法与恢复路径）**：`app.py:5548-5549` 已经把 `src_name` 算成"服务器素材 <spec>"，且 6918 的 `...(d.job||{})` 展开在其后 → 删掉 `src_name: f.name` 就修好；`restoreRvcActive()`(6411-6423) 能在刷新时捞回任务，所以不是永久丢数据（严重度仍 P0，但报告该写清楚）。
4. **补充（F-2/F-3 完全没有测试）**：蓝军 G3 只测了留痕链与刷新链，没测这两条。我的 MUT-E / MUT-F 双 0 红 → 招牌修复的同族里，**① 的控件写进 ②⑤ 提交**这件事一条灯都没有。
5. **补充（F-15 判过头）**：`9333-9335` 那句"这里补一次"承接的是"首屏归零"，不是 `switchTab`；蓝军关心的三处摘要所在容器在默认落点（①）本来就隐藏，用户要到它们必先切页、切页就会重算。这条应撤回。
6. **补充（审计手册）**：在缺 `main.cp312-win_amd64.pyd` 的副本里，`python -m unittest tests.test_submit_paths_agree` 单跑会 ImportError（`main` 的替身模块由 `test_review_fixes.py:31-37` 装）。后来者请统一用 `discover -s tests`，或用 `tests.test_review_fixes tests.test_submit_paths_agree …` 的顺序跑，否则会误判成代码坏了。

---

## 六、纪律自证

- 主工作树全程未改：审计前后 `git status --short` 同为 4 个 M（`app.py`、`src/abc_edit.py`、`static/index.html`、`tests/test_abc_edit.py`）+ 3 个 ??（蓝军三份报告），`git diff --stat` 前后同为 `4 files changed, 730 insertions(+), 67 deletions(-)`；本文件是唯一新增文件（?? 第 4 项）。
- `tests/` 未改；未重启网关（PID 23144 的 StartTime 仍是 2026-10-16 11:43:23）；未调用任何 POST/写库接口，只做了 `GET /api/system/info`（返回 `version: v10.16.0`、`loaded_main: yue2-q4.gguf`、`loaded_vae: yue2-vae-f16.gguf（同目录）`）、`GET /api/tasks/unified?limit=500`、`GET /api/history`；未启动任何 GPU 任务。
- worktree：`…\Temp\qoder\wt-audit`（d08c518 分离 HEAD，内含我加的一次性 `py312/python.exe` 桩与一份 vendored catalog 拷贝）已 `git worktree remove --force`；`git worktree list` 现只剩主树 `E:/AI/10AIMusic/Yue/yuE-2/yuE-2 d08c518 [master]`。
- 只读导出副本：`C:\Users\59221\AppData\Local\Temp\qoder\rev3\{index.html,app.py,CHANGELOG.md}`；因蓝军 G3 记录过"主树跑全量会把 `%TEMP%\qoder\rev` 当临时目录清走"的事故，我先镜像了一份到 `%TEMP%\yueAuditBase` 做本体阅读，行号与 rev3 一致。
