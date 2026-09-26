# RESPONSE（整改与逐条回应）— 基线 `308f833`

- 仓库：`E:\AI\10AIMusic\Yue\yuE-2\yuE-2`，评审基线 commit `308f833` + 本轮整改后的工作区
- 回应对象：`BLUE-TEAM-REVIEW-308f833-G3.md`（B1–B25 前端与状态）、`-G4.md`（D1–D22 文档/配置/交付）、`-G2b.md`（S1–S12 安全、Y1–Y9 隐私与许可）
- 日期：2026-09-26
- 立场：不采信注释与自查，每条要么落到代码行、要么写明"推迟/不修"的理由。允许驳回蓝军，但驳回必须带复核证据。

## 〇、结论先行

**68 条 findings：48 已修、7 部分采纳、4 经复核判定"本身不是缺陷"、8 推迟（附理由与复入口条件）、1 需用户决策（版本号策略）。**

三条必须先看的实话：

1. **本轮整改有两条覆盖缺口，蓝军自己没审到，我也没补上。** G1/G2（后端正确性 + 第一轮安全）两个子代理在 150 轮上限处静默死亡，没有产出报告；实际入库的只有 G3/G4/G2b 三份。所以 **`_resolve_cot` 之外的生成主链路、批量队列状态机、LRC 对齐算法的正确性从未被对抗性审查过**。这不是"问题少"，是"没看过"。详见第四节。
2. **后端改动至今未在运行时生效。** 网关 uvicorn `reload=False`，进程里跑的还是 `308f833` 的 app.py。本轮所有 `app.py` 改动（守卫、上限、清理、argv、缓存、S11）要**等一次网关重启**才成立；`static/index.html` 与 `dsh-plugin/lib/client.js` 是即时生效的，所以现在处于"新前端 + 旧后端"的组合态（正是 D5 描述的那个状态）。重启需要你授权，且当前有 CPU 生成在跑。
3. **`.bat` 的两处拉起方式改动无法实测。** 我不能重启在跑的服务，只做了等价 A/B 探针（见 S5），启动脚本本身的端到端验证欠奉。

验证手段：`py_compile`（app/settings/ai_router/ai_prompt/sheetsage_pt）全通过；`node --check` 通过 `static/index.html` 内联脚本 99,774 字符与 `dsh-plugin/lib/client.js`；`tests/test_review_fixes.py` **33 个用例 0.55 秒全绿**（只碰临时目录与只读端点，不提交任何计算）。

## 一、🔴 逐条回应（12 条）

### S1 · 音色名 → `python -c` 源码注入 = 本机 RCE
**采纳·已修。** 所有喂给训练环境子进程的脚本一律 `sys.argv` 取参，源码字符串里不再出现任何用户可控值：体检 `app.py:2192-2200`、导出 `app.py:2284-2290`、融合 `app.py:2257-2262`（注释里记了这条是裁定项）。名字净化仍在 `app.py:3225`（只用于目录名），但它已经不是注入面了。
回归：`tests/test_review_fixes.py` 无直接用例（要真跑 RVC 环境才能触发），由"参数不进源码"这一结构保证；`-c` 源码里 `%`/`+`/f-string 插值已 grep 清零。

### S2 · `yue2workbench://` 协议处理把 URL 未加引号拼进 cmd
**采纳·已修。** `scripts/register_protocol.bat` 的注册值改为只调启动脚本、**不再转发 URL 参数**，并删除那行从未生效的 `set "CMD=\"%%1\""`；rem 里写明了 `yue2workbench://x"&calc.exe` 这类注入为何成立。
代价：外部页面不能再通过协议 URL 带参深链。功能上原来也没消费这个参数。

### S3 · 零鉴权 + 零 CSRF：简单请求可被任意网页打出
**采纳·已修（带残余声明）。** 新增 `@app.middleware("http") _guard_local_only`（`app.py:134-215`）：Host 非回环 → 403（挡 DNS 重绑定）；POST/PUT/PATCH/DELETE 带非本机 `Origin`/`Referer` → 403（挡"发请求不要回包"那类）。守卫在路由之前执行，用 405 与 403 的差值可证明它确实先于 handler。
**残余（诚实说明）：** 这条规则管的是**变更类**请求。本机任意进程仍能发 GET 读全部歌词/历史（浏览器扩展、本机其他网页的 `<img>` 之类），因为 GET 无 `Origin` 时无法与 curl 区分；根治只有鉴权，而单机工作台加鉴权会把"刷新不丢任务、看门狗自动重启"全打乱。当前把风险面收在：只监听回环（Y1 加了断言）+ Y5 把最贵的 GET 端点缓存化。
回归：`test_dns_rebinding_host_is_rejected`、`test_cross_site_post_is_rejected_before_handler`、`test_local_calls_still_work`、`test_loopback_host_parsing`、`test_origin_parsing`、`test_lan_mode_still_rejects_cross_site`。

### S4 · 训练 worker 无条件 `release()` 导致 GPU 闸永久失效
**采纳·已修。** `app.py:3038` 起 `gpu_held = False`，只有 `acquire()` 成功才置 True（`app.py:3047`），`finally` 里 `if gpu_held:` 才释放（`app.py:3207`）。acquire 之前的任何异常不再污染信号量。
回归：无（并发时序，需真训练；结构上是单变量守卫）。

### B1 · `StatusCard` 在 rail 判定处 `return null` 跳过 2 个 Hook → React #300
**采纳·已修。** `dsh-plugin/lib/client.js`：所有 hook 调用移到 rail 判定之前，条件返回只留在最后一个 hook 之后。这是硬约束（hook 顺序），页面上"slot entry crashed in 'sidebar.footer.action'"的报错源头即此。
回归：`node --check` 通过；需刷新 :3081 目视确认侧栏状态卡渲染（未做，见第七节）。

### B2/B3 · 轮询表声明成 `Set` 却调 `.get/.set`
**采纳·已修。** `static/index.html:2516` `rvcPolls = new Map()`、`:2812` `rvcTrainPolls = new Map()`，配套 `stopRvcPoll`（`:2519`）/`stopRvcTrainPoll`（`:2816`）在完成/失败/丢失三条出口都清定时器。原缺陷后果：提交成功也弹"提交失败"、完成 toast 永不触发、定时器每 5s/10s 翻倍泄漏（也就是 B23）。
回归：无自动化用例（纯前端），代码结构可 grep 验证。

### B4 · `currentConfig()/applyConfig()` 未定义 → 表单持久化整块失效
**采纳·已修。** `static/index.html:3083` `_FORM_IDS` 白名单 + `:3086 currentConfig()` + `:3092 applyConfig()`，并补上 `abcChords` 勾选框（同时解决 B7）。
回归：无（前端）；白名单是显式数组，遗漏字段可通过读代码判定。

### D1 · README「三步开唱」在本仓库不成立
**采纳·已修。** README 中英文都加了"⚠️ 诚实说明：`git clone` 只拿到工作台代码"的五行表：`py312/`（且根目录没有 requirements.txt）、`main.cp312-win_amd64.pyd`、`cpp/audiocpp_server.exe`、`checkpoints/model.safetensors`、`runtime/models/` 全部不入库，也没有 Release 包。
残余：真正的"一键可得"需要发 Release 或做 pip 安装器，属产品决策（第七节）。

### D2 · 分离链路绑作者机 + 硬写 `cuda`
**采纳·已修。** `app.py:2374` `GSV_ROOT = Path(os.environ.get("YUE2_GSV_ROOT") or ...)`；调用侧 `--device backend_mode()`（`app.py:2463`）不再硬写；旧链不可用时给的是"缺什么、怎么指路"而不是 FileNotFoundError（`app.py:2486-2492`）。
回归：`test_private_default_path_is_env_overridable`、`test_chain_probe_is_callable_and_boolean`。

### D3 · 头条功能"逐字卡拉OK歌词"前端 0 消费者
**采纳·已修。** 新增 `⬇ 逐字 eLRC` 行按钮，与 `.lrc` 共用 `downloadTrack(btn, fmt)`（`static/index.html:2273`）；无逐字版本时明确报错而不是给个空文件。同时把 README 的宣称收敛到事实：**逐字高亮由支持 eLRC 的外部播放器渲染，工作台自己的页面只放音频 + 提供两种下载**。
残余：页面内不做卡拉OK渲染。这是产品决策，不是 bug。

### D4 · `docs/PROMOTION.md` 对外广播已废弃的 ASR 方案
**采纳·已修。** 对外文案与内部事实对齐：强制对齐（FunASR 字级时间戳给中文、wav2vec2 CTC 给英文 + VAD onsets 吸附）为主，SenseVoice ASR 只用于换声取词，文件指向 `src/lrc_align.py`。

## 二、🟠 逐条回应（24 条）

| 编号 | 裁定 | 落点与说明 |
|---|---|---|
| S5 | 采纳·已修 | 网关与 dsh 的拉起从 WMI 改为 `Start-Process -WindowStyle Hidden`（`scripts/启动音乐工作台.bat:59-65`、`scripts/启动dsh工作台.bat:24-34`）。**A/B 实证**：同一段探针子进程脚本，WMI 派生读不到父进程变量、Start-Process 读得到（`child PROBE_VAR=<missing>` vs `hello-from-parent`）。副作用连带修掉了 S6 的隐患——原来"靠继承传密钥"的注释是假的，WMI 下密钥根本没到 node。**未实测端到端**（不能重启在跑的服务）。 |
| S6 | 采纳·已修 | `DEEPSEEK_API_KEY` 不再展开进命令行；靠环境继承（S5 之后这条才真正成立）。 |
| S7 | 采纳·已修 | `_rvc_job_purge`（`app.py:1133`）按 ID 精确删工作目录，只认一层且校验父目录，绝不 glob；`_OUTPUT_EXTS` 补 `.elrc`（`app.py:1112`）；删除端点改为两个 purge 都跑（`app.py:1771`）。回归：`test_rvc_job_purge_removes_uploaded_source`、`test_rvc_job_purge_cannot_escape_jobs_dir`、`test_output_purge_covers_every_extension`、`test_generate_delete_clears_output_and_rvc_workspace`。 |
| S8 | 采纳·已修 | 上传一律 `_stream_upload_to`（`app.py:945`）流式落盘 + 200MB 闸门：换声源曲 `:815`、素材入库 `:979`、训练样本 `:2698`。不再整读进内存。 |
| S9 | 采纳·已修 | 击杀前先 `tasklist /FI "PID eq <pid>"` 校验是 node.exe，否则打印跳过（`src/ai_router.py:85-115`）。一个 GET 再也杀不掉用户的无关程序。 |
| S10 | 采纳·已修 | `src/ai_router.py:29` `DSH_PORT = _port("dsh")`，探活 URL、netstat 匹配、拉起参数三处同源。 |
| S11 | 采纳·已修 | `models_switch`（`app.py:1041-1054`）：先剥 `model/` 前缀，再要求 `Path(path).name == path`，带目录一律 400；写进 `server.json` 的就是这个校验过的值。顺带修掉了旧代码把路径拼成 `model/model/x.gguf` 的问题。回归：`test_traversal_is_rejected_before_any_write`、`test_written_path_is_the_validated_filename`（后者同时锁住"回写值=校验值"）。 |
| S12 | 部分采纳·推迟根治 | `trust_remote_code=True` 保留：关掉就没有转谱。已做：README 明示权重/远程代码来自上游、`checkpoints/` 保留上游 LICENSE 与 THIRD_PARTY_NOTICES。**未做**：revision 固定 + 逐文件哈希锁。理由：需要联网核对上游仓库并可能锁死自动更新，属发布决策，不该在一次整改里悄悄改。列为公开分发前必须闭环项。 |
| B5 | 采纳·已修 | `static/index.html:2084`：存在打开的 `⋯` 菜单时跳过整块 DOM 重建。实测过"打开菜单 6.5 秒自动关闭"的路径已断。 |
| B6 | 采纳·已修 | `pollJob` 网络/JSON 失败改为 `netMiss` 计数退避，连续多次才降级提示并停止"谎报进行中"。 |
| B7 | 采纳·已修 | `abcChords` 进 `_FORM_IDS`（`static/index.html:3083`），刷新后勾选状态与提交值一致。 |
| B8 | 采纳·已修（运行时欠） | 队列按"一次提交"分组本身正确，问题在旧状态文件无 `qid` 塌成空桶；`_batch_read`（`app.py:1800`）做迁移。回归：`test_legacy_items_get_a_queue_id`（58 条历史条目）、`test_missing_state_file_reads_as_empty`。**面板要等网关重启才显示**（后端未生效）。 |
| B9 | 采纳·已修 | `fallbackCopy`（`:991`）检查 `document.execCommand("copy")` 返回值——它失败只返回 false 不抛错，不看返回值就是谎报"已复制"。 |
| B10 | 采纳·已修 | 转谱轮询 `static/index.html:1870-1886`：网络异常与 `!ok` 都进 `miss` 计数，连错 5 次才认输；服务端任务仍在跑这件事不再被前端判死。 |
| B11 | 采纳·已修 | README/README_EN 的实现口径同步，并显式写出"重启前后哪条承诺才成立"。 |
| B12 | 采纳·已修 | `static/index.html:2060` `extra` 内部也按 `seen` 去重，同 id 双来源不再重复出行。 |
| D5 | 部分采纳 | "本次改动不是可原子发布的单元"是**流程事实**，不该假装已原子。处置：整改后仍待一次网关重启，且本节第〇条 2 已把组合态（新前端+旧后端）写在最前面。真正的原子性要靠发版流程（tag + Release），归到 D16 的用户决策里。 |
| D6 | 采纳·已修 | 见 B8。 |
| D7 | 采纳·已修 | `_CHORD_RE_LOOSE`（`app.py:626`）+ `_abc_chord_tokens`（`:630`）覆盖 `C9/Fm9/G13/Cmaj9/Csus/C5/A7alt` 这类扩展记号；乐谱框旁的断言跟着改。回归：`test_extended_chord_symbols_count_as_chords`、`test_melody_score_forces_melody_route`、`test_chord_score_forces_full_route`、`test_off_with_score_is_corrected`、`test_empty_score_leaves_user_choice`。 |
| D8 | 采纳·已修 | README_EN 与中文版逐段对齐（强制对齐、Quick Start 不入库清单、目录表、5 条新 FAQ）。 |
| D9 | 采纳·已修 | `src/ai_prompt.py`：手册目录改 `YUE2_SKILLS_DIR` 环境变量优先；一份都没加载到时**换一套不含"已加载手册"声称的提示词**并打印告警，部分缺失时在末尾标注缺哪本。自查输出 `files loaded: 4/4`。 |
| D10 | 采纳·已修 | 新增根 `LICENSE`：分层声明（仓库代码 CC BY-NC 4.0 / 上游权重逐表列出 / 第三方资产出处）。与 README 原有宣称和 `Copyright (c) 2026 RevolutionLA` 对齐。`scripts/gtcrn.py` 无出处这件事在 LICENSE 与 README 里都写成"公开分发前必须核对"，不粉饰。 |
| D11 | 采纳·已修 | 见 S10；另 `src/ai_prompt.py` 的网关端口也改为从 `ports.json` 取（`YUE2_GATEWAY_PORT` 可覆盖），不再把 7863 写死在提示词里。 |
| D12 | 部分采纳 | 已建 `tests/test_review_fixes.py`：**33 个用例、0.55 秒、可在生成进行中运行**（沙箱化落盘目录、stub 掉 `main`/`voices`/`asr`/`denoise`/`lrc`，不发任何计算类请求）。**CI 推迟**：本机无 runner，`.github/workflows` 写了也验证不了，且这些用例依赖仓库内 `py312/`（不入库），CI 里跑不起来——要么先解决 D1 的依赖分发，要么 CI 只是装饰。 |

## 三、🟡 一行一条（32 条）

| 编号 | 级别 | 裁定 | 证据/落点 |
|---|---|---|---|
| B13 | 🟡 | 确认无缺陷 | 蓝军自述"document 级 click 仅 1 处"，复核一致，无改动 |
| B14 | 🟡 | 确认无缺陷 | 捕获阶段拦截实测成立；`preventDefault` 对 `span` 无默认行为，留着无害 |
| B15 | 🟡 | 部分采纳 | 高危两处（`pollJob`、转谱轮询）已按"计数退避 + 降级提示"改造；全文剩 24 处 `catch {}` 集中在主题色/取色器/localStorage/通知父窗口一类尽力而为路径，保留 |
| B16 | 🟡 | 推迟 | 全文 62 个 fetch 调用点、仅 5 处走 `fetchT`。全量加超时是机械大改，风险收益不划算；真正会"谎报状态"的两处（B6/B10）已修 |
| B17 | 🟡 | 采纳·已修 | `idTag`（`static/index.html:963-966`）加 `tabindex="0" role="button" aria-label`，并补 `keydown`（Enter/空格）捕获监听（`:977`） |
| B18 | 🟡 | 推迟 | `.tid` 命中区约 5×14px；整行本身可点可对冲，放大徽标会挤爆历史行布局。留作视觉决策 |
| B19 | 🟡 | 确认无缺陷 | 复核所有 innerHTML 插值点：可控数据一律过 `escapeHtml`，其余是服务端自造数字/枚举 |
| B20 | 🟡 | 部分采纳 | 展示侧不再猜：`route = a.cot_suggested \|\| ""`，服务端没给就不显示路线断言（`static/index.html:1810`）。仅在 `analysis` 完全缺失时保留一次兜底默认并立即补发分析（`:1891-1895`）。最终裁定权在后端 `_resolve_cot`（`app.py:662`） |
| B21 | 🟡 | 采纳·已修 | `dsh-plugin/lib/client.js:315-362`：8 秒 favicon 轮询从**同步 XHR** 改为异步链式探测，并补 `return clearInterval + titleMo.disconnect()`；网关假死不再阻塞 dsh 界面线程，组件卸载不再泄漏 |
| B22 | 🟡 | 推迟 | 历史页 3 fetch + 99 行全量重建确实存在；改为增量/虚拟列表是性能重构，不与本轮整改混做（B5 的"重建冲掉交互态"已单独止血） |
| B23 | 🟡 | 采纳·已修 | 轮询遇 404 三次判定"状态丢失"（`static/index.html:2625`、`:2987`），文案明写"服务重启过，成品见音色列表"，不再无限静默轮询 |
| B24 | 🟡 | 采纳·已修 | `static/index.html:2393` 改为解析 JSON 后再取 `message`，不再把 Response 对象当 JSON 用 |
| B25 | 🟡 | 推迟 | 与 B5 同源：状态行每 2s 重建会冲掉正选中的 ID 文本。历史行徽标同样可复制，收益不足以支撑"状态行局部更新"的重构 |
| D13 | 🟡 | 采纳·已修 | `.elrc` 进 `_OUTPUT_EXTS`（`app.py:1112`），删除不再留孤儿。回归 `test_output_purge_covers_every_extension` |
| D14 | 🟡 | 部分采纳 | **`audiocpp.py` 的判断是错的**：它被内核依赖（live import），不是死代码，删了会崩。`src/chunking.py` 全仓 0 引用属实，但内核的 import 面不可读、无法证明它不反射导入，故**保留并在此声明**，不做"看起来更干净"的删除 |
| D15 | 🟡 | 推迟 | 已归档评审报告的 `文件:行号` 随整改漂移，改写历史报告等于伪造证据。处置：本轮及后续报告一律给**当前**行号，旧行号只在当次快照内有效 |
| D16 | 🟡 | 需用户决策 | 无 CHANGELOG、仅 `v1.0` tag，而 PROMOTION 仍以 v1.0 宣传含未发布行为的工作区。要么发 `v1.1` tag + Release，要么改文案不报版本号——需要用户定策略，不自作 |
| D17 | 🟡 | 采纳·已修 | 根启动 bat 缺 `py312` 时的提示改成本机可执行的指引（`py312\` 须与脚本同级 / 整包拷全），不再报作者机绝对路径 |
| D18 | 🟡 | 部分采纳 | `checkpoints/` 是 558f7c5 一次性 vendored 的上游代码，已在 `LICENSE` 里逐个标注出处与许可，并保留上游 LICENSE/THIRD_PARTY_NOTICES。**未做**：锁定 revision + 哈希校验清单（与 S12 同一决策） |
| D19 | 🟡 | 采纳·已修 | README 中英文都写清"重启不丢"仅对队列/存档成立，在跑那一首会被标 `error: 服务重启，任务中断`；"CPU 慢 5-10 倍"标注为社区经验值、无本机实测 |
| D20 | 🟡 | 推迟 | 对 8080 引擎的 CPU 回退判定仍靠自由文本正则（`app.py:1455` `_is_vram_error`）。引擎二进制不可改，拿不到结构化错误码；文本匹配是唯一手段，只能等上游。已在 README 的"显存自适应"处如实描述 |
| D21 | 🟡 | 采纳·已文档化 | 外层与不可改内核的边界（`app = main.app` + `app.routes` 重排）确实是 monkeypatch；已在 README 加"🧱 内核边界"说明：哪些行为改不了、为什么改路由顺序、改了要重启 |
| D22 | 🟡 | 采纳·已修 | `_SCORE_ENGINE_LOCK`（`app.py:565`）包住 `transcribe_abc`（`app.py:782`），并发转谱不再互踩共享目录 `sheetsage2-output/` 的中间文件 |
| Y1 | 🟡 | 采纳·已修 | `_resolve_lan_mode`（`app.py:156-171`）在 import 期断言：非回环绑定且无 `YUE2_ALLOW_LAN=1` 直接 `RuntimeError` 拒绝启动；开启 LAN 后守卫自动从"只认回环"放宽为"只认同源"（`_origin_allowed`），并打印醒目告警。`settings.py:12` 注释同步。回归 `test_loopback_bind_is_not_lan_mode`、`test_open_bind_refuses_startup_without_opt_out`、`test_lan_mode_still_rejects_cross_site` |
| Y2 | 🟡 | 推迟 | 子进程 stderr 原文回传前端并写进 output meta，会泄露绝对路径与账号名。**有意保留**：本机排障就靠这几行原文，而接口只在回环可达（S3/Y1）。若要收敛，正确做法是脱敏后另存调试日志，不是简单截断 |
| Y3 | 🟡 | 确认无缺陷 | 三处插值（`j.step`/`j.preview_url`/`m.size_mb`）均为服务端自造字段；抽查全量 innerHTML 点，用户可控数据都过 `escapeHtml`。风险在于"以后改数据源"，已记入评审记忆 |
| Y4 | 🟡 | 采纳·已修 | 守卫统一补头（`app.py:200-215`）：`X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer`、`Content-Security-Policy: frame-ancestors 'self' http://127.0.0.1:{7863,3081} http://localhost:{...}`。不用 `X-Frame-Options` 是因为 dsh :3081 合法内嵌 :7863，会被一刀切挡死。回归 `test_security_headers_on_every_response` |
| Y5 | 🟡 | 采纳·已修 | `rvc_model_check`（`app.py:2178`）按 `(mtime_ns, size)` 指纹缓存成功结果，`?force=1` 绕过；失败不缓存（一次性 OOM 不该永久钉死结论）。回归 `test_repeated_check_reuses_cache_until_file_changes`（含重训覆盖自动失效）、`test_broken_model_is_not_cached` |
| Y6 | 🟡 | 采纳·已修 | `.gitignore` 补 `.atomcode/`、`.workbuddy/`（`git check-ignore -v` 验证生效）。另核过 `secrets/local_env.bat`（含真实密钥）已在 `.gitignore:12`，仓库只跟踪 `local_env.example.bat` |
| Y7 | 🟡 | 采纳·已修 | `generate_start` 不再二次 `_make_job` 造出永不运行的第二个 ID；`_new_id()` 抛错时后台线程不再"客户端失败而任务在跑"；失败时把 `error` 写进 job 状态（`app.py:1611-1646`），响应固定 `job: None` |
| Y8 | 🟡 | 部分采纳 | 已建 `LICENSE`（见 D10）。`scripts/gtcrn.py` 的出处核对、`scripts/model_trained_on_dns3.tar` 是否随包发布，**仍未闭环**；已核实该 tar 从未进过 git 历史（`git log --all --` 无记录），故 LICENSE 里写成"本仓库不分发"而不是含糊带过 |
| Y9 | 🟡 | 采纳·已修 | `if True:  # 旧两步链（降级路径）`（基线 `app.py:2187`）已改为正常的 `else` + 可用性前置判断（`app.py:2479-2492`），降级分支不再长得像主路径 |

## 四、覆盖缺口（本流程自己的缺陷，必须记账）

1. **两个维度从未被审。** adversarial-review 派发 5 个蓝军维度，G1（后端正确性）与 G2（第一轮安全）在 150 轮硬上限处静默死亡，只留下"我在侦察仓库结构"的痕迹。入库报告是 G3/G4/G2b 三份。因此以下三块**零对抗覆盖**：
   - 生成主链路（`/api/generate/start` 之后的 payload 组装、meta 落盘、断点续跑与暂停/续跑状态机的边界条件）；
   - 批量队列状态机（除 B8/D6 已审到的 `qid` 分组外的调度、重试、僵尸条目复位）；
   - LRC 强制对齐算法本身（时间轴可信度、锚点吸附的错音风险）。
   **处置**：写进 ADJUDICATION，作为下一轮评审的必做范围；本轮不假装覆盖。
2. **G4 存在编造。** 复核 G4 表格时，D12/D13/D14/D17/D18/D23/D24/D26/D27 若干条按原始措辞对不上代码。逐条回查结果：D13（`.elrc` 孤儿）与 D17（bat 里作者机路径）**确有其事**并已修；D14 的 `audiocpp.py` 死代码说**是假的**（内核 live import）；D23 之后的编号在文件里根本不存在（表格止于 D22）。**处置**：一条编造记录 = 整份报告的措辞需按代码复核，本 RESPONSE 就是这个复核产物；后续蓝军 prompt 已加"禁止泛化未验证结论"与窄范围约束（G2b 用 90 次工具调用完成，是可复制的形态）。

## 五、推迟清单与复入口条件

| 项 | 推迟理由 | 什么时候必须做 |
|---|---|---|
| S12/D18 | `trust_remote_code` 哈希锁与 revision 固定，牵动模型自动更新策略 | 公开分发 / 接受外部贡献前 |
| D12 | CI 依赖 `py312/`（不入库），当前写 workflow 只是装饰 | D1 的依赖分发方案落地后 |
| D16 | 版本号与 CHANGELOG 策略需用户决策 | 本次 push 之前先问一次；若用户同意，随手打 tag |
| D20 | 引擎二进制不可改，无结构化错误码可用 | 上游 audiocpp 提供错误码时 |
| B16/B18/B22/B25 | 前端机械重构/视觉/性能，与本轮安全-交付整改不同源，混做会掩盖真改动 | 下一次前端专项 |
| Y2 | 本机排障需要 stderr 原文；接口回环可达 | 若真要开放 LAN（`YUE2_ALLOW_LAN=1`）则**必须先修** |

## 六、运行时状态与生效条件（不许含糊）

| 改动 | 现在生效？ | 生效条件 |
|---|---|---|
| `static/index.html`（B2/B3/B4/B5/B9/B12/B17/B20/B23/B24、eLRC 按钮、`abcChords` 持久化） | **已生效** | 浏览器刷新即可（服务从磁盘直出） |
| `dsh-plugin/lib/client.js`（B1/B21） | 未确认 | dsh 是否从 `_dsh_home` 副本加载需实测；:3081 重启或插件重载 |
| `app.py` 全部（守卫、上限、清理、argv、S11、Y1、Y5、D22、Y7、S4、S7-S8） | **未生效** | **网关重启**（`reload=False`）；当前有 CPU 生成在跑，需用户择时 |
| `scripts/*.bat`（S2/S5/S6/D17） | 未生效 | 下次从脚本启动时（本轮不能运行 .bat） |
| `README.md` / `README_EN.md` / `LICENSE` / `docs/*` | 已生效 | 文本即时 |

**明确警告**：网关重启前的组合态是"新前端 + 旧后端"。旧后端仍会静默截断超长输入、仍无 Host/Origin 守卫、删除仍留 `.elrc` 与 RVC 工作目录孤儿、`models_switch` 仍可被塞相对路径。前端已不再显示旧的后端字段时会静默少显示（例如队列面板 `queue` 字段缺失 → 该条留空）。因此**不要**把本轮整改当作"已经安全"来宣传，直到重启完成——这一句同时适用于本 RESPONSE 和 D5。

## 七、回归用例总账

`tests/test_review_fixes.py`，33 例，`py312\python.exe -m unittest discover -s tests -v` 全绿（0.55s）。分组与锁定的编号：

- 任务 ID：`TestTaskId`（需求③）
- 删除与清理：`TestPurge`、`TestDeleteEndpoints`（D13/S7/B8 端到端）
- 输入上限：`TestInputLimits`
- 和弦与档位配对：`TestChordRouting`（D7 + 需求②）
- 队列迁移：`TestQueueMigration`（D6/B8）
- 本机守卫：`TestLocalGuard`（S3/Y1/Y4）
- 开放绑定断言：`TestLanBind`（Y1）
- 模型切换路径：`TestModelSwitchPath`（S11）
- 体检缓存：`TestRvcCheckCache`（Y5）
- 分离链配置：`TestGsvChainConfig`（D2）

没有自动化覆盖的已修项（S1/S4/B1-B5/B9/B10/B17/B21/B23/B24/D1-D4/D8-D11/D17/D19/Y9）：要么需要真训练/真 GPU/真浏览器时序，要么是纯文本与结构变更。这份清单本身就是"哪些修复只靠读代码担保"的透明账目，第三轮审计应优先攻这里。

---

# 第三轮追加：第三方审计 N-1…N-13 逐条处置（同一基线 `308f833`，整改后的工作区再审）

评审对象 = `THIRD-PARTY-REVIEW-308f833.md`。先说最难听的一句：**N-1 与 N-3 是我这一轮整改自己引入的**——B21 的"已修"是换了一种坏法（favicon 角标从"能亮但阻塞"变成"永不亮"），Y1 的"已修"把 DNS 重绑定请回了门内。两处我在写 RESPONSE 时都标进了"结构可 grep 验证/已修"的放心条目，属于夸大，接受第三方对该措辞的批评。

## 八-1、本轮已修（含证据与用例）

| 编号 | 处置 | 证据（当前工作区） | 用例 |
|---|---|---|---|
| N-1 XHR 没有 `.ok`，忙碌角标永不点亮 | **已修** | `dsh-plugin/lib/client.js:339-341` 改判 `x.status >= 200 && x.status < 300`，注释里写明了基线 `x1.ok` 的同款错误（`git show HEAD:` 可查，基线也是坏的） | 无（需真浏览器；`node --check` 通过） |
| N-2 worker 用过期快照整体回写 → 追加条目被吞；状态文件非原子写 → 崩溃即清零 | **已修** | `app.py:1919 _batch_write` 改 tmp+`os.replace`；worker 只拿快照挑下一条（`:1954`），登记/终态全部锁内重读只改自己那一格（`:1974-1982`、`:2011-2018`、`:2023-2032`）；`_batch_store` 保留给 HTTP 侧并写明其窗口（`:1932`） | `TestQueueMigration.test_write_is_atomic_and_leaves_no_temp`、`test_store_only_replaces_listed_ids` |
| N-3 LAN 放宽让 `Origin==Host` 天然成立，且端口不参与比较 | **已修（判定换根）** | `app.py:156 _lan_host_allowlist`（名单只能来自 `YUE2_LAN_HOSTS` 或具体绑定地址）、`:171 _resolve_lan_mode`（通配绑定且无名单 ⇒ 拒绝启动）、`:216 _origin_in_allowlist`（主机名命中白名单**且**端口等于网关端口）、`:199/249-253` CSP 同步用 `_FRAME_HOSTS` | `TestLocalGuard.test_lan_mode_allows_only_whitelisted_host_and_port`（含重绑定形态：Host/Origin 同为 evil.example 仍 403）、`TestLanBind` 4 例 |
| N-4 单首在跑时提交批量：条目没排队就顶掉全局 current job | **已修** | `app.py:1968` 先 `with _GPU_SEM` 排队，拿到闸门后才 `_gen_set_job`+写 meta（`:2005-2006`） | 结构锁定：无（测它要真起计算） |
| N-5 取消后条目卡 running，下轮状态接口改写成"服务重启，任务中断" | **已修** | `app.py:2008-2019` 取消路径写终态 `cancelled`+`已由用户取消` 并清 `current`；`batch_stop` 侧 `:2192` 同一状态字 | 结构锁定：无 |
| N-6 `_batch_ensure_worker` 无锁判活可起两个 worker | **已修** | `app.py:1929 _BATCH_WORKER_LOCK` + `:2035-2043` 判活与赋值同临界区 | 无（并发时序） |
| N-7 训练上传只有逐文件 200MB，无累计量与磁盘闸门 | **已修** | `app.py:1005-1006`（`_DISK_FLOOR_BYTES`/`_MAX_UPLOAD_TOTAL` 各 2GB）、`:1009 _free_bytes`、`:1028-1038` 落盘前双闸门、`:1053-1054` 成功才累加、`rvc_train:3386-3394` 传 `budget` | `TestUploadBudget` 3 例（累计、超限 413、低盘 507 且不留残文件） |
| N-8 宽松式把引号内英文单词当和弦 | **已修** | `app.py:658-666` 根音后改成捕获组 + `:668 _is_chord_token` 词形白名单（字母集 + `alt/add/dim/aug/sus/maj/min/no`）；`:676-687` 顺带滤掉 `%` 注释行 | `TestChordProseGuard` 3 例（`"Chorus"/"Dog"/"D.C."` 不算和弦；13 个真实记号仍算） |
| N-9 `%cd%` 裸插进 PowerShell 单引号串，路径含 `'` 即语法崩且被 `catch{exit 1}` 吞掉 | **已修** | `scripts/启动音乐工作台.bat:28` 统一 `set "YUE2_ROOT_DIR=%cd%"`，网关与看门狗两条 `Start-Process` 全部用 `$env:`/`Join-Path`；`启动dsh工作台.bat:32-38` 同款（node 路径、工作目录、两个日志、入口脚本一律走 env） | 无（`.bat` 禁跑）；改动是纯传参方式，不改行为 |
| N-10 `_kill_stale_listener` 只看镜像名就杀 3081 上的 node | **已修** | `src/ai_router.py:114 _is_dsh_listener`：tasklist 确证 node.exe **且** `Get-CimInstance` 取到的 CommandLine 含 `dsh` 才杀；取不到命令行按"不是自己的"处理 | 无（会真杀进程） |
| N-11 存储入口仍静默截断（历史 meta、模板） | **已修** | `app.py:1062 history_add` 先 `_limit_text` 再落盘（判错不留孤儿 wav），字段上限与生成入口同源；`:3714 save_template` 用 `_CAPS`（style/lyrics/abc 与 `_MAX_*` 对齐，其余字段 4000） | `TestStorageCaps` 4 例（长谱原样存、超限 400、历史超限不落盘、5000 字歌词不截断） |
| 附带自纠：`_read_token` 用 `re.search` 取**第一条** token | **已修（自查，非审计条目）** | `src/ai_router.py:70-76` 改 `findall()[-1]`：拉起脚本每次截断重写日志、本模块以 `"ab"` 追加，两种写法并存时旧 token 会留在文件头部，返回第一条等于把用户导向死链 | 无 |
| 第三节-3：崩溃自愈无上限，条目自身是崩溃诱因时每轮轮询复活一次（看门狗再把网关拉回来 = 无限陪葬） | **已修（原拟推迟，权衡后当场做）** | `app.py:1930-1933`（`_REVIVE_WINDOW_SEC=600`/`_REVIVE_MAX=3`）+ `:2139-2164` 自愈改为"短时间连续复活"计数：窗口内超过 3 次即判 `error` 并写明"该任务本身很可能就是崩溃诱因"，不再拉起 worker。判据用时间窗而不是累计次数，是为了不误杀"用户为改配置正常重启两次"这种常见情形 | `TestQueueMigration.test_crash_loop_revive_is_bounded`（判死且不再 ensure_worker）、`test_normal_restart_still_resumes`（超窗计数清零、照常续跑） |

## 八-2、承认但不修的（理由与复入口）

| 编号 | 判定 | 理由 |
|---|---|---|
| N-12（第三方第三节-2）`_CANCEL_EVENT` 是全局单例，`batch_status` 自愈与 resume/retry 都无条件 `clear()`，可能与另一子系统的 `set()` 相撞 | **记账，暂不重构** | 语义上"停止"就是全局一键（UI 也只有一个停止按钮），拆成 per-subsystem token 需要同时改取消传播链（`_gen_run` 在 `with _GPU_SEM` 内、worker 在两处判、训练另有 pause 通道）。当前竞态要求"取消已置位的同时触发另一子系统复位"，窗口在毫秒级且后果是连发剩余条目复活——不是静默丢数据。复入口：出现"取消后仍有任务复活"的真实反馈，或做 LAN 多人使用前必须拆 |
| 状态接口读路径的自愈写盘（`GET /batch/status` 里写文件） | **保留** | 这是断点续跑的入口，挪走等于放弃自愈；风险已由原子写（N-2）压到"最多丢一次轮询的显示"。它现在也是崩溃复活计数（`revive_n`/`revive_ts`）的唯一落笔点 |
| N-9 第二半（裁定 (a)⑤ 追认）：`_dsh_web.log` 有两个写者、语义不一致——拉起脚本用 `Start-Process -RedirectStandardOutput` 每次**截断**重写，`ai_router` 以 `"ab"` **追加** | **修读侧，不改写侧（记账）** | 两条链路的写法语义本来就不该统一：脚本那次重定向是 dsh 进程自己的 stdout 归口，追加的是网关写给用户的跳转提示，合并成一种写法只会在"脚本刚截断、提示随即消失"时更难查。真正会伤人的是读侧——旧 token 排在新 token 前面，`re.search` 取第一条就把用户导向上一次会话的死链。`src/ai_router.py:70-77` 已改为 `findall()[-1]`（注释写明两种写法语义并存这一事实本身），截断与追加两种情况下"最后一条"都恰好是当前有效 token。复入口：若将来有第二个读 token 的位置出现，必须复用同一个取末条函数，不要各写一份 |

## 八-3、第三方结论中我方反驳的一条

- 第三节把 `GET /abc/analyze` 记作"无长度闸门"：两个分析入口都有 `[:200_000]`（`app.py:812`、`:819`），且分析结果只用于前端摘要与档位提示，不是提交路径；真正的 20000 字符闸门在 `generate_start`/`batch_start`/存储入口三处。截断阈值是提交上限的 10 倍，任何能提交的谱都会被完整分析。裁定为"非缺陷"。

## 八-4、第三轮回归账目

`py312\python.exe -m unittest discover -s tests` → **Ran 49 tests in 1.3s … OK**（第一轮 33 例 → 本轮 +16）。全部用例只操作临时目录与只读端点，不提交任何计算；新增类：`TestStorageCaps`、`TestUploadBudget`、`TestChordProseGuard`，并在 `TestQueueMigration`/`TestLocalGuard`/`TestLanBind`/`TestGsvChainConfig` 内扩项（其中 `test_private_default_path_is_env_overridable` 原来是 grep 源码文本的弱用例——第三方的批评成立，已改成真调 `app._gsv_root()` 验证 env 生效）。测 worker 相关用例都不碰真计算：测自愈只 patch `_batch_ensure_worker` 记一次调用，测 N-2 只锁住它依赖的底层不变量（改一条不吞另一条、写盘原子），因为直接跑 `batch_start` 会真起一条生成任务打正在跑的 GPU/CPU。

诚实边界（本轮仍只有静态担保）：N-1（浏览器）、N-4/N-5/N-6（并发时序与真计算）、N-9（`.bat` 禁跑）、N-10（会真杀进程）四条没有自动化覆盖；worker 的 N-2 只锁住了它依赖的底层不变量（改一条不吞另一条、写盘原子），因为直接测 worker 必须走 `batch_start`，而那会真起一条生成任务打正在跑的 GPU/CPU。网关进程仍是旧代码，以上全部要到下一次重启才生效。
