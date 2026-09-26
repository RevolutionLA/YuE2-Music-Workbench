"""音乐工作台 - YuE2 音乐生成 Web 服务入口。

在编译好的网关（main.cp312-win_amd64.pyd）之上扩展新路由：
  * 模型切换（Q8_0 / Q4_K_M / 其它 GGUF 量化）
  * 本地音色库（参考音频 + 参考文本）
  * 参考音频 -> 歌词 的翻唱工作流（ASR）
  * 模板 / 预设管理
原有的音乐生成、health、presets 等接口全部保留。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import base64
import re
import secrets
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

# 业务模块已归档到 src/，注入路径让下方 import 无需改动
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

# 本地回环直连，绕过系统代理（如 Clash/V2Ray 的 127.0.0.1:7890）。
# 必须在 import main 之前设置：编译网关内部的 httpx 客户端默认 trust_env=True，
# 走了系统代理会导致访问本地 audio.cpp 后端全部 502。
for _pk in ("NO_PROXY", "no_proxy"):
    _pv = os.environ.get(_pk, "")
    if "127.0.0.1" not in _pv:
        os.environ[_pk] = (_pv + "," if _pv else "") + "127.0.0.1,localhost,::1"

import uvicorn
from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
import httpx

import main  # 编译网关
from settings import settings
import voices
import asr
import denoise
import lrc as lrc_mod

# 强制对齐（已知歌词 → 精确时间轴）：可选依赖，缺失时自动退回 lrc_mod 旧链路
try:
    import lrc_align as lrc_align_mod
except Exception:  # funasr 未安装 / 模型缺失
    lrc_align_mod = None

router = APIRouter(prefix="/api")
app = main.app  # 复用编译网关的 FastAPI 应用

# 网关 7863 页面一律不自动弹出：用户入口统一是 3081 工作台（启动脚本负责打开）。
# 不论双击 bat 还是看门狗自动重启网关，都不会再弹 7863 迷惑用户；7863 服务本身保留。
main.settings.open_browser = False

ROOT = Path(__file__).resolve().parent


def _hide_engine_windows_loop() -> None:
    """常驻线程：隐藏 audiocpp 引擎的控制台窗口。

    编译网关（main.pyd）内部的 start_audiocpp 未带 CREATE_NO_WINDOW，
    每次拉起引擎都会弹一个刷 ggml 日志的 CMD 窗口；pyd 无法修改，
    这里周期性把标题含 audiocpp_server.exe 的可见窗口 SW_HIDE 兜底。
    """
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    SW_HIDE = 0
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    buf = ctypes.create_unicode_buffer(512)

    def _on_window(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            user32.GetWindowTextW(hwnd, buf, 512)
            if "audiocpp_server" in buf.value.lower():
                user32.ShowWindow(hwnd, SW_HIDE)
        return True

    while True:
        try:
            user32.EnumWindows(enum_proc(_on_window), 0)
        except Exception:
            pass
        time.sleep(0.5)  # 高频扫，窗口弹出后几乎立刻隐藏，肉眼无感


threading.Thread(target=_hide_engine_windows_loop, daemon=True).start()

MODEL_DIR = ROOT / "cpp" / "model"
SERVER_JSON = ROOT / "cpp" / "server.json"
AUDIOCPP_BIN = ROOT / settings.audiocpp_bin

# 允许前端跨域访问（本地页面与接口同源，默认即可；此处为安全兜底）。
# 本机使用：仅放行本机来源，杜绝任意网页经 CORS 打内网接口。
_P_GW = settings.app_port
_P_DSH = settings.dsh_port
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://127.0.0.1:{_P_GW}", f"http://localhost:{_P_GW}",
        f"http://127.0.0.1:{_P_DSH}", f"http://localhost:{_P_DSH}",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# 模型切换
# --------------------------------------------------------------------------- #
_QUANT_MAP = {
    "q8_0": "Q8_0（高精度 · 推荐质量）",
    "q8_0_f16": "Q8_0+F16（最大精度）",
    "bf16": "BF16（原始精度）",
    "q4_k_m": "Q4_K_M（省显存 · 6GB卡首选）",
    "q4_k_s": "Q4_K_S",
    "q4_k": "Q4_K",
    "q4_0": "Q4_0（轻量 · 6GB卡可跑）",
    "q4_1": "Q4_1",
    "q5_k_m": "Q5_K_M",
    "q5_k_s": "Q5_K_S",
    "q5_0": "Q5_0",
    "q5_1": "Q5_1",
    "q6_k": "Q6_K",
    "q6_k_m": "Q6_K_M",
    "q3_k_m": "Q3_K_M（极小显存）",
    "q3_k": "Q3_K",
    "q2_k": "Q2_K（最小体积）",
}


def _read_server_json() -> dict:
    return json.loads(SERVER_JSON.read_text(encoding="utf-8"))


def _write_server_json(cfg: dict) -> None:
    SERVER_JSON.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _quant_label(name: str) -> str:
    base = name.lower().replace(".gguf", "").replace("yue2-", "").replace("yue-", "")
    base = re.sub(r"^yue2?[-_]?3b?[-_]?", "", base)
    for key, label in _QUANT_MAP.items():
        if key in base:
            return label
    return name


def available_models() -> list[dict]:
    """扫描 cpp/model/ 下所有可用的 yue2 主模型（gguf，不含 vae）。

    后端 audio.cpp 的路径规则（实测）：
      * models[].path            相对后端工作目录 cpp\\ ，因此要带 "model/" 前缀
      * session_options 里的 gguf  相对 path 所在目录解析，必须是纯文件名
    """
    items: list[dict] = []
    for gguf in sorted(MODEL_DIR.rglob("*.gguf")):
        if "vae" in gguf.name.lower():
            continue
        parent = gguf.parent
        vae = next(
            (p.name for p in parent.glob("*vae*.gguf")),
            next((p.name for p in gguf.parent.glob("**/*vae*.gguf")), None),
        )
        rel = gguf.relative_to(MODEL_DIR).as_posix()
        items.append(
            {
                "file": gguf.name,
                "path": rel,
                "server_path": "model/" + rel,          # 写入 server.json 的 path
                "model_gguf": gguf.name,                # session_options：纯文件名
                "size_gb": round(gguf.stat().st_size / 1024**3, 2),
                "vae": vae,
                "quant": _quant_label(gguf.name),
            }
        )
    return items


# 引擎模式持久化：cuda（默认）/ cpu 兜底。CPU 慢 5-10 倍但显存不足时必然能出结果。
# 注意：不用引擎的 --min-free-memory-mb 守卫——它同时检查主机内存且预估极度悲观
# （把 mmap 权重+最大 KV cache 全算满，12GB+），在 16GB 内存的机器上会拦掉所有加载。
# 显存预检由 _gpu_free_mb()（nvidia-smi）完成，只看显存。
_ENGINE_STATE = ROOT / "runtime" / "data" / "engine_state.json"
_VRAM_HEADROOM_MB = 700   # 生成时显存需预留的余量（Q4 实测峰值约 4.6GB + 系统占用）


def _engine_state_read() -> dict:
    try:
        return json.loads(_ENGINE_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _engine_state_write(updates: dict) -> dict:
    state = _engine_state_read()
    state.update(updates)
    _ENGINE_STATE.parent.mkdir(parents=True, exist_ok=True)
    _ENGINE_STATE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return state


def backend_mode() -> str:
    mode = _engine_state_read().get("backend", "cuda")
    return mode if mode in ("cuda", "cpu") else "cuda"


def _gpu_free_mb() -> int | None:
    """查询 GPU 当前空闲显存（MiB）；查询失败返回 None（不拦截）。"""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # 侧栏轮询高频调用，必须隐藏窗口
        )
        return int(out.strip().splitlines()[0])
    except Exception:
        return None


def _hide_engine_windows_once() -> None:
    """立即隐藏所有 audiocpp 引擎控制台窗口（配合拉起引擎后调用）。"""
    if os.name != "nt":
        return

    def _hide():
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            buf = ctypes.create_unicode_buffer(512)

            def _on_window(hwnd, _lparam):
                user32.GetWindowTextW(hwnd, buf, 512)
                if "audiocpp_server" in buf.value.lower():
                    user32.ShowWindow(hwnd, 0)  # SW_HIDE
                return True

            # 引擎窗口创建需要一点时间，连续扫几轮确保覆盖
            for _ in range(10):
                user32.EnumWindows(enum_proc(_on_window), 0)
                time.sleep(0.3)
        except Exception:
            pass

    threading.Thread(target=_hide, daemon=True).start()


def _restart_audiocpp(server_json_path: str, backend: str | None = None) -> None:
    """杀掉当前 audio.cpp 服务进程，并按新配置重启。

    cwd 必须是 cpp\\ ：后端以工作目录为基准解析模型相对路径。
    backend 参数可临时覆盖本次启动的后端（cpu/cuda），不改变持久化模式。
    """
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/IM", "audiocpp_server.exe", "/F"],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    else:
        subprocess.run(["pkill", "-f", "audiocpp_server"], capture_output=True)
    time.sleep(1.0)
    exe = str(AUDIOCPP_BIN)
    args = [exe, "--config", server_json_path]
    mode = (backend or backend_mode()).lower()
    if mode != "cuda":
        args += ["--backend", mode]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    workdir = str(AUDIOCPP_BIN.parent)
    if creationflags:
        subprocess.Popen(args, cwd=workdir, creationflags=creationflags)
    else:
        subprocess.Popen(args, cwd=workdir)
    _hide_engine_windows_once()  # 引擎若被外部机制带出控制台窗口，立即压下


@router.get("/models/list")
def models_list():
    return {"models": available_models()}


@router.get("/models/current")
def models_current():
    """返回当前 server.json 生效的模型（相对 cpp/model 的路径，供前端下拉回显）。"""
    try:
        cfg = _read_server_json()
        p = (cfg.get("models") or [{}])[0].get("path", "")
    except Exception:
        p = ""
    p = (p or "").replace("\\", "/")
    if p.startswith("model/"):
        p = p[len("model/"):]
    return {"path": p}


# --------------------------------------------------------------------------- #
# 模型完整性校验 + 局部修复（借鉴 YuE2-Studio：目录存在 ≠ 模型可用）
# 逐文件校验期望文件（主模型/VAE/sidecar/SheetSage2 权重）的最小字节数与
# GGUF magic；缺哪个修哪个，避免整包重下。
# --------------------------------------------------------------------------- #
_GGUF_MAGIC = b"GGUF"
# (仓库, 仓库内路径, 落地相对路径(相对 ROOT), 最小字节数, 是否 GGUF)
_MODEL_MANIFEST_MAIN = [
    ("ngquocvinh/YuE2-3B-GGUF", "yue2-3b-q4_k_m.gguf", "cpp/model/yue2-q4_k_m/yue2-3b-q4_k_m.gguf", 2_000_000_000, True),
    ("audio-cpp/Yue2-3B-GGUF", "yue2-vae-f16.gguf", "cpp/model/yue2-q4_k_m/yue2-vae-f16.gguf", 100_000_000, True),
    ("ngquocvinh/YuE2-3B-GGUF", "sidecars/yue2-model-config.json", "cpp/model/yue2-q4_k_m/sidecars/yue2-model-config.json", 10, False),
    ("ngquocvinh/YuE2-3B-GGUF", "sidecars/yue2-generation-config.json", "cpp/model/yue2-q4_k_m/sidecars/yue2-generation-config.json", 10, False),
    ("ngquocvinh/YuE2-3B-GGUF", "sidecars/yue2-qwen.tiktoken", "cpp/model/yue2-q4_k_m/sidecars/yue2-qwen.tiktoken", 100_000, False),
    ("ngquocvinh/YuE2-3B-GGUF", "sidecars/yue2-vae-config.json", "cpp/model/yue2-q4_k_m/sidecars/yue2-vae-config.json", 10, False),
    ("m-a-p/SheetSage2", "model.safetensors", "checkpoints/model.safetensors", 100_000_000, False),
]
_REPAIR_LOCK = threading.Lock()
_REPAIR_STATE: dict = {"running": False, "ts": None, "repaired": [], "failed": [], "message": ""}


def _model_verify_one(rel: str, min_size: int, is_gguf: bool) -> tuple[bool, str]:
    """校验单个文件：存在 + 字节数达标 +（GGUF 时）头部 magic 正确。"""
    p = ROOT / rel
    if not p.is_file():
        return False, "缺失"
    size = p.stat().st_size
    if size < min_size:
        return False, f"不完整（{size} 字节 < 期望 ≥{min_size}）"
    if is_gguf:
        try:
            with open(p, "rb") as f:
                if f.read(4) != _GGUF_MAGIC:
                    return False, "GGUF 头无效"
        except OSError as e:
            return False, f"读取失败：{e}"
    return True, "ok"


@router.get("/models/verify")
def models_verify():
    """逐文件校验模型完整性，返回各文件状态与整体结论。"""
    files = []
    ok_all = True
    for repo, rel_remote, rel, min_size, is_gguf in _MODEL_MANIFEST_MAIN:
        ok, reason = _model_verify_one(rel, min_size, is_gguf)
        ok_all = ok_all and ok
        files.append({"repo": repo, "remote": rel_remote, "path": rel,
                      "name": Path(rel).name, "ok": ok, "reason": reason})
    return {"ok": ok_all, "files": files,
            "repair_running": _REPAIR_STATE.get("running", False)}


@router.post("/models/repair")
def models_repair():
    """局部修复：只重下校验失败的文件（断点续传，多镜像回退）。"""
    with _REPAIR_LOCK:
        if _REPAIR_STATE.get("running"):
            raise HTTPException(status_code=409, detail="修复已在进行中")
        _REPAIR_STATE.update({"running": True, "ts": time.time(),
                              "repaired": [], "failed": [], "message": "校验中…"})
    threading.Thread(target=_model_repair_run, daemon=True).start()
    return {"ok": True, "message": "修复已开始（只补缺失/损坏的文件），可稍后刷新查看。"}


def _model_repair_run():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from download_models import download_one, mirrors  # 复用下载脚本的镜像/续传逻辑
    except Exception as e:
        with _REPAIR_LOCK:
            _REPAIR_STATE.update({"running": False, "message": f"加载下载模块失败：{e}"})
        return
    repaired, failed = [], []
    try:
        for repo, rel_remote, rel, min_size, is_gguf in _MODEL_MANIFEST_MAIN:
            ok, reason = _model_verify_one(rel, min_size, is_gguf)
            if ok:
                continue
            dest = ROOT / rel
            with _REPAIR_LOCK:
                _REPAIR_STATE["message"] = f"修复 {Path(rel).name} …"
            try:
                # 只清损坏的主文件；.part 保留给断点续传（download_one 基于其字节数发 Range）
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            if download_one(repo, rel_remote, dest):
                ok2, reason2 = _model_verify_one(rel, min_size, is_gguf)
                (repaired if ok2 else failed).append({"name": Path(rel).name, "reason": reason2})
            else:
                failed.append({"name": Path(rel).name, "reason": "全部镜像下载失败"})
    finally:
        with _REPAIR_LOCK:
            _REPAIR_STATE.update({
                "running": False,
                "repaired": repaired, "failed": failed,
                "message": ("修复完成" if not failed and repaired else
                            "模型本就完整" if not repaired and not failed else
                            f"完成，{len(failed)} 个文件仍失败") ,
            })


@router.get("/models/repair/status")
def models_repair_status():
    return _REPAIR_STATE


# --------------------------------------------------------------------------- #
# 引擎后端模式（cuda / cpu 兜底）
# --------------------------------------------------------------------------- #
@router.get("/backend/mode")
def backend_mode_get():
    ok, free = _vram_ok_for_gpu()
    return {
        "mode": backend_mode(),
        "vram_free_mb": free,
        "vram_ok": ok,
        "headroom_mb": _VRAM_HEADROOM_MB,
    }


@router.post("/backend/mode")
def backend_mode_set(payload: dict):
    mode = (payload.get("mode") or "").strip().lower()
    if mode not in ("cuda", "cpu"):
        raise HTTPException(status_code=400, detail="mode must be cuda or cpu")
    if mode == backend_mode():
        return {"ok": True, "mode": mode, "message": f"引擎已是 {mode} 模式"}
    _engine_state_write({"backend": mode})
    _restart_audiocpp(str(SERVER_JSON))
    return {
        "ok": True,
        "mode": mode,
        "message": ("已切换 CPU 模式：显存不足也能出结果，但速度慢约 5-10 倍"
                    if mode == "cpu" else "已切回 GPU（CUDA）模式"),
    }


# ---------- 谱面提取（SheetSage2 音频 → ABC 记谱，供"改词翻唱"工作流） ----------
# 异步任务模式：真实歌曲转谱常超 1 分钟，超出 dsh 反代超时上限，
# 因此提交后立即返回 job_id，前端轮询 /api/score/result 取结果。
_SCORE_JOBS: dict[str, dict] = {}
_SCORE_LOCK = threading.Lock()
# 乐谱持久化：转谱结果落盘 data/scores/，刷新/重启不丢，可复用回填
SCORES_DIR = ROOT / "runtime" / "data" / "scores"


def _score_save(job_id: str, abc: str) -> dict:
    """乐谱入库（JSON 文件），返回记录（含 id、时间、ABC、分析摘要）。"""
    SCORES_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"id": job_id, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "abc": abc, "analysis": _abc_analyze(abc)}
    (SCORES_DIR / f"{job_id}.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return rec


# ---------- ABC 乐谱分析（调性 / 拍号 / BPM / 曲式 / 音域，借鉴 YuE2-Studio 转谱分析） ----------
_KEY_MAP = {"C": "C 大调", "G": "G 大调", "D": "D 大调", "A": "A 大调", "E": "E 大调", "B": "B 大调",
            "F#": "升 F 大调", "C#": "升 C 大调", "F": "F 大调", "Bb": "降 B 大调", "Eb": "降 E 大调",
            "Ab": "降 A 大调", "Db": "降 D 大调", "Gb": "降 G 大调",
            "Am": "A 小调", "Em": "E 小调", "Bm": "B 小调", "F#m": "升 F 小调", "C#m": "升 C 小调",
            "G#m": "升 G 小调", "Dm": "D 小调", "Gm": "G 小调", "Cm": "C 小调", "Fm": "F 小调",
            "Bbm": "降 B 小调", "Ebm": "降 E 小调"}
_PITCH_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_SECTION_RE = re.compile(r"^\[([^\]\r\n]{1,32})\]", re.M)


def _abc_analyze(abc: str) -> dict:
    """从 ABC 文本提取调性/拍号/默认速度/曲式段落/音域，供前端展示摘要。"""
    out: dict = {}
    m = re.search(r"^K:\s*(\S+)", abc, re.M)
    if m:
        raw = m.group(1).strip()
        out["key_raw"] = raw
        out["key"] = _KEY_MAP.get(raw, _KEY_MAP.get(raw.rstrip("m").strip() + "m", raw))
    m = re.search(r"^M:\s*(\S+)", abc, re.M)
    if m:
        out["meter"] = m.group(1)
    m = re.search(r"^Q:\s*(?:\"[^\"]*\"|'[^']*')?\s*(?:\d+\s*/\s*\d+)?\s*=\s*(\d+)", abc, re.M)
    if m:
        try:
            out["bpm"] = int(m.group(1))
        except ValueError:
            pass
    # 曲式：取行首 [xxx] 段落标记，去重保序（Verse/Chorus/Bridge…）
    sections, seen = [], set()
    for sm in _SECTION_RE.finditer(abc):
        name = sm.group(1).strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            sections.append(name)
    if sections:
        out["sections"] = sections
    # 音域：V: 声部内的音名。ABC 记谱：大写 A-G=中音区，小写=高八度；
    # 数字跟在音名后是时值（如 C2=四分音符），八度只用 , 和 ' 表示。
    pitches: list[int] = []
    in_music = False
    for line in abc.splitlines():
        s = line.strip()
        if s.startswith(("K:", "M:", "Q:", "L:", "T:", "C:", "W:", "w:", "%%", "X:")):
            continue
        if s.upper().startswith("V:") or (s and not s[:1].isalpha()):
            in_music = True
        if in_music:
            for pm in re.finditer(r"(_|\^|=)?([A-Ga-g])([',]*)", s):
                acc, letter, octs = pm.groups()
                semi = _PITCH_SEMITONE[letter.upper()] + ({"_": -1, "^": 1}.get(acc, 0))
                if letter.islower():
                    semi += 12  # 小写 = 高八度
                for ch in octs:
                    semi += 12 if ch == "'" else -12
                pitches.append(semi)
    if pitches:
        names_sharp = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        lo, hi = min(pitches), max(pitches)
        def name(s: int) -> str:
            # 基准：未加八度记号的大写音 = 第四八度（C4=中央 C），与 ABC 惯例一致
            return names_sharp[s % 12] + str(s // 12 + 4)
        out["range"] = f"{name(lo)} → {name(hi)}（{hi - lo} 个半音）"
    return out


@router.get("/abc/analyze")
def abc_analyze(abc: str = ""):
    """对任意 ABC 文本做分析（前端输入乐谱框的实时摘要）。"""
    return {"analysis": _abc_analyze(abc[:200_000])}


@router.post("/abc/analyze")
def abc_analyze_post(payload: dict):
    """POST 版分析：规避 GET query 请求行上限（16KB），中长乐谱也能安全分析。"""
    abc = payload.get("abc") or ""
    return {"analysis": _abc_analyze(abc[:200_000])}


@router.get("/score/status")
def score_status():
    """SheetSage2 就绪状态（权重/HF 缓存是否已下载），供前端决定是否显示入口。"""
    import sheetsage_pt
    try:
        ready = bool(sheetsage_pt.mert_cached() or sheetsage_pt.resolve_checkpoint().is_dir())
    except FileNotFoundError:
        ready = False  # 权重未下载是新装环境常态，返回未就绪而非 500
    return {"ready": ready}


def _score_run(job_id: str, src: Path, melody_only: bool):
    """后台线程：执行转谱并写回任务表。"""
    started = time.time()
    try:
        import sheetsage_pt
        abc = sheetsage_pt.transcribe_abc(str(src), melody_only=melody_only)
        rec = _score_save(job_id, abc)  # 落盘：刷新/重启不丢，可复用回填
        with _SCORE_LOCK:
            _SCORE_JOBS[job_id].update({"done": True, "abc": abc,
                                        "analysis": rec.get("analysis"), "score_id": rec["id"]})
        _win_toast("🎼 乐谱提取完成", f"耗时 {int(time.time()-started)//60} 分 {int(time.time()-started)%60} 秒，已入库可回填")
    except Exception as e:
        with _SCORE_LOCK:
            _SCORE_JOBS[job_id].update({"done": True, "error": str(e)[:300]})
        _win_toast("✕ 乐谱提取失败", str(e)[:120])
    finally:
        try:
            src.unlink(missing_ok=True)
        except Exception:
            pass
        # 任务表只保留最近 20 条，防内存累积
        with _SCORE_LOCK:
            if len(_SCORE_JOBS) > 20:
                for k in sorted(_SCORE_JOBS.keys())[:-20]:
                    _SCORE_JOBS.pop(k, None)


@router.post("/score")
async def score_submit(audio: UploadFile = File(...), melody_only: str = Form("true")):
    """提交转谱任务，立即返回 job_id（前端轮询 /api/score/result）。"""
    ext = Path(audio.filename or "in.wav").suffix.lower()
    if ext not in (".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus", ".aac", ".wma"):
        ext = ".wav"
    tmp_dir = ROOT / "tmp" / "score"  # 绝对路径：不依赖进程 CWD
    tmp_dir.mkdir(parents=True, exist_ok=True)
    job_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + os.urandom(3).hex()
    src = tmp_dir / f"score_{job_id}{ext}"
    await _stream_upload_to(audio, src, 200 * 1024 * 1024, "音频")
    with _SCORE_LOCK:
        _SCORE_JOBS[job_id] = {"done": False, "ts": time.time()}
    threading.Thread(target=_score_run, args=(job_id, src, melody_only != "false"), daemon=True).start()
    return {"job_id": job_id}


@router.get("/score/result")
def score_result(job_id: str):
    job_id = os.path.basename(job_id.replace("\\", "/"))  # 防路径穿越
    with _SCORE_LOCK:
        job = _SCORE_JOBS.get(job_id)
    if not job:
        # 持久化回退：服务重启后内存任务表已清空，但已完成的结果仍落盘在 SCORES_DIR
        p = SCORES_DIR / f"{job_id}.json"
        if p.is_file():
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
                return {"done": True, "abc": rec.get("abc"), "error": None,
                        "analysis": rec.get("analysis"),
                        "score_id": rec.get("id") or job_id}
            except Exception:
                pass
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return {"done": job["done"], "abc": job.get("abc"), "error": job.get("error"),
            "analysis": job.get("analysis"),
            "score_id": job.get("score_id")}


@router.get("/scores")
def scores_list():
    """已保存乐谱列表（新→旧），供创作页回填复用。"""
    if not SCORES_DIR.is_dir():
        return []
    out = []
    for p in SCORES_DIR.glob("*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            out.append({"id": rec.get("id"), "time": rec.get("time"),
                        "abc_preview": (rec.get("abc") or "")[:120]})
        except Exception:
            continue
    out.sort(key=lambda r: r.get("id") or "", reverse=True)
    return out


@router.get("/scores/{score_id}")
def scores_get(score_id: str):
    # 路径穿越防护：只允许纯文件名（Windows 下反斜杠不分段，必须显式 basename）
    score_id = os.path.basename(score_id.replace("\\", "/"))
    p = (SCORES_DIR / f"{score_id}.json").resolve()
    if p.parent != SCORES_DIR.resolve() or not p.is_file():
        raise HTTPException(status_code=404, detail="乐谱不存在")
    return json.loads(p.read_text(encoding="utf-8"))


@router.delete("/scores/{score_id}")
def scores_delete(score_id: str):
    score_id = os.path.basename(score_id.replace("\\", "/"))
    p = (SCORES_DIR / f"{score_id}.json").resolve()
    if p.parent == SCORES_DIR.resolve() and p.is_file():
        p.unlink()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# 生成历史（服务端留存音频，可回放 / 回填 / 下载）
# --------------------------------------------------------------------------- #
HIST_DIR = ROOT / "runtime" / "data" / "records"
HIST_JSON = HIST_DIR / "history.json"
HIST_KEEP = 1000


def _hist_read() -> list[dict]:
    if not HIST_JSON.is_file():
        return []
    try:
        data = json.loads(HIST_JSON.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _hist_write(items: list[dict]) -> None:
    HIST_JSON.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@router.get("/history")
def history_list():
    return {"items": _hist_read()}


@router.get("/history/active")
def history_active():
    """进行中/排队的任务（历史页顶部展示）：生成 + 换声 + 音色训练。"""
    items = []
    # 生成任务：output/ 里 running/pending 状态的 meta（cancelled 已终止，不算进行中）
    if OUTPUT_DIR.is_dir():
        for p in sorted(OUTPUT_DIR.glob("*.json"), reverse=True):
            m = _output_read_meta(p.stem)
            if m and m.get("status") in ("running", "pending"):
                m["kind"] = m.get("kind") or "generate"
                items.append(m)
    # 换声任务
    with _RVC_LOCK:
        items += [j for j in _RVC_JOBS.values() if j.get("status") == "running"]
    # 音色制作任务
    if RVC_TRAIN_DIR.is_dir():
        for d in sorted(RVC_TRAIN_DIR.iterdir(), reverse=True):
            job = _rvc_train_read(d.name)
            # 含 paused：暂停任务需在进度列表显示「▶ 继续」，否则重启后找不到入口续跑
            if job.get("status") in ("running", "pending", "paused"):
                if job.get("status") == "running" and job.get("step") == "训练中":
                    cur, total = _rvc_train_epoch(job.get("name", ""), int(job.get("epochs") or 0))
                    if cur:
                        job["epoch"] = cur
                        job["epochs_total"] = total or job.get("epochs", 0)
                job["kind"] = "train"
                items.append(job)
    return {"items": items}


# --------------------------------------------------------------------------- #
# 上传流式写盘：分块写、边写边判限额，不再整读进内存
# （旧写法 await file.read() 会让 200MB 上传先吃光内存再判超限）
# --------------------------------------------------------------------------- #
async def _stream_upload_to(file: UploadFile, dest: Path, limit: int,
                            label: str = "文件") -> int:
    """把上传文件分块流式写到 dest，超过 limit 字节抛 413 并清理残文件。返回写入字节数。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    total = 0
    try:
        with open(tmp, "wb") as fh:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"{label}超过 {limit // (1024*1024)}MB 上限，请先剪辑或压缩（5 分钟 wav 约 50MB）")
                fh.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="上传音频为空")
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return total


@router.post("/history")
async def history_add(audio: UploadFile = File(...), meta: str = Form("{}")):
    HIST_DIR.mkdir(parents=True, exist_ok=True)
    try:
        m = json.loads(meta or "{}")
    except Exception:
        m = {}
    now = datetime.now()
    rid = now.strftime("%Y%m%d_%H%M%S_") + os.urandom(3).hex()
    fn = rid + ".wav"
    size = await _stream_upload_to(audio, HIST_DIR / fn, 200 * 1024 * 1024, "音频")
    item = {
        "id": rid,
        "ts": now.isoformat(timespec="seconds"),
        "file": fn,
        "bytes": size,
        "style": str(m.get("style", ""))[:600],
        "lyrics": str(m.get("lyrics", ""))[:4000],
        "cot": m.get("cot", "full"),
        "abc": str(m.get("abc", ""))[:4000],
        "seed": m.get("seed"),
        "cfg": m.get("cfg_scale"),
        "steps": m.get("num_inference_steps"),
        "sec": m.get("sec"),
        "seed_actual": m.get("seed_actual"),
        "model": m.get("model_used", ""),
    }
    items = [item] + _hist_read()
    # 只保留最近 HIST_KEEP 条，同时删除落盘的旧音频
    for gone in items[HIST_KEEP:]:
        (HIST_DIR / gone.get("file", "")).unlink(missing_ok=True)
    items = items[:HIST_KEEP]
    _hist_write(items)
    return {"ok": True, "item": item}


@router.get("/history/{rid}/audio")
def history_audio(rid: str):
    rid = os.path.basename(rid)
    fn = next((i["file"] for i in _hist_read() if i["id"] == rid), None)
    if not fn:
        raise HTTPException(status_code=404, detail="not found")
    fp = HIST_DIR / fn
    if not fp.is_file():
        raise HTTPException(status_code=404, detail="audio file missing")
    return Response(content=fp.read_bytes(), media_type="audio/wav")


@router.delete("/history/clear")
def history_clear():
    for i in _hist_read():
        (HIST_DIR / i.get("file", "")).unlink(missing_ok=True)
    _hist_write([])
    return {"ok": True}


@router.delete("/history/{rid}")
def history_delete(rid: str):
    rid = os.path.basename(rid)
    items = _hist_read()
    keep = [i for i in items if i["id"] != rid]
    if len(keep) == len(items):
        raise HTTPException(status_code=404, detail="not found")
    for gone in items:
        if gone["id"] == rid:
            (HIST_DIR / gone.get("file", "")).unlink(missing_ok=True)
    _hist_write(keep)
    return {"ok": True}


@router.post("/models/switch")
def models_switch(payload: dict):
    path = (payload.get("path") or "").strip().replace("\\", "/")
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    # 只允许 model/ 前缀 + 纯文件名，防 ../ 穿越（server.json 本就要求相对路径）
    safe = Path(path).name
    target = MODEL_DIR / safe
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"model file not found: {safe}")
    cfg = _read_server_json()
    # session_options：纯文件名，由后端相对 path 所在目录解析
    model_gguf = target.name
    vae = (payload.get("vae") or "").strip().replace("\\", "/")
    vae = Path(vae).name if vae else None
    if not vae:
        vae = next((p.name for p in target.parent.glob("*vae*.gguf")), None)
    if not vae or not (target.parent / vae).is_file():
        # 模型目录内没有 vae 时，退回 cpp/model 根的公共 vae（同目录文件用纯文件名）
        if (target.parent / "yue2-vae-f16.gguf").is_file():
            vae = "yue2-vae-f16.gguf"
        else:
            raise HTTPException(
                status_code=400,
                detail=f"未在 {target.parent.name} 目录找到 vae gguf，无法切换",
            )
    model_entry = cfg["models"][0]
    # path 相对后端工作目录 cpp\ —— 必须带 model/ 前缀
    model_entry["path"] = "model/" + path
    model_entry["session_options"] = {
        "yue2.model_gguf": model_gguf,
        "yue2.vae_gguf": vae,
    }
    _write_server_json(cfg)
    # 重启后端加载新模型
    _restart_audiocpp(str(SERVER_JSON))
    return {
        "ok": True,
        "message": f"已切换到 {model_gguf}，后端已重启加载。",
        "path": path,
    }


# --------------------------------------------------------------------------- #
# 服务端托管生成任务：output/ 自动落盘，成败都保存（前端刷新不丢任务）
# --------------------------------------------------------------------------- #
OUTPUT_DIR = ROOT / "runtime" / "output"


def _output_meta_path(rid: str) -> Path:
    return OUTPUT_DIR / f"{rid}.json"


def _output_wav_path(rid: str) -> Path:
    return OUTPUT_DIR / f"{rid}.wav"


def _output_lyrics_path(rid: str) -> Path:
    return OUTPUT_DIR / f"{rid}.txt"


def _output_lrc_path(rid: str) -> Path:
    return OUTPUT_DIR / f"{rid}.lrc"


def _lrc_job_read(rid: str) -> dict | None:
    try:
        return json.loads((_output_lrc_path(rid).with_suffix(".lrcjob")).read_text(encoding="utf-8"))
    except Exception:
        return None


def _lrc_job_write(rid: str, state: dict) -> None:
    p = _output_lrc_path(rid).with_suffix(".lrcjob")
    try:
        p.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _lrc_build_thread(rid: str) -> None:
    """后台线程：对齐歌词与音频生成 .lrc，状态写 .lrcjob 供前端轮询。

    主路径改为**强制对齐**（src/lrc_align.py）：歌词文本已知，只需把它压到音频上，
    不做 ASR 识别，因此不受歌声漏识影响（实测起唱点误差中位 2.07s → 0.66s）。
    强制对齐不可用时才退回旧的 whisper 段级匹配链路（src/lrc.py）。
    """
    _lrc_job_write(rid, {"status": "running"})
    try:
        meta = _output_read_meta(rid)
        wav = _output_wav_path(rid)
        lyrics = str(meta.get("lyrics") or "") if meta else ""
        if not meta or not wav.is_file() or not lyrics.strip():
            raise RuntimeError("任务缺少音频或歌词")
        text, method, diag = None, "legacy", {}
        # 主路径：强制对齐（fa-zh 中文字级 / ctc-wav2vec2 英文词级 / vad-distribute 末级）。
        # 全程无 ASR，比旧的 SenseVoice/whisper 识别兜底更准（歌声漏识严重）。
        if lrc_align_mod is not None:
            try:
                res = lrc_align_mod.align(wav, lyrics)
                if res.get("lrc"):
                    text, method = res["lrc"], res.get("method", "align")
                    diag = res.get("diagnostics") or {}
                    if res.get("elrc"):
                        (OUTPUT_DIR / f"{rid}.elrc").write_text(res["elrc"], encoding="utf-8")
            except Exception as exc:  # 强制对齐彻底失败 → 旧链路兜底
                logger.warning("强制对齐失败，退回旧链路：%s", exc)
        if text is None:  # 强制对齐未产出（极罕见）→ 旧链路 whisper 兜底
            text = lrc_mod.generate_lrc(wav, lyrics, meta.get("task_name", ""))
        _output_lrc_path(rid).write_text(text, encoding="utf-8")
        _lrc_job_write(rid, {"status": "done", "method": method, "diagnostics": diag})
    except HTTPException as e:
        _lrc_job_write(rid, {"status": "error", "error": str(e.detail)})
    except Exception as e:
        _lrc_job_write(rid, {"status": "error", "error": str(e)[:300]})


def _lrc_build_async(rid: str) -> None:
    threading.Thread(target=_lrc_build_thread, args=(rid,), daemon=True).start()


def _lyrics_clean(text: str) -> str:
    """清洗歌词为可投稿的纯文本：去掉 YuE2 特有指令标记，保留结构段落标记。

    去除 [structure]/[mix]/[verse] 等中括号以外的模型指令行
    （如 [verse-start] 之外的 --lam/param 之类），并把多个空行合并。
    """
    lines = []
    for raw in str(text or "").splitlines():
        line = raw.rstrip()
        # 跳过纯指令行：形如 [xxx] 的 YuE 结构标记之外的参数/注释行
        if re.fullmatch(r"\s*(--[\w-]+(\s+\S+)?)\s*", line):
            continue
        lines.append(line)
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out).strip() + "\n"
    return out


def _output_write_lyrics(rid: str, lyrics: str, task_name: str = "") -> None:
    """生成成功后把歌词落盘为 txt（方便投稿音乐平台）。失败静默，不影响主流程。"""
    try:
        if not str(lyrics or "").strip():
            return
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        header = f"歌曲：{task_name}\n" if task_name else ""
        _output_lyrics_path(rid).write_text(
            header + _lyrics_clean(lyrics), encoding="utf-8"
        )
    except Exception:
        pass


def _output_read_meta(rid: str) -> dict | None:
    p = _output_meta_path(rid)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _output_write_meta(meta: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (_output_meta_path(meta["id"])).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# 内存中的当前任务（服务重启后靠 output/*.json 的 running 状态恢复为 error/orphan）
_GEN_JOB: dict | None = None
_GEN_LOCK = threading.Lock()
# 启动后懒清理只允许执行一次的标志（防止前端轮询把运行中任务误判为中断）
_ORPHAN_SWEEP_DONE = False

# GPU 全局互斥闸门：生成/批量/换声/训练四类 GPU 任务统一排队，防止并发打满显存互杀后端
_GPU_SEM = threading.Semaphore(1)


class _GpuBusy(HTTPException):
    def __init__(self, detail: str):
        super().__init__(status_code=409, detail=detail)


def _gen_set_job(job: dict | None) -> None:
    global _GEN_JOB
    with _GEN_LOCK:
        _GEN_JOB = job


def _gen_get_job() -> dict | None:
    with _GEN_LOCK:
        return _GEN_JOB


_CANCEL_EVENT = threading.Event()  # 用户主动终止：中断当前推理并停止后续连发


def _kill_audiocpp_now() -> None:
    """强杀引擎进程以立即中断推理（连接断开即刻失败），下次生成前 _ensure_backend 会自动重启。"""
    def _kill():
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/IM", "audiocpp_server.exe", "/F"],
                               capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                subprocess.run(["pkill", "-f", "audiocpp_server"], capture_output=True)
        except Exception:
            pass
    threading.Thread(target=_kill, daemon=True).start()


def _is_vram_error(msg: str) -> bool:
    return bool(re.search(
        r"memory|alloc|oom|out_of_memory|free_memory|cuda_error|vram",
        str(msg), re.I,
    ))


def _gen_run_once(payload: dict) -> tuple[int, str, bytes, dict]:
    """调用一次 /api/music/generate，返回 (status_code, detail, content, headers)。"""
    with httpx.Client(
        base_url=f"http://127.0.0.1:{settings.app_port}", timeout=settings.audiocpp_timeout_sec,
        trust_env=False,
    ) as client:
        r = client.post("/api/music/generate", json=payload)
    detail = r.text[:800]
    try:
        detail = json.loads(detail).get("detail", detail)
    except Exception:
        pass
    return r.status_code, detail, r.content, dict(r.headers)


def _vram_ok_for_gpu() -> tuple[bool, int | None]:
    """生成前显存预检：空闲显存需 >= 权重峰值(~3GB) + 余量。返回 (是否通过, 空闲MB)。"""
    free = _gpu_free_mb()
    if free is None:
        return True, None  # 查不到就不拦截，交给引擎自己报错
    return free >= _VRAM_HEADROOM_MB + 3000, free


def _audiocpp_alive() -> bool:
    """后端健康探测（1s 超时，任何异常视为掉线）。"""
    try:
        r = httpx.get(settings.audiocpp_base_url + "/health", timeout=1.0)
        return r.status_code < 500
    except Exception:
        return False


def _commit_free_mb() -> int | None:
    """系统可用提交内存（MB）。查不到返回 None。"""
    try:
        import ctypes
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MEMORYSTATUSEX(); m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return int(m.ullAvailPageFile // (1024 * 1024))  # 可用提交 = 可用页文件
    except Exception:
        return None


def _ensure_backend() -> None:
    """生成/换声前自检：后端掉线则自动拉起，避免整次生成直接失败。"""
    if _audiocpp_alive():
        return
    try:
        _restart_audiocpp(str(SERVER_JSON))
    except Exception:
        pass
    # 等后端就绪（最多 90s，加载权重需要时间）
    for _ in range(45):
        if _audiocpp_alive():
            return
        time.sleep(2.0)


def _win_toast(title: str, body: str) -> None:
    """Windows 系统通知（浏览器最小化/后台也可见）。fire-and-forget，失败静默。"""
    try:
        # 文本经 base64 传入，杜绝 $/引号/反引号 破坏 PowerShell 表达式
        tb64 = base64.b64encode(title.encode("utf-8")).decode("ascii")
        bb64 = base64.b64encode(body.encode("utf-8")).decode("ascii")
        ps = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
            "ContentType = WindowsRuntime] | Out-Null; "
            f"$ti = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{tb64}')); "
            f"$bo = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{bb64}')); "
            "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
            "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
            "$x = $t.GetXml(); "
            "$x = $x -replace '<text id=\"1\">', ('<text id=\"1\">' + $ti + '</text><text id=\"2\">') ; "
            "$x = $x -replace '</text></toast>', ($bo + '</text></toast>'); "
            "[void]$t.LoadXml($x); "
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
            "'音乐工作台').Show([Windows.UI.Notifications.ToastNotification]::new($t))"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _gen_run(job: dict, payload: dict) -> None:
    """后台线程：调用本网关的 /api/music/generate，把结果落盘到 output/。

    无论成功或失败，meta（含 style / lyrics / 全部参数 / 错误信息）都会保存。
    GPU 显存不足（预检或引擎报错）时自动切 CPU 后端重试（慢但能出结果），
    完成后自动切回 GPU 模式。切换过程记录在 job.cpu_fallback / job.gpu_error。
    """
    rid = job["id"]
    started = time.time()
    used_cpu_fallback = False
    try:
        # 后端自检：audiocpp 掉线（如上次异常崩溃）则自动拉起，避免整次生成直接失败
        _ensure_backend()
        # 提交内存预检：full/32步 重负载任务实测需 ~23GB commit（权重+AR/NAR 缓存），
        # 不足时 audiocpp 会在推理中段 abort（0xc0000409）白白等几分钟，这里提前拦截
        cf = _commit_free_mb()
        if cf is not None and cf < 8000:
            raise RuntimeError(
                f"系统可用提交内存不足（剩 {cf // 1024}GB，需 ≥8GB）。"
                "请关闭其他占内存的程序后重试；或把页面文件调大（建议初始 30GB/最大 48GB）。")
        # 生成前显存预检：不足就直接用 CPU，不先撞一次墙
        if backend_mode() == "cuda":
            # 若之前提取过乐谱，SheetSage2/MERT 模型常驻 GPU 会挤占显存，
            # 先把它们从显存卸载并清空缓存，避免生成被误判降级 CPU。
            try:
                import sheetsage_pt
                sheetsage_pt.unload()
            except Exception:
                pass
            ok, free = _vram_ok_for_gpu()
            if not ok:
                used_cpu_fallback = True
                job.update(
                    status="running", cpu_fallback=True,
                    gpu_error=f"生成前显存预检不足：空闲 {free}MB",
                    note="显存不足，已自动切换 CPU 后端（速度慢约 5-10 倍）",
                )
                _output_write_meta(job)
                _restart_audiocpp(str(SERVER_JSON), backend="cpu")
                _wait_backend_ready()

        def attempt() -> tuple[int, str, bytes, dict]:
            try:
                return _gen_run_once(payload)
            except Exception as e:
                return 0, str(e)[:800], b"", {}

        if _CANCEL_EVENT.is_set():
            return

        code, detail, content, headers = attempt()

        # 用户主动终止：引擎进程已被强杀，这次调用以连接失败收场
        if _CANCEL_EVENT.is_set():
            return

        # 引擎仍然报显存类错误（预检漏网/竞争）：切 CPU 重试一次
        if code != 200 and not used_cpu_fallback and backend_mode() == "cuda" and _is_vram_error(detail):
            used_cpu_fallback = True
            job.update(
                status="running", cpu_fallback=True,
                gpu_error=str(detail)[:800],
                note="显存不足，已自动切换 CPU 后端重试（速度慢约 5-10 倍）",
            )
            _output_write_meta(job)
            _restart_audiocpp(str(SERVER_JSON), backend="cpu")
            _wait_backend_ready()
            if _CANCEL_EVENT.is_set():
                return
            code, detail, content, headers = attempt()

        elapsed = round(time.time() - started, 1)
        title = (job.get("style") or "").split(",")[0][:40] or rid
        if code != 200:
            job.update(status="error", error=str(detail), sec=elapsed)
            _output_write_meta(job)
            _win_toast("✕ 生成失败：" + title, str(detail)[:120])
        else:
            seed_actual = headers.get("X-Seed") or payload.get("seed")
            wav = _output_wav_path(rid)
            wav.write_bytes(content)
            job.update(
                status="done",
                bytes=len(content),
                sec=elapsed,
                seed_actual=seed_actual,
                file=wav.name,
            )
            _output_write_meta(job)
            _output_write_lyrics(rid, job.get("lyrics", ""), job.get("task_name", ""))
            _lrc_build_async(rid)  # 后台对齐生成 .lrc（几秒到几十秒，不阻塞主流程）
            _win_toast(
                "🎵 生成完成：" + title,
                f"耗时 {int(elapsed // 60)} 分 {int(elapsed % 60)} 秒，已保存到 output/",
            )

        # 兜底用完把引擎切回 GPU，避免下次不明不白变慢
        if used_cpu_fallback and backend_mode() == "cpu":
            try:
                _engine_state_write({"backend": "cuda"})
                _restart_audiocpp(str(SERVER_JSON), backend="cuda")
            except Exception:
                pass
    except Exception as e:  # 网络/超时/后端崩溃都算失败，同样落盘
        job.update(status="error", error=str(e)[:800], sec=round(time.time() - started, 1))
        _output_write_meta(job)


def _wait_backend_ready(timeout: float = 300.0) -> bool:
    """等待 audio.cpp 后端 /health 恢复（模型加载可能要 1-2 分钟）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with httpx.Client(base_url=settings.audiocpp_base_url,
                              timeout=5.0, trust_env=False) as c:
                if c.get("/health").status_code == 200:
                    return True
        except Exception:
            pass
        time.sleep(2.0)
    return False


@router.post("/generate/stop")
def generate_stop():
    """用户主动终止当前生成/连发任务：标记 cancelled、强杀引擎立即中断推理、
    阻止后续连发与批量队列继续。网关本身不停，下次生成会自动重启引擎。"""
    job = _gen_get_job()
    # 占位 job（id=None，排队中）只清状态不写盘，避免生成 output/None.json
    cancelled_any = bool(job and job.get("id") and job.get("status") == "running")
    if cancelled_any:
        job["status"] = "cancelled"
        job["error"] = "用户主动终止"
        job["sec"] = None
        _output_write_meta(job)
    _gen_set_job(None)  # 立即结束轮询/恢复态
    _CANCEL_EVENT.set()
    _kill_audiocpp_now()
    # 批量队列一并叫停：未开始的全部取消（读-改-写整体持锁，防止与批量 worker 互相覆盖）
    # 注意：锁内必须用无锁的 _batch_read()/_batch_write()；
    # _batch_snapshot()/_batch_store() 会重复加同一把非重入锁导致死锁
    with _BATCH_LOCK:
        state = _batch_read()
        if state.get("running"):
            state["running"] = False
            for it in state["items"]:
                if it["status"] in ("pending", "running"):
                    it["status"] = "cancelled"
            _batch_write(state)
    return {"ok": True, "cancelled": cancelled_any,
            "message": "已终止当前任务" + ("，引擎将自动恢复" if cancelled_any else "")}


@router.post("/generate/start")
async def generate_start(payload: dict):
    global _ORPHAN_SWEEP_DONE, _GEN_JOB
    style = str(payload.get("style") or "").strip()
    if not style:
        raise HTTPException(status_code=400, detail="style is required")
    # check-and-set 原子化：先占位再放线程，防止并发请求双双通过检查（TOCTOU）
    with _GEN_LOCK:
        cur = _GEN_JOB
        if cur and cur.get("status") == "running":
            raise HTTPException(status_code=409, detail="已有生成任务在进行中")
        _ORPHAN_SWEEP_DONE = True  # 已有真实任务登记，懒清理不再触发
        _GEN_JOB = {"id": None, "status": "running", "queued": True}
    try:
        count = max(1, min(20, int(payload.get("count") or 1)))
    except Exception:
        count = 1

    def _make_job(i: int, seed_val) -> dict:
        rid = datetime.now().strftime("%Y%m%d_%H%M%S_") + os.urandom(2).hex()
        params = {
            k: payload.get(k)
            for k in ("cfg_scale", "num_inference_steps",
                      "abc_temperature", "abc_top_p", "abc_top_k",
                      "semantic_temperature", "semantic_top_p", "semantic_top_k")
            if payload.get(k) is not None
        }
        if seed_val is not None:
            params["seed"] = seed_val
        return {
            "id": rid,
            "status": "running",
            "ts": datetime.now().isoformat(timespec="seconds"),
            "style": style[:600],
            "lyrics": str(payload.get("lyrics") or "")[:4000],
            "cot": payload.get("cot", "full"),
            "abc": str(payload.get("abc") or "")[:4000],
            "task_name": str(payload.get("task_name") or "").strip()[:100],
            "model": payload.get("model", "yue2"),
            "params": params,
            "chain_index": i,
            "chain_total": count,
        }

    # 连发 N 次：参数完全一致，仅种子不同（用户填了种子则依次 +1，否则完全随机）。
    # 无效 seed（-1/None，即"随机"）在这里就定成真实随机数——历史记录存下
    # 引擎实际用的种子，回填时才能复现同一结果，而不是把 -1 填回表单
    base_seed = payload.get("seed")
    if not (isinstance(base_seed, int) and base_seed >= 0):
        base_seed = secrets.randbelow(2**31)
    payload = {**payload, "seed": base_seed}

    def _chain_runner():
        _CANCEL_EVENT.clear()
        try:
            # GPU 全局闸门：等批量/换声/训练任务释放后再开始（无超时，保证最终会执行）
            with _GPU_SEM:
                for i in range(1, count + 1):
                    if _CANCEL_EVENT.is_set():
                        break
                    p = dict(payload)
                    p.pop("count", None)
                    if count > 1:
                        if isinstance(base_seed, int) and base_seed >= 0:
                            p["seed"] = base_seed + (i - 1)
                        else:
                            p["seed"] = secrets.randbelow(2**31)
                    job = _make_job(i, p.get("seed") if isinstance(p.get("seed"), int) else None)
                    _output_write_meta(job)
                    _gen_set_job(job)
                    _gen_run(job, p)
                    # 单首失败不中断连发，继续下一首
            # 保留最后一个任务的最终状态（done/error），供页面刷新后恢复；下次 start 时会被覆盖
        finally:
            # 占位 job 未被真实任务替换（取消/异常）时清掉，避免轮询端永远显示"排队中"
            j = _gen_get_job()
            if j and j.get("id") is None:
                _gen_set_job(None)
    threading.Thread(target=_chain_runner, daemon=True).start()
    return {"ok": True, "count": count, "job": _make_job(1, base_seed if isinstance(base_seed, int) else None)}


@router.get("/generate/current")
def generate_current():
    """前端刷新后靠这个接口恢复“生成中”状态；也返回最近一条结果。"""
    job = _gen_get_job()
    if job is None:
        # 服务重启过：扫描 output/ 里遗留的 running 状态，标记为中断。
        # 仅启动后首次调用生效（_STARTUP_EPOCH 之后落盘的 running 是排队中的正常任务，
        # 不能改判，否则前端常态轮询会把运行中的换声/批量任务误标为"中断"）。
        global _ORPHAN_SWEEP_DONE
        if not _ORPHAN_SWEEP_DONE and OUTPUT_DIR.is_dir():
            _ORPHAN_SWEEP_DONE = True
            for p in sorted(OUTPUT_DIR.glob("*.json")):
                try:
                    m = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if m.get("status") == "running":
                    m["status"] = "error"
                    m["error"] = "服务重启，任务中断"
                    p.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        latest = None
        if OUTPUT_DIR.is_dir():
            metas = [m for m in (_output_read_meta(p.stem) for p in OUTPUT_DIR.glob("*.json")) if m]
            metas.sort(key=lambda m: m.get("ts", ""), reverse=True)
            latest = metas[0] if metas else None
        return {"job": None, "latest": latest}
    return {"job": job}


@router.get("/generate/list")
def generate_list():
    items = []
    if OUTPUT_DIR.is_dir():
        for p in sorted(OUTPUT_DIR.glob("*.json"), reverse=True):
            m = _output_read_meta(p.stem)
            if m:
                items.append(m)
    return {"items": items[:1000]}


@router.get("/generate/audio/{rid}")
def generate_audio(rid: str):
    rid = os.path.basename(rid)
    fp = _output_wav_path(rid)
    if not fp.is_file():
        raise HTTPException(status_code=404, detail="audio not found")
    return Response(content=fp.read_bytes(), media_type="audio/wav")


@router.get("/generate/lyrics/{rid}")
def generate_lyrics(rid: str):
    """歌词文件下载（历史任务回填）：txt 已存在直接返回，否则从 meta 现生成一份。"""
    rid = os.path.basename(rid)
    meta = _output_read_meta(rid)
    if not meta:
        raise HTTPException(status_code=404, detail="task not found")
    fp = _output_lyrics_path(rid)
    if not fp.is_file():
        _output_write_lyrics(rid, meta.get("lyrics", ""), meta.get("task_name", ""))
        if not fp.is_file():
            raise HTTPException(status_code=404, detail="该任务没有歌词内容")
    return Response(
        content=fp.read_bytes(),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{rid}.txt"'},
    )


@router.post("/generate/lrc/{rid}")
def generate_lrc_build(rid: str):
    """为历史任务生成/重建 LRC（后台对齐，前端轮询 /generate/lrc/{rid} 查状态）。"""
    rid = os.path.basename(rid)
    meta = _output_read_meta(rid)
    if not meta:
        raise HTTPException(status_code=404, detail="task not found")
    if not _output_wav_path(rid).is_file():
        raise HTTPException(status_code=404, detail="音频文件不存在")
    if not str(meta.get("lyrics") or "").strip():
        raise HTTPException(status_code=400, detail="该任务没有歌词内容")
    _lrc_build_async(rid)
    return {"ok": True, "status": "running"}


@router.get("/generate/lrc/{rid}")
def generate_lrc_status(rid: str):
    """LRC 生成状态查询；done 时直接返回 lrc 文本。"""
    rid = os.path.basename(rid)
    fp = _output_lrc_path(rid)
    if fp.is_file():
        out = {"status": "done", "lrc": fp.read_text(encoding="utf-8")}
        job = _lrc_job_read(rid) or {}
        # 附带对齐方式与诊断（方法/命中率/偏差），便于判断时间轴可信度
        if job.get("method"):
            out["method"] = job["method"]
        if job.get("diagnostics"):
            out["diagnostics"] = job["diagnostics"]
        elrc = OUTPUT_DIR / f"{rid}.elrc"
        if elrc.is_file():
            out["elrc"] = elrc.read_text(encoding="utf-8")
        return out
    job = _lrc_job_read(rid)
    if job:
        return job
    return {"status": "none"}


@router.patch("/generate/{rid}/name")
def generate_rename(rid: str, payload: dict = Body(default={})):
    """重命名历史任务（改 meta 里的 task_name，历史展示与下载命名同步生效）。"""
    rid = os.path.basename(rid)
    meta = _output_read_meta(rid)
    if not meta:
        raise HTTPException(status_code=404, detail="task not found")
    name = str(payload.get("task_name") or "").strip()[:100]
    if not name:
        raise HTTPException(status_code=400, detail="任务名不能为空")
    meta["task_name"] = name
    _output_write_meta(meta)
    return {"ok": True, "task_name": name}


@router.delete("/generate/{rid}")
def generate_delete(rid: str):
    rid = os.path.basename(rid)
    job = _gen_get_job()
    if job and job.get("id") == rid and job.get("status") == "running":
        raise HTTPException(status_code=409, detail="任务进行中，不能删除")
    removed = False
    for p in (_output_wav_path(rid), _output_meta_path(rid),
              _output_lyrics_path(rid), _output_lrc_path(rid),
              _output_lrc_path(rid).with_suffix(".lrcjob")):
        if p.is_file():
            p.unlink()
            removed = True
    # 批量队列条目兜底：其产物可能已被清理（output 无文件），但队列记录还在
    # batch_state.json 里——不清理的话列表里永远删不掉（实测「冒烟测试」）
    state = _batch_read()
    items = state.get("items") or []
    if any(it.get("id") == rid for it in items):
        state["items"] = [it for it in items if it["id"] != rid]
        _batch_store(state)
        removed = True
    if not removed:
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True}


# --------------------------------------------------------------------------- #
# 批量生成队列：顺序执行，单个失败不影响后续；任务与结果全部落盘
# --------------------------------------------------------------------------- #
_BATCH_STATE = ROOT / "runtime" / "data" / "batch_state.json"


def _batch_read() -> dict:
    try:
        return json.loads(_BATCH_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"items": [], "running": False, "current": None}


def _batch_write(state: dict) -> None:
    _BATCH_STATE.parent.mkdir(parents=True, exist_ok=True)
    _BATCH_STATE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


_BATCH_LOCK = threading.Lock()
_BATCH_WORKER: threading.Thread | None = None


def _batch_snapshot() -> dict:
    with _BATCH_LOCK:
        return _batch_read()


def _batch_store(state: dict) -> None:
    with _BATCH_LOCK:
        _batch_write(state)


def _batch_run_worker() -> None:
    """批量队列工作线程：逐个执行 pending 任务；失败记录后继续下一个。"""
    _CANCEL_EVENT.clear()
    while True:
        state = _batch_snapshot()
        if not state.get("running") or _CANCEL_EVENT.is_set():
            return
        nxt = next((it for it in state["items"] if it["status"] == "pending"), None)
        if nxt is None:
            state["running"] = False
            state["current"] = None
            _batch_store(state)
            return
        nxt["status"] = "running"
        nxt["started_ts"] = datetime.now().isoformat(timespec="seconds")
        state["current"] = nxt["id"]
        _batch_store(state)

        payload = dict(nxt["payload"])
        rid = nxt["id"]
        job = {
            "id": rid,
            "status": "running",
            "ts": nxt["started_ts"],
            "style": payload.get("style", "")[:600],
            "lyrics": str(payload.get("lyrics") or "")[:4000],
            "cot": payload.get("cot", "full"),
            "abc": str(payload.get("abc") or "")[:4000],
            "task_name": str(nxt.get("name") or "")[:100],
            "model": payload.get("model", "yue2"),
            "params": {
                k: payload.get(k)
                for k in ("seed", "cfg_scale", "num_inference_steps",
                          "abc_temperature", "abc_top_p", "abc_top_k",
                          "semantic_temperature", "semantic_top_p", "semantic_top_k")
                if payload.get(k) is not None
            },
            "batch": True,
            "batch_name": nxt.get("name", ""),
        }
        _output_write_meta(job)
        _gen_set_job(job)
        with _GPU_SEM:  # 与单首生成/换声/训练互斥，防止并发打满显存
            if _CANCEL_EVENT.is_set():
                break
            _gen_run(job, payload)
        if _CANCEL_EVENT.is_set():
            break
        # _gen_run 已把 job 状态更新为 done/error 并写 output meta
        final = _output_read_meta(rid) or job
        # 锁内最小化更新：只改当前条目与 current，不整体回写，避免覆盖 stop 的并发标记
        with _BATCH_LOCK:
            state = _batch_read()
            it = next((x for x in state["items"] if x["id"] == rid), None)
            if it is not None and state.get("current") == rid and it.get("status") != "cancelled":
                it["status"] = "done" if final.get("status") == "done" else "error"
                it["sec"] = final.get("sec")
                it["error"] = final.get("error", "")
                it["bytes"] = final.get("bytes")
            state["current"] = None
            _batch_write(state)


def _batch_ensure_worker() -> None:
    global _BATCH_WORKER
    if _BATCH_WORKER is None or not _BATCH_WORKER.is_alive():
        _BATCH_WORKER = threading.Thread(target=_batch_run_worker, daemon=True)
        _BATCH_WORKER.start()


@router.post("/batch/start")
def batch_start(payload: dict):
    """payload: {name?: str, tasks: [{name?, lyrics, style, cot, ...}, ...]}"""
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise HTTPException(status_code=400, detail="tasks is required (non-empty list)")
    state = _batch_snapshot()
    # 已有任务在进行中（批量在跑或单首生成占用 GPU）时，新任务追加到队尾排队，不插队
    appending = bool(state.get("items"))
    qname = str(payload.get("name") or "").strip() or datetime.now().strftime("%m%d-%H%M")
    items = []
    for i, t in enumerate(tasks, 1):
        style = str(t.get("style") or "").strip()
        lyrics = str(t.get("lyrics") or "").strip()
        if not style or not lyrics:
            raise HTTPException(status_code=400,
                                detail=f"第 {i} 个任务缺少 style 或 lyrics")
        rid = datetime.now().strftime("%Y%m%d_%H%M%S_") + os.urandom(2).hex()
        items.append({
            "id": rid,
            "name": str(t.get("name") or f"{qname} #{i}")[:100],
            "status": "pending",
            "payload": {
                "lyrics": lyrics[:4000], "style": style[:600],
                "cot": t.get("cot", "off"), "abc": str(t.get("abc") or "")[:4000],
                "model": t.get("model", "yue2"),
                "seed": t.get("seed"), "cfg_scale": t.get("cfg_scale"),
                "num_inference_steps": t.get("num_inference_steps"),
                "abc_temperature": t.get("abc_temperature"),
                "abc_top_p": t.get("abc_top_p"), "abc_top_k": t.get("abc_top_k"),
                "semantic_temperature": t.get("semantic_temperature"),
                "semantic_top_p": t.get("semantic_top_p"),
                "semantic_top_k": t.get("semantic_top_k"),
            },
        })
    if appending:
        # 排队模式：追加到现有批量队列尾部，沿用其队列名
        with _BATCH_LOCK:
            state = _batch_read()
            state["items"].extend(items)
            if not state.get("running"):
                state["running"] = True
            if not state.get("name"):
                state["name"] = qname
            _batch_write(state)
    else:
        state = {"items": items, "running": True, "current": None, "name": qname}
        _batch_store(state)
    _batch_ensure_worker()
    total = len(_batch_snapshot()["items"])
    msg = (f"已加入队列（排在第 {total - len(items) + 1}~{total} 位，当前任务完成后依次执行）"
           if appending else f"已开始批量任务（共 {len(items)} 首）")
    return {"ok": True, "queued": appending, "name": state.get("name") or qname,
            "count": len(items), "total": total, "items": items, "message": msg}


@router.get("/batch/status")
def batch_status():
    state = _batch_snapshot()
    # 僵尸队列自愈：running=True 但 worker 线程已死（进程内异常退出/重启后标志未清）
    # 且当前没有真实任务在跑——复位标志并把卡死条目放回 pending，重新拉起 worker。
    worker_alive = _BATCH_WORKER is not None and _BATCH_WORKER.is_alive()
    gen_running = (_gen_get_job() or {}).get("status") == "running"
    if state.get("running") and not worker_alive and not gen_running:
        restored = 0
        for it in state["items"]:
            if it["status"] == "running":
                it["status"] = "pending"
                restored += 1
        state["current"] = None
        # 仍有待跑条目则保持 running=True 再拉起 worker（worker 只在 running 时工作）；
        # 否则清除假死标志
        state["running"] = any(it["status"] == "pending" for it in state["items"])
        _batch_store(state)
        if state["running"]:
            _CANCEL_EVENT.clear()
            _batch_ensure_worker()
    if not state.get("running"):
        # 服务重启或工作线程死亡时，把遗留的 running 任务标记为中断
        changed = False
        for it in state["items"]:
            if it["status"] == "running":
                it["status"] = "error"
                it["error"] = "服务重启，任务中断"
                changed = True
        if changed:
            _batch_store(state)
    done = sum(1 for it in state["items"] if it["status"] == "done")
    err = sum(1 for it in state["items"] if it["status"] == "error")
    return {
        "running": bool(state.get("running")),
        "name": state.get("name", ""),
        "current": state.get("current"),
        "total": len(state["items"]),
        "done": done, "error": err,
        "pending": sum(1 for it in state["items"] if it["status"] == "pending"),
        "items": state["items"],
    }


@router.post("/batch/stop")
def batch_stop():
    """停止批量队列：当前正在跑的一首会算完，后续 pending 全部取消。"""
    state = _batch_snapshot()
    if not state.get("running"):
        return {"ok": True, "message": "队列未在运行"}
    state["running"] = False
    for it in state["items"]:
        if it["status"] == "pending":
            it["status"] = "cancelled"
    _batch_store(state)
    return {"ok": True, "message": "已停止队列（当前歌曲会算完）"}


@router.post("/batch/resume")
def batch_resume():
    """一键继续：把意外终止遗留的卡死条目重新入队并拉起 worker。

    应用崩溃/被杀时，正在跑的条目会永远停在 running（重启后 worker 只取
    pending，不会碰它）；stop 停掉的 pending 则变成 cancelled。这里把
    running 与 cancelled 一并复位为 pending，从头重跑这些条目（已完成的
    done 条目不动），然后确保 worker 线程在跑。
    """
    state = _batch_snapshot()
    if state.get("running"):
        raise HTTPException(status_code=409, detail="队列正在运行，无需继续")
    restored = 0
    for it in state["items"]:
        if it["status"] in ("running", "cancelled", "error"):
            it["status"] = "pending"
            it.pop("current", None)
            restored += 1
    # 有 pending（含本就等待中的假死队列）就拉起 worker——
    # 不能因 restored==0 提前返回，否则"等待中却没在跑"的队列永远无法手动启动
    has_pending = any(it["status"] == "pending" for it in state["items"])
    if not has_pending:
        return {"ok": True, "message": "没有可继续的任务（队列全部完成或为空）", "restored": 0}
    state["running"] = True
    state["current"] = None
    _batch_store(state)
    _CANCEL_EVENT.clear()
    _batch_ensure_worker()
    msg = f"已恢复 {restored} 个未完成任务，继续执行" if restored else "队列已在等待中，继续执行"
    return {"ok": True, "message": msg, "restored": restored}


@router.post("/batch/retry")
def batch_retry(payload: dict):
    """单条重试：把一个 error/cancelled 条目复位为 pending 并拉起 worker。"""
    rid = os.path.basename(str(payload.get("id") or ""))
    if not rid:
        raise HTTPException(status_code=400, detail="缺少任务 id")
    with _BATCH_LOCK:
        state = _batch_read()
        if state.get("running"):
            raise HTTPException(status_code=409, detail="队列正在运行，稍后再试")
        it = next((x for x in state["items"] if x["id"] == rid), None)
        if it is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        if it["status"] not in ("error", "cancelled", "running"):
            raise HTTPException(status_code=409, detail="仅失败/已取消/卡死的任务可重试")
        it["status"] = "pending"
        it.pop("error", None)
        state["running"] = True
        state["current"] = None
        _batch_write(state)
    _CANCEL_EVENT.clear()
    _batch_ensure_worker()
    return {"ok": True, "message": "已重新入队"}


@router.delete("/batch/{rid}")
def batch_delete(rid: str):
    rid = os.path.basename(rid)
    state = _batch_snapshot()
    if rid == state.get("current") and state.get("running"):
        raise HTTPException(status_code=409, detail="该任务正在生成，不能删除")
    state["items"] = [it for it in state["items"] if it["id"] != rid]
    _batch_store(state)
    for p in (_output_wav_path(rid), _output_meta_path(rid)):
        if p.is_file():
            p.unlink()
    return {"ok": True}


# --------------------------------------------------------------------------- #
# RVC 换声（音色转换）：进程调用 rvc/infer/cli.py，不污染网关进程
# --------------------------------------------------------------------------- #
RVC_DIR = ROOT / "runtime" / "rvc"
RVC_PY = ROOT / "py312" / "python.exe"
RVC_MODELS_DIR = RVC_DIR / "assets" / "weights"
RVC_JOB_DIR = RVC_DIR / "jobs"
_RVC_LOCK = threading.Lock()
_RVC_JOBS: dict[str, dict] = {}  # rid -> {id,status,error,sec,model,...}


def _rvc_models() -> list[str]:
    if not RVC_MODELS_DIR.is_dir():
        return []
    return sorted(p.name for p in RVC_MODELS_DIR.glob("*.pth"))


def _rvc_model_in_use(model: str) -> bool:
    """该音色是否正被某个运行中的换声任务使用。"""
    with _RVC_LOCK:
        return any(
            j.get("status") == "running" and j.get("model") == model
            for j in _RVC_JOBS.values()
        )


@router.get("/rvc/models")
def rvc_models():
    items = []
    if RVC_MODELS_DIR.is_dir():
        for p in sorted(RVC_MODELS_DIR.glob("*.pth")):
            items.append({
                "name": p.name,
                "size_mb": round(p.stat().st_size / 1e6, 1),
                "mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            })
    return {"models": [i["name"] for i in items], "items": items}


_RVC_CHECK_LOCK = threading.Lock()  # 体检串行化：大模型加载费内存，防连点叠加


@router.get("/rvc/models/{name}/check")
def rvc_model_check(name: str):
    """模型体检：加载 pth 校验结构完整性、采样率/f0/版本，并检查配套 index 是否存在。"""
    name = os.path.basename(name)
    p = RVC_MODELS_DIR / name
    if not p.is_file():
        raise HTTPException(status_code=404, detail="音色模型不存在")
    checks, info = {}, {}
    # 用训练环境子进程读元信息（app.py 不加载 torch，避免常驻显存/内存）。
    # 单脚本一次 load 输出全部指标；参数走 sys.argv 传路径，杜绝字符串拼接注入。
    script = (
        "import sys, json, torch\n"
        "c = torch.load(sys.argv[1], map_location='cpu')\n"
        "m = c.get('model', c)\n"
        "w = m.get('weight', m) if isinstance(m, dict) else m\n"
        "ks = set(w.keys()) if hasattr(w, 'keys') else set()\n"
        "print(json.dumps({'sr': str(c.get('sr','?')), 'f0': bool(c.get('f0', True)), "
        "'version': str(c.get('version','?')), 'epoch': str(c.get('info','?')), "
        "'struct': bool(any('enc_p' in k for k in ks) and any('dec' in k for k in ks)), "
        "'n': len(ks)}))"
    )
    if not _RVC_CHECK_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="上一次体检还在进行中，请稍候")
    try:
        r = subprocess.run([str(RVC_PY), "-c", script, str(p)], capture_output=True, timeout=300,
                           cwd=str(RVC_DIR),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        _RVC_CHECK_LOCK.release()
    if r.returncode != 0:
        raise HTTPException(status_code=422, detail="模型无法加载（可能损坏）：" + r.stderr.decode("utf-8", "ignore")[-200:])
    try:
        j = json.loads(r.stdout.decode("utf-8", "ignore").strip().splitlines()[-1])
    except Exception:
        j = {}
    info = {k: j.get(k, "?") for k in ("sr", "f0", "version", "epoch")}
    checks["结构完整"] = bool(j.get("struct"))
    checks["权重非空"] = int(j.get("n", 0)) > 50
    idx = list((RVC_DIR / "logs").glob(f"added_*_{p.stem}_v2.index"))
    checks["配套索引"] = bool(idx)
    ok = all(checks.values())
    return {
        "name": name, "ok": ok, "checks": checks, "info": info,
        "index": idx[0].name if idx else None,
        "size_mb": round(p.stat().st_size / 1e6, 1),
        "summary": "体检通过" if ok else "存在问题：" + "、".join(k for k, v in checks.items() if not v),
    }


@router.post("/rvc/models/merge")
def rvc_model_merge(payload: dict):
    """模型融合：两个成品 pth 按比例插值出新音色。

    不直接调官方 process_ckpt.merge——它按训练检查点格式取权重（ckpt["model"]）、
    以相对路径落盘、f0 走 i18n 字符串比较，三处都与成品 pth 不匹配；此处内联等价
    插值：兼容成品/检查点两种格式、绝对路径落盘、f0 直接写 int。"""
    a = os.path.basename(str(payload.get("a") or ""))
    b = os.path.basename(str(payload.get("b") or ""))
    try:
        alpha = min(1.0, max(0.0, float(payload.get("alpha", 0.5))))  # a 的权重占比
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="比例须为 0-1 的数字")
    new_name = re.sub(r'[\\/:*?"<>|\s]+', "_", str(payload.get("name") or "").strip())[:40]
    if not a or not b or not new_name:
        raise HTTPException(status_code=400, detail="需要 a、b 两个音色名和新名称")
    pa, pb = RVC_MODELS_DIR / a, RVC_MODELS_DIR / b
    for p, tag in ((pa, a), (pb, b)):
        if not p.is_file():
            raise HTTPException(status_code=404, detail=f"音色不存在：{tag}")
    # 读 A/B 的元信息校验同源（同版本同采样率才能融合）；argv 传参，主进程不依赖 torch
    meta_script = (
        "import sys, json, torch\n"
        "c = torch.load(sys.argv[1], map_location='cpu')\n"
        "print(json.dumps({'sr': str(c.get('sr','')), 'f0': bool(c.get('f0', True)), 'version': str(c.get('version',''))}))"
    )
    def _meta(p: Path) -> dict:
        rr = subprocess.run([str(RVC_PY), "-c", meta_script, str(p)], capture_output=True, timeout=300,
                            cwd=str(RVC_DIR),
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if rr.returncode != 0:
            raise HTTPException(status_code=422, detail=f"模型无法加载：{p.name}（{rr.stderr.decode('utf-8', 'ignore')[-150:]}）")
        try:
            return json.loads(rr.stdout.decode("utf-8", "ignore").strip().splitlines()[-1])
        except Exception:
            raise HTTPException(status_code=422, detail=f"模型元信息解析失败：{p.name}")
    ma, mb = _meta(pa), _meta(pb)
    if mb["sr"] != ma["sr"] or mb["version"] != ma["version"]:
        raise HTTPException(status_code=400, detail="两个音色的采样率或版本不同，无法融合")
    out_name = new_name if new_name.lower().endswith(".pth") else new_name + ".pth"
    out = RVC_MODELS_DIR / out_name
    if out.exists():
        raise HTTPException(status_code=409, detail="同名音色已存在，请换个名字")
    # 插值本体：官方 process_ckpt.merge 按训练检查点格式（ckpt["model"]）取权重、相对路径
    # 落盘、f0 走 i18n 字符串比较，三处都与成品 pth 不匹配——故内联等价插值：
    # 兼容成品（{"weight",...}）与检查点（{"model",...}）两种来源，绝对路径落盘，f0 写 int。
    # 全部参数走 sys.argv，杜绝字符串拼接注入（裁定 B-1/N-3）。
    script = (
        "import sys, json, torch\n"
        "from collections import OrderedDict\n"
        "p1, p2 = sys.argv[1], sys.argv[2]\n"
        "alpha, out_path, info = float(sys.argv[3]), sys.argv[4], sys.argv[5]\n"
        "def load_w(p):\n"
        "    c = torch.load(p, map_location='cpu')\n"
        "    m = c.get('model', c)\n"
        "    w = m.get('weight', m) if isinstance(m, dict) else m\n"
        "    return c, {k: v for k, v in w.items() if 'enc_q' not in k}\n"
        "c1, w1 = load_w(p1)\n"
        "c2, w2 = load_w(p2)\n"
        "opt = OrderedDict()\n"
        "opt['weight'] = {}\n"
        "for k in w1.keys():\n"
        "    if k not in w2:\n"
        "        continue\n"
        "    va, vb = w1[k], w2[k]\n"
        "    if hasattr(va, 'float'):\n"
        "        va, vb = va.float(), vb.float()\n"
        "        opt['weight'][k] = (alpha * va + (1 - alpha) * vb).half()\n"
        "    else:\n"
        "        opt['weight'][k] = va\n"
        "opt['config'] = c1.get('config')\n"
        "opt['info'] = info\n"
        "opt['version'] = str(c1.get('version', ''))\n"
        "opt['sr'] = str(c1.get('sr', ''))\n"
        "opt['f0'] = int(c1.get('f0', 1))\n"
        "torch.save(opt, out_path)\n"
        "print(json.dumps({'ok': True, 'n': len(opt['weight'])}))"
    )
    r = subprocess.run([str(RVC_PY), "-c", script, str(pa), str(pb), str(alpha), str(out),
                        f"融合 {a} x {b}"], capture_output=True, timeout=600,
                       cwd=str(RVC_DIR),
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0 or not out.is_file():
        raise HTTPException(status_code=500, detail="融合失败：" + r.stderr.decode("utf-8", "ignore")[-300:])
    return {"ok": True, "name": out_name}


@router.delete("/rvc/models/{name}")
def rvc_model_delete(name: str):
    name = os.path.basename(name)
    p = RVC_MODELS_DIR / name
    if not p.is_file():
        raise HTTPException(status_code=404, detail="音色模型不存在")
    if _rvc_model_in_use(name):
        raise HTTPException(status_code=409, detail="该音色正在被换声任务使用，不能删除")
    p.unlink()
    # 顺带清理同名索引文件
    for idx in (RVC_DIR / "logs").glob(f"added_*_{p.stem}_v2.index"):
        try:
            idx.unlink()
        except Exception:
            pass
    return {"ok": True}


@router.post("/rvc/models/{name}/rename")
def rvc_model_rename(name: str, payload: dict):
    name = os.path.basename(name)
    new = re.sub(r'[\\/:*?"<>|\s]+', "_", str(payload.get("name") or "").strip())[:40]
    if not new:
        raise HTTPException(status_code=400, detail="新名称不能为空")
    new_name = new if new.lower().endswith(".pth") else new + ".pth"
    p = RVC_MODELS_DIR / name
    if not p.is_file():
        raise HTTPException(status_code=404, detail="音色模型不存在")
    if new_name == name:
        return {"ok": True, "name": name}
    if (RVC_MODELS_DIR / new_name).is_file():
        raise HTTPException(status_code=409, detail=f"已存在同名音色：{new_name}")
    if _rvc_model_in_use(name):
        raise HTTPException(status_code=409, detail="该音色正在被换声任务使用，不能重命名")
    p.rename(RVC_MODELS_DIR / new_name)
    # 同步重命名索引文件（CLI 按 weights 里的模型 stem 找 added_*_<stem>_v2.index）
    for idx in (RVC_DIR / "logs").glob(f"added_*_{p.stem}_v2.index"):
        try:
            idx.rename(idx.with_name(idx.name.replace(f"_{p.stem}_", f"_{Path(new_name).stem}_")))
        except Exception:
            pass
    return {"ok": True, "name": new_name}


# 人声分离：官方 RVC23 的 PyMSS 框架（BS-Roformer-Resurrection，onnxruntime 加速，
# 实测 3.5 分钟歌约 1.5 分钟，比 GPT-SoVITS 的 UVR5 两步链快且人声更干净）。
# 产物命名与旧链一致（<stem>_vocals.wav / <stem>_other.wav），下游混音/下载逻辑不变。
PYMSS_ROOT = RVC_DIR / "tools"
PYMSS_MODEL = "BS-Roformer-Resurrection"
# 旧链路（GPT-SoVITS 的 BS-RoFormer + HP5）保留作降级
GSV_ROOT = Path(r"E:\AI\10AIMusic\GPT-SoVITS-v2pro-20250604")
GSV_PY = GSV_ROOT / "runtime" / "python.exe"
SEP_ROFORMER = GSV_ROOT / "sep_roformer.py"
SEP_HP5 = GSV_ROOT / "sep_hp5.py"


def vocal_sep_available() -> bool:
    return (RVC_DIR / "tools" / "pymss" / "workflow.py").is_file()


def _pymss_env() -> dict:
    env = {
        **os.environ,
        "PYTHONPATH": str(PYMSS_ROOT),
        "HF_ENDPOINT": os.environ.get("HF_ENDPOINT", "https://hf-mirror.com"),
        "KMP_DUPLICATE_LIB_OK": "TRUE",
    }
    # 分离引擎（PyTorch/ONNX）会开满全部核的线程把 CPU 吃满，本机浏览器被饿到
    # 整页假死（灰底空白）。限制线程数到约 3/4，给系统与界面留出响应能力。
    cores = os.cpu_count() or 8
    keep = str(max(1, cores - max(1, cores // 4)))
    env.setdefault("OMP_NUM_THREADS", keep)
    env.setdefault("MKL_NUM_THREADS", keep)
    return env


def _pymss_creationflags() -> int:
    """PyMSS 分离子进程降 CPU 优先级（低于普通程序），浏览器/界面优先拿到算力。"""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    flags |= getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x4000)
    return flags


def _commit_headroom_mb() -> int:
    """当前提交内存余量（MB）。Windows 提交超 Commit Limit 会整系统 OOM。"""
    try:
        import ctypes
        from ctypes import wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                        ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                        ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                        ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                        ("ullAvailExtendedVirtual", ctypes.c_uint64)]

        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        # ullAvailPageFile ≈ Commit Limit - Commit Charge
        return int(st.ullAvailPageFile // (1024 * 1024))
    except Exception:
        return 1 << 30  # 查询失败时不拦截


def _require_headroom_for_preview(need_mb: int = 2600) -> None:
    """试听/转换的前置内存闸门（df77 OOM 教训：试听子进程加载 800MB 模型撞上
    训练进程，提交内存耗尽把训练挤死——试听必须自证有余量才准跑）。"""
    avail = _commit_headroom_mb()
    if avail < need_mb:
        raise HTTPException(
            status_code=503,
            detail=f"内存余量不足（剩 {avail}MB，试听需约 {need_mb}MB）——训练正在吃内存，"
                   f"现在试听会把训练挤死。等训练完成后再试，或重启网关后试。")


def _run_vocal_separation(src: Path, in_dir: Path, job: dict) -> tuple[Path, dict]:
    """人声分离：首选官方 PyMSS 一步分离（人声+伴奏）；PyMSS 不可用时降级旧两步链。

    返回 (送 RVC 的人声路径, 产物字典)；产物字典含原人声与伴奏，
    供换声完成后合成完整歌曲与多产物下载。"""
    sep_dir = in_dir / "sep"
    sep_dir.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, str] = {}

    def _newest(pattern: str) -> Path | None:
        cands = sorted(sep_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        return cands[0] if cands else None

    if vocal_sep_available():
        # PyMSS 一步分离：产物 <stem>_vocals.wav（含和声）+ <stem>_other.wav（伴奏）
        job["sep_stage"] = "人声分离（PyMSS BS-Roformer-Resurrection，约 1.5 分钟/3.5 分钟歌）"
        r = subprocess.run(
            [str(RVC_PY), "-m", "tools.pymss.cli", "infer", PYMSS_MODEL,
             "-i", str(src), "-o", str(sep_dir), "--device", "cuda"],
            capture_output=True, timeout=3600,
            creationflags=_pymss_creationflags(),
            cwd=str(RVC_DIR), env=_pymss_env(),
        )
        if r.returncode == 0:
            vocals = _newest(f"{src.stem}_vocals.wav")
            accompaniment = _newest(f"{src.stem}_other.wav")
            if vocals is not None and accompaniment is not None:
                artifacts["vocals_raw"] = vocals.name
                artifacts["accompaniment"] = accompaniment.name
                # 人声即送 RVC（不再需要 HP5 二次去和声；PyMSS 一步已足够干净）
                return vocals, artifacts
            tail = (r.stderr or r.stdout or b"")[-300:].decode("utf-8", "replace")
            raise RuntimeError(f"人声分离（PyMSS）未产出完整产物：{tail}")
        tail = (r.stderr or r.stdout or b"")[-300:].decode("utf-8", "replace")
        # PyMSS 失败（模型未下载/断网等）→ 降级旧两步链
        job["sep_stage"] = "PyMSS 失败，降级旧分离链（BS-RoFormer + HP5）"
        if not (GSV_PY.is_file() and SEP_ROFORMER.is_file() and SEP_HP5.is_file()):
            raise RuntimeError(f"人声分离（PyMSS）失败且旧链不可用：{tail}")
        _pymss_err = tail
    else:
        _pymss_err = "PyMSS 模块缺失"
    if True:  # 旧两步链（降级路径）
        job["sep_stage"] = "分离伴奏（BS-RoFormer，较慢）"
        r1 = subprocess.run(
            [str(GSV_PY), str(SEP_ROFORMER), str(src), str(sep_dir)],
            capture_output=True, timeout=3600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    if r1.returncode != 0:
        tail = (r1.stderr or r1.stdout or b"")[-300:].decode("utf-8", "replace")
        raise RuntimeError(f"人声分离（伴奏分离）失败：{tail}")
    # 排除伴奏产物（other/instrument 字样），取人声轨道
    vocals = next((v for v in sorted(sep_dir.glob(f"{src.stem}_*.wav"),
                                     key=lambda p: p.stat().st_mtime, reverse=True)
                   if not any(k in v.stem.lower() for k in ("other", "instrument"))), None)
    if not vocals:
        raise RuntimeError("人声分离（伴奏分离）未找到人声轨道")
    # 伴奏：BS-RoFormer 的 <输入stem>_other.wav（注意：不含 vocals 后缀，与产物同名规则一致）。
    # 注意 HP5 的 instrument_*.wav 是去和声副产品（无和声时≈静音），不能当伴奏。
    accompaniment = sep_dir / f"{src.stem}_other.wav"
    if not accompaniment.is_file():
        accompaniment = next((v for v in sorted(sep_dir.glob("*_other.wav"))
                              if not v.name.startswith(("vocal_", "instrument_"))), None)
    if accompaniment is None or not accompaniment.is_file():
        raise RuntimeError("人声分离（伴奏分离）未找到伴奏轨道")
    artifacts["vocals_raw"] = vocals.name      # 原人声（含和声，BS-RoFormer）
    artifacts["accompaniment"] = accompaniment.name  # 伴奏（BS-RoFormer other 声部）

    # 步骤 2：HP5 去和声（只留主唱）
    job["sep_stage"] = "去除和声（HP5）"
    r2 = subprocess.run(
        [str(GSV_PY), str(SEP_HP5), str(vocals), str(sep_dir)],
        capture_output=True, timeout=3600,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if r2.returncode != 0:
        tail = (r2.stderr or r2.stdout or b"")[-300:].decode("utf-8", "replace")
        raise RuntimeError(f"人声分离（去和声）失败：{tail}")
    main_vocal = next((v for v in sorted(sep_dir.glob("vocal_*.wav"),
                                         key=lambda p: p.stat().st_mtime, reverse=True)), None)
    if not main_vocal:
        # HP5 失败时降级：用含和声的人声继续（比带伴奏好）
        return vocals, artifacts
    return main_vocal, artifacts


def _rvc_convert_worker(rid: str, job: dict, src: Path, in_dir: Path,
                        model: str, pitch: int, f0_method: str,
                        index_rate: float, protect: float, rms_mix_rate: float,
                        separate_vocal: bool = False) -> None:
    """换声推理 worker（上传入口与历史转发入口共用）。"""
    started = time.time()
    with _RVC_LOCK:
        _RVC_JOBS[rid] = {**_RVC_JOBS.get(rid, job), "status": "running"}
    try:
        artifacts: dict[str, str] = {}
        orig_src = in_dir / next(p.name for p in in_dir.iterdir() if p.name.startswith("src"))
        if separate_vocal:
            src, artifacts = _run_vocal_separation(src, in_dir, job)
        out_name = "converted.wav"
        cmd = [
            str(RVC_PY), str(RVC_DIR / "infer" / "cli.py"),
            "--model", model,
            "--input", str(src), "--output", str(in_dir / out_name),
            "--pitch", str(int(pitch)), "--f0-method", f0_method,
            "--index-rate", str(index_rate), "--protect", str(protect),
            "--rms-mix-rate", str(rms_mix_rate),
            # 输出重采样到 48k：源素材普遍 44.1/48k，40k 直出会损失高频
            "--resample-sr", "48000",
            "--overwrite",
        ]
        env = {**os.environ,
               "PYTHONPATH": str(RVC_DIR),
               "weight_root": str(RVC_MODELS_DIR),
               "index_root": str(RVC_DIR / "logs"),
               "rmvpe_root": str(RVC_DIR / "assets" / "rmvpe"),
               "outside_index_root": str(RVC_DIR / "assets" / "indices"),
               "OPENBLAS_NUM_THREADS": "1",
               # 与常驻 CUDA 的 audiocpp 引擎共存时，CUDA Graph 捕获会在
               # torch.cuda.synchronize 处死锁（实测 GTX 1660S + 双 CUDA 进程）。
               # 关闭图加速走 eager 推理，功能不变：295s 音频全程约 19s。
               "RVC_CUDA_GRAPH": "0"}
        with _GPU_SEM:  # 与生成/批量/训练互斥，防止并发打满显存
            proc = subprocess.run(
                cmd, cwd=str(RVC_DIR), env=env, capture_output=True, timeout=1800,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        out_path = in_dir / out_name
        if proc.returncode != 0 or not out_path.is_file():
            tail = (proc.stderr or proc.stdout or b"")[-500:].decode("utf-8", "replace")
            raise RuntimeError(f"RVC 推理失败：{tail}")
        # 落盘到 output/（历史页可回放），meta 记录全部参数
        wav = _output_wav_path(rid)
        wav.write_bytes(out_path.read_bytes())
        meta = {
            **job, "status": "done", "sec": round(time.time() - started, 1),
            "bytes": wav.stat().st_size, "file": wav.name, "kind": "rvc",
            "style": f"RVC 换声 · {model}", "lyrics": f"源音频：{job['src_name']}",
            "cot": "rvc", "params": {"pitch": pitch, "f0_method": f0_method,
                                      "index_rate": index_rate, "protect": protect,
                                      "rms_mix_rate": rms_mix_rate},
        }
        # 多产物：分离开启时，把原人声/伴奏拷进 output/，并把换声人声与伴奏混音成完整歌曲
        assets: dict[str, str] = {}
        if separate_vocal:
            try:
                import soundfile as sf
                import numpy as np
                sep_dir = in_dir / "sep"

                def _copy(src_p: Path, dst_name: str) -> None:
                    dst = OUTPUT_DIR / f"{rid}_{dst_name}.wav"
                    dst.write_bytes(src_p.read_bytes())
                    assets[dst_name] = dst.name

                # 原人声（含和声）
                raw_v_name = artifacts.get("vocals_raw", "")
                raw_v = sep_dir / raw_v_name if raw_v_name else None
                if raw_v is not None and raw_v.is_file():
                    _copy(raw_v, "vocals_original")
                # 伴奏：分离函数已精确登记（BS-RoFormer other 声部），直接取用
                acc_name = artifacts.get("accompaniment", "")
                acc = sep_dir / acc_name if acc_name else None
                if acc is not None and not acc.is_file():
                    acc = None
                if acc is not None and acc.is_file():
                    _copy(acc, "accompaniment")
                    # 混音：换声后的人声 + 伴奏 → 完整歌曲（按伴奏采样率对齐）
                    voc, sr_v = sf.read(str(wav), dtype="float32", always_2d=True)
                    accm, sr_a = sf.read(str(acc), dtype="float32", always_2d=True)
                    if sr_v != sr_a:
                        import librosa
                        voc = librosa.resample(voc.T, orig_sr=sr_v, target_sr=sr_a).T
                        sr_v = sr_a
                    n = max(voc.shape[0], accm.shape[0])
                    if voc.shape[0] < n:
                        voc = np.pad(voc, ((0, n - voc.shape[0]), (0, 0)))
                    if accm.shape[0] < n:
                        accm = np.pad(accm, ((0, n - accm.shape[0]), (0, 0)))
                    if voc.shape[1] != accm.shape[1]:
                        voc = voc[:, :1] if voc.shape[1] == 1 else np.repeat(voc[:, :1], accm.shape[1], axis=1)
                        accm = accm[:, :1] if accm.shape[1] == 1 else np.repeat(accm[:, :1], voc.shape[1], axis=1)
                    # 响度校准：把换声人声的 RMS 对齐到原曲人声，避免合成后忽大忽小
                    try:
                        raw_p = sep_dir / (artifacts.get("vocals_raw") or "")
                        if raw_p.is_file():
                            rawm, _ = sf.read(str(raw_p), dtype="float32", always_2d=True)
                            if sr_v != sr_a:
                                import librosa
                                rawm = librosa.resample(rawm.T, orig_sr=sr_a, target_sr=sr_a).T
                            ref_rms = float(np.sqrt((rawm ** 2).mean()))
                            voc_rms = float(np.sqrt((voc ** 2).mean()))
                            if voc_rms > 1e-6 and ref_rms > 1e-6:
                                gain = min(3.0, max(0.33, ref_rms / voc_rms))
                                voc *= gain
                    except Exception:
                        pass
                    mixed = voc + accm
                    peak = float(np.abs(mixed).max()) / 0.99
                    if peak > 1:
                        mixed /= peak
                    full = OUTPUT_DIR / f"{rid}_full_song.wav"
                    sf.write(str(full), mixed, sr_a)
                    assets["full_song"] = full.name
            except Exception:
                # 多产物失败不影响主结果（换声人声已在），meta 里如实省略 assets
                assets = {}
        if assets:
            meta["assets"] = assets
        _output_write_meta(meta)
        with _RVC_LOCK:
            _RVC_JOBS[rid] = {**_RVC_JOBS[rid], "status": "done",
                              "sec": meta["sec"], "bytes": meta["bytes"]}
        _win_toast("🎵 换声完成：" + model, f"耗时 {meta['sec']} 秒，已保存到 output/")
    except Exception as e:
        with _RVC_LOCK:
            _RVC_JOBS[rid] = {**_RVC_JOBS.get(rid, job),
                              "status": "error", "error": str(e)[:500],
                              "sec": round(time.time() - started, 1)}
        _win_toast("✕ 换声失败：" + model, str(e)[:120])


@router.post("/rvc/convert")
async def rvc_convert(
    file: UploadFile,
    model: str = Form(...),
    task_name: str = Form(""),
    pitch: int = Form(0),
    f0_method: str = Form("rmvpe"),
    index_rate: float = Form(0.75),
    protect: float = Form(0.33),
    rms_mix_rate: float = Form(1.0),
    separate_vocal: str = Form("auto"),
):
    """上传音频 + 选音色模型 → 后台转换 → 结果落盘 output/（与生成结果同处可回放）。

    separate_vocal: auto=默认先人声分离（带伴奏歌曲必需）；off=直接换声（输入已是干声）。"""
    if not RVC_PY.is_file():
        raise HTTPException(status_code=500, detail="rvc python 环境缺失（py312/python.exe）")
    models = _rvc_models()
    if model not in models:
        raise HTTPException(status_code=400, detail=f"未知音色模型：{model}（可用：{models}）")
    ext = Path(file.filename or "in.wav").suffix.lower()
    if ext not in (".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus", ".aac", ".wma"):
        ext = ".wav"
    rid = datetime.now().strftime("%Y%m%d_%H%M%S_") + os.urandom(2).hex()
    in_dir = RVC_JOB_DIR / rid
    in_dir.mkdir(parents=True, exist_ok=True)
    src = in_dir / f"src{ext}"
    await _stream_upload_to(file, src, 200 * 1024 * 1024, "音频")
    # 读取源音频时长，供前端估算换声进度（读失败则按 40k 双声道 wav 粗略折算）
    try:
        import soundfile as sf
        src_duration = round(float(sf.info(str(src)).duration), 1)
    except Exception:
        src_duration = round(src.stat().st_size / 160_000, 1)

    job = {
        "id": rid, "status": "running", "ts": datetime.now().isoformat(timespec="seconds"),
        "task_name": str(task_name or "").strip()[:100],
        "model": model, "pitch": pitch, "f0_method": f0_method,
        "index_rate": index_rate, "protect": protect, "rms_mix_rate": rms_mix_rate,
        "src_name": (file.filename or "")[:120],
        "src_size": src.stat().st_size, "src_duration": src_duration,
    }
    with _RVC_LOCK:
        _RVC_JOBS[rid] = job

    threading.Thread(
        target=_rvc_convert_worker,
        args=(rid, job, src, in_dir, model, pitch, f0_method, index_rate, protect,
              rms_mix_rate, separate_vocal != "off"),
        daemon=True,
    ).start()
    return {"ok": True, "id": rid, "job": job}


@router.get("/rvc/status/{rid}")
def rvc_status(rid: str):
    rid = os.path.basename(rid)
    with _RVC_LOCK:
        job = _RVC_JOBS.get(rid)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或服务已重启")
    return job


@router.get("/rvc/active")
def rvc_active():
    """进行中的换声任务（页面刷新后恢复进度条用；服务重启则列表为空）。"""
    with _RVC_LOCK:
        jobs = [j for j in _RVC_JOBS.values() if j.get("status") == "running"]
    return {"items": sorted(jobs, key=lambda x: x.get("ts", ""), reverse=True)}


@router.get("/rvc/audio/{rid}")
def rvc_audio(rid: str, part: str = ""):
    """下载换声产物。part 为空=换声后的人声（主产物）；
    part=vocals_original/accompaniment/full_song=分离任务的多产物之一。"""
    rid = os.path.basename(rid)
    if part:
        part = os.path.basename(part)
        if not re.fullmatch(r"[a-z_]+", part):
            raise HTTPException(status_code=400, detail="非法产物名")
        meta_path = _output_meta_path(rid)
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            raise HTTPException(status_code=404, detail="任务不存在")
        fname = (meta.get("assets") or {}).get(part)
        if not fname:
            raise HTTPException(status_code=404, detail=f"产物不存在：{part}")
        wav = OUTPUT_DIR / fname
        if not wav.is_file():
            raise HTTPException(status_code=404, detail="产物文件已丢失")
        labels = {"vocals_original": "原人声", "accompaniment": "伴奏", "full_song": "完整歌曲"}
        return FileResponse(str(wav), media_type="audio/wav",
                            filename=f"{rid}_{labels.get(part, part)}.wav")
    wav = _output_wav_path(rid)
    if not wav.is_file():
        raise HTTPException(status_code=404, detail="结果不存在")
    return FileResponse(str(wav), media_type="audio/wav", filename=wav.name)


# --------------------------------------------------------------------------- #
# 训练中试听：用 logs/<name>/ 最新的 G_* 检查点在 CPU 上做 20 秒迷你推理。
# 设计约束：绝不碰 _GPU_SEM、绝不打断训练进程——训练继续占 GPU，试听走 CPU 慢一点
# （约 20-40 秒）但零冲突；产物存 trains/<rid>/preview.wav，前端任务卡片直接播放。
# --------------------------------------------------------------------------- #
_RVC_PREVIEW_LOCK = threading.Lock()  # 同一时刻只跑一个试听（CPU 推理也吃核）


@router.post("/rvc/train/preview/{rid}")
def rvc_train_preview(rid: str):
    rid = os.path.basename(rid)
    job = _rvc_train_read(rid)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.get("status") not in ("running", "pending", "done", "error"):
        raise HTTPException(status_code=409, detail="任务状态异常")
    # 训练运行中试听 = 最危险的内存撞车窗口（df77 OOM 教训），前置闸门自证余量
    _require_headroom_for_preview()
    name = job.get("name", "")
    logs = RVC_DIR / "logs" / name
    ckpts = sorted(logs.glob("G_*.pth"), key=lambda p: p.stat().st_mtime)
    # 训练已出成品的，优先用成品（weights 下的 pth 才有完整推理结构）
    final = _rvc_latest_export(name)
    if final is not None and final.stat().st_mtime >= (ckpts[-1].stat().st_mtime if ckpts else 0):
        model_path, tag = final, "成品"
    elif ckpts:
        model_path = ckpts[-1]
        step = model_path.stem.split("_")[-1]
        tag = f"检查点 #{step}（step，非轮次）"
        # 关键：G_*.pth 是训练检查点（{"model",...}），缺 weight/config 键，直接喂
        # infer/cli.py 必抛 ValueError——先过官方 extract_small_model 转成推理可用结构。
        # 产物缓存到任务目录，同一检查点只转换一次；转换走 CPU，不碰训练的 GPU。
        conv_out = RVC_TRAIN_DIR / rid / "preview_model.pth"
        if not conv_out.is_file() or conv_out.stat().st_mtime < model_path.stat().st_mtime:
            conv_script = (
                "import sys\n"
                "sys.path.insert(0, '.')\n"
                "from train.process_ckpt import extract_small_model\n"
                "extract_small_model(sys.argv[1], sys.argv[2], '40k', True,\n"
                "                    'preview extract', 'v2')\n"
            )
            wroot = RVC_MODELS_DIR
            conv = subprocess.run(
                [str(RVC_PY), "-c", conv_script, str(model_path), conv_out.stem],
                capture_output=True, timeout=600, cwd=str(RVC_DIR),
                env={**os.environ, "weight_root": str(wroot),
                     "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
                     "CUDA_VISIBLE_DEVICES": ""},
                creationflags=_pymss_creationflags())
            produced = wroot / (conv_out.stem + ".pth")
            if conv.returncode != 0 or not produced.is_file():
                tail = (conv.stderr or b"")[-200:].decode("utf-8", "replace")
                raise HTTPException(status_code=500, detail="检查点转换失败：" + tail)
            shutil.move(str(produced), str(conv_out))
        model_path, tag = conv_out, f"第 {step} step 检查点（已转换）"
    else:
        raise HTTPException(status_code=404, detail="还没有可试听的检查点（训练尚未产出 G_* 文件），请稍后再试")
    # 找一段素材切片做源音频（dataset_clean 优先，回退 dataset）
    src_dir = RVC_TRAIN_DIR / rid / "dataset_clean"
    if not src_dir.is_dir() or not any(src_dir.iterdir()):
        src_dir = RVC_TRAIN_DIR / rid / "dataset"
    wavs = sorted(p for p in src_dir.iterdir() if p.is_file()) if src_dir.is_dir() else []
    if not wavs:
        raise HTTPException(status_code=404, detail="找不到源素材切片，无法试听")
    src = wavs[0]
    out = RVC_TRAIN_DIR / rid / "preview.wav"
    if not _RVC_PREVIEW_LOCK.acquire(blocking=False):  # 原子抢锁，杜绝 TOCTOU
        raise HTTPException(status_code=409, detail="上一次试听还在生成中，请稍候")
    try:
        cmd = [
            str(RVC_PY), str(RVC_DIR / "infer" / "cli.py"),
            "--model", str(model_path),
            "--input", str(src), "--output", str(out),
            "--pitch", "0", "--f0-method", "rmvpe",
            "--index-rate", "0.75", "--protect", "0.33",
            "--rms-mix-rate", "1.0", "--overwrite",
        ]
        env = {**os.environ,
               "PYTHONPATH": str(RVC_DIR),
               "weight_root": str(RVC_MODELS_DIR),
               "index_root": str(RVC_DIR / "logs"),
               "rmvpe_root": str(RVC_DIR / "assets" / "rmvpe"),
               "outside_index_root": str(RVC_DIR / "assets" / "indices"),
               "OPENBLAS_NUM_THREADS": "1",
               # 关键：试听强制走 CPU——训练正占着 GPU，绝不能与其抢显存
               "CUDA_VISIBLE_DEVICES": "",
               "RVC_CUDA_GRAPH": "0"}
        try:
            proc = subprocess.run(cmd, cwd=str(RVC_DIR), env=env, capture_output=True,
                                  timeout=600, creationflags=_pymss_creationflags())
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="试听生成超时（CPU 推理较慢），请稍后重试")
        if proc.returncode != 0 or not out.is_file():
            tail = (proc.stderr or proc.stdout or b"")[-300:].decode("utf-8", "replace")
            raise HTTPException(status_code=500, detail="试听生成失败：" + tail)
    finally:
        _RVC_PREVIEW_LOCK.release()
    return {"ok": True, "url": f"/api/rvc/train/preview/{rid}/audio", "source": tag,
            "model": model_path.name}


@router.get("/rvc/train/preview/{rid}/audio")
def rvc_train_preview_audio(rid: str):
    p = RVC_TRAIN_DIR / os.path.basename(rid) / "preview.wav"
    if not p.is_file():
        raise HTTPException(status_code=404, detail="试听文件不存在，请先生成")
    return FileResponse(str(p), media_type="audio/wav", filename="preview.wav")


# --------------------------------------------------------------------------- #
# 音色制作（RVC 训练流水线）：上传样本 → 预处理 → F0/特征 → 训练 → 索引 → 导出
# --------------------------------------------------------------------------- #
RVC_TRAIN_DIR = RVC_DIR / "trains"
RVC_TRAIN_LOCK = threading.Lock()
RVC_TRAIN_JOBS: dict[str, dict] = {}
_RVC_TRAIN_WORKER: threading.Thread | None = None
# 当前正在执行的训练/流水线子进程句柄（pause 端点 terminate 它实现优雅停训）
_RVC_TRAIN_PROC: subprocess.Popen | None = None
_RVC_TRAIN_PAUSE_REQ: bool = False  # 置位表示已请求暂停，子进程退出不再当错误处理


class _RvcTrainPaused(Exception):
    """内部信号：训练被用户主动暂停（pause 端点 terminate 子进程触发）。"""


def _rvc_train_read(rid: str) -> dict:
    try:
        return json.loads((RVC_TRAIN_DIR / rid / "job.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


_EPOCH_RE = re.compile(r"====>\s*轮次：(\d+)")


def _rvc_train_epoch(name: str, fallback_total: int = 0) -> tuple[int, int]:
    """从训练日志解析当前轮次。返回 (当前轮, 总轮数)；读不到返回 (0, 0)。
    总轮数优先用任务提交时的 epochs（logs config 里是底模采样配置，不可用）。"""
    log = RVC_DIR / "logs" / name / "train.log"
    try:
        txt = log.read_text(encoding="utf-8", errors="replace")
        ms = _EPOCH_RE.findall(txt)
        if not ms:
            return 0, 0
        return int(ms[-1]), int(fallback_total or 0)
    except Exception:
        return 0, 0


_EPOCH_DONE_RE = re.compile(
    r"====>\s*轮次：(\d+)\s*\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
)


def _rvc_train_per_epoch_sec(name: str) -> float | None:
    """从训练日志稳健估算"每轮耗时"（秒），供前端算剩余时间。

    日志里每个完成轮都有带时间戳的 "====> 轮次：N [YYYY-MM-DD HH:MM:SS]" 行，
    相邻轮时间差即该轮真实耗时。直接取"最近一次差值"会被偶发卡顿（GC/显存挤占/
    索引导出）污染——实测一轮可达 1055~1311s 而正常仅 ~110s。
    因此：收集全部相邻轮差值，剔除 >3×中位数的异常轮后取均值；
    数据不足（<3 轮）时退回中位数。读不到返回 None（前端隐藏剩余时间）。
    """
    log = RVC_DIR / "logs" / name / "train.log"
    try:
        txt = log.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    pts = []
    for m in _EPOCH_DONE_RE.finditer(txt):
        try:
            t = datetime.strptime(m.group(2), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        pts.append((int(m.group(1)), t))
    pts.sort()
    durs = []
    for (e0, t0), (e1, t1) in zip(pts, pts[1:]):
        if e1 > e0:
            durs.append((t1 - t0).total_seconds() / (e1 - e0))
    if not durs:
        return None
    med = sorted(durs)[len(durs) // 2]
    keep = [d for d in durs if d <= 3 * med] or durs
    return round(sum(keep) / len(keep), 1)


def _rvc_train_write(rid: str, job: dict) -> None:
    d = RVC_TRAIN_DIR / rid
    d.mkdir(parents=True, exist_ok=True)
    # 原子写（裁定 C-4）：先写临时文件再 os.replace，避免读到半截 JSON
    tmp = d / "job.json.tmp"
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, d / "job.json")


def _rvc_run_step(cmd: list[str], job: dict, step: str) -> None:
    """执行一个训练流水线步骤；失败抛异常，日志写进 job.log_tail。"""
    # 关键：这两个是模块级变量，函数内有赋值必须声明 global，
    # 否则 Python 按局部变量处理——暂停标志永不生效、句柄永不更新（已踩坑）
    global _RVC_TRAIN_PROC, _RVC_TRAIN_PAUSE_REQ
    job["step"] = step
    job["status"] = "running"
    _rvc_train_write(job["id"], job)
    env = {**os.environ,
           "PYTHONPATH": str(RVC_DIR),
           "weight_root": str(RVC_MODELS_DIR),
           "index_root": str(RVC_DIR / "logs"),
           "rmvpe_root": str(RVC_DIR / "assets" / "rmvpe"),
           "outside_index_root": str(RVC_DIR / "assets" / "indices"),
           "OPENBLAS_NUM_THREADS": "1",
           # 同上：CUDA Graph 与常驻 audiocpp 引擎冲突会死锁，训练进程一并关闭
           "RVC_CUDA_GRAPH": "0"}
    # 超时按训练规模动态计算：每轮约 2.5-4 分钟（素材量相关），留 1 小时启动/落盘余量。
    # 固定 6 小时曾把大素材长训练（1358 切片 × 200 轮 ≈ 8 小时）在健康跑到一半时误杀。
    est_sec = int(job.get("epochs") or 200) * 240 + 3600
    with RVC_TRAIN_LOCK:
        _RVC_TRAIN_PROC = subprocess.Popen(
            [cmd[0], "-P", *cmd[1:]], cwd=str(RVC_DIR), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            # 训练长跑数小时：降 CPU 优先级保浏览器/界面响应（PyMSS 同款，灰屏教训）
            creationflags=_pymss_creationflags(),
        )
        _proc = _RVC_TRAIN_PROC
        # 暂停请求可能在两步之间到达（此刻无子进程可杀，标志仍置位）：
        # 立即终止刚拉起的进程，避免"暂停被吞、训练继续跑"的竞态窗口
        _pause_pending = _RVC_TRAIN_PAUSE_REQ
        if _pause_pending:
            try:
                _proc.terminate()
            except Exception:
                pass
    try:
        proc_out, proc_err = _proc.communicate(timeout=est_sec)
    except subprocess.TimeoutExpired:
        _proc.kill()
        proc_out, proc_err = _proc.communicate()
        raise RuntimeError(f"步骤 {step} 超时（超过 {est_sec // 60} 分钟）：{step} 异常")
    finally:
        with RVC_TRAIN_LOCK:
            _RVC_TRAIN_PROC = None
    tail = ((proc_err or b"") + (proc_out or b""))[-600:].decode("utf-8", "replace")
    job["log_tail"] = tail[-400:]
    _rvc_train_write(job["id"], job)
    if _RVC_TRAIN_PAUSE_REQ or _pause_pending:
        # 用户主动暂停：子进程被 terminate 退出，属预期，抛专用信号让 worker 走 paused 收尾
        raise _RvcTrainPaused()
    if _proc.returncode != 0:
        raise RuntimeError(f"步骤 {step} 失败（exit {_proc.returncode}）：{tail[-300:]}")


def _rvc_latest_export(name: str) -> Path | None:
    # 精确匹配（裁定 N-2）：glob "voc*" 会误选无关的 vocal_x.pth
    for cand in (f"{name}.pth", f"{name}.pt"):
        p = RVC_MODELS_DIR / cand
        if p.is_file():
            return p
    return None


def _rvc_train_worker(rid: str, name: str, epochs: int,
                      separate_vocal: bool = False, resume: bool = False) -> None:
    job = _rvc_train_read(rid)
    started = time.time()
    exp_logs = RVC_DIR / "logs" / name
    try:
        n_p = max(1, (os.cpu_count() or 4) // 2)
        ds = RVC_TRAIN_DIR / rid / "dataset"
        exp_logs.mkdir(parents=True, exist_ok=True)  # 预处理会往 logs/<name>/ 写日志
        # GPU 全局闸门：整条训练流水线与生成/批量/换声互斥（防 6GB 显存双进程 OOM）
        _GPU_SEM.acquire()
        # 0) 可选：上传的是完整歌曲（人声+伴奏）时，先用官方 PyMSS 一步分离出干净人声，
        #    预处理改用净化目录；单个文件分离失败自动回退用原文件，全部失败才报错。
        train_dir = ds
        if separate_vocal:
            clean = RVC_TRAIN_DIR / rid / "dataset_clean"
            clean.mkdir(parents=True, exist_ok=True)
            wavs = sorted(p for p in ds.iterdir() if p.is_file())
            ok_cnt = 0
            for i, w in enumerate(wavs, 1):
                dst = clean / f"{w.stem}.wav"
                if resume and dst.is_file():
                    ok_cnt += 1  # 续跑：已分离过的直接复用，从断点继续
                    continue
                job["step"] = f"人声分离（PyMSS{'·续跑' if resume else ''}）{i}/{len(wavs)}"
                _rvc_train_write(rid, job)
                try:
                    r = subprocess.run(
                        [str(RVC_PY), "-m", "tools.pymss.cli", "infer", PYMSS_MODEL,
                         "-i", str(w), "-o", str(clean), "--device", "cuda"],
                        capture_output=True, timeout=3600,
                        creationflags=_pymss_creationflags(),
                        cwd=str(RVC_DIR), env=_pymss_env(),
                    )
                    voc = clean / f"{w.stem}_vocals.wav"
                    if r.returncode == 0 and voc.is_file():
                        shutil.move(str(voc), str(dst))
                        (clean / f"{w.stem}_other.wav").unlink(missing_ok=True)  # 伴奏不留
                        ok_cnt += 1
                except Exception:
                    pass  # 单文件失败回退用原文件
            if ok_cnt == 0:
                raise RuntimeError("人声分离全部失败（检查 PyMSS 模型缓存/网络）；若上传的本来就是干声，请取消勾选后重新提交")
            train_dir = clean
            job["samples_separated"] = ok_cnt
            _rvc_train_write(rid, job)
        # 1) 预处理切片（40k、3.7s/片）
        _rvc_run_step([str(RVC_PY), str(RVC_TRAIN_DIR.parent / "train" / "preprocess.py"),
                       str(train_dir), "40000", str(n_p), str(exp_logs), "False", "3.7"], job, "预处理切片")
        # 2) F0 提取（rmvpe, cuda）
        _rvc_run_step([str(RVC_PY), str(RVC_DIR / "train" / "dataset" / "extract_f0.py"),
                       "cuda", "1", "0", "0", str(exp_logs), "False"], job, "F0 提取")
        # 3) Hubert 特征（v2 → 768 维）
        _rvc_run_step([str(RVC_PY), str(RVC_DIR / "train" / "dataset" / "extract_hubert_feature.py"),
                       "cuda", "1", "0", str(exp_logs), "v2", "False"], job, "音色特征提取")
        # 3.5) 生成 filelist.txt + config.json（webui 在启动训练前做同样的事）
        gt_dir = exp_logs / "0_gt_wavs"
        feats_dir = exp_logs / "3_feature768"
        import soundfile as _sf
        pairs = []
        for wav in sorted(gt_dir.glob("*.wav")):
            base = wav.stem
            feat = feats_dir / f"{base}.npy"
            f0 = exp_logs / "2a_f0" / f"{base}.wav.npy"
            f0nsf = exp_logs / "2b-f0nsf" / f"{base}.wav.npy"
            if feat.is_file() and f0.is_file() and f0nsf.is_file():
                # 过滤短于训练段长（12800 采样 ≈ 0.32s）的切片：
                # 恢复训练重建 DataLoader 后采样到它必崩 slice_segments（exit 1）
                try:
                    if _sf.info(wav).frames < 12800:
                        continue
                except Exception:
                    continue  # 读不出的残缺文件同样排除
                pairs.append((wav.resolve().as_posix(), feat.resolve().as_posix(),
                              f0.resolve().as_posix(), f0nsf.resolve().as_posix()))
        if not pairs:
            raise RuntimeError("预处理/特征提取后没有可用样本（检查音频是否为有效人声、ffmpeg 是否正常）")
        mute = (RVC_DIR / "logs" / "mute").resolve()
        # 每行 5 列：wav|feature|f0|f0nsf|speaker_id（data_utils 按 record[:5] 解包）
        lines = ["|".join([*p, "0"]) for p in pairs]
        mute_line = "|".join([
            (mute / "0_gt_wavs" / "mute40k.wav").as_posix(),
            (mute / "3_feature768" / "mute.npy").as_posix(),
            (mute / "2a_f0" / "mute.wav.npy").as_posix(),
            (mute / "2b-f0nsf" / "mute.wav.npy").as_posix(),
            "0",
        ])
        lines += [mute_line, mute_line]  # webui 同样把静音行重复两遍
        (exp_logs / "filelist.txt").write_text("\n".join(lines) + "\n", encoding="utf8")
        cfg = json.loads((RVC_DIR / "configs" / "v1" / "40k.json").read_text(encoding="utf-8"))
        cfg.pop("speaker_info", None)
        (exp_logs / "config.json").write_text(
            json.dumps(cfg, ensure_ascii=False, indent=4), encoding="utf-8"
        )
        job["samples_used"] = len(pairs)
        _rvc_train_write(rid, job)
        # 4) 训练（40k v2 f0，从 pretrained_v2 底模热启；-sw 0 不中途导出，
        #    训练完只导出最终成品一个 pth 进音色库，避免中间权重污染音色列表）
        _rvc_run_step([str(RVC_PY), str(RVC_DIR / "train" / "train.py"),
                       "-e", name, "-sr", "40k", "-f0", "1", "-bs", "4",
                       "-te", str(epochs), "-se", str(max(5, epochs // 4)),
                       "-pg", "assets/pretrained_v2/f0G40k.pth", "-pd", "assets/pretrained_v2/f0D40k.pth",
                       "-l", "1", "-c", "0", "-sw", "0", "-v", "v2"], job, "训练中")
        # 5) 音色索引
        _rvc_run_step([str(RVC_PY), str(RVC_DIR / "train" / "train_index.py"),
                       name, "v2", str(RVC_DIR / "assets" / "indices"), str(n_p)], job, "音色索引")
        # 5.5) 只导出最终成品：从最新检查点 G_2333333.pth 提取推理用小模型
        job["step"] = "导出成品"
        _rvc_train_write(rid, job)
        final_ckpt = exp_logs / "G_2333333.pth"
        if not final_ckpt.is_file():
            raise RuntimeError("训练完成但未找到最终检查点 G_2333333.pth")
        _rvc_run_step([str(RVC_PY), "-c",
                       "import sys, json; sys.path.insert(0, '.'); "
                       "from train.process_ckpt import extract_small_model; "
                       "info = extract_small_model(r'%s', r'%s', '40k', 1, '%d epoch', 'v2'); "
                       "print(info)" % (final_ckpt, name, epochs)], job, "导出成品")
        exported = RVC_MODELS_DIR / f"{name}.pth"
        if not exported.is_file():
            raise RuntimeError("成品导出失败（weights 下未生成 %s.pth）" % name)
        # 6) 清理训练检查点（G_*/D_* 每个 400-800MB，成品已导出即无用），保留索引与日志
        for ck in exp_logs.glob("G_*.pth"):
            ck.unlink(missing_ok=True)
        for ck in exp_logs.glob("D_*.pth"):
            ck.unlink(missing_ok=True)
        idx_files = list((RVC_DIR / "logs" / name).glob("added_*.index"))
        job.update(
            status="done", step="完成",
            model=exported.name,
            index=idx_files[0].name if idx_files else "",
            sec=round(time.time() - started, 1),
        )
        _rvc_train_write(rid, job)
        # 落盘到 output/：历史页可查看（kind=train，无音频，点击可"去使用"）
        _output_write_meta({
            "id": rid, "ts": job.get("ts"), "status": "done", "kind": "train",
            "voice_name": name, "model": exported.name, "index": job.get("index", ""),
            "epochs": epochs, "samples_used": job.get("samples_used"),
            "sec": job.get("sec"), "bytes": 0,
            "style": f"音色制作 · {name}", "lyrics": f"训练 {epochs} 轮 · {job.get('samples_used', '?')} 个样本",
            "cot": "train", "abc": "", "params": {},
        })
        _win_toast("🎵 音色制作完成：" + name, f"模型 {exported.name} 已可使用，耗时 {round((time.time()-started)/60)} 分钟")
    except _RvcTrainPaused:
        # 用户主动暂停：不标 error、不发失败 toast，静默停在 paused 状态（GPU 在 finally 释放）
        job.update(status="paused", step=job.get("step", "训练中"),
                   error=None, sec=round(time.time() - started, 1),
                   paused_at=datetime.now().isoformat(timespec="seconds"))
        _rvc_train_write(rid, job)
    except Exception as e:
        job.update(status="error", step=job.get("step", ""), error=str(e)[:500],
                   sec=round(time.time() - started, 1))
        _rvc_train_write(rid, job)
        _output_write_meta({
            "id": rid, "ts": job.get("ts"), "status": "error", "kind": "train",
            "voice_name": name, "epochs": epochs,
            "samples_used": job.get("samples_used"),
            "sec": job.get("sec"), "bytes": 0, "error": str(e)[:500],
            "style": f"音色制作 · {name}", "lyrics": f"训练 {epochs} 轮",
            "cot": "train", "abc": "", "params": {},
        })
        _win_toast("✕ 音色制作失败：" + name, str(e)[:120])
    finally:
        _GPU_SEM.release()  # 与上方 acquire() 配对，异常路径也必须释放闸门
        with RVC_TRAIN_LOCK:
            RVC_TRAIN_JOBS[rid] = job


@router.post("/rvc/train")
async def rvc_train(
    files: list[UploadFile],
    name: str = Form(...),
    epochs: int = Form(200),
    separate_vocal: str = Form("off"),
):
    """上传干声样本（或勾选自动分离后直接传完整歌曲）→ 创建音色制作任务（独占运行，与换声/生成共用 GPU）。

    separate_vocal: auto=训练前先用官方 PyMSS 逐文件分离出干净人声（上传完整歌曲时勾选）；
    off=直接训练（上传的已是干声）。"""
    global _RVC_TRAIN_WORKER, _RVC_TRAIN_PAUSE_REQ
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", name.strip())[:40] or "voice"
    if not 150 <= epochs <= 400:
        raise HTTPException(status_code=400,
                            detail="训练轮数须在 150-400 之间（低于 150 音色明显发虚；推荐 200，样本少可用 300）")
    if RVC_MODELS_DIR.joinpath(f"{name}.pth").is_file() or any(RVC_MODELS_DIR.glob(f"{name}*.pth")):
        raise HTTPException(status_code=409, detail=f"音色名已存在：{name}")
    # 在训互斥：已有制作任务排队/运行中时拒绝，防双进程 CUDA OOM 与 logs/<name> 互写
    if _RVC_TRAIN_WORKER is not None and _RVC_TRAIN_WORKER.is_alive():
        with RVC_TRAIN_LOCK:
            busy = any(j.get("status") in ("running", "pending")
                       for j in RVC_TRAIN_JOBS.values())
        if busy:
            raise HTTPException(status_code=409, detail="已有音色制作任务在进行中，请等待完成后再提交")
    rid = datetime.now().strftime("%Y%m%d_%H%M%S_") + os.urandom(2).hex()
    ds = RVC_TRAIN_DIR / rid / "dataset"
    ds.mkdir(parents=True, exist_ok=True)
    total = 0
    for i, f in enumerate(files):
        ext = Path(f.filename or "s.wav").suffix.lower() or ".wav"
        if ext not in (".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus", ".aac", ".wma"):
            ext = ".wav"
        p = ds / f"sample_{i:03d}{ext}"
        size = await _stream_upload_to(f, p, 200 * 1024 * 1024, f"第 {i+1} 个样本")
        total += size
    if total < 300_000:
        raise HTTPException(status_code=400, detail="样本太少（建议 3-10 分钟干净干声）")
    job = {
        "id": rid, "name": name, "status": "pending", "step": "排队中",
        "epochs": epochs, "ts": datetime.now().isoformat(timespec="seconds"),
        "samples": len(files),
    }
    _rvc_train_write(rid, job)
    with RVC_TRAIN_LOCK:
        # 与 resume 同理：清掉上一次运行遗留的暂停标志，防新任务第一步自终止
        _RVC_TRAIN_PAUSE_REQ = False
        RVC_TRAIN_JOBS[rid] = job
    _RVC_TRAIN_WORKER = threading.Thread(target=_rvc_train_worker,
                                         args=(rid, name, epochs, separate_vocal == "auto"),
                                         daemon=True)
    _RVC_TRAIN_WORKER.start()
    return {"ok": True, "id": rid, "name": name, "job": job}


@router.get("/rvc/train/status/{rid}")
def rvc_train_status(rid: str):
    rid = os.path.basename(rid)
    job = _rvc_train_read(rid)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.get("status") == "running" and job.get("step") == "训练中":
        cur, total = _rvc_train_epoch(job.get("name", ""), int(job.get("epochs") or 0))
        if cur:
            job["epoch"] = cur
            job["epochs_total"] = total or job.get("epochs", 0)
        pes = _rvc_train_per_epoch_sec(job.get("name", ""))
        if pes:
            job["per_epoch_sec"] = pes
    # 已生成过试听的回传播放地址（刷新页面后播放器不消失，裁定 F-4）
    if (RVC_TRAIN_DIR / rid / "preview.wav").is_file():
        job["preview_url"] = f"/api/rvc/train/preview/{rid}/audio"
    return job


@router.get("/rvc/train/active")
def rvc_train_active():
    """进行中/排队的音色制作任务（页面刷新后恢复进度条用）。

    另返回近 24h 内已结束（done/error）的落盘任务：网关重启会杀掉进行中的
    worker，任务列表若只回内存态，用户会看到任务"凭空消失"——落盘记录必须可见，
    error 任务可一键续跑。"""
    out = []
    cutoff = time.time() - 24 * 3600
    if RVC_TRAIN_DIR.is_dir():
        for d in sorted(RVC_TRAIN_DIR.iterdir(), reverse=True):
            job = _rvc_train_read(d.name)
            if not job:
                continue
            st = job.get("status")
            if st in ("running", "pending", "paused"):
                # paused 也须回显：暂停任务需在进度列表显示「▶ 继续」，否则重启后找不到入口续跑
                if st == "running" and job.get("step") == "训练中":
                    cur, total = _rvc_train_epoch(job.get("name", ""), int(job.get("epochs") or 0))
                    if cur:
                        job["epoch"] = cur
                        job["epochs_total"] = total or job.get("epochs", 0)
                    pes = _rvc_train_per_epoch_sec(job.get("name", ""))
                    if pes:
                        job["per_epoch_sec"] = pes
                out.append(job)
            elif st in ("done", "error"):
                # 落盘时间兜底：job.json 的 mtime 在 24h 内才回显，避免列表无限膨胀
                try:
                    if (d / "job.json").stat().st_mtime < cutoff:
                        continue
                except OSError:
                    continue
                out.append(job)
    return {"items": out}


@router.post("/rvc/train/resume/{rid}")
def rvc_train_resume(rid: str, confirm: str = Form("no")):
    """续跑被中断的音色制作任务：复用已上传样本与已分离产物，从断点继续。

    confirm=yes 才允许重跑 done 任务——重跑会覆盖同名成品 pth（裁定 F-1）。"""
    global _RVC_TRAIN_WORKER, _RVC_TRAIN_PAUSE_REQ
    rid = os.path.basename(rid)
    job = _rvc_train_read(rid)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    ds = RVC_TRAIN_DIR / rid / "dataset"
    if not ds.is_dir() or not any(ds.iterdir()):
        raise HTTPException(status_code=404, detail="原始样本已丢失，无法续跑（请重新提交）")
    # 检查+置 pending 必须原子完成（裁定 C-2）：否则两个并发 resume 都能通过检查，
    # 双 worker 对同一 rid 双写；done 任务重跑会覆盖同名成品，需显式确认
    if job.get("status") == "done" and confirm != "yes":
        raise HTTPException(status_code=409, detail="该任务已完成。重跑会覆盖现有成品音色，前端需传 confirm=yes 显式确认")
    with RVC_TRAIN_LOCK:
        if job.get("status") in ("running", "pending"):
            raise HTTPException(status_code=409, detail="任务仍在进行中，无需续跑")
        busy = any(j.get("status") in ("running", "pending")
                   for j in RVC_TRAIN_JOBS.values() if j.get("id") != rid)
        if busy:
            raise HTTPException(status_code=409, detail="已有音色制作任务在进行中，请等待完成后再续跑")
        job["status"] = "pending"
        job["step"] = "排队中（续跑）"
        job["error"] = None
        # 暂停标志属于上一次运行：受理续跑时必须复位，否则新 worker 第一步
        # 读到陈旧 True 会立即自终止——任务"秒回暂停"（隐患，已踩坑）
        _RVC_TRAIN_PAUSE_REQ = False
        _rvc_train_write(rid, job)
        RVC_TRAIN_JOBS[rid] = job
    # 续跑时保留原 separate_vocal 意图：只要存在 dataset_clean 目录即视为需要分离
    sep_flag = (RVC_TRAIN_DIR / rid / "dataset_clean").is_dir()
    _RVC_TRAIN_WORKER = threading.Thread(
        target=_rvc_train_worker, args=(rid, job["name"], int(job.get("epochs") or 200),
                                        sep_flag, True),
        daemon=True)
    _RVC_TRAIN_WORKER.start()
    return {"ok": True, "id": rid, "job": job}


@router.post("/rvc/train/pause/{rid}")
def rvc_train_pause(rid: str):
    """优雅暂停音色制作：terminate 训练子进程（每轮落盘检查点，丢最多一轮损失可接受），
    状态改 paused；之后 /rvc/train/resume 从检查点断点续跑，可跨网关重启。"""
    # 关键：模块级变量赋值必须声明 global，否则 UnboundLocalError——
    # 上一版端点每次都 500 崩溃（空响应），标志/句柄从未生效（已踩坑）
    global _RVC_TRAIN_PROC, _RVC_TRAIN_PAUSE_REQ
    rid = os.path.basename(rid)
    job = _rvc_train_read(rid)
    if job.get("status") != "running":
        raise HTTPException(status_code=409,
                            detail="任务不在运行中，无法暂停（仅训练中可暂停；已完成/排队/失败任务无需暂停）")
    name = job.get("name", "")
    with RVC_TRAIN_LOCK:
        # 置位暂停标志：_rvc_run_step 检测到子进程因 terminate 退出时不报 error
        _RVC_TRAIN_PAUSE_REQ = True
        proc = _RVC_TRAIN_PROC
        _RVC_TRAIN_PROC = None
    if proc is not None:
        try:
            proc.terminate()
        except Exception:
            pass
    # 兜底强制清理：句柄可能因竞态/时序丢失，但训练进程还活着占着 GPU——
    # taskkill /T 杀句柄进程树；再按命令行特征（train.py + 音色名）扫杀残留，
    # 确保显存必释放（绝不能用 /IM python.exe——会连网关一起杀）
    try:
        if proc is not None:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -match 'train\\.py' -and $_.CommandLine -match '{re.escape(name)}' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"],
            capture_output=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass
    job.update(status="paused", step=job.get("step", "训练中"),
               error=None, paused_at=datetime.now().isoformat(timespec="seconds"))
    _rvc_train_write(rid, job)
    RVC_TRAIN_JOBS[rid] = job
    return {"ok": True, "id": rid, "job": job}


@router.delete("/rvc/train/{rid}")
def rvc_train_delete(rid: str):
    rid = os.path.basename(rid)
    job = _rvc_train_read(rid)
    if job.get("status") in ("running", "pending"):
        raise HTTPException(status_code=409, detail="任务进行中，不能删除")
    shutil.rmtree(RVC_TRAIN_DIR / rid, ignore_errors=True)
    return {"ok": True}


@router.post("/rvc/convert/{rid}")
async def rvc_convert_by_rid(rid: str, payload: dict):
    """把 output/ 里已生成的歌曲直接送入换声（历史页一键转发，无需重新上传）。"""
    rid = os.path.basename(rid)
    src_wav = _output_wav_path(rid)
    if not src_wav.is_file():
        raise HTTPException(status_code=404, detail="源音频不存在")
    meta_path = _output_meta_path(rid)
    meta = {}
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    if meta.get("kind") == "rvc":
        raise HTTPException(status_code=400, detail="换声结果不能再送换声（请选择生成或上传的音频）")
    model = str(payload.get("model") or "").strip()
    models = _rvc_models()
    if model not in models:
        raise HTTPException(status_code=400, detail=f"未知音色模型：{model}（可用：{models}）")
    rid2 = datetime.now().strftime("%Y%m%d_%H%M%S_") + os.urandom(2).hex()
    in_dir = RVC_JOB_DIR / rid2
    in_dir.mkdir(parents=True, exist_ok=True)
    src = in_dir / "src.wav"
    shutil.copyfile(src_wav, src)
    try:
        import soundfile as sf
        src_duration = round(float(sf.info(str(src)).duration), 1)
    except Exception:
        src_duration = round(src.stat().st_size / 160_000, 1)
    job = {
        "id": rid2, "status": "running", "ts": datetime.now().isoformat(timespec="seconds"),
        "task_name": str(payload.get("task_name") or "").strip()[:100],
        "model": model, "pitch": int(payload.get("pitch") or 0),
        "f0_method": str(payload.get("f0_method") or "rmvpe"),
        "index_rate": float(payload.get("index_rate") or 0.75),
        "protect": float(payload.get("protect") or 0.33),
        "rms_mix_rate": float(payload.get("rms_mix_rate") or 1.0),
        "src_name": f"历史歌曲 {rid}", "src_size": src.stat().st_size,
        "src_duration": src_duration, "src_rid": rid,
    }
    with _RVC_LOCK:
        _RVC_JOBS[rid2] = job
    threading.Thread(target=_rvc_convert_worker,
                     args=(rid2, job, src, in_dir, model,
                           int(payload.get("pitch") or 0),
                           str(payload.get("f0_method") or "rmvpe"),
                           float(payload.get("index_rate") or 0.75),
                           float(payload.get("protect") or 0.33),
                           float(payload.get("rms_mix_rate") or 1.0),
                           payload.get("separate_vocal", "auto") != "off"),
                     daemon=True).start()
    return {"ok": True, "id": rid2, "job": job}


# --------------------------------------------------------------------------- #
# 音色库（参考音频 + 参考文本）
# --------------------------------------------------------------------------- #
@router.get("/voices")
def list_voices():
    return {"voices": voices.list_voices()}


@router.post("/voices")
def save_voice(
    name: str = Form(...),
    reference_text: str = Form(""),
    audio: UploadFile = File(...),
):
    data = audio.file.read()
    suffix = Path(audio.filename or "prompt.wav").suffix.lower() or ".wav"
    item = voices.save_voice(name, reference_text, data, suffix)
    return {"ok": True, "voice": item}


@router.delete("/voices/{voice_id}")
def delete_voice(voice_id: str):
    voices.delete_voice(voice_id)
    return {"ok": True}


@router.post("/voices/transcribe")
async def transcribe_voice(audio: UploadFile = File(...)):
    """上传参考音频 -> 自动识别歌词/文本（ASR），用于翻唱工作流。"""
    p = ROOT / "tmp" / "asr"
    p.mkdir(parents=True, exist_ok=True)
    ext = Path(audio.filename or "prompt.wav").suffix.lower()
    if ext not in (".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus", ".aac", ".wma"):
        ext = ".wav"
    f = p / (datetime.now().strftime("%H%M%S_") + os.urandom(2).hex() + ext)
    await _stream_upload_to(audio, f, 200 * 1024 * 1024, "音频")
    try:
        text = asr.recognize_wav_bytes(f.read_bytes(), audio.filename or "prompt.wav")
    finally:
        f.unlink(missing_ok=True)
    return {"ok": True, "text": text}


@router.post("/voices/denoise")
async def denoise_voice(audio: UploadFile = File(...)):
    """上传参考音频 -> 降噪后返回增强音频。"""
    p = ROOT / "tmp" / "denoise"
    p.mkdir(parents=True, exist_ok=True)
    ext = Path(audio.filename or "ref.wav").suffix.lower()
    if ext not in (".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus", ".aac", ".wma"):
        ext = ".wav"
    f = p / (datetime.now().strftime("%H%M%S_") + os.urandom(2).hex() + ext)
    await _stream_upload_to(audio, f, 200 * 1024 * 1024, "音频")
    try:
        out = denoise.denoise_wav_bytes(f.read_bytes(), audio.filename or "ref.wav")
    finally:
        f.unlink(missing_ok=True)
    return Response(
        content=out,
        media_type="audio/wav",
        headers={"Content-Disposition": 'attachment; filename="enh.wav"'},
    )


# --------------------------------------------------------------------------- #
# 模板管理（前后端均可使用；此处提供服务端持久化到本地 templates.json）
# --------------------------------------------------------------------------- #
_TEMPLATES_FILE = ROOT / "templates.json"


def _load_templates() -> list[dict]:
    if not _TEMPLATES_FILE.exists():
        return []
    try:
        data = json.loads(_TEMPLATES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


@router.get("/templates")
def list_templates():
    return {"templates": _load_templates()}


@router.post("/templates")
def save_template(payload: dict):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    tpl = {
        "id": voices._SAFE.sub("-", name) + "-" + str(int(time.time() * 1000)),
        "name": name[:60],
        "created_at": datetime.now().isoformat(),
    }
    # 全量参数白名单，便于模板一键回填
    for key in ("style", "lyrics", "cot", "abc", "seed", "cfg", "steps",
                "gender", "abc_temperature", "abc_top_p", "abc_top_k",
                "semantic_temperature", "semantic_top_p", "semantic_top_k"):
        if key in payload and payload[key] is not None:
            val = payload[key]
            tpl[key] = str(val)[:4000] if isinstance(val, str) else val
    items = _load_templates()
    items.insert(0, tpl)
    _TEMPLATES_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True, "template": tpl}


@router.delete("/templates/{template_id}")
def delete_template(template_id: str):
    items = _load_templates()
    kept = [t for t in items if t.get("id") != template_id]
    if len(kept) == len(items):
        raise HTTPException(status_code=404, detail="template not found")
    _TEMPLATES_FILE.write_text(
        json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True}


# 将扩展路由挂载到编译网关
app.include_router(router)

# AI 工作台（dsh 内核桥接）
from ai_router import router as _ai_router
app.include_router(_ai_router)


# --------------------------------------------------------------------------- #
# 启动时孤儿任务自愈：任何跨重启仍为 running 的落盘任务统一标记为中断，
# 免得历史页/进行中区永远挂着假任务（生成任务在 /generate/current 有懒清理，
# 这里是启动时的一次性兜底，覆盖换声/训练等所有 kind）。
# --------------------------------------------------------------------------- #
def _orphan_cleanup_on_startup() -> None:
    # 1) output/ 元数据（生成/批量/换声/训练的归档记录）
    if OUTPUT_DIR.is_dir():
        for p in OUTPUT_DIR.glob("*.json"):
            try:
                m = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if m.get("status") == "running":
                m["status"] = "error"
                m["error"] = "服务重启，任务中断"
                try:
                    p.write_text(json.dumps(m, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
                except Exception:
                    pass
    # 2) 音色训练 job.json（训练 worker 随进程终止，日志停在最后一轮）
    if RVC_TRAIN_DIR.is_dir():
        for d in RVC_TRAIN_DIR.iterdir():
            jp = d / "job.json"
            if not jp.is_file():
                continue
            try:
                job = json.loads(jp.read_text(encoding="utf-8"))
            except Exception:
                continue
            if job.get("status") in ("running", "pending"):
                job["status"] = "error"
                job["error"] = "服务重启，任务中断"
                if job.get("step") == "训练中":
                    job["step"] = "已中断"
                try:
                    jp.write_text(json.dumps(job, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
                except Exception:
                    pass
    # 3) 批量队列自恢复：进程被杀时正在跑的条目会永远卡在 running（worker 只取
    # pending），重启后整个队列假死、等待中的条目也没法手动启动。这里把卡死的
    # running 复位为 pending；若还有待跑条目则自动拉起 worker 继续执行。
    try:
        bs = _batch_snapshot()
        restored = 0
        for it in bs.get("items", []):
            if it.get("status") == "running":
                it["status"] = "pending"
                restored += 1
        bs["running"] = False
        bs["current"] = None
        _batch_store(bs)
        has_pending = any(it.get("status") == "pending" for it in bs.get("items", []))
        if restored or has_pending:
            _CANCEL_EVENT.clear()
            _batch_ensure_worker()
    except Exception:
        pass  # 队列文件损坏时不应阻断启动；前端可手动重试


# 孤儿清理仅在真实启动服务时执行；模块导入（如 dsh 桥子进程 import app）不得触发，
# 否则会把正在运行的任务误判为"服务重启，任务中断"。
_PURE_API_TIP = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>音乐工作台 · 网关</title><style>
body{font-family:"Microsoft YaHei",system-ui,sans-serif;background:#15161a;color:#e6e6e6;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.box{max-width:520px;padding:28px 32px;background:#1d1f25;border:1px solid #2c2f38;
border-radius:12px;line-height:1.7}
a{color:#6ea8fe}b{color:#ffd479}code{background:#262930;padding:2px 6px;border-radius:4px}
</style></head><body><div class="box">
<h3>这是 API 网关，不是工作台入口</h3>
<p>当前 <code>gateway_serve_ui=false</code>，网关已退化为纯 API 服务，不再托管页面。</p>
<p>请打开工作台：<a href="http://127.0.0.1:{dsh}/">http://127.0.0.1:{dsh}/</a></p>
<p style="opacity:.7;font-size:13px">若 3081 未运行，双击 <b>启动音乐工作台.bat</b>；
想让网关恢复直出页面，把 <code>settings.py</code> 的 <code>gateway_serve_ui</code> 改回 <code>True</code>。</p>
</div></body></html>"""


def _serve_index():
    # 纯 API 模式（阶段二）：网关不再托管 UI，只给一条明确的入口指引，
    # 避免"双入口"——页面只能有一个家，就是 3081。
    if not settings.gateway_serve_ui:
        return Response(
            content=_PURE_API_TIP.replace("{dsh}", str(settings.dsh_port)).encode("utf-8"),
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )
    idx = ROOT / "static" / "index.html"
    if idx.is_file():
        content = idx.read_bytes()
    else:
        content = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                   "<title>音乐工作台</title></head><body>"
                   "<h1>index.html 缺失</h1>"
                   "<p>请确认 static/index.html 存在。</p></body></html>").encode("utf-8")
    # 禁缓存：前端页面迭代频繁，避免浏览器用旧版 JS 导致"改了不生效"
    return Response(
        content=content,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


_INDEX_ROUTE = APIRouter()


@_INDEX_ROUTE.get("/", include_in_schema=False)
def _index_root():
    return _serve_index()


# 将根路由插到路由表最前面，覆盖编译网关自带的 / 处理器
app.include_router(_INDEX_ROUTE)
# include_router 追加在末尾，因此此刻最后一个 "/" 路由就是我们自己的
_mine_root = next(
    (r for r in reversed(app.routes)
     if getattr(r, "path", None) == "/" and type(r).__name__ == "APIRoute"),
    None,
)
if _mine_root is not None:
    # 删除所有 "/" 路由（含编译网关自带、带硬编码 403 门槛的那条），
    # 再把我们自己的这条放回最前面，确保根路径命中新版页面。
    app.routes[:] = [r for r in app.routes
                     if getattr(r, "path", None) != "/" or r is _mine_root]
    app.routes.remove(_mine_root)
    app.routes.insert(0, _mine_root)

if __name__ == "__main__":
    _orphan_cleanup_on_startup()
    uvicorn.run(
        app,
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
    )