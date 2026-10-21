# 第三方独立复核 · v10.17.1「纯 clone 也能跑」

- **复核对象**：`git diff --cached`（30 文件，基线 `9d0bb9b`），行号我自己重定位。
- **立场**：不采信蓝军报告的行号与结论，也不采信开发自述；每个结论都在代码或本机重跑里找到落点。
- **取证边界**：未启动/停止网关（7863）与引擎（8080），未提交任何生成/训练/分离任务，未改 `runtime/`、`cpp/`、`py312/`，未 commit/push/checkout/reset。所有实验在临时目录、内存替身或本地回环假服务里做；涉及 `runtime/` 的路径一律替换为临时目录（`m._SHIM_LOG`、`sr.ROOT`、`app._ENGINE_STATE`）。
- **基线复核**：本机全量 `614 tests, OK`（84.5 秒），与蓝军一致。
- **重跑口径**：蓝军总表里 `定级依据=实测` 的 B1–B8（B19 为推理项，按题不重跑）全部原样重跑，**8 条全部复现**，无一条"证据不成立"。差异只在量级（B5 3.09s vs 2.83s 同结论；B7 的爆炸半径被低估，见稽核）。

---

## 第一部分 · 蓝军意见逐项验证表

| 编号 | 蓝军意见（一句话） | 验证结果 | 证据（文件:行号） |
| --- | --- | --- | --- |
| B1 | 引擎全死时 health 仍报 gateway ok，前端点绿灯 | ✅ 属实落地 | 重跑得到 `{'gateway':'ok','audiocpp':{'status':'down'...}}`；main.py:230、242；static/index.html:4980 |
| B2 | 新入口 bat 用 `/dev/null`，全新 clone 永远报找不到 Python | ✅ 属实落地 | 重跑 rc=1，`where python` 明明在 PATH 上；一键补齐运行件.bat:17-18 |
| B3 | `--with-rvc` 缺 tensorboard/matplotlib，训练 import 即死 | ✅ 属实落地 | 重跑 ModuleNotFoundError；setup_runtime.py:54-55 无这两包，:548 无条件 [OK] |
| B4 | 分块下载不校验 206 窗口，拼出 N 倍垃圾仍报 [OK] | ✅ 属实落地 | 重跑 14 字节源文件拼成 56 字节，`verify_one -> (True,'ok')`；fetch_models.py:125-144,205-207 |
| B5 | async 端点里跑同步 start_audiocpp，事件循环冻结 | ✅ 属实落地 | 重跑 POST 3.0s 期间 GET health 3.09s（蓝军 2.83s，同结论）；main.py:331,371-373 |
| B6 | setup_runtime 假绿灯：跳过模型也写「[OK] 写歌出歌」，失败仍 exit 0 | ✅ 属实落地 | 重跑 `main() exit code = 0`，两组失败全打 [x] 仍 0；setup_runtime.py:556,629-636 |
| B7 | 全新 clone 缺 spec 时 main.py import 即抛 | ✅ 属实落地（**面被低估**） | 重跑 `Ran 1 test / FAILED (errors=1)`；补 spec+state 后**整个套件 24 failures + 5 errors**，不是蓝军写的 3 条；main.py:88-92 |
| B8 | RVC 补丁多套一层目录，4 个源全不存在，静默跳过 | ✅ 属实落地 | 重跑 4 个 `src_exists=False`，`find patches` 只有 `patches/rvc-infer/cli.py` 这一层；setup_runtime.py:56-57,520-524 |
| B9 | 测试条数 603/573/559/614 四套口径 | ✅ 属实落地 | README.md:233,316,379；README_EN.md:176,263；requirements.txt:7；实测 614 |
| B10 | README 写 `-text`，实际是 `text` | ✅ 属实落地 | README.md:340 vs .gitattributes:10 |
| B11 | 两个 `.bat` 是 UTF-8，测试名单不含 | ✅ 属实落地 | `secrets/local_env.example.bat`、`dsh-plugin/启动AI工作台.bat` 均可 UTF-8 解；tests/test_script_encoding.py:23-33 |
| B12 | CHANGELOG/版本没动，本轮改动仍报 v10.17.1 | ✅ 属实落地 | CHANGELOG.md:12；HEAD 日期 2026-10-17，本轮注释日期 2026-10-21 |
| B13 | `X-Seed` 是死契约（headers 键被降小写） | ✅ 属实落地 | 重跑 `dict(headers).get("X-Seed") -> None`；main.py:415；app.py:2639 |
| B14 | TRANSFORMERS_CACHE 修复依赖 import 顺序，测试只断言 env | ✅ 属实落地 | 实测 `transformers.utils.hub.TRANSFORMERS_CACHE` 在任何 configure 之前已是 `~/.cache/huggingface/hub`（import 期求值）；src/sheetsage_pt.py:38；tests/test_sheetsage_cache.py:33 |
| B15 | sitecustomize 只在不存在时写，老机器拿不到兜底 | ✅ 属实落地 | py312 里那份含 5 行脚本**写不出来**的注释+代码，`patches/rvc-clean-guards/sitecustomize.py` 里没有 → 证明它来自手工而非本安装器；setup_runtime.py:534 |
| B16 | 已有 exe 就信请求的后端；server.json 不覆盖却无条件重写 state | ✅ 属实落地 | setup_runtime.py:260-265,324-339 |
| B17 | 缺 state 时 shim 按 cpu、app 按 cuda | ✅ 属实落地 | main.py:144 vs app.py:474 |
| B18 | Vulkan 没有 CPU 兜底（预检/回切都以 cuda 为门） | ✅ 属实落地 | app.py:2580,2618 |
| B19 | GGUF 只查 magic+最小字节；`--workers 0` 无校验 | ✅ 属实落地 | fetch_models.py:83-89,177 |
| B20 | LICENSE 自研清单漏了新文件 | ✅ 属实落地 | LICENSE:6 |
| B21 | 补丁跟上游 main 并无条件覆盖 | ✅ 属实落地 | setup_runtime.py:481,520-531 |

> 两处需要更正的蓝军措辞（不影响定级）：① B1 说启动脚本"看到 200 就进 :ready"——`:waitloop` 那条 curl **不检查状态码**（`curl` 无 `-f` 时对 503 也回 0，实测），任何 HTTP 响应都算就绪，见 scripts/启动音乐工作台.bat:127-128；② B7 的"3 条红"是只跑 `test_shim_main.py` 的结果，全套件是 29 条红。

---

## 第二部分 · 新发现的缺陷

### T 项总表

| 编号 | 级别 | 定级依据 | 一句话 | 维度 | 位置（文件:行号） |
| --- | --- | --- | --- | --- | --- |
| T1 | 🟠 | 实测 | `/health` 只给 2 秒预算，把"忙"判成"死"：出歌期间页脚翻红，出歌端点还会去 Popen 第二个引擎 | 3 正确性 / 5 并发 / 17 文档 | main.py:204-206,371-373；static/index.html:4980 |
| T2 | 🟠 | 实测 | `engine_backends()` 把一次探测失败永久缓存成「只有 cpu」，`_restart_audiocpp` 于是给 CUDA 机器传 `--backend cpu`，页面仍报 cuda | 3 正确性 / 14 迁移 | app.py:488-505,604；app.py:749-753 |
| T3 | 🟠 | 实测 | `--with-rvc` 的权重下载：`hf_transfer` 不在任何清单里 + 调用无 try/except，新机器装到这一步必崩 | 12 依赖 / 11 兼容 | setup_runtime.py:501,504；requirements.txt（0 处） |
| T4 | 🟠 | 实测 | `ensure_engine` 的"已有 exe"分支不验参数表：缺 `cpp/model_specs/yue2.json` 也打 [OK] 写歌出歌，而 main.py import 期就 raise | 4 错误处理 / 16 发布工程 | setup_runtime.py:260-265,295-298；main.py:88-92 |
| T5 | 🟠 | 实测 | 蓝军 B1 的备选修法「health 回 503」会打瞎看门狗，而看门狗的免死名单里没有出歌 | 2 架构 / 4 错误处理 | watchdog.py:100-110,60-64,350-362；main.py:230 |
| T6 | 🟡 | 推理·待实测 | 出歌 CPU 回退的收尾无条件写 `engine_state.backend="cuda"`，在 Vulkan 机器上把状态写歪，新测试会因此变红 | 14 迁移 | app.py:2658-2662；tests/test_shim_main.py:196-215 |
| T7 | 🟡 | 推理·待实测 | `PYTHONIOENCODING=cp<从 chcp 抠出来的值>` 无校验，畸形值让 Python 在初始化阶段 Fatal，启动脚本第一步就死 | 11 兼容 | scripts/启动音乐工作台.bat；一键补齐运行件.bat |
| T8 | 🟡 | 实测 | `.gitattributes` 的 `*.ps1` 是一条没有任何属性的空规则，什么也没保证 | 16 发布工程 / 17 文档 | .gitattributes:16 |
| T9 | 🟡 | 实测 | `ensure_rvc` 的下载异常裸奔（网络不通就 traceback，不是 [x]），5 个 pymss 模型全失败仍打 [OK] 并 return True | 4 错误处理 | setup_runtime.py:504,544-549 |
| T10 | 🟡 | 实测 | README「后端写错时引擎会当场启动失败，不会静默降级」与本轮 `resolve_backend` 的实际行为相反 | 17 文档一致性 | README.md（FAQ「没有 NVIDIA 显卡能跑吗」段） |

---

### T1 · 🟠 health 的 2 秒预算把"忙"判成"死"：页脚翻红，出歌端点还会拉起第二个引擎

**问题**
`_engine_get("/health", timeout=2.0)`（main.py:229）与 `_engine_alive(timeout=2.0/1.5)`（main.py:123,371）把"2 秒内没答"直接当成 down。而这个文件自己在 main.py:204-206 写下了校准值：**"生成期间引擎被 CPU 占满，只读请求也要等十几秒"**（并据此把 health 从 19.7 秒优化到单次请求）。同一个预算下 `/v1/models` 要十几秒，`/health` 走同一条 HTTP 栈，没有理由认为它一定在 2 秒内。

**触发链**
1. 引擎活着、正在算（或正在加载权重）→ `/health` 3 秒才回；
2. 页脚每轮 `refreshHealth()` → `/api/health` 里 `_engine_get` 2 秒超时 → `audiocpp.status="down"` → static/index.html:4984 走 else 分支 → **"引擎未就绪"红字**，而歌正在生成；
3. 同一时刻若有 `POST /api/music/generate`（批量第二首、回退重试、或用户在另一个窗口点生成），`_engine_alive(2.0)`→False → `start_audiocpp()` → 它只探 `/health`（1.5 秒）也判死 → **直接 `Popen` 第二个 `audiocpp_server.exe`**（main.py:172-174），第二个进程抢 8080 失败退出；随后脚本还要在 90 秒里等 `/health`（1.5 秒预算，忙引擎永远等不到）→ 最后仍回 502。

**可达性**：第 1 步就是出歌的常态（作者自己写的 2 秒→19.7 秒的优化记录）。第 3 步受 `_GEN_JOB`/`_GPU_SEM` 串行化限制，不是每首歌都撞得上；但"页脚在整首歌期间报红"是**每首歌**都会发生的（只要 `/health` 超过 2 秒）。

**定级**：🟠。这是红线①的反面：不是"死了报活"，而是"活着报死"，代价是用户以为引擎挂了、去点"停止"或强杀网关。第二个引擎的 Popen 还会往同一个 `audiocpp_server.log` 里追加、多占一份内存。

**修复方向**：健康判据要跟"生成中"这个已知状态解耦——把 `/health` 的预算提到与实测延迟同量级（或让引擎暴露一个纯内存的 `/ping`），并在 `music_generate` 里**先看进程是否在跑、端口是否有监听**，再决定要不要自启；至少给 `start_audiocpp` 加"已在运行则不 Popen"的判据（现在只有 `/health` 一条）。

**复现命令**

```bash
./py312/python.exe - <<'PY'
import importlib.util, json, os, sys, threading, time, tempfile, http.server, socketserver
from pathlib import Path
os.environ["YUE2_NO_ENGINE_AUTOSTART"] = "1"
tmp = tempfile.mkdtemp()
spec = importlib.util.spec_from_file_location("shimT", Path("main.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m._SHIM_LOG = Path(tmp) / "shim.log"
spawned = []
m.subprocess.Popen = lambda *a, **k: spawned.append(a[0]) or object()
m.settings.audiocpp_ready_timeout_sec = 0.6
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        time.sleep(3.0)                      # 引擎活着，只是忙
        body = json.dumps({"status": "ok", "backend": "cpu", "models": 1}).encode()
        self.send_response(200); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
m.settings.audiocpp_base_url = f"http://127.0.0.1:{srv.server_address[1]}"
from fastapi.testclient import TestClient
c = TestClient(m.app)
t=time.time(); j = c.get("/api/health").json(); print(f"/api/health ({round(time.time()-t,2)}s) -> audiocpp={j['audiocpp']}")
t=time.time(); r = c.post("/api/music/generate", json={"style":"s","lyrics":"a"})
print(f"POST /api/music/generate ({round(time.time()-t,2)}s) ->", r.status_code, r.json())
print("Popen 被调用", len(spawned), "次 ->", spawned[:1])
srv.shutdown()
PY
```

实际输出：

```text
/api/health (2.37s) -> audiocpp={'status': 'down', 'backend': None, 'models': 0, 'loaded': None}
[main.py] 10-21 18:58:04 ⚠ 引擎已拉起但超时没回 /health，交给 app.py 的按需自检
POST /api/music/generate (7.85s) -> 502 {'detail': 'audio.cpp 引擎不可用，无法生成'}
Popen 被调用 1 次 -> [['...\\cpp\\audiocpp_server.exe', '--config', '...\\cpp\\server.json', '--backend', 'vulkan', '--log']]
```

---

### T2 · 🟠 一次探测失败被永久缓存，CUDA 机器静默掉到 CPU，页面还报 cuda

**问题**
`engine_backends()`（app.py:481-505）在**任何**异常（exe 不在、`--version` 超时 30 秒、输出格式不是 `backends:` 开头、只打到 stderr）下都落成 `found or ["cpu"]`，并写进 `_ENGINE_BACKENDS_CACHE`——**之后永不再问**。而本轮新加的 `_restart_audiocpp` 用它的结果决定命令行：`mode, note = resolve_backend(...)`；`args += ["--backend", mode]`（app.py:604-607）。

**触发链**
1. 用户先起网关、后装引擎（README 的三步开唱里，step 2 装引擎与 step 3 起网关之间没有任何约束；看门狗也可能在安装前就把网关拉起来）；
2. 页面一打开就 `fetch("/api/backend/mode")` → `engine_backends()` → exe 不在 → `["cpu"]`，**缓存钉死**；
3. 引擎装好、页面换成 CUDA 构建、用户切模型或点「切CPU/切GPU」→ `_restart_audiocpp` → `resolve_backend("cuda")` 返回 `("cpu", "该引擎构建既没有 CUDA 也没有 Vulkan…")` → 引擎被 `--backend cpu` 拉起；
4. `backend_mode_get()` 仍按 `engine_state` 报 `mode="cuda"`（页脚/模式行写"GPU 模式"），只有 stderr 里一行 `logger.warning`；
5. 之后每一首歌都在 CPU 上跑——按 README 的锚点是**十几小时**，而用户以为自己在 GPU 上。

本轮之前 `_restart_audiocpp` 在 cuda 模式下**不传** `--backend`，由 server.json 决定，不会出现这个静默降级。这是本轮改动引入的行为变化。

**可达性**：只要在第一次 `engine_backends()` 之前引擎不可用/不响应就成立（安装顺序、杀软扫描 460MB PE、慢盘）。缓存没有 TTL、没有失效点，网关活多久就错多久。

**定级**：🟠。红线①的"假绿灯"：页面说 GPU、引擎跑 CPU，且没有任何面向用户的提示。

**修复方向**：探测失败不要落成 `["cpu"]`（应返回 None 并让调用方退回"不传 `--backend`、信 server.json"）；缓存要么带 TTL，要么在每次 `_restart_audiocpp`/后端切换前失效；`_restart_audiocpp` 不该把"探测失败"当成"这个构建只有 cpu"。

**复现命令**

```bash
./py312/python.exe - <<'PY'
import json, os, sys, tempfile, types
from pathlib import Path
os.environ["YUE2_NO_ENGINE_AUTOSTART"] = "1"   # 不让 app.py 去 import 真网关（会拉引擎）
sys.path.insert(0,'.'); sys.path.insert(0,'src')
if "main" not in sys.modules:                 # 不让 app.py 去 import 真网关（会拉引擎）
    from fastapi import FastAPI
    st = types.ModuleType("main"); st.app = FastAPI()
    st.settings = types.SimpleNamespace(open_browser=True); sys.modules["main"] = st
import app
tmp = Path(tempfile.mkdtemp())
app._ENGINE_STATE = tmp/"engine_state.json"
app._ENGINE_STATE.write_text(json.dumps({"backend":"cuda"}), encoding="utf-8")
app._hide_engine_windows_once = lambda: None
app._ENGINE_BACKENDS_CACHE = None
app.AUDIOCPP_BIN = tmp/"audiocpp_server.exe"  # 引擎还没装
print("A) 未安装时 engine_backends() =", app.engine_backends())
class R:
    def __init__(self, out): self.stdout=out; self.returncode=0
app.subprocess.run = lambda *a, **k: R("audio.cpp 0.9.2\nbackends: cpu,cuda\n")  # 现在装好了
print("B) 装好之后 engine_backends() =", app.engine_backends())
print("C) resolve_backend('cuda') =", app.resolve_backend("cuda"))
calls=[]
app.subprocess.Popen = lambda args, **k: calls.append(args) or object()
app._restart_audiocpp(str(tmp/"server.json"), backend="cuda")
print("D) 实际拉起 =", calls[-1])
print("E) backend_mode_get() =", {k:v for k,v in app.backend_mode_get().items() if k in ('mode','available')})
PY
```

实际输出：

```text
后端已按引擎能力改写：该引擎构建既没有 CUDA 也没有 Vulkan（可用：cpu），只能用 CPU
A) 未安装时 engine_backends() = ['cpu']
B) 装好之后 engine_backends() = ['cpu']
C) resolve_backend('cuda') = ('cpu', '该引擎构建既没有 CUDA 也没有 Vulkan（可用：cpu），只能用 CPU')
D) 实际拉起 = ['...\\audiocpp_server.exe', '--config', '...\\server.json', '--backend', 'cpu']
E) backend_mode_get() = {'mode': 'cuda', 'available': ['cpu']}
```

---

### T3 · 🟠 `--with-rvc` 的权重下载在新机器上必崩：`hf_transfer` 没进任何清单 + 调用无保护

**问题**
`ensure_rvc` 在下载 hubert/rmvpe/pretrained 前无条件设 `os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"`（setup_runtime.py:501），随后 `snapshot_download(...)`（:504）**没有任何 try/except**。而 `hf_transfer` 既不在 `requirements.txt`，也不在 `requirements-transcribe.txt`（`grep hf_transfer requirements*.txt` → 0 处），huggingface_hub 0.36 在 `file_download.py:396-405` 会因此抛 `ValueError`。

**触发链**
1. 全新机器 `一键补齐运行件.bat --with-rvc`（README 明说这一条命令会把权重"拉下来"）；
2. pip 装的是 `requirements.txt`（11 个包）与 RVC_PKGS（7 个包），**没有 hf_transfer**；
3. 第一个文件下载时 `import hf_transfer` 失败 → `ValueError: Fast download using 'hf_transfer' is enabled ... Try pip install hf_transfer`；
4. 异常冒出 `ensure_rvc` → `main()` 没有兜 → 整个安装脚本 traceback 退出；
5. `runtime/rvc/assets/hubert_base|rmvpe|pretrained_v2` 是空的 → ⑨ 换声 / ⑦ 训练第一次调用就报模型缺失。而补丁步骤在下载**之后**，所以连补丁也不会应用。

**可达性**：任何一台按文档走的干净机器（`hf_transfer` 不是 huggingface_hub 的依赖）。本机之所以没事，是作者手工装过——同一类"作者机上绿"。

**定级**：🟠。这是 README 承诺的"一条命令补齐 ⑦⑨⑩"在干净机器上的硬阻断，且失败形态是 traceback 而不是脚本自己文档承诺的 [x]+缺什么。

**修复方向**：把 `hf_transfer` 写进清单（或删掉 `HF_HUB_ENABLE_HF_TRANSFER` 这行，本来 `snapshot_download` 自带多线程）；`snapshot_download` 包 try/except 并打 `[x]`；把补丁应用移到下载之前，让"至少能跑 rmvpe"不受下载失败牵连。

**复现命令**

```bash
./py312/python.exe - <<'PY'
import sys, tempfile
sys.modules["hf_transfer"] = None            # 模拟这台机器没装（requirements 里确实 0 处）
from huggingface_hub import constants, file_download
constants.HF_HUB_ENABLE_HF_TRANSFER = True    # == setup_runtime.py:501 设的那个变量
try:
    file_download.http_get("http://127.0.0.1:9/never-reached", temp_file=tempfile.mktemp())
except Exception as e:
    print(type(e).__name__, ":", str(e)[:160])
PY
grep -c "hf_transfer" requirements.txt requirements-transcribe.txt
```

实际输出：

```text
ValueError : Fast download using 'hf_transfer' is enabled (HF_HUB_ENABLE_HF_TRANSFER=1) but 'hf_transfer' package is not available in your environment. Try `pip install hf_transfer`.
requirements.txt:0
requirements-transcribe.txt:0
```

---

### T4 · 🟠 安装器的"引擎已存在"分支不验参数表：[OK] 写歌出歌，而工作台根本起不来

**问题**
`ensure_engine` 下载路径结束时会检查 `cpp/model_specs/yue2.json`（setup_runtime.py:295-298，注释写着"main.py 会拒绝启动"），但**"已存在 exe"那条早退分支不做这个检查**（:260-265）。而 `main.py` 在 import 期就因为缺这份表 `raise RuntimeError`（main.py:88-92），`app.py:46` 又无条件 `import main`。

**触发链**
1. `cpp/` 来自旧包/半解压/手工拷 exe（`model_specs/` 没跟着）；
2. 重跑 `一键补齐运行件.bat` → `ensure_engine` 打"已存在 —— 跳过"，返回 `"cpu"`（成功）；
3. 后面每一步都成功，`report()` 无条件打 `[OK] 写歌出歌`（:556），exit 0；
4. 双击启动脚本 → `python -s app.py` → `import main` → RuntimeError → 网关起不来；看门狗的"启动即死熔断"重试 3 次后放弃，用户只看到"网关未就绪"。

**可达性**：作者这台机器恰好有 spec，所以看不出；对任何"只更新了 exe"或"解压中断"的现场都成立。

**定级**：🟠。与 B6 同族但是**不同的分支**：B6 说的是"跳过模型也打 OK"，这条是"引擎目录不完整也打 OK"，后果更重（整个工作台不可启动，而不是某个功能缺失）。

**修复方向**：把 spec/exe 的存在性检查提到早退分支之前（一处 `_engine_sanity()` 两处调用）；`report()` 的"写歌出歌"以"exe+spec+server.json+主权重"四项全在为准。

**复现命令**

```bash
./py312/python.exe - <<'PY'
import importlib.util, tempfile
from pathlib import Path
spec = importlib.util.spec_from_file_location("sr", "scripts/setup_runtime.py")
sr = importlib.util.module_from_spec(spec); spec.loader.exec_module(sr)
tmp = Path(tempfile.mkdtemp()); sr.ROOT = tmp
cpp = tmp/"cpp"; cpp.mkdir(); (cpp/"audiocpp_server.exe").write_bytes(b"not-a-real-exe")
print("spec before ->", (cpp/"model_specs"/"yue2.json").is_file())
print("ensure_engine(cpu) ->", repr(sr.ensure_engine("cpu", "12.4", None)))
print("spec after  ->", (cpp/"model_specs"/"yue2.json").is_file())
PY
sed -n '88,92p' main.py
```

实际输出：

```text
spec before -> False
--- 3/5 audio.cpp 推理引擎 cpp\ ---
[setup]   已存在：...\cpp\audiocpp_server.exe —— 跳过
ensure_engine(cpu) -> 'cpu'
spec after  -> False
if not _ENGINE_OPTIONS:
    raise RuntimeError(
        "找不到 audio.cpp 的 YuE2 参数表（cpp/model_specs/yue2.json）。"
```

---

### T5 · 🟠 蓝军 B1 的"health 回 503"修法会打瞎看门狗，而免死名单里没有出歌

**问题**
这只是"修法陷阱"，当前代码并未这么写——但蓝军 P0 第 2 条给的就是这条路（"至少让 `/api/health` 在 `audiocpp.status === "down"` 时回 `gateway: "degraded"`"以及更激进的 503）。看门狗把 5xx 当死亡（watchdog.py:100-110 `return r.status < 500` / `e.code < 500`），而它的"长任务免死"只认 4 个文件（:60-64），**没有出歌的 `output/*.json`**。

**触发链**
1. 照修法把 health 改成"引擎 down → 503"；
2. 出歌中（T1 已证 `/health` 超过 2 秒就判 down）→ health 回 503；
3. 看门狗 5 轮短探（50 秒）+ 90 秒深探（也是 503 → `< 500` 为假）→ 判假死；
4. `long_job_pardon()` 在 `rvc/trains|logs|jobs`、`data/batch_state.json` 里找"最近 360 秒被写"的文件——出歌不写这些 → 无豁免；
5. `taskkill` 网关 → 正在等引擎的那条 `/api/music/generate` 断开，**整首歌报废**（以及 B5 那 90 秒冻结里用户本来就点不动的"终止"）。

**可达性**：只要实施该修法，每一首 `full+8 步` 以上的歌都会经过"引擎忙→health 503→击杀"这条链。

**定级**：🟠（作为修法陷阱）。真正安全的修法在**前端**：`if (be.status === "ok")` 单独判绿；HTTP 状态码保持 200，只把 `gateway` 改成 `"degraded"` 并同步 :4980。若确实要动状态码，必须同时把出歌产物加进 `LONG_JOB_GLOBS`。

**修复方向**：见上；并给 `long_job_pardon` 补 `output/*.json`（出歌每完成一首会落盘 meta）。

**复现命令**

```bash
./py312/python.exe - <<'PY'
import threading, http.server, socketserver, importlib.util, subprocess
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        self.send_response(503); self.send_header("Content-Length","0"); self.end_headers()
srv = socketserver.TCPServer(("127.0.0.1",0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}/api/health"
spec = importlib.util.spec_from_file_location("wd", "watchdog.py")
wd = importlib.util.module_from_spec(spec); spec.loader.exec_module(wd)
print("watchdog.probe(503) ->", wd.probe(url))
print("LONG_JOB_GLOBS =", wd.LONG_JOB_GLOBS)
r = subprocess.run(["C:/Windows/System32/curl.exe","-s","--noproxy","*","-m","5",url],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print("启动脚本那条 curl 对 503 的退出码 =", r.returncode)
srv.shutdown()
PY
```

实际输出：

```text
watchdog.probe(503) -> False
LONG_JOB_GLOBS = ('rvc/trains/*/job.json', 'rvc/logs/*/train.log', 'rvc/jobs/*/converted.wav', 'data/batch_state.json')
启动脚本那条 curl 对 503 的退出码 = 0
```

三行合起来说明：503 会让**看门狗**杀网关，却不会让**启动脚本**变严格（它根本不看状态码）——所以这个修法一边不解决 B1 的启动面，一边制造新的击杀面。

---

### 🟡 明细（不展开）

- **T6**：`if used_cpu_fallback and backend_mode() == "cpu": _engine_state_write({"backend": "cuda"})`（app.py:2658-2662）在 Vulkan 机器上会写下一个引擎并不会采用的后端（`_restart_audiocpp` 随后 resolve 成 vulkan），于是 `server.json=vulkan / engine_state=cuda` 长期不一致，而 `tests/test_shim_main.py:196-215` 正是断言这两者必须相等——**生产代码自己能把这个新测试弄红**。触发需要"state 曾是 cuda（缺文件默认值/B17 或升级残留）+ 一次显存类错误"。
- **T7**：`for /f "tokens=2 delims=:" %%p in ('chcp')` → `PYTHONIOENCODING=cp%OEMCP%:backslashreplace`，**没有任何校验**。实测 `cp:` 与 `cp850.` 都让 Python 在 `init_stdio_encoding` 阶段 Fatal（rc=1，一行 Python 都跑不起来）。只要 `chcp` 的输出格式不是 `<任意>: <纯数字>`（本地化标点/后缀/解析落空），整个启动脚本第 1 步就死，且报错里不会出现"码页"二字。
- **T8**：`.gitattributes:16` 的 `*.ps1` 后面没有任何属性；`git check-attr -a scripts/开放局域网.ps1` 回空、`git ls-files --eol` 的 `attr/` 也是空——这条规则**什么也没做**，注释里"ps1 必须带 BOM"完全靠人守（BOM 本身确实在：实测 `b"\xef\xbb\xbf"`）。
- **T9**：`ensure_rvc` 的 `snapshot_download` 无 try/except——本机离线重跑时它抛 `LocalEntryNotFoundError` 直接终止脚本（不是 `[x]` + `return False`），而 `一键补齐运行件.bat` 的失败提示是"上面最后一条带 x 的就是原因"，此时**一条 [x] 都没有**；同一函数里 5 个 pymss 模型的下载结果只用于日志（:544-547），之后 :548 仍无条件打 `[OK] RVC 运行件就绪` 并 `return True`。
- **T10**：README 的 FAQ 写"`engine_state.json` 的 backend 必须与实际构建一致……写错时引擎会当场启动失败，不会静默降级"；本轮 `_restart_audiocpp` 引入 `resolve_backend` 之后，写错会被**改写**（只留一行 logger.warning），这句话正好说反了。

---

## 第三部分 · 蓝军覆盖度稽核

**整项跳过 / 走过场的维度**

| 维度 | 蓝军表态 | 实际问题 |
| --- | --- | --- |
| 3 正确性 | 只列 B4/B8/B13/B18，并称参数映射"逐项读过、未发现问题" | 漏了 T1（健康判据把忙当死）与 T2（能力探测失败被缓存成 cpu）。我复核了映射：`_pick_payload` 的 27 项白名单与 app.py 送的字段名逐个对得上（`abc_*`/`semantic_*` 同名、`cfg_scale→guidance_scale` 唯一别名），这部分蓝军结论成立 |
| 4 错误处理 | 列了 B1/B6/B8/B2 | 漏了 T4（安装器早退分支不验 spec）、T9（裸异常 + 无条件 [OK]）。蓝军把"安装器说假话"只落在"可选件失败仍 exit 0"上，没查"引擎目录不完整"这条 |
| 5 并发与竞态 | 只有 B5，另把"shim 绕过 `_GPU_SEM`"写进未验证 | 漏了 T1 的第二个引擎 Popen。蓝军说"`_GPU_SEM` 不覆盖 shim 直连端点"——实测下来单首/批量都经 `_chain_runner` 持锁，前端也没有直连 `/api/music/generate` 的调用点，这条风险实际很低（我按"无异议"处理） |
| 10 测试有效性 | 3 条变异全杀（附录 A），自称"本机配置断言"问题在 B7 | 我另做 3 条变异**全部被杀**（见下），测试本身有效；缺口是**覆盖盲区**：没有任何用例覆盖 `_restart_audiocpp` 新引入的 `resolve_backend`、`engine_backends()` 的失败/缓存语义、以及"引擎忙时 health 说什么"。另外 `tests/test_shim_main.py:240` 把 B1 的假绿灯**写成了断言**（蓝军已指出） |
| 12 依赖与供应链 | 重点查了 RVC 推理依赖与 numpy/librosa/torch | 漏了 `hf_transfer`（T3）。这条比 tensorboard 更致命：它让整条 `--with-rvc` 在下载阶段就崩，而不是"装完少一个包" |
| 11 兼容性 | 实测 `infer/cli.py --help`、`pymss.cli --help` 可跑，numpy/librosa 调用点用关键字参数 | 结论我复核**成立**：`librosa_mel_fn(sr=..., n_fft=...)` 与 `mel(sr=...)` 全是关键字（runtime/rvc/infer/rmvpe.py:423、train/mel_processing.py:98）；`astype(..., copy=False)`/`np.array(..., copy=False)` 作用在 ndarray 上。漏的是 T7（码页推导）与 T3（hf_transfer）这类"环境级"兼容 |
| 14 迁移与状态演进 | B15/B16/B17 | 漏了 T6（回退收尾把 vulkan 机器的 state 写成 cuda）。B15 我另有一条更硬的证据：**本机装着的 sitecustomize 含 5 行当前脚本写不出来的注释+代码**，而仓库 `patches/` 那份没有——正好证明"作者手工补过、安装器复制不出来" |
| 16 发布工程 | B12/B9/B8，并称暂存区没带进 `runtime/`、`cpp/` | 漏了 T8（`*.ps1` 空规则）。暂存区洁净这条我核过：`git diff --cached --name-only` 30 个文件全是源码/脚本/文档 |
| 17 文档一致性 | 覆盖最全（B1/B3/B6/B8/B9/B10/B11/B12） | 漏了 T10（"不会静默降级"与本轮 resolve 相反）与 README:225「纯 clone 现在真的能跑起来了」的过度声明（B2 让第 2 步就断，T3 让 `--with-rvc` 断） |
| 1 需求一致性 | 判"发现问题" | 同上；另外"三步开唱"第 2 步在没有 uv、也没有 py launcher 的机器上无路可走（`local_312()` 只认 `py -0p`），文档没写这个前提 |
| 19 许可证合规 | 判"未发现新增冲突"，只有 B20 | 复核无异议：`main.py` 是按 spec 重写，`patches/` 是上一轮就有的 MIT 派生。**已覆盖，无异议**（LICENSE:6） |
| 2 架构与设计 | 判"发现问题" | 结论成立；但"health 语义与前端 OR 条件耦合"只写了前端一侧，没写**看门狗也吃这个状态码**（T5），这才是"health 语义不能随便改"的真正原因 |

**抽查蓝军的 🟡/归组条目，实为更高等级的**

- **B18（Vulkan 无 CPU 兜底）应为 🟠**：本轮把 Vulkan 变成"没有 N 卡时的首选"（README 首段、`report()` 的 insert），却把"显存不足自动切 CPU 重试"整段留在 `backend_mode()=="cuda"` 门内（app.py:2580,2618）。核显走统一内存，README 自己都提醒"跑歌时别同时开大程序"——也就是说**新默认路径丢掉了老路径有的唯一自动兜底**，代价是一首几小时/十几小时的歌直接失败。这不是文案问题。
- **B7（干净 clone 测试）影响面被低估**：蓝军只贴了 `test_shim_main.py` 的 3 条红；我按同一方法跑**整个套件**是 `Ran 614 tests ... FAILED (failures=24, errors=5, skipped=2)`，红的还包括 `test_lora_*`/`test_rvc_*` 等**旧**用例（都依赖本机 `cpp/server.json`）。"别人的机器上 614 全绿"这个前提完全不成立，程度比报告写的重。
- **B16 边界偏高**：`ensure_server_json` 不覆盖 server.json 却无条件重写 engine_state，确实能造出"配置说 vulkan、状态说 cpu"，但触发需要用户显式换 `--backend` 重跑；维持 🟡 可接受（我倾向记为"应在 P1 修"）。

**我复核后维持蓝军结论、不再重复的**：B1/B2/B3/B4/B5/B6/B8/B9/B10/B11/B12/B13/B14/B15/B17/B19/B20/B21（逐条实证见第一部分）。

**变异验证（我自己做的 3 条，全部被杀）**

| 变异 | 位置 | 结果 |
| --- | --- | --- |
| 去掉 safetensors 的长度一致性检查（`if size != need` → `if False`） | scripts/fetch_models.py:101-103 | `FAIL: test_truncated_file_is_rejected_even_when_it_clears_min_size` |
| 「切GPU」目标改回硬编码 cuda | static/index.html | `FAIL: test_switch_target_comes_from_the_api` |
| `_pick_payload` 的 `v == ""` 过滤删掉 | main.py:261 | `FAIL: test_empty_values_are_omitted_not_sent_as_blanks` |

命令模板（在 `git checkout-index` 导出的临时副本里改坏再跑，原仓库未改动）：

```bash
rm -rf /tmp/yue2-mut2 && mkdir -p /tmp/yue2-mut2 && git checkout-index -a --prefix=/tmp/yue2-mut2/
cd /tmp/yue2-mut2 && ./py312/python.exe -m unittest discover -s tests -p "test_fetch_models.py"
```

---

## 第四部分 · 发布门结论

**能不能 push：不能。**

1. **硬性阻塞（必须修完才能发）**
   - `git status` 仍有 30 个文件停在暂存区（未提交），`CHANGELOG.md` 顶部还是 `[v10.17.1] - 2026-10-17`，版本号未按 README 的"主.日.序"规则升到 `v10.21.x` → 版本与改动不一致（B12）。
   - **B2**：`一键补齐运行件.bat:17-18` 的 `>/dev/null`，让"纯 clone"的第 2 步对任何没有 `py312` 的机器都恒失败——这正好是本轮标题承诺的那一步。
   - **B1 + T1**：health 的绿灯在两个方向都说假话（死了说活、活着说死），而前端与启动脚本都吃它。
   - **B4**：下载器能把 2.7GB 拼成 N 倍垃圾并打 `[OK]`，随后 `os.replace` 覆盖掉旧文件。
   - **B7**：干净 clone 上整套件 `24 failures + 5 errors`，README 的"614 全绿"只在作者机器上成立。
   - **T3**：`--with-rvc` 在干净机器上必崩（hf_transfer），而 README 对外承诺"一条命令补齐 ⑦⑨⑩"。
   - **T4**：安装器在"引擎已存在但缺 spec"时打 `[OK] 写歌出歌`，而工作台起不来。
   - `main.py:88-92` 的 import 期 `raise` 把"缺一个可选文件"升级成"整个网关无法 import"——至少要先降级成"generate 返回 503"再谈发布。

2. **应在发布前顺手修（不单独阻塞，但都是本轮引入的）**：T2（静默掉 CPU）、T5（若真去改 503）、T9、T10、T6、T8；B18 建议按 🟠 处理。

3. **保留意见（我没能验证的）**
   - 真实出歌全链路（`/api/music/generate` → 引擎 → WAV）没跑：禁忌不允许提交生成任务。因此 T1 的"频率"（真实引擎在生成期间 `/health` 到底几秒）只有 main.py 自己的注释作依据，我复现的是"只要超过 2 秒就误判"这个机制，不是这个频率。
   - 引擎 `--version` 我**没有**在本机重跑（避免动到正在跑的引擎进程）；`engine_backends()` 的解析正确性依赖 README/蓝军记录的 `backends: cpu,vulkan` 输出形状，我用替身验证的是"解析失败/缓存"这一侧。
   - Vulkan 显存/统一内存 OOM 的真实形态、训练全程、`--with-transcribe` 的 1GB 下载与转谱、英文 Windows 下的 GBK `.bat`，均未跑（与蓝军边界相同）。
   - 与闭源 `main.pyd` 的历史行为对比仍然无法做（pyd 不在仓库里），B1 的"新引入 vs 沿袭"未证。

*本报告所有命令均在本机 Git Bash + `./py312/python.exe` 3.12.9 下原样执行过；两条 `推理·待实测`（T6/T7）已如实标注。*
