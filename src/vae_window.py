"""VAE 解码窗口 sidecar 的定位与改写。

纯函数模块：只读 `cpp/server.json`、只改 sidecar 文件，不碰引擎、不碰全局状态。
app.py 保留同名薄包装，测试对 `app._active_vae_config` 等名字的 patch 仍然生效。
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

SUFFIX = ".bak-vae-window"
CHOICES = (512, 1024)
SIDECAR_NAME = "yue2-vae-config.json"
DEFAULT_FRAMES = 1024


def rel(p: Path, root: Path) -> str:
    """仓库内路径→相对 posix 路径；不是仓库内（测试里的假路径）就原样给，绝不抛。"""
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


def active_model_gguf(root: Path) -> Path | None:
    """引擎**接下来会加载**的权重文件（绝对路径）；读不到配置就返回 None。

    权威是 `cpp/server.json` 的 `models[].session_options["yue2.model_gguf"]`
    （缺了用 `models[].path` 的 basename）。不用 engine_state：那是"上次真的加载了
    什么"，切了量化档但引擎没重启时它是旧值。
    """
    try:
        cfg = json.loads((root / "cpp" / "server.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entries = cfg.get("models") or []
    if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
        return None
    entry = entries[0]
    rel_path = str(entry.get("path") or "").strip().replace("\\", "/")
    so = entry.get("session_options") or {}
    name = str(so.get("yue2.model_gguf") or "").strip().replace("\\", "/")
    if not name and rel_path:
        name = rel_path.rsplit("/", 1)[-1]
    if not name:
        return None
    # model_gguf 只是文件名；**目录以 path 为准**（path 相对 cpp/），否则换档后会找错目录。
    parent = ((root / "cpp" / rel_path).parent if "/" in rel_path
              else (root / "cpp" / "model"))
    return parent / name


def sidecar_dir_of(model_file: Path) -> Path:
    """引擎在**模型同目录**的 sidecars/ 下找 VAE sidecar。"""
    return model_file.parent / "sidecars"


def active_config(root: Path) -> Path | None:
    """当前在用模型对应的 VAE sidecar；定位不到返回 None（不拿别的档顶替）。"""
    model = active_model_gguf(root)
    if model is None:
        return None
    sidecar = sidecar_dir_of(model) / SIDECAR_NAME
    return sidecar if sidecar.is_file() else None


def configs(root: Path) -> list[Path]:
    """所有 VAE sidecar 配置，**当前在用那份永远排第一**（现取不缓存）。

    扁平布局（`cpp/model/sidecars/`）与按量化分子目录布局
    （`cpp/model/<quant>/sidecars/`）都要认。
    """
    model_dir = root / "cpp" / "model"
    found: list[Path] = []
    active = active_config(root)
    if active is not None:
        found.append(active)
    candidates = [model_dir / "sidecars" / SIDECAR_NAME]
    candidates += sorted(model_dir.glob(f"*/sidecars/{SIDECAR_NAME}"))
    for p in candidates:
        if p.is_file() and p not in found:
            found.append(p)
    return found


def window_of(path: Path) -> int:
    """一份 sidecar 当前的 decode_core_frames；读不出来按官方默认 1024 计。"""
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
        return int(cfg.get("decode_core_frames") or DEFAULT_FRAMES)
    except (OSError, ValueError, TypeError, AttributeError):
        return DEFAULT_FRAMES


def patch_window(path: Path, frames: int, suffix: str = SUFFIX) -> tuple[int, int]:
    """把一份 sidecar 改成目标窗口，返回 (old, new)。

    与 scripts/set_vae_window.py 同约定：同值直接返回、不动文件；只有第一次改动前
    留备份；先写临时文件再 os.replace，避免半份配置。
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    old = int(data.get("decode_core_frames") or 0)
    if old == frames:
        return old, old
    bak = path.with_name(path.name + suffix)
    if not bak.exists():
        shutil.copy2(path, bak)
    data["decode_core_frames"] = frames
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return old, frames
