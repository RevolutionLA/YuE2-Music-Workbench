"""系统域路由：模型清单、能力清单、设置、后端模式、VAE 窗口、系统信息。

从 app.py 拆出。所有依赖（常量/助手/引擎重启）通过 `host` 在**请求时**解析，
因此测试对 `app._active_vae_config` 之类的 patch 依旧生效；`host` 由 app.py 传入
`sys.modules[__name__]`，本模块不反向 import app，避免循环依赖。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException


def models_list(host: Any):
    return {"models": host.available_models()}


def models_current(host: Any):
    """返回当前 server.json 生效的模型（相对 cpp/model 的路径，供前端下拉回显）。"""
    try:
        cfg = host._read_server_json()
        p = (cfg.get("models") or [{}])[0].get("path", "")
    except Exception:
        p = ""
    p = (p or "").replace("\\", "/")
    if p.startswith("model/"):
        p = p[len("model/"):]
    return {"path": p}


def capabilities(host: Any):
    """本机当前真正能用/不能用的功能清单（不依赖 VAE 的功能也要让人看见）。"""
    feats: list[dict] = []

    def add(label: str, ok: bool, note: str = "") -> None:
        feats.append({"label": label, "ok": bool(ok), "note": note})

    add("歌词 TXT 导出", True, "不需要 GPU/VAE；歌词框和任务卡都能下载")
    lrc_ok = host.lrc_align_mod is not None
    add("LRC / 逐字 eLRC", lrc_ok,
        "需要音频时间轴；已有音频时不用重新生成歌曲" if lrc_ok
        else "缺强制对齐依赖（funasr/模型），当前只有旧链路")
    add("乐谱提取 / 转谱", (host.ROOT / "checkpoints" / "model.safetensors").is_file(),
        "SheetSage2/MERT；不依赖 VAE")
    sep_ok, sep_why = host._sep_available()
    add("人声/伴奏分离", sep_ok, "" if sep_ok else sep_why)
    rvc_ok = host.RVC_PY.is_file() and (host.RVC_DIR / "infer" / "cli.py").is_file()
    add("换声 / 音色制作", rvc_ok,
        "RVC runtime" if rvc_ok else "runtime/rvc 缺 infer/cli.py")
    key_ok = host._deepseek_key_set()
    add("AI 写词", key_ok, "" if key_ok else "需在 ⑫ 设置里填 DeepSeek Key")
    gen_ok = host.AUDIOCPP_BIN.is_file() and host.SERVER_JSON.is_file()
    add("歌曲生成（AR/NAR/VAE）", gen_ok,
        "VAE 阶段受显存限制；长歌会在开跑前被预检拦下，不会白等"
        if gen_ok else "缺 audio.cpp 引擎或 cpp/server.json")
    return {"features": feats}


def settings_get(host: Any):
    return {"cpu_fallback_allowed": host._cpu_fallback_allowed(),
            "cpu_fallback_source": host._cpu_fallback_source(),
            "deepseek_api_key_set": host._deepseek_key_set(),
            # 来源必须可见：env 来源时本页清除不掉，前端据此换文案（issue #4-2）
            "deepseek_key_source": host._deepseek_key_source()}


def settings_set(host: Any, payload: dict):
    messages: list[str] = []
    if "cpu_fallback_allowed" in payload:
        if not isinstance(payload.get("cpu_fallback_allowed"), bool):
            raise HTTPException(status_code=400,
                                detail="cpu_fallback_allowed 必须是 true/false")
        host._settings_write({"cpu_fallback_allowed": payload["cpu_fallback_allowed"]})
        messages.append("已允许 GPU 分配失败时自动降级到 CPU：会整首重算，核显上可能慢十几倍。"
                        if payload["cpu_fallback_allowed"] else
                        "已禁止自动降级 CPU：GPU 分配失败会直接报错，AR 检查点会保留，可稍后重试。")
    if "deepseek_api_key" in payload:
        key = payload.get("deepseek_api_key")
        if not isinstance(key, str) or len(key) > 500:
            raise HTTPException(status_code=400,
                                detail="deepseek_api_key 必须是字符串（≤500 字符）")
        overridden = host._deepseek_key_source() == "env"
        host._deepseek_key_write(key)
        if key.strip():
            messages.append("DeepSeek Key 已保存到 secrets/deepseek_api_key.txt"
                            + ("（但" + host._deepseek_env_note() + "）" if overridden else ""))
        else:
            messages.append("DeepSeek Key 文件已清除")
    if payload.get("deepseek_api_key_clear") is True:
        host._deepseek_key_write("")
        # 清掉文件后还要看 env：是 env 在撑着"已配置"，就不能只报"已清除"
        if host._deepseek_key_source() == "env":
            messages.append("已删除本页保存的 Key，但" + host._deepseek_env_note())
        else:
            messages.append("DeepSeek Key 已清除")
    if not messages:
        raise HTTPException(status_code=400, detail="没有可保存的设置")
    return {"ok": True,
            "cpu_fallback_allowed": host._cpu_fallback_allowed(),
            "deepseek_api_key_set": host._deepseek_key_set(),
            "deepseek_key_source": host._deepseek_key_source(),
            "message": "；".join(messages)}


def backend_mode_get(host: Any):
    ok, free = host._vram_ok_for_gpu()
    return {
        "mode": host.backend_mode(),
        # 前端据此决定"切GPU"该提交 cuda 还是 vulkan：核显机器上 GPU≠CUDA
        "available": host.engine_backends(),
        "cpu_fallback_allowed": host._cpu_fallback_allowed(),
        "vram_free_mb": free,
        "vram_ok": ok,
        "headroom_mb": host._VRAM_HEADROOM_MB,
    }


def backend_mode_set(host: Any, payload: dict):
    want = (payload.get("mode") or "").strip().lower()
    have = host.engine_backends()
    if want not in ("cuda", "cpu", "vulkan"):
        raise HTTPException(
            status_code=400,
            detail=f"mode must be one of: cuda, cpu, vulkan（本机引擎构建支持：{','.join(have)}）",
        )
    # "GPU" 落到这份构建真能跑的后端：CUDA 构建→cuda，Vulkan 构建→vulkan，
    # 两者都没有就只能在 CPU 上跑，并把原因写进回包（前端会原样弹出来）。
    mode, note = host.resolve_backend(want)
    if mode == host.backend_mode():
        return {"ok": True, "mode": mode, "message": note or f"引擎已是 {mode} 模式",
                "available": have}
    host._engine_state_write({"backend": mode})
    host._restart_audiocpp(str(host.SERVER_JSON))
    base = ("已切换 CPU 模式：显存不足也能出结果，但速度慢约 5-10 倍"
            if mode == "cpu" else
            "已切到 Vulkan（集显/核显）模式：没有 N 卡时通常比 CPU 快一个数量级"
            if mode == "vulkan" else "已切回 GPU（CUDA）模式")
    return {
        "ok": True,
        "mode": mode,
        "message": f"{note}；{base}" if note else base,
        "available": have,
    }


def vae_window_get(host: Any):
    """设置页开关的当前状态：**引擎实际会读到的**那份 sidecar 的窗口值 + 各文件明细。"""
    configs = host._vae_window_configs()
    if not configs:
        raise HTTPException(
            status_code=404,
            detail="没找到 VAE sidecar 配置（cpp/model/sidecars/yue2-vae-config.json 或 "
                   "cpp/model/*/sidecars/yue2-vae-config.json）；"
                   "请先运行「一键补齐运行件.bat」把模型下载完",
        )
    active = host._active_vae_config()
    if active is not None and active not in configs:
        active = None
    per = [{"path": host._rel(p), "frames": host._vae_window_of(p),
            "active": p == active} for p in configs]
    head = next((c for c in per if c["active"]), per[0])
    model = host._active_model_gguf()
    out = {"frames": head["frames"], "choices": list(host._VAE_WINDOW_CHOICES),
           "configs": per,
           "active": head["path"] if head["active"] else None,
           "model": model.name if model is not None else ""}
    if active is None:
        out["note"] = (f"定位不到当前加载模型的 sidecar（cpp/server.json 缺 models[].path 或"
                       f"该目录下没有 sidecars/），开关只能改全部 {len(per)} 份；多档不一致时"
                       f"可能改不到引擎真正读的那份。")
    # 多份不一致时把差异挑明：开关只报当前那份，别的档差着值是正常的（用户可能故意分开设）
    others = [c for c in per if not c["active"] and c["frames"] != head["frames"]]
    if others:
        out["mismatch"] = [c["path"] for c in others]
    return out


def vae_window_set(host: Any, payload: dict):
    """切换 VAE 解码窗口并重启引擎。frames 只接受 512 / 1024（UI 两档）。

    默认**只改当前加载模型那一份**（`all: true` 才批量改全部）：老代码改全部分，
    而扁平布局下那句 glob 一份都匹配不到，于是出现"页面显示 512、引擎读的仍是 1024"。
    """
    try:
        frames = int(payload.get("frames"))
    except (TypeError, ValueError):
        frames = -1
    if frames not in host._VAE_WINDOW_CHOICES:
        raise HTTPException(
            status_code=400,
            detail=f"frames 只支持 {list(host._VAE_WINDOW_CHOICES)}"
                   "（512=低显存约 1.95GB，1024=官方默认约 3.89GB）",
        )
    configs = host._vae_window_configs()
    if not configs:
        raise HTTPException(status_code=404, detail="没找到 VAE sidecar 配置，无法切换窗口")
    active = host._active_vae_config()
    if active is None or active not in configs:
        # 定位不到在用那份：只能全改（旧行为），但必须说清，不能让用户以为只改了一份
        active = None
        targets = configs
        scope = f"定位不到当前加载模型，已改全部 {len(configs)} 份"
    elif payload.get("all") is True:
        targets = configs
        scope = f"已改全部 {len(configs)} 份"
    else:
        targets = [active]
        scope = "只改当前加载的那份"
    changed: list[dict] = []
    for p in targets:
        old, new = host._vae_window_patch(p, frames)
        changed.append({"path": host._rel(p), "old": old, "new": new,
                        "active": p == active})
    touched = [c for c in changed if c["new"] != c["old"]]
    if not touched:
        return {"ok": True, "frames": frames, "restarted": False, "changed": [],
                "active": host._rel(active) if active else None,
                "message": f"VAE 窗口已是 {frames} 帧，无需切换（引擎未重启）"}
    host._restart_audiocpp(str(host.SERVER_JSON))
    where = "、".join(f"{c['path']}（{c['old']} → {c['new']}）" for c in touched)
    note = ("；512 窗口显存约 1.95GB、长歌更稳，但切片变多接缝略明显，请完整试听一首"
            if frames == 512 else
            "；1024 为官方默认、速度略快，但 16GB 内存机器跑长歌可能 OutOfDeviceMemory")
    print(f"[vae/window] {scope} → {frames} 帧：{where}", flush=True)
    return {"ok": True, "frames": frames, "restarted": True, "changed": touched,
            "active": host._rel(active) if active else None,
            "message": f"已切换 VAE 窗口（{scope}）：{where}；引擎正在重启，约半分钟后生效{note}"}


def system_info(host: Any):
    """版本号 / 端口 / 目录 / 磁盘 / 显存 / 引擎就绪——全本地，不联网，不回密钥。"""
    import ports as _ports
    p = _ports.load()
    vram_total = host._gpu_total_mb()
    vram_free = host._gpu_free_mb()
    out_dir = Path(host.OUTPUT_DIR)
    return {
        "version": host._version_from_changelog(),
        # 走的是 pyd 还是 shim：同版本两台机器内核可能不同，必须能查出来（issue #4-7）
        "gateway_kernel": host._gateway_kernel(),
        "security": {"lan_mode": host.LAN_MODE,
                     "direct_access_token": bool(host.ACCESS_TOKEN),
                     "loopback_exempt": True},
        "ports": {"gateway": p.get("gateway"), "dsh": p.get("dsh"),
                  "audiocpp": p.get("audiocpp"), "ports_file": str(_ports.PORTS_FILE),
                  "ports_file_exists": _ports.PORTS_FILE.is_file()},
        "paths": {"root": str(host.ROOT), "output_dir": str(out_dir),
                  "runtime_dir": str(host.ROOT / "runtime"),
                  "scores_dir": str(host.SCORES_DIR), "records_dir": str(host.HIST_DIR),
                  "rvc_weights_dir": str(host.RVC_MODELS_DIR),
                  "model_dir": str(host.MODEL_DIR),
                  "log_dir": str(host.ROOT / "runtime" / "data" / "logs")},
        "disk": {"output_free_mb": host._free_bytes(out_dir) // (1024 * 1024),
                 "output_total_mb": host._disk_total_bytes(out_dir) // (1024 * 1024),
                 "output_files": host._output_file_count(out_dir),
                 "upload_floor_mb": host._DISK_FLOOR_BYTES // (1024 * 1024)},
        "gpu": {"mode": host.backend_mode(), "total_mb": vram_total, "free_mb": vram_free,
                "used_mb": (None if vram_total is None or vram_free is None
                            else max(0, vram_total - vram_free)),
                "headroom_mb": host._VRAM_HEADROOM_MB,
                "query_failed": vram_total is None and vram_free is None},
        "engines": {
            "audiocpp_alive": host._audiocpp_alive(),
            # 值必须来自 server.json（后端真正读的那份），不能来自 engine_state 的自述。
            "loaded_main": host._loaded_main_label(),
            "loaded_vae": host._loaded_vae_label(),
            "loaded_lora": host._loaded_lora_label(),
            "vocal_separation_ready": host._sep_available()[0],
            "sheetsage2_ready": host._sheetsage_ready(),
            "ai_workbench": host._ai_ready(),
        },
        "read_only": ("端口/输出目录/并发策略在本页只读展示，不支持在线修改："
                      "改了会与启动脚本和看门狗各持一套口径，风险大于收益（规划 §十-5）"),
    }


def build_router(host: Any) -> APIRouter:
    r = APIRouter()

    @r.get("/models/list")
    def _models_list():
        return models_list(host)

    @r.get("/models/current")
    def _models_current():
        return models_current(host)

    @r.get("/capabilities")
    def _capabilities():
        return capabilities(host)

    @r.get("/settings")
    def _settings_get():
        return settings_get(host)

    @r.post("/settings")
    def _settings_set(payload: dict):
        return settings_set(host, payload)

    @r.get("/backend/mode")
    def _backend_mode_get():
        return backend_mode_get(host)

    @r.post("/backend/mode")
    def _backend_mode_set(payload: dict):
        return backend_mode_set(host, payload)

    @r.get("/vae/window")
    def _vae_window_get():
        return vae_window_get(host)

    @r.post("/vae/window")
    def _vae_window_set(payload: dict):
        return vae_window_set(host, payload)

    @r.get("/system/info")
    def _system_info():
        return system_info(host)

    return r
