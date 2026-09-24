# BLUE-TEAM REVIEW — 基线 e32bbb5 未提交改动

审查对象：app.py（模型体检/融合/试听/续跑/active 扩展）、static/index.html、dsh-plugin/lib/client.js。
方法：只采信代码本身，全部结论附 文件:行号。审查人：蓝军子代理（敌意审查）。

## A 正确性

**A-1 · P1 · preview 用 G_*.pth 喂 infer/cli.py 结构不兼容，训练中试听必然失败**
- 证据链：训练保存的检查点结构是 `{"model": state_dict, "iteration", "optimizer", "learning_rate"}`（runtime/rvc/train/utils.py:166-174），**没有 `weight`、`config`、`sr`、`f0`、`version` 键**。而 infer/cli.py 的 `load_model_metadata` 要求 `checkpoint["weight"]["emb_g.weight"]`（runtime/rvc/infer/cli.py:95-99，无则 `raise ValueError("Model does not contain weight/emb_g.weight")`），后续 `get_vc` 还要 `self.cpt["config"]`（runtime/rvc/infer/vc/modules.py:174-176）。
- 触发：训练 running 中点「🎧 试听」，app.py:2340-2341 选中 `logs/<name>/G_*.pth`，子进程在 cli.py:236 直接抛 ValueError。
- 后果：训练中试听功能（本次改动的卖点）100% 失败，只有训练完成走「成品」分支（app.py:2337-2339）才可用。正确做法是先过 `extract_small_model` 再推理。

**A-2 · P1 · merge 子进程未设 cwd，官方 merge 把产物写到相对路径，融合大概率永远失败**
- 证据链：`process_ckpt.merge` 最后一行 `torch.save(opt, "assets/weights/%s.pth" % name)`（runtime/rvc/train/process_ckpt.py:290）——相对路径，依赖进程 cwd。而 app.py:1902-1903 的 `subprocess.run([RVC_PY, "-c", script], ...)` **没有传 `cwd=str(RVC_DIR)`**（对比同文件 preview 2375、训练 2491 都传了）。启动脚本 cd 到仓库根，故实际写入 `<repo>/assets/weights/`（不存在则 torch.save 抛异常、被 process_ckpt.py:292 裸 except 吞掉返回 traceback 字符串、子进程 exit 0）。
- 后果：`out.is_file()`（app.py:1904，检查的是 `RVC_MODELS_DIR = runtime/rvc/assets/weights`）为 False → 恒 500 "融合失败"。（未实测运行，见"未验证项"；但代码层面 cwd 链条成立。）

**A-3 · P1 · merge 传给官方 merge 的 f0 参数类型错误，融合产物恒为 f0=0**
- 证据链：app.py:1888 `f0 = 1 if ma["f0"] else 0`，以 `%d` 拼进脚本传 int。而官方 merge 内部 `opt["f0"] = 1 if f0 == i18n("是") else 0`（runtime/rvc/train/process_ckpt.py:285）——期望的是 i18n 字符串"是"，`1 == "是"` 恒 False。
- 后果：两个 f0 模型融合出的 pth `f0=0`，推理时走 nono 分支（modules.py:177-189），用 f0 权重加载 nono 模型（strict=False 静默丢键）→ 融合音色变调/发哑。即使 A-2 修好，这也会产出坏模型。

**A-4 · P2 · merge/check 的 alpha/参数解析无异常兜底**
- app.py:1863 `float(payload.get("alpha", 0.5))`：alpha 传 "abc" → ValueError；alpha 键缺失 → `float(None)` TypeError。均未被 FastAPI 转成 400，返回裸 500。

## B 安全

**B-1 · P1 · python -c 脚本用 `%` 裸拼路径，含单引号的文件名可注入任意代码**
- 证据链：app.py:1896-1900（merge）、1814-1818/1829-1835（check）、1873-1876（_meta）都是 `"… r'%s' …" % p` 形式。`a`/`b` 只做了 `os.path.basename`（app.py:1861-1862），**没有 re.sub 清洗**——单引号不在该黑名单里，Windows 文件名合法含 `'` 和空格；rename 端点同样不滤单引号。
- 触发：weights 目录下存在名为含 `'` 的 pth，点"融合"即执行注入代码。
- 缓解：服务绑 127.0.0.1，但浏览器任意页面可经 CORS 打接口，仍构成 CSRF→RCE 链。建议：脚本改 `sys.argv` 传参或 json+stdin，杜绝字符串拼接。

**B-2 · P2 · check/merge 对 pth 无大小/内容限制，torch.load 反序列化任意 pickle**
- 本地单用户场景可接受，但 torch.load 天然执行 pickle；torch≥2.6 默认 weights_only=True 可缓解——子进程 torch 版本未验证。

其余：路径注入已被 `os.path.basename` 挡住；无 SSRF 面。

## C 可靠性

**C-1 · P2 · `_RVC_PREVIEW_LOCK` 的 locked()+with 是 TOCTOU，防不住真并发**
- app.py:2353-2355。`locked()` 检查与 `with` 之间无原子性，两个请求可同时通过检查后串行排队——第二个不会 409 而是排队重跑。应改 `acquire(blocking=False)`。`global` 声明纯多余。

**C-2 · P2 · resume 的并发检查存在竞态窗口**
- app.py:2779-2784 检查后才置 pending 并启动线程；两个并发 resume（或 resume 与 /rvc/train 提交交错）都能通过检查，双 worker 对同一 rid 双写。应把"检查+置 pending"放进 RVC_TRAIN_LOCK 原子完成。

**C-3 · P2 · 读检查点 vs 训练写检查点的非原子竞态**
- `-sw 0` 下每次保存新文件，不覆盖同文件；但 preview 按 mtime 选最新时若训练恰在写该文件，torch.load 读到半截 → 500（自愈，重试即可）。概率低，定 P2。

**C-4 · P2 · job.json 读写无原子性/无锁**
- `_rvc_train_write` 直接 write_text；读到半截 JSON 时该任务当次被静默跳过。瞬时、自愈。

其余：子进程均有 timeout，无僵尸；preview 504 路径锁正常释放；check 端点无锁可被并发点击放大 CPU 占用（P3）。

## D 前后端契约

逐项核对一致：preview 返回 `{ok,url,source,model}` 与前端消费一致 ✔；check/merge/resume/active 字段契约均一致 ✔。

**D-1 · P2 · active 回显 done/error 任务后前端无条件对其 pollRvcTrain，产生误导性横幅/toast**
- index.html:2782-2786 对每个 item 无差别 `pollRvcTrain(j.id)`；命中 done 分支会 toast"音色制作完成"并写横幅，error 分支写"音色制作失败"横幅。刷新页面时，20 小时前的旧任务会逐个弹 toast。轮询本身第一拍即 clearInterval，不会死循环——但初次加载的 toast/横幅轰炸是实打实的回归。

**D-2 · P3 · 前端 merge 的 alpha 校验重复且 prompt 按空白 split**——文件名带空格无法输入，服务端把空格清洗为 `_`，行为不一致但无实害。

## E 与运行中任务的兼容性

- preview 强制 CPU（`CUDA_VISIBLE_DEVICES=""`），不碰 `_GPU_SEM`，不抢显存 ✔。但未限制 torch 线程数（只设了 OPENBLAS_NUM_THREADS=1），CPU 推理会与训练数据管线抢核，训练每轮耗时可能被拉长。P2。
- check/merge 子进程 `map_location='cpu'`，不触 CUDA ✔。
- resume 新 worker 与旧 worker（已死）的 `_GPU_SEM` 由 finally 释放 ✔；C-2 竞态下可双 worker 排队抢 SEM。
- separate_vocal 的 PyMSS 分离在 worker 内持 `_GPU_SEM` 后跑 cuda，与换声/生成互斥 ✔。

## F 修改引入的新缺陷（增量）

**F-1 · P2 · resume 不阻止对 done 任务重跑，绕过重名检查静默覆盖成品**
- app.py:2774 只拦 running/pending；done 任务也可 resume，重跑用同一 name 再训，`extract_small_model` 产物直接覆盖 `RVC_MODELS_DIR/<name>.pth`，`/rvc/train` 的重名 409 检查在 resume 路径完全绕过。

**F-2 · P2 · separate_vocal 推断以"dataset_clean 目录存在"为准**
- 目录一旦创建即永久残留（分离全失败也保留）；done 重跑场景会把原 dataset 再分离一次（浪费 GPU 时间）。逻辑基本自洽，主要风险挂在 F-1 上。

**F-3 · P3 · done/error 用 job.json mtime 卡 24h**——resume 刷新 mtime 使失败任务多挂 24h，符合预期，无实害。

**F-4 · P2 · preview_url 的持久性**——页面刷新后 restoreRvcTrainActive 拉的 job 无 preview_url 字段，播放器消失（文件还在，需重新点试听）。可感知、有降级，定 P2。

**F-5 · client.js（断连提示条）**——逻辑自洽，自动重载确已移除，恢复后自动熄灭提示条。未发现缺陷。

**F-6 · P2 · check 端点对同一模型连续两次完整 torch.load**（两段脚本各 load 一遍 400-800MB），体检一次双倍 IO/内存；无并发锁。P2 资源浪费。

## 未验证项

1. A-2 的实际后果取决于启动时进程 cwd 与 `<repo>/assets/` 是否恰好存在——未实际运行 merge 验证。
2. py312 内 torch 版本是否 ≥2.6（weights_only 默认值），影响 B-2。
3. i18n("是") 在该运行环境的实际返回值（A-3 的结论在两种情况下均成立，但机制细节未实测）。
4. preview 实测耗时（声称 20-40s）与 600s timeout 余量是否够 CPU 慢机。
5. `G_2333333.pth` 命名在 `-sw 0` 下的实际落盘名（静态核对通过、未跑训练验证）。

---

## P0/P1 摘要

- **P1（A-1）** 训练中试听必然失败：G_*.pth 是 `{"model",...}` 结构，infer/cli.py 要求 `weight/emb_g.weight` + `config` → 结构不兼容，未先过 extract_small_model。
- **P1（A-2）** 融合大概率永远失败：`process_ckpt.merge` 以相对路径落盘（process_ckpt.py:290），merge 子进程未设 `cwd=RVC_DIR` → 产物写错位置/异常被吞 → 恒 500。
- **P1（A-3）** 融合产物 f0 恒为 0：传 int，官方 merge 判 `f0 == i18n("是")` 恒 False。
- **P1（B-1）** python -c 脚本 `%` 裸拼路径，单引号未清洗 → 注入任意代码；配合 CORS 宽松构成 CSRF→RCE 链。
