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
from urllib.parse import urlparse
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
# 真正的 add_middleware 在下方 LAN 解析之后——allow_origins 需要 LAN_HOSTS。
_P_GW = settings.app_port
_P_DSH = settings.dsh_port


# --------------------------------------------------------------------------- #
# 本机接口守卫（蓝军 S3）
#
# CORS 只挡「读响应」，挡不住「发请求」：无 body 的 POST / DELETE 与
# text/plain 表单都属浏览器简单请求，不发预检，任意网页的 JS 都能对
# 127.0.0.1:7863 发出去并生效（停任务、杀引擎、重下模型），只是读不到回包。
# 再叠一层 DNS 重绑定（myevil.com 解析到 127.0.0.1）就连响应也能读。
# 本机工作台没有远程调用方，所以按两条硬规则收紧：
#   1) Host 必须是回环地址——重绑定带来的 Host: myevil.com 直接 403；
#   2) 变更类请求（POST/PUT/PATCH/DELETE）若带 Origin/Referer，来源主机
#      也必须是回环地址——跨站简单请求全部 403。
# 命令行/curl/看门狗这类不发 Origin 的本机调用不受影响。
# --------------------------------------------------------------------------- #
_LOOP_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_LAN_OPT_OUT = {"1", "true", "yes", "on"}


def _host_name(host_header: str) -> str:
    """取 Host/IPv6 字面量里的主机名（去端口、去方括号），统一小写。"""
    h = (host_header or "").strip().lower()
    if h.startswith("["):
        return h[1:].split("]", 1)[0]
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def _host_is_loop(host_header: str) -> bool:
    h = (host_header or "").strip().lower()
    if h.startswith("["):                    # IPv6 字面量 [::1]:7863
        return h.split("]", 1)[0] + "]" in _LOOP_HOSTS
    if h.count(":") > 1:                     # 裸 IPv6（不带端口）
        return h in _LOOP_HOSTS
    return _host_name(h) in _LOOP_HOSTS


def _lan_host_allowlist(bind_host: str) -> set[str]:
    """LAN 模式下允许被访问的主机名白名单（第三方审计 N-3）。

    绝不能拿「Origin 主机 == Host 主机」当同源判据：那两个字符串都由攻击者的域名决定，
    DNS 重绑定把 evil.com 解析到内网 IP 时两者天然相等，等于没判。
    唯一可信的名单来自运维显式声明：YUE2_LAN_HOSTS 优先，其次退到绑定的那个具体地址。
    """
    raw = os.environ.get("YUE2_LAN_HOSTS", "")
    hosts = {h.strip().lower().rstrip(".") for h in raw.split(",") if h.strip()}
    if hosts:
        return hosts
    b = (bind_host or "").strip().lower()
    return {b} if b and b not in ("0.0.0.0", "::") else set()


def _resolve_lan_mode(bind_host: str) -> bool:
    """回环绑定返回 False；非回环绑定必须显式 YUE2_ALLOW_LAN=1 才允许启动（蓝军 Y1）。

    否则改了 settings.app_host 就"以为开放了"，实际 Host 检查会把局域网请求全 403，
    比启动即失败更难查。
    """
    if (bind_host or "").strip().lower() in _LOOP_HOSTS:
        return False
    if os.environ.get("YUE2_ALLOW_LAN", "").strip().lower() not in _LAN_OPT_OUT:
        raise RuntimeError(
            f"网关绑定地址 {bind_host!r} 不是回环地址：本工作台没有鉴权、没有多用户隔离，"
            "不能这样暴露到局域网。确认要开放请设环境变量 YUE2_ALLOW_LAN=1，"
            "并设 YUE2_LAN_HOSTS=<本机 IP 或主机名，逗号分隔>，且自行在前面套反代与鉴权。"
        )
    if not _lan_host_allowlist(bind_host):
        raise RuntimeError(
            f"已开 YUE2_ALLOW_LAN，但网关绑在通配地址 {bind_host!r} 且没给 YUE2_LAN_HOSTS："
            "没有主机名白名单就无法把『你的设备』和『DNS 重绑定过来的网页』区分开，"
            "整套同源防护会形同虚设。请改绑具体地址（如 192.168.1.7），"
            "或设 YUE2_LAN_HOSTS=192.168.1.7,dash.local。"
        )
    return True


LAN_MODE = _resolve_lan_mode(str(getattr(settings, "app_host", "") or ""))
LAN_HOSTS = _lan_host_allowlist(str(getattr(settings, "app_host", "") or ""))
# 可内嵌本工作台 UI 的来源：回环两向 + LAN 白名单（未开 LAN 时后者为空，行为不变）。
# 注意 frame-ancestors 的 'self' 不含跨端口，所以 dsh(3081) 内嵌 网关(7863) 必须逐个列出。
_FRAME_HOSTS = ["127.0.0.1", "localhost"] + sorted(LAN_HOSTS - {"127.0.0.1", "localhost"})
if LAN_MODE:
    print(f"[guard] ⚠ YUE2_ALLOW_LAN 已开启：网关绑定 {settings.app_host}，"
          f"只认主机名 {sorted(LAN_HOSTS)}；同网段设备均可调用接口（无鉴权），"
          "开 LAN 请同时假定 stderr 原文里的绝对路径/账号名会被同网段看到（Y2），"
          "并务必在前面加反代与鉴权，勿暴露公网", flush=True)
    # 评审复查补充：dsh 代理硬编码连 127.0.0.1:<gw>（ui-panel.mjs LAB_HOST）。
    # 绑具体 LAN IP 时回环上没人监听，本机工作台 ECONNREFUSED——HTTP 守卫修得
    # 再对也到不了那一层。这是 TCP 层的坑，只能在启动时说破。
    if (str(settings.app_host).strip().lower() not in ("0.0.0.0", "::")):
        print(f"[guard] ⚠ LAN 模式下 app_host={settings.app_host!r} 是具体地址：本机 dsh 工作台的"
              f"内部代理只会连 127.0.0.1:{_P_GW}，将直接 ECONNREFUSED。"
              "请把 settings.app_host 改为 0.0.0.0（同时监听回环与局域网），"
              "除非你确定只从其它设备直连网关端口、不用本机工作台", flush=True)

# CORS 白名单：回环两端口；开 LAN 后把 LAN_HOSTS 的两端口也纳入（评审 P1-2：
# 有人直接开 http://<lanhost>:7863 时，只认回环的 CORS 会让响应缺 ACAO 头，
# 页面能收但 JS 读不到，症状莫名其妙）。add_middleware 顺序保持在守卫之前，
# 让守卫仍是最外层，403 响应不会被 CORS 层加工。
_CORS_ORIGINS = [
    f"http://127.0.0.1:{_P_GW}", f"http://localhost:{_P_GW}",
    f"http://127.0.0.1:{_P_DSH}", f"http://localhost:{_P_DSH}",
]
if LAN_MODE:
    _CORS_ORIGINS += [f"http://{h}:{p}" for h in sorted(LAN_HOSTS) for p in (_P_GW, _P_DSH)]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _origin_is_loop(value: str) -> bool:
    if not value or value == "null":
        return False
    try:
        return (urlparse(value).hostname or "").lower() in _LOOP_HOSTS
    except ValueError:
        return False


def _origin_in_allowlist(value: str) -> bool:
    """LAN 模式的放行判据：主机名必须在白名单里，且端口是网关(7863)或工作台(3081)。

    dsh 工作台把页面挂在 :3081、经内部代理转发到网关，浏览器看到的同源 Origin
    就是 http://<lanhost>:3081——只认网关端口会把 UI 的每一个写请求都 403 掉。
    """
    if not value or value == "null":
        return False
    try:
        u = urlparse(value)
    except ValueError:
        return False
    h = (u.hostname or "").lower()
    if not h or h not in LAN_HOSTS:
        return False
    return u.port in (None, _P_GW, _P_DSH)


@app.middleware("http")
async def _guard_local_only(request, call_next):
    host = request.headers.get("host", "")
    if LAN_MODE:
        # 回环 Host 必须放行：dsh 代理(ui-panel.mjs)的内部跳变恒定发出
        # Host: 127.0.0.1:7863。外部攻击者直接打 lanhost:7863 时 Host 就是
        # lanhost（∈白名单）；DNS 重绑定的 Host: evil.example 两条都不满足，照旧 403。
        if _host_name(host) not in LAN_HOSTS and not _host_is_loop(host):
            return JSONResponse(status_code=403, content={"detail": "主机名不在局域网白名单内"})
    elif not _host_is_loop(host):
        return JSONResponse(status_code=403, content={"detail": "只接受面向本机回环地址的请求"})
    if request.method.upper() in _WRITE_METHODS:
        origin = request.headers.get("origin") or request.headers.get("referer") or ""
        # 浏览器同源 POST 也会带 Origin，所以放行条件是"本机/白名单"，而不是"没带 Origin 就放过"
        if origin and not (_origin_in_allowlist(origin) if LAN_MODE else _origin_is_loop(origin)):
            return JSONResponse(status_code=403, content={"detail": "跨站请求被拒绝"})
    response = await call_next(request)
    # 蓝军 Y4：任何网页都能把 127.0.0.1 iframe 进自己页面做点击劫持（诱导用户点"生成/删除"）。
    # 工作台 UI 确实要被 dsh(3081) 内嵌，所以不能一刀切 X-Frame-Options，改用 CSP 的
    # frame-ancestors 白名单——只放行本机回环与 LAN 主机名白名单里的那些来源。
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Content-Security-Policy",
        "frame-ancestors 'self' " + " ".join(
            f"http://{_h}:{_p}" for _h in _FRAME_HOSTS for _p in (_P_GW, _P_DSH)),
    )
    return response


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


def _gpu_total_mb() -> int | None:
    """整卡显存（MiB）；查不到返回 None。训练 batch_size 按它取，不是按空闲量。"""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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
# SheetSage2 转谱串行闸门：上游把中间产物与 score.abc 写死在共享目录
# `sheetsage2-output/`（src/sheetsage_pt.py:152,167），并发两次转谱会互相覆盖，
# 后者可能读到前者写回的谱。模型本身也是单实例，并发只会把内存翻倍。
_SCORE_ENGINE_LOCK = threading.Lock()
# 乐谱持久化：转谱结果落盘 data/scores/，刷新/重启不丢，可复用回填
SCORES_DIR = ROOT / "runtime" / "data" / "scores"

_ID_SEEN: set[str] = set()


def _id_busy(rid: str) -> bool:
    """ID 是否已被历史上的产物占用（跨重启也算）。"""
    cands = [OUTPUT_DIR / rid, SCORES_DIR / f"{rid}.json", HIST_DIR / f"{rid}.wav",
             RVC_JOB_DIR / rid, RVC_TRAIN_DIR / rid]
    if OUTPUT_DIR.is_dir():
        cands += list(OUTPUT_DIR.glob(rid + ".*"))  # <id>.json/.wav/.txt/.lrc/.lrcjob/.elrc…
    return any(p.exists() for p in cands)


def _new_id() -> str:
    """任务 ID：秒级时间戳 + 32 位随机，进程内与磁盘双重查重。

    ID 同时就是产物文件名（output/<id>.json、data/scores/<id>.json、rvc/jobs/<id>/…），
    撞号等于把别人的记录和音频静默覆盖，删除/重试还会连带命中同号的另一半，
    所以宁可多绕几圈也不允许重复。时间戳前缀保留，列表排序与旧记录格式不受影响。
    """
    for _ in range(64):
        rid = datetime.now().strftime("%Y%m%d_%H%M%S_") + os.urandom(4).hex()
        if rid not in _ID_SEEN and not _id_busy(rid):
            _ID_SEEN.add(rid)
            return rid
    raise HTTPException(status_code=503, detail="任务 ID 连续冲突，请稍后重试")


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
_VOICE_RE = re.compile(r"^V:\s*([A-Za-z0-9_\-]+)", re.M)
# YuE2 原生引号和弦词汇（大三和弦无后缀，故根音可单独成词）+ 可选斜杠低音。
# 只认这套闭集：C:maj、Cmaj9、C13、A7alt 都不是原生记号，不该被当成和弦。
_ABC_QUALITIES = ("maj7", "m(maj7)", "m7b5", "7sus4", "dim7", "sus4", "sus2",
                  "aug", "dim", "maj", "min", "m6", "m7", "m", "6", "7")
_CHORD_RE = re.compile(
    r'"[A-G](?:##|bb|[b#])?(?:'
    + "|".join(re.escape(q) for q in _ABC_QUALITIES)
    + r')?(?:/[A-G](?:##|bb|[b#])?)?"')
# 「谱面上有没有和弦记号」用宽松式：ABC 正文里被双引号包住、以音名开头的记号就是和弦。
# 上面那份闭集是**记号表能被改写/转调**的范围，不是"存在性"的范围——拿闭集判存在会把
# "C9" "Cadd9" "G13" "Fm9" "Csus" 判成无和弦，进而把该走 full 的谱错路由到 melody。
_CHORD_RE_LOOSE = re.compile(
    r'"[A-G](?:##|bb|[b#])?([A-Za-z0-9()#\-]*)(?:/[A-G](?:##|bb|[b#])?)?"')
# 根音之后允许出现的记号字符：数字、升降、括号/连字符，以及和弦后缀用到的字母
# （maj min dim aug sus add m M 七九十一十三 等）。故意做成"字母白名单"而不是
# [A-Za-z] 全放开——全放开会把正文里任何引号住的英文单词（"Chorus"）当成和弦，
# 纯旋律谱就此被误判成带和弦、改走 full，那是把没和弦的谱硬塞给和弦路线。
_CHORD_TAIL_CHARS = set("0123456789#()-+/.majinsdugMAJINSDG")
_CHORD_TAIL_WORDS = ("alt", "add", "dim", "aug", "sus", "maj", "min", "no", "A7", "b5")


def _is_chord_token(text: str) -> bool:
    """宽松式命中后的一遍词形校验：排除"以 A–G 开头的普通英文单词"这类假阳性。"""
    rest = text
    for word in _CHORD_TAIL_WORDS:
        rest = rest.replace(word, "")
    return all(ch in _CHORD_TAIL_CHARS for ch in rest)


def _abc_chord_tokens(abc: str, loose: bool = True) -> list[str]:
    """正文小节里的和弦记号列表（loose=存在性判定，strict=记号表可改写范围）。"""
    # 只在正文小节里找：X:/T:/K:/V: 这类信息行、% 开头的注释行里引号包住的标题
    # 文字不算和弦记号
    body = "\n".join(l for l in abc.splitlines()
                     if not re.match(r"^[A-Za-z]:", l.strip())
                     and not l.lstrip().startswith("%"))
    if not loose:
        return _CHORD_RE.findall(body)
    out = []
    for tail in _CHORD_RE_LOOSE.findall(body):
        if _is_chord_token(tail):
            out.append(tail)
    return out


def _abc_has_chords(abc: str) -> bool:
    """谱面是否带和弦声部/和弦记号（决定 YuE2 该走 full 还是 melody 路线）。"""
    if any("chord" in v for v in (name.lower() for name in _VOICE_RE.findall(abc))):
        return True
    return bool(_abc_chord_tokens(abc))


# 用户输入长度上限。以前超 4000 字是**静默截断**：歌词被切掉的后果是强制对齐拿到的
# 歌词与模型实际唱的不是同一份（尾部对不上），乐谱被切掉的后果是只优化/只执行前半首。
# 现在超限直接 400 说明原因；上限放宽到足够装下一整首（20000 字符 ≈ 5 分钟歌的完整谱）。
_MAX_LYRICS = 20000
_MAX_ABC = 20000
_MAX_STYLE = 2000


def _limit_text(label: str, val, cap: int) -> str:
    s = str(val or "")
    if len(s) > cap:
        raise HTTPException(
            status_code=400,
            detail=f"{label}过长：{len(s)} 字符，上限 {cap}。请精简后再提交"
                   "（不再静默截断——截断会让歌词对齐与乐谱只覆盖半首歌）")
    return s


def _resolve_cot(cot: str, abc: str) -> tuple[str, str]:
    """乐谱非空 ⇒ 必须走「消费这份谱」的路线；乐谱为空 ⇒ 才交给模型自己规划。

    外部 ABC 会绕过符号规划器直接被当成条件，但 melody / full 是两套不同的原生指令，
    而且引擎不会替你改写谱面：melody 吃「和弦记号已删除」的旋律谱（它不会自动去掉谱里
    的 "C"、"Am7"），full 吃旋律+和弦谱。路线和谱的形态不符就属于口径不符，谱面条件会
    走偏，用户听到的就是没照他给的旋律走。所以这里按谱的实际形态双向纠正。
    另外 cot=off 带谱会被引擎判 400（"external ABC requires cot=melody or cot=full"）。
    """
    mode = str(cot or "").strip().lower()
    mode = mode if mode in ("full", "melody", "off") else "full"
    if not (abc or "").strip():
        return mode, ""
    want = "full" if _abc_has_chords(abc) else "melody"
    if mode == want:
        return mode, ""
    if mode == "off":
        return want, f"已填写乐谱，off 不消费乐谱，已改为 {want}（按你的乐谱生成）"
    if want == "melody":
        return "melody", ("乐谱里没有和弦记号，已改用 melody 路线按谱生成"
                          "（full 要吃旋律+和弦谱，口径不符谱面条件会走偏）")
    return "full", ("乐谱带和弦记号，已改用 full 路线按谱生成"
                    "（melody 不会自动删掉和弦记号，口径不符谱面条件会走偏）")


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
        if s.upper().startswith("V:"):
            continue  # 声部头只有 clef/name 等指令，里面的字母不是音（"Vocal" 会被读成 c、a）
        if in_music:
            # 和弦记号（"Am7"）里的字母不是唱出来的音，先摘掉再统计音域
            for pm in re.finditer(r"(_|\^|=)?([A-Ga-g])([',]*)", re.sub(r'"[^"]*"', " ", s)):
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
    voices = list(dict.fromkeys(_VOICE_RE.findall(abc)))
    if voices:
        out["voices"] = voices
    tokens = _abc_chord_tokens(abc)
    chords = bool(tokens) or _abc_has_chords(abc)
    out["has_chords"] = chords
    out["chord_count"] = len(tokens)
    out["cot_suggested"] = "full" if chords else "melody"
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
        with _SCORE_ENGINE_LOCK:
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
    job_id = _new_id()
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
            an = rec.get("analysis") or {}
            out.append({"id": rec.get("id"), "time": rec.get("time"),
                        "cot": an.get("cot_suggested"),
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
    # 换声任务（含排队中：串行队列里后面的条目要在任务管理页看得见）
    with _RVC_LOCK:
        items += [_rvc_live(j) for j in _RVC_JOBS.values()
                  if j.get("status") in ("running", "pending")]
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
#
# 单文件限额挡不住"总量"：训练上传可以一次POST N 个文件，每个都合法地低于
# 200MB，把 runtime/ 撑满后整个盘（含系统盘）一起遭殃，而且是在训练流水线里
# 炸的，报出来的是一堆 F0/特征提取失败，看不出根因是磁盘。所以再加两道：
# 一次请求的累计字节上限 + 落盘前剩余空间下限。
# --------------------------------------------------------------------------- #
_DISK_FLOOR_BYTES = 2 * 1024 ** 3      # 剩余空间低于此值就拒绝再收上传
_MAX_UPLOAD_TOTAL = 2 * 1024 ** 3      # 单次请求累计落地不超过此值


def _free_bytes(path: Path) -> int:
    """所在磁盘分区的剩余字节；探测失败返回极大值（不把用户挡在门外）。"""
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(str(probe)).free
    except Exception:
        return 1 << 62


async def _stream_upload_to(file: UploadFile, dest: Path, limit: int,
                            label: str = "文件",
                            budget: dict | None = None) -> int:
    """把上传文件分块流式写到 dest，超过 limit 字节抛 413 并清理残文件。返回写入字节数。

    budget 传一个字典时，成功落地的字节数累加进 budget["used"]，供多文件请求查总量。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _free_bytes(dest.parent) < _DISK_FLOOR_BYTES:
        raise HTTPException(
            status_code=507,
            detail=f"磁盘剩余不足 {_DISK_FLOOR_BYTES // (1024**3)}GB，"
                   "请先清理 runtime/ 下的旧产物（输出/训练工作目录）再上传")
    if budget is not None:
        if int(budget.get("used", 0)) >= _MAX_UPLOAD_TOTAL:
            raise HTTPException(
                status_code=413,
                detail=f"本次上传累计已达 {_MAX_UPLOAD_TOTAL // (1024**3)}GB 上限，"
                       "请分批或先清理旧素材")
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
        if budget is not None:
            budget["used"] = int(budget.get("used", 0)) + total
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
    if not isinstance(m, dict):
        m = {}
    now = datetime.now()
    # 先校验再落盘：顺序反过来会让一次被判 400 的上传在磁盘上留下没人认领的 wav。
    style_s = _limit_text("曲风描述", m.get("style"), _MAX_STYLE)
    lyrics_s = _limit_text("歌词", m.get("lyrics"), _MAX_LYRICS)
    abc_s = _limit_text("乐谱", m.get("abc"), _MAX_ABC)
    rid = _new_id()
    fn = rid + ".wav"
    size = await _stream_upload_to(audio, HIST_DIR / fn, 200 * 1024 * 1024, "音频")
    item = {
        "id": rid,
        "ts": now.isoformat(timespec="seconds"),
        "file": fn,
        "bytes": size,
        # 与生成入口同一套上限和同一条"过长即报错"口径：历史里存的那份词谱，
        # 必须和当初真正喂给引擎的那份一字不差，否则复用记录=复用半首歌。
        "style": style_s,
        "lyrics": lyrics_s,
        "cot": m.get("cot", "full"),
        "abc": abc_s,
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
    _output_purge(rid)   # 逐字歌词 .lrc/.elrc 与歌词 txt 落在 output/，不清就是孤儿
    _hist_write(keep)
    return {"ok": True}


@router.post("/models/switch")
def models_switch(payload: dict):
    path = (payload.get("path") or "").strip().replace("\\", "/")
    if path.startswith("model/"):
        path = path[len("model/"):]
    if not path:
        raise HTTPException(status_code=400, detail="path is required")
    # 蓝军 S11：写进 server.json 的必须是**参与校验的那个值**。以前校验用 safe、回写用原始 path，
    # 于是 "../../x" 能带着未校验的路径逃逸引擎工作目录（现在也顺带修掉了重复的 model/ 前缀）。
    safe = Path(path).name
    if safe != path:
        raise HTTPException(status_code=400, detail="只接受 model/ 目录下的纯文件名")
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


# 一个任务 ID 在 output/ 下的全部产物后缀。删除任务必须按这张表清干净——
# 以前只删 .wav+.json，歌词 txt / lrc / lrcjob / elrc 全留在盘上（孤儿产物）。
_OUTPUT_EXTS = (".wav", ".json", ".txt", ".lrc", ".lrcjob", ".elrc")


def _output_extra_assets(rid: str) -> list[str]:
    """从该任务的 meta 里取"多产物"文件名（换声的 vocals_original / accompaniment /
    full_song 这一族叫 `<id>_名字.wav`，后缀白名单命不中）。只认 meta 自己登记的
    名字，且必须落在 `<rid>_*.wav` 这个形状里——rid 来自 URL，绝不 glob。"""
    try:
        meta = json.loads((OUTPUT_DIR / (rid + ".json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: list[str] = []
    for name in (meta.get("assets") or {}).values():
        name = str(name or "")
        if name.startswith(rid + "_") and name.endswith(".wav") and name not in out:
            out.append(name)
    return out


def _output_purge(rid: str) -> int:
    """删掉该任务在 output/ 下的所有产物，返回删除个数。只认白名单后缀，
    绝不按 glob 匹配（rid 来自 URL，'*' 之类的值会把整目录清空）。
    多产物按 meta 登记的名字删，否则换声删一条就留三个几十 MB 的孤儿。"""
    rid = os.path.basename(str(rid or "").strip())
    if not rid or rid in (".", ".."):
        return 0
    extra = _output_extra_assets(rid)
    n = 0
    for name in extra:
        p = OUTPUT_DIR / name
        try:
            if p.is_file():
                p.unlink()
                n += 1
        except OSError:
            pass  # 被占用（Windows 上播放器还开着）：清不掉就留着，不阻断删除流程
    for ext in _OUTPUT_EXTS:
        p = OUTPUT_DIR / (rid + ext)
        try:
            if p.is_file():
                p.unlink()
                n += 1
        except OSError:
            pass  # 被占用（Windows 上播放器还开着）：清不掉就留着，不阻断删除流程
    return n


def _rvc_job_purge(rid: str) -> bool:
    """删掉换声任务的工作目录 runtime/rvc/jobs/<id>/。

    里面存的是用户上传的原曲（常 30MB+）与分离出的干声/伴奏，只删 output/ 下的
    成品 wav 会留下这一整目录当孤儿——1.8GB/21 个任务就是这么攒出来的。
    只按 ID 精确匹配一层目录，绝不 glob。"""
    rid = os.path.basename(str(rid or "").strip())
    if not rid or rid in (".", ".."):
        return False
    d = (RVC_JOB_DIR / rid).resolve()
    if not d.is_dir() or d.parent != RVC_JOB_DIR.resolve():
        return False
    shutil.rmtree(d, ignore_errors=True)
    return not d.exists()


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
    # 长度闸门：超限如实报错，不再静默截断（截断过的歌词会让强制对齐对不上尾部）
    _limit_text("曲风描述", style, _MAX_STYLE)
    _limit_text("歌词", payload.get("lyrics"), _MAX_LYRICS)
    _limit_text("乐谱", payload.get("abc"), _MAX_ABC)
    # 有谱即按谱：乐谱框非空就必须走消费乐谱的路线，不能让它滑到 off / 形态不符的 full
    cot_eff, cot_note = _resolve_cot(str(payload.get("cot") or "full"),
                                     str(payload.get("abc") or ""))
    payload["cot"] = cot_eff
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
        rid = _new_id()
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
            "style": style[:_MAX_STYLE],
            "lyrics": str(payload.get("lyrics") or "")[:_MAX_LYRICS],
            "cot": payload.get("cot", "full"),
            "abc": str(payload.get("abc") or "")[:_MAX_ABC],
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
        fail = None
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
        except Exception as exc:
            # 线程内异常（如任务 ID 连续冲突 503）以前只是无声死线程：
            # 页面停在"排队中"或凭空变回空闲，用户以为提交了却在跑
            fail = f"任务启动失败：{type(exc).__name__}: {exc}"
            raise
        finally:
            # 占位 job 未被真实任务替换（取消/异常）时清掉，避免轮询端永远显示"排队中"
            j = _gen_get_job()
            if j and j.get("id") is None:
                _gen_set_job(None if not fail else {"id": None, "status": "error", "error": fail})
    threading.Thread(target=_chain_runner, daemon=True).start()
    # 响应里不再 _make_job(1)：那会凭空多烧一个 ID（蓝军 Y7），而那个 ID 对应的任务
    # 从来不会运行——真实的首个 job 由上面的线程登记，前端靠 /generate/current 轮询。
    return {"ok": True, "count": count, "cot": cot_eff, "cot_note": cot_note, "job": None}


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
    # 评审 F3：排队中的换声任务只删工作目录、队列里那份参数元组还挂着——
    # src 一没就出队开跑，子进程失败，任务"删了又复活"成 error。先摘队列再删。
    # 正在转换的与生成任务同一口径：409，不许删。
    with _RVC_LOCK:
        rvc_job = _RVC_JOBS.get(rid)
    if rvc_job is not None:
        rvc_status = rvc_job.get("status")
        if rvc_status == "running":
            raise HTTPException(status_code=409,
                                detail="换声正在转换中，不能删除（等它跑完，或排队中先取消）")
        if rvc_status == "pending":
            if not _rvc_cancel(rid):
                raise HTTPException(status_code=409, detail="该任务刚好已开始转换，未能删除")
            with _RVC_LOCK:
                _RVC_JOBS[rid] = {**_RVC_JOBS.get(rid, rvc_job),
                                  "status": "cancelled", "queue_pos": 0}
    removed = False
    if _output_purge(rid):        # 白名单后缀全清（含 .elrc，手写列表以前就漏了它）
        removed = True
    if _rvc_job_purge(rid):       # 换声任务：原曲与分离 stem 留在 jobs/<id>/，不清就是 1.8G 孤儿
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
        st = json.loads(_BATCH_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"items": [], "running": False, "current": None}
    # 迁移：队列 ID 是后加的功能，老状态文件里的条目没有它。不补的话所有老条目都落进
    # 同一个空 qid 桶，「当前队列」会退化成全量历史（几十首看起来像又在重跑），
    # 而新提交被算成另一队。这里给缺 qid 的条目补一个确定性的旧队列身份。
    legacy = "legacy:" + str(st.get("name") or "旧队列")
    changed = False
    for it in st.get("items") or []:
        if not it.get("qid"):
            it["qid"] = legacy
            changed = True
    if changed and not st.get("qid"):
        st["qid"] = legacy
    return st


def _batch_write(state: dict) -> None:
    _BATCH_STATE.parent.mkdir(parents=True, exist_ok=True)
    # 蓝军 N-2：写一半被杀 = JSON 损坏 = 下次 _batch_read 吞异常返回空 = 整个队列历史静默清零。
    # 临时文件 + os.replace 才是原子发布（同目录，跨盘 rename 不原子）。
    tmp = _BATCH_STATE.with_name(_BATCH_STATE.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _BATCH_STATE)


_BATCH_LOCK = threading.Lock()
_BATCH_WORKER_LOCK = threading.Lock()   # 只护"判活+起线程"，与 _BATCH_LOCK 不同作用域
# 崩溃循环保护：同一条目在 _REVIVE_WINDOW_SEC 内被自愈复活超过 _REVIVE_MAX 次，
# 就停在 error 等人工，不再让看门狗陪着它一遍遍重启。
_REVIVE_WINDOW_SEC = 600
_REVIVE_MAX = 3
# 本进程的启动时刻：自愈文案要靠它分清"服务真重启过"与"工作线程自己结束了"。
# 没有这个基准，任何一条被改写成"服务重启，任务中断"都是没有证据的指控。
_PROC_START_TS = time.time()


def _batch_store(state: dict) -> None:
    """HTTP 侧的整份回写：调用方已经 _batch_snapshot()→改→这里写。

    与 worker 的用法区别要写清楚（蓝军 N-2）：worker 改成"锁内重读、只改自己那一格"，
    因为它持有的是跨几十分钟的老快照，整体回写会把期间用户追加的条目抹掉。
    HTTP 处理函数从读到了写只有几毫秒，且是用户自己点的那一下，窗口窄到可以接受；
    真要并发改队列（两个人同时删）才会互相覆盖——局域网多人用之前需要收敛成同一种写法。
    """
    with _BATCH_LOCK:
        _batch_write(state)
_BATCH_WORKER: threading.Thread | None = None


def _batch_snapshot() -> dict:
    with _BATCH_LOCK:
        return _batch_read()


def _batch_run_worker() -> None:
    """批量队列工作线程：逐个执行 pending 任务；失败记录后继续下一个。"""
    _CANCEL_EVENT.clear()
    while True:
        state = _batch_snapshot()
        if not state.get("running") or _CANCEL_EVENT.is_set():
            return
        nxt_id = next((it["id"] for it in state["items"] if it["status"] == "pending"), None)
        if nxt_id is None:
            with _BATCH_LOCK:
                state = _batch_read()
                state["running"] = False
                state["current"] = None
                _batch_write(state)
            return
        # 蓝军 N-4：先拿到 GPU 闸门，再把自己登记成"当前任务"。旧写法是先标 running、
        # 后排队，于是单首正在生成时提交的批量条目会立刻顶掉全局 current job——
        # 前端"当前任务"卡显示一条根本没在算的假 running，真在跑那首的结束也不再回显。
        with _GPU_SEM:  # 与单首生成/换声/训练互斥，防止并发打满显存
            if _CANCEL_EVENT.is_set():
                return
            # 蓝军 N-2：锁内重读、只改这一条。旧写法把循环开头那份快照整体写回，
            # 落在"快照之后、回写之前"的 batch_start 追加会被连根抹掉——用户已经收到
            # "已加入队列（排在第 N 位）"，条目却再也不存在，也没有任何报错。
            with _BATCH_LOCK:
                state = _batch_read()
                nxt = next((x for x in state["items"] if x["id"] == nxt_id), None)
                if nxt is None or nxt.get("status") != "pending":
                    continue        # 已被 stop/retry 改走，回头重看队列
                nxt["status"] = "running"
                nxt["started_ts"] = datetime.now().isoformat(timespec="seconds")
                state["current"] = nxt_id
                _batch_write(state)
                payload = dict(nxt["payload"])
                rid = nxt["id"]
                job = {
                    "id": rid,
                    "status": "running",
                    "ts": nxt["started_ts"],
                    "style": payload.get("style", "")[:_MAX_STYLE],
                    "lyrics": str(payload.get("lyrics") or "")[:_MAX_LYRICS],
                    "cot": payload.get("cot", "full"),
                    "abc": str(payload.get("abc") or "")[:_MAX_ABC],
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
            _gen_run(job, payload)
            if _CANCEL_EVENT.is_set():
                # 蓝军 N-5：取消必须落终态。旧写法在这里 break，条目卡在 running，
                # 下一轮 /batch/status 的自愈把它写成"服务重启，任务中断"——服务没重启过。
                with _BATCH_LOCK:
                    state = _batch_read()
                    it = next((x for x in state["items"] if x["id"] == nxt_id), None)
                    if it is not None and it.get("status") == "running":
                        it["status"] = "cancelled"
                        it["error"] = "已由用户取消"
                    state["current"] = None
                    _batch_write(state)
                return
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
    # 蓝军 N-6：判活与赋值必须同临界区。FastAPI 的同步路由跑在线程池里，
    # 并发 batch_start/resume/retry 可以同时看到"线程已死"，起了两个 worker
    # 重复执行同一条目（GPU 闸门只保证串行，不保证不重复跑第二遍）。
    global _BATCH_WORKER
    with _BATCH_WORKER_LOCK:
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
    old_items = state.get("items") or []
    # 队列里还有未跑完的条目才叫"追加排队"；全是已完成的历史条目时，这一批要开自己的
    # 队列身份（名字+ID），否则几天前的队列名会让新提交看着像旧任务重跑
    live = any(it.get("status") in ("pending", "running") for it in old_items)
    appending = live
    qname = str(payload.get("name") or "").strip() or datetime.now().strftime("%m%d-%H%M%S")
    if appending:
        # 排队进正在跑的队列 = 沿用那支队列的身份（ID 与名字），否则队列汇总会出现
        # 「名字是老的、总数只算刚追加的几首」这种新的误导
        qid = str(old_items[-1].get("qid") or "") or _new_id()
        qname = str(state.get("name") or "").strip() or qname
    else:
        qid = _new_id()
    items = []
    for i, t in enumerate(tasks, 1):
        style = str(t.get("style") or "").strip()
        lyrics = str(t.get("lyrics") or "").strip()
        if not style or not lyrics:
            raise HTTPException(status_code=400,
                                detail=f"第 {i} 个任务缺少 style 或 lyrics")
        # 与单首生成同一道长度闸门：整批里任何一条超限就整批退回，不静默截断
        _limit_text(f"第 {i} 个任务的曲风描述", style, _MAX_STYLE)
        _limit_text(f"第 {i} 个任务的歌词", lyrics, _MAX_LYRICS)
        _limit_text(f"第 {i} 个任务的乐谱", t.get("abc"), _MAX_ABC)
        rid = _new_id()
        abc_t = str(t.get("abc") or "")[:_MAX_ABC]
        # 同单首生成：缺省仍是 off（批量以快为先），但只要带了谱就不能让谱子落空
        cot_t, cot_note = _resolve_cot(str(t.get("cot") or "off"), abc_t)
        items.append({
            "id": rid,
            "qid": qid,
            "name": str(t.get("name") or f"{qname} #{i}")[:100],
            "status": "pending",
            "cot_note": cot_note,
            "payload": {
                "lyrics": lyrics, "style": style[:_MAX_STYLE],
                "cot": cot_t, "abc": abc_t,
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
            state["qid"] = qid
            _batch_write(state)
    else:
        # 旧条目全是已完成的历史：原样留在状态文件里（不删数据），但本批开新队列身份
        with _BATCH_LOCK:
            state = _batch_read()
            state["items"] = (state.get("items") or []) + items
            state.update({"running": True, "current": None, "name": qname, "qid": qid})
            _batch_write(state)
    _batch_ensure_worker()
    total = len(_batch_snapshot()["items"])
    msg = (f"已加入队列（排在第 {total - len(items) + 1}~{total} 位，当前任务完成后依次执行）"
           if appending else f"已开始批量任务（共 {len(items)} 首）")
    return {"ok": True, "queued": appending, "name": state.get("name") or qname,
            "queue_id": qid,
            "count": len(items), "total": total, "items": items, "message": msg,
            "cot_note": next((it["cot_note"] for it in items if it.get("cot_note")), "")}


@router.get("/batch/status")
def batch_status():
    state = _batch_snapshot()
    # 僵尸队列自愈：running=True 但 worker 线程已死（进程内异常退出/重启后标志未清）
    # 且当前没有真实任务在跑——复位标志并把卡死条目放回 pending，重新拉起 worker。
    worker_alive = _BATCH_WORKER is not None and _BATCH_WORKER.is_alive()
    gen_running = (_gen_get_job() or {}).get("status") == "running"
    if state.get("running") and not worker_alive and not gen_running:
        # 崩溃循环保护（第三方第三节-3）：如果某个条目本身就是把进程打崩的诱因
        # （超大歌词撑爆内存这类），自愈会把它放回 pending 并重启 worker，而看门狗
        # 又会把网关拉回来——每次首帧轮询复活一次，机器反复陪葬。判据用"短时间内的
        # 连续复活"而不是"复活过几次"：用户为了改配置正常重启两次不该被判死。
        now_ts = datetime.now()
        restored = 0
        for it in state["items"]:
            if it["status"] != "running":
                continue
            prev = None
            try:
                prev = datetime.fromisoformat(str(it.get("revive_ts"))) if it.get("revive_ts") else None
            except ValueError:
                prev = None
            rapid = prev is not None and (now_ts - prev).total_seconds() < _REVIVE_WINDOW_SEC
            n = int(it.get("revive_n") or 0) + 1 if rapid else 1
            if n > _REVIVE_MAX:
                it["status"] = "error"
                it["error"] = (f"连续 {n} 次在中途中断（{_REVIVE_WINDOW_SEC} 秒内复活），已停止自动重跑："
                               "该任务本身很可能就是崩溃诱因（内存/显存打爆），请单独重试或缩小输入")
                continue
            it["revive_ts"] = now_ts.isoformat(timespec="seconds")
            it["revive_n"] = n
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
        # 服务重启或工作线程死亡时，把遗留的 running 任务标记为中断。
        # 冒烟实测（09-26 22:17）：用户点"停止"后，正在算的那一首会在下一次轮询（十秒内）
        # 被这段代码写成"服务重启，任务中断"，而网关 PID 从头到尾没变过——指控无据。
        # 两条收口：① worker 线程还活着且这条就是它登记的 current，说明它仍在算，不动它；
        # ② 只有该条目早于本进程启动才有资格说"服务重启"，否则如实说工作线程没回写。
        worker_live = _BATCH_WORKER is not None and _BATCH_WORKER.is_alive()
        changed = False
        for it in state["items"]:
            if it["status"] != "running":
                continue
            if worker_live and state.get("current") == it["id"]:
                continue
            try:
                pre_start = datetime.fromisoformat(str(it.get("started_ts"))).timestamp() < _PROC_START_TS
            except (TypeError, ValueError):
                pre_start = False
            it["status"] = "error"
            it["error"] = ("服务重启，任务中断" if pre_start
                           else "队列已停止：该条目的计算线程未回写结果，请重跑这一条")
            changed = True
        if changed:
            _batch_store(state)
    done = sum(1 for it in state["items"] if it["status"] == "done")
    err = sum(1 for it in state["items"] if it["status"] == "error")
    # 「当前队列」= 最后一次提交的那一批（按 qid 分组）。历史已完成条目仍留在 items 里，
    # 但不能再被当成这次队列的总数——那正是"寻兰看起来在重跑"的来源。
    # 以 state["qid"] 为准（续跑/重试会往队尾塞条目，不能拿末条的 qid 当队头）
    cur_qid = str(state.get("qid") or "") or str((state["items"][-1].get("qid") if state["items"] else "") or "")
    cur = [it for it in state["items"] if (it.get("qid") or "") == cur_qid]
    return {
        "running": bool(state.get("running")),
        "name": state.get("name", ""),
        "queue": {"id": cur_qid, "name": state.get("name", ""),
                  "total": len(cur),
                  "done": sum(1 for it in cur if it["status"] == "done"),
                  "error": sum(1 for it in cur if it["status"] == "error"),
                  "pending": sum(1 for it in cur if it["status"] == "pending")},
        "queued_hist": len(state["items"]) - len(cur),
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
    with _BATCH_LOCK:      # 读-改-写必须在锁内：并发删除会各自基于旧快照回写，把对方的删除吃掉
        state = _batch_read()
        if rid == state.get("current") and state.get("running"):
            raise HTTPException(status_code=409, detail="该任务正在生成，不能删除")
        state["items"] = [it for it in state["items"] if it["id"] != rid]
        _batch_write(state)
    _output_purge(rid)
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
    # 兜底：G_*/D_* 是训练检查点、"." 开头是临时/备份中途货，都不该被列成可选音色
    # （以前试听导出会短暂往这里落一份 G_xxx.pth，下拉框里会冒出一个"杂音色"——评审 G3）
    return sorted(p.name for p in RVC_MODELS_DIR.glob("*.pth")
                  if not p.name.startswith((".", "G_", "D_")))


# 索引有两个家：训练写到 assets/indices（train_index.py 的外链目录），下载的音色放 logs 根。
# 推理侧自己也是先看 outside_index_root=assets/indices 再看 index_root=logs
# （runtime/rvc/infer/vc/utils.py:7-57）。以前网关只 glob logs/ 下的 added_*_名字_v2.index，
# 于是自己练出来的音色一律被体检报成"缺配套索引"，删音色时同名索引也留在 assets/indices 里没人认领。
def _rvc_index_dirs() -> tuple[Path, ...]:
    # 取用时再算：RVC_DIR / RVC_MODELS_DIR 可被环境变量或测试改写，写死在导入期就会指错地方
    return (RVC_MODELS_DIR.parent / "indices", RVC_DIR / "logs")


def _rvc_index_owned(stem: str, name: str) -> bool:
    """这个索引文件是否"就是"该音色的（删除/改名用严格判定）。

    查找侧（runtime 的 get_index_path_from_model）允许子串匹配，所以 王菲.pth 会
    把 王菲V6 的索引也列出来——用于"有没有索引"是对的，用于"删掉它"就是删别人的东西。
    这里只认以 `_音色名_v1/_v2` 收尾（允许多说话人的 `_spkidN` 尾巴）。"""
    low = name.lower()
    stem_l = stem.lower()
    head = low[: -len(".index")]
    head = re.sub(r"_spkid\d+$", "", head, flags=re.I)
    return head.endswith(f"_{stem_l}_v1") or head.endswith(f"_{stem_l}_v2")


def _rvc_index_files(stem: str, exact: bool = False) -> list[Path]:
    """按推理侧的查找规则返回该音色所有配套索引（两处目录、名字规则与 runtime 一致）。

    规则必须比"added_*_名字_v2.index"宽：训练产出的外链叫
    `名字_added_IVF..._名字_v2.index`（train_index.py:52-76 拼的前缀是音色名），
    只匹配 added_ 开头会把刚练完的音色判成缺索引。
    exact=True 换成"只认这个音色自己的"（删除/改名），见 _rvc_index_owned。

    排序不是小事：王菲 与 王菲V6 同时存在时，子串规则让 王菲 也匹配到
    `..._王菲V6_v2.index`，按文件名排序它排在前面——于是 王菲 换声实际用的是
    王菲V6 的检索库（音色串台）。这里把"严格属于本音色的"排在前面，
    宽松匹配的只做兜底，绝不盖过亲生索引。"""
    exp = re.sub(r"_e\d+_s\d+$", "", stem, flags=re.I).lower()
    own: list[Path] = []
    loose: list[Path] = []
    for d in _rvc_index_dirs():
        if not d.is_dir():
            continue
        found_own: list[Path] = []
        found_loose: list[Path] = []
        for root, _, files in os.walk(d, topdown=False):
            for name in files:
                low = name.lower()
                if not low.endswith(".index") or "trained" in low:
                    continue
                index_stem = low[: -len(".index")]
                spk = re.search(r"_spkid(\d+)$", index_stem, re.I)
                if spk and spk.group(1) != "0":
                    continue        # 多说话人索引，换声不指定说话人时 runtime 也不会选它
                if _rvc_index_owned(stem, name):
                    found_own.append(Path(root, name))
                elif not exact and (index_stem.startswith(exp + "_added_")
                                    or f"_{exp}_v1" in index_stem
                                    or f"_{exp}_v2" in index_stem
                                    or stem.lower() in index_stem):
                    found_loose.append(Path(root, name))
        own += sorted(found_own)
        loose += sorted(found_loose)
    return own + loose


def _rvc_index_for(model: str) -> Path | None:
    files = _rvc_index_files(Path(model).stem)
    return files[0] if files else None


def _rvc_require_index(model: str, index_rate: float) -> None:
    """提交前就把"没有配套索引"这件事说清楚（官方 WebUI 在点转换时做同一件事：webui.py:249-254）。

    不查的话失败点在十几分钟队列之后：runtime 里的 CLI 遇到 index_rate>0 而索引缺失是直接抛
    FileNotFoundError（infer/cli.py:152），用户看到的是"排队半天然后报错"，
    而且根本不知道该改参数还是该去补索引。"""
    if index_rate <= 0 or _rvc_index_for(model):
        return
    stem = Path(model).stem
    raise HTTPException(
        status_code=400,
        detail=f"音色「{stem}」没有配套的检索索引（在 assets/indices 与 logs 里按推理侧规则找过）。"
               f"把「音色检索强度」调到 0 可以只用模型本身换声（音色相似度会降），"
               f"或补上同名索引后再提交。",
    )


_RVC_F0_METHODS = ("rmvpe", "fcpe", "pm")   # 与推理 CLI choices 同步（infer/cli.py --f0-method）
_RVC_CAPS: dict | None = None
_RVC_FCPE_OK: bool | None = None


def _rvc_cli_caps() -> dict:
    """探测本机 vendored 推理 CLI 实际支持哪些旋钮（评审 H2）。

    为什么必须探：runtime/ 整目录不入库（.gitignore:2），这几处 CLI 扩展只存在于本机磁盘。
    哪天重装 runtime 或换机器，拿到的是上游原版——原版不认识 --filter-radius，
    而网关原先**无条件**把它拼进命令：argparse 直接 unrecognized arguments 退出码 2，
    等于每一单换声都失败。探测结果进程内缓存（一次 --help 约 0.1 秒）。"""
    global _RVC_CAPS
    if _RVC_CAPS is None:
        txt = ""
        try:
            r = subprocess.run([str(RVC_PY), str(RVC_DIR / "infer" / "cli.py"), "--help"],
                               capture_output=True, timeout=120,
                               env={**os.environ, "PYTHONPATH": str(RVC_DIR)},
                               creationflags=_pymss_creationflags())
            txt = (r.stdout or b"").decode("utf-8", "ignore")
        except Exception:
            txt = ""
        _RVC_CAPS = {"filter_radius": "--filter-radius" in txt, "fcpe": "fcpe" in txt}
    return _RVC_CAPS


def _rvc_fcpe_ok() -> bool:
    """torchfcpe 是否可用（评审 H3）。没有依赖时选 fcpe 照样能提交，
    排队之后才在 FCPEInfer 加载处炸——和索引缺失是同一族"晚爆的错误"。
    import torchfcpe 实测 5.4 秒，所以只在真选了 fcpe 时才探，结果进程内缓存。"""
    global _RVC_FCPE_OK
    if _RVC_FCPE_OK is None:
        _RVC_FCPE_OK = False
        try:
            r = subprocess.run([str(RVC_PY), "-c", "import torchfcpe"],
                               capture_output=True, timeout=180,
                               env={**os.environ, "PYTHONPATH": str(RVC_DIR)},
                               creationflags=_pymss_creationflags())
            _RVC_FCPE_OK = r.returncode == 0
        except Exception:
            _RVC_FCPE_OK = False
    return _RVC_FCPE_OK


# ===== 音域匹配（评审 C4）：让"换了但不好听"从玄学变成可诊断、可自动修正 =====

_RVC_F0STATS_LOCK = threading.Lock()
_RVC_F0STATS_MEM: dict[str, dict] = {}


def _rvc_f0_stats(model: str, refresh: bool = False) -> dict | None:
    """目标音色的舒适音域。**两个来源，优先级固定**（评审 J1）：
    ① 训练实测：读 logs/<stem>/2a_f0/*.npy（训练时 rmvpe 实测的每切片 f0），
      算全集中位数与 p5–p95，落 sidecar `f0_stats.json`，2a_f0 目录未变则复用；
    ② 参考音频建档：下载音色（本机 孙燕姿/王菲/邓丽君 等）没有训练素材，
      但用户可以上传该歌手的一段歌建档——sidecar 里 source="reference" 时照用。
    有 2a_f0 时**永远以训练实测为准**：建档接口会拒绝覆盖，取值也不看参考 sidecar，
    防止一个 12 秒片段把几百切片的实测数字降级。数字绝不跨音色冒充。"""
    stem = os.path.basename(str(model)).removesuffix(".pth")
    f0_dir = RVC_DIR / "logs" / stem / "2a_f0"
    cache = RVC_DIR / "logs" / stem / "f0_stats.json"

    def _reference_stats() -> dict | None:
        try:
            c = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            return None
        if c.get("source") == "reference" and c.get("median_hz"):
            return {k: v for k, v in c.items() if not k.startswith("_")}
        return None

    if not f0_dir.is_dir():
        return _reference_stats()
    try:
        stamp = max(p.stat().st_mtime_ns for p in f0_dir.glob("*.npy"))
    except ValueError:
        return _reference_stats()
    with _RVC_F0STATS_LOCK:
        mem = _RVC_F0STATS_MEM.get(stem) if not refresh else None
        if mem and mem.get("_stamp") == stamp:
            return {k: v for k, v in mem.items() if not k.startswith("_")}
    if cache.is_file() and not refresh:
        try:
            c = json.loads(cache.read_text(encoding="utf-8"))
            if c.get("_stamp") == stamp:
                c.setdefault("source", "training")  # 旧 sidecar 没标来源，补上
                with _RVC_F0STATS_LOCK:
                    _RVC_F0STATS_MEM[stem] = c
                return {k: v for k, v in c.items() if not k.startswith("_")}
        except Exception:
            pass
    import numpy as np
    parts, files = [], 0
    for f in sorted(f0_dir.glob("*.npy")):
        try:
            a = np.load(f)
            v = a[a > 0]  # 0 帧 = 无声/清音，音域统计不该把它们算进去
        except Exception:
            continue
        files += 1
        if v.size:
            parts.append(np.asarray(v, dtype=np.float64))
    if not parts:
        return None
    allv = np.concatenate(parts)
    stats = {"median_hz": round(float(np.median(allv)), 1),
             "p5_hz": round(float(np.percentile(allv, 5)), 1),
             "p95_hz": round(float(np.percentile(allv, 95)), 1),
             "frames": int(allv.size), "files": files, "source": "training"}
    with _RVC_F0STATS_LOCK:
        _RVC_F0STATS_MEM[stem] = {**stats, "_stamp": stamp}
    try:
        cache.write_text(json.dumps({**stats, "_stamp": stamp}, ensure_ascii=False),
                         encoding="utf-8")
    except Exception:
        pass
    return stats


# 源音域只分析前 5 分钟：dio 线性耗时，整首长跑只是让提交按钮多转几秒；
# 5 分钟足够覆盖主歌+副歌的音域分布，结果里如实带 `analyzed_sec`。
_RVC_PITCH_CAP_SEC = 300


def _rvc_f0_of_audio(path: Path) -> dict:
    """pyworld.dio 抽源音频 f0（纯 CPU，实测 3.7 秒素材 0.07 秒）。
    带伴奏的整曲会被伴奏污染——调用方负责在结果里说清这一点；
    换声 worker 里我们只对**分离后的人声**跑它，那个数字才是可信的。"""
    import math
    import numpy as np
    import soundfile as sf
    import pyworld as pw
    x, sr = sf.read(str(path), dtype="float32")
    if getattr(x, "ndim", 1) > 1:
        x = x.mean(axis=1)
    analyzed = min(len(x) / sr, _RVC_PITCH_CAP_SEC)
    x = x[:int(analyzed * sr)]
    if analyzed < 3:
        raise HTTPException(status_code=422, detail="音频太短（<3 秒），判不了音域")
    f0 = pw.dio(x.astype(np.float64), int(sr), frame_period=10.0)[0]
    voiced = f0[f0 > 0]
    if voiced.size < 100:
        raise HTTPException(status_code=422,
                            detail=f"可测出的有声帧太少（{int(voiced.size)}），"
                                   "音域判不了——素材里可能几乎没有人声")
    return {"median_hz": round(float(np.median(voiced)), 1),
            "p5_hz": round(float(np.percentile(voiced, 5)), 1),
            "p95_hz": round(float(np.percentile(voiced, 95)), 1),
            "voiced_frames": int(voiced.size),
            "analyzed_sec": round(analyzed, 1)}


def _rvc_pitch_suggestion(src_median: float, tgt_median: float,
                          pitch_used: int) -> dict:
    """建议变调 = 两个中位数的半音距离（±24 钳位，与换声闸门同口径）。"""
    import math
    semis = 12.0 * math.log2(float(tgt_median) / float(src_median))
    pitch = max(-24, min(24, int(round(semis))))
    diff = pitch - int(pitch_used)
    return {"suggested_pitch": pitch, "raw_semis": round(semis, 2),
            "delta": diff,
            "apply": diff != 0 and abs(semis) >= 1.0}


@router.post("/rvc/pitch/advice")
async def rvc_pitch_advice(file: UploadFile, model: str = Form(...)):
    """换声前的音域体检：量源唱音域 → 对照目标音色舒适音域 → 给一句可执行的变调建议。
    不占 GPU、不进换声队列，几秒出结果。三档诚实：
    ① 双方都有数据 → 建议；② 目标音色是外部下载（无训练 f0）→ 只报源音域，说明没法建议；
    ③ 源带伴奏 → 数字可能被伴奏带偏，提示改用干声或看换声后 meta 里的可信值。"""
    models = _rvc_models()
    if model not in models:
        raise HTTPException(status_code=400, detail=f"未知音色模型：{model}（可用：{models}）")
    ext = Path(file.filename or "in.wav").suffix.lower()
    if ext not in _RVC_AUDIO_EXTS:
        ext = ".wav"
    rid = _new_id()
    work = RVC_JOB_DIR / f"pitch_{rid}"
    work.mkdir(parents=True, exist_ok=True)
    probe = work / f"src{ext}"
    try:
        await _stream_upload_to(file, probe, 200 * 1024 * 1024, "音频")
        src = _rvc_f0_of_audio(probe)
        tgt = _rvc_f0_stats(model)
        out = {"ok": True, "source": src, "target": tgt, "model": model}
        if tgt:
            out["suggestion"] = _rvc_pitch_suggestion(
                src["median_hz"], tgt["median_hz"], 0)
            n = out["suggestion"]["suggested_pitch"]
            tgt_cn = ("参考音频实测" if tgt.get("source") == "reference"
                      else "训练素材实测")
            out["text"] = (f"源唱中位 {src['median_hz']:.0f}Hz，音色「{model}」舒适音域中位 "
                           f"{tgt['median_hz']:.0f}Hz（{tgt_cn}）→ 建议变调 "
                           f"{n:+d} 半音（音域差 {out['suggestion']['raw_semis']:+.1f}）")
        else:
            out["text"] = (f"源唱中位 {src['median_hz']:.0f}Hz；音色「{model}」没有音域数据"
                           "（外部下载的音色没有训练素材），无法给变调建议——"
                           "在音色卡片上给它传一段该歌手本人的歌（10~30 秒即可）建档后即可。")
        out["caveat"] = ("整曲带伴奏时 f0 会被伴奏污染，此数字仅供参考；"
                         "开「先人声分离」跑完后，成品 meta 里会带可信的源音域与最终建议。")
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


@router.post("/rvc/models/{name}/f0-reference")
async def rvc_model_f0_reference(name: str, file: UploadFile):
    """给外部下载音色建"参考音域"档案（评审 J1）。本机 4 个现役音色全是下载的、
    没有训练素材，2a_f0 永远为空 → C4 的变调建议对它们 0% 生效。唯一可信的补救是
    **这个本人的一段干净人声**（不能拿换声产物反推——那是源音高的镜像，不含目标音色信息，
    评审实测两个不同音色产物中位只差 0.09 半音）。传一段该音色本人的歌（建议干声，
    10~30 秒足够）→ 复用 _rvc_f0_of_audio 算中位/p5–p95 → 落 sidecar f0_stats.json，
    标记 source="reference"。此后 _rvc_f0_stats 的取值顺序：训练 2a_f0 实测 → 参考档案 → None。
    诚实口径：参考数字绝不冒充训练实测，卡片与文案都标注来源；已有训练实测的音色不许被
    参考档案覆盖（端点直接拒绝——一段 12 秒片段不许降级几百切片的实测）。"""
    name = os.path.basename(name)
    if name not in _rvc_models():
        raise HTTPException(status_code=404,
                            detail=f"音色模型不存在：{name}（可用：{_rvc_models()}）")
    stem = name.removesuffix(".pth")
    f0_dir = RVC_DIR / "logs" / stem / "2a_f0"
    if any(f0_dir.glob("*.npy")):
        raise HTTPException(status_code=409, detail=(
            f"「{stem}」已有本机训练素材的音域实测，不用也不许用参考音频建档覆盖"))
    ext = Path(file.filename or "in.wav").suffix.lower()
    if ext not in _RVC_AUDIO_EXTS:
        ext = ".wav"
    rid = _new_id()
    work = RVC_JOB_DIR / f"pitchref_{rid}"
    work.mkdir(parents=True, exist_ok=True)
    probe = work / f"ref{ext}"
    try:
        await _stream_upload_to(file, probe, 60 * 1024 * 1024, "参考音频")
        stats = _rvc_f0_of_audio(probe)  # 有声帧太少会在里面直接 422，不编数
        sidecar = RVC_DIR / "logs" / stem / "f0_stats.json"
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        # _stamp 用参考音频文件的 mtime（评审方案）：与训练 2a_f0 的 npy 戳共用一把尺，
        # 换一段参考文件重建档会自然失效旧的 sidecar 缓存判定。
        stamp = probe.stat().st_mtime_ns
        record = {**stats, "source": "reference", "_stamp": stamp,
                  "ref_name": Path(file.filename or "ref").name}
        sidecar.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        with _RVC_F0STATS_LOCK:
            _RVC_F0STATS_MEM.pop(stem, None)  # 让下次读取走新档案
        return {"ok": True, "model": name,
                "stats": {k: v for k, v in record.items() if not k.startswith("_")}}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _rvc_pitch(raw) -> int:
    """变调半音：非整数直接拒，越界也拒（页面上的滑杆就是 ±24）。"""
    try:
        v = float(raw if raw not in (None, "") else 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"变调须是整数半音，收到：{raw!r}")
    if not -24 <= v <= 24:
        raise HTTPException(status_code=400, detail=f"变调须在 -24~24 半音之间，收到 {raw!r}")
    if v != int(v):
        raise HTTPException(status_code=400, detail=f"变调须是整数半音（不能 4.5），收到 {raw!r}")
    return int(v)


def _rvc_convert_params(model: str, f0_method, index_rate, protect, rms_mix_rate,
                        filter_radius=None, resample_sr=None):
    """换声参数的统一闸门，返回规整后的 (f0_method, index_rate, protect, rms_mix_rate,
    filter_radius, resample_sr)。

    为什么要在入队前拦：这些数会一路带到十几分钟之后开跑的子进程命令行里。变调算法写错是
    argparse 直接退出（用户只看到一句"转换失败"）；索引缺失是 FileNotFoundError；
    比例越界更糟——不报错，而是算出负权重的混合，用户听到的是"声音怪"却查不出为什么。
    以前只有体检（GET）在找索引，两个提交入口（上传、历史页送去换声）各查各的、还漏查。"""
    f0_method = str(f0_method or "rmvpe").strip().lower()
    if f0_method not in _RVC_F0_METHODS:
        raise HTTPException(status_code=400,
                            detail=f"未知变调算法：{f0_method}（可用：{'、'.join(_RVC_F0_METHODS)}；"
                                   f"rmvpe 通用首选，fcpe 新一代更快更准，pm 最快但容易飘）")
    if f0_method == "fcpe":
        # 不许悄悄降级成 rmvpe：那是"点 A 得 B"，音高算法换了听感就变了。
        # 能力缺失只能在提交时说清楚（评审 H2/H3）。
        if not _rvc_cli_caps().get("fcpe"):
            raise HTTPException(status_code=400,
                                detail="本机 RVC 推理 CLI 不支持 fcpe（runtime 可能被重装成未打补丁的"
                                       "上游原版）：换 rmvpe，或按 patches/rvc-infer/ 的说明恢复补丁")
        if not _rvc_fcpe_ok():
            raise HTTPException(status_code=400,
                                detail="fcpe 需要 torchfcpe 依赖，本机 import 失败："
                                       "py312\\python.exe -m pip install torchfcpe，或改用 rmvpe")
    out = []
    for label, raw, lo, hi, dflt in (("音色检索强度", index_rate, 0.0, 1.0, 0.75),
                                     ("protect", protect, 0.0, 0.5, 0.33),
                                     ("音量对齐强度", rms_mix_rate, 0.0, 1.0, 1.0)):
        try:
            v = float(dflt if raw is None or raw == "" else raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"{label}须是数字，收到：{raw!r}")
        if not lo <= v <= hi:
            raise HTTPException(status_code=400,
                                detail=f"{label}须在 {lo}-{hi} 之间，收到 {v}")
        out.append(v)
    # 音高平滑半径（官方 WebUI 同名旋钮，评审 C2）：>2 起效，社区口径"默认 3，
    # 哑音毛刺明显时 5~7，调大发闷"；0-2 一律视为关闭。
    try:
        fr = int(3 if filter_radius is None or filter_radius == "" else filter_radius)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400,
                            detail=f"音高平滑半径须是整数，收到：{filter_radius!r}")
    if not 0 <= fr <= 7:
        raise HTTPException(status_code=400, detail=f"音高平滑半径须在 0-7 之间，收到 {fr}")
    # 评审 H1：scipy.signal.medfilt 要求核为奇数，偶数核在 pipeline 里直接抛
    # ValueError: Each element of kernel_size should be odd（实测 --filter-radius 4 整条换声崩）。
    # 页面是 step=1 的 number 输入，4、6 随手能填且能过闸门，所以在这里钳到下一个奇数
    # （4→5、6→7）；≤2 本就不触发滤波（0/1/2 一律视为关闭），不动它。
    if fr > 2 and fr % 2 == 0:
        fr += 1
    # 输出采样率（评审 C3）：0=跟随模型原生。40k 模型的 Nyquist 就是 20kHz，
    # 强行上采样到 48k 不增加任何信息，只多一次重采样失真——默认必须是 0。
    try:
        rsr = int(0 if resample_sr is None or resample_sr == "" else resample_sr)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400,
                            detail=f"输出采样率须是整数，收到：{resample_sr!r}")
    if rsr != 0 and not 16000 <= rsr <= 192000:
        raise HTTPException(status_code=400,
                            detail=f"输出采样率只接受 0（模型原生）或 16000-192000，收到 {rsr}")
    _rvc_require_index(model, out[0])
    return f0_method, out[0], out[1], out[2], fr, rsr


def _rvc_model_in_use(model: str) -> bool:
    """该音色是否正被某个换声任务使用（含排队中的：删了它，轮到它跑时会找不到模型）。"""
    with _RVC_LOCK:
        return any(
            j.get("status") in ("running", "pending") and j.get("model") == model
            for j in _RVC_JOBS.values()
        )


@router.get("/rvc/models")
def rvc_models():
    items = []
    for p in sorted(RVC_MODELS_DIR.glob("*.pth")) if RVC_MODELS_DIR.is_dir() else []:
        items.append({
            "name": p.name,
            "size_mb": round(p.stat().st_size / 1e6, 1),
            "mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            # 音域卡片（评审 C4）：只有本机练过的音色有；None 时页面显示"无音域数据"
            "f0_range": _rvc_f0_stats(p.name),
        })
    return {"models": [i["name"] for i in items], "items": items}


_RVC_CHECK_LOCK = threading.Lock()  # 体检串行化：大模型加载费内存，防连点叠加
# 蓝军 Y5：体检要另起训练环境子进程 torch.load 整个 pth（可达数十秒、最长 300 秒超时），
# 而它挂在 GET 上——前端列表每刷新一次就重来一遍。按「文件指纹（mtime+size）」缓存成功结果：
# 模型文件没换过就直接回缓存，?force=1 强制重测，文件被重训覆盖后指纹变了自动失效。
_RVC_CHECK_CACHE: dict[str, tuple] = {}


@router.get("/rvc/models/{name}/check")
def rvc_model_check(name: str, force: bool = False):
    """模型体检：加载 pth 校验结构完整性、采样率/f0/版本，并检查配套 index 是否存在。"""
    name = os.path.basename(name)
    p = RVC_MODELS_DIR / name
    if not p.is_file():
        raise HTTPException(status_code=404, detail="音色模型不存在")
    st = p.stat()
    stamp = (st.st_mtime_ns, st.st_size)
    if not force:
        hit = _RVC_CHECK_CACHE.get(name)
        if hit and hit[0] == stamp:
            return dict(hit[1], cached=True)
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
    idx = _rvc_index_files(p.stem)
    checks["配套索引"] = bool(idx)
    ok = all(checks.values())
    payload = {
        "name": name, "ok": ok, "checks": checks, "info": info,
        "index": idx[0].name if idx else None,
        "size_mb": round(st.st_size / 1e6, 1),
        "summary": "体检通过" if ok else "存在问题：" + "、".join(k for k, v in checks.items() if not v),
    }
    _RVC_CHECK_CACHE[name] = (stamp, payload)   # 只缓存跑通的结果；加载失败/超时下次仍会重测
    return payload


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
    # 顺带清理同名索引（两处目录都要清，否则 assets/indices 里留成孤儿文件）。
    # exact=True：查找侧允许子串匹配（王菲 会把 王菲V6 的索引也列出来），
    # 删东西时必须只认这个音色自己的，不然就是删邻居的文件。
    for idx in _rvc_index_files(p.stem, exact=True):
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
    # 同步重命名索引文件（两处目录都跟着改；exact=True 只动这个音色自己的，
    # 不然改名 王菲 会把 王菲V6 的索引一起改掉）
    for idx in _rvc_index_files(p.stem, exact=True):
        try:
            idx.rename(idx.with_name(idx.name.replace(f"_{p.stem}_", f"_{Path(new_name).stem}_")))
        except Exception:
            pass
    # 音域档案（评审 C4）跟着改名走：sidecar 在 logs/<名>/f0_stats.json，
    # 不搬的话改完名换声页就会说"该音色没有音域数据"——数据明明练过。
    old_side = RVC_DIR / "logs" / p.stem / "f0_stats.json"
    if old_side.is_file():
        try:
            dst_dir = RVC_DIR / "logs" / Path(new_name).stem
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_side), str(dst_dir / "f0_stats.json"))
        except Exception:
            pass
    with _RVC_F0STATS_LOCK:
        _RVC_F0STATS_MEM.pop(p.stem, None)
        _RVC_F0STATS_MEM.pop(Path(new_name).stem, None)
    return {"ok": True, "name": new_name}


# 人声分离：官方 RVC23 的 PyMSS 框架（BS-Roformer-Resurrection，onnxruntime 加速，
# 实测 3.5 分钟歌约 1.5 分钟，比 GPT-SoVITS 的 UVR5 两步链快且人声更干净）。
# 产物命名与旧链一致（<stem>_vocals.wav / <stem>_other.wav），下游混音/下载逻辑不变。
PYMSS_ROOT = RVC_DIR / "tools"
PYMSS_MODEL = "BS-Roformer-Resurrection"
# 旧链路（GPT-SoVITS 的 BS-RoFormer + HP5）保留作降级。
# 这是一份**外部**安装（不在本仓库内），默认路径是作者机；换机器时用环境变量
# YUE2_GSV_ROOT 指到自己的 GPT-SoVITS 目录即可，未配置且默认路径不存在时
# 会明确报错，而不是拿一个别人机器上的路径去撞。
def _gsv_root() -> Path:
    """外部 GPT-SoVITS 安装位置：env 优先，未配置才回落到默认路径（写成正经函数，
    是为了让回归用例能真的验证 env 生效，而不是只在源码里 grep 到变量名）。"""
    return Path(os.environ.get("YUE2_GSV_ROOT")
                or r"E:\AI\10AIMusic\GPT-SoVITS-v2pro-20250604")


GSV_ROOT = _gsv_root()
GSV_PY = GSV_ROOT / "runtime" / "python.exe"
SEP_ROFORMER = GSV_ROOT / "sep_roformer.py"
SEP_HP5 = GSV_ROOT / "sep_hp5.py"


def _gsv_chain_available() -> bool:
    return GSV_PY.is_file() and SEP_ROFORMER.is_file() and SEP_HP5.is_file()


def vocal_sep_available() -> bool:
    return (RVC_DIR / "tools" / "pymss" / "workflow.py").is_file()


def _pymss_model_dir() -> Path:
    """PyMSS 权重解析顺序（与 tools/pymss/model_registry.py:_default_model_dir 一致）：
    环境变量 PYMSS_MODEL_DIR → 仓库内 tools/all_models → ~/.cache/pymss/models。"""
    env = os.environ.get("PYMSS_MODEL_DIR")
    if env:
        return Path(env)
    repo = RVC_DIR / "tools" / "all_models"
    if repo.is_dir():
        return repo
    return Path.home() / ".cache" / "pymss" / "models"


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


# --------------------------------------------------------------------------- #
# 静音门（gate）：RVC 对"没有人声的片段"并不会输出静音——实测纯静音输入会被模型
# "哼"成一个恒定音（221s 歌的前 28 秒原人声为数字零，换声输出却持续 -26dB、主频
# 279Hz 的蜂鸣）。所以必须以"真正送进 RVC 的那个人声"的包络为准，把换声结果的
# 无人声段压下去；同时用 HP5 去和声（和声会让音高跟踪在高音处跳轨 → 电音）。
# --------------------------------------------------------------------------- #
def _frame_db(mono: "np.ndarray", n: int, starts: "np.ndarray", block: int = 4096) -> "np.ndarray":
    """逐帧 RMS dB。分块计算：内存 O(块 × 窗长)，不随音频时长线性增长。

    评审 F1：旧实现 np.stack 全展开成 float64，600s 音频在网关进程里顶出 1.06 GB
    瞬时峰值，绕过了所有 headroom 闸门（本机有 OOM 前科）。float32 精度对 dB
    判定足够（1e-12 下限本就是 ~240dB 的余量）。"""
    import numpy as np

    out = np.empty(len(starts), dtype=np.float32)
    for i in range(0, len(starts), block):
        blk = np.stack([mono[s:s + n] for s in starts[i:i + block]]).astype(np.float32)
        out[i:i + block] = 20 * np.log10(np.clip(np.sqrt((blk ** 2).mean(axis=1)), 1e-12, None))
    return out


def _gate_envelope(ref: "np.ndarray", sr: int, *, win_s: float = 0.02,
                   hop_s: float = 0.01, attack_ms: float = 6.0,
                   release_ms: float = 140.0, hold_frames: int = 8,
                   offset_s: float = 0.03) -> tuple["np.ndarray", float, "np.ndarray"]:
    """按参考人声的能量包络算逐样本门控增益（0~1）。

    阈值取「参考信号 90 分位帧能量 − 38dB」并夹在 [-75, -45]，开帧后保持 hold_frames
    帧（避免咬字间的短促停顿被切），开→关走 release、关→开走 attack 的指数平滑。
    offset_s 把整条控制线往前挪一点点：RVC 输出相对输入有少量延迟，不补偿会吃掉
    每句的第一个音。返回 (逐样本增益, 阈值dB, 逐帧有人声布尔)。"""
    import numpy as np

    mono = ref.mean(axis=1) if ref.ndim > 1 else ref
    n = max(1, int(sr * win_s))
    hop = max(1, int(sr * hop_s))
    starts = np.arange(0, max(1, len(mono) - n + 1), hop)
    if starts.size == 0:
        starts = np.array([0])
    db = _frame_db(mono, n, starts)
    thr = float(np.clip(np.percentile(db, 90) - 38.0, -75.0, -45.0))
    close_thr = thr - 6.0
    open_ = db >= thr
    # 保持：开帧后 hold_frames 内仍视为开
    target = np.zeros(len(db), dtype=np.float64)
    hold = 0
    for i, is_open in enumerate(open_):
        if is_open:
            hold = hold_frames
        if hold > 0:
            target[i] = 1.0
            hold -= 1
        elif db[i] < close_thr:
            target[i] = 0.0
        else:
            target[i] = target[i - 1] if i else 0.0
    # 指数平滑（attack 用于 0→1，release 用于 1→0）
    a_atk = 1.0 - float(np.exp(-hop / max(1.0, sr * attack_ms / 1000.0)))
    a_rel = 1.0 - float(np.exp(-hop / max(1.0, sr * release_ms / 1000.0)))
    prev = target[0]
    smooth = np.empty_like(target)
    for i, t in enumerate(target):
        a = a_atk if t > prev else a_rel
        prev = prev + a * (t - prev)
        smooth[i] = prev
    centers = starts + n / 2.0 + sr * offset_s
    # 逐样本增益同样分块落值：一次性 np.interp 会造出 int64 下标 + float64 输出
    # 两份 8 字节临时数组（评审 F1 同源问题），分块后只剩 float32 结果本身。
    gain = np.empty(len(mono), dtype=np.float32)
    bs = 1 << 20
    for i in range(0, len(gain), bs):
        j = min(i + bs, len(gain))
        gain[i:j] = np.interp(np.arange(i, j, dtype=np.float64), centers, smooth)
    return gain, thr, open_


def _apply_silence_gate(voc: "np.ndarray", ref: "np.ndarray", ref_sr: int, *,
                        depth_db: float = -60.0) -> tuple["np.ndarray", dict]:
    """用 ref（真正送进 RVC 的那个人声）的包络门控 voc（换声结果）。

    返回 (门控后的音频, 统计+逐帧有人声掩码)。统计里的掩码供后续响度校准只按
    有人声的片段算 RMS——否则被关掉的静音段会把整体响度算低，把人声越推越小。"""
    import numpy as np

    gain, thr, open_ = _gate_envelope(ref, ref_sr)
    floor = float(10 ** (depth_db / 20.0))
    gain = floor + (1.0 - floor) * gain
    if len(gain) != len(voc):
        # 换声输出的采样数/采样率与输入不完全一致：按时间轴归一重采样控制线
        gain = np.interp(np.linspace(0.0, 1.0, len(voc)),
                         np.linspace(0.0, 1.0, len(gain)), gain).astype(np.float32)
    out = (voc * gain[:, None]) if voc.ndim > 1 else voc * gain
    closed_ratio = float((gain <= floor + 1e-6).mean())
    return out.astype(np.float32), {
        "gate_thr_db": round(thr, 1),
        "gate_depth_db": depth_db,
        "gate_closed_ratio": round(closed_ratio, 3),
        "gate_open_frames": open_,
    }


def _mask_to_signal(mask: "np.ndarray", n: int) -> "np.ndarray":
    """把逐帧（10ms）有人声掩码按时间轴展开到 n 个采样点的布尔掩码。"""
    import numpy as np

    if mask is None or len(mask) == 0:
        return np.ones(n, dtype=bool)
    grid = np.linspace(0.0, 1.0, len(mask))
    return np.interp(np.linspace(0.0, 1.0, n), grid, mask.astype(np.float64)) >= 0.5


def _mix_vocal_accompaniment(voc: "np.ndarray", acc: "np.ndarray") -> "np.ndarray":
    """人声与伴奏相加。声道数不一致时只允许「单声道升到立体声」，
    绝不把立体声伴奏压成单声道（旧写法会把 2 声道伴奏截成 1 声道，成品丢立体声）。"""
    import numpy as np

    n = max(voc.shape[0], acc.shape[0])
    if voc.shape[0] < n:
        voc = np.pad(voc, ((0, n - voc.shape[0]), (0, 0)))
    if acc.shape[0] < n:
        acc = np.pad(acc, ((0, n - acc.shape[0]), (0, 0)))
    if voc.shape[1] != acc.shape[1]:
        if voc.shape[1] == 1:
            voc = np.repeat(voc, acc.shape[1], axis=1)
        elif acc.shape[1] == 1:
            acc = np.repeat(acc, voc.shape[1], axis=1)
        else:
            voc = voc[:, :acc.shape[1]]
    return voc + acc


def _run_harmony_strip(vocal_path: Path, sep_dir: Path, job: dict) -> Path | None:
    """HP5 去和声，只留主唱；不可用或失败返回 None（调用方继续用含和声的人声）。"""
    if not (GSV_PY.is_file() and SEP_HP5.is_file()):
        return None
    job["sep_stage"] = "去除和声（HP5，只留主唱）"
    try:
        r = subprocess.run(
            [str(GSV_PY), str(SEP_HP5), str(vocal_path), str(sep_dir)],
            capture_output=True, timeout=3600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    return next((v for v in sorted(sep_dir.glob("vocal_*.wav"),
                                   key=lambda p: p.stat().st_mtime, reverse=True)), None)


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


def _run_vocal_separation(src: Path, in_dir: Path, job: dict,
                          strip_harmony: bool = False) -> tuple[Path, dict]:
    """人声分离：首选官方 PyMSS 一步分离（人声+伴奏）；PyMSS 不可用时降级旧两步链。

    strip_harmony=True 时，PyMSS 分离出的人声再过一遍 HP5 只留主唱——和声（副歌叠唱、
    双人声）会让 RVC 的音高跟踪跳轨，是换声后高音处出电音的主要可修诱因。

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
             "-i", str(src), "-o", str(sep_dir), "--device", backend_mode()],
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
                # PyMSS 的人声声部含和声；默认直接送 RVC（一步分离已足够干净），
                # 需要只留主唱时（strip_harmony）再走 HP5，产物 vocals_raw 仍是含和声那份
                if strip_harmony:
                    main_vocal = _run_harmony_strip(vocals, sep_dir, job)
                    if main_vocal is not None:
                        return main_vocal, artifacts
                return vocals, artifacts
            tail = (r.stderr or r.stdout or b"")[-300:].decode("utf-8", "replace")
            raise RuntimeError(f"人声分离（PyMSS）未产出完整产物：{tail}")
        tail = (r.stderr or r.stdout or b"")[-300:].decode("utf-8", "replace")
        # PyMSS 失败（模型未下载/断网等）→ 降级旧两步链
        job["sep_stage"] = "PyMSS 失败，降级旧分离链（BS-RoFormer + HP5）"
        _pymss_err = tail
    else:
        _pymss_err = "PyMSS 模块缺失"
    if not _gsv_chain_available():
        # 以前这里直接拿作者机路径去 subprocess，换机器就是一条 FileNotFoundError；
        # 现在如实说明缺什么、怎么指路
        raise RuntimeError(
            f"人声分离不可用：PyMSS 未成功（{_pymss_err[:160]}），且本机未找到旧分离链 GPT-SoVITS"
            f"（当前指向 {GSV_ROOT}；装过 GPT-SoVITS 的机器设环境变量 YUE2_GSV_ROOT 指过去，"
            "或安装 runtime/rvc/tools/pymss）")
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

    # 步骤 2：HP5 去和声（只留主唱）；HP5 失败时降级用含和声的人声继续（比带伴奏好）
    return _run_harmony_strip(vocals, sep_dir, job) or vocals, artifacts


def _rvc_mark_running(rid: str, job: dict) -> None:
    """GPU 真到手的一刻才标 running 并起表。

    出队 ≠ 开算：`_GPU_SEM` 还被歌曲生成/批量/音色训练占着的时候（训练整条流程都持锁，
    可以是几十分钟），提前写 running 就等于把等锁的时间画成转换进度——正是这次要修的
    "明明在等却说在转换"。与批量 worker 同一口径：先拿锁，再登记在算（app.py:1997）。"""
    with _RVC_LOCK:
        cur = _RVC_JOBS.get(rid) or job
        if cur.get("status") == "running":
            return                      # 分离阶段已经起过表，推理不再重算
        _RVC_JOBS[rid] = {**cur, "status": "running", "queue_pos": 0, "step": "",
                          "started_ts": datetime.now().isoformat(timespec="seconds")}


def _rvc_convert_worker(rid: str, job: dict, src: Path, in_dir: Path,
                        model: str, pitch: int, f0_method: str,
                        index_rate: float, protect: float, rms_mix_rate: float,
                        separate_vocal: bool = False, gate: bool = True,
                        strip_harmony: bool = False,
                        filter_radius: int = 3, resample_sr: int = 0) -> None:
    """换声推理 worker（上传入口与历史转发入口共用；由 _rvc_queue_loop 串行调度）。"""
    started: float | None = None   # 拿到 GPU 才起表：等生成/训练放锁的时间不是转换耗时

    def _start_watch() -> None:
        nonlocal started
        if started is None:
            started = time.time()

    def _elapsed() -> float:
        return round(time.time() - (started or time.time()), 1)

    try:
        # 轮到它了但还没拿到 GPU：保持 pending，只把原因写清楚，表留给 _rvc_mark_running
        with _RVC_LOCK:
            _RVC_JOBS[rid] = {**_RVC_JOBS.get(rid, job), "queue_pos": 0,
                              "step": "等待本机空闲（生成/批量/训练正占用 GPU）"}
        artifacts: dict[str, str] = {}
        if separate_vocal:
            # 分离同样吃显存（BS-Roformer 大模型），而它在下面的推理信号量之外，
            # 所以单独占一次锁、跑完即放：换声排队期间不至于撞上正在生成的歌曲。
            with _GPU_SEM:
                _rvc_mark_running(rid, job)
                _start_watch()
                src, artifacts = _run_vocal_separation(src, in_dir, job, strip_harmony)
        out_name = "converted.wav"
        cmd = [
            str(RVC_PY), str(RVC_DIR / "infer" / "cli.py"),
            "--model", model,
            "--input", str(src), "--output", str(in_dir / out_name),
            "--pitch", str(int(pitch)), "--f0-method", f0_method,
            "--index-rate", str(index_rate), "--protect", str(protect),
            "--rms-mix-rate", str(rms_mix_rate),
            # 输出采样率：0 = 跟随模型原生（评审 C3）。以前的注释写着"40k 直出会损失高频"，
            # 前提是错的：40k 模型的 Nyquist 就是 20kHz，上采样到 48k 不增加任何信息；
            # 混音环节本来就会按伴奏采样率对齐，不需要在这里提前统一。
            "--resample-sr", str(int(resample_sr)),
            "--overwrite",
        ]
        # 音高轨迹中值滤波半径（官方 WebUI 同名旋钮，评审 C2）：默认 3，
        # 哑音/毛刺/断续明显时 5~7，调大发闷，<3 关闭。
        # 只在探测到 CLI 认识这个参数时才传（评审 H2）：runtime 不入库，重装后是上游原版，
        # 无条件传会 argparse 退出码 2、每一单换声都失败。不支持时明写在任务上，不许静默。
        if _rvc_cli_caps().get("filter_radius"):
            cmd += ["--filter-radius", str(int(filter_radius))]
        else:
            job["caps_warn"] = ("本机 RVC 运行时不支持音高平滑（--filter-radius），此步已跳过；"
                                "要恢复请按 patches/rvc-infer/ 说明打补丁")
            with _RVC_LOCK:
                cur = _RVC_JOBS.get(rid)
                if cur is not None:
                    cur["caps_warn"] = job["caps_warn"]
        # 索引必须点名给：不给时 runtime 自己按"子串"猜（infer/vc/utils.py:7-57），
        # 本机 王菲 与 王菲V6 并存时，按文件名排序 王菲V6 的外链排在前面——
        # 于是选 王菲 实际用了 王菲V6 的检索库，两个名字听起来是同一个人在唱。
        idx = _rvc_index_for(model) if index_rate > 0 else None
        if idx:
            cmd += ["--index", str(idx)]
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
            _rvc_mark_running(rid, job)
            _start_watch()
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
        gate_info: dict = {}
        gate_mask = None
        if gate:
            # 静音门：以"真正送进 RVC 的人声"为参考，把换声结果里没人声的片段压到 -60dB。
            # 不做这一步，前奏/间奏会顶着模型自己哼出来的恒定音（实测 -26dB / 279Hz）。
            try:
                import soundfile as sf
                import numpy as np
                voc_raw, sr_v = sf.read(str(out_path), dtype="float32", always_2d=True)
                try:
                    ref, sr_r = sf.read(str(src), dtype="float32", always_2d=True)
                except Exception:
                    import librosa
                    ref, sr_r = librosa.load(str(src), sr=None, mono=False)
                    ref = np.asarray(ref, dtype="float32")
                    if ref.ndim == 1:
                        ref = ref[:, None]
                gated, gate_info = _apply_silence_gate(voc_raw, ref, sr_r)
                gate_mask = gate_info.pop("gate_open_frames", None)
                sf.write(str(wav), gated, sr_v)
            except Exception:
                gate_info = {}
                gate_mask = None
        if not gate_info:
            wav.write_bytes(out_path.read_bytes())
        meta = {
            **job, "status": "done", "sec": _elapsed(),
            "bytes": wav.stat().st_size, "file": wav.name, "kind": "rvc",
            "style": f"RVC 换声 · {model}", "lyrics": f"源音频：{job['src_name']}",
            "cot": "rvc", "params": {"pitch": pitch, "f0_method": f0_method,
                                      "index_rate": index_rate, "protect": protect,
                                      "rms_mix_rate": rms_mix_rate,
                                      "filter_radius": filter_radius,
                                      "resample_sr": resample_sr,
                                      "separate_vocal": bool(separate_vocal),
                                      "strip_harmony": bool(strip_harmony),
                                      "gate": bool(gate)},
        }
        if gate_info:
            meta["gate"] = gate_info
        # 音域留档（评审 C4）：这里量的 src 是**分离后真正送进 RVC 的人声**（没开分离时
        # 是用户自称的干声），比换声前拿整曲探针量的数字可信。测不出来绝不把成功的换声
        # 改判失败，但失败必须留痕（meta.src_f0.error），不许静默。
        try:
            src_f0 = _rvc_f0_of_audio(src)
            meta["src_f0"] = src_f0
            tgt_f0 = _rvc_f0_stats(model)
            if tgt_f0:
                sug = _rvc_pitch_suggestion(src_f0["median_hz"], tgt_f0["median_hz"], pitch)
                meta["pitch_advice"] = {**sug, "pitch_used": pitch}
                if abs(pitch - sug["suggested_pitch"]) >= 4:
                    meta["pitch_note"] = (
                        f"源唱与「{model}」的音域差 {sug['raw_semis']:+.1f} 半音，"
                        f"本单只变了 {pitch:+d}——结果若发紧/电音重，下次试 {sug['suggested_pitch']:+d}")
        except Exception as e:
            meta["src_f0"] = {"error": str(e)[:200]}
        # 排队阶段的 step/queue_pos 不留进产物 meta：status=done 却挂着"排队中"，
        # 事后翻 meta 排查会被带偏（评审 v1.2.0 G4）
        meta.pop("step", None)
        meta.pop("queue_pos", None)
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
                    import librosa
                    voc, sr_v = sf.read(str(wav), dtype="float32", always_2d=True)
                    accm, sr_a = sf.read(str(acc), dtype="float32", always_2d=True)
                    if sr_v != sr_a:
                        voc = librosa.resample(voc.T, orig_sr=sr_v, target_sr=sr_a).T
                        sr_v = sr_a
                    # 响度校准：把换声人声的 RMS 对齐到"真正送进 RVC 的那个人声"，且只按
                    # 有人声的片段统计——静音段一起算会把人声越推越小
                    try:
                        rawm, _sr_raw = sf.read(str(src), dtype="float32", always_2d=True)
                        if gate_mask is not None:
                            voc_sel = voc[_mask_to_signal(gate_mask, voc.shape[0])]
                            raw_sel = rawm[_mask_to_signal(gate_mask, rawm.shape[0])]
                        else:
                            voc_sel, raw_sel = voc, rawm
                        ref_rms = float(np.sqrt((raw_sel.astype(np.float64) ** 2).mean()))
                        voc_rms = float(np.sqrt((voc_sel.astype(np.float64) ** 2).mean()))
                        if voc_rms > 1e-6 and ref_rms > 1e-6:
                            voc = voc * min(3.0, max(0.33, ref_rms / voc_rms))
                    except Exception:
                        pass
                    mixed = _mix_vocal_accompaniment(voc, accm)
                    peak = float(np.abs(mixed).max()) / 0.99
                    if peak > 1:
                        mixed /= peak
                    full = OUTPUT_DIR / f"{rid}_full_song.wav"
                    sf.write(str(full), mixed, sr_a, subtype="PCM_24")
                    assets["full_song"] = full.name
            except Exception:
                # 多产物失败不影响主结果（换声人声已在），meta 里如实省略 assets
                assets = {}
        if assets:
            meta["assets"] = assets
        _output_write_meta(meta)
        with _RVC_LOCK:
            # assets/gate 一起回填到内存任务：换声页的任务卡要靠它们决定
            # "播放成品 / 播放干人声" 该给哪个按钮，以及门控是否真的生效了
            _RVC_JOBS[rid] = {**_RVC_JOBS[rid], "status": "done",
                              "sec": meta["sec"], "bytes": meta["bytes"],
                              "assets": assets, "gate": gate_info or None}
        _win_toast("🎵 换声完成：" + model, f"耗时 {meta['sec']} 秒，已保存到 output/")
    except Exception as e:
        with _RVC_LOCK:
            _RVC_JOBS[rid] = {**_RVC_JOBS.get(rid, job),
                              "status": "error", "error": str(e)[:500],
                              "sec": _elapsed()}
        _win_toast("✕ 换声失败：" + model, str(e)[:120])


# --------------------------------------------------------------------------- #
# 换声串行队列：一次只跑一个任务，后提交的排队等前一个算完
#
# 以前的写法是每来一个请求就 threading.Thread 起一个 worker，而唯一的互斥
# _GPU_SEM 只包住"推理子进程"那十几分钟里的一小段——连点两次换声，两份
# BS-Roformer 分离模型会同时进显存（这才是资源撑不住的根因），并且第二条
# 明明在排队却显示"转换中"、画着进度条。
#
# 两把锁作用域不同，别混用：_RVC_QUEUE_LOCK 只护「入队 + 判活 + 起线程」这一小段，
# _RVC_LOCK 护任务状态字典；任务体一律在队列锁外执行。
_RVC_QUEUE_LOCK = threading.Lock()
_RVC_QUEUE: list[tuple] = []          # 待跑任务的 _rvc_convert_worker 参数元组，先进先出
_RVC_WORKER: threading.Thread | None = None
_RVC_CURRENT: list[str | None] = [None]   # 队列线程此刻正在转换的那条 id（None = 没在算）


def _rvc_positions_locked() -> dict[str, int]:
    """rid -> 第几位。调用方须持有 _RVC_QUEUE_LOCK。

    正在转换的那条算第 1 位：否则它后面那条会拿到"第 1 位"，而 GPU 其实还不在它手上。
    排队中的条目要么还在队列里、要么已被线程取走当成 current，两种算法结果一致，
    所以位次不随「出队」这一步的时间抖动。"""
    base = 1 if _RVC_CURRENT[0] else 0
    pos = {args[0]: base + i + 1 for i, args in enumerate(_RVC_QUEUE)}
    if _RVC_CURRENT[0]:
        pos[_RVC_CURRENT[0]] = 1
    return pos


def _rvc_queue_loop() -> None:
    global _RVC_WORKER
    while True:
        with _RVC_QUEUE_LOCK:
            if not _RVC_QUEUE:
                # 清空指针必须在锁内、退出之前：否则新任务刚好看到"线程还活着"
                # 而不起线程，这边一死就没人接手（丢唤醒）。
                _RVC_WORKER = None
                return
            args = _RVC_QUEUE.pop(0)
            _RVC_CURRENT[0] = args[0]
        try:
            _rvc_convert_worker(*args)
        except Exception as e:
            # worker 内部自带 try/except 落 error 状态；真跑到这里说明是队列层出的问题
            # （参数元组不对、任务字典被删…）。静默吞掉等于抹掉队列的死因。
            print(f"[rvc-queue] 任务 {args[0]} 异常退出，队列继续：{e}", flush=True)
        finally:
            with _RVC_QUEUE_LOCK:
                _RVC_CURRENT[0] = None


def _rvc_submit(args: tuple) -> int:
    """排入队列，返回位次（第 1 位 = 正在转换或马上就是它）。"""
    global _RVC_WORKER
    with _RVC_QUEUE_LOCK:
        _RVC_QUEUE.append(args)
        pos = _rvc_positions_locked()[args[0]]
        if _RVC_WORKER is None or not _RVC_WORKER.is_alive():
            _RVC_WORKER = threading.Thread(target=_rvc_queue_loop, daemon=True)
            _RVC_WORKER.start()
    return pos


def _rvc_cancel(rid: str) -> bool:
    """把还没开跑的任务从队列摘掉；已在执行的返回 False——推理进程不可半途终止。"""
    with _RVC_QUEUE_LOCK:
        for i, args in enumerate(_RVC_QUEUE):
            if args[0] == rid:
                _RVC_QUEUE.pop(i)
                return True
    return False


def _rvc_live(job: dict) -> dict:
    """按当前队列实时回填位次：入队那一刻写下的位次，前面跑完一条就过期了。"""
    if job.get("status") != "pending":
        return job
    with _RVC_QUEUE_LOCK:
        pos = _rvc_positions_locked().get(job.get("id"), 0)
    return {**job, "queue_pos": pos or 1}


@router.post("/rvc/cancel/{rid}")
def rvc_cancel(rid: str):
    """取消一条排队中的换声任务（已开始转换的不能取消）。"""
    rid = os.path.basename(rid)
    with _RVC_LOCK:
        job = _RVC_JOBS.get(rid)
        status = job.get("status") if job else None
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已完成")
    if status != "pending":
        # 对已经算完的条目说"正在转换中不能取消"是另一句谎话——它早就结束了
        if status in ("done", "error", "cancelled"):
            raise HTTPException(status_code=409,
                                detail=f"该任务已经结束（{status}），无需取消")
        raise HTTPException(status_code=409,
                            detail="该任务已在转换中，不能取消（取消只对排队中的任务有效）")
    if not _rvc_cancel(rid):
        raise HTTPException(status_code=409, detail="任务刚好已开始，未能取消")
    with _RVC_LOCK:
        _RVC_JOBS[rid] = {**_RVC_JOBS.get(rid, job), "status": "cancelled", "queue_pos": 0}
        job = _RVC_JOBS[rid]
    # 取消掉的任务永远轮不到执行，它那份上传（可达几十 MB）就成了没人认领的孤儿：
    # 换声产物只在算完时才落盘，所以这里删掉工作目录不会碰到任何历史记录的文件。
    # 评审 F2：不再手写 rmtree——_rvc_job_purge 的 basename/`.`/`..`/resolve 守卫
    # 与 generate_delete 共用同一套已审校验（手写版对 ".." 是放行的，靠上游 404 侥幸兜住）。
    _rvc_job_purge(rid)
    return {"ok": True, "job": job}


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
    gate: str = Form("on"),
    strip_harmony: str = Form("off"),
    filter_radius: int = Form(3),
    resample_sr: int = Form(0),
):
    """上传音频 + 选音色模型 → 后台转换 → 结果落盘 output/（与生成结果同处可回放）。

    separate_vocal: auto=默认先人声分离（带伴奏歌曲必需）；off=直接换声（输入已是干声）。
    gate: on=换声后按原人声包络做静音门，消除前奏/间奏的底噪（模型会在没人声时自己哼音）。
    strip_harmony: on=分离出的人声再过 HP5 只留主唱（副歌有叠唱时能减少高音电音，多约 1 分钟）。"""
    if not RVC_PY.is_file():
        raise HTTPException(status_code=500, detail="rvc python 环境缺失（py312/python.exe）")
    models = _rvc_models()
    if model not in models:
        raise HTTPException(status_code=400, detail=f"未知音色模型：{model}（可用：{models}）")
    # 先验参数再收文件：几百 MB 传到一半才发现索引缺失，等于让人白等一次上传
    pitch = _rvc_pitch(pitch)
    f0_method, index_rate, protect, rms_mix_rate, filter_radius, resample_sr = \
        _rvc_convert_params(model, f0_method, index_rate, protect, rms_mix_rate,
                            filter_radius, resample_sr)
    ext = Path(file.filename or "in.wav").suffix.lower()
    if ext not in (".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus", ".aac", ".wma"):
        ext = ".wav"
    rid = _new_id()
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
        "id": rid, "status": "pending", "step": "排队中", "queue_pos": 0,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "task_name": str(task_name or "").strip()[:100],
        "model": model, "pitch": pitch, "f0_method": f0_method,
        "index_rate": index_rate, "protect": protect, "rms_mix_rate": rms_mix_rate,
        "filter_radius": filter_radius, "resample_sr": resample_sr,
        "src_name": (file.filename or "")[:120],
        "src_size": src.stat().st_size, "src_duration": src_duration,
        # 前端要靠这个字段区分两套耗时口径：含分离的整条比只换声慢一个量级
        "separate_vocal": separate_vocal != "off",
        "gate": gate != "off", "strip_harmony": strip_harmony == "on",
    }
    with _RVC_LOCK:
        _RVC_JOBS[rid] = job
    pos = _rvc_submit((rid, job, src, in_dir, model, pitch, f0_method, index_rate,
                       protect, rms_mix_rate, separate_vocal != "off", gate != "off",
                       strip_harmony == "on", filter_radius, resample_sr))
    # 位次在入队之后才知道；只补排队中的条目——万一已经轮到它开跑，
    # 这里整字典覆盖会把 running 状态打回 pending（队列就假死了）。
    with _RVC_LOCK:
        cur = _RVC_JOBS.get(rid)
        if cur is not None and cur.get("status") == "pending":
            cur["queue_pos"] = pos
            job = cur
    return {"ok": True, "id": rid, "job": job, "position": pos}


@router.get("/rvc/status/{rid}")
def rvc_status(rid: str):
    rid = os.path.basename(rid)
    with _RVC_LOCK:
        job = _RVC_JOBS.get(rid)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或服务已重启")
    return _rvc_live(job)


@router.get("/rvc/active")
def rvc_active():
    """进行中的换声任务（含排队中的；页面刷新后恢复队列用，服务重启则列表为空）。

    排在前面的先跑：正在转换的一条置顶，其余按提交时间升序，前端列表顺序即执行顺序。"""
    with _RVC_LOCK:
        jobs = [_rvc_live(j) for j in _RVC_JOBS.values()
                if j.get("status") in ("running", "pending")]
    jobs.sort(key=lambda x: (x.get("status") != "running", x.get("ts", "")))
    return {"items": jobs}


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
# 训练中/训练后试听：用 logs/<name>/ 的 G_* 检查点在 CPU 上做迷你推理。
# 设计约束：绝不碰 _GPU_SEM、绝不打断训练进程——训练继续占 GPU，试听走 CPU 慢一点
# （约 20-40 秒）但零冲突；产物存 trains/<rid>/preview.wav，前端任务卡片直接播放。
# --------------------------------------------------------------------------- #
_RVC_PREVIEW_LOCK = threading.Lock()  # 同一时刻只跑一个试听（CPU 推理也吃核）
# 成品导出后保留的检查点个数（一对 G/D 约 1.2GB）：
# 全删 = 断掉"换个点再听一次"的路，全留 = 200 轮训练吃掉几十 GB。
_RVC_KEEP_CKPTS = 2


def _rvc_checkpoints(name: str) -> list[Path]:
    """该训练现存可用的 G_* 检查点，按时间从新到旧。"""
    logs = RVC_DIR / "logs" / name
    if not logs.is_dir():
        return []
    return sorted(logs.glob("G_*.pth"), key=lambda p: p.stat().st_mtime, reverse=True)


def _rvc_prune_checkpoints(name: str, keep: int = _RVC_KEEP_CKPTS) -> int:
    """留下最近 keep 个检查点（及其配套 D_*，续跑要用），其余删掉，返回删除文件数。"""
    keep_steps = {p.stem.split("_")[-1] for p in _rvc_checkpoints(name)[:keep]}
    logs = RVC_DIR / "logs" / name
    removed = 0
    if logs.is_dir():
        for p in logs.glob("[GD]_*.pth"):
            if p.stem.split("_")[-1] in keep_steps:
                continue
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass  # 训练进程可能还在写：删不掉就算了，下次再说
    return removed


def _rvc_loss_summary(name: str, window: int = 60) -> dict:
    """从 logs/<name>/train.log 取损失曲线的首尾均值："训练完成"不等于"收敛了"。"""
    p = RVC_DIR / "logs" / name / "train.log"
    if not p.is_file():
        return {}
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    rows: list[dict] = []
    for ln in lines:
        if "loss_disc=" not in ln:
            continue
        vals = {}
        for k in ("loss_disc", "loss_gen", "loss_fm", "loss_mel", "loss_kl"):
            m = re.search(rf"{k}=(\-?[\d.]+)", ln)
            if m:
                vals[k] = float(m.group(1))
        if vals:
            rows.append(vals)
    if not rows:
        return {}

    def avg(part: list[dict]) -> dict:
        keys = [k for k in ("loss_disc", "loss_gen", "loss_fm", "loss_mel", "loss_kl")
                if any(k in r for r in part)]
        return {k: round(sum(r.get(k, 0.0) for r in part if k in r)
                         / max(1, sum(1 for r in part if k in r)), 3) for k in keys}

    return {"points": len(rows),
            "head": avg(rows[:window]),
            "tail": avg(rows[-window:]),
            "epochs_logged": sum(1 for ln in lines if "轮次：" in ln)}


def _rvc_extract_small(ckpt: Path, stem: str, info: str,
                       timeout: int = 600,
                       fail_msg: str = "检查点转换失败：") -> tuple[Path, Path]:
    """跑一次官方 extract_small_model，产物只落在专用临时工作目录里。

    它写死相对 CWD 的 "assets/weights/<stem>.pth"（train/process_ckpt.py:216），
    根本不认 weight_root 环境变量——以前每回试听中间检查点，G_xxx.pth 都会短暂
    落进音色权重目录，被 _rvc_models() 列成一个可选音色（评审 v1.2.0 G3）。
    想不让它落进去，唯一可靠的办法就是换一个 CWD 让它写。
    返回 (产物文件, 临时工作目录)，调用方负责搬走产物并 rmtree 临时目录。"""
    work = (RVC_DIR / "export_tmp" /
            f"{stem}_{os.getpid()}_{int(time.time() * 1000)}")
    (work / "assets" / "weights").mkdir(parents=True, exist_ok=True)
    # 先留在 RVC_DIR 里 import（i18n 在导入期按 CWD 读 ./i18n/locale/*.json），
    # 再把 CWD 切到临时目录调函数——torch.save 的 "assets/weights/%s.pth" 就落在这里
    script = (
        "import sys, os\n"
        "sys.path.insert(0, sys.argv[3])\n"
        "from train.process_ckpt import extract_small_model\n"
        "os.chdir(sys.argv[5])\n"
        "print(extract_small_model(sys.argv[1], sys.argv[2], '40k', 1,\n"
        "                          sys.argv[4], 'v2'))\n"
    )
    proc = subprocess.run(
        [str(RVC_PY), "-c", script, str(ckpt), stem,
         str(RVC_DIR), info, str(work)],
        capture_output=True, timeout=timeout, cwd=str(RVC_DIR),
        env={**os.environ, "PYTHONPATH": str(RVC_DIR),
             "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
             "CUDA_VISIBLE_DEVICES": ""},
        creationflags=_pymss_creationflags())
    produced = work / "assets" / "weights" / f"{stem}.pth"
    if proc.returncode != 0 or not produced.is_file():
        # extract_small_model 吞异常时把 traceback 当返回值 print 出去（返回码仍是 0），
        # 真实死因在 stdout，stderr 一起带上才找得到根因
        tail = ((proc.stderr or b"") + b"\n" + (proc.stdout or b""))[-300:]
        shutil.rmtree(work, ignore_errors=True)
        raise HTTPException(status_code=500,
                            detail=f"{fail_msg}{tail.decode('utf-8', 'replace')}")
    return produced, work


def _rvc_small_model(ckpt: Path, cache: Path) -> Path:
    """训练检查点转成推理可用结构（缓存按检查点各自存一份）。

    G_*.pth 里是 {"model", "optim_g", ...}，缺 weight/config 键，直接喂
    infer/cli.py 必抛 ValueError——先过官方 extract_small_model。
    缓存文件名带检查点标识：以前共用 preview_model.pth，按 mtime 判新旧，
    先听新点再回头听旧点时会把旧点的结果悄悄换成新点的音频（标签骗人）。"""
    if cache.is_file() and cache.stat().st_mtime >= ckpt.stat().st_mtime:
        return cache
    produced, work = _rvc_extract_small(ckpt, cache.stem, "preview extract")
    try:
        shutil.move(str(produced), str(cache))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return cache


def _rvc_preview_infer(model_path: Path, src: Path, out: Path,
                       index: Path | None = None) -> None:
    """CPU 迷你推理一段素材切片；失败抛 HTTPException，成功落盘 out。"""
    cmd = [
        str(RVC_PY), str(RVC_DIR / "infer" / "cli.py"),
        "--model", str(model_path),
        "--input", str(src), "--output", str(out),
        "--pitch", "0", "--f0-method", "rmvpe",
        "--index-rate", "0.75" if index else "0",
        "--protect", "0.33", "--rms-mix-rate", "1.0", "--overwrite",
    ]
    if index:
        cmd += ["--index", str(index)]
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


def _rvc_preview_source(rid: str, name: str = "") -> Path:
    """挑一段试听用的素材。顺序有代价差别：

    1) logs/<name>/0_gt_wavs —— 预处理切好的 3.7 秒切片，CPU 推理十几秒出结果；
    2) dataset_clean —— 分离出来的人声，但是整首的长度；
    3) dataset —— 用户上传的原始文件。本机一个任务的这里放的是整首 150 秒的歌，
       实测一次试听跑了 143 秒；换个源同样一件事只要十几秒。"""
    cands = ([RVC_DIR / "logs" / name / "0_gt_wavs"] if name else []) + [
        RVC_TRAIN_DIR / rid / "dataset_clean", RVC_TRAIN_DIR / rid / "dataset"]
    for d in cands:
        if d.is_dir():
            wavs = sorted(p for p in d.iterdir()
                          if p.is_file() and p.suffix.lower() in _RVC_AUDIO_EXTS)
            if wavs:
                return wavs[0]
    raise HTTPException(status_code=404, detail="找不到源素材切片，无法试听")


def _rvc_ck_label(ckpt: Path) -> str:
    return f"{ckpt.name}（step {ckpt.stem.split('_')[-1]}，非轮次）"


@router.post("/rvc/train/preview/{rid}")
def rvc_train_preview(rid: str, payload: dict = Body(default={})):
    """试听某个检查点：不传 ck 就听最新的那个（训练中的实时进度同样可用）。

    返回 ckpts 列表，前端据此给出"换点再听"和"以这个点定稿"的按钮。"""
    ck = str(payload.get("ck") or "")
    rid = os.path.basename(rid)
    job = _rvc_train_read(rid)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.get("status") not in ("running", "pending", "done", "error"):
        raise HTTPException(status_code=409, detail="任务状态异常")
    # 训练运行中试听 = 最危险的内存撞车窗口（df77 OOM 教训），前置闸门自证余量
    _require_headroom_for_preview()
    name = job.get("name", "")
    ckpts = _rvc_checkpoints(name)          # 从新到旧
    final = _rvc_latest_export(name)
    # weights/<name>.pth 已经导出，结构直接可用不必再转换；但它只有在本轮训练
    # 收尾后才该盖过检查点（同名音色的旧成品不能冒充这次训练的结果）
    use_final = final is not None and (not ckpts
                                       or final.stat().st_mtime >= ckpts[0].stat().st_mtime)
    if ck:
        # 点名要哪个点就必须是那个点——不能"你要 A，我给你最新的 B"还不说明
        want = os.path.basename(ck)
        pick = next((p for p in ckpts if p.name == want), None)
        if pick is None:
            raise HTTPException(status_code=404,
                                detail=f"检查点 {want} 不存在（只保留最近 {_RVC_KEEP_CKPTS} 个，更早的已清理）")
        model_path = _rvc_small_model(
            pick, RVC_TRAIN_DIR / rid / f"preview_model_{pick.stem.split('_')[-1]}.pth")
        tag = _rvc_ck_label(pick)
    elif use_final:
        model_path, tag = final, "成品"
    elif ckpts:
        pick = ckpts[0]
        model_path = _rvc_small_model(
            pick, RVC_TRAIN_DIR / rid / f"preview_model_{pick.stem.split('_')[-1]}.pth")
        tag = _rvc_ck_label(pick)
    else:
        raise HTTPException(status_code=404,
                            detail="还没有可试听的检查点（训练尚未产出 G_* 文件），请稍后再试")
    src = _rvc_preview_source(rid, name)
    out = RVC_TRAIN_DIR / rid / "preview.wav"
    if not _RVC_PREVIEW_LOCK.acquire(blocking=False):  # 原子抢锁，杜绝 TOCTOU
        raise HTTPException(status_code=409, detail="上一次试听还在生成中，请稍候")
    try:
        _rvc_preview_infer(model_path, src, out, _rvc_index_for(name + ".pth"))
    finally:
        _RVC_PREVIEW_LOCK.release()
    return {"ok": True, "url": f"/api/rvc/train/preview/{rid}/audio", "source": tag,
            "model": model_path.name,
            "ckpts": [p.name for p in ckpts],
            "audio": src.name}


@router.get("/rvc/train/preview/{rid}/audio")
def rvc_train_preview_audio(rid: str):
    p = RVC_TRAIN_DIR / os.path.basename(rid) / "preview.wav"
    if not p.is_file():
        raise HTTPException(status_code=404, detail="试听文件不存在，请先生成")
    return FileResponse(str(p), media_type="audio/wav", filename="preview.wav")


@router.post("/rvc/train/promote/{rid}")
def rvc_train_promote(rid: str, payload: dict = Body(default={})):
    """"就定这个点"：把选中的检查点重新导出为成品，覆盖 weights/<name>.pth。

    音色名不变 ⇒ 配套索引原样可用（索引由特征库生成，与用哪个检查点无关），
    换点定稿后不需要重训、也不需要改任何检索文件。"""
    ck = str(payload.get("ck") or "")
    if not ck:
        raise HTTPException(status_code=400,
                            detail="必须指定要定稿的检查点 ck（例如 G_2333333.pth）")
    rid = os.path.basename(rid)
    job = _rvc_train_read(rid)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    name = job.get("name", "")
    want = os.path.basename(ck or "")
    pick = next((p for p in _rvc_checkpoints(name) if p.name == want), None)
    if pick is None:
        raise HTTPException(status_code=404,
                            detail=f"检查点 {want or '(空)'} 不存在（只保留最近 {_RVC_KEEP_CKPTS} 个）")
    step = want.split("_")[-1].split(".")[0]
    target = RVC_MODELS_DIR / f"{name}.pth"
    # 导出先进专用临时目录，真成品一个字节都不动（评审 v1.2.0 G2）：
    # 以前直接往 weights/<name>.pth 写，半途被杀/磁盘满时只要 mtime 新过
    # 检查点就"被判成功"，音色当场报废且没有回滚路径。
    produced, work = _rvc_extract_small(pick, name, f"{step} (promoted)", 900,
                                        fail_msg="定稿失败（成品未更新）：")
    try:
        old_sz = target.stat().st_size if target.is_file() else 0
        new_sz = produced.stat().st_size
        # 体积判据：成品正常约 50MB 量级，半截货（超时被杀/中途崩）远小于此；
        # 小于 1MB 或不到原成品一半 → 拒收，原文件保持不动
        if new_sz < 1_000_000 or new_sz * 2 < old_sz:
            raise HTTPException(
                status_code=500,
                detail=f"定稿失败（导出的成品体积不可信：{new_sz} B，"
                       f"原成品 {old_sz} B），原音色文件未改动")
        if target.is_file():
            os.replace(str(target), str(target) + ".bak")  # 上一版留一份可回滚
        os.replace(str(produced), str(target))             # 同卷原子替换
    finally:
        shutil.rmtree(work, ignore_errors=True)
    job["promoted_ckpt"] = want
    job["model"] = target.name
    _rvc_train_write(rid, job)
    return {"ok": True, "model": target.name, "ckpt": want,
            "message": f"已用 {want} 定稿，音色 {name} 现在就是这一个点"}


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
    # 超时要按训练规模算：固定 6 小时曾把大素材长训练（1358 切片 × 200 轮 ≈ 8 小时）
    # 在健康跑到一半时误杀。每轮多长这件事只用本机实测过的数字说话——
    # _rvc_train_pace() 从已成功任务里取"每切片每轮秒数"，没有实测记录才回落 240 秒/轮
    # （同一份素材，CPU 与 GPU 差一个量级，所以首次训练偏保守是故意的：宁多等不误杀）。
    epochs = int(job.get("epochs") or 200)
    if step == "训练中":
        rate, job["epoch_est_note"] = _rvc_epoch_rate(job)
        job["epoch_est_sec"] = round(rate, 1)
        est_sec = int(epochs * rate) + 3600  # +1 小时：加载底模、断点续训、落盘余量
    else:
        est_sec = epochs * 240 + 3600
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
        _t0 = time.perf_counter()
        proc_out, proc_err = _proc.communicate(timeout=est_sec)
    except subprocess.TimeoutExpired:
        _proc.kill()
        proc_out, proc_err = _proc.communicate()
        raise RuntimeError(f"步骤 {step} 超时（超过 {est_sec // 60} 分钟）：{step} 异常")
    finally:
        with RVC_TRAIN_LOCK:
            _RVC_TRAIN_PROC = None
    step_sec = round(time.perf_counter() - _t0, 1)
    tail = ((proc_err or b"") + (proc_out or b""))[-600:].decode("utf-8", "replace")
    job["log_tail"] = tail[-400:]
    # 每一步的实测耗时都记进任务：训练要跑多久这件事必须有出处（本机跑过的数字），
    # 而不是拍一个"每轮约 4 分钟"的公式——同一份素材，CPU 与 GPU 差一个量级。
    job["step_secs"] = {**(job.get("step_secs") or {}), step: step_sec}
    if step == "训练中":
        job["train_sec"] = step_sec
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


# ---------------------------------------------------------------------------
# A1 素材净化（评审 P0）：分离伴奏之后、切片之前，补上社区标准三步里的
# 后两步——去混响 → 轻降噪。用的是 RVC23 自带的 PyMSS 框架原生支持的
# 净化模型（tools/pymss/resources/model_catalog.json 里 supported:true），
# 不引任何新 Python 库，只需要把权重下到与分离模型同一个缓存目录。
# 档位口径来自评审 A1："降噪务必保守，过度降噪比轻微底噪更糟"——
# 轻档全是小体积 VR 架构模型（59MB+18MB），中档才上 roformer（204MB+127MB）。
_RVC_CLEAN_TIERS = {
    "light": ("UVR-DeReverb-aufr33-jarredou_4band_v4_ms_fullband", "UVR-DeNoise-Lite"),
    "medium": ("dereverb_bs_roformer_anvuew_sdr_22.5050", "UVR-DeNoise"),
}
_RVC_CLEAN_CN = {"light": "轻", "medium": "中"}
# 每家的"干净人声"输出声部叫法不一，全部实名取自权重配套 yaml（本机实测核对）：
#   UVR-DeReverb 4band VR → instruments ['Dry','Reverb']
#   dereverb_bs_roformer  → ['noreverb','reverb']
#   UVR-DeNoise(-Lite)    → ['Noise','No Noise']
# 注意不能用子串匹配区分——"No Noise" 包含 "Noise"、"noreverb" 包含 "reverb"，
# 必须拿归一化后的**完整声部名**比对精确名单。
_RVC_CLEAN_KEEP_NAMES = {"dry", "noreverb", "nonoise", "vocals", "vocal", "clean"}
_RVC_CLEAN_DROP_NAMES = {"reverb", "noise", "wet", "other", "echo", "novocals",
                         "instrumental", "instrument", "accompaniment", "backing"}


def _rvc_clean_norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _rvc_clean_select(stage_out: Path, stem: str) -> Path | None:
    """在一阶段产物里挑出该输入对应的"干净人声"文件；挑不出返回 None（回退用原文件）。"""
    raw = [p.name[len(stem) + 1:] for p in stage_out.glob(f"{stem}_*")
           if p.suffix.lower() == ".wav"]
    # 声部名里没有下划线（Dry / No Noise / noreverb）——"0_Dry.wav" 这种带下划线的
    # 是别的输入（stem="0"）名下的文件被前缀撞名，不能认作本 stem 的声部
    raw = [r for r in raw if "_" not in r]
    cands = [p for p in stage_out.glob(f"{stem}_*")
             if p.suffix.lower() == ".wav" and p.name[len(stem) + 1:] in raw]
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]                      # 单声部模型：唯一输出就是它
    exact = [p for p in cands
             if _rvc_clean_norm(p.name[len(stem) + 1:].rsplit(".", 1)[0])
             in _RVC_CLEAN_KEEP_NAMES]
    if exact:
        return exact[0]
    rest = [p for p in cands
            if _rvc_clean_norm(p.name[len(stem) + 1:].rsplit(".", 1)[0])
            not in _RVC_CLEAN_DROP_NAMES]
    return rest[0] if len(rest) == 1 else None


def _rvc_pymss_stage(model: str, in_dir: Path, out_dir: Path, dev: str) -> str:
    """对一个目录跑一次 PyMSS 推理（权重只加载一次）。返回 ""=成功，否则为错误摘要。

    `infer -i` 原生支持传目录；整批失败不直接判死——调用方还可以逐文件重试，
    一个坏文件不该拖垮整批素材。--download：换机器/清过缓存时自动补权重
    （和分离模型同一个解析链：PYMSS_MODEL_DIR → tools/all_models → ~/.cache/pymss/models）。"""
    out_dir.mkdir(parents=True, exist_ok=True)

    def _one(src: Path) -> str:
        try:
            r = subprocess.run(
                # 注意包名：必须是 `-m pymss.cli` 而不是分离链在用的 `-m tools.pymss.cli`。
                # VR 架构模型（降噪/去混响）走 tools/pymss/modules 的别名层，那里写死了
                # 只接受 "pymss.modules." 前缀（_core_shims.py:8-10）——用 tools.pymss 调用
                # 会报 invalid local module alias 直接崩（真机实测）。bs_roformer 分离链
                # 不经过这层，所以老调用一直没暴露这个坑。
                [str(RVC_PY), "-m", "pymss.cli", "infer", model,
                 "-i", str(src), "-o", str(out_dir), "--device", dev, "--download"],
                capture_output=True, timeout=7200,
                creationflags=_pymss_creationflags(),
                cwd=str(RVC_DIR), env=_pymss_env())
        except Exception as e:
            return str(e)[:160]
        if r.returncode != 0:
            return ((r.stderr or b"") + b"\n" + (r.stdout or b""))[-300:] \
                .decode("utf-8", "replace")
        return ""

    err = _one(in_dir)
    # 评审 J3（真机撞过）：给 -i 传不存在的目录，PyMSS 退 0、零产出。调用方按产物
    # 文件判成败兜得住，但"退 0 即成功"这个窗口一旦哪天换机器/升级后行为变了，
    # 就是"净化静默跳过、拿未净化素材继续练"。这里先把它暴露成显式错误。
    if not err and not any(out_dir.iterdir()):
        return "PyMSS 退出码 0 但没有任何产物（输入未命中或模型未生效）"
    return err


def _rvc_clean_dataset(train_dir: Path, out_dir: Path, tier: str,
                       job: dict, resume: bool = False) -> dict:
    """素材净化：去混响 → 轻降噪（评审 A1，顺序与档位口径同社区共识）。

    留档原则：输入目录（原始上传或分离产物）一律不动，净化结果只写 out_dir——
    前后两份自然并存，用户随时能 A/B；净化一旦过头不丢原始素材。
    单文件没有产物 → 带着该文件的净化前版本进入下一步（计入 carried），
    两阶段都全批拿不到任何产物 → 抛错，绝不默默把未净化的素材当"已净化"继续练。"""
    rid = job["id"]
    de_model, dn_model = _RVC_CLEAN_TIERS[tier]
    files = sorted(p for p in train_dir.iterdir()
                   if p.is_file() and p.suffix.lower() in _RVC_AUDIO_EXTS)
    if not files:
        raise RuntimeError(f"净化输入目录没有可识别的音频：{train_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    if resume and all((out_dir / f"{p.stem}.wav").is_file() for p in files):
        return {"tier": tier, "label": _RVC_CLEAN_CN[tier],
                "dereverb": de_model, "denoise": dn_model,
                "files": len(files), "fully_cleaned": len(files),
                "carried": 0, "sec": 0.0, "skipped": True,
                "before": str(train_dir), "after": str(out_dir)}
    dev = backend_mode()
    cn = _RVC_CLEAN_CN[tier]
    tmp = out_dir.parent / "_clean_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    t0 = time.perf_counter()
    cur = {p.stem: p for p in files}          # 每个样本进入下一阶段时用的是哪个文件
    carry = 0
    err_note = ""
    for model, label, cn_label in ((de_model, "dereverb", "去混响"),
                                   (dn_model, "denoise", "降噪")):
        in_dir = train_dir if label == "dereverb" else (tmp / f"{label}_in")
        if label != "dereverb":
            in_dir.mkdir(parents=True, exist_ok=True)
            for src in cur.values():
                shutil.copy2(str(src), str(in_dir / src.name))
        stage_out = tmp / f"{label}_out"
        job["step"] = f"素材净化（{cn} · {cn_label}，{len(cur)} 个文件）"
        _rvc_train_write(rid, job)
        err = _rvc_pymss_stage(model, in_dir, stage_out, dev)
        if err:
            err_note = (err_note + f"；{cn_label}整批失败后逐文件重试：")[:200]
            for src in list(cur.values()):
                if any(stage_out.glob(f"{src.stem}_*")):
                    continue
                err_note += f"{cn_label}:{src.name[:40]} " + \
                    (_rvc_pymss_stage(model, src, stage_out, dev) or "ok")[:60]
        nxt = tmp / f"{label}_v"
        nxt.mkdir(parents=True, exist_ok=True)
        new_cur = {}
        for stem, src in cur.items():
            pick = _rvc_clean_select(stage_out, stem)
            if pick is None:
                new_cur[stem] = src           # 回退：带着净化前的版本进下一步
                carry += 1
            else:
                dst = nxt / f"{stem}.wav"
                shutil.move(str(pick), str(dst))
                new_cur[stem] = dst
        cur = new_cur
    real = sum(1 for src in cur.values() if src.parent.name.endswith("_v"))
    if real == 0:
        # 一桶没净化成：什么都不往 out_dir 落。若把回退副本先搬过去，续跑的
        # "产物已齐就跳过"会把这堆未净化副本当成已净化放行——留档目录必须只装真净化过的。
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError("素材净化全部失败（PyMSS 净化模型缺失或推理报错，"
                           f"权重需落在 {_pymss_model_dir()}）{('：' + err_note[:160]) if err_note else ''}；"
                           "素材本来就是干净干声的话，取消净化后重新提交")
    for stem, src in cur.items():
        cleaned = src.parent.name.endswith("_v")
        dst = out_dir / (f"{stem}.wav" if cleaned else src.name)
        shutil.move(str(src), str(dst))
    shutil.rmtree(tmp, ignore_errors=True)
    return {"tier": tier, "label": cn, "dereverb": de_model, "denoise": dn_model,
            "files": len(files), "fully_cleaned": real, "carried": carry,
            "sec": round(time.perf_counter() - t0, 1),
            "before": str(train_dir), "after": str(out_dir)}


def _rvc_train_worker(rid: str, name: str, epochs: int,
                      separate_vocal: bool = False, resume: bool = False,
                      clean_tier: str = "off") -> None:
    job = _rvc_train_read(rid)
    started = time.time()
    exp_logs = RVC_DIR / "logs" / name
    gpu_held = False   # 只有真的 acquire 成功才允许 release：早于闸门失败的异常
                       # 若在 finally 里无条件 release，信号量计数会+1，
                       # GPU 互斥闸从此永久失效（训练与生成并发 → 显存 OOM）
    try:
        n_p = max(1, (os.cpu_count() or 4) // 2)
        ds = RVC_TRAIN_DIR / rid / "dataset"
        exp_logs.mkdir(parents=True, exist_ok=True)  # 预处理会往 logs/<name>/ 写日志
        # GPU 全局闸门：整条训练流水线与生成/批量/换声互斥（防 6GB 显存双进程 OOM）
        _GPU_SEM.acquire()
        gpu_held = True
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
                         "-i", str(w), "-o", str(clean), "--device", backend_mode()],
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
        # 0.5) 可选：素材净化（评审 A1，社区标准三步的后两步：去混响→轻降噪）。
        #      原始目录/分离产物原样留档，净化结果落 dataset_purified——前后两份并存，
        #      净化过头随时能 A/B 听回来，这一步不再是单行道。
        if clean_tier in _RVC_CLEAN_TIERS:
            cleaned = RVC_TRAIN_DIR / rid / "dataset_purified"
            info = _rvc_clean_dataset(train_dir, cleaned, clean_tier, job, resume)
            train_dir = cleaned
            job["clean"] = info
            job["step"] = "素材净化完成"
            _rvc_train_write(rid, job)
        # 1) 预处理切片（40k、3.7s/片）
        #    脚本位置跟着 RVC_DIR 走，不从 RVC_TRAIN_DIR 反推父目录：后者是任务落盘目录，
        #    一旦被改写（测试沙箱、以后挪盘），这里就会静默指向一个不存在的 train/
        _rvc_run_step([str(RVC_PY), str(RVC_DIR / "train" / "preprocess.py"),
                       str(train_dir), "40000", str(n_p), str(exp_logs), "False", "3.7"], job, "预处理切片")
        # 2) F0 提取（rmvpe）3) Hubert 特征（v2 → 768 维）
        # 设备跟随引擎当前模式：以前硬写 "cuda"，无独显机器上这两步会直接抛
        # torch 设备错误（README 声称"无独显也能跑"，CPU 只是慢不是不能跑）
        sep_dev = backend_mode()
        # extract_f0.py 读参数是按 mode 分支的（train/dataset/extract_f0.py:20-44）：
        # cuda 收 n_part i_part i_gpu exp_dir is_half，cpu 收 exp_dir n_p f0method。
        # 以前不管什么设备都按 cuda 那一串传，切到 CPU 的机器上 exp_dir 被读成 "1"，
        # F0 对着一个不存在的目录"跑成功"，最后 filelist 空 → 报"没有可用样本"，
        # 用户完全看不出是传参错了。
        if sep_dev == "cuda":
            f0_argv = [sep_dev, "1", "0", "0", str(exp_logs), "False"]
        else:
            f0_argv = [sep_dev, str(exp_logs), str(n_p), "rmvpe"]
        _rvc_run_step([str(RVC_PY), str(RVC_DIR / "train" / "dataset" / "extract_f0.py"),
                       *f0_argv], job, "F0 提取")
        _rvc_run_step([str(RVC_PY), str(RVC_DIR / "train" / "dataset" / "extract_hubert_feature.py"),
                       sep_dev, "1", "0", str(exp_logs), "v2", "False"], job, "音色特征提取")
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
        #    batch_size 按显存自适应：写死 4 时 6GB 卡常年顶满（别人占一点就 OOM），
        #    16GB 机器只用四分之一、白等几倍时间
        bs, bs_note = _rvc_train_batch_size()
        job["batch_size"] = bs
        job["batch_note"] = bs_note
        _rvc_train_write(rid, job)
        _rvc_run_step([str(RVC_PY), str(RVC_DIR / "train" / "train.py"),
                       "-e", name, "-sr", "40k", "-f0", "1", "-bs", str(bs),
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
        # 参数一律走 sys.argv：以前音色名是 %-插值进 python -c 的源码字符串里的，
        # 名字里带一个单引号就能从 r'...' 里跳出来，在网关进程里执行任意 Python
        # （配合零鉴权的 POST /api/train，外部网页即可打本机）。
        _rvc_run_step([str(RVC_PY), "-c",
                       "import sys; sys.path.insert(0, '.'); "
                       "from train.process_ckpt import extract_small_model; "
                       "print(extract_small_model(sys.argv[1], sys.argv[2], '40k', 1, "
                       "sys.argv[3] + ' epoch', 'v2'))",
                       str(final_ckpt), str(name), str(epochs)],
                      job, "导出成品")
        exported = RVC_MODELS_DIR / f"{name}.pth"
        if not exported.is_file():
            raise RuntimeError("成品导出失败（weights 下未生成 %s.pth）" % name)
        # 6) 检查点收尾：保留最近 _RVC_KEEP_CKPTS 对（试听/换点定稿/续跑都要用），
        #    其余删掉；索引与日志一律保留
        removed = _rvc_prune_checkpoints(name)
        idx_files = list((RVC_DIR / "logs" / name).glob("added_*.index"))
        # 自检这一步仍在干活，所以状态还是 running（提前报 done 就是撒谎）
        # 音域档案（评审 C4）：训练素材的 f0 全集此时已齐，顺手算出这个音色的
        # 舒适音域并落 sidecar——换声页的变调建议靠它，没这一步就得让用户当场扫 npy。
        try:
            job["f0_range"] = _rvc_f0_stats(name, refresh=True)
        except Exception:
            job["f0_range"] = None
        job.update(
            status="running", step="自动自检",
            model=exported.name,
            index=idx_files[0].name if idx_files else "",
            kept_ckpts=[p.name for p in _rvc_checkpoints(name)],
            ckpts_pruned=removed,
            loss=_rvc_loss_summary(name),
            sec=round(time.time() - started, 1),
        )
        _rvc_train_write(rid, job)
        # 7) 自动自检：训练成功后立刻用自己的一段素材做一次 CPU 迷你推理，
        #    产出 preview.wav——"练完了"不等于"能用了"，先听到再决定去不去换声。
        #    失败绝不把已完成的任务改判失败（成品已经在库里），只在任务上留痕。
        try:
            src = _rvc_preview_source(rid, name)
            out = RVC_TRAIN_DIR / rid / "preview.wav"
            _rvc_preview_infer(exported, src, out, idx_files[0] if idx_files else None)
            job["preview_url"] = f"/api/rvc/train/preview/{rid}/audio"
            job["self_check"] = {"ok": True, "source": src.name,
                                 "indexed": bool(idx_files)}
        except Exception as pe:
            job["self_check"] = {"ok": False, "error": str(pe)[:200]}
        job.update(status="done", step="完成", sec=round(time.time() - started, 1))
        _rvc_train_write(rid, job)
        # 落盘到 output/：历史页可查看（kind=train，无音频，点击可"去使用"）
        _cl = job.get("clean") or {}
        _output_write_meta({
            "id": rid, "ts": job.get("ts"), "status": "done", "kind": "train",
            "voice_name": name, "model": exported.name, "index": job.get("index", ""),
            "epochs": epochs, "samples_used": job.get("samples_used"),
            "sec": job.get("sec"), "bytes": 0,
            "style": f"音色制作 · {name}" + (f" · 已净化（{_cl.get('label')}档）" if _cl else ""),
            "lyrics": f"训练 {epochs} 轮 · {job.get('samples_used', '?')} 个样本",
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
        if gpu_held:
            _GPU_SEM.release()   # 与上方 acquire() 配对；未持有绝不释放
        with RVC_TRAIN_LOCK:
            RVC_TRAIN_JOBS[rid] = job


_RVC_AUDIO_EXTS = (".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus", ".aac", ".wma")
_RVC_SCAN_MAX_SEC = 600     # 单文件只分析前 10 分钟：更长的按抽样说话，别把网关内存吃爆
_RVC_SILENCE_DB = -45.0     # 帧 RMS 低于此视为静音（干声口径）


def _rvc_suggest_epochs(total_sec: float) -> tuple[int, str]:
    """三档轮数（官方口径：至少 10 分钟低噪干声；素材差才靠加轮数硬救）。

    档位是给页面用的，不是给模型用的：1200 轮在 6GB 卡上是一整天，
    用户真正需要知道的是"这批素材值不值得练到 200"。"""
    if total_sec < 60:
        return 30, "素材不足 1 分钟：只够试听档（30 轮）看看像不像，别指望音色稳"
    if total_sec < 480:
        return 100, f"{total_sec / 60:.1f} 分钟素材：100 轮起步，听完不满意再加到 200"
    if total_sec < 1800:
        return 200, f"{total_sec / 60:.1f} 分钟素材：200 轮（官方推荐的常规档）"
    return 100, f"{total_sec / 60:.1f} 分钟素材很足：100 轮通常就到顶了，多练只是耗时"


def _rvc_train_pace() -> dict | None:
    """本机实测的训练速度：只采信真正跑完整条训练的任务记录。

    为什么不给公式：同一份素材在 CPU 与 GPU 上差一个量级，写死的"每轮约 N 分钟"
    在这个仓库里已经错过一次（6 小时超时把健康跑到一半的 200 轮训练误杀）。
    没有实测记录就如实说没有，等第一次成功训练落盘后这里自动有数。"""
    best: dict | None = None
    for jp in sorted(RVC_TRAIN_DIR.glob("*/job.json"),
                     key=lambda p: p.stat().st_mtime):
        try:
            j = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:
            continue
        sec, ep, used = j.get("train_sec"), j.get("epochs"), j.get("samples_used")
        if j.get("status") != "done" or not sec or not ep or not used:
            continue
        best = {"source": j.get("id"), "epochs": ep, "samples_used": used,
                "epoch_sec": round(float(sec) / int(ep), 1),
                "sec_per_slice_epoch": round(float(sec) / int(ep) / int(used), 4),
                "backend_at_measure": j.get("backend")}
    return best


def _rvc_epoch_rate(job: dict) -> tuple[float, str]:
    """这一批素材跑一轮要多少秒：只有本机实测数字，没有实测时如实说保守值。"""
    pace = _rvc_train_pace()
    used = int(job.get("samples_used") or 0)
    if pace and used > 0:
        return max(5.0, float(pace["sec_per_slice_epoch"]) * used), "实测（样本任务 %s）" % pace["source"]
    return 240.0, "本机还没有跑完整过的训练，按 240 秒/轮保守估计"


def _rvc_train_batch_size() -> tuple[int, str]:
    """训练 batch_size 按整卡显存取（官方 WebUI 同一口径：webui.py:180 batch_size = VRAM_GB // 2）。

    以前写死 4：6GB 卡上勉强跑得动但显存常年顶满（一旦别占一点就 OOM），
    16GB 的机器却只用到四分之一、白等三倍时间。查不到显存才回落到 4。"""
    if backend_mode() != "cuda":
        return 4, "CPU（按内存安全值）"
    total = _gpu_total_mb()
    if not total:
        return 4, "显存未知，回落 4"
    gb = max(1, int(round(total / 1024)))
    bs = max(1, min(8, gb // 2))
    return bs, f"{gb}GB 显存 → batch {bs}"


def _rvc_dataset_scan(ds: Path) -> dict:
    """训练数据集体检：时长/条数/采样率/声道/响度/爆音/静音占比 + 建议轮数与耗时预估。

    官方语料口径（README、faq、docs/training_tips）是"至少 10 分钟低噪干声、
    按 >5 秒静音切开"，以前这两条只写在页面提示里，没人替用户量——
    于是 40 分钟带伴奏的现场录音也一样开跑，两小时后拿到一个发虚的音色。"""
    import numpy as np
    import soundfile as sf
    files = sorted(p for p in ds.iterdir()
                   if p.is_file() and p.suffix.lower() in _RVC_AUDIO_EXTS) \
        if ds.is_dir() else []
    rep = {"files": len(files), "unreadable": [], "total_sec": 0.0,
           "sample_rates": [], "channels": [], "peak_dbfs": None,
           "voiced_dbfs": None, "silence_ratio": None, "clipped_ratio": 0.0,
           "noise_floor_dbfs": None,
           "longest_silence_sec": 0.0, "silence_runs_over_5s": 0,
           "sampled": False, "warnings": [], "est_slices": None}
    if not files:
        rep["warnings"].append("目录里没有可识别的音频文件（支持 wav/flac/mp3/m4a/ogg/opus/aac/wma）")
        rep["suggest_epochs"] = 30
        rep["advice"] = "素材读不出来，先修格式再练"
        return rep
    srs: set[int] = set()
    chs: set[int] = set()
    peaks: list[float] = []
    voiced: list[float] = []
    sil_frames = tot_frames = 0
    clip_samples = tot_samples = 0
    quiet_pool: list[float] = []       # 每个文件最安静 10% 帧的中位 dBFS ≈ 底噪水平。
    # 不拿 -45 静音门当唯一入口：噪声大到没有帧低于 -45 时，恰恰是最该提示净化的素材
    # 反而"测不出底噪"——分位数口径下这一类会如实报出高底噪。
    unreadable: list[str] = []
    total_sec = 0.0
    for p in files:
        try:
            info = sf.info(str(p))
            sr = int(info.samplerate)
            frames = int(info.frames)
            take = min(frames, max(1, int(sr * _RVC_SCAN_MAX_SEC)))
            x, sr = sf.read(str(p), frames=take, always_2d=True, dtype="float32")
        except Exception:
            unreadable.append(p.name[:60])
            continue
        if take < frames:
            rep["sampled"] = True
        total_sec += frames / max(sr, 1)
        srs.add(sr)
        chs.add(int(x.shape[1]))
        peaks.append(float(np.max(np.abs(x))) if x.size else 0.0)
        clip_samples += int((np.abs(x) >= 0.999).sum())
        tot_samples += int(x.size)
        m = x.mean(axis=1)
        blk = max(1, sr // 2)                 # 半秒一帧，与 RVC 换声侧的 rms 口径一致
        nb = len(m) // blk
        if not nb:
            continue
        rms = np.sqrt((m[:nb * blk].reshape(nb, blk) ** 2).mean(axis=1) + 1e-12)
        db = 20 * np.log10(rms)
        sil = db < _RVC_SILENCE_DB
        sil_frames += int(sil.sum())
        tot_frames += nb
        if (~sil).sum():
            voiced.append(float(np.median(db[~sil])))
        if nb:
            quiet_pool.append(float(np.percentile(db, 10)))
        pad = np.concatenate(([False], sil, [False]))
        edge = np.diff(pad.astype(np.int8))
        lens = (np.flatnonzero(edge == -1) - np.flatnonzero(edge == 1)) * blk / sr
        if len(lens):
            rep["longest_silence_sec"] = max(rep["longest_silence_sec"], float(lens.max()))
            rep["silence_runs_over_5s"] += int((lens > 5).sum())
    rep["unreadable"] = unreadable[:20]
    rep["total_sec"] = round(total_sec, 1)
    rep["sample_rates"] = sorted(srs)
    rep["channels"] = sorted(chs)
    rep["peak_dbfs"] = round(20 * np.log10(max(max(peaks), 1e-9)), 1) if peaks else None
    rep["voiced_dbfs"] = round(float(np.median(voiced)), 1) if voiced else None
    rep["noise_floor_dbfs"] = round(float(np.median(quiet_pool)), 1) if quiet_pool else None
    rep["silence_ratio"] = round(sil_frames / tot_frames, 3) if tot_frames else None
    rep["clipped_ratio"] = round(clip_samples / tot_samples, 5) if tot_samples else 0.0
    rep["est_slices"] = max(1, round(total_sec / 3.7))   # 预处理切片 per=3.7s
    w = rep["warnings"]
    if unreadable:
        w.append(f"{len(unreadable)} 个文件读不出来（{('、'.join(unreadable[:3]))}），训练不会用到它们")
    if total_sec < 180:
        w.append(f"总时长只有 {total_sec / 60:.1f} 分钟：官方口径 10 分钟起，素材太少音色必然发虚")
    if rep["silence_ratio"] is not None and rep["silence_ratio"] > 0.35:
        w.append(f"静音占 {rep['silence_ratio'] * 100:.0f}%：留白太多会把模型喂成'爱哼空拍'，"
                 "按 >5 秒的静音切开、去掉纯前奏尾奏")
    if rep["silence_runs_over_5s"]:
        w.append(f"有 {rep['silence_runs_over_5s']} 处长于 5 秒的静音段（官方训练提示要求切开）")
    if rep["clipped_ratio"] > 0.0005:
        w.append(f"爆音采样占 {rep['clipped_ratio'] * 100:.2f}%：录音削波了，重新导出、把音量留出余量")
    if rep["peak_dbfs"] is not None and rep["peak_dbfs"] > -0.3:
        w.append("峰值顶到 0 dBFS：没余量了，容易和爆音一起进去")
    if rep["voiced_dbfs"] is not None and rep["voiced_dbfs"] < -30:
        w.append(f"人声响度只有约 {rep['voiced_dbfs']} dBFS：太轻，规范化到 -16~-12 再练")
    if any(sr < 32000 for sr in srs):
        w.append(f"有低于 32k 的采样率（{sorted(srs)}）：素材本身糊，练出来也糊")
    if len(srs) > 1:
        w.append(f"采样率不统一（{sorted(srs)}）：预处理会统一到 40k，但低的那批不会因此变清晰")
    if chs and chs != {1}:
        w.append("立体声素材会被折成单声道参与训练（不影响流程，只是提醒）")
    # 净化建议（评审 A1 第 1 条）：底噪＝最安静 10% 帧的中位电平（留白段的能量），
    # 这是机器能可靠量出来的那半边；混响量不准就直说量不准，让人按素材类型选档——
    # 不拿一个假指标冒充分档依据。
    nf = rep["noise_floor_dbfs"]
    if nf is None:
        rep["clean_advice"] = {"tier": "off", "measured": False,
                               "reasons": ["整批素材没有一帧能解码出波形，底噪无从判断——"
                                           "素材是现场/带混响的，请手动选「轻」净化"]}
    elif nf > -42:
        rep["clean_advice"] = {"tier": "medium", "measured": True,
                               "reasons": [f"最安静的一档仍在 {nf} dBFS 上下（留白时噪声清晰可闻）："
                                           "建议「中」档净化"]}
        w.append(f"底噪约 {nf} dBFS 偏高：这批素材值得净化后再练（见净化档位选择）")
    elif nf > -55:
        rep["clean_advice"] = {"tier": "light", "measured": True,
                               "reasons": [f"留白能量约 {nf} dBFS：有一定噪声，"
                                           "建议「轻」净化（去混响 + 轻降噪）"]}
    else:
        rep["clean_advice"] = {"tier": "off", "measured": True,
                               "reasons": [f"留白能量约 {nf} dBFS（够低）：降噪这步可省；"
                                           "混响机器判不准——上传的是整首现场时请手动选「轻」"]}
    sug, why = _rvc_suggest_epochs(total_sec)
    rep["suggest_epochs"] = sug
    rep["advice"] = why
    pace = _rvc_train_pace()
    rep["pace"] = pace
    if pace:
        est_sec = pace["sec_per_slice_epoch"] * rep["est_slices"] * sug
        rep["epoch_sec_est"] = round(pace["sec_per_slice_epoch"] * rep["est_slices"], 1)
        rep["est_min"] = round(est_sec / 60, 1)
        rep["pace_note"] = (f"按本机实测（任务 {pace['source']}：{pace['samples_used']} 个切片 "
                            f"{pace['epoch_sec']} 秒/轮）折算，估算只含训练那一步")
    else:
        rep["est_min"] = None
        rep["pace_note"] = ("本机还没有一次跑完整训练的记录，所以给不出耗时预估；"
                            "第一次成功训练后这里会自动有数")
    return rep


@router.post("/rvc/train/check")
async def rvc_train_check(files: list[UploadFile]):
    """上传素材先体检（不启动训练）：报告落盘，确认后用返回的 id 直接开练，不必重传。"""
    if not files:
        raise HTTPException(status_code=400, detail="需要至少一个音频文件")
    if not RVC_PY.is_file():
        raise HTTPException(status_code=500, detail="rvc python 环境缺失（py312/python.exe）")
    rid = _new_id()
    ds = RVC_TRAIN_DIR / rid / "dataset"
    ds.mkdir(parents=True, exist_ok=True)
    budget = {"used": 0}
    for i, f in enumerate(files):
        ext = Path(f.filename or "s.wav").suffix.lower() or ".wav"
        if ext not in _RVC_AUDIO_EXTS:
            ext = ".wav"
        await _stream_upload_to(f, ds / f"sample_{i:03d}{ext}", 200 * 1024 * 1024,
                                f"第 {i + 1} 个样本", budget=budget)
    report = _rvc_dataset_scan(ds)
    job = {"id": rid, "name": "", "status": "checked", "step": "体检完成（未开始训练）",
           "ts": datetime.now().isoformat(timespec="seconds"),
           "samples": report["files"], "bytes": budget["used"], "check": report}
    _rvc_train_write(rid, job)
    with RVC_TRAIN_LOCK:
        RVC_TRAIN_JOBS[rid] = job
    return {"ok": True, "id": rid, "report": report}


@router.post("/rvc/train")
async def rvc_train(
    files: list[UploadFile] | None = File(None),
    name: str = Form(...),
    epochs: int = Form(200),
    separate_vocal: str = Form("off"),
    clean_tier: str = Form("off"),
    dataset_id: str = Form(""),
):
    """上传干声样本（或勾选自动分离后直接传完整歌曲）→ 创建音色制作任务（独占运行，与换声/生成共用 GPU）。

    separate_vocal: auto=训练前先用官方 PyMSS 逐文件分离出干净人声（上传完整歌曲时勾选）；
    off=直接训练（上传的已是干声）。
    clean_tier: light=去混响+轻降噪 / medium=中档净化（评审 A1，都在分离之后、切片之前做，
    原始素材留档可 A/B）；off=不净化。
    dataset_id: 带 POST /rvc/train/check 返回的 id 就直接用那份已体检过的素材，几十分钟的歌不必传两遍。"""
    global _RVC_TRAIN_WORKER, _RVC_TRAIN_PAUSE_REQ
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", name.strip())[:40] or "voice"
    if not 1 <= epochs <= 1200:
        raise HTTPException(status_code=400,
                            detail="训练轮数须在 1-1200 之间（30 试听 / 100 常用 / 200 精训；"
                                   "6GB 显存的卡上 1200 轮约一整天）")
    if clean_tier not in ("off", "light", "medium"):
        raise HTTPException(status_code=400,
                            detail="净化档位须是 off/light/medium（轻=去混响+轻降噪；中=更重的净化模型）")
    if RVC_MODELS_DIR.joinpath(f"{name}.pth").is_file() or any(RVC_MODELS_DIR.glob(f"{name}*.pth")):
        raise HTTPException(status_code=409, detail=f"音色名已存在：{name}")
    # 在训互斥：已有制作任务排队/运行中时拒绝，防双进程 CUDA OOM 与 logs/<name> 互写
    if _RVC_TRAIN_WORKER is not None and _RVC_TRAIN_WORKER.is_alive():
        with RVC_TRAIN_LOCK:
            busy = any(j.get("status") in ("running", "pending")
                       for j in RVC_TRAIN_JOBS.values())
        if busy:
            raise HTTPException(status_code=409, detail="已有音色制作任务在进行中，请等待完成后再提交")
    if dataset_id:
        rid = os.path.basename(dataset_id)
        ds = RVC_TRAIN_DIR / rid / "dataset"
        if not ds.is_dir():
            raise HTTPException(status_code=404,
                                detail=f"体检记录不存在：{rid}（素材目录已被清理，请重新上传并体检）")
        n_samples = sum(1 for p in ds.iterdir() if p.is_file())
        if not n_samples:
            raise HTTPException(status_code=400, detail="体检记录里没有素材文件，请重新上传")
        total = sum(p.stat().st_size for p in ds.iterdir() if p.is_file())
    else:
        if not files:
            raise HTTPException(status_code=400,
                                detail="需要上传素材文件，或带 dataset_id 复用刚体检过的那份")
        rid = _new_id()
        ds = RVC_TRAIN_DIR / rid / "dataset"
        ds.mkdir(parents=True, exist_ok=True)
        # 本次请求已落地的累计字节。训练是"一次传一沓文件"的入口，逐文件 200MB 闸门
        # 在这里等于没闸：文件数不限就能把 runtime/ 撑到爆，而炸点在后面的 F0/特征提取
        # 阶段，报出来是一串流水线错误，看不出根因是磁盘没了。
        budget = {"used": 0}
        for i, f in enumerate(files):
            ext = Path(f.filename or "s.wav").suffix.lower() or ".wav"
            if ext not in _RVC_AUDIO_EXTS:
                ext = ".wav"
            p = ds / f"sample_{i:03d}{ext}"
            await _stream_upload_to(f, p, 200 * 1024 * 1024, f"第 {i+1} 个样本",
                                    budget=budget)
        n_samples = len(files)
        total = budget["used"]
    if total < 300_000:
        raise HTTPException(status_code=400, detail="样本太少（建议 3-10 分钟干净干声）")
    prev = _rvc_train_read(rid) if dataset_id else {}
    job = {
        "id": rid, "name": name, "status": "pending", "step": "排队中",
        "epochs": epochs, "ts": datetime.now().isoformat(timespec="seconds"),
        "samples": n_samples,
        "clean_tier": clean_tier,
        # 训练速度按设备差一个量级，把当时的模式记下来，后面的预估才有出处
        "backend": backend_mode(),
        # 体检报告跟着任务走：历史页要能回看"当初这批素材长什么样"
        "check": prev.get("check") or None,
    }
    _rvc_train_write(rid, job)
    with RVC_TRAIN_LOCK:
        # 与 resume 同理：清掉上一次运行遗留的暂停标志，防新任务第一步自终止
        _RVC_TRAIN_PAUSE_REQ = False
        RVC_TRAIN_JOBS[rid] = job
    _RVC_TRAIN_WORKER = threading.Thread(target=_rvc_train_worker,
                                         args=(rid, name, epochs, separate_vocal == "auto",
                                               False, clean_tier),
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
    # 净化档位同样继承：续跑不该悄悄丢掉用户当初选的净化（dataset_purified 存在即已净化过，
    # _rvc_clean_dataset 的 resume 分支会直接复用，不会重跑）
    clean_flag = str(job.get("clean_tier") or "off")
    _RVC_TRAIN_WORKER = threading.Thread(
        target=_rvc_train_worker, args=(rid, job["name"], int(job.get("epochs") or 200),
                                        sep_flag, True, clean_flag),
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
    # 参数一律在拷文件、建目录之前规整完：这里失败的调用不该在本机留下半成品目录。
    # 也顺手改掉老写法的一个坑——`float(payload.get("index_rate") or 0.75)` 把用户
    # 主动设的 0（只用模型、不检索）当成"没填"，又悄悄放回 0.75。
    g_pitch = _rvc_pitch(payload.get("pitch"))
    g_f0, g_index, g_protect, g_rms, g_fr, g_rsr = _rvc_convert_params(
        model, payload.get("f0_method"), payload.get("index_rate"),
        payload.get("protect"), payload.get("rms_mix_rate"),
        payload.get("filter_radius"), payload.get("resample_sr"))
    g_sep = payload.get("separate_vocal", "auto") != "off"
    g_gate = payload.get("gate", "on") != "off"
    g_harmony = payload.get("strip_harmony") == "on"
    rid2 = _new_id()
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
        "id": rid2, "status": "pending", "step": "排队中", "queue_pos": 0,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "task_name": str(payload.get("task_name") or "").strip()[:100],
        "model": model, "pitch": g_pitch,
        "f0_method": g_f0,
        "index_rate": g_index,
        "protect": g_protect,
        "rms_mix_rate": g_rms,
        "filter_radius": g_fr,
        "resample_sr": g_rsr,
        "src_name": f"历史歌曲 {rid}", "src_size": src.stat().st_size,
        "src_duration": src_duration, "src_rid": rid,
        "separate_vocal": bool(g_sep),
        "gate": g_gate,
        "strip_harmony": g_harmony,
    }
    with _RVC_LOCK:
        _RVC_JOBS[rid2] = job
    pos = _rvc_submit((rid2, job, src, in_dir, model, g_pitch, g_f0, g_index,
                       g_protect, g_rms, g_sep, g_gate, g_harmony, g_fr, g_rsr))
    with _RVC_LOCK:
        cur = _RVC_JOBS.get(rid2)
        if cur is not None and cur.get("status") == "pending":
            cur["queue_pos"] = pos
            job = cur
    return {"ok": True, "id": rid2, "job": job, "position": pos}


# --------------------------------------------------------------------------- #
# 音色库（参考音频 + 参考文本）
# --------------------------------------------------------------------------- #
@router.get("/voices")
def list_voices():
    return {"voices": voices.list_voices()}


@router.post("/voices")
async def save_voice(
    name: str = Form(...),
    reference_text: str = Form(""),
    audio: UploadFile = File(...),
):
    suffix = Path(audio.filename or "prompt.wav").suffix.lower() or ".wav"
    # 走与其它上传同一个流式闸门：以前是 audio.file.read() 整读进内存且无上限，
    # 一个大文件就能把网关顶到 OOM（还会顺带打死正在跑的训练）。
    tmp = ROOT / "tmp" / "voices" / (datetime.now().strftime("%H%M%S_") + os.urandom(2).hex() + suffix)
    try:
        await _stream_upload_to(audio, tmp, 100 * 1024 * 1024, "音色参考音频")
        data = tmp.read_bytes()
    finally:
        tmp.unlink(missing_ok=True)
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
    _CAPS = {"style": _MAX_STYLE, "lyrics": _MAX_LYRICS, "abc": _MAX_ABC}
    for key in ("style", "lyrics", "cot", "abc", "seed", "cfg", "steps",
                "gender", "abc_temperature", "abc_top_p", "abc_top_k",
                "semantic_temperature", "semantic_top_p", "semantic_top_k"):
        if key in payload and payload[key] is not None:
            val = payload[key]
            # 模板的三本文本用与生成入口相同的上限：以前统一砍到 4000 字符，
            # 比 _MAX_ABC/_MAX_LYRICS 短，回填出的谱会比原稿少一截且不留痕迹。
            if isinstance(val, str):
                tpl[key] = _limit_text(f"模板字段 {key}", val, _CAPS.get(key, 4000))
            else:
                tpl[key] = val
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