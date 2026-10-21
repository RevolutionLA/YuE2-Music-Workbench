# 蓝军评审 · v10.17.1「纯 clone 也能跑 + 核显 Vulkan + 补齐换声/分离/逐字对齐」

- **审查对象**：暂存区 `git diff --cached`（30 文件，+2714/−209；基线 `9d0bb9b`），工作区无其它改动。
- **审查立场**：不采信开发团队的自述与注释。注释写"已修复"，去代码里确认；文档写"测试全绿"，自己跑。
- **取证边界（先声明）**：未启动/停止网关（7863）与引擎（8080），未提交任何生成/分离/训练任务，未改 `runtime/`、`cpp/`、`py312/`，未 `commit/push/checkout/reset`。所有实验都在临时目录或内存替身里做（`TestClient`、本地回环病态 HTTP 服务、`git checkout-index` 导出的临时副本）。因此"出歌全链路在 shim 下真能出片"这一类**没有被验证**，见文末「未验证项」。
- **已跑过的全量回归**：`614 tests, OK`（95.4 秒），基线干净。

---

## 缺陷总表

| 编号 | 级别 | 定级依据 | 一句话 | 维度 | 位置（文件:行号） |
| --- | --- | --- | --- | --- | --- |
| B1 | 🔴 | 实测 | 引擎全死时 `/api/health` 仍回 `gateway:"ok"`，前端据此点亮绿灯「引擎就绪」 | 4 错误处理 / 17 文档一致性 | main.py:230；static/index.html:4980 |
| B2 | 🔴 | 实测 | 新入口 `一键补齐运行件.bat` 用 `>/dev/null`，全新 clone 上永远报「找不到任何 Python」并退 1 | 3 正确性 / 11 兼容性 | 一键补齐运行件.bat:17-18 |
| B3 | 🟠 | 实测 | `--with-rvc` 宣称 ⑦ 可用，但 tensorboard/matplotlib 没装，训练进程 import 即死 | 12 依赖与供应链 | scripts/setup_runtime.py:54,548；runtime/rvc/train/train.py:60 |
| B4 | 🟠 | 实测 | 分块下载不校验 206 响应长度/Content-Range，病态镜像拼出 N 倍垃圾仍报 `[OK]` | 3 正确性 | scripts/fetch_models.py:125-144,205-207 |
| B5 | 🟠 | 实测 | async 端点里跑同步 `start_audiocpp()`，引擎重启期间整个网关事件循环冻结（上限 90 秒） | 5 并发与竞态 | main.py:371-373 |
| B6 | 🟠 | 实测 | `setup_runtime` 假绿灯：`--skip-models` 也打「[OK] 写歌出歌」，可选件全失败仍 exit 0 | 4 错误处理 / 17 文档一致性 | scripts/setup_runtime.py:15-16,556,629-636 |
| B7 | 🟠 | 实测 | 全新 clone 上测试跑不起来：缺 `cpp/model_specs/yue2.json` 时 `main.py` import 即抛错 | 10 测试有效性 / 17 文档一致性 | main.py:88-92；tests/test_shim_main.py:203,226,231 |
| B8 | 🟠 | 实测 | RVC 补丁路径多套了一层目录，4 个补丁源全部不存在 → 静默跳过，从未应用 | 3 正确性 / 16 发布工程 | scripts/setup_runtime.py:56-57,520-531 |
| B9 | 🟡 | 实测 | 测试条数三个数字互不相同：README 603 / requirements 573 / 实际 614 | 17 文档一致性 | README.md:233,316；README_EN.md:176,263；requirements.txt:7 |
| B10 | 🟡 | 实测 | README 说 `.gitattributes` 用 `*.bat -text eol=crlf`，实际文件是 `text eol=crlf`（真做换行规范化） | 17 文档一致性 | README.md:340 vs .gitattributes:10 |
| B11 | 🟡 | 实测 | 编码规矩声称所有 `.bat` 是 GBK，但 2 个 `.bat` 是 UTF-8，测试名单也不含它们 | 17 文档一致性 / 11 兼容性 | secrets/local_env.example.bat:1；dsh-plugin/启动AI工作台.bat:1；tests/test_script_encoding.py:23 |
| B12 | 🟡 | 实测 | CHANGELOG 没更新、版本号没动：本轮 10-21 的改动仍对外报 `v10.17.1`（10-17） | 16 发布工程 | CHANGELOG.md:12；app.py:797-808 |
| B13 | 🟡 | 实测 | `X-Seed` 是死契约：`dict(httpx.Headers)` 把键降成小写，`headers.get("X-Seed")` 永远 None | 3 正确性 | main.py:415；app.py:2639 |
| B14 | 🟡 | 实测 | `TRANSFORMERS_CACHE` 修复只在 transformers 尚未 import 时有效，新测试只断言 env 不断言实际生效值 | 10 测试有效性 | src/sheetsage_pt.py:38；tests/test_sheetsage_cache.py:33 |
| B15 | 🟡 | 实测 | sitecustomize 只在目标不存在时写入：已有该文件的机器永远拿不到 `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD` 兜底 | 14 迁移与状态演进 | scripts/setup_runtime.py:532-538 |
| B16 | 🟡 | 实测 | `ensure_engine` 见到 exe 就信请求的后端；`ensure_server_json` 不覆盖 server.json 却无条件重写 engine_state | 14 迁移与状态演进 | scripts/setup_runtime.py:260-265,324-339 |
| B17 | 🟡 | 实测 | 缺 `engine_state.json` 时两套缺省：`main._engine_backend()` 按 cpu、`app.backend_mode()` 按 cuda | 2 架构与设计 | main.py:144；app.py:474 |
| B18 | 🟡 | 实测 | Vulkan 后端没有 CPU 兜底：显存预检与回退都以 `backend_mode()=="cuda"` 为门 | 3 正确性 | app.py:2580,2618 |
| B19 | 🟡 | 推理·待实测 | 下载器只对 safetensors 做结构校验；GGUF 只查 magic + 最小字节（2.0GB vs 实际 2.88GB） | 12 依赖与供应链 | scripts/fetch_models.py:77-106 |
| B20 | 🟡 | 实测 | LICENSE 第 1 节的自研代码清单没有本轮新增的 `main.py` / `requirements*.txt` / `server.json.example` | 19 许可证合规 | LICENSE:6 |
| B21 | 🟡 | 实测 | 补丁以 `--depth 1` 跟随上游 main 并无条件覆盖上游文件，`.orig` 只留第一次，上游一变动就静默回退其修复 | 12 依赖与供应链 / 14 迁移 | scripts/setup_runtime.py:481,520-531 |

> 说明：B1/B2 各自独立成立，但它们合起来正好命中红线①"不许静默降级/假绿灯"的两个面——一个在页面，一个在安装器。

---

### B1 · 🔴 引擎全死时 `/api/health` 仍回 `gateway:"ok"`，前端据此点亮绿灯「引擎就绪」

**证据链**

- `main.py:230`：`out: dict[str, Any] = {"gateway": "ok"}` —— 无条件写死。下面的分支只改 `out["audiocpp"]`（`:233-242`），`gateway` 永远是 `"ok"`。
- `main.py:242`：引擎连不上时 `out["audiocpp"] = {"status": "down", ...}` —— 这一半是诚实的。
- `static/index.html:4980`：`if (r.ok && (j.gateway === "ok" || be.status === "ok"))` —— **或**条件。只要 `gateway` 是 `"ok"`，`audiocpp.status === "down"` 也照样进绿灯分支。
- `static/index.html:4983`：`$("healthText").textContent = "引擎就绪 · " + (j.models?.yue2 || "yue2")` —— 绿灯文案 + 模型名。
- `main.py:243-245`：模型名来自 `cpp/server.json`，与引擎死活无关，于是"引擎就绪 · yue2-3b-q4_k_m.gguf"里两个字段都是假的。
- 新增测试 `tests/test_shim_main.py:240-243` 把这条行为**锁死**了：`self.assertEqual(j["gateway"], "ok")`，同时 `status == "down"`。

**复现命令**

```bash
./py312/python.exe - <<'PY'
import importlib.util, json, os, sys
from pathlib import Path
os.environ["YUE2_NO_ENGINE_AUTOSTART"] = "1"
sys.path.insert(0, ".")
spec = importlib.util.spec_from_file_location("shim", Path("main.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m._engine_get = lambda *a, **k: None          # 引擎全死：/health 拿不到
from fastapi.testclient import TestClient
print("HTTP 200 body =", TestClient(m.app).get("/api/health").json())
PY
node -e 'const j={gateway:"ok",audiocpp:{status:"down",backend:null},models:{yue2:"yue2-3b-q4_k_m.gguf"}};const r={ok:true};const be=j.audiocpp||{};if(r.ok&&(j.gateway==="ok"||be.status==="ok")){console.log("footer => ok / 引擎就绪 · "+(j.models?.yue2||"yue2"))}else{console.log("footer => err")}'
```

实际输出：

```text
HTTP 200 body = {'gateway': 'ok', 'audiocpp': {'status': 'down', 'backend': None, 'models': 0, 'loaded': None}, 'models': {'yue2': 'yue2-3b-q4_k_m.gguf'}}
footer => ok / 引擎就绪 · yue2-3b-q4_k_m.gguf
```

第二条是照抄 `index.html:4980/4983` 的判据与文案（只把 DOM 操作换成 `console.log`）。

**触发条件**：引擎没起、崩了、被 `generate_stop` 强杀后还没重启、正在加载权重（`/health` 2 秒超时）、或生成把 CPU 占满导致 `/health` 超过 2 秒（`main.py:204-206` 自己写了"只读请求也要等十几秒"）。这些状态下 `/api/health` 都回 `gateway:"ok"`。

**用户可见后果**：页脚绿灯 +「引擎就绪 · yue2-3b-q4_k_m.gguf」，点「生成歌曲」后 90 秒超时/502。启动脚本也拿 `/api/health` 判"网关就绪"（`scripts/启动音乐工作台.bat` 的 `:waitloop`），它看到 200 就进 `:ready` 并 `start ""` 打开页面——用户拿到一个"全部就绪"的界面，实际是空壳。这是红线①点名的"假绿灯"。

**修复方向**：`gateway` 只有在"网关自身可服务"时才配叫 ok；引擎状态应由 `audiocpp.status` 单独承载，前端把条件改成 `be.status === "ok"`（`gateway` 只用来区分"网关没连上"）。若要与老页面兼容，至少让 `/api/health` 在 `audiocpp.status === "down"` 时回 `gateway: "degraded"`，并同步改 `:4980`。顺手把 `tests/test_shim_main.py:240` 的断言改成"down 时不得为 ok"，否则测试会继续把这个 bug 焊死。

---

### B2 · 🔴 新入口 `一键补齐运行件.bat` 用 `>/dev/null`，全新 clone 上永远报「找不到任何 Python」并退 1

**证据链**

- `一键补齐运行件.bat:17-18`（GBK 解码后）：
  - `if not defined PY where python >/dev/null 2>&1 && set "PY=python"`
  - `if not defined PY where py >/dev/null 2>&1 && set "PY=py -3"`
- `>/dev/null` 是 POSIX 路径。cmd.exe 把它当相对路径 `\dev\null` 打开，目录不存在 → 打印「系统找不到指定的路径。」并把整条复合命令判失败（errorlevel 1），`&&` 后面的 `set "PY=..."` 永远不执行。
- 于是 `PY` 只可能来自 `:16` 的 `py312\python.exe`。而这段代码存在的**唯一场景**恰恰是 `py312` 还不存在的全新 clone。
- 命中后走 `:19-25`：`echo [x] 这台机器上找不到任何 Python。` + `exit /b 1`。
- 全仓只有这两处 `/dev/null`（`grep -rn "/dev/null" --include=*.bat .` 只命中这两行），所以不是误伤整个批处理生态，是这一处新写的。

**复现命令**

```bash
./py312/python.exe - <<'PY'
import subprocess, pathlib, shutil, os
root = pathlib.Path(os.environ["TEMP"]) / "qsb_final"
shutil.rmtree(root, ignore_errors=True); (root / "scripts").mkdir(parents=True)
shutil.copy("一键补齐运行件.bat", root)
(root / "scripts" / "setup_runtime.py").write_text('print("STUB setup_runtime RAN")\n', encoding="utf-8")
env = {"PATH": r"C:\Windows\system32;C:\Windows;" + str(pathlib.Path("py312").resolve()),
       "SystemRoot": r"C:\Windows", "TEMP": r"C:\Windows\Temp"}
print("where python ->", subprocess.run(["cmd","/d","/c","where python"], capture_output=True, env=env).stdout.decode("gbk","replace").strip())
r = subprocess.run(["cmd","/d","/c", str(root / "一键补齐运行件.bat")], capture_output=True, cwd=root, env=env, input=b"\r\n", timeout=30)
print("rc =", r.returncode)
print(r.stdout.decode("gbk","replace"))
print("stderr:", r.stderr.decode("gbk","replace").strip())
PY
```

实际输出（Python 确实在 PATH 上，脚本仍然说没有）：

```text
where python -> C:\Users\HW\Desktop\YuE2\YuE2-Music-Workbench\py312\python.exe
rc = 1
[x] 这台机器上找不到任何 Python。
    装一个就能继续：winget install astral-sh.uv   （或去 python.org 装 3.12）
    装好后重新双击本脚本，已经完成的步骤会自动跳过。
请按任意键继续. . .

stderr: 系统找不到指定的路径。
系统找不到指定的路径。
```

**触发条件**：`py312\python.exe` 不存在（= 首次运行 = README「三步开唱」的第 2 步），且机器上有任何 Python（python / py）。两个 stderr 行就是两次 `/dev/null` 重定向失败。

**用户可见后果**：README.md:225 的"✅ 纯 clone 现在真的能跑起来了"在第 2 步就断掉；用户被告知"这台机器上找不到任何 Python"，按提示去 `winget install astral-sh.uv` 装完再双击，**还是同一句**（因为 `py312` 依旧不存在，而 `where python` 那条路仍然是坏的）。这是一个会让人反复重装工具链的死循环，比直接报错更贵。

**修复方向**：改成 `>nul 2>&1`（cmd 的空设备）。这两行本来就是 ASCII 逻辑，不需要碰 GBK 编码。顺带把"找不到 Python"的文案改成"只在 `where python` / `where py` 都确实失败时才说"，并在写测试时覆盖：`tests/test_script_encoding.py` 只查编码，不查 `/dev/null`，可以加一条"bat 里不许出现 `/dev/null`"的静态检查（成本一行）。

---

### B3 · 🟠 `--with-rvc` 宣称 ⑦ 可用，但 tensorboard/matplotlib 没装，训练进程 import 即死

**证据链**

- `runtime/rvc/train/train.py:60`：`from torch.utils.tensorboard import SummaryWriter` —— 模块级 import，没有 try/except。
- `runtime/rvc/train/utils.py:243-251`（`plot_spectrogram_to_numpy`，训练日志阶段会调用）：函数内 `import matplotlib`，运行到就炸。
- `scripts/setup_runtime.py:54-55`：`RVC_PKGS = ["av", "faiss-cpu", "ffmpeg-python", "onnxruntime", "praat-parselmouth", "einops", "local-attention"]` —— 没有 tensorboard，没有 matplotlib。
- `scripts/setup_runtime.py:548`：`log("  [OK] RVC 运行件就绪（CPU 上换声/训练都慢，训练以小时计）")` —— 无条件打 [OK]。
- 上游自己的清单 `runtime/rvc/requirments_cpu_py312.txt:38,55` **明确列了** `matplotlib` 与 `tensorboard>=2.19.0`，安装器却另起了一份手挑清单。
- 本机实测：`tensorboard`、`matplotlib` 都不在 py312 里（见下）。

**复现命令**

```bash
./py312/python.exe -c "from torch.utils.tensorboard import SummaryWriter"
grep -n "tensorboard\|matplotlib" scripts/setup_runtime.py || echo "setup_runtime.py 里 0 处"
./py312/python.exe -c "import importlib.util as u; print('tensorboard', bool(u.find_spec('tensorboard')), '| matplotlib', bool(u.find_spec('matplotlib')))"
```

实际输出：

```text
Traceback (most recent call last):
  File "<string>", line 1, in <module>
  File "C:\Users\HW\Desktop\YuE2\YuE2-Music-Workbench\py312\Lib\site-packages\torch\utils\tensorboard\__init__.py", line 1, in <module>
    import tensorboard
ModuleNotFoundError: No module named 'tensorboard'
setup_runtime.py 里 0 处
tensorboard False | matplotlib False
```

**触发条件**：`一键补齐运行件.bat --with-rvc` → 走 ⑦ 训练（`app.py:7943` 起 `train/train.py`）。

**用户可见后果**：安装器说"RVC 运行件就绪"，训练页点下去后子进程在第一行 import 就退出，`log_tail` 里只有 `ModuleNotFoundError: No module named 'tensorboard'`；用户以为是训练框架坏了，实际是安装器少装两个包。这正是"页面/安装脚本声称的行为与代码不一致"。

**修复方向**：把 `tensorboard`、`matplotlib` 加进 `RVC_PKGS`（或直接 `pip install -r runtime/rvc/requirments_cpu_py312.txt` 再做覆盖，至少要与上游清单对账一次）；`ensure_rvc` 结尾加一次 import 自检（`import torch.utils.tensorboard` + `import matplotlib`），失败就不打 [OK]。顺带：`coloredlogs` 也不在环境里（上游清单 :46 有），如果只被 GUI 用到可以不装，但要写明"CLI 路径不依赖"。

---

### B4 · 🟠 分块下载不校验 206 响应长度/Content-Range，病态镜像拼出 N 倍垃圾仍报 `[OK]`

**证据链**

- `scripts/fetch_models.py:129-135`：请求带 `Range: bytes={start+have}-{start+length-1}`，但**接受任何 206**，不比对 `Content-Range` 的起点/总长，也不校验"这一次到底收了多少字节是不是 `length`"。
- `scripts/fetch_models.py:142-143`：只检查 `have < length`（不够就报错），**多了不管**。
- `scripts/fetch_models.py:195-204`：按块顺序拼接、`os.replace` 覆盖目标。
- `scripts/fetch_models.py:83-89`：GGUF 的"校验"只有 `size >= min_size` + 头 4 字节 `GGUF`。拼接结果比正确文件大几倍时，两条都满足。
- `scripts/fetch_models.py:206`：`log(f"  {'[OK]' if ok else '[x]'} ...")` —— 于是坏文件拿到 [OK]。

**复现命令**

```bash
./py312/python.exe - <<'PY'
import importlib.util, threading, tempfile, http.server, socketserver
from pathlib import Path
spec = importlib.util.spec_from_file_location("fm", "scripts/fetch_models.py")
fm = importlib.util.module_from_spec(spec); spec.loader.exec_module(fm)
PAYLOAD = b"GGUF" + b"0123456789"          # 14 字节，magic 正确
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        rng = self.headers.get("Range", "")
        if rng == "bytes=0-0":             # 长度探测：老实回 206
            self.send_response(206); self.send_header("Content-Range", f"bytes 0-0/{len(PAYLOAD)}")
            self.send_header("Content-Length", "1"); self.end_headers(); self.wfile.write(PAYLOAD[:1]); return
        self.send_response(206)            # 下载：也回 206，但把整个文件从 0 发回来
        self.send_header("Content-Range", f"bytes 0-{len(PAYLOAD)-1}/{len(PAYLOAD)}")
        self.send_header("Content-Length", str(len(PAYLOAD))); self.end_headers(); self.wfile.write(PAYLOAD)
srv = socketserver.TCPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
fm.MIRROR = f"http://127.0.0.1:{srv.server_address[1]}"
fm.CHUNK = 4
with tempfile.TemporaryDirectory() as d:
    dest = Path(d) / "m.gguf"
    ok = fm.download("repo", "m.gguf", dest, 1, True, workers=1)
    print("download() ->", ok, "| 拼出来:", len(dest.read_bytes()), "字节 | 源文件:", len(PAYLOAD), "字节")
    print("verify_one ->", fm.verify_one(dest, 1, True))
    print("文件头:", dest.read_bytes()[:16])
srv.shutdown()
PY
```

实际输出（14 字节源文件被拼成 56 字节，进度条自己都打印 `56.0B/14.0B` 与 `余约 -1 分`，仍报 OK）：

```text
[18:46:13] -> m.gguf  共 14.0B，切 4 块 × 1 并发
[18:46:13]   14.0B/14.0B  140.0B/s  余约 0 分
[18:46:13]   28.0B/14.0B  140.0B/s  余约 -1 分
[18:46:13]   42.0B/14.0B  140.0B/s  余约 -1 分
[18:46:13]   56.0B/14.0B  140.0B/s  余约 -1 分
[18:46:13]   [OK] m.gguf 56.0B —— ok
download() -> True | 拼出来: 56 字节 | 源文件: 14 字节
verify_one -> (True, 'ok')
文件头: b'GGUF0123456789GG'
```

**触发条件**：镜像/代理/CDN 对 Range 请求回 206 但内容不是请求窗口（返回整文件、返回从 0 开始、或忽略末端）。`head_size` 只用 `bytes=0-0` 探总长，探到了就继续，下载阶段完全没有长度一致性检查。

**用户可见后果**：2.88GB 的主模型被拼成几倍体积（磁盘先炸），然后 `verify_one` 打 [OK]；引擎加载时失败或直接 OOM。用户看到的是"下载成功、模型校验通过、引擎起不来"。若目标路径原本有一份被截断的半成品，会被这份垃圾 `os.replace` 覆盖。

**修复方向**：`fetch_chunk` 里校验 `Content-Range` 的起止是否等于请求窗口、累计字节不得大于 `length`；拼接前断言每块 `stat().st_size == length`；GGUF 至少校验头部解析出来的张量偏移之和（与 safetensors 同级的结构校验），并给出期望字节数（可选 `Content-Length`）。这些都是本地判据，不需要联网。

---

### B5 · 🟠 async 端点里跑同步 `start_audiocpp()`，引擎重启期间整个网关事件循环冻结（上限 90 秒）

**证据链**

- `main.py:331`：`async def music_generate(request: Request)`。
- `main.py:371`：`if not _engine_alive(timeout=2.0):` —— 同步 `httpx.get`，跑在事件循环线程上。
- `main.py:372`：`if not start_audiocpp():` —— `start_audiocpp` 是同步函数，内部 `while time.time() < deadline: ... time.sleep(2.0)`（`main.py:178-183`），`deadline = settings.audiocpp_ready_timeout_sec`，而 `settings.py:41` 写的是 **90.0 秒**。
- 这个端点没有 `await` 任何东西，也没有 `run_in_threadpool`；Starlette 的 `def`（同步）端点会进线程池，`async def` 不会。
- 事件循环被占死时，线程池端点也拿不到调度：下面的实验里 `/api/health` 是同步 `def`（`main.py:224`），照样被拖住 2.83 秒。

**复现命令**

```bash
./py312/python.exe - <<'PY'
import importlib.util, time, threading, os
from pathlib import Path
os.environ["YUE2_NO_ENGINE_AUTOSTART"] = "1"
spec = importlib.util.spec_from_file_location("shim5", Path("main.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
from fastapi.testclient import TestClient
m._engine_alive = lambda timeout=1.5: False
def slow_start():
    time.sleep(3.0)        # 模拟 start_audiocpp() 等引擎 /health（真实上限 90 秒）
    return False
m.start_audiocpp = slow_start
res = {}
with TestClient(m.app) as c:
    def post():
        t = time.time(); r = c.post("/api/music/generate", json={"style": "s", "lyrics": "a"})
        res["POST /api/music/generate"] = (r.status_code, round(time.time() - t, 2))
    th = threading.Thread(target=post); th.start()
    time.sleep(0.4)
    t = time.time(); r = c.get("/api/health")
    res["GET /api/health（0.4 秒后才发）"] = (r.status_code, round(time.time() - t, 2))
    th.join()
print(res)
PY
```

实际输出（桩只睡 3 秒；真实路径是 90 秒上限）：

```text
{'POST /api/music/generate': (502, 3.0), 'GET /api/health（0.4 秒后才发）': (200, 2.83)}
```

**触发条件**：引擎不在（冷启动、崩溃、被"终止任务"强杀后下一次生成、模型切换中）。`app.py::_ensure_backend` 大多数时候会先把引擎拉起来，但只要引擎在 `_gen_run` 自检之后、shim 自检之前掉线，就会走到这里。

**用户可见后果**：这 90 秒里整个 7863 无响应——页脚健康灯不刷新、「终止任务」按钮点了没反应、⑪ 任务管理/历史/设置全部挂起，浏览器侧表现为"工作台卡死"。用户很可能在这段时间里强杀网关，把正在等待的其它状态一起带走。

**修复方向**：`music_generate` 改成同步 `def`（Starlette 自动丢线程池，也是项目里 `plan_abc` 已经采用的写法），或把 `_engine_alive` / `start_audiocpp` 包进 `run_in_threadpool`。`_engine_alive(2.0)` 也应该就地缩短为异步 `httpx` 调用，别在事件循环上阻塞。

---

### B6 · 🟠 `setup_runtime` 假绿灯：`--skip-models` 也打「[OK] 写歌出歌」，可选件全失败仍 exit 0

**证据链**

- `scripts/setup_runtime.py:15-16`（docstring）："任何一步失败都如实退出非 0 并说清缺什么，不会在没装上的情况下打绿勾。"
- `scripts/setup_runtime.py:556`：`("[OK]", "写歌出歌：双击 启动音乐工作台.bat -> http://127.0.0.1:7863")` —— 常量，不读任何实际状态。`--skip-models` 时权重一份都没有，照样绿。
- `scripts/setup_runtime.py:629-633`：`transcribe_ready = ensure_transcribe(...)`、`rvc_ready = ensure_rvc(...)`、`lrc_ready = ensure_lrc(...)` 的返回值只进 `report()` 的 [x]/[OK] 文案，**从不影响退出码**；`:636` 无条件 `return 0`。
- `scripts/setup_runtime.py:559-564`：失败时打印的下一步是"加 --with-transcribe / 加 --with-rvc"，而用户**已经**加过——文案把人指回原地。

**复现命令**

```bash
./py312/python.exe - <<'PY'
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("sr6", "scripts/setup_runtime.py")
sr = importlib.util.module_from_spec(spec); spec.loader.exec_module(sr)
print("=== A) 权重一份都没有（等同 --skip-models 之后）===")
sr.report("cpu", "q4km")
print("=== B) --with-rvc / --with-lrc 都失败了 ===")
sr.ensure_py312 = lambda: Path("py312/python.exe")
sr.install_deps = lambda idx: True
sr.ensure_engine = lambda b,c,p: "cpu"
sr.ensure_server_json = lambda b,q: True
sr.ensure_models = lambda q,w,s: True
sr.ensure_rvc = lambda i,p,s: False
sr.ensure_lrc = lambda i: False
sys.argv = ["setup_runtime.py", "--with-rvc", "--with-lrc"]
print("main() exit code =", sr.main())
PY
```

实际输出（截取关键行）：

```text
=== A) 权重一份都没有（等同 --skip-models 之后）===
[setup]   [OK] 写歌出歌：双击 启动音乐工作台.bat -> http://127.0.0.1:7863
[setup]   [OK] 开源兼容网关 main.py 顶上闭源 main.cp312-win_amd64.pyd —— 纯 clone 也能起工作台
[setup]   [x] ③④ 从音频提取乐谱：加 --with-transcribe 装 torch 与 SheetSage2 权重
[setup]   [x] ⑨ 换声 / ⑦ 音色制作 / ⑩ 人声分离：加 --with-rvc 安装（官方 RVC + PyMSS + hubert/rmvpe/预训练权重）
[setup]   [x] 逐字 LRC/eLRC：加 --with-lrc 安装（funasr + fa-zh）
=== B) --with-rvc / --with-lrc 都失败了 ===
[setup]   [x] ⑨ 换声 / ⑦ 音色制作 / ⑩ 人声分离：加 --with-rvc 安装（官方 RVC + PyMSS + hubert/rmvpe/预训练权重）
[setup]   [x] 逐字 LRC/eLRC：加 --with-lrc 安装（funasr + fa-zh）
main() exit code = 0
```

**触发条件**：`--skip-models`（断网/先装引擎后下权重）、`--with-rvc` / `--with-lrc` / `--with-transcribe` 任一失败。

**用户可见后果**：用户按"绿色清单"去点生成，得到"引擎不可用/模型缺失"；脚本层面没有任何失败信号，`一键补齐运行件.bat` 的 `if errorlevel 1` 也不会触发（B2 那条分支），自动化/批量部署同样拿到 0。对红线①是直接违反：安装器自己在打假勾。

**修复方向**：`report()` 接受一个 `models_ok`（来自 `fetch_models --check`）与各可选件的真实结果；"写歌出歌"这一行只在 `server.json + 主/VAE 权重 + 引擎 spec` 都在时打 [OK]，否则 [x] 并说明缺哪一件。`main()` 在有 `--with-*` 失败时 `return 1`（或至少 `return 2` 区分"主流程 OK、可选失败"），失败文案改成"上次 --with-rvc 失败：<原因>"而不是"加 --with-rvc"。

---

### B7 · 🟠 全新 clone 上测试跑不起来：缺 `cpp/model_specs/yue2.json` 时 `main.py` import 即抛错

**证据链**

- `main.py:88-92`：模块级 `raise RuntimeError("找不到 audio.cpp 的 YuE2 参数表（cpp/model_specs/yue2.json）...")`。`cpp/` 整个目录在 `.gitignore:61` 里，所以**仓库里没有这个文件**。
- `app.py:46`：`import main` —— import 期就炸，于是所有 `import app` 的测试（不止新加的）在干净 clone 上全部 collect 失败。
- `tests/test_shim_main.py:203`：`cfg = json.loads((ROOT / "cpp" / "server.json").read_text(...))` —— 即便补上 spec，这一条要本机的 `cpp/server.json`。
- `tests/test_shim_main.py:226-229`、`:231-243`：`_server_json_models()` 也读 `cpp/server.json`，缺失时 `models` 键整个不存在，`j["models"]["yue2"]` 抛 `KeyError`。
- 旧的 `tests/test_review_fixes.py:30` 是靠往 `sys.modules["main"]` 注入替身才让套件在无内核机器上可跑的；新文件用 `importlib` 按路径加载真 `main.py`，把这条退路绕过去了。

**复现命令**

```bash
rm -rf /tmp/yue2-clean && mkdir -p /tmp/yue2-clean && git checkout-index -a --prefix=/tmp/yue2-clean/ && cd /tmp/yue2-clean && /c/Users/HW/Desktop/YuE2/YuE2-Music-Workbench/py312/python.exe -m unittest discover -s tests -p "test_shim_main.py" 2>&1 | tail -6
mkdir -p /tmp/yue2-clean/cpp/model_specs /tmp/yue2-clean/runtime/data && cp cpp/model_specs/yue2.json /tmp/yue2-clean/cpp/model_specs/ && cp runtime/data/engine_state.json /tmp/yue2-clean/runtime/data/ && cd /tmp/yue2-clean && /c/Users/HW/Desktop/YuE2/YuE2-Music-Workbench/py312/python.exe -m unittest discover -s tests -p "test_shim_main.py" 2>&1 | tail -4
```

实际输出：

```text
Ran 1 test in 0.000s

FAILED (errors=1)
...
Ran 21 tests in 0.080s

FAILED (failures=1, errors=2, skipped=1)
```

第一条是 `ImportError ... RuntimeError: 找不到 audio.cpp 的 YuE2 参数表（cpp/model_specs/yue2.json）`；第二条补上 spec 与 state 后仍有 3 条红（`test_backend_is_consistent_between_config_and_state` ERROR、`test_model_name_comes_from_server_json...` FAIL、`test_health_shape_survives_an_unreachable_engine` ERROR）。

**触发条件**：任何没有本机部署现场（`cpp/`、`runtime/data/`）的 clone / CI / 新机器。

**用户可见后果**："614 条全绿"只在作者这台机器上成立。别人 clone 下来照 README 跑 `-m unittest discover -s tests`，在**第一条**测试就整片红，而且报错指向一个 README 说"一键补齐会装"的路径——分不清是自己装坏了还是项目坏了。同时这也说明新加的 6 个测试文件里有一半是"本机配置断言"而不是"行为断言"（`test_backend_is_consistent_between_config_and_state` 断言本机 `server.json` 后端 == `engine_state` 后端，用户合法地改一处就会红）。

**修复方向**：把 `main.py` 的参数表读取改成"延迟到第一次用到"（`lru_cache` 的 `_engine_options()`），import 期不要 raise；或在 `cpp/` 缺失时退化为"只提供 app/settings + health，generate 返回 503 并说明缺 spec"。测试侧：`test_shim_main.py` 里所有读本机 `cpp/` 的用例加 `skipUnless(路径存在)`；`test_backend_is_consistent...` 改成"用临时写出的 server.json/state 验证三者一致性"，不要断言本机现场。CI 至少要能在 `git clone` 后的裸树上跑绿（或显式 skip 并说明）。

---

### B8 · 🟠 RVC 补丁路径多套了一层目录，4 个补丁源全部不存在 → 静默跳过，从未应用

**证据链**

- `scripts/setup_runtime.py:56-57`：`RVC_PATCHES = ["infer/cli.py", "infer/vc/pipeline.py", "infer/vc/modules.py", "train/preprocess.py"]`（相对 `runtime/rvc/`）。
- `scripts/setup_runtime.py:520-524`：
  `src = ROOT / "patches" / ("rvc-clean-guards" if rel.startswith("train/") else "rvc-infer") / rel`
  也就是去找 `patches/rvc-infer/infer/cli.py`、`patches/rvc-clean-guards/train/preprocess.py`。
- 实际仓库布局是 `patches/rvc-infer/cli.py`、`patches/rvc-clean-guards/preprocess.py`（补丁目录根 = 对应上游子目录）。`find patches -name "*.py"` 可证。
- `scripts/setup_runtime.py:523-524`：`if not src.is_file() or not dst.is_file(): continue` —— 源不存在就直接跳过，**不打印任何东西**。
- 后果落在 `app.py:5562-5566`：没有补丁就探不到 `--filter-radius`，`job["caps_warn"]` 记一句"本机 RVC 运行时不支持音高平滑"，音高平滑整段静默失效。

**复现命令**

```bash
./py312/python.exe - <<'PY'
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location("sr8", "scripts/setup_runtime.py")
sr = importlib.util.module_from_spec(spec); spec.loader.exec_module(sr)
for rel in sr.RVC_PATCHES:
    src = Path("patches")/("rvc-clean-guards" if rel.startswith("train/") else "rvc-infer")/rel
    print(f"{rel:22s} src_exists={src.is_file()}")
PY
find patches -name "*.py" | sort
```

实际输出：

```text
infer/cli.py           src_exists=False
infer/vc/pipeline.py   src_exists=False
infer/vc/modules.py    src_exists=False
train/preprocess.py    src_exists=False
patches/rvc-clean-guards/preprocess.py
patches/rvc-clean-guards/repro_nan_trace.py
patches/rvc-clean-guards/sitecustomize.py
patches/rvc-infer/cli.py
patches/rvc-infer/vc/modules.py
patches/rvc-infer/vc/pipeline.py
```

**触发条件**：任何一次 `一键补齐运行件.bat --with-rvc`（= README 推荐路径）。

**用户可见后果**：换声页的"音高平滑半径"永远不生效（5~7 档调了没变化），fcpe 的 caps 探测也永远走"无能力"分支；README/README_EN 明确写 "`--with-rvc` ... applies the patches in `patches/`"，与事实相反。本机这台之所以有补丁，是因为作者早先手工补过（`runtime/rvc/infer/cli.py.orig` 是原版、`cli.py` 带 `--filter-radius`），所以**只有这台机器是好的**——典型的"作者机上绿"。

**修复方向**：`rel.split("/", 1)[1]` 再拼补丁目录（`infer/cli.py` → `patches/rvc-infer/cli.py`，`train/preprocess.py` → `patches/rvc-clean-guards/preprocess.py`）；源缺失时至少 `log("[!] 补丁缺失：...")` 而不是静默 `continue`；装完用 `_rvc_cli_caps()` 同口径自检一次，探不到 `--filter-radius` 就明说"音高平滑不可用"。

---

## 🟡 明细（不展开，合并同类）

- **B9 测试条数三套口径**：`README.md:233,316` 与 `README_EN.md:176,263` 写 **603**，`requirements.txt:7` 写 **573**，README 版本表里 v10.17.1 那一行写"本标签 559 项"（`README.md:379`），实测 **614**（`./py312/python.exe -m unittest discover -s tests`）。同一个数在四处不同，改版本时没人对账。位置：README.md:233,316,379；README_EN.md:176,263；requirements.txt:7。
- **B10 `.gitattributes` 描述与文件相反**：README.md:340 说"`*.bat -text eol=crlf` 挡住换行规范化"，.gitattributes:10 实际是 `*.bat text eol=crlf`（`text` 会做规范化：索引里存 LF、检出给 CRLF）。我实测过 add→checkout 往返字节完全一致（md5 相同），所以**没有功能损失**，但文档把"会规范化"写成了"挡住规范化"，下一个人照着 `-text` 去改就会真的把 CRLF 丢掉。
- **B11 两个 `.bat` 不是 GBK**：`secrets/local_env.example.bat`（UTF-8 无 BOM，63 个非 ASCII 字节）与 `dsh-plugin/启动AI工作台.bat`（UTF-8 无 BOM，120 个非 ASCII 字节）都受 `*.bat` 规则覆盖，却违反 README.md:334 的"全部 GBK"。`tests/test_script_encoding.py:23-33` 的名单只列 9 个文件，正好漏掉这两个。实际影响目前只在中文字符串/注释显示（属于命令行以外），但规矩自相矛盾。
- **B12 版本与 CHANGELOG 没走发布流程**：本轮改动的注释日期是 2026-10-21，`CHANGELOG.md:12` 顶部仍是 `[v10.17.1] - 2026-10-17`，`app.py:797-808` 的 `_version_from_changelog()` 于是把 v10.17.1 报给 ⑬ 关于页。按 README 自己的版本规则（主.日.序）这次应当是 `v10.21.x`。30 个文件、55 条新测试没有 changelog 条目。
- **B13 `X-Seed` 是死契约**：`main.py:415` 写 `X-Seed`，注释说 `app.py:2549` 会读它；实际 `app.py:2639` 是 `headers.get("X-Seed")`，而 `_gen_run_once` 返回的是 `dict(r.headers)`（app.py:2451）。实测 `dict(httpx.Response(...).headers)` 的键全小写（`{'x-seed': '123'}`），所以 `get("X-Seed")` 永远 `None`，`seed_actual` 只能回落到 `payload.get("seed")`。今天不炸是因为 app.py 已经 `_materialize_seed` 过；一旦 shim 自己换种子（`main.py:359-367` 的"非 int 就随机"），记录里的种子会与引擎实际用的不一致且没有提示。
- **B14 `TRANSFORMERS_CACHE` 修复取决于 import 顺序**：`transformers 4.45.2` 的 `utils/hub.py:102` 是 `TRANSFORMERS_CACHE = os.getenv("TRANSFORMERS_CACHE", ...)`，**import 期求值**。`src/sheetsage_pt.py:38` 只改 `os.environ`。当前网关的 import 链恰好没有提前 import transformers（我实测 `import app` 后 `'transformers' in sys.modules == False`），所以修复在本机有效；但只要有任何模块先 import 了它（例如先跑过 `import funasr`——实测会连带 import transformers），`cached_file()` 用的就还是 `C:\Users\HW\.cache\huggingface\hub`，转谱仍会报"本地缓存不完整"。`tests/test_sheetsage_cache.py:33-37` 只断言 `os.environ` 两值相等，不断言 `transformers.utils.hub.TRANSFORMERS_CACHE`，**这个测试在 bug 仍然存在时也会绿**。
- **B15 sitecustomize 只在不存在时写入**：`scripts/setup_runtime.py:534` `if sc_src.is_file() and not sc_dst.is_file():`。任何已有 `sitecustomize.py` 的机器（上一次部署、别的包自带）永远拿不到追加的 `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD`，也不会有任何提示。本机那份正是"仓库原版 + 手工追加"（`patches/rvc-clean-guards/sitecustomize.py` 里没有这行）。补一句实测：本机 `pretrained_v2/f0G40k.pth` 在默认 `weights_only=True` 下能正常 load，所以这条兜底今天**不是**硬依赖——但它存在的理由（用户自备 .pth）没有被任何测试覆盖。
- **B16 安装器对"已有文件"的两套策略**：`ensure_engine`（:260-265）见到 `cpp/audiocpp_server.exe` 就 `return backend`（请求值），不跑 `--version` 读 `backends:`；旧 CPU 包 + 默认 `--backend cuda` 会写出 `backend:"cuda"` 的配置。`ensure_server_json`（:324-339）不覆盖已有 `cpp/server.json`，**却无条件重写 `runtime/data/engine_state.json`**（:331-339），于是换 `--backend cpu`/`--q8` 重跑一次就会得到"server.json 说 vulkan/q4、state 说 cpu/q8"。`tests/test_shim_main.py:196-215` 正是断言这两者必须相等——安装器自己会造出那个红。
- **B17 缺 state 时两套缺省**：`main.py:144` `return mode if mode in (...) else "cpu"`；`app.py:470-475` `mode = _engine_state_read().get("backend", "cuda")`。删掉/损坏 `runtime/data/engine_state.json` 后，shim 用 cpu 起引擎、页面按 cuda 显示并做显存预检，两个进程各说各话（本文件自己的 `test_backend_is_consistent...` 也覆盖不到这种"文件不存在"的状态）。
- **B18 Vulkan 没有 CPU 兜底**：`app.py:2580` 的显存预检与 `:2618` 的"显存类错误自动切 CPU 重试"都写成 `backend_mode() == "cuda"`。核显走统一内存，OOM 形态与独显不同；真撞上时用户只会拿到一条错误，没有 v10.17.1 之前那套"自动降级重试"。要么把门改成 `in ("cuda","vulkan")`，要么在文案里明说"Vulkan 不做自动降级"。
- **B19 GGUF 校验偏弱（推理·待实测）**：`scripts/fetch_models.py:83-89` 对 GGUF 只查 `size >= min_size`（主模型门槛 2,000,000,000，实际约 2.88GB）与头 4 字节。若镜像给的是"长度对但内容错"（或被代理插了 HTML 头），这里验不出来。safetensors 那条（:90-105）做得对，可以照抄思路解析 GGUF 头。另：`--workers 0` 会让 `ThreadPoolExecutor(max_workers=0)` 直接 `ValueError`，没有参数校验。
- **B20 LICENSE 清单漏了新文件**：`LICENSE:6` 的自研代码枚举是"`app.py`、`src/`、`static/`、`dsh-plugin/`、`scripts/`、`watchdog.py`、`settings.py`"，本轮新增的根 `main.py`、`requirements.txt`、`requirements-transcribe.txt`、`server.json.example` 都没进清单。内容上没有复制第三方代码（`main.py` 是照引擎 spec 重写，`patches/` 是上一轮就有的），所以**不构成许可冲突**，但"分层声明"这一节现在与仓库实际文件对不上。
- **B21 补丁覆盖策略**：`scripts/setup_runtime.py:481` 用 `--depth 1` 克隆上游 `main`（移动目标），`:520-531` 只要字节不同就用仓库里的整份文件覆盖，`.orig` 只在第一次备份。上游改一行，这里就会把它静默回退成仓库那一版；用户看到的 `caps_warn` 会变成"运行时不支持"，而不是"补丁把上游改坏了"。至少应记录上游 commit、在补丁与上游基线不一致时拒绝覆盖并提示。

---

## 维度覆盖声明（13 项逐项表态）

1. **需求一致性**：**发现问题**。红线①在三处被破：`/api/health` 假绿（B1）、安装器假勾（B6）、`--with-rvc` 宣称训练可用但缺依赖（B3）；README"纯 clone 就能跑"在 B2（第一步就跑不动）、B7（干净 clone 测试全红）上不成立。
2. **架构与设计**：**发现问题**。shim 的边界划得干净（只做 app/settings、health、generate、拉起引擎），但两个进程各自维护"后端"缺省（B17）、health 语义与前端 OR 条件耦合（B1）、shim 的异步端点里放同步阻塞（B5）。生成串行化靠 `app.py::_GPU_SEM`，shim 自己没有任何闸门——直连 `/api/music/generate` 的调用可以绕过，这一条未在真实负载下验证，记在「未验证项」。
3. **正确性**：**发现问题**。B4（下载拼接）、B8（补丁路径）、B13（X-Seed）、B18（Vulkan 无回退）。`_pick_payload` 的字段筛选、`_engine_body` 的两层写法、`cfg_scale→guidance_scale` 映射与 spec 对得上（`cpp/model_specs/yue2.json` 27 项，我逐项读过），这部分**未发现问题**。
4. **错误处理**：**发现问题**。B1（把 down 说成 ok）、B6（失败也打勾）、B8（源缺失静默 continue）、B2（错误原因说错）。做得对的部分：`_audio_from_response` 没音频时 502 且带 `artifact_ids`，不是返回空字节；引擎 `{"error":{"message"}}` 被原话搬进 `detail`，没有被洗成 400。
5. **并发与竞态**：**发现问题**。B5 是实测的阻塞；`_gen_run_once` 的 keep-awake 线程语义**未发现问题**（`SetThreadExecutionState` 按线程，`finally` 撤销，与训练线程互不干扰；变异验证见附录）。`_GPU_SEM` 不覆盖 shim 直连端点——未实测，列入未验证。
7. **安全**：**未发现新增漏洞**。新端点在 loopback + `_guard_local_only`（Host 回环、写方法查 Origin/referer）之后；`main.py` 起子进程用列表参数、无 shell；`register_protocol.bat` 明确不再把 `%1` 拼进命令行（这是上一轮就修好的），新改动没有把它加回来。提一句供应链事实：下载的引擎 zip 与 GGUF 都没有签名/哈希校验（B4、B19），属"信任镜像"模型。
10. **测试有效性**：**部分有效，覆盖不足**。三条变异全部被正确杀掉（附录 A）；但 B7（干净 clone 全红）、B14（断言 env 不断言生效值）、B8（补丁从未应用却无测试）、B3（训练依赖无测试）说明新测试只覆盖了"本机这套文件在"的情形。
11. **兼容性**：**发现问题**（B2 的 cmd 语法、B3 的缺失依赖、B11 的编码例外）。重点核过 RVC 在 numpy 2.5.4 / librosa 1.0 / torch 2.8 下的真实调用：`infer/cli.py --help` 与 `python -m pymss.cli --help` 都能 import 并跑通（实测）；`librosa.filters.mel` 的调用点（`infer/rmvpe.py:423`、`train/mel_processing.py:98`）已经全部用关键字参数，librosa 1.0 的"位置参数必须消失"这条不命中；`np.array(..., copy=False)` 的几处（`vr_separator.py:399/415`、`pymss/utils.py:827`）作用对象是 ndarray/mlx array，不触发 numpy 2 的"无法避免复制就报错"。**没有找到 numpy2/librosa1.0 必然坏掉的代码路径**——这一条与"只看 requirements 就判死"的直觉相反，实测为准。torch 2.8 的 `torch.load` 默认 `weights_only=True` 由环境变量兜底覆盖（`TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD` 在 `torch/serialization.py:1443` 是**调用期**读取，sitecustomize 设得早，覆盖得住 Infer 的若干调用点）；但兜底的**安装**不可靠（B15）。
12. **依赖与供应链**：**发现问题**。`requirements.txt` 的 12 个钉死版本与机器上已装版本**逐个对上**（fastapi 0.115.12 / starlette 0.46.2 / uvicorn 0.54.0 / httpx 0.28.1 / python-multipart 0.0.32 / pydantic 2.14.1 / numpy 2.5.4 / soundfile 0.14.0 / scipy 1.18.1 / librosa 1.0.0 / pyworld 0.3.5 / aiofiles 25.1.0），BOM 也能被 pip 正常解析（`pip install --dry-run --no-index` 逐行读到）。问题在 RVC 侧：手挑清单漏 tensorboard/matplotlib（B3）、补丁跟随上游 main（B21）、下载无哈希（B19）。
14. **迁移与状态演进**：**发现问题**。B16（server.json 不覆盖 / state 无条件覆盖）、B17（缺 state 的缺省不一致）、B15（sitecustomize 一次性写入）。这三条都会在"老机器升级"这一真实路径上发作，本机恰好因为手工补过而看不出来。
16. **发布工程**：**发现问题**。B12（CHANGELOG/版本号没动）、B8（补丁路径）、B9（数字对不上）。做对的部分：暂存区 30 个文件全部是源码/脚本/文档，没有把 `runtime/`、`cpp/`、`deploy/`、模型或权重带进来（`git diff --cached --name-only` 逐条核过）；`.gitignore` 新增 `deploy/` 与新测试的"缺失即 skip"一致。
17. **文档一致性**：**问题最多的一维**。B1（页面说"引擎就绪"）、B6（安装器说"能出歌"）、B9（603 vs 614 vs 573）、B10（`-text` vs `text`）、B11（"全部 GBK"）、B12（版本号）、B3/B8（README 说补丁/训练已就绪）。README 的 CPU/Vulkan **实测锚点本身是可信的**（数字具体、给的条件具体、承认 ±40% 波动），这部分值得保留。
19. **许可证合规**：**未发现新增冲突**。`main.py` / `fetch_models.py` / `setup_runtime.py` 都是照引擎 spec 与公开 API 重写，没有把 RVC/audio.cpp 的源码抄进来；`patches/` 是上一轮就存在的上游 MIT 派生文件（不在本轮 diff 里）。唯一问题是 LICENSE 的自研清单没跟着更新（B20）。

---

## 优先级路线图

**P0（今天就要修，发布前）**

1. **B2**：`>/dev/null` → `>nul`。一行改动，决定"全新 clone 能不能开始装"。
2. **B1**：`/api/health` 的 `gateway` 语义 + `index.html:4980` 的 OR 条件。同时改 `tests/test_shim_main.py:240` 那条把 bug 焊死的断言。
3. **B7**：`main.py` 的参数表延迟加载（或缺失时降级到 503），并给依赖本机 `cpp/` 的测试加 `skipUnless`——这是"别人的机器上能不能跑"的门。
4. **B6**：`setup_runtime` 的退出码与 `[OK]` 判据。安装器说假话的代价比功能缺失大。

**P1（本轮内）**

5. **B3**：补 `tensorboard`/`matplotlib`（或改成装上游清单），`ensure_rvc` 结尾加 import 自检。
6. **B8**：补丁路径 `rel.split("/",1)[1]`，缺源要出声。
7. **B4**：`fetch_chunk` 校验 `Content-Range`/长度，拼接前断言块大小。
8. **B5**：`music_generate` 改同步 `def` 或把阻塞调用丢线程池。
9. **B9/B10/B11/B12**：文档四个数字/事实一次对齐；CHANGELOG 补条目并按规定升到 `v10.21.x`。

**P2（下一轮）**

10. **B16/B17/B15**：安装器的"已有文件"策略与缺省值收敛成一处真源；sitecustomize 改成"缺那行就补那行"。
11. **B18**：Vulkan 的显存/OOM 回退（或明说不回退）。
12. **B13/B14**：`X-Seed` 改成 `headers.get("x-seed")` 或直接用 `httpx.Headers`；转谱缓存测试改成断言 `transformers.utils.hub.TRANSFORMERS_CACHE`。
13. **B19/B20/B21**：GGUF 结构校验、LICENSE 清单、补丁与上游 commit 绑定。

---

## 做得对、值得保持的

- **参数白名单运行时读引擎 spec**（`main.py:67-92`）：不手抄 27 个字段名，读不到就拒绝启动。这是这个 shim 里最有价值的设计决定——它把"内核 vs 开源层字段漂移"变成了启动期可见的错误，而不是运行期静默丢参数。
- **错误形状不洗白**（`main.py:385-402`）：引擎的 `{"error":{"message"}}` 原话搬进 `detail`，`_is_vram_error` 的关键词判据还能用；参数错 400/422、上游故障 502 分开。比"5xx 一律 400"好得多。
- **`resolve_backend` 用二进制自证**（`app.py:481-529`）：`audiocpp_server.exe --version` 的 `backends: cpu,vulkan` 是唯一真源（我实测这条输出确实在 stdout 上），不再靠"有没有 nvidia-smi"猜；改错后端时会带一句人话而不是假装成功。
- **`_gen_run_once` 的 keep-awake 线程语义**（`app.py:2435-2444`）：`try/finally` 成对、按线程声明、异常路径也撤销。变异验证证明测试真的会红（附录 A-3）。
- **GBK + BOM 的编码规矩本身**：`git add → checkout` 往返字节不变（实测 md5 相同），`.ps1` 的 BOM、`requirements*.txt` 的 BOM 都是真的（`git ls-files --eol` 与字节检查都过），`PYTHONIOENCODING` 从 `chcp` 推导的写法在 936 控制台下是对的。
- **测试的变异可杀性**：`request.options` 双层、cuda→vulkan 落地、keep-awake 的 finally，三条变异分别杀掉 2/2/1 个用例（附录 A）。这在本项目里是稀缺品质。
- **README 的 CPU 锚点诚实**：93.5 分钟/66.7 秒、RTF≈84、"别按页面 ETA 安排时间"——具体、有条件、承认波动。没有把"能跑"写成"很快"。

---

## 未验证项（如实写）

1. **出歌全链路在 shim 下真的能出片**：禁忌不允许提交生成任务，我只验证了 400 分支、payload 形状与错误映射；`/api/music/generate` → 引擎 → WAV → `output/` 这条真实链路本轮**没有**跑过。
2. **与闭源 `main.cp312-win_amd64.pyd` 的行为对比**：pyd 不在仓库里，无法确认 B1（health 的 `gateway`）是"新引入"还是"沿袭"。我按"新文件必须自己说真话"定级，但历史归属未证。
3. **生成期间 `/health` 的真实耗时**：B1 的触发条件之一（引擎被占满时 2 秒超时）是从 `main.py:204-206` 的自述与 `app.py` 的同类判据推出来的，没有在跑歌时实测（禁忌）。
4. **镜像故障下的真实表现**：B4 用本地病态 HTTP 服务复现了判据缺陷，但没有在 hf-mirror 真实抖动的链路上跑过 2.88GB 的下载。
5. **训练是否还有 tensorboard 之外的坑**：我验证了"import 就死"（B3），没有装 tensorboard 去跑一轮训练（会改 `py312/`，且训练以小时计）。
6. **Vulkan OOM 的实际形态**（B18）：没有在核显上造一次显存/内存不足。
7. **`--with-transcribe` / `--with-lrc` 的下载与真实转谱**：需要联网与数 GB 流量，未跑；B14 只用 import 顺序与常量求值证明"可能失效"，没有制造一次真实的转谱失败。
8. **英文 Windows 下的 GBK `.bat`**：只在本机 936 控制台验证；`.gitattributes` 注释里"单字节码页下只会字符难看"这句没有实测条件。
9. **`http` 直连 shim 是否会被 `_GPU_SEM` 漏掉**：没有并发提交两个生成请求（禁忌），只从代码判断。
10. **`cryptography`/证书类依赖**：本轮未涉及。

---

## 附录 A · 变异验证记录（tests 的有效性）

在 `git checkout-index` 导出的临时副本里改坏被测代码，确认对应测试会红（原仓库未改动）。

**A-1 `main.py::_engine_body` 去掉 `request.options` 那一层**

```bash
rm -rf /tmp/yue2-mut && mkdir -p /tmp/yue2-mut/cpp/model_specs && git checkout-index -a --prefix=/tmp/yue2-mut/ && cp cpp/model_specs/yue2.json /tmp/yue2-mut/cpp/model_specs/ && cd /tmp/yue2-mut && /c/Users/HW/Desktop/YuE2/YuE2-Music-Workbench/py312/python.exe - <<'PY'
from pathlib import Path
p = Path("main.py"); s = p.read_text(encoding="utf-8")
old = '"request": {"task": "gen", **opts, "options": dict(opts)}'
assert old in s
p.write_text(s.replace(old, '"request": {"task": "gen", **opts}'), encoding="utf-8")
print("mutated")
PY
/c/Users/HW/Desktop/YuE2/YuE2-Music-Workbench/py312/python.exe -m unittest discover -s tests -p "test_shim_main.py" 2>&1 | tail -3
```

结果：`test_options_appear_at_both_levels` 与 `test_mutating_body_does_not_alias_opts` 双双 ERROR（`KeyError: 'options'`），`FAILED (failures=1, errors=4, skipped=1)`（其中 1 failures + 2 errors 是本机 `cpp/server.json` 缺席导致的 B7，与本变异无关；被本变异杀掉的正是那 2 条）。**测试有效。**

**A-2 `app.py::resolve_backend` 把"cuda 请求在 vulkan 构建上"改成落到 cpu**

```bash
rm -rf /tmp/yue2-mut3 && mkdir /tmp/yue2-mut3 && git checkout-index -a --prefix=/tmp/yue2-mut3/ && cd /tmp/yue2-mut3 && /c/Users/HW/Desktop/YuE2/YuE2-Music-Workbench/py312/python.exe - <<'PY'
from pathlib import Path
p = Path("app.py"); s = p.read_text(encoding="utf-8")
old = '        if "vulkan" in have:\n            return "vulkan", f"该引擎构建没有 CUDA 后端'
assert old in s
p.write_text(s.replace(old, '        if "vulkan" in have:\n            return "cpu", f"该引擎构建没有 CUDA 后端'), encoding="utf-8")
print("mutated")
PY
/c/Users/HW/Desktop/YuE2/YuE2-Music-Workbench/py312/python.exe -m unittest discover -s tests -p "test_backend_mode.py" 2>&1 | tail -2
```

结果：`test_gpu_means_vulkan_when_the_build_has_no_cuda`、`test_setter_turns_gpu_click_into_vulkan` 两条 FAIL，`FAILED (failures=2)`。**测试有效。**

**A-3 `app.py::_gen_run_once` 把 `finally: _rvc_keep_awake(False)` 改成只在成功路径撤销**

```bash
rm -rf /tmp/yue2-mut4 && mkdir /tmp/yue2-mut4 && git checkout-index -a --prefix=/tmp/yue2-mut4/ && cd /tmp/yue2-mut4 && /c/Users/HW/Desktop/YuE2/YuE2-Music-Workbench/py312/python.exe - <<'PY'
from pathlib import Path
p = Path("app.py"); s = p.read_text(encoding="utf-8")
old = '''            r = client.post("/api/music/generate", json=payload)
    finally:
        _rvc_keep_awake(False)'''
assert old in s
new = '''            r = client.post("/api/music/generate", json=payload)
    except Exception:
        raise
    _rvc_keep_awake(False)'''
p.write_text(s.replace(old, new), encoding="utf-8")
print("mutated")
PY
/c/Users/HW/Desktop/YuE2/YuE2-Music-Workbench/py312/python.exe -m unittest discover -s tests -p "test_gen_keep_awake.py" 2>&1 | tail -2
```

结果：`test_hold_is_released_even_when_the_call_explodes` FAIL（`- [True]` vs `+ [True, False]`），`FAILED (failures=1)`。**测试有效。**

**A-4 干净 clone 的全量基线**：`git checkout-index` 副本 + 直接跑，`test_shim_main.py` 在 import 期就 error（B7）。

---

*报告生成：2026-10-21。所有命令均在本机 Git Bash + `./py312/python.exe` 3.12.9 下原样执行过；标"推理·待实测"的只有 B19。*
