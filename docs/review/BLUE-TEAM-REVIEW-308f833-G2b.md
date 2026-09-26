# 蓝军 G2b 对抗性审查报告 — 安全 / 隐私 / 资源生命周期

- 仓库：`E:\AI\10AIMusic\Yue\yuE-2\yuE-2`，基线 commit `308f833` + 工作区未提交改动
- 审查日期：2026-09-26
- 立场：敌意审查。不采信注释与文档声称，一律回到代码与实测。
- 审查范围（严格）：`app.py`、`src/*.py`、`settings.py`、`static/index.html`（仅请求/URL 拼接与插值点）、`dsh-plugin/lib/client.js`、`dsh-plugin/src/ui-panel.mjs`（仅为核实 iframe 暴露面）、`scripts/*.py`、启动/停止 `.bat`。未通读 `cpp/`、`runtime/`、vendored 第三方库。
- 实证手段：`py_compile` 全量通过；纯函数/表达式离线复算（探针在 `%TEMP%\blue-g2b\`）；只读 `GET 127.0.0.1:7863/api/health`、`/api/ai/status`；`netstat` / `tasklist` / WMI 只读查询；一次隔离的 WMI 环境继承实验（自建临时子进程，未触碰任何在跑进程）。
- 禁令遵守：未重启/杀死任何进程、未运行 `.bat`、未调用任何创建类/删除类端点、未修改仓库内除本文件外的任何文件。

---

## 一、缺陷总表

| 编号 | 级别 | 一句话 | 维度 | 位置（文件:行号） |
|---|---|---|---|---|
| S1 | 🔴 | RVC 音色名里的单引号可逃出 `python -c` 源码字符串，实现本机任意代码执行 | 7 安全·命令/代码注入 | `app.py:2919-2923`（注入点）+ `app.py:2987`（放行的净化器） |
| S2 | 🔴 | `yue2workbench://` 协议处理器把 URL 未加引号拼进 `cmd /c`，任意网页可拉起并注入命令 | 7 安全·命令注入 | `scripts/register_protocol.bat:23` |
| S3 | 🔴 | 网关零鉴权且零 CSRF 防护：无 body 的 POST 与 GET 属 CORS「简单请求」，任意外部网页可停任务/杀引擎/触发重下/杀 :3081；叠 DNS 重绑定即可读全部歌词并同源发任意调用 | 7 安全·鉴权/CSRF | `app.py:104-117`、`settings.py:12`、`app.py:1343`、`app.py:366`、`app.py:3129`、`app.py:2555`、`src/ai_router.py:51-120` |
| S4 | 🔴 | 训练 worker 在 `finally` 无条件 `_GPU_SEM.release()`，而 acquire 之前还有可失败语句 → 释放未持有 → GPU 互斥闸永久失效 → 训练/生成并发把引擎打到 OOM | 13 资源与生命周期 | `app.py:2812-2817`、`app.py:2969-2970` |
| S5 | 🟠 | 启动脚本 `set` 出的环境变量对 WMI 派生的网关进程全部无效（已实证），镜像/缓存/ffmpeg 路径等承诺落空，首启与看门狗重启是两套环境 | 13/15 配置与生命周期 | `scripts/启动音乐工作台.bat:24-40,60`、对照 `watchdog.py:79-82`、`app.py:32-35` |
| S6 | 🟠 | `DEEPSEEK_API_KEY` 被明文塞进 node 进程的命令行（本机任意进程可读），与仓库自己写下的约定直接冲突 | 15 配置与密钥 | `scripts/启动dsh工作台.bat:28`、对照 `src/ai_router.py:97-98` |
| S7 | 🟠 | 删除任务不干净：`runtime/rvc/jobs/<rid>/`（用户上传的整首歌 + 分离干声/伴奏）全仓无一处清理；`.elrc`（完整歌词时间轴）不在删除清单 | 8 隐私与数据 | `app.py:2469-2472`、`app.py:3215`、`app.py:1590-1592`、`app.py:1010`、`app.py:3173-3180` |
| S8 | 🟠 | `POST /api/voices` 整读上传进内存且无任何大小上限，绕开自家流式闸门 | 13 资源 | `app.py:3257-3266`、对照 `app.py:809-833` |
| S9 | 🟠 | 为自愈 :3081 而对「监听 3081 的任意 PID」下 `taskkill /F /T`，不校验是否 dsh；一个 GET 就能杀掉用户别的程序 | 13 资源/7 安全 | `src/ai_router.py:75-91` |
| S10 | 🟠 | `ai_router` 硬编码 3081 与 token 正则，绕过 ports.json 单一真源，改端口即整块 AI 工作台失效 | 15 配置 | `src/ai_router.py:63,70,82,107`、对照 `src/ports.py` |
| S11 | 🟠 | `models_switch` 用净化前的 `path` 写 `server.json`（校验用的是另一个变量 `safe`）→ 相对路径逃逸引擎工作目录 | 7 安全·路径穿越 | `app.py:908-934` |
| S12 | 🟠 | `trust_remote_code=True` 加载仓库内 `checkpoints/*.py`：公开仓库被 PR/fork 投毒一个 .py，即对所有 pull 用户 RCE，且无 revision/哈希锁定 | 7 安全·外部代码加载 | `src/sheetsage_pt.py:110-114`、`src/sheetsage_pt.py:67-85` |
| Y1 | 🟡 | 当前绑 127.0.0.1（netstat 实证，正确），但没有一行断言阻止 `app_host="0.0.0.0"`，而全仓零鉴权 | 7 安全 | `settings.py:12` |
| Y2 | 🟡 | 子进程 stderr / 异常原文回传前端并写进 output meta → 绝对路径与账号名泄露 | 8 隐私 | `app.py:2001`、`app.py:2051`、`app.py:2103`、`app.py:2641`、`app.py:1236-1239` |
| Y3 | 🟡 | 前端 3 处 innerHTML 插值未过 escapeHtml，当前数据源恰好是服务端自造字段，一旦改为可控即成存储型 XSS | 7 安全·XSS | `static/index.html:2789`(`j.step`)、`:2801`(`j.preview_url`)、`:2683`(`m.size_mb`) |
| Y4 | 🟡 | `/api/health` 与 3081 静态响应都没有 X-Frame-Options / CSP frame-ancestors，工作台 UI 可被任意网页内嵌做点击劫持 | 7 安全 | curl 实测响应头、`dsh-plugin/src/ui-panel.mjs:250-264` |
| Y5 | 🟡 | `GET /api/rvc/models/{name}/check` 每次起一个 300s torch.load 子进程，可用 `<img>` 跨源反复触发 | 7/13 | `app.py:1971-1999` |
| Y6 | 🟡 | `.atomcode/`、`.workbuddy/` 未被 .gitignore 覆盖，`git add -A` 会把工具缓存/会话状态提交进公开仓库 | 15 配置 | `git check-ignore` 实测无输出、`.gitignore` 全文 |
| Y7 | 🟡 | `generate_start` 成功后又再调一次 `_make_job` 造出永不运行的第二个 ID；`_new_id()` 抛 503 时后台线程已启动，客户端看到失败而任务在跑 | 13 生命周期 | `app.py:1458`、`app.py:479-484` |
| Y8 | 🟡 | vendored `scripts/gtcrn.py` 与 `scripts/model_trained_on_dns3.tar` 无出处/许可声明，仓库根亦无 LICENSE（`checkpoints/` 反而齐全） | 19 许可合规 | `scripts/gtcrn.py:1-8`、`ls` 根目录无 LICENSE |
| Y9 | 🟡 | `if True:` 假条件包住「降级分支」，读起来像永远执行的主路径 | 13 可维护性 | `app.py:2264` |

---

## 二、🔴 逐条展开

### S1 · RVC 音色名 → `python -c` 源代码注入 = 本机任意代码执行

**证据链**

1. `app.py:2987` 的净化器只挡 `[\\/:*?"<>|\s]`：
   ```python
   name = re.sub(r'[\\/:*?"<>|\s]+', "_", name.strip())[:40] or "voice"
   ```
   单引号 `'`、`(`、`)`、`,`、`.`、`;`、`#`、`_` 全部放行，且**空白被替换成下划线**——这恰好是 PowerShell/Python 里最需要的字符反而不缺。
2. `app.py:2919-2923` 把 `name` 用 `%` 直接拼进一段 Python 源码，再交给 `python -c` 执行（经 `_rvc_run_step` → `subprocess.Popen`）：
   ```python
   "info = extract_small_model(r'%s', r'%s', '40k', 1, '%d epoch', 'v2'); "
   "print(info)" % (final_ckpt, name, epochs)
   ```
3. 离线复算（探针 `%TEMP%\blue-g2b\probe2.py`，用仓库自带 `py312/python.exe` 执行）：
   ```
   sanitized name = "a',__import__('os').system('calc'))#" len 36
   generated -c source:
      ... info = extract_small_model(r'C:\x\G_2333333.pth', r'a',__import__('os').system('calc'))#', '40k', 1, '200 epoch', 'v2'); print(info)
   parses as valid python? -> YES  ==> code injection confirmed
   ```
   `compile()` 通过，长度 36 ≤ 40 截断上限，注释符 `#` 吃掉尾部的括号与参数。**这不是理论风险，是语法上已经闭合的注入。**

**触发条件**：任何能 `POST /api/rvc/train`（multipart，字段 `name`）的主体；执行时机是训练流水线的「导出成品」步（训练完成后约数小时，或 `resume` 时更快）。上传样本要求 ≥300KB（`app.py:3011`），对恶意进程不是障碍。

**用户可见后果**：以网关进程权限（结合 S5 未证实的 WMI 派生上下文，可能是更高权限）执行任意代码——删改 `runtime/`、读 `secrets/local_env.bat`、把用户的歌声样本与 API key 外传，全部一条命令的事。用户侧只看到「音色制作完成」。

**修复方向**（按优先级）：
- 与其它步骤统一：`extract_small_model` 的参数**一律走 `sys.argv`**（同文件 `app.py:2067-2099` 的融合脚本就是这么写的，注释还写明「全部参数走 sys.argv，杜绝字符串拼接注入（裁定 B-1/N-3）」——同一个裁定在 2919 处被漏掉）。
- 净化器改为白名单：`re.sub(r'[^0-9A-Za-z_\u4e00-\u9fff-]', "_", ...)`，并显式拒绝 Windows 保留名（见 S4/C 探针）。
- 兜底：所有 `python -c` 的 `script` 常量与可变部分分离，可变部分只能是 argv。

---

### S2 · 协议处理器 `%1` 未加引号 → 本机命令注入

**证据链**：`scripts/register_protocol.bat:23`
```bat
reg add "HKCU\Software\Classes\yue2workbench\shell\open\command" /ve /d "cmd /c \"\"%START_BAT%\" %%1\"" /f
```
落盘的注册表值是 `cmd /c ""...\启动音乐工作台.bat" %1`。`cmd /c "..."` 会剥掉首尾引号，剩下 `"...\启动音乐工作台.bat" <URL原样>`；`%1` 处于**引号之外**，其中的 `&` `|` `<` `>` 被 cmd 当作命令分隔符。任何页面里 `location='yue2workbench://&calc.exe'` 即让 cmd 追加执行 `calc.exe`（单 token 命令不需要空格，绕开「空格被 URL 编码」这一常见反驳）。`.bat` 自身对 `%1` 的使用是安全的（`启动音乐工作台.bat:37` 用 `"%~1"=="no-open"` 带引号比较），注入发生在它被调用之前。

**触发条件**：用户一次性运行过 `register_protocol.bat`（交付说明里推荐过）；浏览器对自定义协议有一次「要打开 YuE2 Music Workbench 吗」确认——**这是唯一的实际屏障，而一个伪装成「点确定继续播放」的话术足以跨过它**。

**用户可见后果**：本机代码执行、任意程序被静默拉起；处理器写在 HKCU，重装/换浏览器都不自动消失。

**修复方向**：注册值改为 `cmd /c ""...bat" "%1""`（把 `%1` 也关进引号）或 `...bat" "%~1"` 之后在 .bat 内做严格前缀校验（只接受 `yue2workbench:launch` 这类固定串，其余拒绝）；同时在 `.bat` 开头 `if not "%~1"=="launch" exit /b 1`。注册脚本宜提供一次性 `--verify` 回读校验。

---

### S3 · 零鉴权 + 零 CSRF：外部网页可以打进来

**证据链**

1. `settings.py:12` `app_host="127.0.0.1"`；`netstat -ano` 实测：`127.0.0.1:7863`、`127.0.0.1:3081`、`127.0.0.1:8080` 全部只绑回环——**局域网裸奔这一条不成立，注释与实现相符**。
2. 但 `app.py` 全文 `grep "Depends(|api_token|Sec-Fetch|X-Requested"` **零命中**：`:7863` 上没有任何鉴权、没有 CSRF token、没有 `Sec-Fetch-Site`/`Origin`/`Host` 校验。
3. `app.py:108-117` 的 CORS 只放行 `127.0.0.1|localhost:{7863,3081}`。CORS 只挡「浏览器读响应」，**挡不住已经属于简单请求的副作用**。`fetch` 带 `Content-Type: application/json` 会触发预检而被挡，但下面这些根本不需要 JSON body：
   - `POST /api/generate/stop`（`def generate_stop():` 无 body，`app.py:1343-1344`）→ 跨源 `<form method=post enctype="application/x-www-form-urlencoded">` 直接 `form.submit()` 即执行：把用户正在跑的生成标 cancelled、`_kill_audiocpp_now()` 强杀引擎、并取消整个批量队列（`app.py:1360-1367`）。
   - `POST /api/models/repair`（无 body，`app.py:366-375`）→ 后台起线程重下最多 ~2.7GB。
   - `POST /api/rvc/train/pause/{rid}`、`POST /api/rvc/train/preview/{rid}`（无 body，`app.py:3129`、`app.py:2555`）→ 打断/挤占数小时训练（preview 还会 `_require_headroom_for_preview` 抢 CPU）。
   - **`GET /api/ai/web`**（`src/ai_router.py:51`）→ 简单请求，见 S9：杀 :3081 监听者 + `Popen` 分离 node 进程。
   - `GET /api/rvc/models/{name}/check`（`app.py:1971`）→ `<img src>` 反复触发 300s torch.load 子进程。
4. 只读 `curl 127.0.0.1:7863/api/health` 实测 200，无 `X-Frame-Options`/CSP；再叠 **DNS 重绑定**（uvicorn/FastAPI 不校验 Host）：恶意页面把自身域名的 TTL 设极短后指向 127.0.0.1，即可让浏览器以「同源」身份读 `/api/generate/list`（含全部 style/歌词/参数，`app.py:1489-1498`）、`/api/voices`（含用户的参考文本）、`/api/history`，并以同源 `fetch` 发任意调用（含 JSON body 与 DELETE）——S1 的 RCE 与「删干净用户历史」在这一层变成远程可达。

**用户可见后果**：用户在跑 40 分钟的生成时打开一个带恶意广告的网页，任务被终止、引擎被杀、需要重跑；或（重绑定路径）歌词、音色库、任务记录被静默读走，音色名被植入 S1 载荷。

**修复方向**：
- 全部 `@router` 变更类端点要求一个自定义头（如 `X-Yue2-Client`）——简单请求无法携带自定义头，且它会强制预检；预检在 `allow_origins` 白名单外直接失败。
- 加一层 `@app.middleware("http")`：拒绝 `Origin` 存在且不在白名单的请求；拒绝 `Sec-Fetch-Site: cross-site`；把 `request.headers["host"]` 限定在 `127.0.0.1:7863|localhost:7863|[::1]:7863`（这一条专杀 DNS 重绑定）。
- 启动时生成 `secrets.token_urlsafe()`，写进页面模板，之后要求 `?token=` 或 `Authorization: Bearer`——顺带解决 Y1「改一行 0.0.0.0 就全裸」的问题。
- 给 `/`、`/api/*` 与 3081 静态响应统一加 `X-Frame-Options: DENY` / `Content-Security-Policy: frame-ancestors 'self'`。

---

### S4 · `_GPU_SEM` 释放未持有 → GPU 互斥闸永久失效

**证据链**
```
2812  try:
2813      n_p = max(1, (os.cpu_count() or 4) // 2)
2814      ds = RVC_TRAIN_DIR / rid / "dataset"
2815      exp_logs.mkdir(parents=True, exist_ok=True)     # ← 会在这里抛
2817      _GPU_SEM.acquire()                              # ← 还没拿到闸门
...
2969  finally:
2970      _GPU_SEM.release()   # 与上方 acquire() 配对，异常路径也必须释放闸门   ← 注释是错的
```
`threading.Semaphore` 的 `release()` 不校验是否持有，计数可以超过初值。离线复算（`probe2.py` C/D 段，同一解释器）：
```
== C ==  name=CON  sanitizer_keeps_it=True  exp_logs.mkdir -> NotADirectoryError: [WinError 267] 目录名称无效。
== D ==  after failed worker, _value = 2
         two concurrent heavy jobs both admitted? 1st=True 2nd=True  ==> GPU gate defeated
```
即：音色名 `CON`/`AUX`/`COM1`/`LPT1`（S1 那个净化器全部放行，实测 `mkdir` 在 `exist_ok=True` 下仍抛 `NotADirectoryError`）→ 走到 `except Exception`（`app.py:2956`）写 error meta → `finally` 释放一个从未取得的许可 → `_GPU_SEM` 永久变成 2。此后 `_gen_run`（`app.py:1433`）、批量 worker（`app.py:1683`）、换声（`app.py:2345`）与训练可**两两并发**，6GB 卡必然显存 OOM；`_gen_run` 还会因此触发「自动降级 CPU」的假象，用户看到的就是「突然慢十倍」。同一进程生命周期内不可恢复（只有重启网关）。除保留名外，触发面还包括杀软/OneDrive 占用 `logs/`、路径过长、磁盘满——都是本机真实模式。

**修复方向**：把 `acquire()` 移到 `try` 之外（`_GPU_SEM.acquire(); try: ... finally: _GPU_SEM.release()`），或给 `release()` 加持旗标；并用 `with _GPU_SEM:` 包住第 4 步以后的主体。同时净化器拒绝保留设备名（`CON PRN AUX NUL COM1-9 LPT1-9`），见 S1 修复。

---

## 三、🟠 逐条展开

### S5 · 启动脚本给网关设的环境变量一个都没进网关进程（已实证）

**证据链**：`scripts/启动音乐工作台.bat:24-40` 用 `set` 配好 `HF_ENDPOINT=https://hf-mirror.com`、`HF_HOME=%cd%\runtime\hf_download`、`TORCH_HOME=%cd%\cache`、`PATH=%PYTHON_PATH%;…ffmpeg…sox…`，随后 `:60` 用 `[wmiclass]'Win32_Process').Create('%cd%\py312\python.exe -s %cd%\app.py', '%cd%', $sw)` 拉起网关。WMI 的 `Create` 由 WmiPrvSE 派生进程，只带注册表级（登录时）环境。隔离实验（`%TEMP%\blue-g2b\wmienv.ps1`，父目录 `C:\Users\59221\` 可读，未触碰任何在跑进程）：
```
ReturnValue=0
child sees BLUEG2B_MARKER : False        ← 调用方运行时 set 的变量丢失
child PATH starts with fake: False       ← 调用方 set 的 PATH 前缀丢失
child sees DEEPSEEK_API_KEY: True        ← 只有注册表级持久变量能穿透
child HF_ENDPOINT present  : False       ← 镜像端点没送达
```
同仓两处旁证：`watchdog.py:79-82` 的注释「Win32_Process.Create 是由 WMI 服务派生的，不会自动继承上面这些 set（特别是 NO_PROXY…）」——**作者已经知道这个语义并专门补偿过一次**；`app.py:32-35` 在 import 期手工兜底 `NO_PROXY`，也是同一根因留下的补丁。

**触发条件**：走双击 `.bat` 的正常启动路径。用户把 key 同时写进了系统环境变量（本机就是这样，`ready:true` 实测），所以密钥这条被掩盖；被掩盖不了的失效项是：`HF_ENDPOINT` 镜像（MERT/Hub 下载在国内直连 huggingface.co，脚本注释承诺「大陆镜像加速」）、`TORCH_HOME`/`HF_HOME` 落在 `C:\Users\LA\.cache` 而非项目下（与「所有缓存在项目下」的交付承诺不符，且换机/搬家会重新下 1.6GB）、看门狗重启后反而全部生效 → **同一台机器上「第一次启动」和「被自愈过一次」是两个不同环境**，排障时极具误导性。

**修复方向**：不要用 WMI 传环境。两个可选：(a) 保留无窗口目标，改由 `watchdog.py` 负责首次拉起（它已具备 env 注入与 `stdout=DEVNULL` 的做法），`.bat` 只负责启动看门狗；(b) 把 `启动dsh工作台.bat:28` 已在用的 `cmd /c set A=..&& set B=..&& python -s app.py` 命令行注入形式复制到网关那一行（注意 S6 的教训：密钥不要走这条路）。同时给 `app.py` 启动期加一条自检：若 `HF_HOME` 不在 `ROOT/runtime` 下就 `logger.warning` 并在 `/api/health` 里带一个 `env_warning` 字段，让「环境丢了」可见。

### S6 · API key 明文进命令行

`scripts/启动dsh工作台.bat:28` 把 `set DEEPSEEK_API_KEY=%DEEPSEEK_API_KEY%` 拼进 `$cmd` 字符串再交给 `Win32_Process.Create` → 密钥成为 node 进程**命令行的一部分**，任何本机进程用 `Get-CimInstance Win32_Process` 或任务管理器即可读到（本次审查就是用同类只读查询拿到别的进程命令行的，见 `Get-CimInstance ... CommandLine` 输出）。同仓库 `src/ai_router.py:97-98` 明确写着「用 env 字典传密钥，不走 cmd/PowerShell 字符串拼接：避免特殊字符注入命令行，**也避免密钥出现在进程命令行（WMI 可见）**」——约定已在 Python 侧落实，`.bat` 这一路违背了它。次生问题：密钥若含 `'` 或 `"`（PS 外层是双引号、内层是单引号字面量），该行直接解析失败 → 3081 静默起不来，用户只看到「dsh 未运行」。
**修复**：`Win32_ProcessStartup` 没有 env 参数，因此正确做法是让 `.bat` 调用一小段 `node -e` / `python` 启动器，由它以 `subprocess.Popen(env=...)` 派生 node；或把 key 写进 dsh 自己的配置文件（`_dsh_home` 下、已 gitignore）由内核读取。

### S7 · 删除不干净：用户音频与歌词残留在盘上

- 换声每次提交都建 `runtime/rvc/jobs/<rid>/`（`app.py:2469-2472`），里面是用户上传的**整首原曲** `src.mp3`、`sep/<stem>_vocals.wav`（分离出来的人声干声）、`sep/<stem>_other.wav`（伴奏），以及 RVC 中间产物。全仓 `grep RVC_JOB_DIR` 只有 3 处：`_id_busy` 探测（`:466`）与两处 `mkdir`（`:2481`、`:3215`），**没有任何一处删除**；`DELETE /api/generate/{rid}`（`:1581-1604`）只清 `output/` 五件套与 batch_state 记录；`DELETE /api/rvc/train/{rid}`（`:3173-3180`）只 rmtree `trains/`。用户在任务管理页点「删除」后，最敏感的那份（他的声音干声 + 版权歌曲 mp3）仍在盘上，且随时间线性堆积（每次最多 200MB×N）。
- `generate_delete` 的清单（`:1590-1592`）漏了 `.elrc`：强制对齐会写 `OUTPUT_DIR/<rid>.elrc`（`:1010`），内容是**完整歌词 + 逐句时间轴**，删除任务后残留，且 `/api/generate/lrc/{rid}` 读它（`:1556-1558`）。
- 静态目录裸奔：未发现（`/api/rvc/audio`、`/api/generate/audio`、`/api/history/{id}/audio` 都走 `os.path.basename` + 固定目录，无 `StaticFiles` 挂载 `runtime/`），`git ls-files` 亦确认无任何 wav/mp3/pth 入库（仅 1 张 1MB 截图）。遥测：`src/sheetsage_pt.py:34` 主动设 `HF_HUB_DISABLE_TELEMETRY=1`，仓库内无第三方上报地址。

**修复**：`generate_delete` 对 `kind in ("rvc","train")` 追加 `shutil.rmtree(RVC_JOB_DIR/rid, ignore_errors=True)` 并把 `.elrc` 纳入删除清单（或让 `.elrc` 与 `.lrc` 同生命周期）；另加一个启动期「孤儿 jobs 目录」清扫（对照 `_orphan_cleanup_on_startup` 已有形态），并给 `runtime/rvc/jobs/` 设配额上限。

### S8 · `/api/voices` 整读内存、无上限

`app.py:3263` `data = audio.file.read()`，随后 `voices.save_voice(..., data, suffix)` 落盘；同文件 `:809-811` 的注释写着「旧写法 `await file.read()` 会让 200MB 上传先吃光内存再判超限」并为此造了 `_stream_upload_to`——但音色库这一条没换过去，`/api/rvc/convert`、`/api/score`、`/api/history`、`/api/voices/transcribe`、`/api/voices/denoise` 都用了流式闸门（`:682`、`:2472`、`:846`、`:3284`、`:3302`），只有这里绕过。叠加 S3（跨源 multipart 表单可提交小文件）与零鉴权，任何本机进程/任意网页（诱导用户点一次「选文件+提交」）都能把网关的内存打穿：12GB 机器上连传 5 个 2GB 文件即 OOM，正在跑的生成随之死掉；同时没有大小上限还意味着 `runtime/voices/` 可被无限撑满。
**修复**：`await _stream_upload_to(audio, tmp, 200*1024*1024, "参考音频")` 后再 `read_bytes()` 交给 `voices.save_voice`；并给音色库加总容量配额。

### S9 · 自愈逻辑会杀掉用户的别的程序

`src/ai_router.py:75-91`：`GET /api/ai/web` 若发现 3081「占用但不响应」，就 `netstat -ano -p tcp` 找出监听 :3081 的 PID 并 `taskkill /F /T`，只排除了 `os.getpid()`，**不校验它是不是 dsh node**。而 `:93` 的判定是 `url = _read_token() if _alive() else None`——`_alive()` 只要 `httpx.get("http://127.0.0.1:3081/")` 不抛异常就算活；`_read_token()` 依赖一条把 3081 硬编码的正则（`ai_router.py:63`）。于是：用户自己在 3081 跑着别的东西（vite/其它 UI），或 `_dsh_web.log` 被轮转/截断导致正则取不到 token，都会走到 `_kill_stale_listener()`，把一个完全无关的进程连子进程树强杀——未保存的工作直接丢失，且用户不会收到任何提示（整个函数被 `except Exception: pass` 包住）。结合 S3，这个 GET 还能被外部网页触发。
**修复**：杀之前用命令行校验 `Win32_Process` 的 `Name == 'node.exe'` 且 `CommandLine` 含 `dsh/lib/bin.js web --port <DSH_PORT>`，否则只报错不动手；把「将强杀端口占用者」写进响应并让用户确认；所有 PID 操作加日志。

### S10 · ai_router 绕过端口单一真源

`src/ai_router.py` 里 `3081` 出现在 token 正则（`:63`）、存活探测（`:70`）、netstat 匹配（`:82`）、node `--port`（`:107`）四处硬编码；而 `src/ports.py` + `ports.json` 的整个设计意图就是「改端口只改一处」（其模块注释还点名历史上散落 7863/3081/8080 导致「502/白屏」）。用户按 `_readme` 改 `ports.json` 的 dsh 端口后：`ai_router` 仍拉 3081、token 正则永不命中 → `GET /api/ai/web` 必定 60 秒超时后 503，AI 工作台整块不可用，而报错文案只说「查看 _dsh_web.log」，用户完全找不到真因。
**修复**：`from ports import get as _port`，四处统一用 `_port("dsh")`，token 正则改 `rf"http://127\.0\.0\.1:{p}/\?token=..."`。

### S11 · 模型切换把未净化的路径写进引擎配置

`app.py:908-934`：`path` 先 `\`→`/`，再用 `safe = Path(path).name` 校验 `MODEL_DIR/safe` 存在（`:912-915`），但**真正写进 `server.json` 的是原始 `path`**（`:934` `model_entry["path"] = "model/" + path`）。因此 `{"path": "anything/../../../../evil.gguf"}` 只要 `cpp/model/anything.gguf`… 实际只需 `MODEL_DIR/<最后一段>` 是真实文件，就能让 `models[].path` 变成带 `../` 的逃逸相对路径；`vae` 那一侧（`:919-920`）反倒是净化过的，同一个函数里两套标准。audio.cpp 以 `cpp\` 为工作目录按该 path 打开模型 → 越界读取任意 `.gguf`/任意文件（GGUF 解析失败即引擎崩溃循环），并且 `_restart_audiocpp`（`:941`）会立刻真的重启引擎——用户此刻若正在生成就被拦腰截断。这条不是「读任意文件给用户看」，而是「把越界路径写进被信任的配置文件并让引擎去读」，属于配置完整性问题，但可稳定造成引擎不可用。
**修复**：`model_entry["path"] = "model/" + Path(path).name`（与校验同一个变量）；或校验 `Path(path).resolve().is_relative_to(MODEL_DIR.resolve())` 后写相对形式；写完 `_read_server_json()` 回读断言路径未越界。

### S12 · `trust_remote_code=True` 加载仓库内 Python

`src/sheetsage_pt.py:110-114` 对 `resolve_checkpoint()`（`checkpoints/`，见 `:67-85`）调用 `AutoModel.from_pretrained(str(ckpt), trust_remote_code=True)`。`git ls-files checkpoints/` 实测 `modeling_sheetsage2.py`、`generation_sheetsage2.py`、`configuration_*.py`、`tokenization_sheetsage2.py`、`infer.py` 等**全部已提交进这个公开仓库**，而 `model.safetensors` 被 .gitignore 排除。也就是说：模型权重要用户自己放，**可执行代码却随仓库分发并在 `/api/score` 第一次调用时 `import` 执行**。任何针对 `checkpoints/*.py` 的 PR/fork 投毒、或上游账号被劫持，都会让每个 `git pull` + 点一次「提取乐谱」的用户本机执行任意代码；本地层面，能写 `checkpoints/` 的任意进程即获得同等效果（无需 S1 那种注入）。HF 官方对这一模式的处置（revision 锁定、`HF_HUB_OFFLINE`、代码哈希）这里只做了半件（`:58-65` 的离线开关）。
**修复**：`local_files_only=True` + 首次联网下载后把 `auto_map` 指向的 .py 的 sha256 记进一个 lockfile，加载前逐文件校验；更彻底的做法是把 SheetSage2 的 remote code  vendored 成显式 `import` 的包（走正常的代码审查），并把 `config.json` 的 `auto_map` 删掉——让 `trust_remote_code=False` 也能加载。至少：在 README 里写明这段代码来自哪个上游 commit。

---

## 四、🟡 清单（每条只给方向）

| 编号 | 位置 | 修复方向 |
|---|---|---|
| Y1 | `settings.py:12` | 启动期断言：`app_host` 非回环时必须要求 `YUE2_ALLOW_LAN=1` 且 token 已生成，否则拒绝启动 |
| Y2 | `app.py:2001,2051,2103,2641,1236` | stderr/异常原文只进日志文件，对外返回错误码 + 摘要；`_output_write_meta` 的 `error` 同样脱敏（去掉 `\\` 绝对路径段） |
| Y3 | `index.html:2789,2801,2683` | 三个插值统一过 `escapeHtml()`（`preview_url` 拼进 `src=""` 属性，最该先修） |
| Y4 | `/api/health` 响应头实测无 XFO/CSP；`ui-panel.mjs:250-264` 同样缺 | 网关与 3081 静态响应统一加 `X-Frame-Options: DENY` + `frame-ancestors 'self'`（`/lab/` 自身是被 dsh 同源内嵌的，不受影响） |
| Y5 | `app.py:1971-1999` | 体检端点加 `If-None-Match` 式结果缓存或改 POST，避免 `<img>` 反复起 300s 子进程 |
| Y6 | `git check-ignore .atomcode .workbuddy` 无输出 | 追加两行进 `.gitignore` |
| Y7 | `app.py:1458` | 复用 `_make_job` 已生成的首个 job 作响应，不再二次 `_new_id()` |
| Y8 | `scripts/gtcrn.py:1-8`、根目录无 LICENSE | 补 vendored 文件出处 + LICENSE 段落，并给仓库根本身补一份 |
| Y9 | `app.py:2264` | 把 `if True:` 换成显式的降级分支条件（或删除包装） |

---

## 五、维度覆盖声明

1. **7 安全**
   - 路径穿越：**未发现可利用漏洞**。全部文件类端点（`/api/scores/{id}`、`/api/history/{rid}/audio`、`/api/generate/{audio,lyrics,lrc}/{rid}`、`/api/rvc/{status,audio,train/preview,train/status}`、`/api/rvc/models/{name}/*`）都做了 `os.path.basename(rid)`，Windows 语义下 `\` 也被分段；`/api/scores/{id}` 与 `/api/rvc/audio` 的 `part` 还额外加了 `p.parent == DIR` 与 `re.fullmatch("[a-z_]+")`。3081 的静态托管 `resolveStatic()`（`ui-panel.mjs:231-238`）用 `path.resolve` + 前缀比对，`/lab/%`（畸形转义）会抛在 async 函数里，但**同一函数在 `try` 内**（`:246`），只产生 500/未处理 rejection，不崩服务。**唯一真实穿越是 S11**（写进 server.json 的配置路径）。
   - 任意文件读写：未发现直接读写原语（无用户可控 `open()` 路径）；S11 属间接。上传落盘文件名一律服务端生成（`sample_%03d`、`score_<id>`、uuid12），原始 filename 只取 `suffix` 且过白名单（`app.py:675-677`、`:2465-2467`、`:3005-3007`、`voices.py:91-93`）——**这块做得对**。
   - SSRF：`_restart_audiocpp`/`httpx` 目标恒为 `settings.audiocpp_base_url`（127.0.0.1:8080），用户不给 URL/端口；`/api/models/repair` 的下载源来自固定 manifest + `HF_ENDPOINT` 环境变量（`scripts/download_models.py:53`），非请求可控。**未发现 SSRF**。注意 `app.py:32-35` 把 NO_PROXY 写死含 127.0.0.1，方向正确。
   - 命令注入：全仓 `subprocess` 均为 list 形式、**无 `shell=True`**（已 grep 证实）；`powershell -Command` 的文本参数在 `_win_toast`（`app.py:1194-1201`）用 base64 传参，处理得当。**两处真实注入**：S1（`python -c` 源码拼接，🔴）、S2（协议处理器，🔴）；另有一处同源退化 S1-B/探针 B：`app.py:3162` 把 `re.escape(name)` 塞进 PowerShell 单引号串——`re.escape` **不转义单引号**（实测该片段含 5 个 `'`），音色名带 `'` 会让整条 sweep 解析失败 → 暂停兜底扫杀静默失效，残留训练进程继续占显存（归入 S1 同族修复：argv/白名单）。
   - `trust_remote_code`：**S12 命中**（🟠）。另 `torch.load`（`app.py:1983,2043,2073`、`src/denoise.py:49`）读的是 pickle 格式 `.pth`/`.tar`，本身就是任意代码执行面，但输入只来自本机已放置的权重文件，未与用户可控路径拼接（`os.path.basename` + 固定目录），评 Y 级；建议 `weights_only=True`。
   - CORS/绑定地址：`netstat` 实证三端口全绑 127.0.0.1，CORS 白名单只放本机 4 个 origin，**注释与实现相符**（这是本次审查少数「声称成立」的项）。但 CORS 挡不住简单请求副作用 → S3。
   - 鉴权：`:7863` 无 token（grep 证实零 Depends/零校验）；`:3081` 有 token（`ai_router.py:63` 从 `_dsh_web.log` 抓 `?token=`，且该 log 已 gitignore），但 `ui-panel.mjs` 的 `/lab-api/*` 把 **所有方法**无校验反代给无鉴权的 `:7863`（`:280-288`）→ 只要 3081 的绑定哪天改成 0.0.0.0，删除/训练/RCE 面立即对局域网全开。iframe 本身未发现把无鉴权端口暴露到局域网（3081 仍回环）。
   - XSS：前端 30 处 `innerHTML` 全部逐一看过，歌词/任务名/文件名/错误消息/队列名/音色名/ID **均过 `escapeHtml()`**（`:943-945` 实现正确，含 `'`），toast 与 ABC 分析摘要用 `textContent`（`:903-906`、`:1820`）。**未发现当前可利用点**；3 处潜在未转义插值列 Y3。`client.js` 侧 `postMessage` 用 `"*"` 发送但**接收端做了 `e.origin !== location.origin` 校验**（`client.js:172`），且写 iframe `documentElement` 属性受同源限制，未发现可利用路径。
2. **8 隐私与数据**：日志/错误消息回传绝对路径与账号名成立（Y2，前端已转义不构成 XSS）；上传的人声样本/训练数据落 `runtime/`、`runtime/voices`、`runtime/rvc/{jobs,trains}`，**全部 gitignore 且无静态目录挂载**，未被裸奔公开；**但删除任务删不干净——S7 是真实缺陷**（换声原曲/干声永不删、`.elrc` 残留）；遥测未发现（主动关闭了 HF 遥测）。
3. **13 资源与生命周期**：子进程清理面整体做得比同类项目认真——`_orphan_cleanup_on_startup`（`app.py:3372-3426`）重启后把 running 落盘任务改判中断、批量队列自愈；`_rvc_run_step` 用 `communicate(timeout)` + `kill()`（`:2779-2787`）；pause 有 terminate + `taskkill /T` 双保险。发现的真实问题：**S4 信号量过度释放（🔴）**、S9 强杀无关进程（🟠）、`app.py:3162` 扫杀表达式可被音色名打断（🟠/S1 同族）、S5 环境丢失导致行为分叉（🟠）、Y7 幽灵任务 ID。全局状态并发方面：`_GEN_JOB`/`_BATCH`/`_RVC_JOBS`/`_RVC_TRAIN_*` 都有锁，且 `generate_start` 的 check-and-set 显式持锁（`:1384-1389`）、`_BATCH_LOCK` 内刻意用无锁读写避免自死锁（`:1360-1361` 注释与 `:1926` 实现一致）——**这块是对的**；后台写文件除 `_output_write_meta`/`_rvc_train_write` 外未见裸写，`_rvc_train_write` 还用 `os.replace` 原子替换（`:2737-2740`），`_hist_write`/`_batch_write`/`_save` 三个 JSON 写盘非原子（崩溃可留半截文件，读侧有 `except: return []` 兜底 → 表现为「历史整块消失」，评 Y 级，建议统一 `os.replace`）。定时器：`client.js` 的 3 个 `setInterval` 都有 `clearInterval` 清理路径，但 `MusicStudio` 的 favicon 轮询（`client.js:331-347`）**没有 return 清理**，用的是阻塞 `XMLHttpRequest`，面板反复挂载会叠加同步轮询（🟡，未列入总表以控篇幅）。文件句柄：`_stream_upload_to` 用 `with open`，`log_fd = open(log,"ab")`（`ai_router.py:104`）在 Popen 后未关闭 → 每次拉起 dsh 泄漏一个句柄（🟡）。
4. **15 配置与密钥**：`grep -rn "sk-|api_key|token =|Bearer "` 全范围命中均为「从环境变量读取/报错提示」，**仓库内无硬编码密钥**（`secrets/local_env.bat` 存在且 `git ls-files` 确认未跟踪，只有 `.example` 版本入库）；`git ls-files` 亦确认无任何模型权重/音频入库。真实问题：S6 密钥进命令行（🟠）、S10 端口单一真源被绕过（🟠）、Y6 `.atomcode/`/`.workbuddy/` 未忽略、`ports.json` 的 `YUE2_*_PORT` 环境变量覆盖可把网关换端口而不换 CORS 白名单？——不成立，白名单从 `settings.app_port` 派生（`app.py:106-113`），同源；此项无问题。
5. **19 许可合规（按范围只查一点）**：`checkpoints/` 保留了 `LICENSE` 与 `THIRD_PARTY_NOTICES.md`（值得肯定）；但 **`scripts/gtcrn.py` 是被剥掉出处头的 vendored 实现（仅留模型结构 docstring），`scripts/model_trained_on_dns3.tar` 是随仓权重且无任何声明文件，仓库根本身没有 LICENSE**（`ls` 确认）。列 Y8，不展开。

---

## 六、优先级路线图

**P0（本周内，直接决定「会不会被真实打穿 / 会不会自己踩死自己」）**
1. S1 + 同族（`app.py:2919-2923`、`:3162`）：所有 `python -c` / `powershell -Command` 的可变部分改 argv 或严格白名单；音色名净化改白名单并拒绝 Windows 保留名。
2. S4：`_GPU_SEM.acquire()` 移出 `try`。一行改动，消除「跑过一次失败训练，之后 GPU 互斥永久失效」。
3. S3：加一层「无 Origin/Sec-Fetch-Site/Host 白名单 → 403」的中间件 + 变更类端点要求自定义头。这一步同时压掉 S9 的远程触发面。
4. S2：`register_protocol.bat` 的 `%1` 加引号并在 `.bat` 内做固定串校验；已注册的老用户给一条反注册/修复命令。
5. S6：把密钥从命令行挪走。

**P1（下个迭代，功能正确性与隐私承诺）**
6. S7：`generate_delete` 级联清 `rvc/jobs/<rid>` 与 `.elrc`，启动期孤儿清扫 + `rvc/jobs` 配额。
7. S5：网关首启也走 watchdog/直接 Popen（能传 env 的路径），并在 `/api/health` 暴露 env 自检结果。
8. S8：`/api/voices` 接 `_stream_upload_to` + 库容量上限。
9. S11：`models_switch` 写 `safe` 而非 `path`，写后回读断言。
10. S9 + S10：`_kill_stale_listener` 加进程身份校验；`ai_router` 改读 `ports.py`。
11. S12：remote code 的 sha256 锁定（或 vendored 成显式 import）。

**P2（技术债，可批量）**
12. Y1 绑定断言、Y2 错误脱敏、Y3 三处 escape、Y4 安全响应头、Y5 体检端点缓存、Y6 `.gitignore` 两行、Y7 幽灵 ID、Y8 vendored LICENSE、Y9 `if True:`；以及 13 维度里提到的 JSON 写盘原子化、`log_fd` 句柄关闭、favicon 轮询清理。

---

## 七、做得对、值得保持的

- **路径处理纪律**：所有 `{id}/{name}` 型路由都过 `os.path.basename`，且在 Windows 下显式先 `\`→`/` 再 basename（`app.py:691,733,742`），`/api/scores/{id}` 还二次断言 `p.parent == SCORES_DIR`；`part` 参数走 `[a-z_]+` 全匹配。这是同类 FastAPI 项目里少见的认真程度，后续新增端点请沿用同一模式（别再出现 S11 那种「校验一个变量、写另一个变量」）。
- **上传一律不信任文件名**：只取 `suffix` 且过扩展名白名单，落盘名服务端生成；配合 `_stream_upload_to` 的分块 + 边写边判限 + `.part` + `os.replace` 原子替换 + 异常清理残文件，比「先 read 再判大小」的老写法结实得多（除了 S8 那一处漏网）。
- **任务 ID 唯一性**：`_new_id()`（`app.py:472-484`）秒级时间戳 + 32 位随机 + 进程内 set + 磁盘 `_id_busy` 双查——把「ID 就是产物文件名，撞号等于静默覆盖别人的音频」这个后果想清楚并付了实现成本，值得保持。
- **`_win_toast` 用 base64 传文本进 PowerShell**（`app.py:1194-1201`）：正确规避了 `$`/引号/反引号破坏 PS 表达式，是本次全仓唯一一处「字符串进 shell 却做对了」的示范。
- **并发与自愈结构**：`generate_start` 的占位式 check-and-set（`:1384-1389`）、`_BATCH_LOCK` 内改用无锁读写避免非重入死锁（`:1360-1361`）、`_rvc_train_write` 原子替换、启动期孤儿任务改判 + 批量队列自恢复、`_RVC_CHECK_LOCK`/`_RVC_PREVIEW_LOCK` 用 `acquire(blocking=False)` 做「原子抢锁 + 409」而非 TOCTOU——这些是真实踩坑后长出来的正确结构。
- **绑定地址与仓库卫生**：三端口实测 `127.0.0.1`（不是 0.0.0.0）；`runtime/`、`voices`、`*.wav/mp3/pth/gguf/safetensors`、`_dsh_web.log`（含 token）全部 gitignore，`git ls-files` 复核无用户音频与权重入库；`.atomcode`/`.workbuddy` 是仅存的两个漏网目录。
- **前端转义纪律**：`escapeHtml` 覆盖 `&<>"'`，列表/历史/错误/任务名/文件名/ID 一律走它，动态文本用 `textContent`。本次逐条核对 30 处 `innerHTML` 未发现可利用点。

---

## 八、未验证项（如实声明）

1. **S1/S2 的端到端利用未执行**。只做了「离线复算净化器 + `%` 拼接结果并 `compile()` 通过」「注册表命令串的 cmd 引号语义静态推演」，未真的提交训练任务、未真的注册协议、未真的起 calc（禁令：不得创建任务、不得跑 `.bat`、不得改仓库）。语法闭合 ≠ 完整利用链跑通。
2. **S3 的浏览器侧行为未按实证**：简单请求免预检、`form.submit()` 能命中无 body 端点属规范推理 + 端点签名核对（`generate_stop()`/`models_repair()` 确无参数），未在真浏览器里跨源发起（禁令不允许 POST/DELETE，仅允许 GET）。DNS 重绑定路径是推理链（uvicorn 不校验 Host + 无 Origin 校验），未搭建 rebinding 环境验证。
3. **S4 的触发概率**：`exp_logs.mkdir` 抛错用了 Windows 保留名（`CON`/`AUX`）作确定性触发，已在临时目录实证异常类型；但「用户真的会取名叫 CON」未验证，其它触发（杀软占用/路径过长/磁盘满）为可能性而非实证。信号量过度释放本身（`_value=2`、两并发同时通过）已离线实证。
4. **编译内核 `main.cp312-win_amd64.pyd` 未审**（不可修改且无源码）。它自带 `/`、`/api/health`、presets 等路由（`app.py:3` 文件头与 `:3489` 注释提到「编译网关自带、带硬编码 403 门槛的那条」）以及可能的静态挂载与 httpx 客户端行为，均**未纳入本报告**——`3081 → 7863` 只反代 `…/api/*`（`ui-panel.mjs:280-325`）这一点缩小了面，但内核自身路由的鉴权/穿越状态是不可知的，属于本次审查的真实盲区。
5. **`cpp/audiocpp_server.exe`（:8080）未审**（范围外）。`/api/health` 实测其 `"ui":true`，即引擎自带 Web UI；它绑在 127.0.0.1 上且无鉴权，若将来有人把 `audiocpp_base_url` 改指外网或反过来把 8080 暴露，S3 的结论要重做。
6. **WMI 派生进程的用户身份未确定**。实验证明了「运行时 env 不继承」，但没证明网关是 `LA` 还是 `SYSTEM` 所有（`tasklist /v` 在本机取不到 owner 列，`psutil` 在 `py312` 未安装，父 PID 7236 已退出）。因此 S5 只报「环境变量丢失」这一已证部分；「若为 SYSTEM 则 S1 提权到 SYSTEM」保留为未验证推论。
7. **`src/chunking.py`、`src/denoise.py`、`src/mcp_server.py`、`src/asr.py` 的模型来源校验只做了一次阅读级检查**：`asr.py:22-45` 有加载前守卫、`mcp_server.py` 仅 `mcp.run()`（未见网络绑定），未逐行核。`SenseVoice` 走 `model_source='local'`，但 `allowed_special` 一类 hub 侧参数未逐字确认（范围控制）。
8. **许可合规按指令只查一点**，未做完整依赖许可证矩阵（`py312/` 未入库，`dsh-plugin/node_modules` 未入库，故第三方面本身也不在仓库里）。
9. **`static/index.html` 的 `LAB_BASE`/URL 拼接只做了「与请求/URL 拼接相关」的定向检查**（`/lab/api/*`、`/api/*` 前缀改写、`encodeURIComponent` 覆盖情况），未逐行通读 3055 行；因此「前端把所有 id 都正确编码」是抽样结论。
