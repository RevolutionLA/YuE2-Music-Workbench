"""开源兼容网关 —— 替代闭源的 `main.cp312-win_amd64.pyd`。

这个仓库原本的结构是「编译网关内核 main.pyd + 外挂层 app.py」：`app.py:45` 无条件
`import main`、`:59` `app = main.app`，缺内核时整个网关连 import 都过不去。

本机部署前先量过内核到底还替工作台承担了什么（结论都在下面的实测记录里）：

  * 13 页 UI **不依赖内核**：`app.py:9196` 用 `_INDEX_ROUTE` 自己注册 `/` 并在
    :9209-9215 把路由表里其它 `/` 全删掉、把自己插到最前，`static/` 只有一个
    484KB 自包含的 index.html（无外链 js/css，也不需要 StaticFiles 挂载）。
  * AI 写词页不依赖内核：`src/ai_lab.py` / `src/ai_router.py` 都在仓库里。
  * 前端 62 个 `/api/*` 路径里 `app.py` 自己实现了 61 个，内核残留只有
    `/api/health`（页脚健康灯与 ⑬ 的退回路径）与 `/api/music/generate`
    （`app.py:2354` 的唯一内核调用点）。
  * 内核在导入时拉起 audio.cpp 引擎（`app.py:71` 的注释说明它 spawn 时没带
    CREATE_NO_WINDOW，所以 app.py 才要额外压窗口）。

所以本文件只做这四件事，并且**不假装还原内核的其它行为**：

  1. `app`：一个 FastAPI 实例，供 app.py 外挂路由、中间件与根路由。
  2. `settings`：直接复用仓库的 `settings.py` 单例（app.py 会写 `open_browser=False`）。
  3. `GET /api/health`：按前端 `static/index.html:4976` 写下的形状回
     `{gateway, audiocpp:{status,backend}, models:{...}}`，数据全部现取引擎，
     查不到就报缺席，不编造 "ok"。
  4. `POST /api/music/generate`：把 app.py 转发的字段筛成引擎认得的请求，打给
     `POST /v1/tasks/run`，把回包里的 WAV 原样以字节流返回，并带 `X-Seed`
     （`app.py:2549` 会读它写进任务记录）。

两条实测来的契约，不是照文档猜的：
  * 引擎只从 `request.options.*` 取参数：只把 style 写在 `request.style` 时，
    本机 v0.9.2 当面回 `{"error":{"message":"Yue2 requires non-empty style"}}`
    （2026-10-21 00:34 实测）。`app.py:2594` 那段直连引擎的既有实现之所以两层都写，
    就是这个原因，这里保持同一形状。
  * 可送字段以引擎包自带的 `cpp/model_specs/yue2.json` 为准（v0.9.2 的
    `options.request` 共 27 项）。白名单运行时读那个文件，不手抄常量；读不到就
    在启动时明确报错，而不是把未知字段塞给引擎让它 400。

已知与内核的行为差：内核是 pydantic 模型（`extra=ignore`）做参数校验，这里改成
「按 spec 白名单筛选 + 三条与内核文案一致的显式校验」；筛掉的字段不会留痕，
所以额外把被丢弃的键写进日志，便于发现「界面填了但没送进去」这类假绿灯。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

ROOT = Path(__file__).resolve().parent
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from settings import settings  # 复用仓库那份 settings（端口、引擎路径、默认档位都在里面）  # noqa: E402

# --------------------------------------------------------------------------- #
# 参数白名单：只信引擎包自带的 spec
# --------------------------------------------------------------------------- #
SPEC_PATHS = [ROOT / "cpp" / "model_specs" / "yue2.json"]
_ENGINE_OPTIONS: list[str] = []
_SPEC_SOURCE: str = ""

for _cand in SPEC_PATHS:
    if _cand.is_file():
        try:
            _spec = json.loads(_cand.read_text(encoding="utf-8"))
            _ENGINE_OPTIONS = [
                str(o.get("name"))
                for o in (_spec.get("options") or {}).get("request", [])
                if o.get("name")
            ]
            _SPEC_SOURCE = str(_cand)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"引擎参数表读不出来：{_cand}（{exc}）。没有它就不知道该往 /v1/tasks/run 送哪些字段，"
                "拒绝降级成手抄常量。"
            ) from exc
        break

if not _ENGINE_OPTIONS:
    raise RuntimeError(
        "找不到 audio.cpp 的 YuE2 参数表（cpp/model_specs/yue2.json）。"
        "请确认 audio.cpp 引擎已解压到 cpp/，否则本网关不知道哪些字段能送进引擎。"
    )

# app.py 用的是工作台侧字段名，引擎用的是它自己的名字。这一张映射表是唯一的翻译处。
_FIELD_ALIAS = {"cfg_scale": "guidance_scale"}
_VALID_COT = ("full", "melody", "off")

app = FastAPI(title="YuE2 音乐工作台网关（开源兼容层）", version="deploy-shim-1")


_SHIM_LOG = ROOT / "runtime" / "data" / "logs" / "gateway_shim.log"


def _log(msg: str) -> None:
    """同时写 stdout 与文件。

    走 .bat 启动时网关是隐藏进程，stdout 没人看——之前「引擎拉起失败」就是只打在
    stdout 上，界面上一切正常，只有把网关拉回前台才看得见。现在落一份文件。
    """
    line = f"[main.py] {time.strftime('%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        _SHIM_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_SHIM_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# 引擎自启（还原内核 start_audiocpp 的那一件事；已活着就不动它）
# --------------------------------------------------------------------------- #
def _engine_alive(timeout: float = 1.5) -> bool:
    try:
        r = httpx.get(settings.audiocpp_base_url.rstrip("/") + "/health",
                      timeout=timeout, trust_env=False)
        return r.status_code < 500
    except Exception:  # noqa: BLE001
        return False


def _engine_backend() -> str:
    """与 app.py:backend_mode() 同一口径：读 engine_state，缺失按 cpu 起步。

    支持 cuda / cpu / vulkan。本机实测（Iris Xe + Vulkan 构建）同一条请求从 582~672 秒
    降到 25~38 秒，所以没有 N 卡但有能用的核显时，vulkan 是首选；构建里没有对应后端时
    引擎会当场报错，不会静默降级成别的后端。
    """
    state = ROOT / "runtime" / "data" / "engine_state.json"
    try:
        mode = str(json.loads(state.read_text(encoding="utf-8")).get("backend") or "").lower()
    except Exception:  # noqa: BLE001
        mode = ""
    if mode not in ("cpu", "cuda", "vulkan"):
        # 与 app.backend_mode() 同一口径：state 缺失时看 server.json，最后才是 cpu（评审 B17）
        try:
            cfg = json.loads((ROOT / settings.audiocpp_config).read_text(encoding="utf-8"))
            mode = str(cfg.get("backend") or "").strip().lower()
        except Exception:  # noqa: BLE001
            mode = ""
    return mode if mode in ("cpu", "cuda", "vulkan") else "cpu"


_AUDIOCPP_PROC = None


def start_audiocpp() -> bool:
    """拉起 audio.cpp 引擎。cwd 必须是 cpp\，配置里的模型路径按它相对解析。"""
    global _AUDIOCPP_PROC
    if _engine_alive():
        return True
    exe = ROOT / settings.audiocpp_bin
    cfg = ROOT / settings.audiocpp_config
    if not exe.is_file():
        _log(f"✗ 引擎不在：{exe}（缺 audio.cpp 运行件，生成不可用）")
        return False
    if not cfg.is_file():
        _log(f"✗ 引擎配置不在：{cfg}")
        return False
    log_dir = ROOT / "runtime" / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    args = [str(exe), "--config", str(cfg), "--backend", _engine_backend(),
            # 只给 --log，让引擎把诊断写到我们重定向的 stdout：再叠一个 --log-file 会让
            # 引擎自己去开同一个文件，与我们手里的追加句柄撞（Windows 共享冲突），
            # 而且撞输了的那一方只留下 0 字节日志，比崩了更难查
            "--log"]
    kwargs: dict[str, Any] = {"cwd": str(exe.parent)}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        # Popen 必须在 with 里面：句柄一出 with 就关了，之前把它留在外面导致每次自启
        # 都抛「I/O operation on closed file」——引擎从没起来过，却只留下一份 0 字节日志
        with open(log_dir / "audiocpp_server.log", "ab", buffering=0) as lf:
            kwargs.update({"stdout": lf, "stderr": subprocess.STDOUT})
            _AUDIOCPP_PROC = subprocess.Popen(args, **kwargs)  # noqa: S603
    except Exception as exc:  # noqa: BLE001
        _log(f"✗ 引擎拉起失败：{exc}")
        return False
    deadline = time.time() + settings.audiocpp_ready_timeout_sec
    while time.time() < deadline:
        if _engine_alive():
            _log(f"引擎就绪 :{settings.audiocpp_base_url}（backend={_engine_backend()}）")
            return True
        time.sleep(2.0)
    _log("⚠ 引擎已拉起但超时没回 /health，交给 app.py 的按需自检")
    return False


def _engine_get(path: str, timeout: float = 5.0) -> Any:
    try:
        r = httpx.get(settings.audiocpp_base_url.rstrip("/") + path,
                      timeout=timeout, trust_env=False)
        r.raise_for_status()
        return r.json()
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# GET /api/health
# --------------------------------------------------------------------------- #
def _server_json_models() -> dict:
    """从 cpp/server.json 直接拼出「当前主模型」，不再为这个字段多跑一次 HTTP。

    实测：生成期间引擎被 CPU 占满，/v1/models 这类只读请求也要等十几秒；原先
    health 里串两次 HTTP 让 /api/health 整整 19.7 秒才回话（2026-10-21 02:0x 实测），
    而启动脚本只给 3 秒探测窗口——于是"网关明明活着，脚本却说未就绪"。
    """
    try:
        cfg = json.loads((ROOT / settings.audiocpp_config).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, str] = {}
    for entry in cfg.get("models") or []:
        mid = str(entry.get("id") or "yue2")
        so = entry.get("session_options") or {}
        main_gguf = str(so.get("yue2.model_gguf") or "").strip()
        if not main_gguf:
            main_gguf = str(entry.get("path") or "").replace("\\", "/").rsplit("/", 1)[-1]
        out[mid] = main_gguf or mid
    return out


def _engine_port_listening(timeout: float = 0.3) -> bool:
    """引擎端口是否有人监听。用来把"忙得没空回话"和"进程没了"分开。"""
    try:
        port = int(settings.audiocpp_base_url.rstrip("/").rsplit(":", 1)[-1])
    except ValueError:
        port = 8080
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


@app.get("/api/health")
def health() -> JSONResponse:
    """形状照前端 :4976 的注释来：{gateway, audiocpp:{status,backend}, models:{...}}。

    三种状态必须分清（2026-10-21 三方评审 B1/T1）：
      * ok    —— 引擎回了 /health；
      * busy  —— 端口在监听、但 2 秒内没回话（生成中被打满是常态，不是死）；
      * down  —— 端口都没人监听。
    **HTTP 状态码永远 200**：看门狗把 5xx 当死（watchdog.probe），这里回 502/503 会把
    正在出歌的网关杀掉。真假信息只放在 body 的 gateway 字段里——
    `gateway` 只有引擎 ok/busy 时才是 "ok"，down 时写 "degraded"。
    """
    eng = _engine_get("/health", timeout=2.0)
    if eng:
        status = str(eng.get("status") or "ok")
        gw = "ok"
        backend = eng.get("backend")
        models = eng.get("models")
        loaded = bool(eng.get("loaded")) if "loaded" in eng else None
    elif _engine_port_listening():
        status, gw, backend, models, loaded = "busy", "ok", None, None, None
    else:
        status, gw, backend, models, loaded = "down", "degraded", None, 0, None
    out: dict[str, Any] = {"gateway": gw,
                           "audiocpp": {"status": status, "backend": backend,
                                        "models": models, "loaded": loaded}}
    names = _server_json_models()
    if names:
        out["models"] = names
    return JSONResponse(out)


# --------------------------------------------------------------------------- #
# POST /api/music/generate
# --------------------------------------------------------------------------- #
def _pick_payload(payload: dict) -> tuple[dict, list[str]]:
    """工作台字段 → 引擎字段：只认 spec 声明过的名字，其余一律丢掉并记名。"""
    opts: dict[str, Any] = {}
    dropped: list[str] = []
    for k, v in payload.items():
        if k in ("model", "task", "options", "request", "count"):
            continue
        name = _FIELD_ALIAS.get(k, k)
        if name in _ENGINE_OPTIONS:
            if v is None or v == "":
                continue
            opts[name] = v
        else:
            dropped.append(k)
    return opts, dropped


def _audio_from_response(data: dict) -> tuple[bytes, dict]:
    """从 tasks/run 回包里取音频，两条路都认，且都以字节头为准不靠猜字段名。

    * 出片（默认 stop_after=audio）：本机 v0.9.2 实测回
      `{"audio":"<base64 RIFF/WAVE>","sample_rate":48000,"channels":2,"timing":{...}}`
      —— 这正是仓库 `audiocpp.py:_extract_audio_bytes` 认的那批顶层键，说明内核当年
      就是用它解音频的（2026-10-21 00:43 实测，3.84s 片段 982,875 字节）。
    * 只出谱/只出语义（stop_after=abc|semantic）：没有音频，产物在 `artifacts` 里，
      这条由 app.py:2594 的 plan_abc 直连引擎，不经本路由；留着是为了万一有人从
      这里送 stop_after 时能给一句实话，而不是假称"没有音频＝失败"。
    """
    for key in ("audio", "wav", "data", "output"):
        value = data.get(key)
        if isinstance(value, dict):
            value = value.get("data") or value.get("b64")
        if isinstance(value, str) and value:
            raw_b64 = value.split(",", 1)[1] if value.lower().startswith("data:") else value
            try:
                raw = base64.b64decode(raw_b64)
            except Exception:  # noqa: BLE001
                continue
            if raw[:4] == b"RIFF":
                return raw, {"format": "wav", "sample_rate": data.get("sample_rate"),
                             "channels": data.get("channels")}
    arts = data.get("artifacts") or []
    if isinstance(arts, list):
        for a in arts:
            if not isinstance(a, dict):
                continue
            b64 = a.get("payload")
            if not isinstance(b64, str) or not b64:
                continue
            raw_b64 = b64.split(",", 1)[1] if b64.lower().startswith("data:") else b64
            try:
                raw = base64.b64decode(raw_b64)
            except Exception:  # noqa: BLE001
                continue
            if raw[:4] == b"RIFF":
                return raw, (a.get("meta") or {})
            fmt = str((a.get("meta") or {}).get("format") or "").lower()
            if fmt in ("wav", "audio/wav"):
                return raw, (a.get("meta") or {})
    ids = sorted({str(a.get("id")) for a in arts if isinstance(a, dict)})
    raise HTTPException(
        status_code=502,
        detail={"message": "引擎回了包但里面没有 WAV 音频（若送的是 stop_after=abc/"
                            "semantic，这条路由本来就不产音频）",
                "artifact_ids": ids, "keys": sorted(data.keys())},
    )


_CHECKPOINT_DIR = ROOT / "runtime" / "data" / "gen_checkpoints"
_CHECKPOINT_TTL = 7 * 86400          # 七天没用过的检查点自动作废
_CHECKPOINT_KEYS = ("style", "lyrics", "cot", "seed", "num_inference_steps",
                    "semantic_min_tokens", "semantic_max_tokens")


def _checkpoint_key(opts: dict) -> str:
    """同一份"可复现输入"→同一个键。种子变了就不该复用检查点。"""
    blob = json.dumps({k: opts.get(k) for k in _CHECKPOINT_KEYS},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:40]


def _checkpoint_path(opts: dict) -> Path:
    return _CHECKPOINT_DIR / f"{_checkpoint_key(opts)}.json"


def _load_checkpoint(opts: dict) -> dict | None:
    p = _checkpoint_path(opts)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if time.time() - float(data.get("ts") or 0) > _CHECKPOINT_TTL:
        p.unlink(missing_ok=True)
        return None
    return data if data.get("tokens") else None


def _save_checkpoint(opts: dict, tokens: list, abc: str) -> None:
    _CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    dest = _checkpoint_path(opts)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps({"tokens": tokens, "abc": abc or "",
                               "cot": opts.get("cot"), "ts": time.time()},
                              ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, dest)


def _drop_checkpoint(opts: dict) -> None:
    _checkpoint_path(opts).unlink(missing_ok=True)


def _two_phase_enabled(opts: dict) -> bool:
    """能不能拆成"先 AR、后渲染"。

    外部自带谱（abc）或调用方显式指定 stop_after 时不拆：前者 AR 的产物不再是
    引擎自己计划的谱，后者本来就是"只跑一段"的调用，再拆只会把语义弄乱。
    想关掉整条机制：环境变量 YUE2_NO_TWO_PHASE=1。
    """
    if os.environ.get("YUE2_NO_TWO_PHASE", "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    if str(opts.get("abc") or "").strip():
        return False
    return "stop_after" not in opts


def _phase1_body(model_id: str, opts: dict) -> dict:
    o = dict(opts)
    o["stop_after"] = "semantic"      # 只跑 AR：拿语义 token + 引擎自己计划的谱
    return {"model": model_id, "request": {"task": "gen", **o, "options": dict(o)}}


def _phase2_body(model_id: str, opts: dict, tokens: list, abc: str) -> dict:
    o = dict(opts)
    n = len(tokens)
    o["semantic_prefix"] = tokens
    o["semantic_min_tokens"] = n
    o["semantic_max_tokens"] = n     # 上下界相等 = AR 一个 token 都不再采样
    o.pop("stop_after", None)
    cot = str(o.get("cot") or "full").lower()
    if cot in ("full", "melody") and abc:
        o["abc"] = abc
        o["cot"] = cot
    else:
        o["cot"] = "off"             # 没有谱就只能走 off；前缀照样生效
    return {"model": model_id, "request": {"task": "gen", **o, "options": dict(o)}}


def _artifact_payload(data: dict, want_id: str, want_format: str) -> str:
    """从 tasks/run 的 artifacts 里取指定产物（base64 原文）。"""
    for a in data.get("artifacts") or []:
        if not isinstance(a, dict):
            continue
        meta = a.get("meta") or {}
        if a.get("id") == want_id or (meta.get("format") or "") == want_format:
            return str(a.get("payload") or "")
    return ""


def _engine_body(model_id: str, opts: dict) -> dict:
    """送进 /v1/tasks/run 的转发体。

    两层都写是实测结论，不是冗余：把 style 只放在 `request.style` 时，本机 v0.9.2
    当面回 `Yue2 requires non-empty style`（2026-10-21 00:34）；引擎从
    `request.options.*` 取参数，而 `app.py:2623-2626` 那段既有的直连实现同样两层都写。
    """
    return {"model": model_id,
            "request": {"task": "gen", **opts, "options": dict(opts)}}


@app.post("/api/music/generate")
async def music_generate(request: Request) -> Response:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象")
    return await generate_response(payload)


def _in_test_runner() -> bool:
    """当前进程是不是测试收集器本身（unittest/pytest）。

    只认"进程是怎么启动的"，不认 sys.modules 里有没有 unittest：numpy.testing /
    transformers 这类库一加载就会把它顺手导进来，网关跑过一次 ⑭ 歌词对齐（FunASR）
    之后再生成，就会被误判成测试进程而 503——2026-10-30 的基准实测踩到过。
    项目自己的测试由 tests/conftest.py 设 YUE2_TESTING=1 兜底；这里只补
    `python -m unittest` / `python -m pytest` 两种启动方式的识别。
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    name = str(getattr(spec, "name", "") or "")
    return name in ("unittest.__main__", "pytest.__main__", "py.test")


async def generate_response(payload: dict) -> Response:
    """生成一首歌：内核路由与 app.py 的进程内调用共用这一段，避免反向 HTTP。"""
    style = str(payload.get("style") or "").strip()
    if not style:
        raise HTTPException(status_code=400, detail="style is required")
    cot = str(payload.get("cot") or settings.default_cot).strip().lower()
    if cot not in _VALID_COT:
        # 文案与内核一致（蓝军评审从 main.pyd 字符串表实测命中过这句）
        raise HTTPException(status_code=400,
                            detail=f"cot must be one of: {', '.join(_VALID_COT)}")
    abc = str(payload.get("abc") or "")
    if abc.strip() and cot == "off":
        raise HTTPException(status_code=400,
                            detail="external ABC requires cot=melody or cot=full")

    # 测试进程里绝不允许真生成：一次手滑就是几分钟 GPU 和一份假产物。
    # 需要真引擎的用例显式设 YUE2_ALLOW_REAL_ENGINE=1（冒烟脚本走真实服务，不受影响）。
    if (os.environ.get("YUE2_TESTING") == "1" or _in_test_runner()) \
            and os.environ.get("YUE2_ALLOW_REAL_ENGINE") != "1":
        raise HTTPException(
            status_code=503,
            detail="测试环境禁止真生成：mock main.generate_response，或显式设 YUE2_ALLOW_REAL_ENGINE=1",
        )

    opts, dropped = _pick_payload(payload)
    opts["style"] = style
    opts["cot"] = cot
    if dropped:
        _log(f"丢弃了引擎不认的字段：{sorted(set(dropped))}")

    seed = opts.get("seed")
    try:
        seed = int(seed) if seed is not None else None
    except (TypeError, ValueError):
        seed = None
    if seed is None:
        import secrets as _secrets
        seed = int(_secrets.randbelow(2 ** 31))
        opts["seed"] = seed

    body = _engine_body(str(payload.get("model") or settings.audiocpp_model_id), opts)

    # 端口在监听就别再拉第二个引擎：生成中 /health 可能 2 秒不回，但进程是好的，
    # 再 Popen 一个只会撞端口（三方评审 T1）。真正没进程时才拉起——
    # 拉起是阻塞调用（最长 90 秒），必须丢线程池，否则整个事件循环冻结（B5）。
    if not _engine_alive(timeout=2.0) and not _engine_port_listening():
        if not await run_in_threadpool(start_audiocpp):
            raise HTTPException(status_code=502, detail="audio.cpp 引擎不可用，无法生成")

    url = settings.audiocpp_base_url.rstrip("/") + "/v1/tasks/run"
    timeout = httpx.Timeout(settings.audiocpp_timeout_sec, connect=10.0)
    model_id = str(payload.get("model") or settings.audiocpp_model_id)

    async def _post(req_body: dict):
        async with httpx.AsyncClient(base_url=settings.audiocpp_base_url.rstrip("/"),
                                     timeout=timeout, trust_env=False) as c:
            return await c.post("/v1/tasks/run", json=req_body)

    one_shot = _engine_body(model_id, opts)   # 后端不支持语义产物时退回到它
    ckpt = _load_checkpoint(opts) if _two_phase_enabled(opts) else None
    if ckpt:
        # 上次已经跑完 AR：直接从检查点渲染 NAR+VAE，AR 一秒都不用重跑。
        _log(f"从检查点恢复：跳过 AR（tokens={len(ckpt['tokens'])}，"
             f"检查点 {round((time.time() - float(ckpt.get('ts') or 0)) / 3600, 1)} 小时前）")
        body = _phase2_body(model_id, opts, ckpt["tokens"], str(ckpt.get("abc") or ""))
    elif _two_phase_enabled(opts):
        # 首跑：先只跑 AR（拿语义 token 当检查点），别再发一次性请求——
        # 漏这一步会先完整生成一遍、失败后补跑一次，白烧一倍时间（00:50 实测 210s vs 101s）。
        body = _phase1_body(model_id, opts)
    try:
        r = await _post(body)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502,
                            detail=f"audio.cpp {url} 请求失败：{exc}") from exc

    # ---- 两阶段：AR（检查点）→ 渲染 ----
    if r.status_code < 400 and _two_phase_enabled(opts) and not ckpt:
        tokens, abc = None, ""
        try:
            ar = r.json()
            sem_b64 = _artifact_payload(ar, "semantic", "yue2-semantic")
            abc_b64 = _artifact_payload(ar, "score", "abc")
            if sem_b64:
                sem = json.loads(base64.b64decode(sem_b64).decode("utf-8"))
                tokens = (sem.get("tokens") or sem.get("semantic") or []
                          if isinstance(sem, dict) else sem)
                abc = base64.b64decode(abc_b64).decode("utf-8") if abc_b64 else ""
        except Exception as exc:  # noqa: BLE001
            _log(f"⚠ AR 产物解析失败（{type(exc).__name__}: {exc}）→ 退回一次性生成")
            tokens = None
        if not isinstance(tokens, list) or not tokens:
            # 这个后端不产语义产物（CPU 后端实测会这样）→ 退回一次性生成。
            # 绝不因为"建不了检查点"把一首本来能出的歌弄失败。
            _log("⚠ 引擎没回语义 token（该后端不支持 stop_after=semantic）→ 退回一次性生成，无检查点")
            try:
                r = await _post(one_shot)
            except httpx.HTTPError as exc:
                raise HTTPException(status_code=502,
                                    detail=f"audio.cpp {url} 请求失败：{exc}") from exc
        else:
            _save_checkpoint(opts, tokens, abc)
            _log(f"AR 检查点已保存（tokens={len(tokens)}，谱={len(abc)} 字符）→ 渲染 NAR+VAE")
            body = _phase2_body(model_id, opts, tokens, abc)
            try:
                r = await _post(body)
            except httpx.HTTPError as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"渲染阶段请求失败：{exc}；AR 检查点已保存，重新提交同一首会从检查点继续"
                ) from exc

    if r.status_code >= 400 and _two_phase_enabled(opts):
        # 渲染/AR 失败时把"可续跑"说清楚：AR 检查点是否已存在决定还能不能省那一段。
        has_ck = _load_checkpoint(opts) is not None
        hint = ("；AR 检查点已保存，重新提交同一首会跳过 AR 继续（不会从头重算）"
                if has_ck else "；AR 阶段尚未完成，没有检查点可用")
        try:
            _err = r.json()
        except ValueError:
            _err = r.text[:400]
        raise HTTPException(status_code=502, detail=f"{_err}{hint}")

    if r.status_code >= 400:
        detail: Any
        try:
            body_json = r.json()
        except ValueError:
            detail = r.text[:2000]
        else:
            err = body_json.get("error") if isinstance(body_json, dict) else None
            if isinstance(err, dict):
                # 引擎的错误形状是 {"error":{"message","type"}}，而 app.py 只认
                # {"detail": ...}：这里把 message 原话搬进 detail，一个字都不改写，
                # 显存/内存类判据（app.py:_is_vram_error）靠的就是原文里的关键词。
                detail = str(err.get("message") or err)
            else:
                detail = body_json
        # 参数被引擎拒绝算客户端错，其余按上游故障，别把 5xx 一律洗成 400
        code = r.status_code if r.status_code in (400, 404, 422) else 502
        raise HTTPException(status_code=code, detail=detail)

    try:
        data = r.json()
    except ValueError as exc:
        raise HTTPException(status_code=502,
                            detail=f"audio.cpp 回了非 JSON：{exc}") from exc

    wav, meta = _audio_from_response(data)
    if not wav:
        raise HTTPException(status_code=502, detail="引擎回的 WAV 是空字节")
    if _two_phase_enabled(opts):
        _drop_checkpoint(opts)       # 成品已出，检查点用完即弃（否则同 seed 重提会换波形）

    wall = (data.get("timing") or {}).get("wall_ms")
    headers = {"X-Seed": str(seed), "Content-Disposition": 'inline; filename="song.wav"'}
    if wall is not None:
        headers["X-Engine-Wall-Ms"] = str(wall)
    if meta.get("sample_rate"):
        headers["X-Sample-Rate"] = str(meta["sample_rate"])
    _log(f"生成完成：{len(wav)} 字节，wall_ms={wall}，seed={seed}")
    return Response(content=wav, media_type="audio/wav", headers=headers)


# --------------------------------------------------------------------------- #
# 启动即拉起引擎（与内核同行为）。测试或只想改代码不想动进程时设
# YUE2_NO_ENGINE_AUTOSTART=1 跳过。
# --------------------------------------------------------------------------- #
if (not _in_test_runner()
        and os.environ.get("YUE2_NO_ENGINE_AUTOSTART", "") not in ("1", "true", "yes")):
    start_audiocpp()
