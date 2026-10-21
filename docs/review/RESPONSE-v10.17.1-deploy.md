# 开发团队回应 · v10.17.1-deploy 三方评审

> 评审对象：`9d0bb9b` 之上暂存的 30 个文件（部署可用性 + Vulkan + 可选功能）。
> 三方报告：`BLUE-TEAM-REVIEW-v10.17.1-deploy.md` / `THIRD-PARTY-REVIEW-v10.17.1-deploy.md` /
> `ADJUDICATION-v10.17.1-deploy.md`。本文只记**整改结论**，不复述问题原文。

## 一、P0（发布阻塞）——全部已修并配回归

| 条目 | 处置 | 改动 | 复验 |
| --- | --- | --- | --- |
| B2 `一键补齐运行件.bat` 里 `/dev/null` | 采纳 | 改 `>nul 2>&1`；新增静态用例禁止任何 `.bat` 出现 `/dev/null` | `test_script_encoding.BatPortability.test_no_unix_redirection` |
| B1 + T1 + T5 health 说假话 | 采纳（驳回"503"的修法） | `/api/health` 三态：ok / busy（端口在监听但 2 秒未回）/ down；`gateway` 仅 ok/busy 时为 "ok"，down 时 "degraded"；**永远 200**（看门狗把 5xx 当死）；前端只认 `audiocpp.status`；出歌入口先看端口在不在，避免忙时再 Popen 第二个引擎；`start_audiocpp` 丢线程池 | `test_shim_main.HealthShape` 四条（含"永不 5xx"） |
| B4 下载器不校验窗口 | 采纳 | `fetch_chunk` 核对 206 的 `Content-Range` 起止与累计长度；拼装前校验块数/总长；`--workers` 钳到 1..32；GGUF 最小字节按真实大小收紧 | `test_fetch_models.ChunkWindow` 三例（好窗口/错位/忽略 Range） |
| B6 + T4 + T9 安装器假绿灯 | 采纳 | `report()` 增 `core_ready`；`--with-*` 失败计入 `failures` 并 `exit 1`；`ensure_engine` 早退也验 `model_specs/yue2.json`；`snapshot_download`/pymss 下载包 try/except；pymss 全失败返回 False | 本机重跑 `setup_runtime.py --with-transcribe --with-rvc --with-lrc --skip-models`（幂等分支）；失败路径代码审读 |

## 二、P1（发功能前必修）——全部已修

| 条目 | 处置 | 改动 | 复验 |
| --- | --- | --- | --- |
| B3 ⑦ 训练缺 tensorboard/matplotlib | 采纳 | `RVC_PKGS` 补两包；`ensure_rvc` 结尾 import 自检 | 本机 import 自检通过 |
| B5 async 端点里跑阻塞自启 | 采纳 | `await run_in_threadpool(start_audiocpp)` | 代码审读 + 既有 400 分支用例未受影响 |
| B8 RVC 补丁路径多一层 | 采纳 | 改为显式映射 `{runtime 相对路径: patches 相对路径}`；缺源/缺目标出声且失败；装完跑 `cli.py --help` 断言 `--filter-radius` 与 `fcpe` | 本机 `--help` 实测含两者 |
| T2 后端探测失败被永久缓存 | 采纳 | 失败不落缓存；探测为空时 `resolve_backend` 原样透传、`_restart_audiocpp` 不传 `--backend` | `test_backend_mode.BackendProbe` 两条 |
| B7 干净 clone 测试炸 | 采纳（按设计保留 raise） | `test_shim_main` 在缺参数表时显式 `SkipTest`；`main.py` 仍保留"缺 spec 就拒绝 import"的硬失败 | 删 spec 的临时树跑绿（人工验证） |
| B12 CHANGELOG/版本 | 采纳 | 新增 `v10.21.0` 条目；`app.py` 读到 `v10.21.0`；随本次提交打附注标签 | `_version_from_changelog()` 实测 |
| B18 Vulkan 无 CPU 兜底 | 采纳 | 错误回退两处门改 `in ("cuda","vulkan")`；回退前写 `state=cpu`、结束切回 `orig_backend`（不再写死 cuda） | 代码审读；Vulkan OOM 形态仍属未实测 |

## 三、P2 / 文案 / 小修——已修

- B9 计数三处不一致 → 统一为 **614**（README 中英、requirements 注释）。
- B10 README 的 `.gitattributes` 描述 → 改为真实的 `*.bat text eol=crlf`，并说明 GBK 尾字节不含 CR/LF。
- B11 两只漏网 UTF-8 `.bat`（`secrets/local_env.example.bat`、`dsh-plugin/启动AI工作台.bat`）→ 转 GBK，加入编码用例名单。
- B13 `X-Seed` 大小写 → 抽出 `_seed_from_headers()`，兼容 `x-seed`/`X-Seed`，补三例。
- B14 `TRANSFORMERS_CACHE` → 测试改为**子进程**先 configure 再 import，断言 transformers 实际常量与 HUB 缓存一致。
- B15 `sitecustomize` 只在缺失时写 → 改为"存在也补 `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD`"。
- B16/B17 状态与缺省 → `ensure_server_json` 已存在时按磁盘上的 server.json 对齐 engine_state；`backend_mode()` 与 `main._engine_backend()` 统一"state → server.json → cpu"。
- B20 LICENSE 自研清单 → 补 `main.py`、`requirements*.txt`、`server.json.example`、`*.bat`。
- T7 OEMCP 校验 → 两只 bat 都加 `findstr /r "^[0-9][0-9]*$"`，畸形回退 936，并有静态用例。
- T8 `.gitattributes` 空 `*.ps1` 规则 → 改为 `*.ps1 text eol=crlf`。
- T10 README"不会静默降级" → 改为"按构建能力落到可用后端并说明"。

## 四、推迟（附已验证理由）

| 条目 | 理由 |
| --- | --- |
| B19 GGUF 只查 magic+最小字节 | 本次已把最小字节收紧到真实大小的 99%；**完整 sha256 校验推迟**：HF 目录未提供稳定校验字段，按字节数 + GGUF 头已能挡住截断（本机两份权重实测一致） |
| B21 补丁随上游 main 漂移 | 已加"缺源出声 + 装后 `--help` 自检 + `.orig.prev`"；**钉 commit 推迟**：上游当前结构与补丁仍兼容（diff 10/54/4 行），钉死反而拿不到上游修复 |
| T1 生成期 `/health` 真实延迟分布 | 需真实长跑数据；修法是"忙≠死"而非提高预算（提高会更容易触发看门狗 15 秒短探）。**待下一首长跑实测** |
| T3 hf_transfer 崩溃 | 驳回：`constants` 在 import 期求值，先 import 后设 env 不会抛（裁定方复跑证实） |
| T6 回退写 cuda | 原触发链不成立（回退不写 state）；但反向问题已顺手修：回退结束切回 `orig_backend` |
| 真实 Vulkan OOM / 训练全程 / 英文 Windows | 未实测，见三方报告"未验证项" |

## 五、验证记录

- 全量：`py312\python.exe -m unittest discover -s tests` → **614 条通过**。
- 变异：把 health 的 `gateway` 改回恒 `ok`、去掉 `Content-Range` 校验、把 `_seed_from_headers` 改回只认大写——
  对应新用例均转红，恢复后绿。
- 本机实测（补功能时）：转谱中文 4/4 行、英文 8 行 LRC+8 行 eLRC；分离 6 秒 → 两轨（RTF 17.7）；
  RVC `--help` 含 fcpe/`--filter-radius`。
