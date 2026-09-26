# BLUE-TEAM-REVIEW — 基线 `308f833` + 工作区未提交改动 · G4 组

- **基线**：commit `308f833`（"fix: 乐谱非空即按谱生成"）+ 未提交改动 `README.md` / `README_EN.md` / `app.py` / `src/sheetsage_pt.py` / `static/index.html`（+5 文件，257 插 / 72 删）。
- **审查组**：G4 — 文档一致性 / 发布工程 / 测试有效性 / 兼容与迁移 / 架构。
- **选定维度**：17 文档一致性、16 发布工程、10 测试有效性、14 数据迁移与状态演进、11 兼容性、2 架构与设计。
- **立场**：不采信文档与注释；能实测的实测。全程只读（`git` / `grep` / `py_compile` / 只读 `GET 127.0.0.1:7863` / 读 `runtime/` / 在 `Temp\blue-g4\` 里用 AST 抽出 app.py 纯函数离线执行，未写仓库任何文件、未触发任何 POST/计算）。
- **实测手段清单**：`git ls-files`/`git check-ignore`/`git show`、GitHub releases API、`main.cp312-win_amd64.pyd` 字符串抽取、`py_compile`、对真实 `runtime/data/batch_state.json` 做新逻辑的只读仿真、对真实 `runtime/output/` 做产物格式统计。

---

## 一、缺陷总表（按严重度排序）

| 编号 | 级别 | 一句话 | 维度 | 位置（文件:行号） |
|---|---|---|---|---|
| D1 | 🔴 | README「三步开唱 git clone→双击 bat 就能唱」不可成立：Python 环境、编译内核、推理引擎、SheetSage2 权重全部不入库且无 Release 包可下 | 17/16 | README.md:88-97；.gitignore:4,38,59,116；scripts/download_models.py:40-45 |
| D2 | 🔴 | 人声分离链路依赖作者机私有绝对路径且硬写 `--device cuda`，与 README「无独显也能跑」「完整歌曲自动分离人声」直接矛盾 | 17/11 | app.py:2157（GSV_ROOT）、app.py:2240（`--device cuda`）、app.py:2259-2263（降级链判空即抛）；README.md:54,169 |
| D3 | 🔴 | 头条功能「逐字卡拉OK歌词 / .elrc 逐字高亮」在前端 0 个消费者：UI 只下载 `.lrc`，`elrc` 字段是死接口，eLRC 也不是任何播放器认的标准 | 17/10 | README.md:18,24,53,153,187 vs static/index.html:2071,2227-2254（全文无 `elrc`）；app.py:1009-1010,1556-1558 |
| D4 | 🔴 | `docs/PROMOTION.md` 对外广播已被本项目自己废弃的 ASR 方案，并把贡献者指到旧兜底文件 `src/lrc.py`；同一仓库对生态给出相反事实 | 16/17 | docs/PROMOTION.md:19,57,80 ↔ README.md:53,153,187；src/lrc.py（仍存在）vs src/lrc_align.py |
| D5 | 🟠 | 本次改动不是可原子发布的单元：前端已现读生效、后端 reload=False 仍旧码，运行中的"新 UI + 旧和弦判定"恰好把带 `"Cm(maj7)"`/`"G/B"` 的完整谱判成旋律谱 | 16/14 | app.py:506-513（新闭集，未生效）vs baseline `git show 308f833:app.py` 宽松式正则；static/index.html:1844-1847；实测 `/api/batch/status` 无 `queue` 字段 |
| D6 | 🟠 | 队列 ID 迁移路径失效：真实状态文件 58 条全无 `qid`，只读时"当前队列"退化为全量 58，追加时又另发新 ID 并把未跑完的旧条目算成"历史队列记录" | 14/17 | app.py:1727（`old_items[-1].get("qid")`）、app.py:1825-1826（空串兜底）；README.md:55,205；runtime/data/batch_state.json（实测 0/58 含 qid） |
| D7 | 🟠 | 和弦闭集漏 `"C9" "Cadd9" "Cmaj9" "G13" "Fm9" "C7b9" "Csus" "C5" "A7alt"`，实测判为"无和弦"→ 走 melody，且乐谱框对用户断言"纯旋律谱（无和弦记号）" | 17/11 | app.py:506-513（_ABC_QUALITIES）；static/index.html:1822-1828；README.md:48,193 |
| D8 | 🟠 | 中英文 README 事实冲突（非翻译差异）：英文版仍写歌词来自 "whisper/SenseVoice alignment"、架构表把引擎写成 `src/lrc.py`，无强制对齐/eLRC 任何描述 | 17 | README_EN.md:35,74,102 ↔ README.md:53,143,153,187 |
| D9 | 🟠 | AI 创作助手的系统提示词依赖仓外私有目录 `E:/AI/10AIMusic/Yue/YuE2-skills`，缺文件时 `except: continue` 静默降级，用户无从察觉 | 11/17 | src/ai_prompt.py:12,15-18,40-41,23,25；README.md:49 |
| D10 | 🟠 | 许可/商用三份口径互相矛盾，且仓库根本没有根 LICENSE 文件（徽章却写 CC-BY-NC-4.0） | 16/17 | README.md:11,217,229 ↔ README_EN.md:87,9,106；`ls LICENSE*` = 无（仅 checkpoints/LICENSE） |
| D11 | 🟠 | 端口唯一真源承诺有漏网：ai_router 把 3081 写死在探活 URL、netstat 击杀匹配与拉起参数里，改端口后会去杀 3081 上的无关进程 | 11/17 | src/ai_router.py:63,70,82,107；src/ai_prompt.py:23；scripts/停止音乐工作台.bat:32；README.md:59,148,130 |
| D12 | 🟠 | 全仓 0 个可运行回归测试、0 CI；仓库自认验证形式只有 `py_compile`/`node --check`，且明确写着"运行时验证待网关重启后进行" | 10 | 无 test_*.py / 无 .github/（实测）；docs/review/RESPONSE-e32bbb5.md:44-46；实测 runtime/output 194 个产物全为旧 4-hex ID、0 个 8-hex |
| D13 | 🟡 | 删除任务不清理 `.elrc`（实测 output 存留孤儿 `20260926_033553_8d45.elrc`，同名 wav/json 已删） | 14/2 | app.py:1589-1590（清理列表）、app.py:1923-1925（batch_delete 只删 wav/json） |
| D14 | 🟡 | 558f7c5 评审点名的死代码仍在库内：根 `audiocpp.py`、`src/chunking.py` 全仓 0 引用（实测） | 16/2 | audiocpp.py:1、src/chunking.py:1；docs/review/ADJUDICATION-558f7c5.md:35 |
| D15 | 🟡 | 历史评审报告的 `文件:行号` 全部因整改漂移（merge 1902→2020+、_rvc_latest_export 2502→2799、index.html 2782→2800+） | 16 | docs/review/ADJUDICATION-e32bbb5.md:11,30 ↔ app.py:2020,2799 |
| D16 | 🟡 | 无 CHANGELOG、无版本文件，仅一个 tag `v1.0`，而 PROMOTION 仍以 v1.0 对外宣传当前含未发布行为的工作区 | 16 | `git tag`=v1.0；无 CHANGELOG*；docs/PROMOTION.md:13,80 |
| D17 | 🟡 | 根启动 bat 把作者机路径当用户指引：缺 py312 时报"你双击的不是本项目的启动脚本，请到 E:\AI\... 下运行" | 11/16 | 启动音乐工作台.bat:7（`git show HEAD:启动音乐工作台.bat` 已核） |
| D18 | 🟡 | `checkpoints/` 是 558f7c5 一次性 vendored 的上游代码（39 文件），`trust_remote_code` 直接吃目录内 .py，却无上游 commit/版本记录；换官方权重即覆盖，本次新增的"闭集=_QUALITY_TO_ABC 15 项"耦合会静默漂移 | 11 | src/sheetsage_pt.py:67-85,110-114；checkpoints/notation_sheetsage2.py:108-126；`git log -- checkpoints` 仅 558f7c5 |
| D19 | 🟡 | README「重启服务都不丢任务」只对队列/存档成立，在跑那一首必被标 error「服务重启，任务中断」；FAQ「CPU 慢约 5-10 倍」无任何数据（96 个 meta 里 0 个 cpu_fallback） | 17 | app.py:1813-1817；README.md:57,169；runtime/output/*.json 实测 |
| D20 | 🟡 | 对 8080 引擎的 CPU 回退判定靠自由文本正则猜（`memory\|oom\|vram`），无错误码契约；相比之下 `X-Seed` 头是实测有效（95/96） | 11 | app.py:1120-1124,1277；app.py:1298 |
| D21 | 🟡 | 外层与不可改内核的边界靠 monkeypatch 维持（`app = main.app`、改 `main.settings.open_browser`、直接重排 `app.routes`），而 README 架构图/目录表完全没有 `main.cp312-win_amd64.pyd` 这一层 | 2 | app.py:43,57,61,3488-3493；README.md:136-146,150-162 |
| D22 | 🟡 | 转谱产物写死到共享目录 `sheetsage2-output/`，而 `_score_run` 在无锁后台线程执行：两次转谱并发会互相覆盖中间文件（本次删回读是必要的一半，并发那一半没动） | 2/11 | src/sheetsage_pt.py:152,167；app.py:685,645-664 |

---

## 二、🔴/🟠 逐条展开

### D1 · README 的三步开唱在真实仓库里跑不起来 🔴（17 文档一致性 / 16 发布工程）

**证据链**
1. README.md:88-97 给出的唯一安装路径是 `git clone` → 双击 `启动音乐工作台.bat` → 浏览器自动打开。
2. 该 bat 的第一行判断是 `if not exist "%~dp0py312\python.exe"` 即报错退出（`git show HEAD:启动音乐工作台.bat:4-9`）。
3. `.gitignore:4 py312/`、`:38 *.pyd`（`git check-ignore -v main.cp312-win_amd64.pyd` → 命中 `.gitignore:38`）、`:59 cpp/`、`:116 checkpoints/model.safetensors` 四条规则把 **Python 环境 / 编译网关 / audio.cpp 引擎与可执行文件 / SheetSage2 权重** 全部排除。
4. `git ls-files` = 111 个文件，无上述任何一项；`git status` 显示本地与 `origin/master` 同树，即远端也只有这 111 个文件。
5. `curl https://api.github.com/repos/RevolutionLA/YuE2-Music-Workbench/releases` → `[]`（无任何 Release/压缩包可下）。
6. `scripts/download_models.py:40-45` 只下载 `yue2-3b-q4_k_m.gguf` + `yue2-vae-f16.gguf` 到 `cpp/model/`，不下 `.exe`、不下 `.pyd`、不下 `python.exe`。
7. README 全文（grep `py312|环境|依赖|安装`）没有任何一句说明如何获得这些运行件，反而在 :108-109 直接教用户执行 `py312\python.exe scripts\download_models.py`——引用一个 fresh clone 里不存在的目录。

**触发条件**：任何用户照 README 操作。
**用户可见后果（大白话）**：克隆下来双击启动脚本，只看到"未找到 py312\python.exe"，且脚本还告诉他"你双击的不是本项目的启动脚本"（见 D17）。文档承诺的"一键开唱"对外是 100% 失败路径；这是 README 对用户的实质性误导，也说明这个仓库目前不是一个可发布的开源交付物。
**修复方向**：要么提供带运行件的 Release（并把 README 的 clone 步骤改成"下载压缩包"），要么在 README「三步开唱」前插一节"运行件从哪来"（py312 环境、`main.cp312-win_amd64.pyd`、`cpp/audiocpp_server.exe`、SheetSage2 `model.safetensors` 各自获取方式与校验），并让 bat 的缺失提示逐项指路。

### D2 · 人声分离硬绑作者机路径 + 硬绑 CUDA 🔴（17 / 11）

**证据链**
1. `app.py:2157` `GSV_ROOT = Path(r"E:\AI\10AIMusic\GPT-SoVITS-v2pro-20250604")`，`GSV_PY/SEP_ROFORMER/SEP_HP5` 全部由它派生（:2158-2161）——**仓库跟踪文件里的私有绝对路径**。
2. `app.py:2240` PyMSS 分离子进程固定 `--device cuda`，没有 CPU 分支。
3. `app.py:2236` 只判 `vocal_sep_available()` = `runtime/rvc/tools/pymss/workflow.py` 是否存在（:2163-2164）；该目录在 `runtime/`（gitignore:2）内，fresh clone 不存在。
4. PyMSS 存在但 CUDA 失败时走降级：`app.py:2259-2261` `if not (GSV_PY.is_file() and ...): raise RuntimeError("人声分离（PyMSS）失败且旧链不可用")` → 唯一兜底就是那个 E:\ 路径。
5. README.md:54 承诺"上传干声训练专属音色库：**完整歌曲自动分离人声**"；README.md:169 承诺"没有 NVIDIA 显卡能跑吗？能"；README.md:86 说"无独显也能跑，自动走 CPU"。

**触发条件**：无独显用户（README 明确招揽的群体）上传整首歌做音色训练/换声分离；或任何非作者机用户走 PyMSS 失败分支（模型未下载、断网、显存不足）。
**用户可见后果**：分离必失败，错误里出现一个用户机器上不存在的路径名；训练/换声主路径中断。同时"自动分离人声"这项在文档里是无条件承诺。
**修复方向**：`--device` 跟随 `backend_mode()`/`torch.cuda.is_available()`；GSV 路径改 env/settings 可配且缺失时报"未配置备用分离链"而不是隐式依赖；README 把"完整歌曲自动分离人声"标注硬件前置条件（守"诚实标注限制"的自家规矩）。

### D3 · 「逐字卡拉OK歌词」没有实现体 🔴（17 / 10）

**证据链**
1. README.md:53 行标题带 ⭐：「**逐字卡拉OK歌词** … 产出标准 `.lrc` 与逐字高亮的增强 `.elrc`」；:18 徽章 `歌词-逐字高亮`；:24 首屏标语「逐字卡拉OK歌词」；:153 架构表「增强 ELRC 卡拉OK逐字高亮」；:187 FAQ「且支持逐字高亮的卡拉OK效果」。
2. 生产侧真实存在：`src/lrc_align.py:29,243,517,545-556` 生成 elrc 文本；`app.py:1009-1010` 落盘 `<rid>.elrc`；`app.py:1556-1558` 把它塞进 `GET /api/generate/lrc/{rid}` 响应的 `elrc` 字段。
3. 消费侧为 0：`grep -in "elrc" static/index.html` → **无匹配**；`grep -rn elrc` 全仓（排除 node_modules/runtime/py312）只命中 app.py、src/lrc_align.py、docs、README。前端对歌词只做一件事——`index.html:2071/2227-2254` 下载 `.lrc` 文本文件；页面内连滚动歌词都没有，更没有逐字高亮。
4. `.elrc` 是本项目自造格式（`[mm:ss]<mm:ss>WORD`），非任何播放器的标准；README.md:153 用"增强 ELRC"暗示它是可用产物。

**触发条件**：用户按 README 期待在工作台里看逐字卡拉OK高亮，或把 `.elrc` 拿到外部播放器里。
**用户可见后果**：README 的头牌卖点（⭐ + 徽章 + 首屏标语 + FAQ）对用户不可达；`elrc` API 字段与磁盘文件属于"只产不看"的死功能，会持续误导后续迭代（没人能发现它坏了，因为没消费者）。
**修复方向**：要么在前端播放坞里真做 `<mm:ss>` 内联高亮（数据已在同一个响应里，成本很低），要么把措辞降级为"额外产出逐字时间轴文件 `.elrc`（自有格式，需在应用内播放才生效）"，并同步中英两份 README 与徽章/标语。

### D4 · PROMOTION.md 向生态广播与 README 相反的技术事实 🔴（16 / 17）

**证据链**
1. `docs/PROMOTION.md:19`（发 r/LocalLLaMA 的正文）："Auto LRC synced-lyrics generation (**SenseVoice + whisper dual alignment pipeline**)"。
2. `docs/PROMOTION.md:57`（发 YuE 官方 GitHub Discussions 的正文）："auto .lrc synced lyrics (**SenseVoice+VAD primary, whisper segment-anchor fallback**)"、"the alignment pipeline details are in **`src/lrc.py`** if anyone wants to reuse it"。
3. README.md:187 的 FAQ 标题就叫「歌词对齐为什么不是"识别"出来的？」，正文写"老方案用语音识别（ASR）反推时间轴，歌声上 ASR 漏识率高达 20-30%（实测错得离谱）"、"新方案走强制对齐"；README.md:53 写"歌词强制对齐（**非 ASR 识别**）"。
4. 代码现状：`app.py:48-54` 同时 import `lrc`（旧）与 `lrc_align`（新），`app.py:1001-1014` 主路径强制对齐、仅在强制对齐未产出时才回退 `lrc_mod`。即 `src/lrc.py` 是兜底，不是引擎。
5. `git ls-files` 确认 docs/PROMOTION.md 入库跟踪；文件头 :3 还指导用户"发帖前把「【】」占位符替换成实际内容"——也就是说这些错误陈述是按"直接复制发布"设计的。

**触发条件**：作者或任何读者按该模板发帖（这正是文件的存在目的）。
**用户可见后果**：在 Reddit / YuE 官方 Discussions / Discord 上把一个已被本仓库判定为"错得离谱"的 ASR 方案当成卖点广播，并把想复用代码的人指到兜底文件；对上游社区的错误信息，属于对外广播错误事实。
**修复方向**：PROMOTION 全篇与 README.md:53/143/187 对齐（强制对齐、fa-zh/CTC/VAD、指向 `src/lrc_align.py`），并在模板里加一行"发帖前先对齐 README 的功能措辞"。

### D5 · 未提交改动不是一个可原子发布的单元（当前运行态就是反例） 🟠（16 / 14）

**证据链**
1. 前端现读磁盘、后端 `reload=False`：`curl http://127.0.0.1:3081/lab/index.html | grep -c queueBar` = 2、`grep -c abcChords` = 6 → **新前端已在服务**；`curl http://127.0.0.1:7863/api/batch/status` 返回体只有 `running/name/current/total/done/error/pending/items`，**无 `queue`/`queue_id`/`queued_hist`** → 网关仍跑 `308f833` 的旧 app.py。
2. 旁证：`runtime/output` 194 个产物文件名全是旧 `时间戳_4hex`，`^[0-9]{8}_[0-9]{6}_[0-9a-f]{8}` 命中 **0** 个 → 工作区 `_new_id()` 从未产出过一个真实文件。
3. 新旧和弦判定不等价：旧正则（`git show 308f833:app.py`）为 `"[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus|add)?[\d]*"`，**不认** `Cm(maj7)`（括号）、`7sus4`、`m7b5`、双变音根音与斜杠低音；而 `checkpoints/notation_sheetsage2.py:108-126` 的 `_QUALITY_TO_ABC` 恰好会产出 `m(maj7)`/`m7b5`/`7sus4`，`chord_symbol_to_abc`（:623-624）还会写斜杠低音。
4. 新前端 `static/index.html:1844-1847` 直接用后端 `rj.analysis.cot_suggested` 把 `$("cot")` 拨到该路线。
5. 网关不能随手重启：README.md:57 与 `app.py:1813-1817` 共同说明"重启时正在跑的那一首会被标 error 服务重启，任务中断"；用户此刻正有一个批量队列在跑（`batch_state.json`：`running=true`、`current=20260926_170851_3a9a`、17:09 起）。

**触发条件**：文档/前端改动落盘而后端未重启的整个窗口（本次评审当场就在这个窗口里）。
**用户可见后果**：勾了「提取时带和弦」的用户，用旧判定逻辑拿到被判成"纯旋律谱"的完整谱，前端顺手把档位改成 `melody`——正是本次改动要消灭的错配，且在用户看不到任何提示的情况下发生。整个改动集没有一句"必须重启网关才生效"的说明（无 CHANGELOG、无迁移注记）。
**修复方向**：本次 diff 作为一次提交发布时，README/提交信息里显式写"需重启 7863 生效，重启会中断在跑的一首"；更彻底的做法是让后端能力可探测（`/api/health` 带 capability 位，前端按位启用 queueBar/和弦档位联动），避免前端跑在协议前面。

### D6 · 队列 ID 在真实的旧状态文件上不成立 🟠（14 / 17）

**证据链（对真实 `runtime/data/batch_state.json` 做只读仿真，脚本 `Temp\blue-g4\probe_batch.py`）**
- 现状：顶层 keys `['items','running','current','name']`（无 `qid`），58 条 items **0 条含 `qid`**，`name="寻兰"`，`running=true`，status 分布 done 56 / running 1 / pending 1。
- **Case 1（升级后只看不动）**：按 `app.py:1825-1826` 计算 → `cur_qid=''`、`cur = 全部 58 条`、`queued_hist = 0`。面板"当前队列"仍显示 58/56 —— README.md:55「面板不再把历史条目算进这批的总数」、README.md:205「投 2 首就是这 2 首的队列」在升级后的**第一次新提交之前不成立**。
- **Case 2（在还有 pending 的旧队列上追加 2 首）**：`app.py:1721` `live=True` → `app.py:1727` 取 `old_items[-1].get("qid")` = 空 → **另发一个全新 qid**，同时 `qname` 沿用旧队列名"寻兰"。队列条结果：`队列「寻兰」 · <新ID> · 0/2 完成 · 另有 58 条历史队列记录`。而这 58 条里有 2 条（1 running + 1 pending）**还没跑完**，被文档和 UI 一起叫成"历史队列记录"。这正是 `app.py:1725-1726` 注释声称要防止的"名字是老的、总数只算刚追加的几首"，也是 README.md:205「往还在跑的队列里追加时，新任务沿用那支队列的名字和 ID」——名字沿用了，ID 没沿用。
- 代码里连兜底理由都是错的：`app.py:1824` 注释写「续跑/重试会往队尾塞条目，不能拿末条的 qid 当队头」，但 `batch_resume`（:1871-1875）与 `batch_retry`（:1905）都是原地翻 status、从不塞新条目；真正的失败原因是旧数据没有 qid。
- 顺带：`batch_resume`/`batch_retry` 复活一个**属于旧队列**的 error 条目时，`cur_qid` 仍是新批次 → 该条目的进度不进队列条统计（`app.py:1826`）。
- **Case 3（回滚）**：旧代码不含 qid 读写，读新状态文件不崩（`appending=bool(items)` 正常）；但旧 `batch_start` 的 `state = {"items": items, ...}` 整字典替换路径一旦命中（队列为空时）**会静默丢掉 `qid` 键**，新代码随后把它当 Case 1 处理。回滚不炸，但会倒退状态语义。

**用户可见后果**：作者本人这台机器一升级就会先看到"58 条还是这一批"，再看到"58 条未跑完的记录被称作历史"。用户按 README 理解到的"一次提交=一个队列身份"给不出可复现的行为。
**修复方向**：在 `_batch_read()`/`batch_status()` 里对缺 `qid` 的条目做一次性回填（按 `name` + 连续段 + `state["name"]` 归组并落盘），并在 `appending` 分支优先取 `state.get("qid")` 而非 `old_items[-1]`；缺 ID 时宁可复用队列名对应的旧 ID，也不要新 ID + 旧名。

### D7 · 和弦闭集把真实和弦判成"无和弦"，并且对用户明说假话 🟠（17 / 11）

**证据链（AST 抽出 `app.py` 的 `_CHORD_RE/_abc_chord_tokens/_abc_has_chords/_resolve_cot` 离线执行，脚本 `Temp\blue-g4\probe_pure.py`）**
- 命中正确：`"C" "Am7" "F#sus4" "G/B" "Cmaj7" "Cm(maj7)" "Am7b5" "C7sus4" "Bbbdim" "Emaj7" "Cdim7" "Am/G#" "Bm7b5/E"` 全部 detected=True（对 SheetSage2 自产的 15 种后缀完备，逐条对照 `checkpoints/notation_sheetsage2.py:108-126` 已核）。
- **漏判**：`"C9" "Cmaj9" "Cadd9" "G13" "Fm9" "C7b9" "Csus" "C5" "A7alt" "Caug7"` detected=False（`_ABC_QUALITIES` 里没有 9/11/13/add/alt/b9/sus 裸写）。
- 后果链：`_abc_has_chords`=False → `_resolve_cot(mode, abc)` 返回 `("melody", "乐谱里没有和弦记号，已改用 melody 路线…")`（实测矩阵）；`_abc_analyze` 同时给出 `has_chords=false, chord_count=0, cot_suggested="melody"` → `static/index.html:1825` 在乐谱框下面显示「**纯旋律谱（无和弦记号）** → 按谱走 melody」，把带 `"Cadd9"` 的谱对用户断言为无和弦。
- 文档措辞是无条件的：README.md:48「带和弦走 full」、README.md:193「**带和弦记号的谱走 `full`**」、README_EN.md:89「a chord-annotated score goes through `full`」。README.md:51/195 只在"提取"语境里诚实标注了 SheetSage2 记号表限制，而这条限制恰好说明闭集窄带来的另一头（贴进来的谱）没有被诚实标注。

**触发条件**：README 主打的另一条入口——用户从外部（abcnotation.com、MuseScore 导出、手写谱）贴 ABC。9/11/13/add9/sus/5 是流行与吉他谱最常见的记号。
**用户可见后果**：谱里的和弦记号照旧保留（`melody` 不会删），路线却按无和弦的 melody 走 → 就是 FAQ 里花整段篇幅解释的"听起来没照我的谱唱"。同时 UI 明说"纯旋律谱（无和弦记号）"，用户被误导去怀疑自己的谱。
**修复方向**：两级判定——先宽匹配 `"..."`（任何形如根音+音程/扩展记号的引号 token）判"含和弦"，再用闭集决定"能否交给 full"；对宽匹配命中但闭集不认的记号，走 full + 明确提示"记号 X 不在引擎词汇表内，可能被忽略"，或按 README 已有的诚实标注惯例给 toast。至少把 UI 文案从"无和弦记号"改成"未发现引擎可识别的和弦记号"。

### D8 · 中英 README 事实不等价（本次改动只同步了一半） 🟠（17）

**对照表（只列事实性差异；同一功能的不同措辞不列）**

| 事实 | README.md（中） | README_EN.md（英） | 判定 |
|---|---|---|---|
| 歌词引擎 | :53/:153/:187 强制对齐，中文 FunASR 字级 + 英文 wav2vec2 CTC + VAD 锚点，明确"非 ASR" | :35 "**whisper/SenseVoice alignment**"，:102 credits 只列 faster-whisper/FunASR SenseVoice，:74 架构表 `src/lrc.py` "SenseVoice+VAD primary, whisper segment-anchor fallback" | **互相矛盾**（英文描述的是被中文判定为"错得离谱"的旧方案） |
| 逐字/eLRC | :53/:18/:24/:153 逐字卡拉OK高亮 + `.elrc` | 完全无该条目 | 中文过度宣称（见 D3），英文反而更保守 |
| 端口唯一真源 | :59 功能行、:114-132 专节、:130「配置问题绝不会导致服务起不来」、:148 全源同读 | **无**（功能表、架构、Quick Start 全缺） | 英文缺 1 个功能 + 1 个配置节 |
| 架构目录表 | :152-162 含 `src/ports.py`、`ports.json`、`src/lrc_align.py`、`checkpoints/`、`pic/` | :71-79 只有 8 行，无 ports、无 lrc_align、**无 checkpoints** | 英文表不完整且把读者引到旧文件 |
| 内存闸门 | :60「试听/转换前置内存闸门，与训练互不挤死」 | :42 VRAM adaptive 行无此句 | 英文缺限制/能力说明 |
| 音色训练子能力 | :54 自动分离人声、断点续跑、训练前模型体检、多音色融合、训练过程试听 | :36 仅 "Upload dry vocals, train your own RVC voice models" | 英文少 5 项（且中文这 5 项里含 D2 的硬件前提未标注） |
| 断点续跑归属 | :54（训练续跑）+ :57（任务托管） | :37 只在 Batch queue 行写 resume，训练无 | 归属不同 |
| 换声产物 | :52 未写 | :34 "exports include converted vocal / original vocal / instrumental / full song" | 英文独有（实测 app.py:2249-2252,2293-2296 与 output 下 `*_vocals_original/*_accompaniment/*_full_song.wav` 支持） |
| 通知/favicon | 无该功能行 | :39 Windows toast + favicon busy/idle | 英文独有（代码支持 app.py:1296/1311 `_win_toast`、index.html favicon 逻辑） |
| 任务管理维度 | 无该行（分散在 :56/:57） | :38 状态×类型双筛选、重命名/停止/重试/删除、进行中置顶 | 英文独有 |
| 生成耗时 | :175 full+32步+长歌词 6GB 约 60-90 分钟、CPU 慢 5-10 倍 | 无 | 中文独有的未验证数字（见 D19） |
| 商用口径 | :217「不能。本项目扩展代码**随上游许可分发**」 | :87 "songs you create are yours to use per YuE2's license"、:106 "**This workbench: CC BY-NC 4.0**" | **法律口径不一致**（见 D10） |
| 镜像与 q8 | :99-112 hf-mirror 说明 + `--q8` 手动路径 | :57 一句带过，无 `--q8` | 英文少操作信息 |
| 中英互链 | :— | :18 [中文说明](README.md) | 中文无回链（小） |
| 本次新增 3 段（ABC/ID/队列） | :48-56,190-206 | :30-38,89-97 | **已同步**，内容一致 ✓ |
| 「提取时带和弦」控件名 | 中文原生 | :33/:91 直接留中文 "提取时带和弦" 未译 | 英文读者无法定位 UI 控件 |

**用户可见后果**：英文读者（GitHub 海外流量、Reddit 引流落点）读到的是一个仍然宣称 ASR 方案、没有端口配置章节、法律口径不同的产品；中文读者读到的是一个把逐字高亮当已交付能力的产品。两边都不等于代码现状。项目自己的硬规矩"改行为必须同步更新中英文 README"在本次 diff 里只对 ABC/ID/队列三项被遵守。
**修复方向**：以中文 README 为功能真源重排英文表（逐行对齐 + 差异清单入 CI 检查），先把 README_EN.md:35/74 改成强制对齐与 `src/lrc_align.py`。

### D9 · AI 创作助手依赖仓外私有目录且静默降级 🟠（11 / 17）

**证据链**
1. `src/ai_prompt.py:12` `SKILLS_DIR = Path(r"E:/AI/10AIMusic/Yue/YuE2-skills")`；:15-18 列出 4 个文件（`yue2-song-craft/SKILL.md`、`references/style-guide.md`、`references/lyrics-guide.md`、`yue2-music/SKILL.md`）。
2. `git ls-files | grep -i skill` → **无**：这些手册不在仓库里。
3. `src/ai_prompt.py:36-41` 读取循环 `except Exception: continue` —— 缺文件不报错、不告警、不进日志；只有 `__main__` 自检分支（:53-55）会打印 `files loaded: n/4`。
4. 本机实测 `load_system_prompt()` 得到含完整手册的长提示词（因为 E: 上那台目录存在）；把这 4 个文件移走即退化为 `_PREAMBLE` 5 条 + 空手册分隔符。
5. README.md:49 承诺"内置 DeepSeek 驱动的创作助手：说人话，它帮你出**六要素风格标签和结构化歌词**"——"六要素/结构化歌词"的规则正在缺失的那几份手册里。
6. 同文件 :23 把 `http://127.0.0.1:7863` 写死进提示词（与 README.md:59/148 的"端口唯一真源"冲突），:25 把"本机约束：6GB 显存 + 16GB 内存"写死（作者机配置，README.md:86 只说"16GB+/建议 6GB+"）。

**触发条件**：任何非作者机用户（含 fresh clone）使用 AI 写词写风格。
**用户可见后果**：助手"看起来能用"，但按 5 行前言工作，产出与文档描述的"六要素/双语/自检"规则不一致；16GB 显存的高端卡用户会被助手反复建议"用 melody/off 或 steps=16"。
**修复方向**：把手册内容随仓库分发（或做进 `docs/`/`src/skills/`）；`load_system_prompt()` 在 0/4 命中时明确抛错或在 UI 提示"创作助手处于精简模式"；端口与硬件约束从 `settings`/实测读取。

### D10 · 许可章节三处不一致且缺根 LICENSE 文件 🟠（16 / 17）

**证据链**：README.md:11 徽章 `License: CC-BY-NC-4.0`；README.md:229「本仓库**扩展代码随各上游组件许可分发**」（未给自己的代码指定许可）；README.md:217 答"能商用吗"=「**不能**」；README_EN.md:106「**This workbench: CC BY-NC 4.0**」（自行为代码指定许可）；README_EN.md:87「songs you create are yours to use per YuE2's license」（对用户产出的商用留了口子）。`ls LICENSE*` 在仓库根为空，`git ls-files | grep LICENSE` 只有 `checkpoints/LICENSE`（MERT2 权重许可）与两个 render_assets 许可。
**后果**：GitHub 右侧 license 字段为空/未知，徽章与两份 README 三种说法并存；二次分发者无法判断代码许可，中文说"不能商用"、英文说"你的歌你可以用"在合规语境下是两种答案。
**修复方向**：确定一份代码许可 → 落根 `LICENSE` 文件 → 两份 README 的许可节与"能商用吗"用同一措辞，并明确"模型权重（CC-BY-NC）对**产出音频**的传染性"到底怎么判（这是用户最关心的一条，两边现在给的暗示不同）。

### D11 · 「端口只改一个文件」在 ai_router 里不成立，并有误杀风险 🟠（11 / 17）

**证据链**：README.md:59/148/130 承诺端口全源同读、配置问题绝不导致服务起不来。`src/ai_router.py` 内 4 处写死 3081：:63 从启动日志抓 token 的 URL 正则、:70 探活 `http://127.0.0.1:3081/`、:82 netstat 匹配 `endswith(":3081")`、:107 `--port 3081` 拉起参数；`src/ai_prompt.py:23` 写死 7863；`scripts/停止音乐工作台.bat:32` 回显写死 8080。对照组（说明"应当怎么做"已在别处实现）：`src/ports.py:44-56`、`dsh-plugin/src/ui-panel.mjs:70-84`、`watchdog.py:31-42`、`app.py:106-113` 全部走 ports/env。
**后果**：用户按 README 改 `ports.json`（例如 dsh=3181）后，AI 工作台的探活与"假死击杀"仍盯着 3081 —— 若 3081 上跑着**别的**程序，`ai_router.py:76-90` 会把它 taskkill 掉；同时新工作台永远起不来（`--port` 写死），与"绝不会导致服务起不来"的承诺正相反。
**修复方向**：ai_router 全部改 `from ports import get`；启动日志正则用 f-string 注入端口；bat 回显用已解析变量；`grep -rn "7863\|3081\|8080"` 加进发布前检查。

### D12 · 测试有效性：不存在能失败的检查 🟠（10）

**证据链**
1. `find`（排除 py312/runtime/node_modules）无 `test_*.py`/`*_test.py`/`conftest.py`/`pytest.ini`/`*.test.js`/`*.spec.js`；命中的 `*.test.js` 全在 `dsh-plugin/node_modules/**`（第三方自带）。
2. `ls .github` → 不存在；`git ls-files` 中无 workflow、无 CI 配置、无测试脚本。
3. 仓库自己把"验证"定义成语法检查：`docs/review/RESPONSE-e32bbb5.md:44-46` —— "`py_compile` 通过；前端 `node --check` 通过。…… **运行时验证待网关重启后进行**（不重启不影响在跑任务；所有新端点当前未生效）"。`py_compile`/`node --check` 对本次改动**永真**（不改变行为的语法检查），因此不能作为任何行为证据。
4. 本次三个行为改动的实测回归情况：
   - `_new_id()/_id_busy()`：`runtime/output` 194 个产物 0 个 8-hex ID → 该函数在真实运行里 **从未产出过文件**；唯一可复核的证据是我在 `Temp` 里 AST 抽出执行（生成 3 个同秒 ID 不重复）。没有留下用例：`_id_busy` 对 `HIST_DIR/RVC_JOB_DIR/RVC_TRAIN_DIR` 的查重、`_new_id` 撞 64 次抛 503、`_ID_SEEN` 与磁盘不一致时的行为，全部无用例。
   - `_resolve_cot` 双向纠正：矩阵我在离线脚本里跑了（off+谱→want、选反→纠正、无谱→原样），但仓库内无用例。**未来必然复发的具体场景**：任何一次对 `_ABC_QUALITIES` 或 `_VOICE_RE` 的增删（例如 D7 补 9/add11）都会静默改变路由，而没有用例能发现；`_abc_has_chords` 的 body 过滤规则（`^[A-Za-z]:`）对 `V:` 行、`%` 注释行、`%%` 指令行的边界同样无守护。
   - 转谱不回读旧谱：`src/sheetsage_pt.py:161-167` 的判据（`result["abc"]` 空即抛）无用例。可复发场景已经能构造：`checkpoints/exports_sheetsage2.py:192` 在失败时会**删除** `score.abc`，而 `pipeline_sheetsage2.py:241-242/254-258` 在某些配置下会 raise 而不是返回带 `abc_error` 的 dict —— 两条路径的 `reason` 文本来源不同，本次把 reason 截 200 字符塞进 RuntimeError（:166），无任何用例锁定"错误里写明原因"这条 README.md:195 的诚实承诺。
5. 反向证据（说明不是不能测）：`_resolve_cot`/`_abc_chord_tokens`/`_abc_analyze` 都是纯函数，`_id_busy` 只需注入目录，本次评审 15 分钟就能抽出执行——留在仓库里即成为回归网。

**用户可见后果**：README/docs 里凡"已实测/已验证"的措辞都没有可复现支撑（例如 README.md:187 "实测错得离谱"、app.py:990 "实测起唱点误差中位 2.07s→0.66s"、docs/歌词时间轴-强制对齐方案.md 的 8/8 行命中）；本报告 D5/D6/D7 三个真实缺陷正是"没有可失败检查"的直接产物。
**修复方向**：最低成本落一组 `tests/test_cot.py`（`_resolve_cot` 全矩阵 + 和弦记号白/黑名单）+ `tests/test_queue.py`（用 fixture 状态文件跑 `batch_status` 的 queue 分组，含"全无 qid 的旧文件"用例）+ `scripts/dev_check.bat` 跑 `py_compile` 与 `node --check` 并保留"这些不是行为测试"的措辞。

---

## 三、维度覆盖声明

- **17 文档一致性**：已审，逐条拆命题。可验证且有实现的：唯一 ID（`app.py:472-484`，产物名 `app.py:1009/1500/2468/3000` ✓，但 8-hex 从未落盘见 D12）、队列身份（`app.py:1730/1744/1830-1835` ✓，迁移破口见 D6）、强制对齐三路径（`src/lrc_align.py:113-141` fa-zh、:144-169+:238-296 wav2vec2 CTC、:127-215+:374-402 VAD 吸附/锚点 ✓）、看门狗 401 探活（`watchdog.py:53-60` `HTTPError→code<500 视为存活`；且实测在跑的 watchdog PID 29132 启动于 12:15:51、`watchdog.py` 改于 03:17 → **修复已生效** ✓）、断点续跑（`app.py:3087 /rvc/train/resume`、:1858 /batch/resume ✓）、模型体检（`app.py:1971 /rvc/models/{name}/check`，单脚本一次 load + `_RVC_CHECK_LOCK` ✓）、多音色融合（`app.py:2020 /rvc/models/merge`，绝对路径落盘 + `cwd=RVC_DIR` ✓）、训练试听（`app.py:2555 + :2577-2585 extract_small_model` ✓）、内存闸门（`app.py:2200-2220 GlobalMemoryStatusEx` + :2246 前 ✓）、CPU 回退与自动切回（`app.py:1277-1286,1317-1322` ✓，但 96 个 meta 中 0 次实际发生）、LRC 干净无元数据头（`src/lrc_align.py:516-556` 只写 `[mm:ss]` 与段落标记，全仓 grep 无 `[ar:/[ti:/[al:/[by:/[Offset` ✓ 成立）。不成立/无实现的：**D1 三步开唱、D2 无独显+自动分离、D3 逐字卡拉OK高亮、D7 带和弦走 full（闭集外）**。中英等价性见 D8 对照表；许可见 D10。
- **16 发布工程**：已审。未提交改动不构成可发布单元（D5，含实测的前后端版本劈裂）；无 CHANGELOG/无版本文件/仅 1 个 tag（D16）；PROMOTION 与 README 重复声明不一致（D4、D8）；历史评审报告抽查：`ADJUDICATION-e32bbb5` 的 3 个 P0 与 9 个 P2 **确实已落地**（`app.py:1996/2048/2100/2591/2635` 的 `cwd=str(RVC_DIR)`；`app.py:2577-2585` 的 `extract_small_model`；`app.py:1992/2613` 的 `acquire(blocking=False)`；`app.py:2799` 的精确 glob；`app.py:2094` 的 `opt['f0']=int(...)`），`ADJUDICATION-558f7c5` 的 A1（`app.py:3497` 移入 `__main__`）、A3-1（watchdog 单实例）、A3-3（`app.py:695-704` 落盘回退 + :678 绝对 tmp）、A3-5（`_stream_upload_to`）均已落地——**没有发现"报告漂亮代码没动"的 P0/P1**；唯一被反复搁置的是 A3-2 死代码（D14）与报告行号漂移（D15）。
- **10 测试有效性**：已审，结论 D12：仓库无任何可运行回归测试与 CI，自认的验证形式是永真的语法检查；三个行为改动无留档用例并已给出各自必然复发的场景。
- **14 数据迁移与状态演进**：已审（对真实状态文件做只读仿真）。旧格式 `batch_state.json` 在新代码下不崩但语义退化（D6 Case 1/2）；`runtime/output/*.json` 旧 meta 缺新字段时前端全部走 `|| ""`/`|| 0` 兜底（`static/index.html:1546,1825,1882`），未发现渲染崩溃；`scores_list` 对无 `analysis` 的旧谱返回 `cot:null`（`app.py:720-723`）前端 `s.cot ? ...` 已兜 ✓；回滚安全（D6 Case 3），但旧代码整字典替换路径会静默丢 `qid`；`.elrc` 孤儿残留见 D13；`_new_id` 与旧 4-hex ID 命名不互撞（`glob(rid+".*")` 实测不会命中旧名）✓。
- **11 兼容性**：已审。对内核的假设：`cot must be one of: full, melody, off` 与 `abc requires cot=melody or cot=full` 从 `main.cp312-win_amd64.pyd` 字符串表**实测命中**（app.py:537 引用的文案是 `"external ABC requires cot=melody or cot=full"`，与内核实际文案不完全一致，属文档级不准）；`X-Seed` 头 95/96 meta 实测有效 ✓；`_is_vram_error` 是猜（D20）；内核无 chord 词表（`sus4/maj7/add9` 计数全 0），故 D7 的"YuE2 原生引号和弦词汇"注释依据实际来自 SheetSage2 表（`checkpoints/notation_sheetsage2.py:108-126`），对照后对 SheetSage2 完备、对 YuE2 引擎无据。对上游：`src/sheetsage_pt.py` 是我们自己的 shim（不在 checkpoints/），`checkpoints/` 自 558f7c5 后未改 ✓ 无冲突面，但无版本 pin（D18）。`ports.json`/conda py312：`py312/` 不入库（D1）；中文路径与反斜杠：`os.path.basename(x.replace("\\","/"))` 在 `app.py:691/733/742/1893/1917` 一致使用 ✓，`tmp_dir` 绝对化 ✓，README 截图中文文件名与 bat 的 chcp 65001 ✓。
- **2 架构与设计**：已审。边界清晰的一面：所有新增路由都在可编辑层，没有逻辑只能靠改 `.pyd` 才能生效的证据（引擎侧的窗口隐藏缺陷用 `app.py:66-98` EnumWindows 外部兜底、路由冲突用 `app.py:3488-3493` 外部重排，都发生在可编辑层）✓。不清晰的一面：README 架构图/目录表把 `.pyd` 整层抹掉（D21）；协议字段单一真源情况较好——`cot_suggested/has_chords/chord_count` 只由后端 `_abc_analyze` 产出、前端只读不算 ✓，`queue/queued_hist` 只在后端算 ✓，但端口常量在 `ai_router.py` 破口（D11）、`melody_only` 是跨端字符串协议（`app.py:673` `Form("true")` + `index.html:1819` 写 `"false"/"true"`，无常量共享）、`cot:"rvc"`（`app.py:2361`）把路线字段当类型判别用（真判别是 `kind`，前端按 kind 过滤 ✓，属命名污染非缺陷）。`src/` 与根目录职责：`audiocpp.py`、`chunking.py` 死代码滞留（D14），`voices` 是指向 `runtime/voices` 的 junction（已 untrack ✓）。3502 行 app.py / 3032 行 index.html 的耦合靠"网关现读前端 + 前端只调 /api/*"维持，本次新增的 `idTag/queueBar` 都写在 index.html 内联 JS 里，无组件化——可维护性风险但不构成缺陷。

---

## 四、优先级路线图

**P0（发版前必须处理）**
1. D1 —— README 的 clone 路径要么给出运行件（Release 或"运行件获取"章节），要么改文案；这是当前对外最大的可用性谎言。
2. D2 —— 分离链的 `--device cuda` 与 `E:\...\GPT-SoVITS` 依赖去掉/可配置，README 标注硬件前置。
3. D5 —— 把未提交 diff 作为一次原子发布 + 明确"需重启网关，重启会中断在跑的一首"，并修 D6/D7 后一起生效。
4. D6 —— `qid` 回填：真实状态文件在用户的活跃队列上，一旦按现状重启，下一次追加就会给出错误统计。
5. D4 —— PROMOTION 措辞纠正（对外广播成本不可回收）。

**P1（紧随其后）**
6. D3 —— 前端消费 `elrc` 做真高亮，或整体降级措辞（含徽章/标语/FAQ）。
7. D7 —— 和弦两级判定 + UI 措辞修正。
8. D8 —— 中英 README 逐行对齐（先修 `README_EN.md:35/74`）。
9. D12 —— 落一组能失败的纯函数用例（`_resolve_cot` 矩阵、`_abc_has_chords` 黑白名单、`batch_status` 队列分组含无 qid fixture）。
10. D9 —— skill 手册入库或显式降级告警。
11. D10 / D11 —— 根 LICENSE + 三处许可口径统一；ai_router 端口去硬编码。

**P2（工程卫生）**
12. D13 `.elrc` 进删除清单；D14 死代码清理；D15 报告落点改函数/路由名；D16 引入 CHANGELOG 与版本常量；D17 bat 提示逐项指路；D18 记录 checkpoints 上游 commit；D19 README 措辞去过头项；D20 与 8080 的错误契约落到码而非文本；D21 README 架构图补 `.pyd` 层并说明不可改；D22 转谱串行化或按 job 分目录。

---

## 五、做得对、值得保持（防整改误伤）

1. **`_new_id()/_id_busy()` 的查重面选得对**：覆盖 output/scores/records/rvc-jobs/rvc-trains 五处，并显式 glob 处理同 id 多后缀；时间戳前缀保留使排序与旧记录兼容（实测旧 4-hex 与新 8-hex 不互撞）。
2. **`src/sheetsage_pt.py` 删掉"回读 score.abc"**：方向正确且比要求的更彻底——`checkpoints/exports_sheetsage2.py:192` 在失败时本就会 unlink 旧文件，说明"静默给旧谱"是真实存在的跨歌污染路径；错误消息带上 `abc_error` 前 200 字符，符合 README 的"报错里写明原因"。
3. **和弦闭集对 SheetSage2 自产谱是完备的**（逐条对照 15 个 `_QUALITY_TO_ABC` 后缀 + 斜杠低音 + 双变音，全部实测命中），且 `_CHORD_RE` 只在正文小节匹配、`_abc_analyze` 先摘引号再统计音域、跳过 `V:` 声部头——后者修掉了 "Vocal" 里的 `c/a` 被当音的真实 bug（实测音域由污染变正确）。
4. **`_resolve_cot` 的 `mode == want` 早退**顺带修掉了"档位本来就对时也被塞一句纠正说明"的旧行为，返回值语义（route, note）干净。
5. **看门狗重构是真的**：`probe()` 的 `HTTPError→code<500 即存活` 修掉了 401 误判导致 3081 守护分支从未触发的历史 bug，并且 3081 现在与 7863 同等"击杀后重拉"；实测在跑的 watchdog 已是新码。
6. **`src/ports.py` 的兜底设计**：JSON 坏 → 默认值、env 覆盖、类型/范围校验、未知键返回 -1，绝不因配置阻断启动；`ui-panel.mjs`、`watchdog.py`、`settings.py`、CORS 都真同源。
7. **前端兼容性兜底到位**：`q && q.id`、`j.queue_id ?`、`a.chord_count || 0`、`s.cot ?` —— 新 HTML 打在旧后端上不会白屏（这正是 D5 能安全存在一段时间的唯一原因）。
8. **历史评审闭环质量高**：e32bbb5 的 3 P0 + 9 P2、558f7c5 的 A1/A3-1/A3-3/A3-5 全部在代码里能指到落地行；`cwd=RVC_DIR` 的 11 处一致施加、`extract_small_model` 前置、`acquire(blocking=False)` 消 TOCTOU，都不是嘴上修。这套流程要保留。
9. **仓库纪律**：`runtime/`、`py312/`、`cpp/`、`tmp/`、`secrets/local_env.bat`、`*.wav/*.pth/*.safetensors` 全部不入库，`git ls-files` 111 个文件里无一个用户产物或密钥（`secrets/` 只有 `local_env.example.bat`）；`.gitignore` 无"写了规则却已被跟踪"的虚设条目（逐条 `git ls-files` 交叉核对通过）。

---

## 六、未验证项

1. **未做任何写入型/计算型实测**：`_new_id` 在真实 `/api/generate/start`、`/api/batch/start`、`/api/score`、`/api/rvc/*` 路径上的端到端行为、`queueBar` 的实际渲染、勾选「提取时带和弦」的真实成功/失败样本，均未验证（禁止 POST，且用户有批量任务在跑）。
2. **未重启任何进程**：因此"重启后新逻辑生效"的全部结论来自静态与仿真，非运行时观察。
3. **browser-use MCP 在本会话无可用工具**（`mcp__browser-use__list_tabs` 报无此工具），页面 console error / 逐字高亮缺失的浏览器侧观察只做到 HTML 源码级（`curl /lab/index.html` 计数 + 全文 grep）。
4. **性能/耗时数字全部无数据支撑但也没反证**：README.md:175「full+32步 6GB 约 60-90 分钟」、:169「CPU 慢 5-10 倍」、README.md:51/195「勾选几乎不多花时间」——本机 96 个 meta 里 0 次 cpu_fallback、且当前后端为 CPU 未有完成样本，无法验证；"两种模式都识别和弦、几乎不多花时间"仅由 `checkpoints/exports_sheetsage2.py:78` 的上游注释与 :172 的 `melody_only` 只作用于导出阶段这一静态事实支持。
5. **强制对齐精度**（README.md:187「百毫秒量级」、app.py:990「中位 2.07s→0.66s」、docs/歌词时间轴-强制对齐方案.md「8/8 行命中」）未复核：需要跑对齐任务与人工标注比对，本次禁止触发计算。
6. **GitHub 远端树是否与本地完全一致**只由 `git status` 的 "up to date with 'origin/master'" 支持，未做 `git fetch`（避免写操作）；若远端另有含运行件的分支/Release 页外的下载渠道，D1 的定级需要相应下调。
7. **`checkpoints/model.safetensors` 缺失时 SheetSage2 `from_pretrained` 的实际失败形态**（报错 vs 随机初始化）未验证，需要真加载模型。
8. **回滚演练**（旧代码 + 已含 qid 的新状态文件）只做了逻辑推演，未实际用旧 app.py 起进程验证。
