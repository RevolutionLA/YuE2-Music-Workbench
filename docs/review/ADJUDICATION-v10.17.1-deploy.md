# 中立裁定 · v10.17.1「纯 clone 也能跑 + 核显 Vulkan + 补齐换声/分离/逐字对齐」

- **裁定对象**：暂存区 `git diff --cached`（30 文件，+2714/−209；基线 `9d0bb9b`）。
- **裁定立场**：不采信蓝军与第三方的行号、结论与复现输出，全部自己重定位、重跑。行号均以本报告落笔时的文件为准。
- **取证边界**：未启动/停止网关（7863）与引擎（8080），未提交任何生成/训练/分离任务，未改 `runtime/`、`cpp/`、`py312/`，未 `commit/push/checkout/reset`，未改任何代码。所有实验在临时目录、内存替身或本地回环假服务里做。
- **我自己的基线**：本机全量 `614 tests, OK`（80.8 秒）；`git checkout-index` 导出的干净树：只跑 `test_shim_main.py` 为 `1 error`，补 `spec+state` 后全量 `614 tests, FAILED (failures=24, errors=5, skipped=2)`（与第三方数字一致）。
- **一句话结论**：蓝军把大部分真问题找对了，但把 B9 的数字说成四套、把 B1 的启动脚本证据说得不准确；第三方补出了 T2/T4 两条真缺陷并纠正了 B7 的爆炸半径，但 T3 是**证据不实**（环境变量设晚了，hf_transfer 根本没被启用）、T6 是**触发链不成立**（那行写 cuda 的代码不可达），T1 的"每首歌都翻红"是**未实测的频率断言**。发布仍被 B1/B2/B4/B6+T4 四个 P0 卡住。

---

## 一、第三方 T 项逐条裁定

| 编号 | 裁定 | 定级是否调整 | 理由（≤3 行） |
| --- | --- | --- | --- |
| T1 | 部分采纳 | 🟠 → 🟡 | 机制成立：`main.py:229` 用 2 秒探 `/health`，超时就写 `down`；`main.py:371-373` 再判死并 `Popen` 第二个引擎。但"出歌期间 `/health` 必然 >2 秒"两方都没实测；本机 idle `/health` 2.7ms，6 个 CPU 进程压满时也只有 1.5–5.5ms，作者 19.7 秒那个数字是 `/v1/models` 的，不能外推到 `/health`。在真实生成期间测到分布之前，不能按 🟠 排 P0。 |
| T2 | 采纳 | 维持 🟠 | 我按第三方脚本重跑：引擎没装时 `engine_backends()` 得 `['cpu']` 并永久缓存（`app.py:504`），装好后仍返回 `['cpu']`；`_restart_audiocpp` 于是传 `--backend cpu`（`app.py:604-607`），页面仍报 `mode=cuda`。缓存没有 TTL/失效点，这是本轮新引入的回归。 |
| T3 | **驳回** | — | 代码顺序是 `from huggingface_hub import snapshot_download`（`setup_runtime.py:496`）在前、`os.environ["HF_HUB_ENABLE_HF_TRANSFER"]="1"`（`:501`）在后；huggingface_hub 0.36.0 的 `constants.HF_HUB_ENABLE_HF_TRANSFER` 在 import 期求值。我实测该 flag 仍为 `False`，下载走普通路径，不会抛 `ValueError`。第三方的复现是手工把 `constants.HF_HUB_ENABLE_HF_TRANSFER` 改成 `True`，不是真实代码路径。 |
| T4 | 采纳 | 维持 🟠 | 复现：`cpp/` 里只有 exe、没有 `model_specs/yue2.json` 时，`ensure_engine` 走 `:260-265` 早退并返回 `'cpu'`，spec 仍不存在；随后 `main.py:88-92` 在 import 期 raise，网关起不来。与 B6 是不同分支，后果更重。 |
| T5 | 部分采纳 | 🟠 → 🟡（约束项，非当前缺陷） | 看门狗 `probe()` 确实把 5xx 当死（`watchdog.py:100-110`），`LONG_JOB_GLOBS`（`:61-64`）也确实没有出歌产物；但蓝军 B1 的主修法是 **200 + `gateway:"degraded"`**，503 只出现在 B7 的"generate 返回 503"建议里，T5 把 503 记到 B1 头上是误归因。T5 自己建议"把 `output/*.json` 加进免死名单"也不覆盖生成中段——meta 只在回退/结束/失败时写（`app.py:2598/2625/2636/2649/2666`）。 |
| T6 | **驳回** | — | 触发链不成立：CPU 回退路径只调用 `_restart_audiocpp(..., backend="cpu")`，从不写 `engine_state`，所以 `backend_mode()` 仍是 `cuda`，`:2658` 的 `and backend_mode()=="cpu"` 为假，`:2660` 的 `_engine_state_write({"backend":"cuda"})` 不可达。真正的问题是反过来的：自动回切 GPU 永远不会发生（基线 `9d0bb9b` 就这样），且与本轮 diff 无关。 |
| T7 | 部分采纳 | 维持 🟡（可达性低） | 畸形值确实致命：`PYTHONIOENCODING=cp:` 与 `cp850.` 都让 Python 在 `init_stdio_encoding` 阶段 Fatal（实测 rc=1，一行 Python 都跑不起来）。但本机 `chcp` 解析得 `936`，Windows `chcp` 输出格式稳定，触发需要本地化标点/解析落空；建议只加一个"`OEMCP` 必须是纯数字"的校验，排 P2。 |
| T8 | 部分采纳 | 🟡 → P2 清理 | `git check-attr -a scripts/开放局域网.ps1` 回空，`git ls-files --eol` 的 `attr/` 也是空——`.gitattributes:16` 的 `*.ps1` 确实什么也没做。但 git 属性本来无法强制 BOM，删掉空规则或改注释即可，不是功能缺陷。 |
| T9 | 采纳 | 维持 🟡 | `snapshot_download`（`setup_runtime.py:504`）没有 try/except，离线会 traceback 而不是 `[x]`；5 个 pymss 模型的失败只进日志（`:544-547`），之后仍无条件 `[OK]` 并 `return True`（`:548`）。与 B6 同族，可合并修。 |
| T10 | 部分采纳 | 维持 🟡 | `resolve_backend` 确实会把写错的后端改写（`app.py:604-607` 只留一行 `logger.warning`），与 README「写错时引擎会当场启动失败，不会静默降级」（README.md:418-420）矛盾；但 `main.py` 自启仍是硬失败，"正好说反"过强，准确说法是"部分重启路径会静默改写"。 |

**T 项合计**：采纳 2（T2/T4）、部分采纳 6（T1/T5/T7/T8/T9/T10）、驳回 2（T3/T6）。第三方把 2 条不成立的东西写成了 🟠/🟡，又把 1 条未测频率的 T1 写成 🟠，这是本报告要纠正的主要偏差。

---

## 二、蓝军 B 项最终状态

| 编号 | 最终状态 | 级别 | 一句理由 |
| --- | --- | --- | --- |
| B1 | 成立 | 🔴 | 我重跑：引擎全死时 `/api/health` 仍回 `200 {"gateway":"ok","audiocpp":{"status":"down"}}`；`static/index.html:4980` 的 OR 条件据此点绿灯。证据链与复现都成立。 |
| B2 | 成立（措辞修正） | 🔴 | 我用临时目录 + stub 复现 rc=1、两条 `系统找不到指定的路径`。修正：不是"永远"，而是"任何 `py312` 不存在、但 PATH 上有 python/py 的机器上"；完全没 Python 的机器上这句话反而是对的。 |
| B3 | 成立 | 🟠 | `runtime/rvc/train/train.py:60` 模块级 import tensorboard；`RVC_PKGS`（setup_runtime.py:54-55）没有 tensorboard/matplotlib；本机两者均 `False`。`--with-rvc` 后训练第一次 import 即死。 |
| B4 | 成立 | 🟠 | 我复现 14 字节源文件被拼成 56 字节、`verify_one -> (True,'ok')`。`fetch_chunk` 接受 200/206 但不比对 `Content-Range`、拼接前不校验每块长度；GGUF 只查 magic + 最小字节。 |
| B5 | 成立 | 🟠 | 我复现：POST 阻塞 3.0 秒时，同步的 `/api/health` 也拖了 2.91 秒才回。`async def`（main.py:331）+ 同步 `_engine_alive`/`start_audiocpp`（:371-373）确实占死事件循环；真实上限是 `settings.py:41` 的 90 秒。 |
| B6 | 成立 | 🟠 | 我复现：权重一份都没有时仍打 `[OK] 写歌出歌`；`--with-rvc/--with-lrc` 全失败仍 `exit 0`。`report()` 的"写歌出歌"是常量（setup_runtime.py:556），`main()` 无条件 `return 0`（:636）。 |
| B7 | 部分成立（爆炸半径上调） | 🟠 | 干净导出：`test_shim_main.py` 在 import 期就 error；补 spec+state 后全量 `24 failures + 5 errors`（不是蓝军写的 3 条）。但"import 期 raise"本身是作者写在 docstring 里的设计选择（宁可拒绝启动也不静默丢字段），真正该修的是测试可移植性与安装器校验，不是简单判它错。 |
| B8 | 成立 | 🟠 | 我重跑：4 个补丁源全部 `src_exists=False`；实际文件在 `patches/rvc-infer/cli.py`、`patches/rvc-clean-guards/preprocess.py` 这一层。源缺失时 `continue` 不出声（setup_runtime.py:523-524）。 |
| B9 | 部分成立 | 🟡 | 603（README.md:233/316、README_EN.md:176/263）与 573（requirements.txt:7）确实对不上 614；但 559 是 v10.17.1 标签的历史数字（README.md:379、CHANGELOG.md:47），不是"当前口径"，蓝军把它算成第四套是过度举证。 |
| B10 | 成立 | 🟡 | `.gitattributes:10` 是 `*.bat text eol=crlf`，README.md:340 写成 `-text`；我实测索引 `i/lf`、工作区 `w/crlf`。GBK 尾字节范围不含 0x0A/0x0D，所以 `text` 归一化只动行尾、不会破坏 GBK 字符——文档错，功能无损失。 |
| B11 | 成立 | 🟡 | `secrets/local_env.example.bat` 与 `dsh-plugin/启动AI工作台.bat` 都是 UTF-8 且不能按 GBK 解码；`tests/test_script_encoding.py:23-33` 的 9 个名单正好漏掉它们。两者中文只在 rem 行，实际影响是显示级，但规矩自相矛盾。 |
| B12 | 成立 | 🟡 | `CHANGELOG.md:12` 顶部仍是 `[v10.17.1] - 2026-10-17`，本轮注释日期是 10-21；`app.py:797-808` 会把 v10.17.1 报给 ⑬。这是发布元数据问题，不是运行期 bug，但打标签前必须处理。 |
| B13 | 成立 | 🟡 | `dict(r.headers)`（app.py:2450）把键降成小写，`headers.get("X-Seed")`（:2639）永远 `None`；我实测 `{'x-seed':'123'}.get('X-Seed') -> None`。今天不炸是因为 app.py 已 `_materialize_seed` 过，但契约是死的。 |
| B14 | 成立 | 🟡 | `transformers 4.45.2` 的 `utils/hub.py:102` 在 import 期求值；`configure_hf_home` 只改 `os.environ`（src/sheetsage_pt.py:25-39）。我实测 `import funasr` 会连带 import transformers，之后 `hub.TRANSFORMERS_CACHE` 仍是 `~/.cache/huggingface/hub`；测试只断言 env（tests/test_sheetsage_cache.py:30-40），bug 仍在也会绿。 |
| B15 | 成立 | 🟡 | `setup_runtime.py:534` 只在目标不存在时写 sitecustomize；本机这份第 88 行有手工追加的 `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD`，而 `patches/rvc-clean-guards/sitecustomize.py` 里没有——正好证明安装器复制不出来。 |
| B16 | 成立 | 🟡 | `ensure_engine` 见到 exe 就返回请求的后端（:260-265），不跑 `--version`；`ensure_server_json` 不覆盖 server.json 却无条件重写 engine_state（:324-339）。换 `--backend cpu` 重跑会造出"server.json 说 vulkan、state 说 cpu"，而 tests/test_shim_main.py:196-215 正是断言两者相等。 |
| B17 | 成立 | 🟡 | 缺 state 时 `main._engine_backend()` 按 cpu（main.py:144），`app.backend_mode()` 按 cuda（app.py:470-474）。删掉/损坏 state 后 shim 用 cpu 起引擎、页面按 cuda 显示并做显存预检。 |
| B18 | 改级（上调） | 🟡 → 🟠 | 两处门只认 `backend_mode()=="cuda"`（app.py:2580、:2618），Vulkan 是 README 首推的新默认路径，却丢了老路径唯一的自动 CPU 回退；核显走统一内存，长歌撞上就是几小时报废。**但 Vulkan OOM 的真实形态我没复现**，所以排 P1 而不是 P0。 |
| B19 | 成立 | 🟡 | GGUF 只查 `size >= min_size` 与 4 字节 magic（fetch_models.py:83-89）；主模型实际 2,877,774,656 字节，门槛 2,000,000,000，截到 2.1GB 也能过。`--workers 0` 会让 `ThreadPoolExecutor(max_workers=0)` 直接 `ValueError`（:177），没有参数校验。 |
| B20 | 成立 | 🟡 | `LICENSE:6` 的自研清单是"app.py、src/、static/、dsh-plugin/、scripts/、watchdog.py、settings.py"，本轮新增的根 `main.py`、`requirements*.txt`、`server.json.example` 都没进清单；不构成许可冲突，但分层声明与仓库实际对不上。 |
| B21 | 成立 | 🟡 | `git clone --depth 1` 跟随上游 main（setup_runtime.py:481），`:520-531` 只要字节不同就用整份文件覆盖，`.orig` 只在第一次备份；上游一改，仓库补丁会静默回退上游修复。 |

**B 项合计**：成立 18 条（B1/B2/B3/B4/B5/B6/B8/B10–B17/B19–B21）、部分成立 2 条（B7/B9）、改级 1 条（B18 上调 🟠）。

---

## 三、实质分歧，以及我认为谁对

1. **T1 的频率**：第三方说"页脚在整首歌期间报红是每首歌都会发生"，蓝军把它当 B1 的同族但没单列。**我认为第三方机制对、频率错**：`/health` 在 idle 是 2.7ms，6 个 CPU 进程压满时 1.5–5.5ms；`/v1/models` idle 2.9ms。作者 19.7 秒那段注释明确说的是"health 里串两次 HTTP（含 `/v1/models`）"，不是 `/health` 本身。真实生成时引擎是否把 HTTP 线程也堵住，只能实测；没有这个数据，T1 只能是 🟡 观察项。
2. **T3**：第三方说"新机器装到这一步必崩"，并给了 `ValueError` 的复现。**我认为这条不成立**：它的复现手工把 `constants.HF_HUB_ENABLE_HF_TRANSFER` 置 True，而真实代码在 import 之后才设 env，0.36.0 的 constants 是 import 期求值。我的对照实验得到 `flag=False`，随后是普通的连接错误，不是 hf_transfer 的 `ValueError`。`hf_transfer` 确实不在清单里，但那条 env 是死代码，删掉即可，不需要加包。
3. **T6**：第三方说"回退收尾无条件写 `engine_state.backend="cuda"`，在 Vulkan 机器上把状态写歪，新测试会因此变红"。**我认为这条触发链不成立**：回退路径从不把 state 写成 cpu，`:2658` 的条件永远为假。真正被漏掉的是反方向——**CPU 回退后永远不会自动切回 GPU**（基线就如此）。这不是本轮引入的，但值得单独立项；第三方把它写反了。
4. **B18 的级别**：蓝军 🟡、第三方要求 🟠。**我站第三方**：Vulkan 是这一轮 README 首推的默认路径，而"显存类错误自动切 CPU 重试"整段留在 `backend_mode()=="cuda"` 里；新默认路径丢了老路径唯一的自动兜底。只因 OOM 形态未实测，排 P1 不排 P0。
5. **B7 的爆炸半径**：蓝军只报了 `test_shim_main.py` 的 3 条红；第三方报全量 24F+5E。**我站第三方**：我按同一方法重跑，数字一致；蓝军把"本机 614 全绿"当成了代码属性，实际是这台机器的属性。
6. **B9 的数字**：蓝军说"四处不同（603/573/559/614）"。**我认为 559 是 v10.17.1 标签的历史数字**（README 版本表与 CHANGELOG 都写明"本标签 559 项"），不是当前口径；当前口径只有 603/573/614 三套。蓝军这条证据说得过满。
7. **B1 的启动脚本证据**：蓝军说"看到 200 就进 `:ready`"；第三方纠正"curl 无 `-f`，对 503 也回 0"。**第三方对**：`scripts/启动音乐工作台.bat:127-128` 只判 curl 退出码，不看状态码。结论不变——脚本确实会在引擎未就绪时说"全部就绪"，但理由不是"200"。

---

## 四、共同前提审查（两方共有、未经实测的假设）

| 编号 | 共同假设 | 为什么它是盲区 | 验证方法 / 我看到的反证 |
| --- | --- | --- | --- |
| C1 | "出歌期间引擎 `/health` 会超过 2 秒" | T1 的整个 🟠 和 B1 的触发条件之一都靠它；两方都只引 `main.py:204-206` 的注释，而那段注释说的是 `/v1/models`。 | 真实提交一首 60 秒以上的歌，同时每秒 `curl -w '%{time_total}' http://127.0.0.1:8080/health`，记录 p50/p95；或查引擎 HTTP 是否单线程。我的 idle 实测 2.7ms、6 进程压满 1.5–5.5ms，已足以否定"CPU 一忙就必然 >2s"。 |
| C2 | "614 tests OK 是代码属性，不是这台机器的属性" | 蓝军把它当基线，第三方用干净树纠正；两方在各自机器上都没先问"干净 clone 还绿吗"。 | `git checkout-index -a --prefix=/tmp/x/` 后跑全量：1 个 import error；补 spec+state 后 24F+5E。已做。 |
| C3 | "`main.py` 缺 spec 时 import 期 raise 是缺陷" | B7/T4 都把它当缺陷；但 `main.py:34-36` 的 docstring 明说这是"读不到就拒绝启动"的设计选择。两方都没先判定产品语义。 | 先决定：UI 是否应在缺 spec 时仍可打开？若是，改成 lazy load + `generate` 503；若不是，保留 raise，只修安装器校验与测试 skip。这是产品决策，不是纯技术事实。 |
| C4 | "看门狗只看 HTTP 状态码，5xx 即死" | T5 的整条修法陷阱靠它；蓝军没查看门狗。 | `watchdog.py:100-110` 的 `probe(503) -> False`；`LONG_JOB_GLOBS`（:61-64）无出歌产物。已做。推论：`/api/health` 永远不要回 503；要改就改 body，保持 200。 |
| C5 | "`engine_backends()` 的解析是可靠的，问题只在缓存" | 第三方明确说没重跑 `--version`；蓝军说实测过。 | 我跑 `cpp/audiocpp_server.exe --version`：stdout 有 `backends: cpu,vulkan`，解析正确。T2 的缺陷在"探测失败落成 cpu 并永久缓存"，不在解析。 |
| C6 | "`HF_HUB_ENABLE_HF_TRANSFER=1` 在 `snapshot_download` 前设置就会生效" | 第三方 T3 的唯一支点；蓝军没查。 | 先 `from huggingface_hub import snapshot_download` 再设 env，打印 `constants.HF_HUB_ENABLE_HF_TRANSFER`：我得到 `False`。huggingface_hub 0.36 在 import 期求值。 |
| C7 | "出歌产物 `output/*.json` 在整首歌期间持续落盘，可以给看门狗免死" | T5 的修复建议这么写；两方都没看 `_output_write_meta` 的调用点。 | `app.py:2598/2625/2636/2649/2666`：只在回退、成功、失败时写；正常长歌的中段没有写。把 `output/*.json` 加进 `LONG_JOB_GLOBS` 只能覆盖"刚回退/刚结束"的瞬间，不能覆盖几小时的中段。 |
| C8 | "`_GPU_SEM` 覆盖所有生成入口" | 蓝军把它列进未验证，第三方说前端没有直连 `/api/music/generate`。 | `static/index.html` 走 `/api/generate/start`，确实不直连；但 7863 上的 `/api/music/generate` 仍可被 curl 直接打，绕过 `_GPU_SEM`。不是本轮必须修，但别把"没有前端调用点"当成"没有入口"。 |
| C9 | "`.bat` 加 `-text` 与 GBK 字节会冲突" | 这是蓝军 B10 修法的潜在坑，两方都没展开。 | GBK 尾字节范围 0x40–0xFE，不含 0x0A/0x0D，`text` 归一化只动 CR/LF，不会碰 GBK 字符；真正的问题是 `-text eol=crlf` 里的 `eol` 会被忽略，且现在改属性会把索引里的 LF 全部重写成 CRLF。**正确修法是改 README，不是改属性。** |

---

## 五、合并执行清单

> 定义：P0 = 发布阻断，修完才能提交/打标签；P1 = 本轮发布前应修，若明确记为已知问题可随版发布；P2 = 下一轮。改动量：S <10 行，M 10–50 行，L >50 行。

| 优先级 | 条目 | 来源 | 改动量 | 是否阻塞发布 |
| --- | --- | --- | --- | --- |
| P0 | `一键补齐运行件.bat` 的 `>/dev/null` → `>nul`，并加一条"bat 里不许出现 `/dev/null`"的静态测试 | B2 | S | **是** |
| P0 | `/api/health` 的真话化：保持 HTTP 200，`gateway` 不再无条件 ok（或前端改成只认 `audiocpp.status`），同步改 `static/index.html:4980` 与 `tests/test_shim_main.py:240`；启动脚本不要再用"任意响应=就绪" | B1 | M | **是** |
| P0 | 下载器：`fetch_chunk` 校验 `Content-Range` 起止与累计字节、拼接前断言每块 `length`；GGUF 至少解析头部张量偏移；`--workers` 做 >0 校验 | B4 | M | **是** |
| P0 | 安装器真话化：`report()` 按"exe+spec+server.json+主权重"实际状态打勾；`--with-*` 失败 `return 1/2`；`ensure_engine` 早退分支也验 spec；`snapshot_download` 包 try/except；pymss 全失败不许 `[OK]` | B6 + T4 + T9 | M | **是** |
| P1 | `--with-rvc` 补 `tensorboard`/`matplotlib`（或改吃上游 `requirments_cpu_py312.txt`），`ensure_rvc` 结尾做 import 自检 | B3 | S | 是（若发布 ⑦ 训练） |
| P1 | `music_generate` 的阻塞调用丢进 `run_in_threadpool`（不要再简单改成 `def`，那会丢掉现有 400 契约；`plan_abc` 是 dict body 的同步端点，不能直接照抄） | B5 | S | 是（若发布出歌） |
| P1 | RVC 补丁路径去掉 `infer/`、`train/` 前缀；源缺失要 `[!]` 出声；装完用 `_rvc_cli_caps()` 自检 `--filter-radius` | B8 | S | 是（若发布 ⑨ 换声） |
| P1 | `engine_backends()` 失败不要落成 `['cpu']` 永久缓存；返回 unknown / TTL，`_restart_audiocpp` 在 unknown 时不传 `--backend`；同时处理 `static/index.html:5043` 的 `available` 空数组回退（现在会默认 cuda） | T2 | M | 是（新回归） |
| P1 | 干净 clone 的测试：读本机 `cpp/` 的用例加 `skipUnless`；`test_backend_is_consistent...` 改用临时 server.json/state；CI 在裸树上跑绿或显式 skip | B7 | M | 否（但发布说明要改） |
| P1 | CHANGELOG 补本轮条目、按 `主.日.序` 升到 `v10.21.x`（或发布当天对应号），⑬ 版本随之更新 | B12 | S | 是（打标签前） |
| P1 | Vulkan 的 CPU 回退：把两处门改成 `in ("cuda","vulkan")`，补 Vulkan OOM 关键词，或在 UI 明说"Vulkan 不自动降级" | B18 | S–M | 否（可记为已知限制） |
| P2 | 文档四件套：603/573/614 对齐、`-text` 改 `text eol=crlf`、两个 UTF-8 `.bat` 的处理/豁免、LICENSE 自研清单补 `main.py`/`requirements*.txt`/`server.json.example` | B9/B10/B11/B20 | S | 否 |
| P2 | `X-Seed` 改读 `x-seed` 或用 `httpx.Headers`；`TRANSFORMERS_CACHE` 直接改 `transformers.utils.hub.TRANSFORMERS_CACHE` 并让测试断言生效值；sitecustomize 改成"缺那行就补那行" | B13/B14/B15 | S | 否 |
| P2 | 后端状态收敛：`ensure_engine` 用 `--version` 自证、`ensure_server_json` 的 state 写入与 server.json 一致；`main._engine_backend()` 与 `app.backend_mode()` 缺省统一 | B16/B17 | M | 否 |
| P2 | GGUF 结构校验（与 safetensors 同级）、补丁绑定上游 commit、`.orig` 每次保留带版本的后备 | B19/B21 | M | 否 |
| P2 | T1 先做真实生成期间的 `/health` 分布测量；再决定是"引擎忙状态"、独立 `/ping`，还是前端延迟翻红。**不要**直接把 `/api/health` 的引擎预算提高到 20 秒——那会让 `/api/health` 自身变慢，反而更容易触发看门狗的 15 秒短探 | T1 | M | 否（先测） |
| P2 | 加一条"`/api/health` 必须 200"的守卫测试；看门狗免死名单若要扩，必须用"生成中持续心跳"而不是只在结尾写的 `output/*.json` | T5 | S | 否 |
| P2 | `OEMCP` 纯数字校验；`.gitattributes` 删掉空的 `*.ps1` 或改成注释；README 的"不会静默降级"改成"部分重启路径会按构建能力改写" | T7/T8/T10 | S | 否 |
| — | T3、T6 不立项：T3 的 hf_transfer 崩溃不成立（env 设晚了），T6 的写 cuda 分支不可达；T6 反方向的"回退后不自动切回 GPU"可另开一条 P2 观察项 | T3/T6 | — | 否 |

---

## 六、抽查第三方自身的 🟡/归组条目（防定级套利）

- **T3（🟠）是本次最大的定级套利**：它把"清单里没有 hf_transfer"这个真事实，升级成"新机器必崩"的 🟠，但复现靠手工改 `constants`，不是代码路径。按"未经实测不得定高危"，这条应直接降为"文档/死代码清理"。
- **T6（🟡）是不可达代码**：它的触发条件写反了；`used_cpu_fallback` 为真时 `backend_mode()` 仍是 cuda，`:2658` 的 `and` 永远不成立。把它列成"新测试会因此变红"会误导修复优先级。
- **T1（🟠）是频率套利**：机制真实，但"每首歌"没有数据；把 `main.py` 对 `/v1/models` 的 19.7 秒注释借给 `/health`，是两方共同的推理跳跃。
- **T5（🟠）是"修法陷阱"不是"当前缺陷"**：当前代码没有任何 503，蓝军 B1 也没提 503；把它记成 🟠 会让发布门多背一条不存在的 bug。它的价值是约束——`/api/health` 永远 200。
- **T8（🟡）是无作用规则**：`git check-attr` 回空，删掉即可，不该占一个缺陷位。
- **T7（🟡）可达性低**：畸形 `cp:` 确实 Fatal，但本机 `chcp` 解析稳定；作为 P2 防御性校验可以，作为发布门理由不足。
- **第三方做对的两条**：T4（安装器早退不验 spec）和 T2（探测失败永久缓存）都是真缺陷；B18 上调、B7 爆炸半径纠正也都站得住。它的"8/8 B1–B8 复现"是真的，但它对自己新提的 T3/T6 没有做同等强度的反证——这正是"同模型互搏"的盲区。

---

## 七、裁定后的缺陷总表（机检表）

| 编号 | 级别 | 定级依据 | 一句话 | 维度 | 位置（文件:行号） |
| --- | --- | --- | --- | --- | --- |
| B1 | 🔴 | 实测 | 引擎全死时 `/api/health` 仍回 `gateway:"ok"`，前端据此点绿灯 | 4 错误处理 / 17 文档一致性 | main.py:223-246；static/index.html:4980 |
| B2 | 🔴 | 实测 | 新入口用 `>/dev/null`，有 Python 的全新 clone 也报"找不到任何 Python"并退 1 | 3 正确性 / 11 兼容性 | 一键补齐运行件.bat:17-18 |
| B3 | 🟠 | 实测 | `--with-rvc` 不装 tensorboard/matplotlib，训练 import 即死 | 12 依赖与供应链 | scripts/setup_runtime.py:54-55,548；runtime/rvc/train/train.py:60 |
| B4 | 🟠 | 实测 | 分块下载不校验 206 窗口，病态镜像拼出 N 倍垃圾仍报 `[OK]` | 3 正确性 | scripts/fetch_models.py:125-144,205-207 |
| B5 | 🟠 | 实测 | async 端点里跑同步 `start_audiocpp`，引擎重启期间网关冻结（上限 90 秒） | 5 并发与竞态 | main.py:331-332,371-373；settings.py:41 |
| B6 | 🟠 | 实测 | 安装器假绿灯：`--skip-models` 也打 `[OK] 写歌出歌`，可选件全失败仍 exit 0 | 4 错误处理 / 17 文档一致性 | scripts/setup_runtime.py:556,629-636 |
| B7 | 🟠 | 实测 | 干净 clone 上 `main.py` import 即抛，全量 24F+5E；"614 全绿"只在本机成立 | 10 测试有效性 / 17 文档一致性 | main.py:88-92；tests/test_shim_main.py:196-215,231-251 |
| B8 | 🟠 | 实测 | RVC 补丁路径多套一层，4 个源全不存在，静默跳过 | 3 正确性 / 16 发布工程 | scripts/setup_runtime.py:56-57,520-524 |
| B9 | 🟡 | 实测 | 测试条数 603/573/614 三套口径；559 是标签历史，不是当前口径 | 17 文档一致性 | README.md:233,316；README_EN.md:176,263；requirements.txt:7 |
| B10 | 🟡 | 实测 | README 写 `-text`，实际是 `text eol=crlf`；GBK 字符不受影响 | 17 文档一致性 | README.md:340；.gitattributes:10 |
| B11 | 🟡 | 实测 | 两个 `.bat` 是 UTF-8，编码测试名单不含它们 | 17 文档一致性 / 11 兼容性 | secrets/local_env.example.bat:1；dsh-plugin/启动AI工作台.bat:1；tests/test_script_encoding.py:23-33 |
| B12 | 🟡 | 实测 | CHANGELOG/版本没动，本轮改动仍对外报 v10.17.1 | 16 发布工程 | CHANGELOG.md:12；app.py:797-808 |
| B13 | 🟡 | 实测 | `X-Seed` 是死契约（`dict(headers)` 键降小写） | 3 正确性 | app.py:2450,2639；main.py:415 |
| B14 | 🟡 | 实测 | `TRANSFORMERS_CACHE` 修复只在 transformers 未 import 时有效，测试只断言 env | 10 测试有效性 | src/sheetsage_pt.py:25-39；tests/test_sheetsage_cache.py:30-40 |
| B15 | 🟡 | 实测 | sitecustomize 只在目标不存在时写，老机器拿不到兜底 | 14 迁移与状态演进 | scripts/setup_runtime.py:534-538 |
| B16 | 🟡 | 实测 | 已有 exe 就信请求的后端；server.json 不覆盖却无条件重写 state | 14 迁移与状态演进 | scripts/setup_runtime.py:260-265,324-339 |
| B17 | 🟡 | 实测 | 缺 state 时 shim 按 cpu、app 按 cuda | 2 架构与设计 | main.py:144；app.py:470-474 |
| B18 | 🟠 | 实测 | Vulkan 没有 CPU 兜底：预检与回切都以 `cuda` 为门（OOM 形态未实测） | 3 正确性 / 14 迁移 | app.py:2580,2618 |
| B19 | 🟡 | 推理·待实测 | GGUF 只查 magic+最小字节；`--workers 0` 无校验 | 12 依赖与供应链 | scripts/fetch_models.py:83-89,177 |
| B20 | 🟡 | 实测 | LICENSE 自研清单漏了本轮新增的根文件 | 19 许可证合规 | LICENSE:6 |
| B21 | 🟡 | 实测 | 补丁跟上游 main 并无条件覆盖，`.orig` 只留第一次 | 12 依赖与供应链 | scripts/setup_runtime.py:481,520-531 |
| T2 | 🟠 | 实测 | 探测失败永久缓存成 `['cpu']`，CUDA 机器静默掉 CPU，页面仍报 cuda | 3 正确性 / 14 迁移 | app.py:481-505,604-607,747-757 |
| T4 | 🟠 | 实测 | `ensure_engine` 早退不验 spec，打 `[OK] 写歌出歌` 但网关起不来 | 4 错误处理 / 16 发布工程 | scripts/setup_runtime.py:260-265,295-298；main.py:88-92 |
| T1 | 🟡 | 推理·待实测 | `/health` 2 秒预算把"慢/忙"判成"死"；频率未实测 | 3 正确性 / 5 并发 | main.py:229,371-373；static/index.html:4980 |
| T5 | 🟡 | 实测 | 修法陷阱：health 回 503 会被看门狗当死；免死名单无出歌产物 | 2 架构 / 4 错误处理 | watchdog.py:61-64,100-110,316 |
| T7 | 🟡 | 实测 | `PYTHONIOENCODING=cp<chcp>` 无校验，畸形值让 Python init Fatal | 11 兼容性 | scripts/启动音乐工作台.bat；一键补齐运行件.bat |
| T8 | 🟡 | 实测 | `.gitattributes` 的 `*.ps1` 是空规则，什么也没保证 | 16 发布工程 / 17 文档 | .gitattributes:16 |
| T9 | 🟡 | 实测 | `ensure_rvc` 下载异常裸奔；pymss 全失败仍 `[OK]`/True | 4 错误处理 | scripts/setup_runtime.py:504,544-548 |
| T10 | 🟡 | 实测 | README「不会静默降级」与本轮 `resolve_backend` 的部分路径相反 | 17 文档一致性 | README.md:418-420；app.py:604-607 |

> T3、T6 不在本表：T3 的 hf_transfer 崩溃不成立（env 在 import 之后设置，flag 仍为 False），T6 的 `:2658` 写 cuda 分支不可达。两者的证据与反驳见第一、三节。

### T3/T6 的反证（这两条被驳回，不占缺陷位）

**T3 反证**：按真实顺序先 import、后设 env，huggingface_hub 0.36.0 的常量仍是 `False`；随后下载走普通路径，报的是连接错误，不是 hf_transfer 的 `ValueError`。

```bash
./py312/python.exe - <<'PY'
import os, sys, tempfile
os.environ.pop("HF_HUB_ENABLE_HF_TRANSFER", None)
sys.modules["hf_transfer"] = None
from huggingface_hub import constants, file_download
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
print("constants flag =", constants.HF_HUB_ENABLE_HF_TRANSFER)
try:
    file_download.http_get("http://127.0.0.1:9/never", temp_file=tempfile.mktemp())
except Exception as e:
    print("raised:", type(e).__name__)
PY
```

实际输出：`constants flag = False`；`raised: ConnectionError`（不是 ValueError）。

**T6 反证**：回退路径只重启引擎，不写 state；只要 state 不是 cpu，`:2658` 的 `and` 就为假。用字符串检索即可看到写 cpu 的唯一调用点在 `backend_mode_set`（`:774`），不在 `_gen_run`。

```bash
grep -n '_engine_state_write' app.py
grep -n 'used_cpu_fallback' app.py
```

实际输出：`_engine_state_write` 只出现在 383/774/1910/2033/2660；`used_cpu_fallback` 的赋值在 2592/2619，判断在 2618/2658。2658 之前的任何回退都不会把 `backend_mode()` 变成 cpu，所以 2660 不可达。

---

## 八、关键条目取证（可重跑）

### B1 · 引擎全死时 health 仍报 gateway ok

**复现命令**

```bash
./py312/python.exe - <<'PY'
import importlib.util, os
from pathlib import Path
os.environ["YUE2_NO_ENGINE_AUTOSTART"] = "1"
spec = importlib.util.spec_from_file_location("shim_adj", Path("main.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m._engine_get = lambda *a, **k: None
from fastapi.testclient import TestClient
r = TestClient(m.app).get("/api/health")
print(r.status_code, r.json())
PY
```

实际输出：

```text
200 {'gateway': 'ok', 'audiocpp': {'status': 'down', 'backend': None, 'models': 0, 'loaded': None}, 'models': {'yue2': 'yue2-3b-q4_k_m.gguf'}}
```

### B2 · `>/dev/null` 让有 Python 的机器也报"找不到任何 Python"

**复现命令**

```bash
./py312/python.exe - <<'PY'
import subprocess, pathlib, shutil, os
root = pathlib.Path(os.environ["TEMP"]) / "adj_bat"
shutil.rmtree(root, ignore_errors=True); (root / "scripts").mkdir(parents=True)
shutil.copy("一键补齐运行件.bat", root)
(root / "scripts" / "setup_runtime.py").write_text('print("STUB RAN")\n', encoding="utf-8")
env = {"PATH": r"C:\Windows\system32;C:\Windows;" + str(pathlib.Path("py312").resolve()),
       "SystemRoot": r"C:\Windows", "TEMP": r"C:\Windows\Temp"}
r = subprocess.run(["cmd","/d","/c", str(root / "一键补齐运行件.bat")],
                   capture_output=True, cwd=root, env=env, input=b"\r\n", timeout=30)
print("rc =", r.returncode)
print(r.stdout.decode("gbk","replace"))
print("stderr:", r.stderr.decode("gbk","replace").strip())
PY
```

实际输出（`where python` 明明在 PATH 上）：

```text
rc = 1
[x] 这台机器上找不到任何 Python。
stderr: 系统找不到指定的路径。
系统找不到指定的路径。
```

我把同样的两行改成 `>nul` 后，`PY=[python]`，stderr 为空——修法有效。

### B3 · `--with-rvc` 缺 tensorboard/matplotlib，训练 import 即死

**复现命令**

```bash
./py312/python.exe -c "from torch.utils.tensorboard import SummaryWriter"
./py312/python.exe -c "import importlib.util as u; print('tensorboard', bool(u.find_spec('tensorboard')), '| matplotlib', bool(u.find_spec('matplotlib')))"
grep -n "tensorboard\|matplotlib" scripts/setup_runtime.py
```

实际输出：`ModuleNotFoundError: No module named 'tensorboard'`；`setup_runtime.py` 里 0 处；两者均 `False`。

### B4 · 分块下载拼出 N 倍垃圾仍报 OK

**复现命令**

```bash
./py312/python.exe - <<'PY'
import importlib.util, threading, tempfile, http.server, socketserver
from pathlib import Path
spec = importlib.util.spec_from_file_location("fm_adj", "scripts/fetch_models.py")
fm = importlib.util.module_from_spec(spec); spec.loader.exec_module(fm)
PAYLOAD = b"GGUF" + b"0123456789"
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        rng = self.headers.get("Range", "")
        if rng == "bytes=0-0":
            self.send_response(206); self.send_header("Content-Range", f"bytes 0-0/{len(PAYLOAD)}")
            self.send_header("Content-Length", "1"); self.end_headers(); self.wfile.write(PAYLOAD[:1]); return
        self.send_response(206)
        self.send_header("Content-Range", f"bytes 0-{len(PAYLOAD)-1}/{len(PAYLOAD)}")
        self.send_header("Content-Length", str(len(PAYLOAD))); self.end_headers(); self.wfile.write(PAYLOAD)
srv = socketserver.TCPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
fm.MIRROR = f"http://127.0.0.1:{srv.server_address[1]}"; fm.CHUNK = 4
with tempfile.TemporaryDirectory() as d:
    dest = Path(d) / "m.gguf"
    ok = fm.download("repo", "m.gguf", dest, 1, True, workers=1)
    print("download ->", ok, "| bytes:", dest.stat().st_size, "| verify:", fm.verify_one(dest,1,True))
srv.shutdown()
PY
```

实际输出：`download -> True | bytes: 56 | verify: (True, 'ok')`（源文件 14 字节）。

### B5 · async 端点里同步等引擎，整个事件循环冻结

**复现命令**

```bash
./py312/python.exe - <<'PY'
import importlib.util, time, threading, os
from pathlib import Path
os.environ["YUE2_NO_ENGINE_AUTOSTART"] = "1"
spec = importlib.util.spec_from_file_location("shim_b5", Path("main.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
from fastapi.testclient import TestClient
m._engine_alive = lambda timeout=1.5: False
m.start_audiocpp = lambda: (time.sleep(3.0), False)[1]
res = {}
with TestClient(m.app) as c:
    def post():
        t = time.time(); r = c.post("/api/music/generate", json={"style": "s", "lyrics": "a"})
        res["post"] = (r.status_code, round(time.time() - t, 2))
    th = threading.Thread(target=post); th.start(); time.sleep(0.4)
    t = time.time(); r = c.get("/api/health")
    res["health"] = (r.status_code, round(time.time() - t, 2))
    th.join()
print(res)
PY
```

实际输出：`{'post': (502, 3.0), 'health': (200, 2.91)}`。

### B6 · 安装器假绿灯 + 失败仍 exit 0

**复现命令**

```bash
./py312/python.exe - <<'PY'
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("sr_b6", "scripts/setup_runtime.py")
sr = importlib.util.module_from_spec(spec); spec.loader.exec_module(sr)
sr.report("cpu", "q4km")
sr.ensure_py312 = lambda: Path("py312/python.exe")
sr.install_deps = lambda idx: True
sr.ensure_engine = lambda b,c,p: "cpu"
sr.ensure_server_json = lambda b,q: True
sr.ensure_models = lambda q,w,s: True
sr.ensure_rvc = lambda i,p,s: False
sr.ensure_lrc = lambda i: False
sys.argv = ["setup_runtime.py", "--with-rvc", "--with-lrc"]
print("exit =", sr.main())
PY
```

实际输出：两组失败都打 `[x]`，`[OK] 写歌出歌` 仍在，`exit = 0`。

### B7 · 干净 clone 的测试全红

**复现命令**

```bash
rm -rf /tmp/adj_clean && mkdir -p /tmp/adj_clean && git checkout-index -a --prefix=/tmp/adj_clean/
cd /tmp/adj_clean && /c/Users/HW/Desktop/YuE2/YuE2-Music-Workbench/py312/python.exe -m unittest discover -s tests -p "test_shim_main.py"
mkdir -p cpp/model_specs runtime/data
cp /c/Users/HW/Desktop/YuE2/YuE2-Music-Workbench/cpp/model_specs/yue2.json cpp/model_specs/
cp /c/Users/HW/Desktop/YuE2/YuE2-Music-Workbench/runtime/data/engine_state.json runtime/data/
/c/Users/HW/Desktop/YuE2/YuE2-Music-Workbench/py312/python.exe -m unittest discover -s tests
```

实际输出：第一次 `Ran 1 test ... FAILED (errors=1)`（`RuntimeError: 找不到 audio.cpp 的 YuE2 参数表`）；补 spec+state 后 `Ran 614 tests ... FAILED (failures=24, errors=5, skipped=2)`。

### B8 · 四个 RVC 补丁源全部不存在

**复现命令**

```bash
./py312/python.exe - <<'PY'
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location("sr8", "scripts/setup_runtime.py")
sr = importlib.util.module_from_spec(spec); spec.loader.exec_module(sr)
for rel in sr.RVC_PATCHES:
    src = Path("patches") / ("rvc-clean-guards" if rel.startswith("train/") else "rvc-infer") / rel
    print(f"{rel:22s} src_exists={src.is_file()}")
PY
find patches -name "*.py" | sort
```

实际输出：4 个 `src_exists=False`；`find` 只看到 `patches/rvc-infer/cli.py`、`patches/rvc-infer/vc/modules.py`、`patches/rvc-infer/vc/pipeline.py`、`patches/rvc-clean-guards/preprocess.py` 这一层。

### B18 · Vulkan 没有 CPU 兜底

**复现命令**

```bash
grep -n 'backend_mode() == "cuda"' app.py
grep -n 'used_cpu_fallback and backend_mode' app.py
```

实际输出：

```text
2580:        if backend_mode() == "cuda":
2618:        if code != 200 and not used_cpu_fallback and backend_mode() == "cuda" and _is_vram_error(detail):
2658:        if used_cpu_fallback and backend_mode() == "cpu":
```

说明：实测的是两处门只认 cuda；Vulkan OOM 的具体形态没有复现，故级别上调但排 P1。

### T2 · 探测失败被永久缓存，CUDA 机器静默掉 CPU

**复现命令**

```bash
./py312/python.exe - <<'PY'
import json, os, sys, tempfile, types
from pathlib import Path
os.environ["YUE2_NO_ENGINE_AUTOSTART"] = "1"
sys.path.insert(0, "."); sys.path.insert(0, "src")
from fastapi import FastAPI
st = types.ModuleType("main"); st.app = FastAPI(); st.settings = types.SimpleNamespace(open_browser=True)
sys.modules["main"] = st
import app
tmp = Path(tempfile.mkdtemp())
app._ENGINE_STATE = tmp / "engine_state.json"
app._ENGINE_STATE.write_text(json.dumps({"backend": "cuda"}), encoding="utf-8")
app._hide_engine_windows_once = lambda: None
app._ENGINE_BACKENDS_CACHE = None
app.AUDIOCPP_BIN = tmp / "audiocpp_server.exe"
print("A) 未安装:", app.engine_backends())
class R:
    def __init__(self, out): self.stdout = out; self.returncode = 0
app.subprocess.run = lambda *a, **k: R("audio.cpp 0.9.2\nbackends: cpu,cuda\n")
print("B) 装好后:", app.engine_backends())
print("C) resolve cuda:", app.resolve_backend("cuda"))
calls = []
app.subprocess.Popen = lambda args, **k: calls.append(args) or object()
app._restart_audiocpp(str(tmp / "server.json"), backend="cuda")
print("D) 拉起:", calls[-1])
print("E) mode:", {k: v for k, v in app.backend_mode_get().items() if k in ("mode", "available")})
PY
```

实际输出：`A) ['cpu']`、`B) ['cpu']`、`C) ('cpu', '该引擎构建既没有 CUDA 也没有 Vulkan（可用：cpu），只能用 CPU')`、`D) [... '--backend', 'cpu']`、`E) {'mode': 'cuda', 'available': ['cpu']}`。

### T4 · 安装器早退分支不验 spec

**复现命令**

```bash
./py312/python.exe - <<'PY'
import importlib.util, tempfile
from pathlib import Path
spec = importlib.util.spec_from_file_location("sr_adj", "scripts/setup_runtime.py")
sr = importlib.util.module_from_spec(spec); spec.loader.exec_module(sr)
tmp = Path(tempfile.mkdtemp()); sr.ROOT = tmp
cpp = tmp / "cpp"; cpp.mkdir(); (cpp / "audiocpp_server.exe").write_bytes(b"x")
print("spec before", (cpp / "model_specs" / "yue2.json").is_file())
print("ensure_engine ->", repr(sr.ensure_engine("cpu", "12.4", None)))
print("spec after", (cpp / "model_specs" / "yue2.json").is_file())
PY
```

实际输出：`spec before False`、`ensure_engine -> 'cpu'`、`spec after False`。

---

## 九、一句话总结三方关系

蓝军把真问题找得最多但把范围和数字说得过满，第三方补出了 T2/T4 两条真缺陷、纠正了 B7 的爆炸半径，却为了凑 T 项把 hf_transfer 和回切写成了不存在的缺陷；裁定后发布仍被 B1/B2/B4/B6+T4 四条 P0 卡住，T1 在真实生成数据出来前不能按高危排期。
