"""模型完整性校验 + 局部修复（目录存在 ≠ 模型可用）。

清单跟随当前 server.json（q8 机器不再误查 q4）；修复只重下缺失/损坏的文件。
路径与配置读取由 app 注入，现有测试重定向 server.json/engine_state 仍然生效。
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException

GGUF_MAGIC = b"GGUF"

# (仓库, 仓库内路径, 落地相对路径(相对 ROOT), 最小字节数, 是否 GGUF)
MODEL_MANIFEST_MAIN = [
    ("ngquocvinh/YuE2-3B-GGUF", "yue2-3b-q4_k_m.gguf",
     "cpp/model/yue2-q4_k_m/yue2-3b-q4_k_m.gguf", 2_000_000_000, True),
    ("audio-cpp/Yue2-3B-GGUF", "yue2-vae-f16.gguf",
     "cpp/model/yue2-q4_k_m/yue2-vae-f16.gguf", 100_000_000, True),
    ("ngquocvinh/YuE2-3B-GGUF", "sidecars/yue2-model-config.json",
     "cpp/model/yue2-q4_k_m/sidecars/yue2-model-config.json", 10, False),
    ("ngquocvinh/Yue2-3B-GGUF", "sidecars/yue2-generation-config.json",
     "cpp/model/yue2-q4_k_m/sidecars/yue2-generation-config.json", 10, False),
    ("ngquocvinh/Yue2-3B-GGUF", "sidecars/yue2-qwen.tiktoken",
     "cpp/model/yue2-q4_k_m/sidecars/yue2-qwen.tiktoken", 100_000, False),
    ("ngquocvinh/Yue2-3B-GGUF", "sidecars/yue2-vae-config.json",
     "cpp/model/yue2-q4_k_m/sidecars/yue2-vae-config.json", 10, False),
    ("m-a-p/SheetSage2", "model.safetensors", "checkpoints/model.safetensors",
     100_000_000, False),
]
REPAIR_LOCK = threading.Lock()
REPAIR_STATE: dict = {"running": False, "ts": None, "repaired": [], "failed": [],
                      "message": ""}

SIDECAR_MIN = (("yue2-model-config.json", 10),
               ("yue2-generation-config.json", 10),
               ("yue2-qwen.tiktoken", 100_000),
               ("yue2-vae-config.json", 10))
QUANT_REMOTE = {"q8": "yue2-3b-q8_0.gguf", "q4_k_m": "yue2-3b-q4_k_m.gguf"}


def active_manifest(*, root: Path, model_dir: Path,
                    read_server_json: Callable[[], dict],
                    engine_config_report: Callable[[], dict]
                    ) -> tuple[list[tuple[str, str, str, int, bool]], str]:
    try:
        cfg = read_server_json()
        entry = (cfg.get("models") or [{}])[0]
        rel = str(entry.get("path") or "").strip().replace("\\", "/")
        so = entry.get("session_options") or {}
        main = str(so.get("yue2.model_gguf") or "").strip()
        if not main:
            raise ValueError("server.json 没有 yue2.model_gguf")
        base = (root / "cpp" / rel).parent if rel else model_dir
        main_path = base / main
        if not main_path.is_file() and (model_dir / main).is_file():
            main_path = model_dir / main
        main_rel = main_path.relative_to(root).as_posix()
    except Exception:
        return list(MODEL_MANIFEST_MAIN), "fallback-q4"

    name = main_path.name.lower()
    remote = next((fn for key, fn in QUANT_REMOTE.items() if key in name), "")
    repo = "ngquocvinh/YuE2-3B-GGUF" if remote else ""
    items: list[tuple[str, str, str, int, bool]] = [
        (repo, remote, main_rel, 2_000_000_000, True)]

    conf = engine_config_report()
    vae = str(conf.get("vae") or "")
    if vae:
        vae_path = base / vae
        if not vae_path.is_file():
            vae_path = model_dir / vae
        items.append(("audio-cpp/Yue2-3B-GGUF", "yue2-vae-f16.gguf",
                      vae_path.relative_to(root).as_posix(), 100_000_000, True))

    side_dir = base / "sidecars"
    if not side_dir.is_dir():
        side_dir = model_dir / "sidecars"
    for fn, min_size in SIDECAR_MIN:
        items.append(("ngquocvinh/Yue2-3B-GGUF", f"sidecars/{fn}",
                      (side_dir / fn).relative_to(root).as_posix(), min_size, False))

    items.append(("m-a-p/SheetSage2", "model.safetensors",
                  "checkpoints/model.safetensors", 100_000_000, False))
    return items, "active"


def verify_one(rel: str, min_size: int, is_gguf: bool, *, root: Path) -> tuple[bool, str]:
    p = root / rel
    if not p.is_file():
        return False, "缺失"
    size = p.stat().st_size
    if size < min_size:
        return False, f"不完整（{size} 字节 < 期望 ≥{min_size}）"
    if is_gguf:
        try:
            with open(p, "rb") as f:
                if f.read(4) != GGUF_MAGIC:
                    return False, "GGUF 头无效"
        except OSError as e:
            return False, f"读取失败：{e}"
    return True, "ok"


def verify_payload(manifest, source: str, *, root: Path,
                   repair_running: bool = False) -> dict:
    files = []
    ok_all = True
    for repo, rel_remote, rel, min_size, is_gguf in manifest:
        ok, reason = verify_one(rel, min_size, is_gguf, root=root)
        ok_all = ok_all and ok
        files.append({"repo": repo, "remote": rel_remote, "path": rel,
                      "name": Path(rel).name, "ok": ok, "reason": reason})
    return {"ok": ok_all, "source": source, "files": files,
            "repair_running": repair_running}


def _repair_run(*, root: Path, manifest_fn: Callable, state: dict, lock) -> None:
    sys.path.insert(0, str(root / "scripts"))
    try:
        from download_models import download_one  # 复用镜像/断点续传逻辑
    except Exception as e:
        with lock:
            state.update({"running": False, "message": f"加载下载模块失败：{e}"})
        return
    repaired, failed = [], []
    manifest, _source = manifest_fn()
    try:
        for repo, rel_remote, rel, min_size, is_gguf in manifest:
            ok, reason = verify_one(rel, min_size, is_gguf, root=root)
            if ok:
                continue
            if not repo or not rel_remote:
                failed.append({"name": Path(rel).name,
                               "reason": "该量化档没有在线修复源，请手动补齐或改用 q4_k_m/q8_0"})
                continue
            dest = root / rel
            with lock:
                state["message"] = f"修复 {Path(rel).name} …"
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            if download_one(repo, rel_remote, dest):
                ok2, reason2 = verify_one(rel, min_size, is_gguf, root=root)
                (repaired if ok2 else failed).append(
                    {"name": Path(rel).name, "reason": reason2})
            else:
                failed.append({"name": Path(rel).name, "reason": "全部镜像下载失败"})
    finally:
        with lock:
            state.update({
                "running": False, "repaired": repaired, "failed": failed,
                "message": ("修复完成" if not failed and repaired else
                            "模型本就完整" if not repaired and not failed else
                            f"完成，{len(failed)} 个文件仍失败"),
            })


def start_repair(*, root: Path, manifest_fn: Callable, state: dict, lock) -> dict:
    with lock:
        if state.get("running"):
            raise HTTPException(status_code=409, detail="修复已在进行中")
        state.update({"running": True, "ts": time.time(),
                      "repaired": [], "failed": [], "message": "校验中…"})
    threading.Thread(target=_repair_run, kwargs={
        "root": root, "manifest_fn": manifest_fn, "state": state, "lock": lock,
    }, daemon=True).start()
    return {"ok": True, "message": "修复已开始（只补缺失/损坏的文件），可稍后刷新查看。"}


def build_router(*, root: Path, model_dir: Path,
                 read_server_json: Callable[[], dict],
                 engine_config_report: Callable[[], dict],
                 state: dict, lock) -> APIRouter:
    router = APIRouter(tags=["model-files"])

    def manifest():
        return active_manifest(root=root, model_dir=model_dir,
                               read_server_json=read_server_json,
                               engine_config_report=engine_config_report)

    @router.get("/models/verify")
    def models_verify():
        m, source = manifest()
        return verify_payload(m, source, root=root,
                              repair_running=state.get("running", False))

    @router.post("/models/repair")
    def models_repair():
        return start_repair(root=root, manifest_fn=manifest, state=state, lock=lock)

    @router.get("/models/repair/status")
    def models_repair_status():
        return state

    return router
