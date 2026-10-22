#!/usr/bin/env python3
"""纯 clone 路线的一键补齐运行件：把 git clone 之后缺的东西一样样装上。

仓库里 .gitignore 排除了 py312/、cpp/、runtime/ 与模型权重（体积与上游许可），
以前 README 只能写"请自己从完整分发包获取"——那对新下载这个项目的人来说等于没写。
本脚本把那条路补上：

    1) 建 py312\\ （Python 3.12 + 依赖）
    2) 下 audio.cpp 推理引擎到 cpp\\（有 N 卡下 CUDA 档，没有就下 CPU 档）
    3) 写 cpp\\server.json（照仓库根 server.json.example，按硬件改后端/线程/量化目录）
    4) 写 runtime\\data\\engine_state.json（CPU 机器必须写 cpu，否则 app.py 会按 cuda 默认去启引擎）
    5) 下 YuE2 GGUF 权重（多路分块，见 scripts/fetch_models.py 的实测理由）
    6) 打印一份"现在能干什么 / 还缺什么"的清单

每一步都幂等：已经装好就跳过，可以反复重跑。任何一步失败都如实退出非 0 并说清缺什么，
不会在没装上的情况下打绿勾。

用法（在本仓库根目录）：
    python scripts\\setup_runtime.py                      # 默认 q4_k_m + 自动判后端
    python scripts\\setup_runtime.py --q8 --workers 8
    python scripts\\setup_runtime.py --proxy http://127.0.0.1:7890   # GitHub 直连不动时
    python scripts\\setup_runtime.py --index-url https://pypi.tuna.tsinghua.edu.cn/simple
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE_VERSION = "0.9.2"          # 与 CHANGELOG 里内核升级记录一致
ENGINE_REPO = "0xShug0/audio.cpp"
CPU_ASSET = f"audio-v{ENGINE_VERSION}-bin-windows-x64-cpu-portable.zip"
CUDA_ASSETS = {"12.4": f"audio-v{ENGINE_VERSION}-bin-windows-x64-cuda12.4.zip",
               "13.3": f"audio-v{ENGINE_VERSION}-bin-windows-x64-cuda13.3.zip"}
# 没有 N 卡时选它：这个包里同时带 cpu 与 vulkan 两个后端，核显能用就走 vulkan，
# 不能用改一个字段就退回 cpu，不用重下。
VK_ASSET = f"audio-v{ENGINE_VERSION}-bin-windows-x64-vulkan-portable.zip"

# RVC（⑨ 换声 / ⑦ 音色制作）与逐字对齐（LRC/eLRC）的可选运行件。
# 两者都来自公开上游，本脚本负责"装依赖 + 拉权重 + 打仓库里的补丁"。
RVC_REPO = "https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI"
RVC_HF = "lj1995/VoiceConversionWebUI"
RVC_ASSETS = ["hubert_base/*", "rmvpe.pt",
              "pretrained_v2/f0G40k.pth", "pretrained_v2/f0D40k.pth"]
# matplotlib/tensorboard 是训练侧 import 的（train/train.py 一进来就 import），
# 只装推理依赖会让 ⑦ 在"开始训练"那一刻就 ModuleNotFoundError（评审 B3）。
RVC_PKGS = ["av", "faiss-cpu", "ffmpeg-python", "onnxruntime",
            "praat-parselmouth", "einops", "local-attention",
            "matplotlib", "tensorboard"]
# {runtime 内的相对路径: patches/ 下的相对路径}
# 注意 patches 目录**没有** infer/、train/ 这两层（评审 B8：以前多套一层，四个源全不存在
# 却被静默跳过）。缺源必须出声并失败，不能当作"已打补丁"。
RVC_PATCHES = {
    "infer/cli.py": "rvc-infer/cli.py",
    "infer/vc/pipeline.py": "rvc-infer/vc/pipeline.py",
    "infer/vc/modules.py": "rvc-infer/vc/modules.py",
    "train/preprocess.py": "rvc-clean-guards/preprocess.py",
}
PYMSS_MODELS = ["BS-Roformer-Resurrection",
                "UVR-DeReverb-aufr33-jarredou_4band_v4_ms_fullband",
                "UVR-DeNoise-Lite", "dereverb_bs_roformer_anvuew_sdr_22.5050",
                "UVR-DeNoise"]
LRC_PKGS = ["funasr", "modelscope", "faster-whisper"]
QUANT_DIRS = {"q4km": "yue2-q4_k_m", "q8": "yue2-q8"}
QUANT_FILES = {"q4km": "yue2-3b-q4_k_m.gguf", "q8": "yue2-3b-q8_0.gguf"}


def log(msg: str) -> None:
    print(f"[setup] {msg}", flush=True)


def step(title: str) -> None:
    print(f"\n--- {title} ---", flush=True)


def have_nvidia() -> bool:
    try:
        subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                       capture_output=True, timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except Exception:  # noqa: BLE001
        return False


def physical_threads() -> int:
    """引擎线程数取逻辑核数上限、且不超过 8：笔记本全速跑歌时还要留点余量给系统。"""
    n = os.cpu_count() or 4
    return max(4, min(8, n))


# --------------------------------------------------------------------------- #
# 1) py312
# --------------------------------------------------------------------------- #
def find_uv() -> str | None:
    return shutil.which("uv")


def uv_python_312(uv: str) -> Path | None:
    """`uv python find 3.12` 直接给出 python.exe 路径；返回它的安装根目录。

    两个坑：① uv 给的 `cpython-3.12-...` 常常是个 junction，指向
    `cpython-3.12.9-...`，不 resolve 就 copytree 容易只拷到链接本身；
    ② 名字里写着 3.12 不等于真跑的是 3.12，所以最后用 `--version` 实跑确认。
    """
    subprocess.run([uv, "python", "install", "3.12"], check=False)
    out = subprocess.run([uv, "python", "find", "3.12"], capture_output=True,
                         text=True).stdout.strip()
    if not out or not Path(out).is_file():
        return None
    exe = Path(out).resolve()
    ver = subprocess.run([str(exe), "--version"], capture_output=True, text=True).stdout.strip()
    if " 3.12" not in f" {ver}":
        log(f"  [!] uv 指过来的解释器实跑是 {ver}，不是 3.12——放弃这条路")
        return None
    return exe.parent


def local_312() -> Path | None:
    """没有 uv 时，找本机已装的 CPython 3.12（py 启动器或 PATH）。"""
    if os.name != "nt":
        return None
    try:
        out = subprocess.run(["py", "-0p"], capture_output=True, text=True, timeout=20).stdout
    except Exception:  # noqa: BLE001
        out = ""
    for line in out.splitlines():
        m = re.search(r"(3\.12[^\s]*)\s+.*?(\S+python\.exe)", line)
        if m and Path(m.group(2)).is_file():
            return Path(m.group(2)).parent
    return None


def ensure_py312() -> Path | None:
    step("1/5 Python 3.12 运行环境 py312\\")
    exe = ROOT / "py312" / "python.exe"
    if exe.is_file():
        log(f"  已存在：{exe} —— 跳过")
        return exe
    src: Path | None = None
    uv = find_uv()
    if uv:
        log("  用 uv 拉一份独立 CPython 3.12（本机已有就别重复下）")
        src = uv_python_312(uv)
    if src is None:
        src = local_312()
        if src:
            log(f"  用本机已装的 3.12：{src}")
    if src is None:
        log("  [x] 没找到 uv，也没找到本机 CPython 3.12。")
        log("    任选一种装上后重跑本脚本：")
        log("      a) pip install uv      （或 winget install astral-sh.uv）")
        log("      b) python.org 安装 Python 3.12（勾选 py launcher）")
        return None
    log(f"  复制到 {ROOT / 'py312'} （约 70MB）")
    shutil.copytree(src, ROOT / "py312", dirs_exist_ok=True)
    # uv 的独立构建带 PEP 668 标记，直接往里 pip 会被拦；这是我们自己的一份私有副本，去掉标记
    marker = ROOT / "py312" / "Lib" / "EXTERNALLY-MANAGED"
    if marker.is_file():
        marker.unlink()
    ver = subprocess.run([str(exe), "--version"], capture_output=True, text=True).stdout.strip()
    log(f"  [OK] {ver}")
    if not (ROOT / "py312" / "pythonw.exe").is_file():
        log("  [!] 缺 pythonw.exe，看门狗会起不来（启动脚本仍能跑网关本体）")
    return exe


def install_deps(index_url: str | None) -> bool:
    step("2/5 安装 Python 依赖（requirements.txt）")
    exe = str(ROOT / "py312" / "python.exe")
    req = ROOT / "requirements.txt"
    # pip 读清单用的是 auto_decode：没有 BOM 就按当前区域编码（GBK 机器上是 cp936）解。
    # 这份 requirements.txt 里有中文注释，实测不带 BOM 时 pip 直接 UnicodeDecodeError，
    # 整个第 2 步失败——所以这里先自证一次，别等用户去猜为什么装不上。
    head = req.read_bytes()[:3]
    if head != b"\xef\xbb\xbf":
        log("  [x] requirements.txt 缺 UTF-8 BOM，pip 在中文 Windows 上读不了这份带中文注释的清单。")
        log("      把它重存为「UTF-8 with BOM」后重跑本脚本。")
        return False
    cmd = [exe, "-m", "pip", "install", "--no-input", "--disable-pip-version-check",
           "-r", str(req)]
    if index_url:
        cmd += ["--index-url", index_url]
    t0 = time.time()
    if subprocess.run(cmd).returncode != 0:
        log("  [x] pip 失败。国内网络加镜像重试：")
        log("      python scripts\\setup_runtime.py --index-url https://pypi.tuna.tsinghua.edu.cn/simple")
        return False
    log(f"  [OK] 装完（{int(time.time() - t0)} 秒）")
    # 版本对不上会静默改变行为（本机就踩过 FastAPI 版本让 422 变 200 的坑），所以验一遍
    probe = subprocess.run(
        [exe, "-c", "import fastapi,starlette,httpx;print(fastapi.__version__,starlette.__version__,httpx.__version__)"],
        capture_output=True, text=True)
    log(f"  实测导入到的版本：{probe.stdout.strip() or probe.stderr.strip()[:120]}")
    return True


# --------------------------------------------------------------------------- #
# 2) audio.cpp 引擎
# --------------------------------------------------------------------------- #
def download_file(url: str, dest: Path, proxy: str | None, resume: bool = True) -> bool:
    """带断点续传的单流下载；proxy 传空则跟随系统环境变量。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    done = part.stat().st_size if (resume and part.is_file()) else 0
    headers = {"User-Agent": "yue2-setup/1.0"}
    if done:
        headers["Range"] = f"bytes={done}-"
    handlers: list[urllib.request.BaseHandler] = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=120) as r, \
                open(part, "ab" if done else "wb") as f:
            if done and r.status not in (206,):
                # 服务器忽略了 Range（会从头给），把旧数据丢掉重下
                f.close()
                part.unlink(missing_ok=True)
                return download_file(url, dest, proxy, resume=False)
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
                done += len(b)
                if done % (4 << 20) < (1 << 20):
                    log(f"    {done >> 20} MB …")
    except Exception as e:  # noqa: BLE001
        log(f"    下载中断：{e}（重跑本脚本可续传）")
        return False
    if part.stat().st_size == 0:
        log("    [x] 下回来是空的")
        return False
    part.rename(dest)
    return True


def probe_vulkan(exe: Path) -> bool:
    """问引擎自己：这个构建在 Vulkan 后端下能看到设备吗？

    本机实测：Iris Xe 被枚举为 `Vulkan:0 "Intel(R) Iris(R) Xe Graphics" [IGPU]`。
    只有探到设备才敢把 backend 设成 vulkan——否则用户下一步只会看到引擎起不来。
    """
    try:
        out = subprocess.run([str(exe), "--list-devices", "--backend", "vulkan"],
                             cwd=str(exe.parent), capture_output=True, text=True,
                             timeout=90,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        text = (out.stdout or "") + (out.stderr or "")
    except Exception:  # noqa: BLE001
        return False
    return "Vulkan:" in text


def ensure_engine(backend: str, cuda_ver: str, proxy: str | None) -> str | None:
    """下载并解压引擎；返回**实际可用**的后端（cuda/vulkan/cpu），失败返回 None。"""
    step("3/5 audio.cpp 推理引擎 cpp\\")
    cpp = ROOT / "cpp"
    exe = cpp / "audiocpp_server.exe"
    spec = cpp / "model_specs" / "yue2.json"
    if exe.is_file():
        # 早退分支也必须验参数表：只有 exe、没有 model_specs 时 main.py 会在 import 期
        # raise，而安装器此前照样打 [OK] 写歌出歌（评审 T4）。
        if not spec.is_file():
            log(f"  [x] 引擎在，但参数表 {spec.relative_to(ROOT)} 缺失：main.py 会拒绝启动。"
                f"删掉 {exe.name} 重跑本步骤可重新解压。")
            return None
        log(f"  已存在：{exe} —— 跳过")
        if backend == "vulkan" and not probe_vulkan(exe):
            log("  [!] 这个引擎构建看不到 Vulkan 设备 → 后端改用 cpu")
            return "cpu"
        return backend
    asset = (CUDA_ASSETS[cuda_ver] if backend == "cuda"
             else VK_ASSET if backend == "vulkan" else CPU_ASSET)
    url = f"https://github.com/{ENGINE_REPO}/releases/download/v{ENGINE_VERSION}/{asset}"
    log(f"  后端判为 {backend}，下载 {asset}")
    if backend == "cuda":
        log("  [!] CUDA 档约 460MB，且要求驱动 CUDA 版本与包匹配；起不来就改 --backend cpu 重跑")
    local = cpp / asset
    ok = False
    for attempt in range(3):
        if download_file(url, local, proxy):
            ok = True
            break
        log(f"  第 {attempt + 1} 次失败"
            + ("（GitHub 直连在国内经常不动，可加 --proxy http://127.0.0.1:7890）"
               if not proxy else ""))
        time.sleep(3)
    if not ok:
        return False
    with zipfile.ZipFile(local) as z:
        bad = z.testzip()
        if bad:
            log(f"  [x] zip 里 {bad} 校验失败，删掉 cpp\\{asset} 重跑")
            return False
        log(f"  解压 {len(z.namelist())} 个条目到 cpp\\")
        z.extractall(cpp)
    local.unlink(missing_ok=True)
    if not exe.is_file():
        log(f"  [x] 解压后仍找不到 {exe}——这个包的结构变了，请把上面日志发出来")
        return None
    spec = cpp / "model_specs" / "yue2.json"
    if not spec.is_file():
        log("  [x] 参数表 cpp\\model_specs\\yue2.json 不在（main.py 会拒绝启动）")
        return None
    if backend == "vulkan" and not probe_vulkan(exe):
        log("  [!] 引擎看不到 Vulkan 设备（驱动/Vulkan 运行库缺失？）→ 后端改用 cpu")
        backend = "cpu"
    log(f"  [OK] 引擎就位；后端 {backend}")
    return backend


# --------------------------------------------------------------------------- #
# 3) 配置
# --------------------------------------------------------------------------- #
def ensure_server_json(backend: str, quant: str) -> bool:
    step("4/5 cpp\\server.json 与 engine_state.json")
    cpp = ROOT / "cpp"
    dst = cpp / "server.json"
    tpl_path = ROOT / "server.json.example"
    if not tpl_path.is_file():
        log("  [x] 缺仓库根 server.json.example")
        return False
    cfg = json.loads(tpl_path.read_text(encoding="utf-8"))
    cfg["backend"] = backend
    cfg["threads"] = physical_threads()
    gguf = QUANT_FILES[quant]
    entry = cfg["models"][0]
    entry["path"] = f"model/{QUANT_DIRS[quant]}/{gguf}"
    entry.setdefault("session_options", {})["yue2.model_gguf"] = gguf
    if backend == "vulkan":
        # 核显走共享内存，峰值比独显更紧：阶段之间不释放 AR/NAR 权重时，VAE 的
        # 全曲输出缓冲会和它们叠在一起。mem_saver 用"重新加载"换峰值，Vulkan 默认开；
        # CUDA 保持默认（缓存换速度），用户可自己加。
        entry["session_options"]["mem_saver"] = True
    if dst.is_file():
        # 不覆盖，但 engine_state 必须照**磁盘上那份** server.json 写，不能照本次请求写：
        # 否则两边后端不一致，重启时各说各话（评审 B16）。
        try:
            cur = json.loads(dst.read_text(encoding="utf-8"))
            backend = str(cur.get("backend") or backend)
            if backend == "vulkan":
                # 老部署也补上 mem_saver：只加这一个开关，不动模型路径/线程/后端。
                so = (cur.get("models") or [{}])[0].setdefault("session_options", {})
                if not so.get("mem_saver"):
                    so["mem_saver"] = True
                    dst.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
                    log("  [OK] 已为 Vulkan 补上 session_options.mem_saver（阶段间释放权重）")
        except Exception:  # noqa: BLE001
            pass
        log(f"  已存在 {dst} —— 不覆盖（engine_state 按它的 backend={backend} 对齐）")
    else:
        dst.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"  [OK] 写入（后端 {backend}，{cfg['threads']} 线程，busy_timeout_ms=0）")
        log("    busy_timeout_ms 必须是 0：引擎默认 5 分钟的 busy 守卫会把一首长跑几小时的歌掐断")
    # engine_state：app.py 的 backend_mode() 缺省按 cuda，CPU 机器不写就会去做显存预检/回切
    st = ROOT / "runtime" / "data" / "engine_state.json"
    st.parent.mkdir(parents=True, exist_ok=True)
    data = {"backend": backend,
            "loaded_main": gguf,
            "loaded_vae": "yue2-vae-f16.gguf",
            "loaded_vae_from": "同目录",
            "loaded_path": f"{QUANT_DIRS[quant]}/{gguf}"}
    st.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"  [OK] runtime\\data\\engine_state.json 写成 backend={backend}")
    return True


# --------------------------------------------------------------------------- #
# 4) 权重 + 收尾清单
# --------------------------------------------------------------------------- #
def ensure_models(quant: str, workers: int, skip: bool) -> bool:
    step("5/5 YuE2 GGUF 权重")
    if skip:
        log("  按 --skip-models 跳过")
        return True
    sys.path.insert(0, str(ROOT / "scripts"))
    import fetch_models  # 复用同一个清单与校验口径，别把 URL 抄第二遍
    return fetch_models.main(["--workers", str(workers)] + (["--q8"] if quant == "q8" else [])) == 0


def ensure_mert_cache(proxy: str | None = None) -> bool:
    """按 checkpoints/config.json 里钉住的 revision 下 MERT-v2-FullSong。

    为什么必须钉 revision 而不是下 main：那个配置文件写死了
    `base_model_revision`，远端代码会用 `cached_file(..., revision=...)` 精确去找。
    本机实测：下 main（7001f765）时文件内容 SHA 都对，但 revision 路径不匹配，
    转谱照样报"本地缓存不完整"。所以这里从配置里读，不手抄。
    """
    cfg_path = ROOT / "checkpoints" / "config.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log(f"  [x] 读不了 {cfg_path}：{e}")
        return False
    repo = str(cfg.get("base_model_name_or_path") or "").strip()
    rev = str(cfg.get("base_model_revision") or "").strip() or None
    if not repo:
        log("  [x] checkpoints/config.json 里没有 base_model_name_or_path")
        return False
    dest = ROOT / "runtime" / "hf_download"
    hub = dest / "hub"
    hub.mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "HF_HOME": str(dest),
        "HF_HUB_CACHE": str(hub),
        "HUGGINGFACE_HUB_CACHE": str(hub),
        # 与 src/sheetsage_pt.py::configure_hf_home 同口径：不一致的话 transformers
        # 会去另一个目录找文件，转谱必失败（本机实测）。
        "TRANSFORMERS_CACHE": str(hub),
        "HF_ENDPOINT": os.environ.get("HF_ENDPOINT") or "https://hf-mirror.com",
        "HF_HUB_DISABLE_TELEMETRY": "1",
    })
    if proxy:
        os.environ["HTTPS_PROXY"] = proxy
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        log("  [x] 缺 huggingface_hub —— 先装 requirements-transcribe.txt")
        return False
    log(f"  下 {repo}@{rev[:8] if rev else 'main'}（约 2.5GB，hf-mirror 多路）…")
    t0 = time.time()
    try:
        path = snapshot_download(repo, revision=rev, max_workers=8)
    except Exception as e:  # noqa: BLE001
        log(f"  [x] 下载失败：{type(e).__name__}: {str(e)[:200]}")
        return False
    log(f"  [OK] {round(time.time() - t0)} 秒 -> {path}")
    return True


def ensure_transcribe(quant: str, workers: int, index_url: str | None,
                      proxy: str | None = None) -> bool:
    """可选：③④ 转谱那套（torch + SheetSage2 权重）。

    单独一份 requirements-transcribe.txt，不直接吃 checkpoints/requirements.txt：
    后者钉 numpy==1.24.3/scipy==1.13.1，会把网关 + librosa 1.0 的主环境拆掉。
    """
    step("可选：③④ 转谱运行件（torch + SheetSage2 权重）")
    req = ROOT / "requirements-transcribe.txt"
    if not req.is_file():
        log("  [x] 缺 requirements-transcribe.txt，跳过")
        return False
    if req.read_bytes()[:3] != b"\xef\xbb\xbf":
        log("  [x] requirements-transcribe.txt 缺 UTF-8 BOM（原因同主清单，pip 在中文 Windows 上读不了）")
        return False
    exe = str(ROOT / "py312" / "python.exe")
    cmd = [exe, "-m", "pip", "install", "--no-input", "--disable-pip-version-check", "-r", str(req)]
    if index_url:
        cmd += ["--index-url", index_url]
    if subprocess.run(cmd).returncode != 0:
        log("  [x] 转谱依赖装失败（国内网络加 --index-url 重试）")
        return False
    sys.path.insert(0, str(ROOT / "scripts"))
    import fetch_models
    args = ["--workers", str(workers), "--with-sheetsage"] + (["--q8"] if quant == "q8" else [])
    ok = fetch_models.main(args) == 0
    if ok:
        ok = ensure_mert_cache(proxy)
    if ok:
        log("  [OK] 转谱就绪（torch 是 CPU 版：一段 67 秒的歌做乐谱提取要几分钟到十几分钟）")
    return ok


def _pip_install(pkgs: list[str], index_url: str | None, no_deps: bool = False) -> bool:
    exe = str(ROOT / "py312" / "python.exe")
    cmd = [exe, "-m", "pip", "install", "--no-input", "--disable-pip-version-check", *pkgs]
    if no_deps:
        cmd.insert(6, "--no-deps")
    if index_url:
        cmd += ["--index-url", index_url]
    return subprocess.run(cmd).returncode == 0


def ensure_lrc(index_url: str | None) -> bool:
    """逐字 LRC/eLRC：funasr(fa-zh) + wav2vec2(英文) + faster-whisper(兜底)。

    fa-zh 与 VAD 由 FunASR/ModelScope 自动下载；英文 CTC 的 wav2vec2 权重在第一次
    英文对齐时从 download.pytorch.org 拉（约 360MB，实测 2MB/s）。
    """
    step("可选：逐字 LRC/eLRC（funasr + ModelScope 模型）")
    if not _pip_install(LRC_PKGS, index_url):
        log("  [x] funasr/modelscope 安装失败（国内网络加 --index-url 重试）")
        return False
    try:
        from funasr import AutoModel
    except Exception as e:  # noqa: BLE001
        log(f"  [x] funasr 导入失败：{e}")
        return False
    t0 = time.time()
    AutoModel(model="fa-zh", disable_update=True)   # 首次约 151MB
    log(f"  [OK] fa-zh 就绪（{round(time.time() - t0)} 秒；英文首次对齐另下 wav2vec2 约 360MB）")
    return True


def ensure_rvc(index_url: str | None, proxy: str | None, skip_models: bool) -> bool:
    """⑨ 换声 / ⑦ 音色制作 / ⑩ 人声分离 + 素材净化。

    上游官方 main 自带 tools/pymss（分离/净化）且已支持 py312；这里只做四件事：
    clone、装推理依赖、拉 hubert/rmvpe/pretrained 权重、把仓库 patches/ 里的补丁
    覆盖上去（不覆盖也能跑，只是少 fcpe 与音高平滑）。
    """
    step("可选：RVC 换声/训练/分离运行件")
    rvc = ROOT / "runtime" / "rvc"
    if not (rvc / "infer" / "cli.py").is_file():
        log("  clone 官方 RVC（约 15MB，浅克隆）…")
        cmd = ["git", "clone", "--depth", "1", RVC_REPO, str(rvc)]
        if proxy:
            cmd = ["git", "-c", f"http.proxy={proxy}", "clone", "--depth", "1", RVC_REPO, str(rvc)]
        if subprocess.run(cmd).returncode != 0:
            log("  [x] clone 失败（GitHub 直连不稳时可加 --proxy）")
            return False
    else:
        log(f"  已存在：{rvc.relative_to(ROOT)} —— 跳过 clone")
    if not _pip_install(RVC_PKGS, index_url):
        log("  [x] RVC 推理依赖安装失败")
        return False
    if not _pip_install(["torchfcpe"], index_url, no_deps=True):
        log("  [!] torchfcpe 装失败：fcpe 音高算法不可用，rmvpe 不受影响")
    if not (rvc / "assets" / "hubert_base" / "pytorch_model.bin").is_file():
        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            log("  [x] 缺 huggingface_hub（先装 requirements-transcribe.txt）")
            return False
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
        dl = rvc / "_hf_dl"
        log(f"  下 hubert/rmvpe/pretrained（约 560MB）→ {dl.relative_to(ROOT)}")
        snapshot_download(RVC_HF, repo_type="model", allow_patterns=RVC_ASSETS,
                          local_dir=str(dl), max_workers=8)
        import shutil as _sh
        for sub in ("rmvpe", "pretrained_v2", "weights", "indices", "pymss_weights"):
            (rvc / "assets" / sub).mkdir(parents=True, exist_ok=True)
        (rvc / "logs" / "mute").mkdir(parents=True, exist_ok=True)
        if (dl / "hubert_base").is_dir():
            _sh.copytree(dl / "hubert_base", rvc / "assets" / "hubert_base",
                         dirs_exist_ok=True)
        if (dl / "rmvpe.pt").is_file():
            _sh.copy2(dl / "rmvpe.pt", rvc / "assets" / "rmvpe" / "rmvpe.pt")
        if (dl / "pretrained_v2").is_dir():
            for f in sorted((dl / "pretrained_v2").glob("*.pth")):
                _sh.copy2(f, rvc / "assets" / "pretrained_v2" / f.name)
    else:
        log("  hubert/rmvpe/pretrained 已就位 —— 跳过")
    patch_ok = True
    for rel, src_rel in RVC_PATCHES.items():
        src = ROOT / "patches" / src_rel
        dst = rvc / rel
        if not src.is_file():
            log(f"  [x] 补丁源不存在：patches/{src_rel} —— 跳过 {rel}（别当成已打）")
            patch_ok = False
            continue
        if not dst.is_file():
            log(f"  [x] 目标文件不存在：{rel}（上游结构变了？）")
            patch_ok = False
            continue
        if src.read_bytes() == dst.read_bytes():
            continue
        backup = dst.with_suffix(dst.suffix + ".orig")
        if backup.is_file() and backup.read_bytes() != dst.read_bytes():
            # 已经有一份**不同**的原件：说明上游变过。保留旧备份另存，别让 .orig 只剩第一版
            # （评审 B21）。
            alt = dst.with_suffix(dst.suffix + ".orig.prev")
            alt.write_bytes(backup.read_bytes())
            log(f"  [!] 上游文件与旧备份不同，旧备份另存为 {alt.name}")
        backup.write_bytes(dst.read_bytes())
        dst.write_bytes(src.read_bytes())
        log(f"  补丁已应用：{rel}（原文件留 .orig）")
    if not patch_ok:
        log("  [x] 有补丁没打上：换声能跑但会缺 fcpe / 音高平滑")
    # 装完自检：--help 必须同时看到 --filter-radius 与 fcpe（不是"文件拷过去了"就算）
    try:
        h = subprocess.run([str(ROOT / "py312" / "python.exe"),
                            str(rvc / "infer" / "cli.py"), "--help"],
                           capture_output=True, text=True, timeout=120,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        if "--filter-radius" not in h or "fcpe" not in h:
            log("  [x] 补丁自检失败：cli.py --help 里没有 --filter-radius/fcpe")
            patch_ok = False
    except Exception as e:  # noqa: BLE001
        log(f"  [x] 补丁自检跑不起来：{type(e).__name__}: {e}")
        patch_ok = False
    sc_src = ROOT / "patches" / "rvc-clean-guards" / "sitecustomize.py"
    sc_dst = ROOT / "py312" / "Lib" / "site-packages" / "sitecustomize.py"
    if sc_src.is_file():
        if not sc_dst.is_file():
            sc_dst.write_bytes(sc_src.read_bytes())
            log("  sitecustomize 已安装（cuDNN 守卫 + 水印过滤）")
        # 无论是不是刚写的，都补上 torch.load 兼容开关：已有 sitecustomize 的机器
        # 以前永远拿不到这一行（评审 B15）。
        body = sc_dst.read_text(encoding="utf-8", errors="ignore")
        if "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD" not in body:
            with sc_dst.open("a", encoding="utf-8") as f:
                f.write('\nimport os as _os\n'
                        '_os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")\n')
            log("  sitecustomize 已补 TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD（torch 2.6+ 兼容）")
    if not skip_models:
        log("  预下分离/净化模型（约 590MB，PyMSS 自管缓存 ~/.cache/pymss/models）")
        env = {**os.environ, "PYTHONPATH": str(rvc / "tools"),
               "HF_ENDPOINT": os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")}
        exe = str(ROOT / "py312" / "python.exe")
        bad = []
        for m in PYMSS_MODELS:
            try:
                r = subprocess.run([exe, "-m", "pymss.cli", "download", m], cwd=str(rvc),
                                   env=env, capture_output=True, text=True, timeout=1800)
                ok = r.returncode == 0
            except Exception as e:  # noqa: BLE001
                ok, r = False, e
            log(f"    {'[OK]' if ok else '[x]'} {m}")
            if not ok:
                bad.append(m)
        if len(bad) == len(PYMSS_MODELS):
            log("  [x] 分离/净化模型一个都没下成：⑩ 会在提交时报缺权重（不是假成功）")
            return False
    # 装完自检：训练/推理真正依赖的那几个包必须能 import（B3 的验收点）
    probe = subprocess.run(
        [str(ROOT / "py312" / "python.exe"), "-c",
         "import torch, matplotlib, tensorboard, einops, torchfcpe, faiss, parselmouth;"
         "print('ocr-deps ok')"],
        capture_output=True, text=True)
    if probe.returncode != 0:
        log(f"  [x] RVC 依赖自检失败：{probe.stderr.strip().splitlines()[-1] if probe.stderr else '?'}")
        return False
    if not patch_ok:
        return False
    log("  [OK] RVC 运行件就绪（CPU 上换声/训练都慢，训练以小时计）")
    return True


def report(backend: str, quant: str, transcribe_ready: bool = False,
           rvc_ready: bool = False, lrc_ready: bool = False,
           core_ready: bool = True) -> None:
    step("现在能干什么 / 还缺什么")
    lines = [
        (("[OK]" if core_ready else "[x]"),
         "写歌出歌：双击 启动音乐工作台.bat -> http://127.0.0.1:7863" +
         ("" if core_ready else "（运行件未齐：引擎/参数表/权重有缺项，先看 3/5 与 5/5 的日志）")),
        ("[OK]", "开源兼容网关 main.py 顶上闭源 main.cp312-win_amd64.pyd —— 纯 clone 也能起工作台"),
        ("[OK]", "⑤ 让引擎先规划一份谱（stop_after=abc）：只依赖引擎"),
        (("[OK]" if transcribe_ready else "[x]"),
         "③④ 从音频提取乐谱" + ("：已就绪" if transcribe_ready else
                                 "：加 --with-transcribe 装 torch 与 SheetSage2 权重")),
        (("[OK]" if rvc_ready else "[x]"), "⑨ 换声 / ⑦ 音色制作 / ⑩ 人声分离" +
         ("：已就绪（上游 RVC 自带 PyMSS；换声还需一个你自己的 .pth 音色）" if rvc_ready
          else "：加 --with-rvc 安装（官方 RVC + PyMSS + hubert/rmvpe/预训练权重）")),
        (("[OK]" if lrc_ready else "[x]"), "逐字 LRC/eLRC" +
         ("：已就绪（中文 fa-zh；英文首次对齐会下 wav2vec2 约 360MB）" if lrc_ready
          else "：加 --with-lrc 安装（funasr + fa-zh）")),
        ("?", "⑥ AI 写词：把 secrets/local_env.example.bat 复制成 local_env.bat 填 DEEPSEEK_API_KEY 后重启"),
    ]
    if backend == "vulkan":
        lines.insert(2, ("!", "没有 N 卡，但核显的 Vulkan 可用：本机实测同一条请求 CPU 5,610 秒 → "
                              "Vulkan 318 秒（约 14 倍；小片段上 15~27 倍），输出长度与电平与 CPU 一致。"
                              "核显走统一内存，跑歌时别同时开大程序"))
    elif backend == "cpu":
        lines.insert(2, ("!", "本机既没有 N 卡也没探到可用的 Vulkan 设备：走 CPU。实测锚点：一段 66.7 秒的歌 "
                              "cot=off + 1 步跑了 93.5 分钟（RTF≈84）；NAR 随步数线性放大，"
                              "所以一首 3~4 分钟的歌 full+8 步是十几小时量级、full+32 步以天计。"
                              "页面顶部那句 ETA 是按显卡机器标定的，别照着安排时间"))
    lines.insert(3, ("!", "笔记本请插电；出歌期间网关已声明「别进睡眠」，但合盖仍可能待机——"
                          "要彻底防，跑 powercfg /setacvalueindex SCHEME_CURRENT 4f971e89-eebd-4455-a8de-9e59040e7347 "
                          "5ca83367-6e45-459f-a27b-476b1d01c936 0 把合盖动作改成不睡眠"))
    for mark, t in lines:
        log(f"  {mark} {t}")
    log(f"  量化档：{QUANT_DIRS[quant]}；引擎后端：{backend}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--q8", action="store_true", help="下 q8_0（约4GB）而不是 q4_k_m")
    ap.add_argument("--backend", choices=["auto", "cpu", "cuda", "vulkan"], default="auto")
    ap.add_argument("--cuda", choices=["12.4", "13.3"], default="12.4",
                    help="CUDA 大版本，决定下哪个引擎包")
    ap.add_argument("--workers", type=int, default=6, help="权重并发连接数")
    ap.add_argument("--proxy", default=None, help="GitHub 直连不动时的代理，如 http://127.0.0.1:7890")
    ap.add_argument("--index-url", default=None, help="pip 镜像")
    ap.add_argument("--skip-models", action="store_true")
    ap.add_argument("--with-transcribe", action="store_true",
                    help="顺带装 ③④ 转谱（torch + SheetSage2 权重，约 1GB 下载）")
    ap.add_argument("--with-rvc", action="store_true",
                    help="顺带装 ⑨换声/⑦音色制作/⑩分离（官方 RVC + PyMSS，约 1.2GB 下载）")
    ap.add_argument("--with-lrc", action="store_true",
                    help="顺带装逐字 LRC/eLRC（funasr + fa-zh，约 200MB）")
    ap.add_argument("--skip-pymss-models", action="store_true",
                    help="装 --with-rvc 时跳过 590MB 的分离/净化模型预下载")
    a = ap.parse_args()

    # auto：有 N 卡走 CUDA；没有就下 Vulkan 包（内含 cpu 后端）再按实际设备决定
    backend = a.backend if a.backend != "auto" else ("cuda" if have_nvidia() else "vulkan")
    quant = "q8" if a.q8 else "q4km"
    log(f"硬件判定：{'检测到 nvidia-smi' if have_nvidia() else '没有 NVIDIA 卡'} -> 引擎后端 {backend}；"
        f"权重档 {QUANT_DIRS[quant]}")

    if os.name != "nt":
        log("本脚本按 Windows 分发包写的（.bat/引擎 zip 名都是 Windows 的）。Linux/macOS 请手工走 README。")
        return 1

    if ensure_py312() is None:
        return 1
    if not install_deps(a.index_url):
        return 1
    effective = ensure_engine(backend, a.cuda, a.proxy)
    engine_ok = bool(effective)
    if effective:
        backend = effective
    if not ensure_server_json(backend, quant):
        return 1
    failures: list[str] = [] if engine_ok else ["引擎/参数表"]
    models_ok = ensure_models(quant, a.workers, a.skip_models)
    if not models_ok:
        failures.append("GGUF 权重")
    transcribe_ready = False
    if a.with_transcribe:
        transcribe_ready = ensure_transcribe(quant, a.workers, a.index_url, a.proxy)
        if not transcribe_ready:
            failures.append("转谱")
    rvc_ready = ensure_rvc(a.index_url, a.proxy, a.skip_pymss_models) if a.with_rvc else False
    if a.with_rvc and not rvc_ready:
        failures.append("换声/分离")
    lrc_ready = ensure_lrc(a.index_url) if a.with_lrc else False
    if a.with_lrc and not lrc_ready:
        failures.append("逐字对齐")
    core_ready = engine_ok and models_ok
    report(backend, quant, transcribe_ready, rvc_ready, lrc_ready, core_ready)
    if failures:
        log("\n[x] 有步骤失败：" + "、".join(failures) + "。修好后重跑本脚本（幂等）。")
        return 1
    log("\n下一步：双击 启动音乐工作台.bat（已在跑就先用 停止音乐工作台.bat 停一次，让它读新配置）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
