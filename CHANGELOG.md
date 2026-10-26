# 更新日志

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式。

**版本号规则（v10 起，日期版）**：`主.日.序` —— 主版本固定为 `10`；第二段是**发布当天的日**，
第三段是**当天第几次发**（从 `0` 起）。例：2026-10-09 首次发布 → `10.9.0`；同日再发一版 → `10.9.1`；
次日发布 → `10.10.0`。此规则自 `v10.9.0` 起生效，`v2.0.0` 及更早沿用语义化版本（`v主.次.修订`）。

版本的唯一真源是 git 的**附注标签**（`git tag -l -n`），本文件是给人看的说明；
宣传文案里要写的版本号从本文件顶部条目取，不在别处另记一份数字（`app.py:_version_from_changelog()` 读本文件首条）。

## [v10.26.0] - 2026-10-26

### 内核唯一化与测试隔离（P0）
- **旧编译内核退役**：仓库根的 `main.cp312-win_amd64.pyd`（9-12 构建、23 个可转发字段）
  与 shim（10-24、按 spec 27 字段白名单）长期并存，生产与测试走两条不同的路。
  旧 pyd 已移到 `runtime/backup/`，默认只跑 `main.py`；`tests/test_kernel_hygiene.py`
  会把"仓库根出现 pyd"判红，回滚需显式 `YUE2_ALLOW_PYD=1`。
- **测试不再污染开发机**：新增 `tests/_sandbox.py` / `tests/__init__.py` / `tests/conftest.py`，
  统一禁用引擎自启、重定向测试状态，并在进程退出时还原被误写的 `cpp/server.json` 与 sidecar。
- **真生成硬闸**：`main.generate_response` 在测试进程（unittest/pytest 或 `YUE2_TESTING=1`）
  下对有效生成请求直接 503；需要真引擎的用例显式 `YUE2_ALLOW_REAL_ENGINE=1`。
  这一步是踩出来的：`test_gen_keep_awake` 还在 mock 旧的 `httpx.Client`，改走进程内后
  真把引擎拉起来跑了 AR/NAR，回归从 90 秒变成挂死。

### 引擎调用与资源（P1/P2）
- **整库 JSON 原子写**：新增 `src/json_store.py`，历史库、乐谱库、模板库从直接覆盖写
  改为 `.tmp + os.replace`。这三处不像 output meta 有条目级备份，崩在中途会把整份库
  变成非法 JSON；现在序列化失败或写盘失败都保留旧文件、清掉 `.tmp`。3 条用例覆盖
  往返、序列化失败、写盘失败。
- **统一音频入口**：新增 `src/audio_io.py`，后缀白名单、ffmpeg 定位、转 WAV 只留一份真源。
  实测本机 `soundfile` 能直读 wav/flac/mp3/ogg(Vorbis)，但读不了 m4a/aac/opus/webm/wma；
  音色 ASR/降噪、LRC 对齐、人声分离这些直接走 soundfile 的路径，现在会先把这些格式转成
  16k 单声道 WAV，不再“上传成功、处理时才炸”。音色建档保留原采样率。score/RVC 原有
  ffmpeg/torchaudio 管线不变。新增 9 条真实 m4a 转换与路由集成用例。
- **生成去反向 HTTP**：`app._gen_run_once` 直接调用 `main.generate_response`（进程内），
  不再 HTTP 打自己的 `:7863`；两阶段 AR 检查点逻辑只保留在 `main.py` 一处。
- **引擎所有权**：`_restart_audiocpp` / 终止生成不再 `taskkill /IM audiocpp_server.exe`
  （会连坐杀掉本机其它工作台实例），改为只杀本进程拉起/接管的 PID；句柄丢失时按端口
  查 PID 并核对镜像名，测试进程没有句柄时一律不动手。
- **统一引擎客户端**：新增 `src/engine_client.py`，健康探测、plan_abc 直连、卸载常驻模型
  都走同一套 base_url / timeout / trust_env。
- **上传不再冻事件循环**：`_stream_upload_to` 与 LRC 上传改 `aiofiles`；RVC 素材目录
  统计改 `run_in_threadpool`。新增 AST 守门用例，禁止 `async def` 里直接 `open()`。

### 结构：领域路由拆分（P1）
- `app.py` 继续瘦身（10,561 → 9,686 行）：音色/歌词模板/出谱/LRC/历史/模型校验/系统域
  各自成模块（`src/routers/*.py`），纯逻辑另落 `src/abc_analysis.py`、
  `src/vae_window.py`、`src/system_probe.py`。
- 系统域（模型清单、能力清单、设置、后端模式、VAE 窗口、系统信息）整体迁到
  `src/routers/system.py`；依赖通过 `host` 在请求时解析，测试对
  `app._active_vae_config` 等名字的 patch 仍生效，`app.system_info()` 兼容别名保留。
- 回归：全量 874 通过 / 1 跳过；系统域定向 129 条通过。生成/RVC/批处理仍留在
  `app.py`——它们持有进程级队列与引擎句柄，拆出需要先搬运行状态，另行评估。

### 修复：先跑 LRC 再出谱时 MERT 缓存“找不到”
- 现象：同一网关进程里先做⑭歌词对齐（FunASR/modelscope 已导入 transformers），再出谱会报
  “本地缓存不完整 / 连不上 huggingface”，尽管 `runtime/hf_download/hub` 里 MERT-v2 权重齐全。
- 根因：`TRANSFORMERS_CACHE` 在 transformers 导入时就固化成常量，之后 `configure_hf_home()`
  改环境变量对已导入的库无效；MERT 父模型于是去错误缓存里找。
- 修法：`get_model()` 显式把 `cache_dir=runtime/hf_download/hub` 传给 `from_pretrained`，
  并由自定义加载器原样传给 MERT 父模型，不再依赖任何全局常量；`local_files_only` 继续按离线判定。
- 真机验证（同一进程顺序）：LRC 36 行 + eLRC → 出谱 193 字符 ABC，PASS；
  新增 1 条用例锁住 `cache_dir + local_files_only`。

### 功能：③ 参考歌曲也能直接选服务器歌曲
- `/api/score` 新增可选 `rid`：可直接转谱 `output/` 里已生成或入库的整曲，不必重新上传；
  `audio` 与 `rid` 至少给一个，两者都不给返回明确 400。
- ③ 页新增“服务器上已有的歌”下拉（与 ⑭ 共用同一套取库逻辑），选中后按钮变为
  「转谱所选服务器歌曲」；拖/选本地文件会自动清空下拉，避免文案与提交内容不一致。
- 2 条后端用例（rid 走完整转谱入库、两者都不给 400）+ 页面契约用例；
  无头 14 页巡检 14/14、overflow=0、JS 异常=0。

### 功能：⑭ 歌词时间轴可直接选服务器歌曲
- `/api/lrc/start` 新增可选 `rid`：不重传音频，直接把 output/ 里已生成或入库的整曲复制进
  独立任务目录再对齐；`file` 与 `rid` 必须给一个，首尾都有明确 400。
- ⑭ 页新增“服务器上已有的歌”下拉，进页时由本页自己从 `/api/generate/list` 与
  `/api/history` 取库（符合“页面不代填、输入只来自本页/服务器库”的规矩）；
  下拉实测 88 首，上传新音频仍保留。
- 歌词框 14→12 行，状态更新会滚进视野：新增下拉后状态行不再被卡片底部裁掉。
- 2 条后端用例（服务器的歌走完整对齐流程、两者都不给 400）+ 页面契约用例。

### 修复：模型校验/修复跟随当前 server.json
- 以前 `/api/models/verify` 与“修复”永远检查/下载 **q4** 包；机器上实际跑 q8 时，
  设置页会报“模型缺件”，点修复还会白下约 4GB q4。
- 现在按 `server.json` 的主模型路径动态生成清单（主模型 + 同目录/公共 VAE + sidecars +
  SheetSage2）；只有配置/路径都拿不到时才回退 q4 清单（新装环境仍可修）。
  `verify` 返回 `source: active|fallback-q4`，未提供在线源的量化档会明确说“没有修复源”。
- 本机实测：`source=active`，q8 主模型/VAE/4 个 sidecar/SheetSage2 共 7 个文件全部 OK。
  6 条用例覆盖 q8/q4/未知量化/无配置兜底/响应来源。

### 修复：④ 乐谱加工"调转完了、唱出来还是不对"
- 症状：移调后谱面看着像对的，生成出来的旋律却跑调；不同移调量坏的程度不同
  （−1/−2/−3/+2/+3/+7 最明显，正好是"差半音"的分布）。用户连续两次反馈
  "验证过 +5 没问题、但换个数又不对"，说明不是单个参数的 bug。
- 四个根因，逐个修掉：
  1. **只算音名不算调号**：K:E 的 `f` 实际是 F#，旧实现按 F 算，整条旋律偏半音；
     SheetSage2 输出的谱几乎都带调号且不在音上写显式升降号，所以必踩。
  2. **不认小节内变音继承**：ABC 里显式 `^/_/=` 一直有效到小节线，其后同字母裸音
     全部继承。旧实现逐音只看调号，所有继承音都差半音。
  3. **变音记号写成后缀**（`c#'2`）：标准 ABC 只有前缀 `^_=`，后缀会被解析器当噪音
     丢掉；旧正则 `[#bn]?` 还会把 `bb`（两个 B 音）并成一个降号，吞音且节奏塌缩。
  4. **调式操作与源调式不匹配时只改 `K:` 不动旋律**：对已是小调的谱选"→关系小调"，
     K:Em（1 升）被写成 K:C#m（4 升），同一个 `f` 从 F 变成 F#，整曲凭空高半音；
     K:E 选"→关系大调"则整曲低半音。这比不做更糟。
- 修法：输入侧按「显式变音 > 小节内继承 > 调号自动升降」叠成实际音高；输出侧按新调号
  拼写，需要变音的音**重复写显式记号**（不依赖下游解析器是否实现继承）、必要时补 `=`
  还原号；`_spell_in_key` 保证极端调号（K:C#/K:Cb）跨八度换字母时音高不跑。
  调式转换改为**幂等**：目标调式已达成时逐字节 no-op，接口回 `mode_noop` 并在页面
  用中性提示（不打绿勾，免得"什么都没变"显示成"已加工"）。
- 换 oracle 之后又挖出四个同类问题（都是"看着对、唱着错"）：
  5. **变音继承的范围搞错了**：ABC 2.1 与 abcjs 都只在「同字母 + 同八度」内继承，
     `^^C` 不影响 `c`/`c'`。旧状态按字母记，另一个八度的同名音被一起升降。
     真实谱里出现过 `^A`，这类错听起来就是"某个音莫名高半音"。
  6. **关系大小调把调号也改了**：C# 大调（7 升）的关系小调是 A#m（7 升），
     却被"少用升降号"优化成 Bbm（5 降）——调号一变，同一条旋律整曲低半音。
     现在关系调**沿用源调号的升降数**，一个记号都不动。
  7. **中途变调被抹平**：正文里的 `K:` 行（或行内 `[K:G]`）旧实现一律改写成文件头的
     新调名，一段 K:C→K:G 的曲子移调后两个调号变同一个，后半段整段错；而且其后音符
     仍按文件头的调号算音高。现在每段按自己的调号算、各自移调写回。
  8. **等音调选择反了**：C+1 曾写成 K:C#（7 升），实际该是 K:Db（5 降）。
     输出侧取升降号更少的一侧；输入侧仍按谱面**声明的写法**读（K:C# 就是 7 升，
     不能被等音改选迁就成 Db，否则每个受调号影响的音都差半音）。
     同时修掉坏谱哨兵的假阳性：正文 `K: F#` 的调名 `#` 曾被当成"音符后缀 #"。
- 顺带修调式判定：后端 `K:Gmix` 曾被当成小调（`rest.startswith("m")`），
  前端 `seKeyMode` 的 `/m(\b|in|aj|$)/` 把 `K:Gmaj` 判成小调、把对应两个选项禁用。
  前后端现在共用同一条规则，并加了一条真跑 node 的用例（无 node 时跳过）。
- **验证用第三方解析器 abcjs 做 oracle**（不拿自己的尺子量自己）：
  · `tests/test_abc_oracle.py` + `tests/abc_oracle.js`：8 段合成谱（K:E/Eb/Gm/C#、
    小节内继承与双变音、连音块/连字符/三连音、两种中途变调）× 13 档移调 × 4 种调式 ×
    4 组移调+调式 × 去和弦，共 **157 例**，作为常驻测试（本机无 node 时自动跳过）。
  · 真实语料一次性复核：11 份真实谱（K:E/D/Em/Eb/Bb/C/F/G/Gm/Cm/Am）× 移调 ±1…±12 ×
    4 种调式 × 组合 × 去和弦 × 链式移调，共 **396 例全过**——逐音 MIDI 与预期一致、
    时值不变、和弦根音与斜杠低音同步、输出零双重变音、`+5 再 +5` 与 `+10` 逐字节相同。
  · 线上 7863 实测《如诗》+5/+1/−3/+12 四档同样通过。
  单元测试 71 → 90 条，全量 **922 通过 / 1 跳过**。
- 顺手修两条"会骗人的用例"（`tests/test_rvc_queue_persist.py`）：一条断言
  `worker_calls == []`，可恢复流程本来就会拉起队列线程，替身 worker 必然被叫到；
  另一条想"同步跑真实 worker"，却调到了 setUp 里替换过的替身，于是永远得到 done、
  测不到 error 分支。两条改完后才真正覆盖它们声称覆盖的路径。

### 上游评估（P3）
- **出谱路线 A/B**：同一段 30 秒音频，引擎 SheetSage2（Ins 的 audio.cpp 0.8.2 + F32 权重）
  冷启动 **67.7s** / 432 字符 / 52 音符 / 12 和弦；本项目 PyTorch 官方路径 **90.2s** /
  435 字符 / 51 音符 / 12 和弦。调性、拍号、BPM、和弦数完全一致，ABC 原文相似度 **0.9873**。
- 结论：输出等价，引擎冷启动快约 25%；但要额外 2.7GB 权重、且本次是 0.8.2 旧引擎。
  现阶段**不迁移**，保留 PyTorch 路径；待本项目 0.9.2 接入 SheetSage2 后补热启动/显存
  A/B 再定。完整记录：`docs/出谱路线AB-20261026.md`。

### 安全（P3）
- **源码发布包构建器**：`scripts/build_release.py` 按「git 已跟踪 + 未跟踪但未被忽略」收集文件，
  排除 `cpp/runtime/py312/模型/真实密钥/编译内核`，打包前跑 `check_release_hygiene --release`，
  产出 zip + sha256。用例实测 233 个文件 / 9.7MB；把运行件或真实密钥塞进工作树会被卫生检查拦下。
- **发布卫生检查器**：`scripts/check_release_hygiene.py` 检查编译内核、真实密钥
  （`sk-...` / `YUE2_ACCESS_TOKEN=...`）、模型权重、运行件是否被 git 跟踪，依赖是否全部
  钉版本且保留 UTF-8 BOM，以及端口表/CHANGELOG/前端产物是否完整；`--release` 还会拒绝
  发布目录里的 `secrets/local_env.bat`。只报路径不回显密钥；13 条用例覆盖干净包与各种污染。
  已进 CI 硬门禁与 README 发版清单。
- **可选网关直连令牌**：设 `YUE2_ACCESS_TOKEN` 后，局域网设备**直连 7863** 必须带
  `X-Yue2-Token`、`Authorization: Bearer`、`?token=` 或 cookie；本机回环（本机 UI 与
  dsh 反代）永远免令牌，避免把自己锁在门外。`?token=` 首次访问会换成 HttpOnly cookie，
  HTML 页再 303 去掉地址栏里的 token；未设令牌时行为与旧版一致。
- ⑬ 关于页的系统信息新增 `security.lan_mode/direct_access_token/loopback_exempt`，
  只报布尔、不回令牌本身；安全项的 false 显示为“未开启”而不是红叉。
- `secrets/local_env.example.bat` 与 FAQ 已补令牌用法；13 条安全用例覆盖无令牌 401、
  错令牌、四种带令牌方式、query→cookie、回环豁免、未配置兼容与不回显令牌。

### 结构与验证（P1）
- **ABC 分析纯函数拆出**：`src/abc_analysis.py`（调性/拍号/BPM/曲式/音域/和弦判定）。
  无 app 全局依赖；app 只留 `_abc_analyze/_abc_has_chords` 兼容别名，score 入库、ABC 路由、
  `_resolve_cot` 全部走同一实现。`app.py` 10,183 → **10,057 行**；2 条纯函数用例锁住
  和弦宽松判定与摘要字段。
- **模型校验/修复子域拆出**：`src/routers/model_files.py`（verify/repair/status 与清单生成）。
  路径/配置读取由 app 注入，`_MODEL_MANIFEST_MAIN / _active_model_manifest / models_verify`
  保留兼容别名；`test_fetch_models` 的清单同步断言改读新真源。`app.py` 10,302 → **10,183 行**；
  2 条契约测试锁住路由与 7 文件校验结果。
- **14 页浏览器巡检工具**：`scripts/ui_sweep.js`（Node + CDP）。逐页点击并检查面板可见、
  横向溢出、JS 异常，截图与 `report.json` 落到 `runtime/data/ui-sweep/<时间戳>/`。
  自动挑 9300+ 空闲调试端口（避免连到被强杀残留的僵尸 Edge）、每条 CDP 命令 15s 超时、
  整轮 150s 总超时。本次实测 **14/14 ok，overflow=0，JS 异常 0**。
- **历史 CRUD 拆出**：`src/routers/history.py`（列表/上传入库/回放/清空/删除）。
  `history_active` 留在 app.py（它聚合生成/换声/训练实时状态）；`HIST_DIR` 用 callable
  注入，现有测试对 `HIST_DIR/HIST_JSON` 的重定向仍生效。`app.py` 10,327 → **10,240 行**；
  2 条契约测试覆盖路由与上传/回放/删除/清空往返。
- **LRC 域路由拆出**：`src/routers/lrc.py`（5 个歌词对齐路由）。worker 与 `_lrc_sa_*`
  仍在 app.py，现有用例 patch `app.lrc_align_mod` 的钩子保持不变；`LRC_STANDALONE_DIR`
  用 callable 注入，测试重定向仍生效。`app.py` 10,429 → **10,327 行**；
  2 条契约测试锁住路由与动态目录。
- **乐谱域路由拆出**：`src/routers/score.py`（转谱任务 + 乐谱库 CRUD + 导出）。
  `SCORES_DIR` 以 callable 注入，运行时/测试改 `app.SCORES_DIR` 仍生效；`_SCORE_JOBS`
  两把锁在 app 保留同名别名指向同一对象，旧用例的 clear/patch 不失效。
  `app.py` 从 10,560 行降到 10,429 行；3 条契约测试锁住路由、共享对象与动态目录。
- **音色库路由拆出**：`src/routers/voices.py`（工厂注入依赖），`app.py` 只留
  `router.include_router(...)`；新增路径/方法契约测试。
- **内容工具路由拆出**：`src/routers/content.py`（歌词自检 + 模板持久化）。模板路径用
  callable 注入，运行时改 `app._TEMPLATES_FILE` 与测试重定向都生效；`app.save_template`
  保留为兼容包装，存储逻辑只有一份。
- **页面清单唯一真源**：新增 `static/pages.json`，HTML 侧栏、`test_page_contract`、
  dsh `LAB_TABS`、README 页面表四处交叉校验。顺带修掉 README 表重复 ⑥、缺 ⑧/⑩、
  编号与页面不一致的漂移；ailab 是否进 dsh 由 manifest 的 `dsh` 字段表达，不再写死在测试里。
- **生成三档预设**：②⑤ 共用生成面板新增「草稿 / 标准 / 成曲」（off·8 / full·16 / full·32），
  只改参数不自动提交；填了乐谱时草稿自动用 melody。手改 CoT/步数后高亮会切成“自定义”，
  不再显示假档位。底部文案从“约 4-8 分钟”改为实测的 60-90 分钟，并指向草稿档。
  无头 Edge 实测：默认成曲、草稿 off/8、标准 full/16、有谱草稿 melody/8、手改 20 步→自定义，
  JS 异常 0；布局遵守 `.preset-row > .row-label` 对齐规矩。
- **前端源文件拆分（运行时仍是单文件）**：`static/index.html` 里的样式与脚本拆到
  `web/app.css`、`web/app.js`，HTML 模板放在 `web/index.html`；`scripts/build_ui.py`
  逐字节重建 `static/index.html`，`--check` 进 CI 硬门禁。页面行为与字节内容不变
  （657,592 字节逐字节一致），网关仍不需要 StaticFiles，也不会多一次请求。
  5 条用例锁住“源文件可重建、模板标记、生成物无标记、无外链 CSS/JS”。
- **真引擎冒烟脚本**：`scripts/smoke_e2e.py --kernel|--gen|--score`。本轮实测：
  4 步小样 50.1 秒出 7.13MB WAV；8 秒 mp3 出谱返回 193 字符 ABC；跑完显存回到 535MB。
- 全量回归 **874 条通过（1 skip）**；测试期间 8080/7863 无监听（未拉起引擎）。

### 用户体验与可靠性（issue #6-#9）
- **#6 推荐参数 toast 只在用户主动操作时弹**：以前切页/刷新只要路过 ⑨ 换声页就会弹
  「已按推荐参数填入旋钮」——用户没碰任何控件却被"通知"了一头雾水。现在自动路径
  （renderRvc/切页/轮询）一律静默填参，只有**用户换音色下拉**或**点「一键套用推荐」**
  才提示；「一键套用」按钮文案随之区分两种模式。契约测试锁住三处调用签名。
- **#7 首次使用默认草稿档**：新用户打开 ② 生成页，默认是 full/32 的成曲档——在 6GB
  显卡上要等 60-90 分钟，第一首歌直接劝退。现在 `<select id="cot">` 的 selected 落在
  off、步数默认 8（草稿档）；**已保存过选择的用户不受影响**（localStorage 优先级高于
  默认值，原有恢复逻辑不动）。契约测试锁住 HTML 里的 selected 位与 steps 默认值。
- **#8 显式「送到下一环节」按钮**：①→②/⑤、③→④/⑤、④→⑤、谱编辑→⑤ 共 5 个按钮，
  把"复制粘贴换个页"的手动接力变成一键直达。这是页面独立性规矩的**唯一显式例外**：
  仅用户点击触发（切页/刷新绝不代填）、目标页有不同内容时先 confirm、空或相同则静默写入。
  `PageIndependence` 契约测试新增防退化断言（switchTab 内不得出现接力调用），
  README「页面之间不代填」一节同步写明例外的三个条件。
- **#9 换声队列落盘 + 重启恢复**：`_RVC_QUEUE/_RVC_JOBS` 以前纯内存，网关被看门狗杀掉
  或手动重启后，排队中的任务无声消失（用户明明看到"已加入队列第 N 位"）。现在非终态
  条目实时落盘 `runtime/rvc/queue_state.json`（终态即摘除，成品归档仍以 output/meta.json
  为权威）；启动自恢复 `_rvc_boot_resume()` 分四路：pending 重校验（音色/源文件还在）后
  排回、running 如实归档「服务重启，任务中断」（不自动重跑——推理子进程随网关一起死了，
  半截 converted.wav 不可信）、缺件判死留痕、崩溃循环保护（600 秒窗口内复活超 3 次停手，
  沿用批量队列的 `_REVIVE_MAX` 口径）。job 新增 `src_file` 字段解决上传扩展名不定
  （src.mp3 等）重启后找不回源文件的问题。新增 `tests/test_rvc_queue_persist.py` 10 条用例
  （替身 worker + 沙箱落盘，全程不碰 GPU/子进程）。
- **有窗口浏览器肉眼验收又抓到两处**（纯 DOM 断言抓不到的）：其一，恢复上次表单后
  档位高亮停在「草稿」而字段值已是 full/32——`applyConfig` 直写 value 不派发事件，
  恢复 IIFE 里补一句 `syncGenPresetFromFields()`；其二，⑨ 页「诚实说明」还是落盘
  之前的旧文案（"排队条目会丢"），已改写为与 #9 实际行为一致。两处均有契约测试锁定。
- **#9 队列文件损坏也能自证**：整份 `queue_state.json` 损坏（手工改坏/磁盘问题）时，
  原文件另存为 `queue_state.corrupt-<时间>.json` 并打 WARN，不再静默按空队列启动把现场
  覆盖掉；文件里单条畸形记录（缺 id、不是对象）跳过处理，不连坐同一文件里的其他任务。
  新增 2 条用例（损坏文件留证、坏条目跳过好条目照常恢复）。
- 全量回归 **926 条通过（1 skip）**；`build_ui.py --check` 通过。

## [v10.24.1] - 2026-10-24

### 修复：双击「启动音乐工作台.bat」毫无反应（窗口一闪而过，什么都不启动）
- **现象**：双击根目录的启动脚本，窗口闪一下就没了，网关/dsh/引擎一个都没起。
  服务其实没坏 —— 手动访问 `http://127.0.0.1:7863/` 一切正常。
- **根因（环境变了，不是代码写错）**：本机开启了 Windows 的「Beta: 使用 Unicode UTF-8
  提供全球语言支持」，`HKLM\...\Nls\CodePage` 的 `OEMCP` 与 `ACP` 都是 **65001**。
  cmd.exe 用**控制台 OEM 码页**解析批处理，而当时所有 `.bat` 按旧约定存的是 **GBK** 字节 →
  cmd 按 UTF-8 去解 GBK → 中文文件名变成「锟斤拷」→
  `call "%~dp0scripts\启动音乐工作台.bat"` 直接报
  `'...' is not recognized as an internal or external command` → 脚本在这一行就退出了，
  后面的网关/看门狗/dsh 全没执行。实测复现：
  ```
  cmd /c 启动音乐工作台.bat no-open
  # '...\scripts\<garbage>.bat' is not recognized as an internal or external command
  # exit=1
  ```
- **修法（双保险，65001 与 936 都能跑）**：
  1. 逻辑脚本改用 **ASCII 文件名**：`scripts/start_lab.bat`、`start_dsh.bat`、`stop_lab.bat`
     —— **路径不参与码页解码，这才是根因修复**；
  2. 原来的中文名脚本保留成一层**纯 ASCII 兼容壳**（`call "%~dp0start_lab.bat" %*`），
     旧快捷方式、`register_protocol` 注册的 `yue2workbench://` 协议、文档里的路径都不失效；
  3. 所有 `.bat` 转 **UTF-8（无 BOM）+ CRLF**，两个入口只 `call` ASCII 目标；
  4. `watchdog.py` 的 `DSH_BAT` 也改指 `scripts/start_dsh.bat`。
- **验证**：先停掉网关/dsh/引擎/看门狗，再 `cmd /c "启动音乐工作台.bat no-open"` 冷启动 →
  三端口全部拉起（7863 / 3081 / 8080），`/api/health` 回 `gateway=ok`、引擎 `cuda`，
  首页 200（654KB），看门狗与 dsh 日志正常，`_dsh_err.log` 0 字节。
- **规矩改了**：`.gitattributes` 与 README「编码规矩」从「`.bat` 存 GBK」改为
  「UTF-8 + 调用目标必须 ASCII」，并写明为什么（这次的实测链）。
  `tests/test_script_encoding.py` 同步重写：新增「`call` 目标必须是纯 ASCII」
  「兼容壳只能有那两行」「入口必须 call ASCII 逻辑脚本」三条，把这次的根因直接钉死。

### 顺带
- `tests/test_ai_native.py` 改为读 `scripts/start_lab.bat`（UTF-8）。

## [v10.24.0] - 2026-10-24

> 整改 GitHub issue #4（评审 v10.23.0 的 8 条）。每条都先在**扁平布局**
> （权重摊在 `cpp/model/`、sidecar 在 `cpp/model/sidecars/`）与**干净导出树**上复现过，
> 修完再用同一条件验证。全量从 727 条涨到 **756 条全绿**；干净 clone 从
> **24 红 + 5 错** 降到 **0 红 0 错（55 skipped）**。

### 修复（P1）：⑫「VAE 显存优化」改不到正在用的那份配置，预检还可能读错
- 根因：三处同一个 glob `*/sidecars/yue2-vae-config.json`（`app.py` 的 `_vae_window_configs`、
  `_VAE_CONFIG_PATHS`、`scripts/set_vae_window.py` 的 `find_configs`）只匹配"每个量化档一个
  子目录"的布局。扁平布局下 `cpp/model/sidecars/yue2-vae-config.json`（当前加载的 q8 用的那份）
  **一份都匹配不到** → 点 512 改的是没在用的 q4 那份，页面却显示 512；预检读
  `_VAE_CONFIG_PATHS[0]`（排序第一 = q4 的 1024）→ 估算偏小 → OOM 或静默降级 CPU。
- 新增 `_active_model_gguf()` / `_active_vae_config()`：从 `cpp/server.json` 的
  `models[0].session_options["yue2.model_gguf"]`（缺则 `path` 的 basename）解析，**目录以 `path` 为准**，
  sidecar 取"模型同目录下 `sidecars/`"（与引擎的 `loaded_vae_from=同目录` 一致）。
  `_vae_window_configs()` 两种布局都认，且**在用那份永远排第一**。
- `GET /api/vae/window` 增加 `active` / `model` / `configs[].active` / `mismatch`；
  定位不到时给 `note` 明说（不拿别的量化档顶替）。
- `POST` 默认**只改 active 那一份**，`{"all": true}` 才批量；回包 `changed` 逐份回显
  `{path, old, new, active}`。定位不到 active 时退回全改，但 message 里写明"已改全部 N 份"。
- 预检改为读 active 那份，并把"读自哪个文件、decode_core_frames 多少"打进日志。
- `scripts/set_vae_window.py` 同步兼容两种布局，输出里标「（在用）」。
- 前端 `vaeWindowLabel` 带上模型名，`title` 列出全部 sidecar 明细；切换成功后不再拿本地目标值
  直接写界面，而是**重新拉一次真实状态**（那正是"显示 A 实际 B"的来源）。

### 修复（P1）：DeepSeek Key 清除无效、页面永远显示"已配置"
- 根因：`_deepseek_key_set()` 先看环境变量，而"清除"只写空文件 → env 在场时永远为真；
  `GET /api/settings` 又不报来源，用户以为换了自己的 key，实际仍走 env
  （`tests/test_batch_restart.py:245` 就是这么红的）。
- 新增 `_deepseek_key_source()` → `"env" | "file" | null`，`GET/POST /api/settings` 都返回。
- 清除时若来源是 env，回包明确说"已删除本页保存的 Key，但当前 Key 来自环境变量
  `DEEPSEEK_API_KEY`，要换请改 `local_env.bat` 后重启"；保存时同样提示"文件不会生效"。
- ⑫ 按来源给三种文案（env 用 `--warn` 色），清除按钮的 confirm 也提前说明 env 清不掉。
- 用例补上 `delenv` 隔离，并新增 3 条：env 来源清除后仍为已配置且文案含"环境变量/local_env.bat"、
  env 在场时保存要警告、全无配置时来源为 `null`。

### 修复（P2）：测试对可选组件不隔离，干净 clone 24 红 + 5 错
- 根因：`cpp/`、`runtime/`、`py312/` 全在 `.gitignore` 里，缺件用例要么拿到
  `500 {"detail":"rvc python 环境缺失"}`，要么直接 `FileNotFoundError`。红绿混在一起，
  新贡献者分不清"我改坏了"和"我没装运行件"。
- 新增 `tests/_env.py`：`HAS_RVC_ENV` / `HAS_PYMSS` / `HAS_PY312` / `HAS_ENGINE` /
  `HAS_SERVER_JSON` 探针 + 统一跳过理由（判据一律"文件真的在"，环境变量能把机器说成装好了，文件不会）。
  按**模块/类级** `skipUnless`，用例根本不收集。
- 端点对缺件从 **500 改 503**（`_require_rvc_env()`）：500 的意思是"代码崩了"，503 才配得上
  "这台机器没装可选运行件"，文案直接给「一键补齐运行件.bat」的指引。

### 修复（P2）：测试把本机量化档写死
- `tests/test_shim_main.py` 两条断言原本写死 `yue2-3b-q4_k_m.gguf`，换 q8 立刻假红。
  改为期望值从 `cpp/server.json` **现读**（`session_options.yue2.model_gguf`，缺则 `path` 的
  basename），并加"必须以 `.gguf` 结尾"守住"取不到就退回 id"那种退化。

### 修复（P2）：老安装缺 `busy_timeout_ms`，无迁移无提示
- 引擎自带的 5 分钟 busy 守卫会把长跑的歌中途 503 掐断，而日志里没有一条指向配置。
- 新增 `_ensure_server_json_busy_timeout()`，网关启动时调用：**缺失 → 备份为
  `*.bak-busy-timeout` 后补 0**；显式非 0 → 只 WARN 不擅改；模型级也带这个键时单独提示
  （本机就是顶层 0、模型级 600000=10 分钟）。
- `scripts/setup_runtime.py` 的"已存在不覆盖"分支现在也幂等补键（以前只在新建时写模板，
  老安装重跑脚本永远补不上）。

### 修复（P2）：`engine_backends()` 缓存没有失效点
- 首次在没 `cpp/*.exe` 的机器上调用会缓存 `['cpu']`，随后装好引擎仍按 cpu 启动，
  直到重启网关（`resolve_backend("cuda")` 还附一句"该构建只能用 CPU"）。
- 加 60 秒 TTL + `_invalidate_engine_backends()`；`_restart_audiocpp` 在 taskkill **之前**
  失效缓存（引擎二进制可能刚被替换），这次重启就按新能力判后端。

### 修复（P3）：`main.cp312-win_amd64.pyd` 遮蔽 `main.py` 且无任何提示
- CPython 扩展模块优先于同名 `.py`，而 `.pyd` 在 `.gitignore` 里、老安装机器上普遍存在
  → 同一版本两台机器走不同内核（pyd 不做字段白名单/丢键日志），没人看得出来。
- 新增 `_gateway_kernel()` → `{kind: "pyd"|"shim", path, note}`；**启动日志**打印
  `[startup] 网关内核：…`，**`/api/system/info`** 新增 `gateway_kernel`，⑬ 关于页可见
  （`SYS_LABEL` 补三个中文标签 + 归入"端口与版本"组）。
- README 写清两条路的能力差异与切换方式（移走同目录 pyd）。启动日志同时打印
  "VAE 窗口配置读自哪个文件"。

### 文档
- README 用例数 642 → **756**（中英同步），并把"测试依赖前提"写清：干净 clone 上是
  `skipped` 不是红，机制在 `tests/_env.py`，端点缺件回 503。
- 新增 `tests/test_vae_window_api.py::ActiveConfigResolution`（10 条）、
  `tests/test_server_json_hygiene.py`（13 条），`test_backend_mode.py` +3 条，
  `test_batch_restart.py` +3 条。

## [v10.23.0] - 2026-10-23

### 修复：设置页「VAE 显存优化」开关点下去 404
- 根因：`static/index.html` 从早期版本起就在调 `/api/vae/window`，但后端从未注册过这对路由，
  点下去浏览器控制台是 404、页面弹「切换失败：Not Found」。`tests/test_vae_window.py`
  只覆盖 `scripts/set_vae_window.py` 的命令行行为，不覆盖 API，所以这个缺口一直没被发现。
- `app.py` 新增 `GET/POST /api/vae/window`：
  - GET 报引擎预检同口径的 `decode_core_frames`（`cpp/model/*/sidecars/yue2-vae-config.json`），
    并附各模型目录明细，避免"开关显示 512、其实那份是 1024"；
  - POST 只收 512/1024（UI 两档），补丁语义与 `set_vae_window.py` 一致：改前留
    `*.bak-vae-window` 备份、同值 no-op 不重复备份、临时文件 + `os.replace` 原子写；
    确实发生了改动才调 `_restart_audiocpp()` 重启引擎，同值不重启。
- 新增 `tests/test_vae_window_api.py`（11 条）：读值 / 切换 / 备份 / 同值不重启 /
  非法值 400 / 无 sidecar 404 / 路由真的注册了 / 前后端调用契约。

### 修复：dsh 工作台开箱即坏（缺依赖声明）
- `dsh-plugin/启动AI工作台.bat` 运行 `node node_modules\@deepseek-ai\dsh\lib\bin.js web`，
  但 `dsh-plugin/package.json` 没有声明 `@deepseek-ai/dsh`——全新克隆后 `npm install`
  不会装上它，⑥ 歌词曲风 AI 工作台起不来。补上 `"@deepseek-ai/dsh": "^0.2.1-rc.2"`
  （2026-10-23 实测可正常运行 web 面板并加载 yue2-lab-plugin）。

## [v10.21.9] - 2026-10-22

### 核显 VAE 低显存模式（实测跑通）
- 根因：Yue2 VAE 的解码窗口由 sidecar 的 `decode_core_frames`（官方 1024）+ halo 16 决定；
  16GB 核显上整段会申请约 3.89GB，直接 `OutOfDeviceMemory`。**它与歌曲长短无关**——4373 帧
  和 5556 帧都申请同一个 3.89GB，所以“把歌缩短”并不能解决。
- 把 `decode_core_frames` 降到 512 后：完整 4373 帧歌曲（8 步）在 Vulkan 上 **27 分钟完成**，
  产出 174.9 秒 / 33.6MB WAV；VAE 分 9 个 tile、约 38 秒，未再出现 3.89GB 分配失败。
- 新增 `scripts/set_vae_window.py --frames 512`：修改前自动备份为 `*.bak-vae-window`，
  `--restore` 可恢复官方 1024；窗口越小显存越低、接缝风险越高，改完请完整试听。
- 当前本机 sidecar 已是 512；备份保留了原始 1024。

### 可观测性
- `app.py:_restart_audiocpp` 现在给引擎加 `--log` 并把 stdout/stderr 落到
  `runtime/data/logs/audiocpp_server.log`；以前 app.py 拉起的引擎一条日志都不写，
  任务页的 AR/NAR/VAE 阶段解析和排障都是瞎子。
- 679 条测试全绿。

## [v10.21.8] - 2026-10-22

### 修复（用户实测）
- ⑭ 歌词时间轴从「系统」组移到新的「工具」组，不再挂在设置/关于下面。
- 失败原因（含展开气泡）宽度严格限制在任务列内：收起两行、展开也按列宽换行，不再横穿状态/歌词列。
- 人声伴奏分离：点虚线框选文件后没有反应——`bindDropZone` 只在拖拽时回调，缺少 `change`；补上后点选也会报文件名并自动提交。
- PyMSS 的 `--device` 只接受 `auto/cpu/cuda/mps/mlx`；核显 Vulkan 之前被传成 `vulkan`，argparse 直接退出。现在 Vulkan 映射为 CPU，分离真正可跑（CPU 会慢，页面提示已改）。
- 换声/音色制作的上传框补上文件名反馈（之前点选后看不到任何变化）。
- 实测：6 秒语音在 CPU PyMSS 上分出「人声/伴奏」两份；纯正弦测试音被静音守卫正确拒绝；⑭ 用真实语音生成 LRC（`[00:00.12]...`）与逐字 eLRC 均成功。
- 676 条测试全绿。

## [v10.21.7] - 2026-10-22

### 任务页与设置
- 失败原因不再溢出任务框：改成两行自动换行 + 展开气泡，长度受列宽约束（原来单行绝对定位会横穿状态/歌词列）。
- 「⋯ 更多」靠近列表底部时自动向上展开，避免被滚动容器/视口裁掉。
- Windows 通知补上 `name`/`batch_name` 兜底；批量任务优先用歌名，不再显示任务 ID 或 Style 首词。
- CPU 自动回退改成 iOS 风格滑块开关（不再用不直观的复选框）。
- DeepSeek Key 可在 ⑫ 设置里填写/清除，写到 `secrets/deepseek_api_key.txt`（不入库、不回传前端）；
  `src/ai_lab.py` 在环境变量缺失时读该文件。
- 明暗模式开关从侧栏移入 ⑫ 设置；侧栏只保留服务健康灯。
- 「随机种子」按钮移到 Seed（-1 随机）输入框旁边。

### 新页面 ⑭ 歌词时间轴
- 与现有 13 页并列的独立任务：上传歌曲音频 + 粘贴歌词 → 后台强制对齐 → 下载 LRC / 逐字 eLRC。
- 不依赖 VAE、不占生成队列；任务状态与产物落在 `runtime/data/lrc_jobs/`，页面可查看最近任务。
- 后端新增 `/api/lrc/start|status|list|download/{id}`；一次只跑一个对齐任务，音频上限 300MB。
- 675 条测试全绿（含独立 LRC 任务、DeepSeek 设置、Key 文件忽略、页面契约 14 页）。

## [v10.21.6] - 2026-10-22

### 任务页与设置
- 「⋯ 更多」菜单：点页面空白处自动收起；打开另一行的菜单会先收起旧菜单，不再互相遮挡；
  点菜单里的动作后也自动收起。
- Windows 通知标题统一成「任务名 成功 / 任务名 失败」，不再拿 Style 首词（“English”）
  或固定文案（“人声分离任务”）当任务名。覆盖生成、分离、换声、转谱、音色制作。
- ⑫ 设置新增「GPU 分配失败时自动降级到 CPU」开关，写入 `runtime/data/settings.json`；
  显式设置优先于 `secrets/local_env.bat`，可随时切换，不用改环境变量。
- 设置页新增「本机可用功能」清单：歌词 TXT、LRC/eLRC、转谱、分离、换声/训练、AI 写词
  各自是否可用；不依赖 VAE 的功能一眼可见。

### 歌词文件
- 歌词框和历史任务卡新增「⬇ 歌词 TXT」：纯前端导出，不需要 GPU/VAE，失败任务也能导出。
  LRC/eLRC 仍需音频时间轴（已有音频时可单独对齐，不必重新生成歌曲）。

### 开跑前预检
- 生成任务开始前按歌词/AR 检查点估算语义帧数，再按本机 Vulkan 预算（保守 7.0GB）估算
  VAE 全曲输出 + 权重/图的峰值；明确放不下时**直接失败并说明原因**，不再等到 VAE 才报错。
- 实测：5556 帧长歌在 4 秒内被拦下，且没有启动推理引擎；整首不再白等一小时。
- 若用户显式开启 CPU 回退，预检不硬拦，改为在任务卡上给出 warning，先试 GPU、失败后再切 CPU。

## [v10.21.5] - 2026-10-22

### 新增
- 任务页与生成进度条显示当前阶段：**AR 作曲中 / NAR 第 N/M 步（约）/ VAE 解码中**。
  引擎没有进度回调，网关从引擎日志的 TRACE/TIMING 行解析；NAR 每一步引擎不写日志，
  步数按「帧数 × 步数 × 每帧步毫秒」估算，界面明确标「约」。
- Vulkan 安装时 `scripts/setup_runtime.py` 自动写 `session_options.mem_saver=true`，
  在 AR/NAR/VAE 阶段之间释放暂存权重/图，降低峰值（用重新加载时间换内存）。

### 实测补充
- 5556 帧长歌在 `mem_saver` 下：NAR 图（364MB）成功分配；VAE 的 1040 帧分块解码
  本身成功（8.4 秒），但随后 **3.89GB 全曲输出缓冲**仍分配失败——`mem_saver` 降的是
  NAR 峰值，缩不小 VAE 的全曲输出缓冲。
- NAR 输出没有 artifact：`stop_after` 只支持 abc/semantic/audio，VAE 失败后只能复用
  AR 检查点，NAR 仍要重跑。要“复用 NAR 直接进 VAE”，需要引擎新增 acoustic latent
  导出或 `stop_after=nar`（当前版本没有）。
- 663 条测试全绿。

## [v10.21.4] - 2026-10-22

来源：用户发现队列第一首在 CPU 上跑了近 4 小时，明确要求这台机器不要再回退 CPU。

### 根因（实测）
- `secrets/local_env.bat` 被写成 **LF 换行**。cmd.exe 解析 .bat 时把最后一行注释和
  `set YUE2_DISABLE_CPU_FALLBACK=1` 拼成一条命令，变量从未生效；启动脚本照常往下跑，
  没有任何报错。
- 04:46:49 GPU 侧 VAE 要分配 `Vulkan0 buffer of size 3893381120`（约 3.9GB），当时物理空闲
  只有约 1GB，分配失败；因为开关没生效，任务按旧逻辑切到 CPU，从 04:46 跑到 08:40，
  近 4 小时没有产出（CPU 引擎工作集 8.81GB，物理空闲只剩 0.72GB）。

### 修复
- `local_env.bat` 统一 CRLF；新增 `_local_env_value()`，进程环境没有该变量时**直接读文件**，
  不再依赖"启动脚本一定加载成功"。
- `/api/backend/mode` 增加 `cpu_fallback_allowed`；页面模式行显示「分配失败不回退 CPU」。
- 验证：终止 CPU 任务后物理空闲 0.72GB → 6.23GB；队列以 Vulkan 重新启动，第一首继续用
  5556 token 的 AR 检查点跳过 AR。657 条测试全绿，跑测试不再改写真实 `engine_state`。

## [v10.21.3] - 2026-10-22

来源：用户要求「失败的任务在任务页也能重启、一键重启」，以及 03:22 复验时发现的队列接口卡死。

### 修复
- 任务页失败/取消卡片新增**可见的**「↻ 重启」（队列条目）/「↻ 重生成」（单首）；队列正在跑时
  也允许重启失败条目——只把这一条排回待跑，不打断当前正在算的歌。
- 队列栏新增「↻ 重启失败任务（N）」：只重启**当前队列**的失败/取消条目，历史队列的旧失败不参与。
- 批量条目在第一次派发时把随机种子**写回队列状态**；重试沿用同一种子，AR 检查点的哈希才算得对。
  实测：恢复 01:18 那份 5556 token 检查点，03:25 直接跳过 AR 进入 NAR+VAE；此前每次重试都重抽
  种子，检查点成了永远对不上的孤儿，重试会从头再跑几小时 AR。
- 修复队列 worker 死锁：种子持久化在 `_BATCH_LOCK` 内又调用了自己加锁的函数，03:22 起所有
  `/api/batch/status` 请求挂起（`/api/health` 却正常）。改为锁内纯函数计算，并补一条子进程
  回归用例（worker 卡死时用 join 超时判红，不会把整套测试一起挂住）。
- CPU 回退的 `engine_state` 还原移入 `finally`：终止/异常不再把后端永久留在 `cpu`
  （03:20 网关重启后引擎按 cpu 拉起的根因）。
- 测试：655 条全绿。

## [v10.21.2] - 2026-10-22

来源：用户反馈「31 条队列任务全部失败」。排查结论不是歌太长，而是**引擎进程里残留了上一次
长歌失败时没释放的 GPU 分配**。

### 实测证据
- 02:03 的 31 条任务每条都在 **2 秒内**失败，错误统一是
  `failed to allocate QwenCausalDecodeRuntime decode graph`（AR 图要 516MB 分不出来）。
- 同一时刻系统提交内存 **23.3GB**、空闲物理 1.2GB；**杀掉引擎进程**后立刻回到
  提交 12.7GB、空闲 8.9GB —— 说明那 10.6GB 是引擎进程内残留的 Vulkan 缓冲
  （ggml 分配失败后不释放，进程活着就会被一直占着）。
- 队列按需自检只看"引擎进程在不在"，进程活着就不会重启 → 31 条全部撞同一堵墙。

### 修复
- **分配失败 → 自动重启引擎（清空进程内残留显存）→ 重试一次**；有 AR 检查点时这次重试
  直接跳过 AR。只重试一次，避免「失败→重启→再失败」死循环；CPU 后端不参与此逻辑。
- 仍失败时保持 v10.21.1 的行为：失败卡片「↻ 从检查点继续」或
  `POST /api/generate/resume/{id}`；核显机器上 `YUE2_DISABLE_CPU_FALLBACK=1` 继续生效。

### 验证
- 重启引擎后 Vulkan 冒烟：32 token / 2 步 → **36.3 秒出片**（245,548 字节），
  内存 5.1GB 空闲 / 12.9GB 提交。
- `py312\python.exe -m unittest discover -s tests` → 645 条通过（+引擎重启闸 3 例）。

## [v10.21.1] - 2026-10-21

来源：用户实测「GPU 分配失败后自动切 CPU，两小时还没完；手动切回 GPU 任务又直接失败」。
本版把生成拆成**可恢复的两阶段**，并给自动 CPU 回退装上用户侧开关。

### 生成可以"从失败的地方继续"了（AR 检查点）
- 一次提交拆成两段：**先只跑 AR**（`stop_after=semantic`，拿语义 token + 引擎自己计划的谱），
  落盘到 `runtime/data/gen_checkpoints/<key>.json`；**再渲染 NAR+VAE**。
- 渲染阶段失败（典型：核显显存不够）时检查点保留；**同参数重新提交会自动跳过 AR**。
  实测（Vulkan，96 token）：一次性 193.5 秒；AR 检查点 135.4 秒；从检查点渲染 20 秒。
  整首 4 分钟的歌，AR 占大头——续跑省的就是这一段。
- 失败卡片上直接给「↻ 从检查点继续」按钮（② 页状态行），走 `POST /api/generate/resume/{id}`，
  用原参数重提；无需回填表单。
- 诚实的差异：从检查点渲染的波形与一次性生成**不逐字节相同**（同一 token 流、同一时长、
  同一语义，RNG 流不同）。检查点 7 天过期；成功出一首即删（避免同 seed 重提换波形）。
- 后端不产语义产物时（CPU 后端实测如此）**自动退回一次性生成**，绝不因为建不了检查点把歌弄失败。

### 自动 CPU 回退改为可选（核显机器默认关）
- 新增 `YUE2_DISABLE_CPU_FALLBACK`：置 1 后，GPU 分配失败**不再**整首重算到 CPU，
  而是带着引擎原话失败，并提示检查点是否可续。核显上 CPU 慢 14 倍以上，整首重算不可接受。
- 回退结束会切回**原来的**后端（此前写死 cuda），CPU 回退期间 state 与实际运行一致。

### 修掉实测抓到的两个新缺陷
- 两阶段首请求漏发：先完整生成一遍、失败后再补跑一次（实测 210 秒 vs 101 秒）。
- 语义产物解析只认 dict，实际顶层是数组 → 改为两者都认。

### 验证
- `py312\python.exe -m unittest discover -s tests` → **648 条通过**；新增检查点 6 例
  （键稳定性/种子敏感性/过期清理/两阶段开关/阶段二上下界/无谱退 off）。
- 真机链路：两阶段 89.6 秒（检查点 70 秒出现、渲染 22 秒、成功即删）；恢复分支 20 秒。

## [v10.21.0] - 2026-10-21

来源：用户「把项目部署到这台没有 N 卡的新机器上，补齐功能、测试、优化，然后推送」。
本次改动只动"部署可用性与后端选择"，不改生成算法；13 页 UI 与既有语义保持。

### 纯 clone 现在能真的跑起来
- 新增**开源兼容网关 `main.py`**：顶上闭源 `main.cp312-win_amd64.pyd` 剩下的事
  （`app`/`settings`、`GET /api/health`、`POST /api/music/generate`、导入时拉起引擎）。
  参数白名单运行时读 `cpp/model_specs/yue2.json`，不手抄常量。有 .pyd 时仍优先用它。
  `/api/health` 分三态（ok / busy / down）且**永远 200**：看门狗把 5xx 当死，而生成中
  `/health` 超时是"忙"不是"死"；`gateway` 字段只在引擎 ok/busy 时才是 "ok"。
- 新增根 `requirements.txt`（fastapi 0.115.12 + starlette 0.46.2 成对钉死；最新 FastAPI 会把
  "空 filename 的 file part"当合法上传真入队）、`requirements-transcribe.txt`、
  `server.json.example`、`.gitattributes`、`一键补齐运行件.bat`、
  `scripts/setup_runtime.py`（一键：py312/引擎/权重/转谱/换声/LRC，幂等）、
  `scripts/fetch_models.py`（分块并发下载 + Content-Range 窗口核对 + GGUF/safetensors 结构校验）。

### 用上核显：Vulkan 后端
- 无 N 卡时按 `Vulkan → CPU` 选后端；`app.py` 后端白名单放开 `vulkan`，
  `engine_backends()` 用 `--version` 读构建能力，`resolve_backend()` 把页面上的「切GPU」
  落到真正可用的后端（N 卡→cuda，核显→vulkan，都没有→cpu 并说明）。
- **实测**（i7-1165G7 + Iris Xe、q4_k_m）：同一条请求 CPU 5,610 秒 → Vulkan 318 秒（约 14 倍）；
  3.84 秒片段 582~672 秒 → 25~38 秒（15~27 倍）。输出长度/时长完全一致，RMS 与峰值同档。

### 补齐的可选功能（均可从公开上游自动安装）
- ③④ 转谱：torch 一套 + SheetSage2 权重；修了 `TRANSFORMERS_CACHE` 与 `HF_HUB_CACHE` 不同源
  导致转谱 100% 报"本地缓存不完整"的缺陷；MERT 按 `checkpoints/config.json` 钉的 revision 下载。
- ⑩ 分离/净化：官方 RVC 自带 PyMSS；预下 5 个模型（约 590MB）；`vocal_separation_ready` 实时为真。
- ⑦⑨ 换声/音色制作：克隆官方 RVC（py312 支持）、hubert/rmvpe/预训练权重，
  并应用 `patches/` 里的 fcpe 与 `--filter-radius` 补丁；`sitecustomize` 补
  `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD`（torch 2.6+ 的 `torch.load` 默认值变更）。
- 逐字 LRC/eLRC：funasr fa-zh（中文）+ wav2vec2 CTC（英文）+ faster-whisper 兜底。

### 修掉的真实缺陷（均由三方评审给出复现命令）
- `app.py` 用了从未定义的 `logger`：强制对齐失败的 fallback 本身会 NameError，
  `.lrcjob` 只留一句 `name 'logger' is not defined`，whisper 兜底一次没跑过。
- 出歌期间没有防睡眠声明（只有 RVC 训练有）：机器进新式待机时 AR 解码被冻住过。
- `README`/`requirements` 的测试计数漂移、`.gitattributes` 描述与文件不符、
  两只漏网的 UTF-8 `.bat`、`X-Seed` 因 httpx 头小写化永远读不到、`engine_backends()` 把一次
  探测失败永久缓存成"只有 cpu"、CPU 回退后用写死的 cuda 覆盖原后端等（共 4 条 P0 + 7 条 P1）。

### 验证
- `py312\python.exe -m unittest discover -s tests` → **642 条通过**（本机约 2 分钟）。
- 新用例含变异验证：`request.options` 双层、`finally` 撤销防睡眠、`cuda→vulkan` 落地、
  下载器窗口错位、`TRANSFORMERS_CACHE` 实际生效值、health 三态与"永不 5xx"。
- 三方对抗式评审报告：`docs/review/BLUE-TEAM-REVIEW-v10.17.1-deploy.md` /
  `THIRD-PARTY-REVIEW-v10.17.1-deploy.md` / `ADJUDICATION-v10.17.1-deploy.md`。

## [v10.17.1] - 2026-10-17

来源：用户「继续完成后续开发任务，检查 UI 布局，测试没问题就推新版」——把上一版留下的三条待办做完。

### ⑤ 新增「让引擎先规划一份谱」：`stop_after=abc` 到手，8-15 秒看到模型打算这么写
v0.9.2 的请求侧选项 `stop_after ∈ {abc, semantic, audio}` 一直没接。**必须直连引擎**：
`/api/music/generate` 属编译内核 `main.pyd`，内省它的 `GenerateJSON` 实测只有 23 个字段、
没有 `stop_after`，而 pydantic 默认 `extra=ignore`——从网关那层塞进去会被**静默丢掉**，
界面写着"只出谱"、后端照样渲染整首歌。所以 `POST /api/generate/plan_abc` 自己发
`{model, request:{task:"gen", …, options:{…}}}`（形状是内省 pyd 真实转发体拿到的），
拿 `_GPU_SEM` 排队、同步 def 不碰事件循环。
引擎回的是 `artifacts[0]`：`mime=text/vnd.abc`、base64 谱文、`meta.truncated`。

- **真页面点出来的数**：⑤ 填 style+词 → 点按钮 → **14.9 秒**回一份 39 行、27 处和弦记号的谱，
  写进本页谱框，摘要行立刻接上（D#m · 100 BPM · 带和弦 → 档位自动定为 full）。
  同一件事走完整生成是 60-90 分钟。
- 位置只在 ⑤（按钮在 `#scoreSection` 里，② 看不见）：结果写进**本页**谱框，不是跨页搬运。
  谱框已有内容时先问一句再覆盖；状态话说在按钮下面那条 `.lint` 上——照今早那条教训办。
- `truncated=true`（被 token 上限截断）必须在文案里说出来，不说不等于没发生。

### ③ 转出来一份没音的谱，不再绿灯报"已保存"
`_abc_analyze` 本来就在数音高，只是没说出口——补 `note_count`。判据来自 2026-10-16 的实测，
这次按原条件复现了一遍：**RMS≈0.012 的 4 秒片段，97 秒转出 `Z3|` 一串休止**，`note_count=0`、没有音域。
以前这条走的是成功分支（"✓ 乐谱已保存（走 melody）"），用户会拿一份没音的谱去生成 40 分钟。
现在三处一起改口：状态行说"一个音都没有（只有休止）——多半是这段太轻或太安静"并给出下一步
（换副歌那种人声明显的段落 / 先归一化音量）、摘要行前缀从 ✓ 换成 ⚠、通知条标题改成"转谱没抓到旋律"。

### UI 布局复查（13 页 × 1680，另测 1280 / 1100 / 900）
逐页量：横向溢出 0、越界元素 0、"看得见却没尺寸"的控件 0，底部留白 9-111px；
⑤ 的动作行加了第 4 个按钮后，1280 宽自然换成两行、900 宽单列排——没有截断。
一条如实记下：**⑫ 加了 LoRA 一节之后左栏变高，右栏底部空了一片**。没动它——
这一页的归类规矩是页面自己注释写下的（左＝服务/模型/引擎，右＝外观/维护），
把「引擎模式」挪去右栏能填平空白，但会打散这个分组，属于用视觉换语义，不划算。

### 测试
本标签 **559 项**（v10.17.0 的 542 → 新增 `tests/test_plan_abc.py` 12 条 +
`tests/test_empty_score_hint.py` 5 条；工作树 596 项含别轮未提交那批）。
`test_plan_abc.py` 用假 `httpx.Client` 抓住**真实发出的请求体**验形状：
`stop_after=abc` 必须在、`cot=off` 必须被改写并说出来、`-1` 种子必须先具体化再发、
空字符串的高级参数不许发出去、引擎报错必须原样透出、没谱可回不许装成功、
处理函数必须是同步 def（否则等锁会把网关整站拖死）。**变异验证做过**：把 `stop_after` 摘掉，测试立刻红。
`test_empty_score_hint.py` 锁住"全是休止＝0 个音"和"和弦记号里的字母不许数成音"，
外加两条界面判据：转谱成功分支必须按 `note_count` 分岔、摘要行不许一边绿勾一边警告。


## [v10.17.0] - 2026-10-17

来源：用户「继续完成开发和测试」——把 v0.9.2 内核换来的 LoRA 能力接进工作台。

### ⑫ 新增「LoRA 适配器」一节：挂、卸、调强度、重扫描
后端两个端点（`app.py:1925` 的 `GET /api/models/loras` 与 `app.py:1942` 的 `POST /api/models/lora`）：
前者列 `cpp/lora/*.safetensors` 并按文件名标出它像 AR 还是 NAR 段，后者写 `session_options`
（`yue2.ar_lora` / `yue2.nar_lora` + 两个 `*_lora_scale`）并**重启引擎**——契约是
"改适配器要开新 session"，不重启就是假生效。卸下是**删键**而不是写空串：留 `""` 会让引擎去找
一个空文件名的适配器。
界面放在 ⑫ 而不是 ②⑤：它是引擎级配置，挂上后对之后**每一次**生成都生效，不属于某一首歌的参数。
这条按页面的硬规矩还专门写了断言守着（生成出口里不许出现 lora 字样）。

### 真机验证：不是"接口通了"，是"听出来变了"
同一条极小请求（同种子 777、`cot=full`、固定 token 预算）打引擎三次：
- 裸模型 → SHA256 `b7e8ea5690c02ed0…`，21 秒；
- 挂上 `ar_lora_inst_v3abc.bf16` + `nar_lora_joint_v4.bf16` → SHA256 变成 `d49695a83c8b0dd2…`，
  **首次 295 秒、同 session 第二次 106 秒且两次逐字节一致**（合并权重按 session 缓存，
  `BF16(W + BF16(scale·(B@A)))` 的合并在装载时做）；
- 再卸下 → 回到 `b7e8ea5690c02ed0…`，与最初那份裸模型**逐字节相同**。
所以三件事同时被证实：适配器真的进了生成、同 session 结果可复现、卸下是真卸下。
**代价要说清**：挂 LoRA 后这条探针从 21 秒涨到 106 秒（首次 295 秒）——不是免费的。

### 踩到并修掉的五条
1. **文件下错两次**：`audio-cpp/Yue2-3B-GGUF/lora/` 只有 README/SHA256SUMS/试听，适配器本体不在里面
   ——HF 对不存在的 `resolve` 路径回 `200`（HEAD）而 GET 才吐 `Entry not found`，
   我据此下了两个 15 字节的假文件。改读引擎自带 `docs/models/yue2.md` 后拿到正确出处
   （`Mothersuperior/*` 两个仓的 **unfused** A/B 文件，`_comfyui` 融合版引擎明写不收）。
2. **格式被引擎当面拒绝过**：先拿社区 peft/ComfyUI 形状那对挂上去，生成时报
   `Unsupported yue2.ar_lora tensor: model.layers.0.mlp.down_proj.lora_A
   （use the matching unfused adapter, not ComfyUI weights）`——
   这是好事：说明开关生效、且引擎会在真正用的时候验货。也据此定下一条：
   **工作台不做"格式像不像"的假装校验，一切以引擎回包为准**，UI 文案里写明"格式不对会报错就卸下再试"。
3. `models_loras()` 里 `LORA_DIR.relative_to(ROOT)` 在目录被搬走时会 500（测试沙箱逼出来的），
   已改成退回绝对路径显示。
4. **真页面实测抓出我自己写的死控件**：⑫ 那节按钮下面放了 `#loraStatus`，可消息全走 `statusSay()`
   进了 `#settingsStatus`——那是**右栏「外观」下面**的一行，点「挂载并重启引擎」的人眼睛在左栏底部，
   实测到挂载成功的瞬间那条线还是空的，等于点完没反应。补 `loraSay()` 把话说在按钮旁边
   （挂载/卸下/扫描/回填/报错五条路都写），并加一条断言锁住"这一节不许再用页面级状态条"。
   顺带把回包里的 `**每一次生成**` 改成纯文本——这行字直接进 `.lint`，不过 markdown，星号会原样露出来。
   ⚠ 复测时踩到一个量法坑：第一次跑探针读到的还是**旧 HTML**（Chrome 把 `/` 缓存了），
   消息看着像没修好；加 `?nc=…` 绕开缓存才拿到真结果。以后改 `static/index.html` 做页面实测，
   URL 一律带防缓存参数。
5. **卸下之后下拉框还谎称挂着**：截图上看得很清楚——状态行已经写"回到裸模型"，
   可 AR/NAR 两个下拉框仍停在刚才那两个适配器名上。原因是回填一律"用户当前选择优先"
   （`keepAr || j.ar`），这对「重新扫描」是对的（别抹掉人家正在挑的东西），
   对「刚挂载/刚卸下」是错的。给 `loadLoras()` 加一个 `fromServer`，
   pushLora 成功后走这条：以服务端真值为准，卸下后下拉框回到「— 不挂（裸模型）—」。
   这是本项目「界面显示必须等于真实提交」那条硬规矩的第 5 次命中，同类问题已经不改一处就发版过一次。

### 测试
本标签上 **542 项**（v10.16.4 的 525 → 新增 `tests/test_lora_plumbing.py` 17 条；工作树 579 项）。
17 条覆盖：只列 `.safetensors`、AR/NAR 角色判定（NAR 里含 "ar" 子串必须先判 nar，否则界面标反）、
挂载写键并重启（**去掉重启这条就变红**，已做变异验证）、卸下删键、强度钳制在 0–2 且 0 必须保留、
`..`/绝对路径/非 safetensors/缺文件各自怎么拒、缺文件的报错要说出目录、⑬ 的标签不许信 `engine_state`
的自述、控件必须在 ⑫ 且生成出口不许出现 lora、状态必须写在按钮下方那条线、回包不许带 markdown 星号、
挂载/卸下之后下拉框必须跟着服务端真值走。全程在临时目录里跑，不碰本机 `server.json` 与引擎状态。
计数按规矩在**暂存树**上实测（`git checkout-index` 导出后补上 `py312` 与只读的 `pymss/resources` 两个联接，
否则那 23 条会因为"rvc python 环境缺失"假失败）。



## [v10.16.4] - 2026-10-16

来源：继续执行 1→4 的第 2、4 项。**第 2 项做完了；第 4 项的前提被证伪，如实记下来。**

### 内核从 9-12 那份升到官方 v0.9.2（本机 `cpp/`，不入库，旧件全量备份在 `cpp/_backup_912/`）
替换前不是"直接换"，而是先做**同种子同参数的字节级对照**：同一条极小请求
（`cot=off`、4 步、语义 token 64-160）分别打现役 8080 与新内核 8091——
两边都回 `application/json` 的 `{audio, channels, sample_rate, timing}`，解出的 WAV
**1228588 字节、48kHz 立体声、6.4 秒、SHA256 前缀 `4db85f844fafacab` 完全相同**，
耗时 7.3s → 5.4s。这一步同时否掉了我之前的一条担心：`/api/music/generate` 根本不在
引擎里（两个 exe 里都搜不到这个串），它是 `main.pyd` 挂在网关上的路由，
引擎侧两边都走 `/v1/tasks/run` —— **API 形状没变，我们那层不用改**。
换完再跑一次**完整链路真生成**（② → 网关 → 新内核）：80.2 秒出 5.0MB / 80 秒成品，
`seed_actual` 正常落档，无 CPU 降级，⑬ 不再报 ⚠。

拿到手的新能力（二进制内实测键名命中）：`yue2.ar_lora` x6、`yue2.nar_lora` x6、
`yue2.stop_after` x1（旧内核全为 0）。官方 `model_specs/yue2.json` 给的契约是
session 侧 `ar_lora/nar_lora` + 各自 `*_lora_scale`（默认 1.0，置 0 关闭），
request 侧 `stop_after ∈ {abc, semantic, audio}`——**`stop_after=abc` 就是"只要谱不做音频"**，
③/⑤ 下一步可以直接吃引擎自己规划的 ABC；官方 `audio-cpp/Yue2-3B-GGUF/lora/` 已有
现成 AR+NAR 适配器（rank32/alpha32，附训练指标与试听 wav）。
**LoRA 那条旧裁定的前提因此翻转**：不是"引擎不支持"，是"我们的内核没升级"——现在升了。

### 顺手修掉一条真实污染：跑测试会把本机引擎状态写脏
升完内核后 ⑬ 又冒出 ⚠，追下去发现**写脏它的就是我们自己的测试**：
`TestModelSwitchPath` 重定向了 `MODEL_DIR`、`_read_server_json`、`_write_server_json`、
`_restart_audiocpp`，唯独没重定向 `_ENGINE_STATE`——于是它调 `models_switch` 成功后，
把测试里那个假模型名 `yue2-q4.gguf` 真写进了本机 `runtime/data/engine_state.json`。
这也解释了上一轮"⑬ 报的权重磁盘上不存在"的**来历**：那个不存在的名字就是测试造的。
补上重定向并加一条断言（`test_engine_state_write_stays_inside_the_sandbox`：
切换后必须写进沙箱，且本机那份字节不变）。
**验证**：连跑两次全量（各 562 项 OK），`engine_state.json` 的 SHA1 跑前/后三次相同 `7830bed26126` → 污染止住。
本机的状态文件也已按真实配置改回 `yue2-3b-q4_k_m.gguf`。

### 第 4 项不成立：那批 imatrix 量化不是给我们的引擎用的
原计划是拿 `scragnog/YuE2-GGUF` 的 imatrix 档（`Q4_K_M-imat` 2.43GB、`IQ4_XS-imat` 2.24GB）
和现在的 q4_k_m（2.88GB）做 A/B。**读完上游说明后判定不做**：该仓明写这些文件
"let it run through **HOT-Step's** GGML inference stack"，命名是 `yue2-lm-*` 并另带
`yue2-tok-f16.gguf` / `minted/`，与本引擎要求的 `yue2-3b-*.gguf` + `sidecars/*.{json,tiktoken}`
不是同一套加载约定；而音频质量谁好谁坏最终仍要靠耳朵（本项目一贯如此）。
**没有为了"看起来做了优化"去下 2.4GB 赌一把。**
6GB 卡上真正管用的杠杆这次有了实测数：刚才 `cot=off + 8 步` 用 **80 秒**出了 80 秒成品，
而 `full + 32 步 + 长词` 要 60-90 分钟——**瓶颈是上下文与步数，不是再省 0.4GB 权重**
（上游 Q4_0 长曲峰值 7755 MiB 也印证：权重 2.9GB，其余 ~4.8GB 是运行期上下文）。
要提质量就在 audio.cpp 兼容档里选（`ngquocvinh` 有 q3_k_m 2.45GB…q6_k_m 3.69GB 全梯），
要提速就压步数/token 预算，两条都由用户挑。

### 测试
本标签上 **525 项**（v10.16.3 的 524 → +1 条沙箱断言；工作树同一命令 562 项，差额 37 条仍是别轮
未提交的 `tests/test_abc_edit.py`）。数字按上版立的规矩在暂存树上实测（`git checkout-index` 导出后跑）。


## [v10.16.3] - 2026-10-16

来源：用户「按 1、2、3、4 继续优化」。这一版落的是**第 3 项（转谱库随上游修 bug）**与
**第 1 项的本机配对纠正**；第 2、4 项（内核升级、imatrix 量化）在文末如实记状态。

### 转谱库 SheetSage2 跟到上游 10-12 版：和弦拼写会跟着调走了
`checkpoints/` 里这套 `.py` 是**随仓库入库的上游代码**，本机停在 **9-16**；上游
**10-12 那次提交修的就是「和弦拼写模块本地加载失败」**，而我们的目录里连
`chord_spelling_sheetsage2.py` 这个文件都没有。本次换回 4 个改动模块 + 1 个新模块，
差异极小（15 个文件逐字节相同，只有 4 个有改动 + 1 个新增），改的内容就一件事：
**解码出来的和弦与调名，按当前局部调重新选同音异名的拼法**。

这不是装饰：B 大调里 `Db:maj` 现在会写成 `C#:maj`（降五级在该调里不存在），调名标签也归一
（`C#:major → Db:major`、`Ab:minor → G#:minor`）；**没有调信息时原样保留，绝不猜**。
③ 转出来的 ABC/MIDI/LAB 与 ④ 的和弦记号因此更贴乐理。

**换库前做了 A/B 对照，不是"跑通就算"**：同一段 25 秒音频分别用 9-16 旧代码与新版各转一次，
谱面 **215 字符逐字相同** → 升级无回归；再用一段正常人声（RMS 0.18）跑新版，
得到 `K:D`、带 `"D" "G" "A"` 和弦记号的完整谱（25 秒耗时 101 秒）。
顺带一条实测结论：**音量太轻的片段会转出只有休止符的空谱**（第一段用的分离成品
RMS 只有 0.012，新旧两版都只出 `Z4|`）——这不是 bug，但用户拿干声片段转谱时会以为转谱坏了，
③ 的空态提示值得据此改写（已列入待办，不在本版动）。

新增 `tests/test_sheetsage2_spelling.py` 6 条：模块必须在、导出路径必须真的调用
`correct_chord_rows`（只 import 不用不算）、调名标签必须归一，以及三条拼写判据本身
（含"无调信息不许乱改"与 `N`/`X` 两类空和弦必须原样活着）。这些用例不加载权重、不碰 GPU。

### 本机配对纠正（第 1 项，配置不入库但记录在案）
⑬ 说实话之后露出的那条漂移已纠正：走工作台自己的 `/api/models/switch` 把
`cpp/server.json` 从 `yue2-3b-q8_0.gguf` 切到 `yue2-q4_k_m/yue2-3b-q4_k_m.gguf` + 同目录 vae，
实测回包「已切换到 yue2-3b-q4_k_m.gguf，后端已重启加载」，⑬ 的 ⚠ 消失、`audiocpp_alive=True`、
后端模式仍 cuda。依据是上游自己的实测数：**Q8_0+F16 峰值 8867 MiB、Q4_0+F16 7755 MiB**
（RTX 5090，longform），本机只有 6144 MiB —— 挂 Q8 注定降级或失败。

### 上游出处两条链接是错的（顺手修，已随 3f5617 推）
中文 README 指向 `github.com/leejet/audio.cpp`——**该地址现在 404**，内核仓库已迁到
`github.com/0xShug0/audio.cpp`；英文 README 更离谱，把 audio.cpp 记成了 `ggml-org/ggml`
（那是它依赖的张量库，不是引擎）。两处都改为实测 200 的真地址，并补上官方
GGUF+LoRA 权重仓 `huggingface.co/audio-cpp/Yue2-3B-GGUF`。

### 第 2、4 项的真实状态（不写成已完成）
- **内核升级**：v0.9.2 的 `windows-x64-cuda13.3` 包正在下载（本机 `cpp/` 现有运行库是
  CUDA 13 一套：`cudart64_13.dll`/`cublas64_13.dll`，驱动 616.92，所以选 13.3 而不是 12.4；
  575MB 的 cudart 包**不必下**，运行库已就位）。计划是解到独立目录并排验证：扫新 exe 是否
  含 `yue2.ar_lora`/`yue2.nar_lora`、单独起服务过 `/health`，**不覆盖现有 `cpp/`**，
  通过并经用户同意再切。**本版没有改动引擎。**
- **imatrix 量化**：尚未开始（要先确认 audio.cpp 能不能直接吃 `scragnog` 那批 imatrix 文件）。
  听感 A/B 按老规矩由用户本人验收。

### 测试
本标签上 **524 项**（上一版 518 → +6，全是本次转谱拼写的用例）。工作树同一命令 **561 项**通过。
> **数字口径更正（我又错了一次，这次是当场的错）**：v10.16.2 的日志写"标签上 515 项"，
> 现在把那一版的树用 `git archive` 导出来实测收集到的是 **518** 条。原因是顺序倒了——
> 我先用 `checkout-index` 数了 515，**之后**才补上那 3 条纵深缺陷测试，然后一起提交，
> 于是日志里留的是补测试之前的数。本版起加一条硬规矩：**回归数必须在最终提交（或等价地，
> 标签树 `git archive` 导出）之后实测，写在补测试之前测的数一律作废**。
> 工作树数字仍然虚高 37 条（别轮未提交的 `tests/test_abc_edit.py`），不作发布凭据。

## [v10.16.2] - 2026-10-16

来源：用户问「没有什么能优化的点了吗」。先把证据取全再决定动哪里：
用跨页读取扫描器把 13 个页面的处理函数逐个对了一遍控件归属，**跨页写控件的口子现在是零**
（唯一那条命中是扫描器把下一条语句并进了上一行无花括号的箭头函数，实读代码判定误报），
页面独立性这一类到此收口。剩下的优化落在三处：评审里被降级但便宜的纵深缺陷、
剥离逻辑的边角、以及**你自己机器上的配置漂移**（见最后一条）。

### 收掉评审降级掉的三条（都补了能挡住回归的测试）
- **产物 meta 改成原子写**（B-7）。原来 `_output_write_meta` 直接 `write_text`，崩在落盘中途
  会把已有 meta 截成半截 JSON——⑪ 读不出 `status`，那首其实已经跑完的歌在界面上等于没存在过。
  改成 temp + `os.replace`，失败时清掉孤儿临时文件。同一棵树里 `job.json`/`batch_state` 早就这么写了，这处是漏的。
- **历史记录轮转删除先验落点**（B-8）。`history_add` 超出 `HIST_KEEP` 时按记录里的 `file` 名直接
  `unlink`，而那个名字取自 `history.json`——手工编辑、旧版本或将来某个没消毒的写入口混进一个
  `../x.wav`，轮转就会删到 `records/` 外面。先取 basename 再校验仍在 `HIST_DIR` 内。
- **style 不会被剥成空串**（B-9）。⑤「歌词+style+乐谱」里 style 只写了节奏、而谱带 `Q:`/`M:` 时，
  「节奏以乐谱为准」的剥离会把整句剥空，而"style 必填"闸门在剥离之前就放行了——引擎收到空描述。
  现在剥空就保留原句并在说明里讲清"这条不适用"；顺带修掉剥离后残留的前导逗号
  （它会被当成一个空短语，⑫ 的信息量自检也因此虚报超预算）。
- **留痕补齐到最需要它的那一刻**（B-6/B-12）。队列条目在 pending/running 阶段没带 `cot_note`，
  于是"服务端要改你的档位"这件事恰恰在排队时看不见；⑪ 那条 🛈 又只显 120 字而原文没处看，
  现在悬停给全文，并标「…（悬停看全文）」。

### 需要你拍板的一条：显存与权重的配对又漂了
⑬ 现在会说实话，于是露出这件事：**`cpp/server.json` 指向 `yue2-3b-q8_0.gguf`（4.26GB），
而本机是 6GB 卡**；此刻显存 free 约 960MB，而生成需要的余量是 700MB。
按本机既有的配对记录（`engine_state` 只负责后端模式，加载真源看 server.json），GPU 路线应当用
`cpp/model/yue2-q4_k_m/yue2-3b-q4_k_m.gguf` + 同目录 vae——实测峰值约 4.6GB，留得住余量。
我没有替你改：换权重直接改变出片风格与耗时，是口味决定。在 ⑤/⑫ 点一下即可，
⑬ 那一行的 ⚠ 会跟着消失。**代价说清楚**：继续挂着 Q8，最可能的现象是显存不足自动降级 CPU
（慢 5–10 倍），或者生成中途报"生成失败"而详情为空。

### 测试
本标签上 **518 项**（v10.16.1 的 515 → +3；工作树同一命令 555，差额仍是别轮未提交那 37 条）。
新增三条都不是字符串锁，并且**逐条做过变异验证**：把守卫退回旧写法，三条分别变红——
非原子写在半截落盘时把原 meta 盖坏、轮转删除确实把 `records/` 外那个无辜文件删了、
style 被剥成空串。测试要的是"改坏就报警"，不是"源码里有那行字"。


## [v10.16.1] - 2026-10-16

来源：用户要求「用三方交叉评审的 skill，再分析下这个项目」。本机没装这个名字的 skill，
跑的是本仓库已经沉淀过 6 轮的那套四方流程：**蓝军（敌意审查）→ 第三方独立复核 → 中立裁定 → 整改**。
基线 `v10.16.0`（commit d08c518）。产物全部入库在 `docs/review/`：
`BLUE-TEAM-REVIEW-d08c518-G1/G2/G3.md`、`THIRD-PARTY-REVIEW-d08c518-G1G3.md`、
`THIRD-PARTY-REVIEW-d08c518-G2.md`、`ADJUDICATION-d08c518.md`。

三方一共交了 47 条，裁定人逐条到代码/日志/文件系统复核后：**成立或部分成立 41 条，驳回 2 条，降级 8 条**。

### P0：⑨ 用「服务器素材」换声，成功会被报成失败
`static/index.html:6913` 先 `await fetch("/api/rvc/convert")`，`:6918` 再读 `src_name: f.name`。
走服务器素材时前端没有 `File` 对象，`f` 是 `null` → 抛 TypeError → 被 catch 显示「提交失败」，
**而请求早已入队、GPU 已经开始烧**。用户再点一次就是再烧一次。
改成状态与文件名一律以服务端回包为准（`app.py` 本来就在 job 里回了 `src_name`）。

### P1：网关"假死"其实是被一把线程锁冻住的
`app.py:3908` 的 `rvc_pitch_advice` 是 `async def`，里面 `with _GPU_SEM:`——而这把锁是
`threading.Semaphore`（`app.py:2065`）。`with` 一个线程锁会**按住整个事件循环**：勾上「先做人声分离」
点一次音域体检，如果当时有一首歌在生成，就要等十几到几十分钟，期间 `/api/system/info`、
健康探测、任务轮询全部无人应答。看门狗连 5 次短探 + 90s 深探无回话就判假死并击杀——
`runtime/data/logs/watchdog.log:762` 实证今天 10:09:22 已经杀掉过一次当时带着在跑批量任务的进程。
本项目的红线写着「async def 里禁止做重活」，这条漏在**等锁**上。等锁挪进 `run_in_threadpool`，
排队超过 20 秒把等待时长写进返回文案。另用 ast 扫全部协程，把这条红线变成能挡回归的测试。
（裁定保留：10:09 那一次不全是它的账，同段日志也有"整机被占满"的判据；但缺陷本身成立且不依赖归因。）

### P1：⑬ 报的"当前主模型"在磁盘上不存在
复核时实测：`/api/system/info` 报 `yue2-q4.gguf`，而 `cpp/model` 下只有
`yue2-3b-q8_0.gguf`、`yue2-q4_k_m/yue2-3b-q4_k_m.gguf` 和两份 vae——**没有那个文件**。
`loaded_main` 只在 `models_switch` 里写，可那份 `engine_state.json` 会被「只改 backend」的
读改写整份重写，模型改名/移动后它就永远停在旧值上。
⑬ 改为从配置真源 `server.json` 推导（那才是后端真正加载的那一份）+ 磁盘存在性校验，
两处对不上时**把两个名字都摆出来**并标 ⚠，不再静默取其一；`_engine_state_write` 补锁。
现在这一行显示 `yue2-3b-q8_0.gguf（⚠ 切换记录里写的是 yue2-q4.gguf；以 server.json 为准）`。

### P1：随机种子从来没有被具体化，队列产物不可复现
`seed_actual = headers.get("X-Seed") or payload.get("seed")`——引擎不回这个头，所以它恒等于提交值；
用户选「随机」时提交值是 `-1`，产物 meta 也就是 `-1`。单首那条路其实早就具体化了，
**漏的是批量**（实扫队列产物 69 份全部说不清自己的种子）。
抽出 `_materialize_seed()`，单首与批量共用同一个口径，测试直接断言两个函数都调它。

### P1：⑪ 的「↻ 重启任务」先杀再 404
按钮的渲染条件只看 `status === "running"`，不区分单首还是队列条目；而 `batch_retry`
在队列 running 时一律 409。于是处理器"先终止再重排"，那次 `/api/generate/stop?scope=current`
掐的是**此刻正在算的那一条**，未必是用户点的这一条，重排还照样失败。
改成：按钮只出现在队列条目的 error/cancelled 状态上，文案改「↻ 重新排队这一条」，
不再盲发全局终止——破坏范围必须等于点击意图。

### P1：界面显示 ≠ 真实提交，还有四个出口
- ②⑤ 提交前会过 `styleWithGender()`，它读的是 **① 装配台上的 `#gender`/`#bpm`**（那两个控件
  在 `#tab-style` 里，② 页面上根本没有），把用户自己写的曲风抹掉人声再拼上别的页的选择。
  函数整个删除，②⑤ 交的就是 `#style` 框里那一段。
- 刷新时 `resumeRunningJob` 反写 ① 的 `#gender`；`cfg_scale`/`num_inference_steps` 仍是无条件覆写
  输入框——与同一个函数刚立的"只填空白格"自相矛盾。改成只说明不写：`在跑那首用的是 cfg 2.0 / 32 步
  （你表单里的选择我没有动）`。
- ② 的参数摘要不分页读共用的 `#abc`：明明交空谱，却能显示「按谱走 melody」。改成按页判定，
  与七里香事故同一个方向的谎。
- ① 的「我的预设」保存与回填会带走 ②⑤ 的档位、② 的歌词、③⑤ 的谱。存的时候不再收这三样，
  载入时只动本页；老预设里那三个字段就此作废，按钮文案也改成「只动本页，不碰歌词/档位/乐谱」。
- 手删谱框只擦了显示区，`lastAbcAnalysis` 还留着 → 进 ⑤ 会重放上一首歌的分析、把档位锁死。
  现在谱一清空结论立刻作废。

### 文档更正（评审抓出来的三处不实，已在本文件 v10.16.0 段就地标注）
1. **回归数字**：`534 项通过` 是**脏工作树**的数——同棵树压着别轮未提交的 `tests/test_abc_edit.py`
   （21 → 58 条）。标签 `v10.16.0` 上真实收集 **497** 条，全绿这件事本身成立；上一版 v10.14.1 写的
   519 同理（标签上 482）。本版数字只认暂存树：`git checkout-index` 出暂存树跑 `unittest discover`
   → **Ran 515**（工作树同一命令是 552）。
2. **「真实跑完一首歌验证档位改写留痕」**：没有凭据。实扫 `data/` 下 163 份产物 meta，`cot_note`
   出现 0 次，统一任务视图 184 条里带 note 的 0 条。当时我做的是"重启后读到 `loaded_main` 有值"，
   那验证的是**管线通了**，不是**值是真的**。
3. **「任务条组件不得复活」的契约测试**：那句写在日志里，可那三条断言是在删组件的同一次提交里
   一起删掉的，`tests/` 里 0 命中。本版补回正身（`test_per_page_task_strip_does_not_come_back`）。

### 测试
本标签上 **515 项**（v10.16.0 的 497 → 515，+18 条），全量在工作树跑 `Ran 552 … OK`。
新增里最值钱的三条不是字符串锁：`TestGpuLockNeverBlocksTheEventLoop` 用 **ast 扫协程**——
只要有人在 `async def` 里 `with _GPU_SEM` 或阻塞式 `acquire()` 就红（嵌套的同步函数例外，
它们本来就跑在线程里）；`TestWeightDisplayTellsTruth` 拿临时目录造假 `server.json` 与模型文件，
真调 `_loaded_main_label()` 断言"文件不存在要带 ⚠""两处不一致要把两个名字都给出"；
`TestSeedIsMaterializedOnBothPaths` 断言 `_materialize_seed` 同时出现在单首与批量两个函数里。
蓝军 G3 用 20 组变异测出「四处点名修复都锁住了，但多数是字符串锁」，这个结论直接决定了
本版新增测试的写法。

### 一条方法论
本轮最该被记住的不是那行 `f.name`，而是 **B-1 与「跑完一首验证留痕」是同一种错**：
把"字段有值"当成"值是真的"，把"接口 200"当成"链路在真实产物上留了痕"。
以后凡是写"已实测"，必须落到文件、进程或磁盘这一层，并在报告里留下能一分钟复现的命令。


## [v10.16.0] - 2026-10-16

来源：用户先两次要求「继续优化，在内置浏览器上自己看 UI」，再就 ① Style 设计页给了三条
具体不顺手（左右两栏要各自独立滚动、六要素装配台不要折叠默认全展开、悬停不要把英文撑出来
——「或者你可以显示一个小小的英文，但按钮的大小不要变化」）；随后报了一个功能性硬伤：
「歌词 + Style 生成歌曲」页选了 full，点开始生成后**被默认改成 melody**，成品听着是七里香
的旋律。追加两句方向：「类似的 bug，你逐个页面检查下」＋「每个页面都有任务管理模块，都太
丑了，去掉，只留一个总的任务管理的页面」。

### 七里香那首的根因：跨页 DOM + localStorage 泄漏
② 与 ③⑤ 共用同一块 `#abcBlock`，提交时读的是"此刻挂在树上的那个谱框"；而
`yue2_compose_form` 又把上次在 ③ 转出来的那份谱一起存着。于是 ② 号称"只给曲风和歌词"，
提交里却带着**另一首歌的谱**；后端 `_resolve_cot` 见到非空谱就按谱的形状改写档位，
full → melody，旋律跟着那份谱走。用户听到的不是 bug 的响声，是别人的歌。
修法是提交口只认本页：`abcForSubmit()` 只在 ③ 返回谱内容，②⑤ 恒返回空串
（`static/index.html:5484`）；③ 的"建议档位"也不再写进 ②⑤ 的 `#cot`——每页自己那份选择，
别的页面没有资格替它改。

### 后端替用户改的每一个主意，都要留得下痕迹
`_resolve_cot` / 节奏让步 / 收尾段改写这三处会悄悄换掉提交内容，说明原来只活在前端一条
3.6 秒的 toast 里：任务记录、队列卡、⑪ 全都查不到，事后无从追查"当时为什么换了档"。
现在 `cot_note` 随 `_make_job` 入档、经 `_unified_task` 透传给队列卡、批量跑完写进产物
meta，⑪ 的历史条目直接显示这条说明（`app.py:2553`、`static/index.html:5966`）。
收到无法识别的 cot 值也不再"当 full 算了"，而是把原始值写进说明里。
批量那条路的缺省档位原来是 `off`、单首是 `full`，注释还写着"批量以快为先"——同一份不带
`cot` 的请求，点「生成歌曲」和点「批量」会走成两种模式，缺省统一成 full（`app.py:2980`）。
RVC 换声的 `index_rate / protect / rms_mix_rate` 在 `Form()` 上写死了数值，把参数层评审
过的缺省盖掉了，改成 `None` 让参数层做主（`app.py:5475`）。

### 刷新页面不再盖掉你写过的东西
`resumeRunningJob` 原来无条件把"正在跑的那一首"回填进表单：刷新前正在写的歌词、刚挑好的
档位会被冲掉。现在只填空白的格子，填了哪几格、哪几格保持原样都写进状态行
（`static/index.html:5386`）。随机种子同理——"随机"跑完把实际种子记成这一首的事实，
不再固化成用户长期的选择。

### ① Style 设计页按用户的三条习惯重做
两栏各自独立滚动（`align-items: stretch` + 每栏 `.pane-body` 自己 `overflow-y: auto`），
装配台四格与曲风卡片恒定全展开（`MULTI_LIMIT` / `multiOpen` 那套折叠逻辑整个删掉），
候选词芯片尺寸恒定：中文是可见标签，英文真值只留在 `title`/`aria-label`，DOM 里不再挂
`<i>`。原来悬停把英文从 `display:none` 翻成 `inline`，芯片从 ~60px 撑到 135–175px，
整行换行、下面每格往下跳——"按钮大小不要变化"这条规矩的全部理由就在这。
① 的曲风框与 ②⑤ 的那份拆成两个独立框（`#styleAsm` / `#style`），
① 存进 `yue2_compose_asm`，不再和生成页共用一份文本。

### 窄屏（749px 实测）四处"被 flex 挤到逐字换行"
多选列表框被 `height:36px` 钉成单行、侧栏页签换行、左下版本行、② 的参数摘要行。
收法：`select[multiple]` 给 `height:auto; min-height:120px`，侧栏 `nav` 横向滚动不换行，
`.side-foot` 与 `.brand-name` `white-space:nowrap`，窄屏另起一套表头列宽档位。

### 删掉每页的「本页任务」条（v10.13.0 那个功能我自己收回了）
234 行删除、0 行保留。理由不是"丑"这一条：它把任务卡塞进每个页面底部，实测在 1680×1000
下要滚出屏幕才看见，等于告诉用户"下面还有东西你得滑"；而 ⑪ 本来就是总的任务管理页。
⑪ 未受影响，删前删后都点过。

### ⑬ 看得见当前真正加载的是哪套权重
`models_switch` 现在把主模型、vae、以及 vae 的来源（跟主模型同目录 / 退回公共 vae）写进
`engine_state`，`system_info` 一并报出，前端标签改成「当前主模型 / 当前 vae」。
退回公共 vae 是**配对改变**（主模型 Q4 配公共 f16 vae），出图音质与显存峰值都可能不同，
这件事以前在界面上完全查不到。⑬ 那行 dsh 也不再叫"界面端口"——它不是残留，
局域网那条路（3081 → portproxy → 7863）还挂在它身上。

### 测试
全量 `py312\python.exe -m unittest discover -s tests` → **534 项通过**（v10.14.1 的 519 → 534）。
> **v10.16.1 更正**：这个 534 是**脏工作树**的数字。同一棵树上压着另一轮会话未提交的
> `tests/test_abc_edit.py`（21 → 58 条），标签 `v10.16.0` 上真实收集到的是 **497 条**
> （第三方在补齐环境的 worktree 里跑出 `Ran 497 … OK`，全绿这件事本身成立）。
> 上一版有同一个毛病：v10.14.1 写的 519，标签上真实 482。往后回归数只认标签。
新增 `tests/test_submit_paths_agree.py`（12 条）锁住三件事：两条提交路径的缺省必须一致、
后端每一次改写都要留下可读的说明、刷新回填不许盖掉用户已写过的内容。
页面契约测试补：② 提交永不带谱、③ 永不写生成页的档位、随机种子不得固化、
`#styleBox` 必须恰好两个（拆框后不许退回共用）。
> **v10.16.1 更正**：原写在这一行末尾的"任务条组件不得复活"**不成立**——那三条
> `test_mod_strip_*` 是在删任务条的同一次提交里一起删掉的，`tests/` 里 0 命中。
> 该断言已在 v10.16.1 补回（`tests/test_page_contract.py`）。
13 页逐页真点过一遍。
> **v10.16.1 更正**：原写"并真实跑完一首歌验证档位改写留痕那条链路"**没有凭据**。
> 评审实扫 `data/` 下 163 份产物 meta，`cot_note` 出现 0 次；统一任务视图 184 条里
> 带 note 的 0 条；最新产物 mtime 早于该字段落地的提交时间。我做的是"重启后读
> `/api/system/info` 看到 loaded_main 有值"——那验证的是**管线通了**，不是**值是真的**，
> 更不是留痕生效。留痕的行为级验证在 v10.16.1 补齐（见该版测试一节）。

## [v10.14.1] - 2026-10-14

来源：用户报「打开 `7863/#style` 左下显示"正在连接……"，打开 `7863/` 又显示"引擎已就绪"，
这个逻辑肯定不正常」，并要求「站在用户角度整体测试所有页面的所有功能，然后修复、优化」＋
「UI 做的不好，按 awwards / webby / FWA 的获奖品质反复自查提升」。

**健康灯的根因是我自己上一版留下的**：「本页任务」条里 `modTimer` 漏了 `let` 声明。
进 ①⑥⑧⑪⑫⑬ 这些"本页没有任务类型"的页签时 `modStopTimer()` 抛 ReferenceError，
异常从 `switchTab` 冒出来，把启动脚本剩下的初始化（`refreshHealth` 首刷 + 15s 心跳）
全部跳过。带 hash 落地在 ① 就一直连不上，落地在默认页就一切正常——
所以同一个产品看起来"两套行为"。已用契约测试钉住：任务条用到的每个 `mod*` 名字都必须有声明。

### 这一轮怎么量的（不是凭感觉改）
内置浏览器桥接的视口是 530×477 且不可调，而布局按 `100vh` 计算——窄视口量出来的结论在
1680 宽下不成立。于是改走**无头 Chrome + CDP**：在 1680×1000 真视口里逐页扫描
对比度 / 命中区 / 间距档位 / 卡片底部空洞，并截真图用眼睛过一遍。
另外踩到一个测量陷阱：隐藏页面里 transition 被冻结，切到深色主题后量出来的底色仍是浅色值，
一度误判"深色模式全站文字看不见"。量颜色前必须先注入 `transition:none`。

### 修复
- **无障碍（13 页 × 明暗两套逐一实测，改后不达标项 = 0）**：① 六要素计数原来压着 `--dim`
  实测 4.27:1、示例词 10.5px，提到 `--muted`/`--fs-xs`；「?」帮助点 15px 里塞 10px 的
  `--dim` 一并补到 AA；⑬ 外链命中区 17px → 24px；复选框 8 处各自抄一遍行内 flex、
  命中区都只有 18px，收成 `.check` 一个类（24px 起），勾选色从写死白改成跟着主题层走
  （深色蓝底白勾只有 3.10:1，换 `--panel` 后 5.58:1）。
- **间距落到尺上**：CSS 里同时在用的 gap 档位有 13 档（2,3,4,5,6,7,8,9,10,12,14,18,20），
  而令牌尺只有 4/6/8/12/16/20/24/32。`gap:7px` 被 860 个元素解出来、`10px` 495 个。
  把 5/7/9/10 全部收进 `var(--sp-*)`（差值 ≤2px），主列间距 14 → `--sp-5`。
- **五处"这块还没做完"的空洞各自归位**：③ 转谱结果栏空 308px、① 左栏 229px、
  ⑩ 202/193px、④ 195px。裁定分两路——ABC 谱编辑器与分离记录列表是"越大多看几行越好"
  的东西，让它们吃掉余量（谱一页几十行，多 300px 就是少滚三屏）；① 的曲风框和 ⑩ 的左栏
  没有可生长的东西，改成贴合内容。① 不走"撑框"是承接上一版的实测否决
  （`#style` 被拉到 540px 空框，把整页权重全吸走）：**输入框高度应当表达"这里大概要写多少"，
  不是在栏里分蛋糕**。
- **⑨/⑦ 的上传口不再裸露原生 file 控件**：「选择文件 / 未选择任何文件」是整页唯一
  没被主题接管的零件，换成与 ③⑩ 同一套 `.drop` 虚线区。提交前的"没选输入"改指向虚线区
  ——对 `display:none` 的 input 做 `scrollIntoView`/`focus` 是空操作，用户只会看到一行红字
  却不知道该去哪儿；⑦ 体检那条以前只写字不给焦点，一并统一。
  顺带修掉 `bindDropZone` 一个真 bug：`multiple` 的输入框拖进来 5 段只收第 1 段。
- **删掉 iframe 时代留下的 24 条死聊天样式**（`.ai-layout/.ai-chat/.ai-msg/.ai-body/`
  `.ai-code/.ai-inputrow…`）。判定口径不是"grep 不到引用"就算，而是真的点开一条会话、
  把历史消息渲染出来之后，这些类在 DOM 里计数全为 0；`.ai-sess-item` 是活的，留下。

### 实测记录（真实路径，不是推断）
13 页功能逐页点过：① 搜配方/展开/点卡片填框/复制；② 服务端歌词校验、插骨架、撤销、
表单自动持久化（18 字段快照）；③ 空态与已存谱列表；④ 从乐谱库载入 → 移调 +5 → 存入库 →
③ 列表可见 → ✕ 删除（库数回到原值，无残留）；⑤ 乐谱选择器 8 条；⑥ 原生对话读真实会话历史；
⑦ 空体检给焦点；⑧ 6 个音色点开发体检报告、筛选 6→2→6、重命名行内表单开→取消无副作用；
⑨ 预设/ F0 / 空提交指向；⑩ 空提交给人话；⑫ 模型下拉与显存提示、明暗切换；⑪ 搜索/双维筛选/
加载更多 60→165/播放进播放坞/更多菜单 7 项。**并真实跑完一首歌**
（`20261014_020226_3e35cc86`，526s / 17.39MB，✓成功）。

### 测试
设计系统契约测试 +7 条：间距档位必须在尺上、复选框行必须是 24px 的一个类、勾色不许写死白、
死聊天样式不许回来、短页要么让工作面生长要么贴合内容、原生 file 控件不得裸露、
任务条用到的名字必须有声明（`tests/test_page_contract.py`）。全量 519 项通过。


来源：用户双击 `启动音乐工作台.bat` 后卡在门外——脚本连报
「门票未生效（HTTP 401）… 等待第 1/24 轮」直到第 6 轮还没进去；同时给出方向：
**把整个工程里的 dsh 去掉，只保留音乐工作台，AI 生成歌曲与相关 skill 必须留着**。

根因（实测，不是猜）：上游 openclaw/dsh 升级后把启动日志里的门票**打了码**——
`dsh-plugin/_dsh_web.log` 现在整份只剩一行
`dsh web: http://127.0.0.1:3081/?token=***redacted***`。而工作台有三处靠读这行活着：
启动脚本 `read_dsh_url`、面板鉴权 `ui-panel.mjs: currentTokenUrl()`、`/api/ai/web`（返回带票 URL 给 iframe）。
读到的串带 `*`，鉴权正则不认 → 谁都验不通票，`/api/ai/web` 只能 503。
**这条依赖一断，"3081 是 UI 唯一入口"的旧架构就从便利变成了单点故障**，于是把入口收回网关。

### 变更
- **工作台入口 = `http://127.0.0.1:7863/`**。`settings.py: gateway_serve_ui=True`
  （页面托管这条路本来就在，`app.py: _serve_index`，当初为 dsh 关掉的）。
  前端**一行都不用改**：`LAB_BASE` 按路径前缀自适应，直连 7863 时自动为空，
  `fetch("/api/...")` 直接命中网关；`static/index.html` 是自包含单文件，不需要 MIME 表/静态挂载。
- **⑥ AI 工作台改原生对话**：`#aiFrame`（iframe 借 dsh 界面）整块删掉，换成消息流 + 输入框 +
  产出卡 + 确认条（`chatLog/chatInput/chatSend/chatConfirm`）。对话跑在网关自己的
  `ai_lab.chat_turn`（DeepSeek + 9 个工具，skill 由 `ai_prompt` 从仓库外的
  `E:/AI/10AIMusic/Yue/YuE2-skills` 读入），`POST /api/ai/chat` 不再 spawn node。
  产出卡只给「📋 复制 Style / 复制 歌词」——延续 v10.12.0 的规矩，页面之间不代填。
- **`/api/ai/status` 的 `via` 从 `"dsh"` 改成 `"gateway:ai_lab.chat_turn"`**：对话在哪儿跑就报哪儿，
  旧值现在是假话。
- **启动脚本不再求票**：`:dshverify` / `:read_dsh_url` 两个子程序整体删除，`:dshwait` 直接放行，
  `:open` 改为实测 `http://127.0.0.1:7863/` 拿到 200 再开浏览器（拿不到就说清楚并给出手动地址）。
  `[3/3]` 那步仍会起 dsh，但改口径说明它是"局域网兼容用"，不再是入口。

### 修复（写测试时当场抓出来的）
- **AI 会话 id 不回传**：`/api/ai/chat` 回的是自己算的 sid，而新建会话的真 id 在
  `chat_turn` 的返回里——前端拿不到，于是**每发一句就开一条新会话，历史当场丢**。
  现以 `chat_turn` 返回的 id 为准（`tests/test_ai_native.py` 锁住）。
- 空消息只允许出现在「确认执行上一轮挂起的删除操作」这一种情况；其余仍 400。
- 异常回包要顺带说清"会话有没有被写坏"（`chat_turn` 拿到最终回复才落盘），否则用户不敢重发。

### 诚实声明（还没做完的部分）
- **dsh 只是不再是入口，没删**：`dsh-plugin/` 仍在、看门狗仍守 3081、
  「开放局域网」那套 portproxy/防火墙仍指向 3081。
- **⑥ 的原生对话是最小版**：不流式、不渲染工具调用过程（只看最终回复与产出卡）、
  不显示 token 用量。`/api/ai/web` 也还在（会 503），等阶段四连 `dsh-plugin` 一起清。
- **LAN 的安全口径要先补再开门**：网关直出页面本身没加 dsh 那套 token/cookie 门，
  但 Windows 防火墙只放行了 3081、7863 没有入站规则，所以**外部机器现在到不了 7863**，
  不构成新暴露。真要经 7863 开放局域网，必须先把门票/ Cookie 门搬进网关（阶段三），
  否则就是评审里那条 P0：零鉴权驱动 GPU 与删除接口。

### 测试与实测
- 512 条全绿（新增 `tests/test_ai_native.py` 6 条：不 spawn node、确认可以为空消息、
  异常话术、`via` 不许再报 dsh、⑥ 不许残留 iframe、启动脚本不许再读 `_dsh_web.log`）。
  测试要挂进真 FastAPI 应用才测得到 400/502——裸 `APIRouter` 没有异常处理栈。
- 真机：网关重启 59 秒拉起；`http://127.0.0.1:7863/` 直出整页（13 个页签、健康灯 ok/cuda、
  每页任务条 28/8/125 条、⑨ 服务器素材 31 项）；`/api/ai/chat` 真跑一轮 2 秒回话、
  会话落盘 `['user','assistant']`；界面里发一句"好了"，气泡与状态都正常；
  `/` 实测 200 后启动脚本才弹浏览器。

## [v10.13.0] - 2026-10-12

来源：用户的两条观察——"做歌曲的流程不是一个单程线性的过程，需要在多个页面之间反复切换"，
以及"每个功能都能做成一个独立模块……每个任务都能在每个页签上分别管理，在任务管理页面上也有统一的管理"。

规则：**统一视图只有一个数据源（`/api/tasks/unified`），但每一页都要能看见并处理自己那一类任务**。
实现沿用"一条组件按页搬家"的既有手法——全站只有一个 `#modTasks` 节点，`switchTab` 把它搬进当前页
底部的插槽，按该页关心的 kind 过滤；不复制 13 份，也不读任何别的页面的控件。

### 新增
- **每页的「本页任务」条**（`static/index.html: MOD_KINDS / modEnter / modLoad / modPaint`）：
  ②⑤ 歌曲生成、③④ 乐谱提取、⑦ 音色制作、⑨ 换声、⑩ 人声分离各有自己的列表；
  ①⑥⑧⑪⑫⑬ 显式声明为空（⑥ 的会话与 ⑧ 的音色不是任务，⑪ 本来就是全局视图）。
  行内可做的操作只对**后端真有端点**的：`▶ 播放`/`⬇ 下载`（三类各自的产物地址）、
  `⏹ 停这一条`（`/api/generate/stop?scope=current`，v10.11.0 拆出来的那种"只掐这一条"）、
  `✕ 取消`（换声队列里**还没开跑**的条目，`/api/rvc/cancel/{rid}`）。
  正在算的换声不给取消按钮，只在行里写清为什么（掐了只剩半份文件）。
- 有在算/排队的才继续 8 秒轮询，全部停了就自己松手；没得看的时候折叠成一行标题。
- 点任务名复制任务 ID（服务端真源，就是落盘文件名）。

### 修复
- **kind 的名字是个真陷阱**：统一接口里换声类目叫 `rvc`，第一版按 `convert` 过滤，结果 ⑨ 的
  任务条永远是空的——看起来像"这功能没数据"，其实是筛选条件对不上。已加契约测试
  `test_mod_strip_kinds_match_the_backend`：前端写的 kind 必须是后端真会发出来的那些，
  并且 13 个页签在过滤表里都要有显式归属（漏一个就红）。

### 实测（真网关、真任务）
⑨ 出 28 条换声（行内 ▶/⬇）、③ 出 8 条乐谱、② 显示 125 条生成；①⑧ 自动隐藏；
切页时组件跟着搬（`host` 逐页核对为当前面板）。控制台除网关重启窗口与引擎未起时的
502 之外无其他报错。测试 506 条全绿（本轮新增 2 条契约）。

### 顺带交代两件过程里的事
- 为验 v10.11.1/v10.12.0 的后端那半，在本机队列跑空后重启了 7863（看门狗 59 秒拉起），
  冒烟四条全过：① 提交没有收尾段的歌词 → 存盘 143 字、末行是用户自己那句、`cot_note` 说了已补
  `[Outro]`（**这就是用户报的那个 bug，现在从根上不再发生**）；② 带说明行的歌词过校验（`ok:true`）；
  ③ 服务器素材的拒绝面：`../../etc/passwd`、`..`、`a/b`、含空格、不存在的 rid → 404 且给出两种
  合法写法，非法产物名 → 400；④ `source_ids` 走 `/api/rvc/train/check` → 真复制了本机 output/ 里
  那 264.8 秒的成品、报告写明"来自本机 output/（复制，不是上传）"，测试作业随后删除干净。
- **那记冒烟里的「⏹ 停这一条」把正在算的 audio.cpp 一起带走了**，而健康检查不会自动拉引擎：
  页面随即显示 502「audio.cpp unreachable」。已按网关自己的启动方式把引擎拉回（隐藏窗口、
  同一份 `cpp/server.json`，3 秒后就绪），未重启网关、未影响任何已完成产物。
  这是一条值得记住的口径：**终止在算的生成 = 引擎进程被杀，下次提交靠 `_ensure_backend` 自拉**。

### 生效状态
`static/index.html` 与 `tests/` 的部分刷新页面即生效；本条不涉后端改动。
v10.11.1 / v10.12.0 的后端那半**已随本次重启生效并冒烟通过**，两个附注标签补打在对应提交上。

## [v10.12.0] - 2026-10-12

来源：用户对产品结构的一条定性要求——"这 10 来个页面最好做到独立，每个页面拿出来都可以单独使用；
把所有类似'把 A 界面的歌词带到 B 界面'的功能都去掉，有这种需求直接 Ctrl+C 就可以了，
**不要自动帮客户去填充**"。

规则（`static/index.html` 顶部注释与 `tests/test_page_contract.py::PageIndependence` 都在锁它）：
**任何页面的代码都不许写另一个页面的控件**。每页的输入只许来自三处——用户当场填的、
本机服务器上已有的库（乐谱库 / 音色库 / 历史 / 分离产物）、本页自己的运行状态。
②⑤ 共用一份表单 DOM、③⑤ 共用一个谱框（`switchTab` 用 `moveInto` 搬节点）不算搬运：
那是同一个控件换了个位置，不存在第二份会漂移的副本。

### 删除（跨页代填）
- **⑪ 点一行就悄悄改写 ② 的六个控件**（style/歌词/档位/ABC/任务名/性别，外加 seed/cfg/steps），
  而且**不切页**——用户看不见自己面前的表单什么时候被人动过。这是最隐蔽的一处，整个 `restore()`
  删掉；播放路径上那五处 `restore(false)` 一并删。行点击现在只做播放。
- **⑪「↩ 回填创作」** → 「📋 复制 Style」「📋 复制 歌词」（`copyOut`，走剪贴板带 execCommand 兜底）。
- **⑥ AI 工作台「→① / →②」自动填充并切页** → 「📋 复制 Style / 复制 歌词」；
  本页的「🔍 校验歌词」保留（校验的是这一页自己的预览框）。
- **⑩ 产物行的「→⑨ 换声 / →⑦ 训练素材 / →③ 转谱」** → 只剩试听与下载；连带删掉
  `handoffAudio` / `pullServerAudioInto` / `pickSepSource` / `sepSourceOptions` 四个函数
  与 ⑨⑦ 两处的「从 ⑩ 导入」按钮（它们还会顺手取消别的页的勾选、把净化档位改掉——全是代决定）。
- **④「↩ 用 ③ 当前的谱」（读别的页的谱框）与「→ ⑤ 用这个谱生成」（写别的页的谱框并切页）** →
  ④ 自己的「📁 从乐谱库载入」「📋 粘贴」进，「💾 存入乐谱库」出，⑤ 自己从库里挑。
- **训练卡与音色卡的「去使用」**（替 ⑨ 选中音色下拉）→ 「📋 复制音色名」；
  ⑧ 音色分析卡的「→ ⑨ 用这个音色换声」同删。
- **LabStore 整个删除**。实测它写的字段几乎没人读：`style`/`lyrics`/`params`/`asm`/`from`/`ts`
  全是死写，唯一真实的用途是拿"别的页留下的 scoreId"去预选 ⑤ 的乐谱下拉——那正是"替用户做选择"。
  ③ 的导出改用一个页面私有变量 `scoreCurrentId`。

### 新增（每页自己向服务器取输入）
- **⑨ 换声页有「服务器素材」下拉**（成品歌 + ⑩ 的三种产物），⑦ 音色制作有**多选**版。
  提交只传标识：`<rid>` 或 `<rid>|<part>`；文件由服务端自己取，浏览器不再把几十 MB 音频
  下载一遍再上传回去。选了纯人声却还勾着分离时，界面把"会白等约 1.5 分钟"**说清楚**，
  但不替你取消勾选。
- 后端三个入口接受服务器素材（`app.py`）：`POST /api/rvc/convert` 加 `source_id`（5405）、
  `POST /api/rvc/train/check` 加 `source_ids`（7908）、`POST /api/rvc/train` 加 `source_ids`（7977，
  优先级 `dataset_id` > `source_ids` > `files`）。解析与校验集中在 `_server_audio_path`（1930）：
  rid 先 `basename` 再要求匹配 `[0-9A-Za-z_-]{1,80}`、`|` 最多一个、part 严格属于
  `{vocals, vocals_noharmony, accompaniment}`、落点 parent 必须就是 `OUTPUT_DIR`、
  `kind=="rvc"` 的产物不能当源——**任何一种不合格都回 4xx 且一个文件都不落盘**。
  复制走 `run_in_threadpool`（`.part` → `os.replace` 原子落地），体积闸门与上传同一套口径
  （复制的字节也计入总量，不许绕过）。
- 顺手修掉一处既有缺陷：`/api/rvc/train` 的 files 分支里 `job.setdefault(...)` 写在 `job` 创建之前，
  传 m4a 上传会直接 500（`NameError`）。

### 行为变化（必须说明）
从 ⑪ 或 ② 发起换声，**以前会去读 ⑨ 页上当前的十个滑杆**，现在改用服务端为该音色推荐的官方档
（`convertServerSong`，参数来自 `/api/rvc/models` 的 `items[].recommend`）。同一个滑杆在两个入口
含义不同、以及"一个页面把另一个页面的界面当隐式参数源"，都是这次要消灭的东西；
要自己细调检索强度/protect/去和声/静音门，请到 ⑨ 歌曲换声页提交。

### 测试
- 504 条全绿。新增 `tests/test_server_sources.py` 31 条（拒绝面逐条断言"目录树零变化"、
  `source_id` 与 multipart 上传逐字节等价、混用编号 000 上传 / 001 复制、413 总量闸门不留半成品、
  复制必须在事件循环外）；`tests/test_page_contract.py` 新增 `PageIndependence` 6 条
  （被删的名字一旦回到代码里就红、独立性所需的复制/选择器必须在位、⑩ 不许长出去向按钮、
  `convertServerSong` 不许出现 ⑨ 的滑杆 id、进 ⑨/⑦ 必须刷新自己的列表）。

### 生效状态
`app.py` 那半**要重启 7863 才生效**；提交时用户的「如诗」链仍在跑（1 条在算 + 数条待跑），
不在别人长跑中间击杀，所以本次只提交代码与测试，tag 等重启冒烟后补。
前端那半不用重启（3081 每次请求读盘），已实测：13 个页签逐个切换正常、控制台零报错零警告，
⑨ 的素材下拉出 32 项、⑦ 的多选 30 项、⑪ 的更多菜单变成「送去换声 / LRC / eLRC / 复制 Style /
复制 歌词 / 改名 / 删除」、⑥ 与 ⑧ 的复制按钮到位、⑪ 的换声对话框只问音色与变调（6 个音色）。

## [v10.11.1] - 2026-10-12

来源：用户实测（在 ⑤「歌词+Style+乐谱生成歌曲」页）——"把歌词从任务管理回填到生成页，
我的歌词后面被明文加了一句 `[Outro]（器乐渐弱收尾）`，再点生成就报错
「[Outro] 行数 1 不在 4-8 行范围」"。998a99d 那道防尾声幻觉说话声的防线，
把自己的输出喂给了不认它的校验器。

规则：**同一句话不能一边由系统写、一边由系统判错**。三条真源各归各位——
`_ensure_song_ending` 只在送引擎之前追加（引擎的 AR 规划器需要"这里收"的信号）；
存盘/历史/LRC 对齐/回填只认用户自己写的那份词；`_validate_lyrics_impl` 必须认得
生成链自己的输出。前端再把四个出口收干净，于是不用重启网关，那 12 条已经存脏的记录
当场就能复用。

### 修复
- **自动补的 `[Outro]` 不再混进存盘记录**（`app.py:_gen_run`）：追加挪到送引擎的最后一刻，
  复制一份 `engine_payload` 再补；`generate_start` 与批量提交只取提示语、不改 payload，
  单首与批量两条入口共用这个唯一出口。
- **校验器认得纯器乐收尾**（`src/ai_tools.py`）：`[Outro]`/`[End]` 整段只留标签、或只留
  括号内的器乐/演唱说明时合法；真有唱词时才按 4-8 行要求，且不把说明行计入行数。
  以前这条逼着 AI 工作台为过校验编满 4 行 filler，也逼着用户手写四行才能收尾。
- **前端四个出口都摘掉机器话**（`static/index.html: stripMachineOutro`）：结构校验、
  生成、入队、以及从 localStorage 恢复表单——校验与提交的对象从此都是"真正发出去的那份词"。
  脏的 localStorage 副本在恢复时被洗净、下一次自动保存就地自愈。
- **顺带断掉一个隐病**：那句说明行不在 `src/lrc_align.py` 的结构行名单里，以前会被当成
  正要唱的词去强制对齐——把 8 个从没发声的字压到渐弱的器乐尾上。存盘不再带它，这条链就断了。

### 实测
- 真实脏记录 `20261010_010042_eb9765df`：488 字→469 字，末行「故事就到这里」完好；
  「🔍 检查结构」只剩两条 warn、不再报错；截住 `fetch` 看真正发出的
  `/api/generate/start` body——469 字、无机器话。**这些全部跑在还没重启的旧网关上**，
  所以用户当下就能用。
- `tests/test_lyrics_ending.py` 新增 7 例，把"存盘那份 / 引擎那份 / 校验认不认"三个方向
  各锁一次；全量 466 条全绿。

### 生效状态（本项目规矩：代码已改 ≠ 已生效）
- 前端那半**已生效**（3081 每次请求读盘，刷新即得），已按上面实测验证。
- `app.py` / `src/ai_tools.py` 那半**要重启 7863 才生效**。本次提交时用户有一条
  「如诗 流行 女声 +5」正在算、后面还排着任务，不在别人长跑中间击杀，
  所以**暂不打 tag**：等队列跑空重启，并按冒烟三件复验（新提交的记录不含那句、
  带说明行的歌词校验为 ok、⑪ 回填能直接生成），验完再补 `v10.11.1` 附注标签。

## [v10.11.0] - 2026-10-11

来源：用户实测报告——"我在任务管理里点了取消/终止，结果所有任务都停了，后端也离线"。
一次点击同时暴露三件事，逐条落在一处。

### 修复
- **「终止」不再摧毁整条批量队列**。旧实现里"只停当前这一条"与"停整条队列"共用一个
  `_CANCEL_EVENT`，于是在 ⑪ 任务管理点掉一首，队列里另外 5 条（本机每条约 99 分钟）被
  一并标成 `cancelled`——一次点击丢掉半天。现在 `/api/generate/stop` 带 `scope`：
  `current`（默认；创作页「终止任务」与历史卡的 ✕）只掐正在算的这一条/这一首（连发的
  后续 take 一起停），未开始的条目一个不动，worker 接着跑下一条；`queue` 才是原来的全停。
  回执说实话：「已终止当前正在算的这一条；批量队列不受影响，还有 5 条待跑，会继续执行」。
- **网关重启后的批量队列自恢复真正生效**。启动块以前先 `running=False` 再拉 worker，而
  worker 的第一句就是"running 为假就退出"——所谓自动续跑从来没真的跑起来过（实测：用户
  09:25 提交的一条在重启后一直挂着"待跑"，看着就像"无法加载新任务"）。现在抽出
  `_batch_boot_resume()`：有待跑条目就把标志立起来再接 worker；复活计数与 `/batch/status`
  的自愈同一把尺（`_REVIVE_WINDOW_SEC` / `_REVIVE_MAX`），条目自己就是崩溃诱因时判死并写明
  原因，不给"启动→复活→再崩→看门狗再拉回"的无限陪葬留口子。
- **引擎自己占着的显存不再被当成"显存不足"**。生成前预检按空闲显存判断，而 6GB 卡上模型
  常驻约 4.4GB——看门狗重启网关、旧引擎进程还活着的那一刻只测得"空闲 245MB"，于是整条
  队列被降级到 CPU（一首 99 分钟变约 10 小时）。新增 `_audiocpp_cuda_ready()`：引擎活着、
  跑在 cuda 且已加载权重时跳过空闲预检；真不足（引擎没起来/仍在 CPU）照旧降级。
- **空闲时点终止不再无谓强杀引擎**：`taskkill /IM audiocpp_server.exe /F` 会打断正在加载
  的模型，也是把引擎推进异常状态的入口；没有推理在跑时就别动它。

### 新增
- ⑪ 任务管理补上「⏹ 停止队列」按钮。后端 `/api/batch/stop` 早就存在，页面从来没接——
  于是"停这一条"和"停整个队列"在界面上只剩一个核弹按钮。现在两个动作分开：停止队列只让
  下一条不再开始，正在算的这条会跑完，未开始的可用「▶ 一键继续」全部复位。

### 现场经过（同一时段的因果链留档，免得下次又把硬件故障记在某个按钮头上）
09:02:51 点终止 → 整条队列被标 cancelled + 强杀引擎；同一秒系统日志出现 `nvlddmkm 153`
显卡驱动故障（14 小时内第二次，另一次 10-10 20:30）→ 网关 6 分钟不回话 → 看门狗按"长任务
6 分钟无产出"判假死、击杀重启（这一步判定是对的）→ 内存里的换声队列清零，面板那一刻显示
后端离线。09:45 重启后队列自动接回，但旧引擎仍占显存使预检误判、把任务降级到 CPU；手工切回
CUDA 后 6 条按序在 GPU 上重跑。

### 测试
全套 **459 例全绿**（+12）。新增 `TestStopScope` 10 例锁：`current` 不动任何未开始条目且
`running` 保持、`queue` 保留旧的全停范围、空闲终止不动引擎不留残余标志、非法 scope 400、
worker 在只停一条后把下一条接着跑完、启动自恢复四种结局（有待跑就立标志接 worker / 卡死条目
复位计数 / 崩溃循环判死不拉 worker / 已完成队列不打扰）；`TestVramPrecheckGuard` 2 例锁
"引擎已在 GPU 载好权重"与"真显存不足"两条分岔。真机实测见上节。

## [v10.9.2] - 2026-10-09

来源：#2 三方对抗审计修复落地 + 用户实测反馈的三个问题（更多菜单被遮挡、节奏双源冲突、尾声幻觉说话声）。同日第三版，按日期版规则为 `10.9.2`。

### 修复
- **审计 B-T1（P1）**：`tool_generate` 读恒为 `None` 的 `r["job"]["id"]` 必崩（TypeError），
  MCP 与 dsh bridge 两条 AI 生成链路 100% 失败。改为从真实任务登记处（`_gen_get_job()`，
  兜底 `/generate/current`）取任务 id；提交成功但 id 未登记时给可读提示而非异常。
- **审计 P-1（P2）**：`/history/{rid}/audio` 与 `/generate/audio/{rid}` 改 `FileResponse`
  （流式 + 自带 Range），LAN 多人并发听歌不再整文件读内存，顺带支持进度条拖动。
- **审计 S-3**：`history_clear` / `history_delete` 对 `file` 字段加 `os.path.basename` +
  resolve containment 校验——history.json 被篡改也删不出 HIST_DIR。
- **审计 B-T3**：dsh token 读取后立即把 `_dsh_web.log` 里的明文抹成 `***redacted***`。
- **审计 B-T6**：dsh CLI 不支持 stdin/文件传 task（已查证 bin.js），按 `/api/ai/chat`
  拼装后的 task 做前置校验（>30000 字符 400 + 可读文案），不再伪装成 502"dsh 内核失败"。
- **审计 C-T2**：`/generate/list` 支持 `?limit=`（1..1000），队列 5s 轮询只取头部 120 条。
- **「⋯ 更多」菜单被后续任务行遮挡**：`.hist-item:hover` 的 `translateY(-1px)` 在悬停时
  给每行创建独立堆叠上下文，菜单的 `z-index:30` 只在行内生效。用 `:has()` 把展开菜单
  （`data-more-menu` / `data-dl-menu`）的行整体抬到 `z-index:40`。
- **尾声幻觉说话/独白声**：用户实测两首（含同 seed 一首共三首）歌词都止于 `[Chorus 2]`
  正文，无收尾段标签——AR 规划器不知道歌在哪结束，尾段自由发挥把训练数据里的口播/独白
  幻觉进来。新增 `_ensure_song_ending`：单首与批量提交统一扫描最后一个结构标签，
  缺收尾自动补 `[Outro]（器乐渐弱收尾）` 并写进 cot_note；已有 `[Outro]`/`[End]` 不动。

### 变更
- **⑤ 页节奏冲突以乐谱为准**：style 与乐谱同时给了节奏信息时（style 写 `88 BPM`/`4/4`，
  乐谱有 `Q:`/`M:`），两份条件会让生成漂在两者之间。`_style_yield_to_abc` 提交前从 style
  剥离与乐谱冲突的 BPM/拍号（不冲突的原样保留），调整说明并入 cot_note 提示用户。
- **审计 B-T7**：新增 `YUE2_AUTO_RESUME=0` 环境变量，可关闭启动自动续跑训练（默认行为不变）。

### 诚实边界
- 审计 S-1 / R-1 / B-T5 维持现状（三道闸与免死窗口已有设计，dsh 审批 UI 行为需实测）；
  U-1（硬编码色收敛）与 F-1（冒烟测试层）为渐进项，纳入后续批次。
- `[Outro]` 补写是**防幻觉的下限措施**，不保证 100% 消除尾声伪影；若要人声淡出，
  在歌词末尾自写 `[Outro]` 衬词段，网关原样保留不覆盖。


## [v10.9.1] - 2026-10-09

来源：用户对 3081 门户的第三轮品质要求——"**「任务管理」的UI还是太过乱了，作为用户，不便于浏览、筛选。你给我优化。
「Style / 曲风描述」的输入框，太高了，没有必要；「歌词」的输入框又太矮了，不便滚动看歌词。
整个 3081 端口的页面你再好好打磨，主要是布局，元素间的对齐和间隔**"。
同日出第二版，按日期版规则为 `10.9.1`。本版是**纯打磨**：不加功能，只把布局、对齐、间隔收敛到可量化的标准上。

### 修复（三个实测出来的真缺陷）
- **`#tab-ailab` 的 `calc(100vh - 190px)` 魔法数算错 26px**。该页真实 chrome 开销是
  `main 顶距 20 + 页头外框 + 上方 .sec 的 24/12 上下 margin + 播放坞让位 62 = 216px`，
  写 190 导致整页 **1200px > 视口 1174px** —— 13 个页签里**只有这一页**长出滚动条；
  又因全站已启用 `scrollbar-gutter: stable`，别的页不跳、只有它跳，反而最扎眼。
  改为**不再手算**：`#tab-ailab` 变成 flex 列 + 定高，`.ailab-grid { flex: 1 }` 自己吃掉剩余高度，
  加减任何一段 chrome 都不需要回来改算式。实测 frame 高 984 → 938.7，页面溢出归零。
- **`.pane-foot .hint-xs` 的无声截断**。原写法 `-webkit-line-clamp: 1; overflow: hidden`，
  **既没有省略号、也没有 title** —— ① 页那条 38px 的文案被压进 19px 的盒子，
  后半句"那两页也各自留了曲风输入框……"**整段消失，用户只看到半句话**。
  改为单行 `text-overflow: ellipsis`（留下可见的截断痕迹），并新增 `syncFootHintTitles()`
  在**确实被截断时**把完整文案挂到 `title` 上（窗口/分栏变化时重算）。
- **`.preset-row` 行首标签与后缀芯片不同行盒**。`align-items: center` 下 19px 的行内
  `<span>` 与 28px 的 `.btn-sm` 是"各自居中"，实测两段文字**中线差 4~5px**。
  新增 `.row-label`（inline-flex + 固定 28px 行盒 + 文字垂直居中），与芯片同档高度、中线天然重合。

### 变更（⑪ 任务管理的浏览与筛选）
- **任务行统一到五列骨架**：任务 / 状态 / 歌词·摘要 / 时长·耗时·大小 / 操作，
  列宽走令牌（`--hcol-task` / `--hcol-state` / `--hcol-lyric` / `--hcol-meta` / `--hcol-act`）。
  此前 `lightTaskRow()` 自建三列、还把 4 颗按钮塞进同一个 `.h-col`（块级 ⇒ 竖排），
  于是乐谱行高 **168px**、其余 72px，整张表的行高在两种值之间跳。
  根因是"CSS grid 的轨道按顺序填充" —— 少一个子元素，后续列整体左移一档。
  现在**每种行都必须发出恰好 5 个子元素**，行高收敛为唯一值 72px。
- **操作簇容量规则**：簇宽 236px、`gap` 6px、`flex-wrap: nowrap`，
  N 颗按钮需满足 `N × min-width + (N−1) × 6 ≤ 236`；4 颗 ⇒ `min-width` 上限 54.5，
  实取 **52px**（58 会 4×58+18=250 > 236，直接溢出到下一列）。乐谱行按钮文案收短为
  `↩ 载入 / MIDI / ABC / 删除`，完整含义移入 `title`。
- **「加载更多」页脚不再复用 `.hist-item`**（复用会继承表格骨架、多出一种行高）→ 独立 `.hist-more`。
- 「歌词 / 摘要」列头改名：乐谱行放 ABC 预览、分离行放音轨清单，`首句歌词` 已不准确。

### 变更（输入框高度 · 关于页 · 全站对齐）
- **② 的 `#style` 从 540px 收到 78px**（默认那句英文 3 行 × 19.2 + padding 18 + 描边 2 = 76，
  刚好一次露全、不留滚动条）；**`#lyrics` 从 100px 提到 394px**（`min-height: 200px`）——
  歌词才是这两页真正要填的东西，此前被挤到要来回滚。
- **⑬ 关于页的系统信息重新分组**：33 行平铺 1021px → **4 组两列 564px**
  （端口与版本 / 目录 / 存储与显存 / 引擎与模型）。用**显式两列 grid** 而不是 CSS 多列——
  多列按高度均分，会把下一组的小标题劈到上一组中间、两组之间毫无间隔，等于把分组的本意抹掉。
- **`html { scrollbar-gutter: stable }`**：Windows 滚动条宽 10px，一出现就把可用宽度
  1920 → 1910；而 `.page-head` / `.grid` 是 `max-width: 1680px; margin: 0 auto` 的居中块，
  容器窄 10px ⇒ 左右各内收 5px ⇒ **有滚动条的页标题在 x=115、没有的在 x=120**，切页肉眼可见横跳。
- **纵向预算收敛为单一真源**：`main` 的底部内边距（`--dock-h + --sp-4`）与
  `.grid.compose .pane` 的高度公式此前各写各的（82 vs 71），每张 compose 页高出视口 11px。

### 测试
- `tests/test_design_system.py`：**41 → 46**。新增
  `test_ailab_height_is_layout_derived_not_a_magic_number`（并正则禁止 `calc(100vh - <字面数>px)`
  再次出现，注释除外）、`test_footer_hint_truncation_leaves_a_trace`
  （进函数体查 `.title =` 与 `scrollWidth` 判据，防止"函数名还在、函数体被掏空"）、
  `test_vertical_budget_is_a_single_source`、`test_preset_row_label_shares_the_chip_line_box`、
  `test_hist_all_row_kinds_share_the_five_column_skeleton`。
- 五条回归敏感性**实跑验证**：改回魔法数 / 去掉 `flex-direction` / 改回 `line-clamp` /
  掏空 `syncFootHintTitles` 函数体 / `min-width` 涨回 58 —— 各自如期失败。
- 全量 `python -m unittest discover -s tests`：**410 条全绿**（v10.9.0 的 394 → 本版 410）。

### 验证（真实 3081 UI · 1920×1174）
- **26 组合（13 页 × 深浅两主题）**：标题横坐标**唯一值 115**；页面级溢出 **0**（此前 ailab 26px）。
- ⑪：行高直方图 `{72: 60}`（唯一高度）；列 x 各自唯一（任务 58 / 状态 588 / 操作右沿 1672 / 簇宽 236）；
  「更多」菜单默认 `display: none`。
- ⑬ 系统信息 564px、4 组齐全；② `#style` 78px、`#lyrics` 394px。
- 全部内联脚本过语法自检（`{total: 1, bad: []}`）；3081 服务端返回的 HTML 与磁盘**逐字节一致**。

## [v10.9.0] - 2026-10-09

来源：用户对当前工作台的五点要求——**① 修一个报错 ② 降"AI 味" ③ 提升可视化 ④ 拆出"⑬ 关于"页 ⑤ 定版本号并发布**。
承接 `v2.0.0`（11 页骨架）与 18 项功能核查，本版是"**从能用走向可信、好看**"的一版：继续扩到 **13 页**，
视觉从"暖纸+核桃木+铜色"的 AI 默认审美换成 **GitHub 数据密集设计语言**，并修掉一个拦住整条"乐谱生成"链路的模型切换缺陷。

### 新增
- **⑬ 关于页**：把原 ⑫ 设置页里的"系统信息"与"关于"拆成独立页签（`static/index.html:#tab-about`）。
  左栏系统信息（只读，`#sysInfo` / `#sysRefresh`），右栏出处（作者 / 邮箱 / 仓库 / YuE2 合集）、
  许可（CC BY-NC 4.0）与诚实声明。⑫ 设置页改为一句指路 + 一个跳转按钮，不再重复堆内容。
  侧栏新增第 ⑬ 项，页面契约、侧栏序号、dsh 侧栏 `LAB_TABS` 同步为 13 页。
- **①②③④④(第五项) 一键切换模型**：`POST /api/models/switch` 现在接受 `model/` 下的**任意层级子路径**
  （如 `yue2-q4_k_m/yue2-3b-q4_k_m.gguf`），不再要求"纯文件名"。切换前后按 `MODEL_DIR.resolve()` 做
  路径逃逸校验，`.gguf` 后缀与"非 vae"两道闸门保留。

### 变更（去 AI 味 · 可视化 · 一致性）
- **整站换用 GitHub 数据密集配色**（取自 `sdesign-simonlin` 设计系统库的 `data-dense/github` 预设）：
  亮色 `#e9edf2` 底 / `#ffffff` 面 / `#d0d7de` 线 / `#1f2328` 字 / 单一强调色 `#0969da`；
  暗色对齐 GitHub Dark（`#0d1117` / `#161b22` / `#30363d` / `#e6edf3` / `#4493f8`）。
  **去掉**暖纸径向光晕、核桃木渐变侧栏、铜色强调色、键面倒角与颗粒——这些是 AI 生成界面的典型"味道"。
- **模块分界**：卡片统一 `1px solid var(--line)` 边框（原来是更浅的 `--line-soft`，低对比屏上糊成一片）；
  区块小标题 `.sec` 改为"标题 + 下划线压住内容"，一眼看清每块从哪开始；`.page-title` 作为整页唯一 26px 锚点。
- **组件可识别性**：`label` 加粗到 600 并用正文色（原来是灰的 11px）；输入框占位符用 `--dim` 明确区分
  "空着"与"填了字"；按钮去掉暖色阴影与滤镜过渡，改扁平强调色 + 一行低调投影；分段控件（`.segbtn`）
  改为内容尺寸 + `nowrap`，修掉"② 六要素装…"被裁成省略号。
- **跨页一致性**：所有页共用同一套令牌与组件类（卡片 / 标签 / 输入 / 按钮 / 分段 / 芯片），
  同属性组件在各页尺寸、颜色、圆角、间距一致；侧栏序号 `.tno` 提升到 12px 并用强调色芯片底色，
  低分辨率下也能看清页码。
- **强调色预设重调**：8 个预设（湖蓝 / 松绿 / 紫藤 / 绯红 / 青瓷 / 赭黄 / 石墨 / 深紫）全部换成冷色系，
  每一档都按 WCAG 算过对比度（4.6:1 起）；旧的暖色存储值经 `ACCENT_LEGACY` 映射迁移到新默认色，
  老用户升级后不会停在坏色上。

### 修复
- **"⑤ 歌词+Style+乐谱生成歌曲"报"模型切换失败：只接受 model/ 目录下的纯文件名"**：根因是
  `available_models()` 返回的 `path` 含子目录（模型实际放在 `cpp/model/yue2-q4_k_m/…`），
  而 `models_switch` 用 `Path(path).name` 与原名比对，凡带目录一律 400。改为解析后限定在 `MODEL_DIR` 内，
  再校验后缀与 vae。**这条 bug 之前拦死了所有嵌套目录下的模型，等于"乐谱生成"链路选不了模型。**

### 测试
- `tests/test_page_contract.py`：页签/面板/序号契约扩到 13 页（28 条全绿）。
- `tests/test_review_fixes.py`：`TestModelSwitchPath` 重写为 6 条——嵌套路径**应被接受**、
  返回相对路径、vae 拒收、缺失文件 404、纯 `..` 穿越仍拒（去掉了原先"嵌套即非法"的错误断言）。
- `tests/test_design_system.py`：页面标题数 12→13；`ACCENT_LEGACY` 迁移测试改为解析多对映射，
  断言旧暖色在场且不过 AA、迁移目标全部过 AA（30 条全绿）。
- 全量 `python -m unittest discover -s tests`：**394 条全绿**（v2.0.0 的 325 → 本版 394）。

## [v2.0.0] - 2026-10-08

来源：用户对旧"创作"页的定性——"**整个音乐工作台上，承载了太多工作流，太混乱了**"，并指名要 11 个页面：
①Style 组装 ②Style+歌词生成歌曲 ③参考歌曲提取乐谱 ④Style+乐谱+歌词生成歌曲 ⑤音色制作
⑥音色库/音色文件分析 ⑦人声伴奏分离 ⑧音色转换 ⑨歌词与曲风 AI 工作台 ⑩任务管理 ⑪设置。
约束三条：保持现有 UI 风格、按已实现的能力重新梳理、**尽量复用已有成果不重复造轮子**。
规划落在 `docs/规划-v2.0-音乐工作台页面重构.md`（与 GitHub issue #1 同文件），实现按其中的 P0→P4 推进。

规则（`static/index.html:2186 switchTab`）：**拆页不许靠复制代码**。②④ 共用同一个 `#genForm`，
③④ 共用同一个 `#abcBlock`——切页签只是把同一个 DOM 节点搬进目标页的插槽（`moveInto` `index.html:2182`），
跨页数据走单一 store（`LabStore` `index.html:6222`）。于是"从 ① 带 Style 去 ②"不是同步两份表单，
而是同一份表单换了个位置：没有第二处可以忘记更新的地方。

### 新增
- **11 页信息架构**：侧栏分三组（创作 ①-④⑧ / 声音 ⑤⑥⑦⑪ / 系统 ⑨⑩）。旧书签的 hash 走别名表
  继续可用（`LEGACY_TAB` `index.html:7665`：`#compose→sing`、`#rvc→convert`、`#rvcTrain→train`、
  `#voice→voices`），旧"创作"页（`#tab-compose`）已拆除。
- **⑦ 人声伴奏分离独立成页**（本版唯一需要新写后端的一页）：`POST /api/audio/separate`（`app.py:5300`）、
  `GET .../status/{id}`（`5346`）、`GET .../audio/{id}?part=`（`5369`）、`GET .../list`（`5402`）。
  含和声人声 / 去和声人声 / 伴奏三种产物可试听、可下载，并能一键送 ⑪ 换声、⑤ 训练素材、③ 转谱。
  GPU 串行沿用既有 `_GPU_SEM`，静音守卫（峰值 <1e-4 判数字静音）与 legacy VR 链路的
  `PYMSS_DISABLE_CUDNN=1` 一并带上，不许静默返回空文件。
- **⑩ 设置页**：`GET /api/system/info`（`app.py:694`）——版本 / 端口真源 / 七个目录 / 磁盘 /
  显存 / 引擎就绪，全本地、只读、不回密钥；侧栏底部只留一个健康灯与主题切换。
- **⑥ 音色文件导入与分析卡**：`POST /api/rvc/models/import`（`app.py:3587`）收 `.pth`（+可选 `.index`），
  同名不静默覆盖（409 先报 size 与 mtime，带 `overwrite=on` 才换）；外部音色**必须**同时给一段
  该歌手本人的干声建档，没有档案的音色给不出变调建议，端点直接拒收并在回包里如实交代建档结果
  （`f0_reference.ok=false` + `warning`，不假装建好了）。`GET /api/rvc/models`（`3874`）新增 `items`
  富数据（size / mtime / f0_range / range_summary / recommend），⑥ 的分析卡与 ⑪ 的变调建议同源。
- **⑨ 任务管理统一视图**：`GET /api/tasks/unified`（`app.py:1324`）把歌曲生成 / 换声 / 训练 / 转谱 /
  分离五类汇成一张表；前端加分页（每页 60 条，可继续加载），分离类行内直接给三种产物的下载与去向。
- **③ 转谱产物归档与导出**：`GET /api/scores/{id}/export?format=mid|abc`（`app.py:1116`）。转谱结束时把
  `sheetsage2-output/` 的 `.mid/.lab/.json/.abc` 按 score_id 收进 `runtime/data/scores/<id>/`
  （`src/sheetsage_pt.py`），删谱时一并清理；以前这些文件转完就躺在那儿，前端一个都拿不到。
- **⑧ 会话侧条 + 产出回填**：接上既有的会话列表 / 详情 / 删除三个端点，另加"把 AI 的产出填进创作"面板——
  从 `feeds` 卡或助手正文里抠出 Style 与歌词，一键送 ① 或 ②，送出前先过一遍 `/api/lyrics/validate`。
- **公共件**：统一轮询器 `startPoller`（`index.html:6278`，一张 `POLLERS` 表管三种间隔、miss 上限与
  最长等待），取代继续增殖的第四、第五份轮询实现。
- **契约测试** `tests/test_page_contract.py`（14 条）：11 页签 ↔ 11 面板、静态 HTML 里的 id 唯一、
  共享节点与 `moveInto` 调用在位、侧栏底部只剩健康灯与主题、dsh 侧栏 `LAB_TABS` 与页面同表。

### 修复
- **⑩ 每一行都显示裸英文键**：`SYS_LABEL` 按扁平键写，渲染器走过的却是 `paths.output_dir` 这种
  点号路径，两边永远对不上。改成点号键 + 叶子兜底（`index.html:6565`），并补一条测试：
  后端加字段而前端标签没跟上就直接红，不让页面再长出 `paths.log_dir`。
- **⑧ 回填永远填成空**：下拉选项用全量 `messages` 的下标，回填却用过滤后的助手数组，两边永远错位；
  feed 卡同一处病。改成两个函数共用一份派生列表 `aiFeedList()`（`index.html:7509`）。
- **⑧ 抓不到 Style**：模型实际输出是 `**Style:**` 后跟代码块，旧正则只认行首裸 `Style:`。
  `aiExtractStyleLyrics`（`7484`）改为允许标签两侧的星号并从代码块取值，尾部的"使用提示"散文一并截掉。
- **① 预算读数只在第一次手改后刷新**：旧守卫 `if (!styleDirty)` 让徽标停在首次输入的状态
  （实测正文 56 字符而徽标仍报 143）——改为每次输入都重画。
- **⑨ 分离任务行没有下载按钮**：`/api/tasks/unified` 给的是 `assets` 不是 `parts`，行渲染少判了一种键。
- **⑦→③ 交接假状态**：点"送去转谱"只把状态写成"转谱进行中…"却没提交——改成如实的前置状态 +
  真正触发一次提交事件。
- **`.out-sticky` 不生效**：被 `.grid > div { position: relative }`（specificity 更高）压掉，
  选择器提到 `.grid.styleout > div.out-sticky`。
- **dsh 侧栏 React #300 崩溃**：`dsh-plugin/_dsh_home/profiles/.../lib/client.js` 与 `src/tools.mjs`
  脱链成了旧副本（硬链接被编辑器写成独立文件），侧栏渲染崩在 hook 顺序上；重建硬链接并重启 dsh。
- **`\r\r\n` 行尾污染**（6821 处）：拼装脚本按文本模式读、按 CRLF 模式写，把每个 `\n` 又翻了一遍；
  已按字节修复，此后一律通用换行读、`newline="\r\n"` 写。

### 诚实声明
- **⑦ 不能取消**：分离没有取消端点，排队位置也拿不到；界面只说"GPU 被占"，进度是按阶段估算的。
- **③ 手改的 ABC 存不回乐谱库**：没有 `POST /api/scores` 这个端点，本版不做，也不假装做了
  （只能复制或导出 MIDI）。
- **⑧ 仍是 iframe 嵌入**：没有自建消息流、流式渲染与工具卡；后端 `/api/ai/chat` 与两套 skill 手册
  已具备，改造列为 v2.1 候选。
- **Style 只有一个真正的编辑器**（①）：②④ 顶部是只读"来源条"预览 + 跳转，不是第二份可编辑副本；
  模型档位 `#modelSel` 只在 ⑩，其余页面只显示当前值（`#modelNow`）。
- **像素级视觉验收未做**：内置浏览器返回 0×0 视口（截不了图），本版验证走 CSSOM 与结构探针，
  1280×800 / 1920×1080 两档与明暗双主题的目视检查留给真机。
- 一次 4–8 分钟的真生成未在本版重跑（结果卡用 `/api/generate/list` 里的真成品调 `renderGenResult` 验的）。

### 测试
- **325 条全绿**（基线 238 + 新增 87：分离 26 / 契约 14 / 音色导入 16 / 转谱导出 18 / 系统信息 13）。

## [v1.7.1] - 2026-10-03

来源：用户听完 v1.7.0 的 LA 复测后给出两条反馈——"电音和吐字不清并存"，以及
**"《如詩一般的形容妳》不是我拿去 AI 生成的，是我自己的原唱"**。第二条直接推翻了
上一轮归因里的一条前提，第一条说明 v1.7.0 那把尺子只看得见一半的病。

规则（`app.py: _rvc_roughness` 新增音头计数）：**"发沙"与"发糊"是两种病，必须两个数**。
谱平坦度量的是"谐波被换成宽带噪声"；辅音是清音、是瞬态，被摊平之后总高频能量几乎不变
（实测只差 0.8dB），平坦度与 e7k 都看不见它——只有数得出"每秒几次音头"才看得见。

### 新增
- **辅音音头密度**（`_rvc_roughness["onsets_per_sec"]`）：3–8kHz 包络在 10ms 栅格上的
  突跳（≥6dB 且随即回落）次数/秒，只按响度筛帧（峰值 −40dB 以内）、**不要求"有声"**——
  齿音爆破音本身就是清音，拿唱帧去筛等于把要量的东西筛掉（实测这样第 5 轮的保留率会虚高成
  0.88，该是 0.66，指标当场失明）。差值走 `_rvc_roughness_shift` 的
  `onset_in/onset_out/onset_ratio/muffled`，保留率 <0.75 判"咬字发糊"，
  与平坦度的"变脏"分开各说一条（换声体检 tips 与训练自检 ⚠ 都有）。

### 修复
- **v1.7.0 的指标看不见用户报的第二个症状**：LA 第 5 轮在真歌上平坦度只差 1.21×（不判脏），
  音头却只剩 0.66——用户耳朵里的"吐字不清"在我们所有数字上都是绿的。这是能力没做完，不是判据太严。
- **档位选择不能只看训练素材那一段自检**：同一批检查点在两种素材上的排序不一样——
  自检切片（干净的近距干声）上 5 轮最干净（1.52×，越低越干净），真歌上 5 轮**既最脏又最糊**
  （1.21× / 0.66），10 轮与 20 轮反而最好（1.17×·0.88 / 1.16×·0.82）。
  已把 LA 定稿改到**第 10 轮**，并把这条写进 `_rvc_prune_checkpoints` 的理由：早期档要留，
  但选哪一档必须在真实素材上听、在真实素材上量。

### 归因翻案（同一次实测）
- 《如詩一般的形容妳.mp3》**不是 YuE2 的输出**：ID3 写着 `artist/album=曹楊 Young`，与那条
  YuE2 任务（`20260930_142205_6d213e16`）同一段 8 秒的波形相关系数只有 **0.05**（同一份录音
  该 >0.98），时长差 0.14 秒，4–8k/0–2k 能量比 0.843 对 0.129。上一轮"原曲自带 AI 味"的
  结论是按"歌名＋时长相近"推出来的，方法本身就是错的——判同一份录音只能靠波形相关。
- 在这首真歌上把嫌疑逐个排掉（同一输入、同一参数，只改一项）：静音门关掉（音头 0.61 照样糊）、
  protect 0.33→0.5（1.21 不变）、采样率 44.1k→40k→44.1k 往返（平坦度 0.466→0.472、音头 2.27→2.15）
  都**不是**原因；换 蛋卷（1.02×，音头 1.12 倍）与 邓丽君（0.87×，0.95 倍）走同一条链路则完全正常。
  → 病在 LA 模型本身，素材侧的天花板是 12 个 AAC 有损文件＋峰值顶满 0dBFS。

### 验收
- 全量回归 238 例通过；新增 `TestRvcRoughness` 第 5 例：谐波堆上每秒一次硬起音 vs 同样能量
  但 0.35 秒慢攻击——前者音头数得出、后者掉一半以上且判 `muffled`，同一条音频自己比不许报警。
- 线上实测：网关重启后跑一条真实换声，任务 `quality.roughness` 已带
  `onset_in/onset_out/onset_ratio/muffled` 四项（LA 第 10 轮在他自己的训练素材上
  0.3666→0.5001、音头 3.46→1.94 判糊；同一模型在真歌上 1.17×、0.88 不判糊）。



## [v1.7.0] - 2026-10-03

来源：用户反馈"用昨天练好的 LA 音色换了两首歌，电音都很重"。复盘出来的不是参数问题，而是**工程
在冒充它有眼睛**——自检只确认"能出声"，检查点只有一份被反复覆写，loss 首尾用均值被尖峰带偏，
页面上因此没有任何一条能指认"是模型变脏了"。这一批把归因做成数字，并且把这三处假能力改成真的。

规则（`app.py: _rvc_roughness / _rvc_ck_scheme / _rvc_archive_ckpts / _rvc_loss_summary`）：
"听着有电音"必须变成一个能对错的数；"逐档试听/选点定稿"必须真的有档可听；本轮进度只能来自本轮日志。

### 新增
- **高频噪声化（电音）指标**（`_rvc_roughness`，app.py:2689-2760）：只对**确认在唱的帧**算谱——
  有基频（pyworld voiced）且帧 RMS 在峰值 −40dB 以内，指标为 8–16kHz 谱平坦度、>12kHz 能量占比、
  包络帧间相关、每 10ms 音高跳变中位数。第一版没做唱帧过滤，把**数字零**当成了高频毛刺，错判了一次
  伴奏和一个音色（这个坑写进了 docstring，也锁了回归用例）。判脏用两条阈值：绝对增量 0.12 或比值 1.35。
  LA 自检素材实测 **0.271 → 0.827（比值 3.05）**，同期蛋卷是**降**的；同一段干净人声喂 LA 是 0.37 → 0.55，
  喂邓丽君不升——锅在模型侧，不在换声面板那几个旋钮（实测调 `index_rate`/`protect` 不动它）。
- **自检会说"变脏了"**（`_rvc_roughness_shift` + 自检块）：任务卡上的自检除"能不能出声"外并列
  素材/产物两个数和一句人话（"发沙/电音，先听 `vocals_original` 判断是素材还是模型"）；换声产物的
  `quality` 里同步带 `roughness.worse`，历史页 🩺 那行直接可见。
- **重训通道**（`POST /api/rvc/train/resume/{rid}` 收 `epochs` 与 `restart=yes`）：练过头这件事
  现在不用重新上传 42 分钟素材就能重来。`restart` 把旧检查点整体移进 `logs/<音色>/ckpt_archive/`
  （移动不是删除），成品 `.pth` 与 `.index` 覆盖前各存一份到 `trains/<rid>/before_rerun/`
  （app.py:5958-5970，索引那份是本次补的：只有 pth 有备份等于没备份）；不带 `restart` 的续跑想把轮数
  调少是 400，并写明"请带 restart=yes"。本轮 LA 按体检建议从 100 轮改 60 轮重跑。
- **重训期间不许拿上一轮的结论验收本轮**（`_rvc_forget_prev_run`，app.py:4586）：60 轮重训跑起来后，
  卡片上还挂着昨天 100 轮那次的自检结论、损失首尾，检查点下拉框写着**已经归档走的** `G_2333333.pth`
  （点"试听这个点"必然 404），播放器里放的是上一个模型的样片。重训开始前这些字段一律清掉，
  样片整份挪进 `before_rerun/prev_preview.wav`（挪走不是删——那还是用户昨天的成果），本轮结束时按新结果重写。
- **体检过却没开练的素材现在看得见也删得掉**（`/rvc/train/active` 补 `status=checked` +
  页面「🗑 删任务」）：这类任务以前既不在列表里、页面也没有任何删除入口（`DELETE /api/rvc/train/{rid}`
  一直没有前端调用），于是上传的几百 MB 素材就这么永久压在 `trains/` 下没人知道。删除只动任务目录，
  已练好的音色与检索库不受影响（要删音色走「音色管理」）；进行中的任务照旧 409。
- **看门狗免死补漏**（`long_job_pardon`，watchdog.py:145-164）：v1.6 那版只看"长任务文件是不是热的"，
  02:56 实测到反例——网关进程已经没了，`trains/*/job.json` 还是热的，于是看门狗宣布"不误杀"之后
  **什么都不做**，页面一直连不上。现在免死必须同时满足"端口有人监听"，端口空着照常在拉起。

### 修复
- **逐点试听/选点定稿此前是假的**：训练命令写死 `-l 1`，100 轮全覆写同一个 `G_2333333.pth`，页面上
  那 4 档候选背后只有一份文件。改为 `-l 0`（每档独立 `G_{步数}.pth`，本机 30 轮实测存出 G_60/G_80/G_120），
  并按 `_rvc_ck_scheme` 保证**一个目录只用一种制式**（`latest_checkpoint_path` 按文件名数字排序，
  写死的 2333333 压过任何真实步数，混用会让自动续跑读错档）：目录里只剩旧档的音色继续走 `-l 1`。
- **剪枝留错了档**（`_rvc_prune_checkpoints`）：原先"留最近 2 档"，而练过头要到中途才听得出来，
  恰好把中段删光。改为按分位留**最终 + 约 25% + 约 50% + 约 75%** 四档（每对 G+D 本机实测 1.27GB，
  四对约 5GB，E: 盘剩 382GB）；下拉框用 `_rvc_ck_label` 显示
  「第 45 轮」而不是让人猜步数（步数换算来自本轮 `train.log`，读不到就照实写"非轮次"）。
  25% 这一档是 60 轮重训**当天**补的：逐档实测证明"越练越脏"（见验收），最优解在更早那一侧，
  只留 50% 以上等于把唯一可能干净的档删掉。
- **产物量不出＝最坏那一种结论，不是"没有数据"**（`_rvc_roughness_shift` + 自检块 + `_rvc_quality_report`）：
  旧写法只要有一侧测不出就 return None，卡片于是只剩"自检片段已生成"。而 60 轮那次的自检产物
  整段顶在 −0.7dBFS、pyworld 找不着稳定基频（唱帧 <50），**三个档里最脏的那个看起来最没问题**——
  这就是静默退化。现在素材量得出而产物量不出时返回 `{worse: true, unmeasurable: true, why_out: "唱帧不足…"}`，
  页面与体检文案照实说"这一档不能用"。
- **loss 首尾窗口不许重叠**（`_rvc_loss_summary`）：窗口 60 而本轮只有 51 个记录点时，head 和 tail
  取的是**同一段**，卡片上"loss_gen 4.007→4.007"是把同一个中位数印两遍冒充趋势。现在按记录点数把窗口
  收窄，重叠时明写"记录点太少，看不出收敛方向"并给 `trend_ok=false`。
- **本轮进度不许拿上一轮的日志冒充**（`_rvc_archive_ckpts` 连 `train.log` 与 tfevents 一起归档）：
  重训刚开始时卡片会显示"第 100 轮 / 共 60 轮"，心跳判活也会被上一轮的时间戳顶着，判成死的。
- **loss 首尾对比用均值失真**（`_rvc_loss_summary`）：改用**中位数**并跳过第 1 轮的冷启动值——本机
  第一次 `loss_disc` 是 2.9e9，均值口径下整段收敛趋势是假的。任务卡上注明"中位数，已跳过冷启动"。
- **定稿端点的 400 举例已经不存在**：`/rvc/train/promote` 原来提示"例如 G_2333333.pth"，`-l 0` 之后
  本机不再产生这种文件，用户照抄必然失败；改为列出该音色**当前真实存在**的档位。

### 验收
- 全量回归 237 例通过（`py312\python.exe -m unittest discover -s tests`，本机实测约 50 秒）：新增
  `TestRvcRoughness` 4 例（数字零不算毛刺 / 更噪的那份每项都更差 / 归因指对那一层 /
  产物量不出＝最坏结论而不是空白）、
  `TestRvcCheckpoints` 扩到 17 例（分位剪枝含 25% 早期档 / 旧制式目录不换手 / 归档而非删除 / 轮次标签 /
  不调 restart 不许把轮数改小 / loss 汇总扛得住冷启动尖峰 / 重训清空上一轮结论且样片只挪不删）、
  `TestWatchdogSparesWorkingTrain` 7 例（有人监听才免死、端口空了照样拉起、陈旧文件不算进度、
  非网关端口不适用）、`TestCheckedTaskVisible` 2 例（体检未开练的任务必须可见可删、训练中的任务 409）。
- 真机验证：`-l 0` 生效——第 6 轮（step 1020）落下独立的 `G_1020.pth` + `D_1020.pth`（03:19 实测），
  本机节奏约 120 秒/轮（从 `train.log` 自带的每轮耗时解析，不是公式）。
- 真机路径：LA 音色按 60 轮重训中（`restart=yes`，`ck_scheme=fresh`，旧 100 轮的检查点与日志在
  `ckpt_archive/`，昨天的成品与索引在 `before_rerun/`）；听感验收按分工由用户本人做。
- **60 轮重训跑完后的逐档实测（同一段训练素材、同一套推理参数，只换检查点）**：
  8–16kHz 谱平坦度 素材 0.2714 → 第 30 轮 0.667（2.46 倍，唱帧 334）→ 第 42 轮 0.785（2.89 倍，
  唱帧 120）→ 第 60 轮 **量不出唱帧**（产物整段满幅、无稳定基频）。练满 100 轮那次是 0.827（3.05 倍）。
  结论：**这个素材上"练得多"与"练得干净"是反着走的**，而且单调——所以已按 20 轮再重训一轮
  （存盘间隔 5 轮，留 5/10/15/20 四档，正好落在实测最优的那一侧），并把剪枝的早期档补上。
- 60 轮那次的自检没有报出任何异常（`self_check` 里连 `roughness` 字段都没有），正是上面第二条
  "静默退化"的现场证据；修好之后的判定会直接打 ⚠。

## [v1.6.0] - 2026-10-03

来源：v1.5.0 解决的是"中断能不能被发现、能不能自己接上"。这一批管剩下两件事——
**一次中断要丢多少轮**，以及**中断当时显卡是什么状态、为什么断，有没有留下证据**。
复盘文档第四节里"显卡驱动/供电/散热"那条线，从这次起有本机实测数据可回看。

规则（`app.py: _rvc_save_every / _rvc_ckpt_health / _rvc_gpu_watch / _rvc_gpu_events / _rvc_keep_awake`）：
偶发中断的代价要用数字约束，不能靠运气——一次中断最多丢 10 轮（存盘间隔封顶 10 轮）；
续跑接的每一档必须先确认是完整的；中断发生时显卡的显存/温度/时钟必须有时间线可查。

### 新增
- **存盘更密**（`-se` 从 `epochs//4` 改为 `_rvc_save_every(epochs)`，取值 5~10 轮）：LA 那次
  100 轮 25 轮才存一次，03:43 断线只能退回第 25 轮；现在最多丢 10 轮，顺带把"逐档试听/挑档定稿"
  的档位从 4 档变 10 档（练过头这种毛病要到中途那几档才听得出来）。代价是磁盘：本机实测一对
  检查点 1.27GB（G 417MB + D 857MB），100 轮的峰值从约 5GB 涨到约 13GB，导出成品后照旧剪到 3 档
  （约 4GB）；E: 盘实测剩 382GB。
- **半截检查点隔离**（`_rvc_ckpt_broken` / `_rvc_ckpt_health`）：不 load 进 torch，只看尾部 256KB
  有没有 zip 的目录记录 EOCD（完整 LA 权重实测 EOCD 距文件尾 22 字节）。强杀正落在 `torch.save`
  中间时留下的就是"大小看着正常、内容没了尾"的文件，而 train.py 加载它失败后**静默回落到底模从头练**
  （train.py:259 的 `except`）——任务上写着"自动续跑第 2 次"，实际前面几十轮全丢。现在续跑前先体检，
  坏的改名 `G_50.bad-<时间>.pth` 隔离，好的原地不动，于是接的是次高档而不是底模。
- **训练期显卡采样**（`_rvc_gpu_watch`）：每 60 秒一次 `nvidia-smi` 快扫，落
  `logs/<音色>/gpu_health.jsonl`（一晚约 60 行、每行 200 字节）并把峰值汇总进 `job["gpu_watch"]`。
  LA 那天"第一次比续跑慢 2.8 倍"是靠两轮时间戳反推的，当时显存/温度/时钟没有任何记录。
  慢速警告 `pace_warn` 现在也带上报警那一刻的采样值。
- **驱动事件进死因**（`_rvc_gpu_events`）：抓到中断时顺手查最近 5 分钟系统日志的 `nvlddmkm` 报错，
  条数与最近时间写进失败原因、`train_error.log` 和 `job["attempts"][].driver_events`——
  定 LA 根因时只能人肉去翻事件查看器，现在页面上直接看得见。
- **训练期间防睡眠**（`_rvc_keep_awake`）：训练这一步用 `SetThreadExecutionState` 声明"这段时间
  机器别睡"，结束立即撤销；**不改用户的电源计划**，也不影响用户手动睡眠。只在 训练中 开，
  别的步骤不声明。
- **页面留痕**：训练卡片加一行 `🛡`（开训前让没让显存 / 中断几次 / 第几次续跑成功 / 显存与核心峰值 /
  降频采样次数），悬停是每次中断的"停在第几轮 + 原因 + 同期几条驱动报错"。

### 修复
- **新守卫差点白写**：`_rvc_ckpt_health` 起初只查 `G_2333333.pth`，那是旧制式（`-l 1`）的名字；
  本机实测新制式（`-l 0`）存出来叫 `G_25/G_50/G_75.pth`，而续跑取的是"文件名数字最大"那一份
  （train/utils.py:222 `latest_checkpoint_path`），正好是保存被打断时最可能残缺的那一份——
  只盯 2333333 等于没查。现在扫目录里全部 `G_*.pth` / `D_*.pth`（`.bad-` 的跳过）。
- **假警报**：`clocks_event_reasons.active` 是位掩码，`0x1` 的含义是"GPU 空闲"（本机空载实测就是
  `0x0000000000000001`），原来把它算进"降频采样"，每次开训/收尾都会凭空冒一条。现在按位与 `~1`，
  只认真降频位（主动降频/功率上限/硬件降速/墙/温度）。
- **进度接口 500**：`_rvc_checkpoints` 在 glob 与 stat 之间赶上"导出成品那一步剪旧档"会抛
  `FileNotFoundError`（本批真机跑到过，02:42:17）。现在丢掉那一份继续列，不把整个列表炸掉。
- **测试替身自己崩**：用 `SimpleNamespace(run=…)` 顶掉 `app.subprocess` 会把 `PIPE`/`Popen` 一起
  抹掉，报错看起来像"守卫崩了"其实是替身缺字段。改用 `_SubProxy`——只换 `run()`，其余属性转给真的 subprocess。

### 测试
- `tests/test_rvc_train_guard.py` 增至 **35 项**（本批 +14）：存盘间隔取值表、EOCD 判坏（完整 zip /
  截断 / 只有 `PK` / 文件不存在）、隔离与保留、重试前体检、防睡眠只对训练成对开关且绝不抛、
  驱动事件解析与"查不到不许抛"、死因带驱动事件进留痕、`nvidia-smi` 字段解析与 `[N/A]` 容错、
  `gpu_health.jsonl` 落盘与峰值汇总（含"空闲位不算降频"）、采样器挂了不许编数字、慢速警告引用实测显存、
  检查点列表的删除竞态。
- 全量回归 `py312\python.exe -m unittest discover -s tests` → **216 项通过**（59.9 秒）。

### 真机复验（2026-10-03 凌晨，合成素材走完整流水线，跑完当场清理）
```
① nvidia-smi 真读并解析全字段：空载 684/6144MB、43℃、SM 300MHz、throttle 0x…01
② nvlddmkm 事件：注入前近 24 小时 0 条；02:39:25 强杀训练进程 → 守卫当场抓到"1 条显卡驱动报错
   （最近 2026-10-03 02:39:25）"，事件 ID 153 进 attempts[].driver_events
③ SetThreadExecutionState 开/关都返回 0x80000000（非 0 = 成功）；训练期间 powercfg /requests 的
   SYSTEM 段里确实挂着 py312\python.exe —— 防睡眠不是空函数。
   电源计划实测「在此时间后睡眠」接通电源=0x0（从不）、电池=0x384（900 秒）
   → 睡眠不是 LA 那次的死因，这条守卫防的是"以后被改成 30 分钟"那种坑
④ 真 LA 权重 417MB 判坏=False；同一文件截掉尾部 40 字节的副本判坏=True → 隔离改名成功
⑤ 中断演练：第 14 轮把最新一档 G_50.pth 抹成半截 + 强杀训练进程 →
   02:39:27 抓到（exit 1，同期 1 条驱动报错）→ 02:39:30 体检隔离 G_50.bad-20261003_023930.pth，
   G_25/G_75 原样保留 → step 变「训练中（自动续跑第 2 次，接第 16 轮）」→ 跑到第 40 轮，
   recovered_after={attempt: 2, from_epoch: 16}；gpu_health.jsonl 14 行，
   峰值显存 5939/6144MB、核心 67℃、SM 最低 300MHz
⑥ 页面（/static/index.html，音色制作页）：训练卡片渲染出
   `🛡 开训前已卸载生成引擎让出显存 · 中断 1 次 · 第 2 次续跑成功 · 显存峰值 5939MB · 核心峰值 67℃`，
   悬停明细"2026-10-03T02:39:27 停在第 16 轮：子进程非零退出（exit 1），同期系统日志 1 条显卡驱动报错
   （最近 2026-10-03 02:39:25）"，浏览器控制台无报错
```

### 追加（同日第三批）：网关重启不再打断训练
凌晨这一小时里本机 LA 的重训死了**两次**（02:38、02:51），两次都不是显卡驱动，而是网关重启把
worker 一起带走——`_orphan_cleanup_on_startup` 只负责把它判成"已中断"，而同一函数里的批量队列
（第 3 段）早就有重启自恢复，训练没有。这是"中断来源"里唯一还能提前防住的一类，所以补上第 4 段：
过了闸门的任务走用户点「↻ 续跑」的同一条路（`rvc_train_resume`：检查点体检、死因归档进
`last_error`、暂停标志复位）自己接回来。

三道闸门（不接也要把原因写进 `job["auto_resume"].skipped`，页面看得见）：
① 只接 **30 分钟内**还在推进的（`_RVC_AUTORESUME_WINDOW_SEC=1800`）——几天前的僵尸记录不许在半夜
复活再烧几小时 GPU；② 同一任务最多自动接 **2 次**（`_RVC_AUTORESUME_MAX`，计数进 `auto_resume_count`）
——真崩在硬件上时"重启→训练→崩→重启"是死循环；③ 用户主动暂停的不接；`train.py`/`preprocess`
子进程**父进程已死**的先 `taskkill /F /T` 收掉（本机 02:51 的实测形态：网关 02:49 没了，子进程
02:51 还在写日志——它的管道属于已死的网关，缓冲区塞满就永久卡住，还占着 4.8GB 显存），
**父进程活着的一个都不许碰**：那是另一个网关实例正在跑的任务，同目录双训练会把检查点写成花。
`_rvc_train_procs` 查询失败一律返回"不知道"，不许把判断建立在猜上。

- 测试：`TestStartupRecovery` 8 项（新鲜中断被接回、三天前的僵尸不接、接过 2 次后转人工、
  暂停不接、样本丢失时启动不许跟着崩、进程探针的 True/False/None 三态、孤儿被收且活着的那个不动、
  `orphan_reaped` 留痕）。守卫用例增至 43 项，全量 **231 项通过**（49.7 秒）。
- 端到端**没有**演：03:04:47 起本机有一轮真实 LA 训练在跑（`/api/rvc/train/active` 与 `nvidia-smi`
  同时确认：util 87%、显存 4808/6144MB、68℃），在它中间重启网关去"验证自恢复"就等于我自己制造
  这批要消除的那种中断。所以这一批只到用例级；下一次真实网关重启时它自己生效，代价最多一个存盘间隔。
- 顺带在这轮真实训练上验到两件事：`logs/LA/gpu_health.jsonl` 确实在写（9 行，util 90–92%、
  显存 4802→4808MB、核心 61→67℃、SM 1935–1950MHz），以及本机轻载时 `nvidia-smi` 回的
  `throttle=0x…01` 是"GPU 空闲"位——上一节修掉的正是这个会误报"降频采样"的位。

### 已知边界
- 60 秒一次是"时间线"不是"曲线"：故障那一秒内发生了什么仍看不到，能定的是中断前后的显存/温度/时钟水位。
- 驱动事件窗口取"最近 5 分钟"，够覆盖"故障→卡死→守卫抓到"这段时间；更早的那条不算这次中断的。
- 防睡眠只挡系统睡眠，挡不住用户手动睡眠/关机，也不改电源计划。
- 隔离下来的 `.bad-*.pth` 不会被自动删除（要留一份可查），一次中断一对约 1.27GB；
  确认无用后手动删即可。
- 自恢复的"30 分钟窗口"是裁定不是测量：短于它怕半夜复活旧任务，长于它又会让真正需要接的任务
  错过重启。不接的时候页面上有 `auto_resume.skipped` 说明理由，用户点一次「↻ 续跑」仍可手动接。
- `gpu_health.jsonl` 是**实时**写的，而 `job["gpu_watch"]` 的峰值汇总要到这一步结束才随任务记录
  落盘（本机 03:04 起那轮真实 LA 训练实测：jsonl 一直在长，记录里的汇总还是空）。跑的过程中要
  看当前显卡水位，直接 `tail` 那个 jsonl。

## [v1.5.0] - 2026-10-02

来源：用户报告——「昨天启动了 LA 音色的训练，中间断了一次，我点了续训才接上」。
根因（复盘全文见 `docs/故障复盘-LA音色训练中断-20261001.md`）：10-01 03:42:59 训练日志停在
第 26 轮 88%，03:43:34 Windows 记下一条 `nvlddmkm` 153「Error occurred on GPUID: 100」——
**显卡驱动故障把训练进程钉死了**：进程不退出、不报错，而当时的等待只有一句
`communicate(timeout=est_sec)`，它只看得见"进程死了"和"总时长到点"。于是任务挂着"训练中"
白占 GPU 互斥闸两小时半，直到 100×140.7 秒 + 1 小时 = 4 小时 55 分的一刀切超时才被动收掉
（证据：`runtime/output/20260930_234330_70c26006.json` 这条失败记录的**出生时间** 06:11:04，
减 17668 秒正好是训练子进程的启动时刻 01:16:36）。机器全程没睡没重启（6013 在线时长可回算），
也不是用户点了暂停（那会写 `paused_at`）。放大器：同一份素材同一套参数第一次 5:38/轮、
续跑 1:59/轮，差 2.8 倍——6GB 卡上显存被分走就会 CUDA 溢出到内存，既拖速又是这类
驱动故障的高发区（本机 30 天内 153 共 40 次）。

规则（`app.py: _rvc_wait_step / _rvc_run_train_step / _rvc_gpu_yield_before_train`）：
长任务的健康信号是**产出**，不是存活。所以判活看"训练日志最后一次推进距今多久"，阈值取
`max(600 秒, 4×每轮耗时)`，其中每轮耗时取"本机预估与日志实测里更慢的那个"，慢机器不误杀；
抓到之后立刻 `taskkill /T` 杀树并 `wait()` 收尸（僵尸训练进程会继续钉着整卡显存），
再自动从 `G_2333333.pth` 检查点续跑，最多 3 次。「一启动就死、一轮都没推进」不重试——
那不是偶发故障，磨三小时也只是白等。

### 新增
- **心跳判活**（`_rvc_wait_step`）：取代 `communicate(timeout=…)`，进程活着但停止产出
  会在 10 分钟内被抓到（旧方案要等 4 小时 55 分），并写清"停在第几轮、多久没动、阈值多少"。
- **异常自动续跑**（`_rvc_run_train_step`）：崩溃/卡死退避后从检查点再来，页面上的 step 变成
  「训练中（自动续跑第 2 次，接第 26 轮）」，成功后记 `recovered_after`；有/没有检查点
  分别说"接第 N 轮"与"从底模重来"，文案不许说反话。
- **开训前让显存**（`_rvc_gpu_yield_before_train`）：空闲不足整卡 75% 就先卸 audiocpp 引擎
  （`server.json` 已配 `lazy_load`，下次生成自动重载），腾不开再降一档 batch；量到的数字
  如实写进任务 `gpu_yield`。
- **慢速留痕**（`pace_warn`）：实测每轮比本机**实测**预估慢 2 倍以上就记录，并说明
  "显存大概率被占、CUDA 溢出到内存既拖速又最容易触发驱动故障"。预估来自 240 秒/轮的
  保守兜底时不报警——那是 CPU 首跑的口径，比了必误报。
- **事故留痕**（`_rvc_record_failure`）：每次中断落一份 `logs/<音色>/train_error.log`
  （不会被续训覆盖）并追加 `job["attempts"]`，含停在第几轮、exit code、子进程输出尾巴。

### 修复
- **续训不再抹掉死因**：`rvc_train_resume` 清空 `error` 前先归档进 `last_error`，`attempts`
  原样保留。上一版 LA 的根因就是这么丢的，只能靠 Windows 事件日志和文件出生时间反推。
- **杀进程必有兜底**：`taskkill` 打不动时退回 `proc.kill()` 再 `wait()`，杜绝"以为杀了
  其实没杀"。
- **进度判定改前缀匹配**（`app.py` 四处 + `static/index.html` 两处）：自动续跑时 step 带后缀，
  旧的 `=== "训练中"` 会让进度条和预计剩余时间凭空消失。
- **测试会话的一处自伤**（`tests/test_review_fixes.py: TestRvcIndexNamed.setUp`）：在
  `app.subprocess` 已被 mock **之后**又存了一次快照，于是 tearDown 把假 run 当原值还原，
  此后整个测试会话里所有真实子进程调用静默失效（本轮新增的守卫用例正是被它抓到）。

### 测试
- 新增 `tests/test_rvc_train_guard.py` 21 项：卡死被抓并杀树、健康任务不误杀、一刀切超时
  仍在、>64KB 输出不会把管道堵成"自造卡死"、慢速警告与"保守兜底不报警"、留痕落盘、
  非零退出抛错、心跳抛专用信号、续训归档死因、重试/无推进即停/到上限/暂停不当事、
  让显存四种分支、以及拿 LA 真实数字做事故回放（新阈值 600 秒 vs 旧 17668 秒，快 29 倍）。
- 真机端到端（合成"嗓音"素材跑 6 轮，跑到第 2 轮时用 `NtSuspendProcess` 把训练子进程冻住）：
  **20:33:19 注入 → 20:33:57 抓到并杀 → 自动续跑第 2 次 → 6 轮跑完 → 索引 2 秒 → 导出 4 秒 →
  status=done**，`attempts` 与 `train_error.log` 都在，产物当场清理，`LA.pth` 未受影响。
  同一次跑里 `pace_warn` 抓到 14.0 秒/轮 vs 预估 5.0 秒 = 2.8 倍，与事故当天的倍数一致。
- 回归全量：`py312\python.exe -m unittest discover -s tests` → **194 项通过**（34.8 秒）。
- 网关已重启载入新代码（旧实例是 09-30 23:41 起的，跑的还是改前的内存版本），
  `/api/health` 与训练历史页均正常。

### 已知边界
- 判活阈值最低 10 分钟，所以"驱动故障→自动续跑"最快也要 10 分钟才发现，不是瞬时；
  换来的是慢机器与刚重启任务绝不被误杀。
- 让显存这一步只在**开训前**做一次；跑到一半别的进程挤进来，靠 `pace_warn` 留痕而不是中断任务。
- 驱动/供电/散热那条线（30 天 40 次 153）属于机器层，本轮未动，建议按复盘文档 P2 排。



## [v1.4.1] - 2026-09-29

来源：用户实测报告——「换声时加减了音调，人声变了调，但伴奏没变，合成后很违和」。
根因：变调旋钮（`--pitch`）以前只挂在 RVC 推理命令行上，伴奏是原样加回去的——
变 4 半音的成品里人声与伴奏差了 4 个半音，两调打架。

规则（`app.py: _rvc_acc_shift_semitones`）：人声吃满 pitch（八度分量是「换到哪个
音区」，由人声承担）；伴奏只吃**非八度分量**并折到听感更小的 ±6 以内——变 ±4 →
伴奏 ±4；变 ±12 / ±24 → 伴奏 0（整八度不改「是什么调」，用户点名的例子）；变 ±16 →
伴奏 ±4；变 ±20 → 伴奏 ∓4；变 ±6 → 伴奏 ±6（三全音两头等价，与人声同向）。为什么
折：往上挪 8 个半音，底鼓基频就从 50Hz 抬到 ~80Hz（±16 那种要到 130Hz，鼓变
「纸箱」），弦乐共振峰整体上移（变细变尖）；而同一个调性，折到近的一头就能到，
不必付这个代价。

### 新增
- **伴奏跟随转调**（`_rvc_transpose_wav` / `_rvc_transpose_accompaniment`）：混音前
  用 ffmpeg **rubberband**（相位声码器：保时长、保共振峰、原生立体声）把伴奏平移
  到同一个调；整八度直接复用原伴奏、不跑转调。ffmpeg 按 `FFMPEG_PATH` → 随包
  `py312/ffmpeg/bin` → `PATH` 探测，并**探到滤镜层**（gyan essentials 构建不带
  rubberband，必须说清原因而不是静默不转）。产物 PCM_24（默认 16 位会白掉一位
  动态范围）。开销：221 秒立体声实测 ~8 秒，相对整个换声任务可忽略。
- **产物与页面**：下载区新增「伴奏（原调）」（A/B 对比、想自己配原调用），「伴奏」
  按钮标注实际转了几个半音；任务卡/历史卡显示跟随结果，meta 记 `acc_pitch`
  （pitch / applied / engine / state / note）。

### 修复
- **转不动绝不静默**：找不到 ffmpeg / 不带 rubberband / 产物近乎静音 → 退回原伴奏、
  任务照常完成，但 `state=failed` 与原因写进任务卡（「成品里伴奏仍是原调」必须让
  用户查得到）。沿用 v1.3.1 的教训：退出码 0 + 数字静音也是失败。

### 测试
全套 **158 例全绿**（148 → +10）。新增锁定：规则全区间（−24..24 逐值）的不变量
（伴奏分量与人声同调、|折后| ≤ 6、只有整八度允许伴奏不动）；整八度不碰 ffmpeg；
真跑 rubberband（440Hz 正弦 +4 半音：实测主频落在理论值 554Hz ±6Hz、时长与立体声
保持、产物 PCM_24）；产物静音必须判失败；worker 全链路（混音用转调后的伴奏、
原调另存、失败退回且状态为 failed）。真机验证：用户 221 秒歌曲 −6 半音 → 伴奏
独立测量 −6.00 半音（帧数 9749570 分毫不差、立体声、FLOAT→PCM_24）；+16 素材 →
伴奏 +4.00。

## [v1.4.0] - 2026-09-28

来源：需求"同一局域网里其它电脑的浏览器也能打开 3081 工作台"。做法不是把 dsh 改成监听
`0.0.0.0`（它设计上拒绝，因为那等于把可执行工具的入口交给全网段），而是三段拼起来：
TCP 层 `netsh portproxy`（192.168.1.6:3081 → 127.0.0.1:3081）＋ dsh 的 `--trusted-host`
（把它自己的同源栅栏认这个地址）＋ 网关的 LAN 白名单。网关仍绑 `0.0.0.0` 但 7863
**不对局域网放行**，局域网只能通过 3081 的面板反代摸到网关。

### 新增
- **两个双击就能用的开关**：`scripts\开放局域网.bat` 与 `scripts\关闭局域网.bat`。
  开放：需要管理员时自动弹一次 UAC（在 bat 里用 `net session` 判，不在管理员窗口里就
  `Start-Process -Verb RunAs` 重开自己），然后在 cmd 窗口里**直接打印给其它电脑的完整
  地址（含 token）**并复制到剪贴板，窗口 `pause` 留住结果；同时把 `secrets\local_env.bat`
  的名单三行放开（改前备份 `local_env.bat.bak`）。关闭：删转发、删防火墙规则、把三行
  `rem` 掉。两个可选参数：`-Restart` 顺带重启工作台让名单立刻生效（只杀本机绑定地址的
  监听进程，portproxy 的 svchost 不动），`-Preview` 只打印地址、什么都不改（不弹 UAC）。
  实测：`开放局域网.bat` 打出 `√ token   实测有效 · 已复制到剪贴板`，`curl http://192.168.1.6:3081/?token=…`
  回 303 并下发 `dsh-auth-…` cookie（authority 就是 `192.168.1.6:3081`）；再跑一次是幂等的
  （名单未改动 / 旧转发先清后建 / 防火墙规则已更新）。
- **cmd 输出改版（2026-09-29，用户实测反馈"刷屏太多、不够简洁"）**：屏幕上只剩
  "标题一行 + 每步一行 √/× + 地址一块 + 三条提示"（13 行，原来 24 行且每行带时间戳）。
  `Log()` 改成**只写 `runtime\data\logs\lan-open.log`**，上屏统一走 `Head`/`Step`/`Memo`/`Rule`/`Tail`
  五个小函数；步骤名固定 8 格宽，宽度按"中文算两格"计（`Vis()`——按 `.Length` 补齐会歪）。
  `√ × ─ ·` 都在 GBK 里，配套 bat 进门就 `chcp 65001`，直连 CP936 也不会成问号。
  精简**不牺牲诚实**：每步的 `×` 行都带原因与修法（IP Helper 被 Disabled、本机 IP 不在
  白名单、转发口没在听、token 已被 dsh 重启换掉、收回后还剩哪几条没删），原先那些
  "设计说明"类流水（例如"网关端口故意不放行"）退到日志里。另外新增
  `开放局域网.ps1 -Preview -Revoke` 的**收回预演**（只读，报"现在挂着什么、真收回会动哪些"，
  不需要管理员，双击 `关闭局域网.bat -Preview` 即可）；bat 收尾不再重复打印日志路径
  （ps1 已经给了那一行），非提权的那个启动窗口在提权成功后 4 秒自关，只在授权被取消时
  留窗说"未做任何改动"——不再留一地黑窗。
- `settings.app_host` 改为可用 `YUE2_HOST` 覆盖（默认仍回环）：把"开放局域网"这件事
  和一个开关放在一起，避免"代码说开放、进程说没开放"的两套行为。
- dsh 面板路由（`/lab*`、`/lab-api*`、`/lab-status`）**新增鉴权闸门**：拿调用方的
  Host+Cookie 回环问 dsh 自己是否已鉴权，结果按 (host, cookie) 缓存 30 s。
  开放前这条是裸奔的——实测不带任何 cookie：`GET /lab/api/health` 200、
  `POST /lab/api/generate/start` 打到网关参数校验（400）、更糟的是
  `GET /lab/api/ai/web` 直接把**带进程 token 的 dsh 地址**返回给调用方，
  等于把"AI 对话"的门票发给任何能连上局域网的机器。现在一律 401，已鉴权才放行。
- `ai_router` 的 `/api/ai/web` 会把 token 地址改写成调用方正在用的 host
  （原来恒返回 `127.0.0.1:3081`，局域网电脑点"AI 助手"会被指到自己那台机器）；
  它自动拉起 dsh 的那条路径补上 `--trusted-host`（缺了它的症状是页面能打开、按钮全没反应）。

### 修复
- **面板反代 GET 全线永久挂起**。`ui-panel.mjs` 原先只用 `target.setTimeout()`，那是
  **绑在 socket 上**的超时：连接池（`maxSockets:24`）被长任务占满时请求还在 agent 队列里
  排队、压根没拿到 socket，于是永不触发 —— 实测现场是 dsh→7863 挂着 24 条 ESTABLISHED、
  `/lab/api/*` 与 `/lab-status` 全 pending，而同一条 URL 在刚起的进程上 8 ms 就 200。
  现补一个与 socket 无关的墙上时钟定时器，超时返回可读 504 并往 `_dsh_err.log` 写一行
  带池状态（在用/空闲/排队）的诊断。故障注入实测（黑洞监听 7863、30 条并发）：
  30/30 全部 8.0 s 拿到 504，包括超出池上限的那 6 条排队请求。
- **网关事件循环被重活冻死**（同一个"全线转圈"现象的真凶之一）。12 个 `async def` 路由里有
  5 个在循环上直接跑音频重活：`/voices`(建档推理)、`/voices/transcribe`(ASR)、
  `/voices/denoise`(UVR)、`/rvc/pitch/advice` 与 `/rvc/models/{name}/f0-reference`(f0 提取)。
  一次净化 22 s，这 22 s 内**整个网关**不响应任何请求，看起来像反代坏了。现全部
  `run_in_threadpool` 卸载。真机验证：局域网跑 22 s 净化期间 `/lab-status` 连续 6 次
  仍 7–40 ms 返回。
- **keep-alive 配对**：uvicorn 默认 5 s 单方面关空闲连接，而面板客户端留 15 s
  （`keepAliveMsecs`）—— 复用到"服务器已关、客户端以为还活着"的连接就会石沉大海。
  `timeout_keep_alive=75`，服务器必须比客户端更晚关。
- **两处"按端口杀进程"误杀系统服务**（本轮踩了两次实祸）：`停止音乐工作台.bat` 与
  `watchdog.py: find_listener_pid()` 都按端口第一条命中就 `taskkill`，而 portproxy 的监听
  行（`192.168.1.6:3081`，宿主是 svchost/iphlpsvc）也匹配 `:3081` —— 杀掉的后果是局域网转发
  与 IP Helper 一起没了，日志里就是"已击杀假死进程 PID 21848"。现在只认回环/通配绑定
  （`_OWN_BIND_PREFIXES` / `LocalAddress -in 127.0.0.1,0.0.0.0,::,::0`）。实测：跑击杀语句后
  svchost（18968）存活，看门狗 35 s 内自愈起网关与 dsh。
- **开了 LAN 之后本机自己不能生成**：LAN 模式下 Origin 判据被换成"只查 LAN 白名单"，
  而本机工作台的 Origin 恒是 `http://127.0.0.1:3081` → 本机的每个写请求被自家守卫 403。
  现在回环与白名单二者都放行（新增回归用例锁定），`evil.example` 照旧拒。
- `scripts\开放局域网.bat` 必须存成 **CRLF**：只有 LF 时 cmd 会把中文 `rem` 注释的后半截
  当命令执行（实测报"…到执行' is not recognized"），首次双击直接失败。
- **"收回"其实什么都没收回**：`netsh interface portproxy show all` 的输出是
  `地址 端口 地址 端口` 四列、**中间没有冒号**，而原解析按 `":3081"` 匹配整行，一条也匹配不上，
  于是日志写成"本机没有该端口的端口转发"、报着成功，`192.168.1.6:3081` 的转发还原封不动挂着
  （实测抓到：删完防火墙后 netsh 仍能列出该条）。现按列解析（`Get-PortProxyRules`），
  并且删完**复核一遍**，没删干净就明说还剩哪几条；顺带处理 netsh 偶发的
  "系统找不到指定的文件"（等 1 s 重试一次）。实测：收回后 `show all` 为空。
- **有效 token 被读成"工作台没在跑"**：PowerShell 5.1 的 `Invoke-WebRequest` 处理 dsh 那条
  带 `Set-Cookie` 的 303 响应时抛 `NullReferenceException`（`-MaximumRedirection 0` 或跟随
  重定向都抛，异常里也没有 `Response`），所以"实测 token 有效性"那步恒判失败、把能用的地址
  标成警告。换成 .NET `HttpWebRequest` 关掉自动重定向直接取状态码；用真实文件里的那段函数
  跑五个用例验证：回环带 token 303、乱 token 401、局域网 authority 带 token 303、
  不带 token 401、关闭端口 0。
- **提权重跑后 `%~dp0` 变空**：bat 里用 `shift` 吃掉 `__admin` 标记会**把 `%0` 一起挪走**，
  之后 `%~dp0` 展开为空，`-File "开放局域网.ps1"` 被当相对路径解析到仓库根（实测报
  "argument ... does not exist"）。现改成进门先 `set "HERE=%~dp0"`、根本不用 shift，
  要不要提权直接由 `net session` 判（管理员实例自然走前台路径）。
- 名单口径核对原先只看进程环境变量：`.bat` 是先 `call local_env.bat` 再跑脚本，所以三行刚被
  脚本放开的那一次，环境变量里还没有，会误报"YUE2_LAN_HOSTS 为空"。现回退到直接读
  `local_env.bat`，并在日志里说破读自哪里。
- `.gitignore` 补 `secrets/*.bak`：脚本改动名单三行前的备份文件里同样有 `DEEPSEEK_API_KEY`，
  原来只忽略了 `secrets/local_env.bat` 一个名字，`git status` 会把 .bak 亮出来。

### 已知问题（本轮未修，记录在册）
- dsh 侧栏 `sidebar.footer.action` 槽位崩 React error #300（`dsh-plugin/lib/client.js` 的
  StatusCard 里 Hook 顺序违规：`if (rail) return null` 之后还有 `useState/useEffect`）。
  蓝军 G3 评审早已记录，本机与局域网都出现，与本次改动无关。
- `scripts\register_protocol.bat` 同样是 LF-only，属同一类隐患，尚未验证是否真会炸。

### 测试
`py312\python.exe -m unittest discover -s tests` → 148 tests OK；`node --check` 过
`ui-panel.mjs`。注意：`dsh-plugin\src\ui-panel.mjs` 与 dsh 实际加载的
`_dsh_home\profiles\web\node_modules\yue2-lab-plugin\src\ui-panel.mjs` 是**硬链接**，
用编辑器保存一旦改成"临时文件+rename"就会断链（实测断过一次，改完 dsh 仍在跑旧代码），
改完务必 `stat -c "%i %h"` 比对，断了就 `New-Item -ItemType HardLink` 重建。

## [v1.3.1] - 2026-09-27

来源：首次真机跑「中」档素材净化时的事故复盘。legacy VR 架构的净化/去混响模型
（`UVR-*` 多 band 卷积）在本机 cuDNN 9.17 + GTX 1660 SUPER 上整张掩码输出 NaN，
被上游 `nan_to_num` 静默洗成 0——产物**退出码 0、文件齐全、内容全是数字静音**，
一路骗过进度检查到预处理才以"没有可用样本"收口（首轮整条链白耗 2.2 小时）。
成因是 vendored 第三方运行时里的数值行为，代码评审看不出来；且「中」档权重是当天
中午才装好的，这是它第一次完整上真机。

### 修复
- **legacy VR 净化子进程显式关 cuDNN**（`PYMSS_DISABLE_CUDNN=1`，由 py312 的
  `sitecustomize.py` 在 `import torch` 前生效，只认这个环境变量、只作用于净化子进程——
  分离/去混响的 bs_roformer 链不受影响）。真机实测：关掉后 GPU 产物峰值与 CPU
  完全一致（0.2962），且**更快**——20s 素材 50.4s → 11.3s（坏掉的 cuDNN kernel 连
  速度都拖垮）。触发方：`app.py` 的 `_pymss_env_cudnn_off()` 按模型名前缀 `UVR-` 判断。
- **净化产物静音守卫三连**（`app.py`）：① 按"选中的那条声部"判峰值（< 1e-4 视为数字
  静音；降噪模型的 Noise 声部实测峰值 4e-4，只按目录扫会误杀好素材）；② 整批选中声部
  全静音 → 自动换 CPU 重跑整批并在任务卡明写原因，个别静音 → 退回净化前版本并计数；
  ③ 落档后逐文件复测峰值，出静音当场报错中止，`min_peak` 写进任务信息——"净化真的
  做了且没做塌"从此有可见证据。续跑复用 `dataset_purified/` 前同样先验峰值，上一轮
  的静音留档不许被"文件齐了就跳过"继承。
- **切片器空产出的诊断修复**（`runtime/rvc/train/preprocess.py`，vendored 文件）：
  整段静音/过短导致一片都切不出时，老代码在 `norm_write` 上抛 `UnboundLocalError`
  （`tmp_audio` 未赋值），13 条素材全报同一个栈、真原因被完全遮住。现在打印
  "[数据切分][跳过] 切不出任何片段"并正常跳过。
- `runtime/` 与 `py312/` 不入库，两处运行侧修复已归档进 `patches/rvc-clean-guards/`
  （含成品文件与恢复步骤）。改动期间全套回归从 141 例扩到 **147 例全绿**——
  新增 6 例锁：cuDNN 开关只喂 legacy VR、GPU 静音→CPU 重跑落地真音频、
  Noise 声部极安静不误杀、resume 拒绝继承静音留档、读不动≠静音、真空切片守卫。

## [v1.3.0] - 2026-09-27

来源：评审文档《RVC最佳实践对标与改进建议》。本轮落地 C1 fcpe / C2 filter_radius / C3 原生采样率、
C4 音域匹配（含其点名的 A3 源端部分）与 A1 素材净化——评审点名的两项 P0 全部完成。

### 新增
- **f0 算法补 fcpe**。vendored 的推理管线（`infer/vc/pipeline.py`）本来就支持 pm/rmvpe/fcpe 三家，
  缺的只是上游 CLI 的 choices 白名单只写了两个、网关与页面跟着只认两个。现在 CLI 放行 `fcpe`，
  换声页三档：**rmvpe（通用首选）/ fcpe（新一代，又快又准，Applio 同款）/ pm（最快、最容易飘）**。
  `torchfcpe` 已装入 py312（附带权重随 wheel 分发，无需另下）。真机验证：邓丽君 + fcpe 完整跑通，
  F0 提取仅 0.64 秒。训练侧不动（改训练 f0 算法要重抽特征，收益不明确）。
- **`filter_radius`（音高中值滤波半径）补回 CLI 与页面**。这是官方 WebUI 有、上游 CLI 从来没暴露的旋钮，
  社区处理**哑音/毛刺/断续**的第一把手。链路：`infer/cli.py --filter-radius`（默认 3）→ `vc_single` →
  `pipeline.get_f0`，在音高提取之后、清音插值之前做 `signal.medfilt`（与官方 WebUI 同一位置同一实现）。
  页面文案：默认 3；哑音毛刺明显调到 5~7；再大就发闷。真机验证：同一段素材 fr=5 与 fr=0 输出确有差别。
  试听/训练自检走同一条 CLI，自动继承默认 3——听到的与换来的一致。
- **换声页新增「输出采样率」**：默认 0 = 模型原生；高级选项可填 44100/48000。
- **素材净化进训练流水线（评审 A1，两项 P0 的最后一项）**。做音色最怕素材不干净——带混响的
  素材练出来的模型会把混响一起"学进音色里"，换声时自带空洞感，事后无法剥离。现在音色制作页
  新增「素材净化」下拉（不净化 / 轻 / 中），在**人声分离之后、切片之前**跑两步：去混响 → 降噪。
  两档模型：轻 = UVR-DeReverb 4band + UVR-DeNoise-Lite（约 73MB，通用稳妥），
  中 = dereverb_bs_roformer + UVR-DeNoise（约 316MB，混响重/底噪脏的素材）；
  权重复用本机 vendored PyMSS 引擎，**零新依赖、runtime 一个字节没改**（不像 C1/C2 需要打补丁）。
  实测 CPU 过完两阶段 2×7.4 秒素材共 28.2 秒，对几十分钟的训练可忽略。
  配套的防呆：**体检报告量底噪**（帧能量第 10 百分位，> -42 dBFS 荐「中」、> -55 荐「轻」）
  并在页面自动套用建议档位（用户手动碰过下拉就不动）；**原始素材/分离产物原样留档**，
  净化结果只写 `dataset_purified/`，训练前后两份可 A/B 对比试听；单文件失败回退原件并在
  任务卡计数，全部失败直接终止报错而不是拿未净化素材假装跑；续跑继承档位、已净化过就跳过。
  一个真机拦路虎：VR 架构模型必须走 `-m pymss.cli`（vendored 的 `_core_shims.py` 硬性要求
  包前缀 `pymss.modules.`，沿用分离链路的 `-m tools.pymss.cli` 会抛 invalid local module alias）——
  已写进代码注释并用例锁死命令行前缀。详见评审文档第十节。
- **下载音色也能建档音域了**（评审 J1，C4 的收尾）。上一版的舒适音域只来自训练素材 `2a_f0`，
  而本机音色库里 4 个音色（孙燕姿/王菲/王菲V6/邓丽君）**全是下载音色，一个都没有 2a_f0**——
  等于 C4 对用户实际在用的音色覆盖率 0%，「分析音域」永远只能说"这个音色没有音域数据"。
  新增 `POST /api/rvc/models/{名}/f0-reference`：给音色上传一段**该歌手本人的歌**（10~30 秒足够，
  上限 60MB，比换声页的 200MB 严），几秒算出中位与 p5–p95，写进 `logs/<名>/f0_stats.json`
  并标 `source: "reference"`。取值口径**与评审字面建议相反**：本机训练素材永远优先——有 `2a_f0`
  还硬要拿参考音频建档直接 409（训练集是这个音色的第一手证据，参考音频不许盖它），
  只在没有训练素材、或 `2a_f0` 是空目录时才用建档档。页面文案与 meta 都区分标注
  "来源：参考音频 N 秒 / 训练素材实测"，绝不把参考音频的数字说成训练素材实测。
  改名时 sidecar 已跟着搬，建的档不会丢。
  为什么不能用"换声产物"反推目标音域：RVC 是 voice conversion，**保留源 f0 曲线**只换音色，
  产物音高 ≈ 源音高 × 2^(pitch/12)，不含任何目标音色信息——实测孙燕姿与邓丽君同一首源歌
  的产物中位差 **0.09 半音**，照这个思路建档会得到"所有音色舒适音域都一样"的假档案。
  这条开发中真撞了一次（拿换声产物建档得中位 315.2Hz ≈ 源音高镜像），验证完即删除该假档。

### 修复
- **换声不再强制上采样到 48k**（`--resample-sr` 原先在网关里硬编码 48000）。`torch.load` 实测本机四个音色：
  孙燕姿是 48k 模型，**王菲/王菲V6/邓丽君都是 40k**——后三者的每一次换声都在做一轮无意义的 40k→48k 重采样：
  40k 模型的 Nyquist 就是 20kHz，上采样补不出高频，只多一次劣化；混音环节本来就按伴奏采样率对齐，
  不需要提前统一。代码里"40k 直出会损失高频"的注释前提是错的，一并改掉。
- **`f0_method` 白名单更新为 rmvpe/fcpe/pm**，参数校验仍在收文件之前完成，坏值 400 不落盘。
- **偶数平滑半径会把整条换声崩掉**（评审 H1，`--filter-radius 4` 实测在 `get_f0` 抛
  `ValueError: Each element of kernel_size should be odd`）：scipy 中值滤波只认奇数核。
  网关侧 4→5、6→7 钳位（≤2 的"关闭档"不动，不许顺手改成 3），pipeline 里再兜一层
  （`radius | 1`），页面对该旋钮改成 0/3/5/7 四档下拉——手输偶数的入口从源头关掉。
  真机复验：`--filter-radius 4` 打补丁后的 CLI 完整跑通。
- **runtime 被重装成上游原版时，换声不再 100% 硬崩**（评审 H2）。`runtime/` 整目录不入库
  （`.gitignore:2`），而原版 CLI 不认识 `--filter-radius`——无条件拼参数等于 argparse 退出码 2、
  每单必败。现在网关首次用到时探测 `cli.py --help`（约 0.1 秒，进程内缓存）：
  不认识就不传该参数、任务上明写 `caps_warn`（不许静默退化）；选 fcpe 而 CLI 不支持时提交直接 400，
  并指出补丁位置。三处 vendored 改动已备份到 `patches/rvc-infer/`（含恢复说明），重装 runtime 后可还原。
- **fcpe 的依赖探测前置到提交时**（评审 H3）：没装 `torchfcpe` 的机器上原先选 fcpe 照样能提交，
  排队之后才在模型加载处炸。现在 import 探测（实测 5.4 秒，只在选 fcpe 时探、结果缓存）失败即 400，
  并给出安装命令或"改用 rmvpe"的出路。
- **音域匹配与自动变调建议**（评审 C4，两项 P0 之一）。把"换了但不好听"从玄学变成可诊断：
  - **目标音色的舒适音域**（`_rvc_f0_stats`）：训练时每个切片的 f0 已落在 `logs/<名>/2a_f0/*.npy`，
    收尾顺手算全集中位与 p5–p95 并落 `f0_stats.json`（重扫实测 0.1 秒 / 335 文件，带内存缓存）。
    音色卡片显示"舒适音域 ≈ 56–132Hz（中位 92）"。**只有本机练过的音色有这个数**——
    外部下载音色（王菲/邓丽君…）没有训练素材，如实显示"无音域数据"，绝不拿别人的数字冒充
    （该缺口由上条 J1 的参考音频建档补上）；
    音色改名时 sidecar 跟着搬（不搬就会假称没数据）。
  - **源音域**（`_rvc_f0_of_audio`）：pyworld.dio 纯 CPU 实测（3.7 秒素材 0.07 秒），取中位与 p5–p95，
    有声帧不足直接 422 而不是编一个数。换声页「分析音域 → 变调建议」几秒出结果，不进 GPU 队列。
  - **建议变调** = `round(12·log2(目标中位/源中位))`，钳 ±24（与闸门同口径）、差不到一个半音不建议；
    换声跑完后 worker 会用**分离后真正进 RVC 的人声**再量一次（比换声前拿整曲探针量的可信），
    写进成品 meta 的 `src_f0/pitch_advice`，音域差与实际变调相差 ≥4 半音时在任务卡上提示
    "下次试 ±N"。测不出来绝不把成功的换声改判失败，但失败一定留痕（`src_f0.error`）。
- **素材净化不再可能"退出码 0 却零产出"还算成功**（评审 J3）。`_rvc_pymss_stage` 原先只认子进程
  返回码；实测给 `-i` 传一个不存在的目录，PyMSS 退 0、一个文件不写，而上层按产物判成败所以兜住了，
  但 `_rvc_clean_dataset` 之外任何直接调用者都会拿到"成功"。现在补一道：返回码 0 而输出目录为空
  → 报"退出码 0 但没有任何产物（输入未命中或模型未生效）"，把将来 PyMSS 升级/换机后
  "净化静默跳过、拿未净化素材继续练"这条路提前堵死。
- **删一条换声记录，盘上不再留三个孤儿**（本团队自查 K1，非评审点名）。`_output_purge` 的白名单
  只覆盖 `<id>.wav/.json/.txt/.lrc/.lrcjob/.elrc`，而带分离的换声还写
  `<id>_vocals_original.wav`、`<id>_accompaniment.wav`、`<id>_full_song.wav`（成品常 30~60MB × 3）
  ——删除接口返回 `ok:true`，这三个字节一直留在 `output/`。现在 purge 先读该任务 meta 登记的
  `assets` 名单按名删，且只认 `<id>_*.wav` 这个形状（meta 里塞 `../app.py` 也不许越界，
  依旧不对 URL 来的 rid 做 glob）。HTTP 真实路径复验：该任务的 4 个文件一次删净，而 meta 里故意塞进
  `assets` 的 `../app.py` 与别人任务的 `20260921_163548_a292_full_song.wav` **都还在**（越界名、
  跨任务名一概不认）。
- **音域体检不必再传整首**（评审 J4）。`/rvc/pitch/advice` 收 200MB 整曲而 `_rvc_f0_of_audio`
  只读前 300 秒，前端文案补"只放源歌 10~30 秒人声片段即可，带伴奏数字会被污染"；
  更可信的一条是**回填**：worker 已经在换声后量过"分离后真正进 RVC 的人声"（`meta.src_f0`），
  任务卡现在直接显示"🎼 源音域 ≈ 中位 xxxHz"（悬停给 p5–p95 与来自建档的建议变调），
  不再要用户拿整首歌去换几个参考数字。

### 测试
- 新增 32 例，共 141 例全绿。锁住：fcpe 可提交且 `filter_radius/resample_sr` 进任务与队列元组、
  fr 越界（>7）与非法采样率（如 8000）一律 400、worker 命令行真的带 `--filter-radius/--resample-sr`
  且默认口径为 `3/0`、按 rid 重换时两个新参数原样继承、**偶数半径钳成奇数（4→5、6→7、2 不动）**、
  **缺 CLI 能力或缺 torchfcpe 时提交 400 且给出路**、**探测到旧 CLI 时 worker 不传该参数并留
  `caps_warn`**、**探测函数解析 --help、只跑一次、探坏按缺能力降级不崩提交**；
  C4 一侧：**目标音域只从真实 2a_f0 算（含 0 帧剔除、sidecar 落盘、外部音色如实给 None）**、
  **源端 pyworld 真跑（220Hz 正弦量出 ~220、纯静音 422）**、**建议数学与 ±24 钳位**、
  **两端齐全才出建议、临时目录跑完即清、done meta 音域测量失败留痕而不改判失败**。
  A1 一侧：**净化两阶段按轻=去混响→降噪顺序真调 `-m pymss.cli`（命令行前缀锁死，防包名别名崩复发）**、
  **声部只认精确名单（No Noise/noreverb 这类子串陷阱各有用例）**、**缺输出逐文件回退并计数**、
  **整批失败重试、全部失败在挪动任何文件前终止且文案给出路**、**续跑已净化即跳过**、
  **前缀撞名（s 抢 s0_Dry）不串**、**档位模型在 vendored 目录里真实存在**、
  **提交闸门只认 off/light/medium**、**档位进任务并随续跑继承**、
  **体检底噪 -38.8 dBFS 荐「中」/ -46 荐「轻」/ 很干净荐「不净化」且文案交代混响测不准**。
  Sandbox 默认 stub 能力探测——否则 fcpe 用例就是踩在"作者机恰好装了 torchfcpe"上的又一次本机假通过。
  J1 一侧（走真实 HTTP 端点，不是直调内部函数）：**下载音色传参考音频即解锁变调建议且来源标注为
  "参考音频实测"**、**已有 `2a_f0` 时建档 409 且不落 sidecar（不许拿参考音频盖训练实测）**、
  **两者都在时训练永远优先**、**`2a_f0` 是空目录才回落到建档档**、**不存在的音色 404、
  纯静音 422 且不写档案**。K1 一侧：**meta 登记的 `<id>_*.wav` 多产物跟着删**、
  **`assets` 里塞穿越名/别人任务的文件名/非 wav 一律不碰**（正反两个方向都锁）。
  J3 一侧：**子进程退 0 而输出目录为空必须报错**、**逐文件都零产出时汇总文案带出真实原因**。

### 已知边界（如实记录，不藏）
- **默认行为变了两处**：filter_radius 从"无滤波"变为默认 3（官方 WebUI 的默认值，绝大多数场景更好，
  想要原始音高轨迹就填 0）；输出采样率从一律 48k 变为跟随模型原生（0）。
- fcpe 在 CPU 上也可用但更快在 GPU；DML 分支走的是 vendored 的 `infer/fcpe.py` 适配器，本机未单独压测。
- 素材净化的默认口径分两层：**页面下拉默认「轻」**（新任务先给一档保护），**API 默认 `off`**
  （显式选择才算数，脚本直连不许被 UI 默认值改变语义）。
- 净化阶段中途按暂停要等当前阶段（模型推理整批）返回才生效，与既有的分离步骤同粒度；
  VR 模型对单声道素材会输出双声道（预处理折回，听感无损，文件体积翻倍）。
- 混响多少机器判不准（体检量的是底噪，不是混响），所以体检只给建议档位；
  上传整首现场录音时请手动选「轻」起步——这句也写进了体检文案。
- "带净化的完整真训练任务"目前只有净化函数直调真机验证 + 网关端点冒烟，
  第一次真训练（如兔裹煎蛋卷重练）跑通后该链路才算满格验收。
  **本轮已推进一格**：真实训练单（`files=` 上传 2 条 14.8 秒素材、档位「轻」）走通净化阶段——
  任务卡 `clean {files:2, fully_cleaned:2, carried:0, sec:37.2}`、`dataset_purified/` 有产物
  （1.3MB）、原始素材原样在（592KB）、`_clean_tmp` 已清；该单之后停在"音色特征提取"并报
  "预处理/特征提取后没有可用样本"，是这个 1 轮/超短素材冒烟单该有的诚实失败，不是净化缺陷。
  **仍然没验收的**：完整 100+ 轮真训练出成品。
- 参考音频建档的数字**可信度低于训练素材实测**：一段带混响或经过母带处理的歌手歌曲，量出来的
  中位只能算"这个嗓子大致唱在哪一段"。所以文案一定标来源，且本机有训练素材时绝不使用建档档；
  建档也不许拿换声产物当参考音频（RVC 保留源 f0，产物音高是源歌的镜像，本轮开发中被这条绊过一次）。
- 换声产物的多产物文件（`<id>_full_song.wav` 等）**只在 meta 还读得出来时才跟着删**：
  meta 损坏或被单独删掉时，purge 仍会清 `<id>.*` 白名单，多产物留盘待人工清理——宁留不误删。

## [v1.2.0] - 2026-09-27

来源：用户提的方向——"音色制作"和"歌曲换声"都围着 RVC 这个项目转，去看官方项目和周边最佳实践，
再回来梳理本工程，目标是"便捷地练出好模型、做出高质量换声"。本轮 = P0（网关层小修）+ P1（训练工作流）。

### 新增
- **训练前先给素材做体检**（`POST /api/rvc/train/check`，页面「先体检素材」）。量的是官方口径要求
  用户自己满足、但以前没人替用户量的那两件事：README/faq 说"至少 10 分钟低噪干声"、
  training_tips 说"按 >5 秒静音切开"，以前只写在页面提示里，于是 40 分钟带伴奏的现场录音也一样开跑，
  两小时后拿到一个发虚的音色。报告含文件数/总时长/采样率/声道/峰值与人声响度/静音占比/最长静音段/
  爆音占比 + 建议轮数 + 耗时预估 + 逐条警告。素材就地落盘，开练时带 `dataset_id` 复用同一批文件
  （几十分钟素材不传第二遍）。实测：35 秒合成素材报 9 个切片，与 `preprocess.py` 真实切出的 9 个一致。
- **轮数分档**：闸门从写死的 150-400 放开到 1-1200（旧闸门让"30 轮先试听"这种合理需求根本无法提交），
  页面给 30 试听 / 100 常用 / 200 精训 三档；建议档按素材总时长给（<1 分钟只值 30 轮、
  10-30 分钟才值 200 轮、超过 30 分钟多练只是耗时）。
- **耗时预估只说实测数字**（`_rvc_train_pace` / `_rvc_epoch_rate`）。每一步单独计时写进任务
  （`step_secs`、训练那步另存 `train_sec`），预估只采信 `status=done` 且同时有
  `train_sec/epochs/samples_used` 的历史记录；本机没有一次完整训练时，页面与体检报告都直接说
  "给不出预估"，不拿公式冒充实测。训练超时上限随之从写死的 `轮数 × 240 秒` 改为按实测推算
  （固定 6 小时曾把健康跑到一半的 200 轮训练误杀）。
- **训练 batch_size 按整卡显存取**（`_rvc_train_batch_size`，官方 WebUI 同一口径 `webui.py:180`
  `batch_size = VRAM_GB // 2`，钳在 1..8）。以前写死 4：6GB 卡常年顶满（别人占一点就 OOM），
  16GB 机器只用四分之一、白等几倍时间。任务卡直接显示"6GB 显存 → batch 3"。
- **检查点不再一删而光**：成品导出后保留最近 2 对 `G_*/D_*`（`_rvc_prune_checkpoints`，
  配套 D 留着续跑才不断链），其余照删（一对约 1.2GB）。配套两个能力：
  **逐点试听**（`POST /api/rvc/train/preview/{rid}` 现在收 `{ck: "G_xxx.pth"}`，点名要哪个点
  就给哪个点，绝不偷换成成品）和**选点定稿**（`POST /api/rvc/train/promote/{rid}` 用官方
  `extract_small_model` 把选中检查点重新导出，经体积校验后**原子替换** `weights/<name>.pth`，
  上一版留 `<name>.pth.bak` 可回滚——音色名不变，
  配套索引原样可用，因为索引由特征库生成与用哪个检查点无关）。练过头这件事从此可以听出来再改，
  不必重训。
- **训练收尾自动自检**：练完立刻用成品 + 自己的一段切片做一次 CPU 迷你推理，产物直接挂在任务卡上
  的播放器；失败只在任务上留痕（`self_check`），不把已完成的任务改判失败。这一步仍算在 `running`
  里（`step="自动自检"`），不提前报 done。卡片同时显示从 `train.log` 解析出的损失首尾均值
  （`_rvc_loss_summary`）——"训练完成"不等于"收敛了"。
- 换声页新增**「音量跟随原唱」**（`rms_mix_rate`，默认 1 保持原行为；调小 = 用模型自己的音量起伏）。

### 修复
- **索引串台（音色不对人的真凶）**。runtime 的查找规则允许子串匹配
  （`infer/vc/utils.py:7-57 get_index_path_from_model`），本机 王菲 与 王菲V6 并存时，
  按文件名排序 `..._王菲V6_v2.index` 排在 `..._王菲_v2.index` 前面——选 王菲 实际在用 王菲V6 的检索库。
  现在 `_rvc_index_files` 把"严格属于本音色的"排在前面、宽松匹配只做兜底，并且推理命令**直接点名
  `--index`**（不给就不该给，别交给猜）。修复前后同一接口的实测：王菲 的 index 从
  `added_IVF2574_..._王菲V6_v2.index` 变成 `added_IVF3250_..._王菲_v2.index`。
- **删一个音色会废掉另一个音色的检索**：删除/改名改用严格判定 `_rvc_index_owned`
  （只认以 `_音色名_v1/_v2` 收尾，允许多说话人的 `_spkidN` 尾巴），不再复用查找侧的宽松规则。
- **提交前就把话说清楚**（官方 WebUI 在点转换时做的同一件事，`webui.py:249-254`）：
  `index_rate > 0` 而该音色没有配套索引 → 400 并给出两条可执行建议（补索引 / 把检索强度调 0）。
  以前失败点在十几分钟队列之后：runtime CLI 直接抛 `FileNotFoundError`（`infer/cli.py:152`），
  用户看到的是"排队半天然后报错"。变调必须是整数且在 ±24 内、`f0_method` 只认 pm/rmvpe
  （页面曾把 fa/nsf 亮在选项里，而推理 CLI 的 choices 只有两个）。
- **参数校验挪到收文件之前**：`/rvc/convert` 原先先把几百 MB 上传落盘再校验，坏参数等于让人白传；
  `/rvc/convert/{rid}` 原先先 `copyfile` + 建任务目录再校验，400 之后在磁盘上留下空任务。
- `index_rate=0`（"完全不用索引"）被 `float(payload.get("index_rate") or 0.75)` 悄悄改回 0.75。
- **「送去换声」丢参数**：入口只带 `pitch`，且默认值是硬编码 0（页面上填的变调被丢掉），
  其余七个参数一律不发。现在与页面上「换声」按钮共用同一个取值函数。
- **CPU 模式下 F0/特征提取的传参顺序**：`extract_f0.py` 的 cpu 分支收 `exp_dir n_p f0method`、
  cuda 分支收 `n_part i_part i_gpu exp_dir is_half`，以前不管设备都按 cuda 那一串传——
  切到 CPU 的机器上 `exp_dir` 被读成 `"1"`，F0 对着一个不存在的目录"跑成功"，
  最后 filelist 空、报"没有可用样本"，完全看不出是传参错。顺带把 `preprocess.py` 的路径
  改为跟随 `RVC_DIR`（原来从任务目录反推父目录，任务目录被改写就指到不存在的 train/）。
- **试听两处会骗人**：① 缓存文件名不分检查点，先听新点再听旧点时 `mtime` 判断会让旧点拿到
  新点的音频（标签却写着旧点）；现在按检查点各存一份。② 试听源优先用 `0_gt_wavs` 里切好的
  3.7 秒切片——本机实测同一任务用整首原始上传要 139.6 秒，用切片 15.1 秒（自检挂在每次训练收尾，
  慢十倍就是让用户白等）。
- **测试在本机真机上"假通过"**：`Sandbox` 以前不重定向索引目录，"提交换声"这类用例是踩在
  作者装过孙燕姿索引这件事上过的，换台机器就凭空多出一堆 400。
- **导出会污染音色库、定稿会毁掉成品**（评审 v1.2.0 G2/G3）。官方 `extract_small_model`
  写死相对 CWD 的 `assets/weights/<名>.pth`（`train/process_ckpt.py:216`，**不认** weight_root
  环境变量），于是：① 每回试听中间检查点，`G_xxx.pth` 会短暂落进音色权重目录，被音色下拉框
  列成一个能推理的"杂音色"（超时/崩溃则永久留下）；② 选点定稿直接往真成品上写，半截货
  （磁盘满/900s 超时被杀）只要 mtime 更新过就"被判成功"，音色当场报废且无回滚。
  现在：导出统一走 `_rvc_extract_small`——子进程在 RVC_DIR 里 import（i18n 按 CWD 读 locale）、
  再 `os.chdir` 到 `export_tmp/<名>_<pid>_<毫秒>` 里写产物；定稿侧校验体积（<1MB 或不足原成品
  一半 → 500 拒收，原文件一字节不动）→ 上一版转 `.bak` → `os.replace` 同卷原子替换；
  `_rvc_models()` 兜底跳过 `G_*`/`D_*`/`.` 开头。真机实测（logs/兔 的 G_2333333.pth）：
  3.1 秒导出 55MB 小模型进临时目录，`assets/weights` 全程零变化，临时目录用后即删。
- **done 的产物 meta 挂着"排队中"**（评审 G4）：`status=done` 却留着 `step="排队中"`、
  `queue_pos=1/2`，前端不看但事后排查和 AI 侧读 meta 会被带偏；写盘前清掉这两个排队阶段字段。

- **带分离换声的 ETA 虚高 3.8 倍**（评审 G1）：页面预估系数 3.75×时长取自那条 828 秒样本，
  而它正是三任务并发抢 GPU 时测出来的（v1.1.3 串行队列要修的根因污染了取证）。同一首 221.1s
  的歌干净串行两次实测 222.0s / 226.4s ≈ 1.0×时长，系数改回 1.0，v1.1.3 那条记录已加注更正。

### 测试
- 新增 33 例，共 107 例全绿（`py312/python.exe -m unittest discover -s tests`，约 2 秒，不碰 GPU）。
  锁住：索引归属与排序（含"删一个不能废另一个"）、提交闸门（坏参数/缺索引一律 400 且不起任务、
  不建目录）、体检报告与复用（同 rid、不二次上传）、轮数档（30 可提交、0 拒绝）、
  batch_size 档位与显存未知回落、每轮耗时只采信跑完的训练且无实测时明写保守、
  检查点保留策略、逐点试听缓存、选点定稿的失败路径与"体积不可信→原成品零改动"、
  导出产物只落临时目录（音色目录全程零变化）、done 产物 meta 无排队阶段残留、
  真实 worker 命令必须点名本音色自己的索引、
  成品比检查点旧时不许冒充本轮结果、试听源选取顺序。

### 已知边界（如实记录，不藏）
- **只保留最近 2 个检查点**：更早的点仍然没了；代价是一对约 1.2GB。想留更多改 `_RVC_KEEP_CKPTS`。
- **自动自检在 GPU 锁内**：多占十几秒到几分钟（取决于有没有切好的切片）。它不碰显存
  （强制 `CUDA_VISIBLE_DEVICES=""`），但会让"训练总耗时"变长一点。
- **体检不做 VAD**：静音与响度都是按帧 RMS 的近似（阈值 -45 dBFS），长静音里混着极轻的呼吸会被算进
  静音；单文件只分析前 10 分钟，更长的按抽样说话。
- **耗时预估只在同机同后端可信**：`pace` 记录里带着当时的 backend；换机器、换 CPU/GPU 后第一单
  仍然显示"没有实测"，直到本机跑完一次训练。
- 逐点试听/定稿要求检查点还在：老的训练任务（本轮之前收尾的）检查点已被当时的代码删光，
  它们的卡片只剩"用成品试听"。



来源：用户实测反馈的第 4 条——在「歌曲换声」页连点两次「开始换声」，两个任务并行跑起来了，
"我的电脑资源撑不住"。要求是时间上串行，不是并行。

### 新增
- **换声串行队列**（`app.py`）。取证：`/api/rvc/convert` 与 `/api/rvc/convert/{rid}` 各自
  `threading.Thread(target=_rvc_convert_worker).start()`，任务字典一创建就写死
  `"status": "running"`；全程唯一的互斥 `_GPU_SEM` **只包住 RVC 推理子进程那一段**，
  而最吃资源的人声分离（PyMSS BS-RoFormer，以及 HP5 去和声）跑在锁外——所以连点两次是
  两份大模型同时进显存，且第二条明明在等锁却显示"转换中"、画着进度条。
  现在改成单线程 FIFO：`_rvc_submit()` 入队并返回位次，`_rvc_queue_loop()` 一次只取一条，
  队列空时在锁内清 `_RVC_WORKER` 再退出（不清就会出现"新任务看到线程还活着而不起新线程，
  这边一死没人接手"的丢唤醒）。位次含正在转换的那条（它算第 1 位），并且**按当前队列实时
  重算**（`_rvc_live()`）——入队那一刻写下的位次，前面跑完一条就过期了。
- **`POST /api/rvc/cancel/{rid}`**：只取消还没开跑的（从队列里摘掉，状态 `cancelled`）；
  已在转换的返回 409 并说明原因——RVC 是一个子进程从头算到尾，半途掐掉只留下坏文件。
  页面上排队条目给「✕ 取消排队」按钮。
- **人声分离纳入 `_GPU_SEM`**：排队的换声不会撞上正在生成的歌曲。推理那处仍各拿各的，
  不嵌套、不自锁。
- **"在转换"这个状态改到 GPU 真到手才登记**（`_rvc_mark_running()`）。出队不等于开算：
  `_GPU_SEM` 可能被一首正在生成的歌、一批任务或一次音色训练占着（训练整条流程持锁，几十分钟），
  在出队那一刻写 `running` + `started_ts` 就是把等锁的时间画成转换进度——和这次投诉是同一个病。
  现在等待期间状态仍是 `pending`，`step` 写明"等待本机空闲（生成/批量/训练正占用 GPU）"，
  页面原样显示这句话；耗时 `sec` 也只从拿到锁的那一刻起算。
- 任务字典补 `queue_pos` / `started_ts` / `separate_vocal`；`GET /api/rvc/active` 与
  `GET /api/history/active` **含排队中**的条目（只列 running 的话刷新页面队列就凭空少一半），
  前者按"正在转换 → 提交先后"排序。AI 工具 `tool_rvc_convert` 回包带上位次与状态。

### 修复
- **P1：换声/训练条目上的"⏹ 终止 / ↻ 重启任务"其实打的是生成接口**。任务管理页这两个按钮
  不分类型一律 `POST /api/generate/stop` + `/api/batch/retry`，也就是说在一条正在转换的
  RVC 行上点"终止"，被终止的是别的歌曲生成。现在只对生成/批量条目显示。
- **P1：两处耗时承诺是假的**。`src/ai_tools.py` 里 `tool_rvc_convert` 的 hint 写"约 1 分钟
  内完成"（实测那首 221 秒带分离的歌整条 828 秒），前端进度条对带分离的任务按
  `15s + 1×时长` 估（同一首歌实际 3.75×）。现在分两套口径：不含分离的沿用 3 次实测给区间，
  含分离的按唯一那次实测粗估、进度封顶 90%、文案明写"粗估（分离阶段实测样本仅一次）"。
  > **v1.2.0 更正（评审 G1）**：这条"3.75×"的样本（01:44 三条中的 828s 那条）正是被
  > **三任务并发抢 GPU** 污染出来的——就是本节要修的并行问题本身。串行落地后同一首
  > 221.1s 的歌两次干净实测：222.0s / 226.4s ≈ **1.0×时长**。前端系数已改回 1.0。
- 排队中的条目不再画进度条（`pollRvc` 的 pending 分支），进度改按 `started_ts` 起算，
  不再把排队的那几分钟算成转换进度。

### 测试
- 新增 6 例（`tests/test_review_fixes.py::TestRvcSerialQueue` + `TestRvcWaitsForGpu`），共 70 例全绿。全部用假任务
  替身（`app._rvc_convert_worker` / `_rvc_submit` / `_rvc_models` 挂在真实调用路径上），
  不碰 GPU，可在跑歌时并行执行。锁住：三条一起提交时**并发峰值必须为 1** 且执行顺序=提交顺序；
  位次随队列前移实时重算；只有没开跑的能取消、取消掉的永不被执行、对正在转换的返回 409；
  上传入口必须入队而不是起线程（回包 `status=pending` + `position`）；两个 active 端点都看得见排队条目；
  真实 worker 在 `_GPU_SEM` 被占时保持 pending、不提前起推理进程，`running`/`started_ts`
  只出现在拿到锁之后（`TestRvcWaitsForGpu` 直接把真 semaphore 掐住验证）。

### 已知边界（如实记录，不藏）
- **队列在网关进程内**：重启后排队条目会丢。产物只在算完时落盘，所以不会留下半截文件，
  重新提交即可。与批量队列不同，换声不做磁盘快照——它一次只有一条，代价小于收益。
- **正在转换的单条不能终止**：只能等它算完，或重启网关（会连带中断别的任务）。
- 音高跟踪的电音（v1.1.2 的 P1）与本次无关，仍是"去和声 + 降检索强度 + 降 protect"缓解，
  不能靠后处理根除。

## [v1.1.2] - 2026-09-27

来源：用户实听反馈（孙燕姿音色换声后的三个听感问题）。三条都先做了实测取证，不是照着猜改参数。

### 修复
- **P0：换声结果在"没人声"的地方不是静音，是模型自己哼出来的音**（`app.py` 换声 worker）。
  取证：那首 221 秒的歌，分离出的 `src_vocals.wav` 前 28 秒是数字零，`converted.wav` 同段
  却稳定在 -26dB、FFT 主峰 279/559Hz（基频+二次谐波）——这就是"前边一段底噪特别大"。
  RVC 拿到静音输入不会输出静音，特征层照样解码出一个恒定音，所以以前这段在伴奏里是盖着的、
  单独听干声就是嗡嗡声。现在加**静音门**：以"真正送进 RVC 的那个人声"的包络为准（未开分离时
  就是源音频本身），开帧 20ms/关帧迟滞 6dB/保持 8 帧/攻 6ms 释 140ms，把没人声的片段压到
  -60dB。实测该段 -26.8dB → -86.8dB，唱段逐帧增益变化中位数 0.0dB（不吃咬字），
  默认开启，页面上可关。
- **P1：换声页只能播干人声，且"完整歌曲"偷偷丢了立体声**（`app.py` 混音 + `static/index.html`）。
  换声页任务卡只请求主产物（干声），用户点"播放"听到的是半成品；而混音那段
  `if voc.shape[1] != accm.shape[1]` 的写法在"人声单声道 + 伴奏立体声"时会把**伴奏压成单声道**
  （实测落盘的 `*_full_song.wav` 只有 1 声道）。现在抽出 `_mix_vocal_accompaniment()`：只允许
  单声道往上升、绝不把立体声压掉；成品改 PCM_24 落盘。换声页与任务管理页都给出**两个点播按钮**
  （▶ 成品（带伴奏） / 🎤 干人声），播的时候 toast 明说放的是哪一份；换声页卡片额外显示
  静音门压掉了多少无人声段。
- **P1：protect 的说明与它的实际方向相反**（`static/index.html` 文案 + 预设）。
  读 `runtime/rvc/infer/vc/pipeline.py:211-216`：`protect` 是非乐音帧上"检索特征"的权重，
  **数值越小越保留原唱的清辅音与呼吸（电音更少），0.5 = 完全不保留**。旧文案写成
  "protect 越高咬字越清楚、气声越多"，还给了 `0.65,0.45` 这个方向相反的"咬字更清"预设。
  现在改名「protect（原唱细节保留）」、说明按代码语义重写，预设换成 🔇 防电音 `(0.6, 0.15)`。
  另按症状补一个真开关：PyMSS 的人声声部**含和声**，副歌叠唱会让音高跟踪在高音处跳轨出电音，
  新增「分离后再去和声（HP5 只留主唱）」（HP5 逻辑抽成 `_run_harmony_strip()`，旧降级链与
  PyMSS 链共用；HP5 不可用时如实回退，不再像以前那样直接报错中断）。

### 新增
- `/api/rvc/convert` 新增 `gate`（默认 on）与 `strip_harmony`（默认 off）两个 Form 字段，
  历史转发入口 `/api/rvc/convert/{rid}` 同名 payload 键（该入口 gate 同样默认 on）；
  静音门的阈值/关闭比例写进 `output/<ID>.json` 的 `gate`，`params` 补记
  `separate_vocal/strip_harmony/gate`，事后能查当时到底开没开。
- 回归用例 +7（共 64）：静音门的两个方向（静音参考必须被压、有人声参考必须不动）、
  参考与输出长度不一致的重标、立体声伴奏不得被压成单声道、短轨补齐、掩码展开、
  worker 与端点参数存在性。

### 已知未修（如实记）
- 高音处"偶发电音"仍不可能被后处理根治：它是特征检索在长音/大跳上的替代失真，能动的杠杆是
  降低 `音色检索强度`、去和声、以及给这个音色补更干净的高音素材重训；本机 vendored RVC 的
  `infer/cli.py` 只放开 `pm/rmvpe` 两种音高算法（`fcpe` 在 pipeline 里存在但 `torchfcpe`
  未安装），所以这一版没有提供第三种算法选项。



来源：`docs/评审-优化复查与整改建议.md`（对已发布提交的复查）。**这一轮抓到的 P0 是上一版
交付说明里的真话不足**：`v1.1.0` 把"LAN 白名单守卫"当交付项写进提交说明，可它在真实链路上
一次都通不了——本复查报告已随本版本一并入库，不藏。

### 修复
- **P0：LAN 模式经 dsh 代理后所有请求 403**（`app.py` 守卫）。`dsh-plugin/src/ui-panel.mjs:146`
  把内部跳变的 `Host` 强制改写成 `127.0.0.1:7863`，而旧判据要求 `Host ∈ LAN_HOSTS` → GET 全被拒；
  写请求又因 UI 挂在 `:3081`、`_origin_in_allowlist` 只认网关端口而 403。现在 LAN 模式
  放行回环 Host（外部请求打 `lanhost:7863` 时 Host 就是白名单主机名，DNS 重绑定的
  `Host: evil.example` 两条都不满足照旧 403），Origin 端口判据放行网关与工作台两个端口。
- **P1-1：那条 LAN 测试是"假绿"** —— 它把 `base_url` 直接设成 `http://192.168.1.7:7863`，
  绕过了代理的 Host 改写，架构与真实链路脱节。新增
  `TestLocalGuard.test_lan_mode_works_through_dsh_proxy` 按代理**真实会发出的请求头**复现
  （回环 Host 200、`:3081` Origin 抵达路由层 405、evil origin 与其它端口仍 403），原用例保留作直连兜底。
- **P1-2：LAN 模式下 CORS 仍只认回环** —— 直接开 `http://<lanhost>:7863` 时响应缺
  `Access-Control-Allow-Origin`，页面收得到但 JS 读不到，症状莫名其妙。`allow_origins` 改为在
  `LAN_HOSTS` 解析**之后**构建并把白名单主机的两个端口纳入；顺带消除旧代码在名单就绪前调用
  `add_middleware`、且重复注册一次的隐患。守卫中间件仍在其后注册（最外层），403 不经 CORS 加工。
- **P1-3：看门狗对"启动即死"没有熔断**（`watchdog.py`）—— 配错环境变量（如开了
  `YUE2_ALLOW_LAN` 却没给 hosts）会让 `python -s app.py` 在绑端口前抛 `RuntimeError`，
  而常规"连败→深探→击杀→重启"约 140 秒才转一圈，永远救不活还刷满日志。新增
  `EARLY_DEATH_WINDOW=60`/`EARLY_DEATH_LIMIT=3`：自己拉起的子进程在窗口内退出计一次，
  连击三次熔断并提示查 `gateway.err.log`。`_respawn()` 返回"是否真拉起"，没拉起就绝不写
  "已重新拉起"（延续 v1.1.0 的"自愈文案不许说谎"）；dsh 经 bat 拉起时 `cmd` 先退不计入。
- **LAN 绑定的 TCP 层坑写进页面与文档**：`app_host` 填具体 LAN IP 时回环上没人监听，
  本机工作台连 `127.0.0.1:7863` 直接 `ECONNREFUSED`，HTTP 守卫修得再对也到不了那一层。
  网关在 LAN 模式检测到这种配法即打 ⚠️ 警告点名改法（`0.0.0.0`），README 中英文各加独立警示段。

### 验证方式
- 回归用例 `py312\python.exe -m unittest discover -s tests` → **Ran 57 tests … OK**（52→57：
  走代理的 LAN 用例 1 只 + `TestWatchdogEarlyDeath` 熔断四只）。
- **诚实的限制**：P0 的修复只有 `TestClient` 层验证（按代理真实请求头复现），**没有**真拿第二台
  设备走 `http://<lan-ip>:3081` 跑通一遍；LAN 模式仍是"知情 opt-in 的无鉴权暴露"，真机验收前
  不对外宣称可用。回环模式（绝大多数用户的默认）不受本轮任何改动影响。
- **网关已按新代码重启并做回环冒烟**（09-27 01:34，PID 15508）：`GET /api/health` 200、
  带 `Origin: http://evil.example` 的写请求 403、带本机工作台 Origin 的写请求 404（通过守卫、
  抵达路由）、`Host: evil.example` 403。LAN 分支本身仍只有 `TestClient` 层验证。
- 复查报告里的 P2 四条（`_id_busy` 全目录扫描、和弦解码失败回退、模型子目录放宽、
  媒体响应跳过 CSP）本轮**未动**，维持待办。

## [v1.1.0] - 2026-09-26

### 新增
- **每个任务一个唯一 ID**（`YYYYMMDD_HHMMSS_xxxxxxxx`，就是落盘文件名）：历史、队列、任务管理三处都显示，点一下即复制。项目名可以重复，ID 不会 —— 之前"寻兰好像又在重跑"就是拿项目名当身份看错了条目。
- **转谱「提取时带和弦」勾选框** + 双向防呆：无和弦的谱自动改走 `melody`、带和弦的谱自动改走 `full`，页面上同时说明为什么这么配，用户选错会被纠正而不是被静默带偏。
- `LICENSE`（自写代码 CC BY-NC 4.0 + 上游模型权重许可清单）与 `docs/review/` 三轮对抗评审归档（蓝军 / 逐条回应 / 第三方 / 中立裁定）。

### 变更
- **状态清扫改为"说得出现场能核实的话"**：只有启动时刻早于本进程的遗留条目才标"服务重启，任务中断"；本进程内工作线程没回写的标"队列已停止…请重跑这一条"；worker 还在算的那一条（`current` 指向它）由 worker 自己写结局，轮询不再代写。
  > 冒烟实测（09-26 22:17）暴露的原缺陷：点"停止"后正在算的歌十秒内就被标成"服务重启，任务中断"，而网关 PID 从头到尾没变过。
- **看门狗判死前先做 90 秒深探针**：单次探活超时 5s→15s、连败阈值 3→5，达到阈值后先长超时确认，回了话就判"慢"不判"死"并清零计数。
  > 依据是 09-26 看门狗日志：21:40 与 21:50 两次击杀都发生在批量任务正在算的时候（那两首的耗时停在 471.2s 与 15735.4s）——6GB 卡被 llama-server 与另一项目的 ASR 进程压满时，`/api/health` 几十秒不回话是正常现象。
- **看门狗重启的网关输出落盘**：`stdout/stderr` 由 `DEVNULL` 改为追加写 `runtime/data/logs/gateway.out.log` / `gateway.err.log`（超 5MB 轮转为 `.old`），自愈不再把现场痕迹一起销毁。
- **看门狗启动时对网关做三轮兜底**：原来"从未见过的服务不判定假死"这条规则对工作台是对的（用户可能就是没开），对网关却是死锁 —— 看门狗单独起来（或网关比它先死）时 `seen` 永远为 False，既不会拉起、也不会击杀那个僵在端口上的进程，"立刻拉回"只存在于注释里。现在启动即判定：三轮不回话 → 端口无人监听就主动拉起，有人监听则纳入假死监控。
  > 本机实测（09-26 22:45）：杀掉旧看门狗与网关后用新代码重启，日志出现 `网关 :7863 无人监听，看门狗主动拉起`，30 秒后 `/api/health` 恢复，`gateway.err.log` 首次留下了自愈后的启动痕迹。
- 输入长度超上限改为一律 400 报错，不再静默截断（`_limit_text`，含 style / lyrics / ABC 三个入口与模板、历史两个存储入口）。

### 修复
- 队列状态文件改为锁内原子写（tmp + `os.replace`），写一半被杀不再清空整个队列；旧数据缺 `qid` 的 58 条历史条目迁移为同一桶，不再塌成空队列。
- 崩溃循环保护：短时间（600s）内连续中断 3 次的条目判死并给出原因，不再"自愈 → 看门狗拉回 → 再崩"反复陪葬；用户正常重启不受影响。
- 并发 `batch_start/resume/retry` 可能同时看到"worker 已死"并起两个线程重复跑同一条目 —— 判活与赋值收进同一临界区。
- 本机接口守卫：写方法校验回环 Host + Origin/Referer；开放绑定需 `YUE2_ALLOW_LAN=1` **且**主机名白名单+端口匹配，否则拒绝启动；补 `nosniff`/`no-referrer`/CSP `frame-ancestors` 响应头。
- 删除类接口清理白名单补全（含 `.elrc`），并拒绝通配与路径穿越；RVC 任务删除会连带清掉上传的原曲与分离 stem。
- 上传增加总量预算与磁盘剩余闸门；模型体检结果缓存（损坏结论不长期钉死，文件变动即失效）。

### 验证方式
改动过的行为分两类留痕，缺一类就不算"已生效"：
- 回归用例：`py312\python.exe -m unittest discover -s tests`，52 例全绿（只碰临时目录与只读端点，不提交任何 GPU 计算）。
- 授权重启冒烟（本机实测，网关 PID 换过之后）：唯一 ID 与 `queue_id` 归组、追加排队总数只增不减（58→61）、取消后 pending 转 `cancelled`、无 `.tmp` 残留、超上限字段回 400、跨站 Origin 回 403。
  09-26 22:46 补一轮"停止时正在算"的定点复现：单条任务进入 `running` 后点停止，连续 12 次轮询（约 36 秒）它始终是 `running`，**误标"服务重启，任务中断"次数 0** —— 同一条路径在修复前会在十秒内被轮询代写成那句无据指控。
- **诚实的限制**：冒烟期间没有任何条目跑到 `done` —— audio.cpp 推理请求在本机显存被 llama-server 与另一项目的 ASR 进程占满（5737/6144 MiB）时失败。这是运行环境问题，不是本轮改动的功能缺陷，但"能出歌"这件事本身要等你腾出显存再验一次。

## [v1.0] - 2026-09-23

首个开源版本（标签 `v1.0` → `c400c92`，作为历史起点永久保留，不回溯修改）。
包含：写词/作曲/生成演唱、ABC 乐谱编辑与翻唱、RVC 换声与专属音色训练、逐字卡拉OK歌词强制对齐、
批量队列与任务托管、看门狗双进程自愈、端口唯一真源、显存自适应。
