# -*- coding: utf-8 -*-
"""把 Yue2 VAE 的解码窗口调小，降低核显/Vulkan 上的显存峰值。

引擎的 VAE 解码按 `decode_core_frames + decode_halo_frames` 分块；官方 1024+16
在 16GB 核显上会为整段申请约 3.89GB，直接 OutOfDeviceMemory。实测改成 512+16 后，
4373 帧（约 2 分 55 秒）的歌在 Vulkan 上完成：VAE 分 9 个 tile、约 38 秒。

用法（改完重启引擎/工作台生效）：
    py312\\python.exe scripts\\set_vae_window.py --frames 512
    py312\\python.exe scripts\\set_vae_window.py --restore

注意：这是模型 sidecar 的本地修改，会先备份；窗口越小显存越低，但边界重新拼接的
次数变多，接缝风险更高，改完请完整试听一首。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKUP_SUFFIX = ".bak-vae-window"
SIDECAR_NAME = "yue2-vae-config.json"


def find_configs(root: Path = ROOT) -> list[Path]:
    """所有 VAE sidecar 配置，两种模型布局都认（issue #4-1）。

    老写法只 glob `*/sidecars/yue2-vae-config.json`，只匹配"每个量化档一个子目录"
    的布局；而"权重直接摊在 cpp/model/ 下"的扁平布局，它一份都匹配不到 —— 于是
    脚本报"没找到配置"，网页开关则改了没在用的那份。
      * 扁平：  cpp/model/yue2-3b-q8_0.gguf  → cpp/model/sidecars/yue2-vae-config.json
      * 子目录：cpp/model/<quant>/<x>.gguf   → cpp/model/<quant>/sidecars/yue2-vae-config.json
    """
    model_dir = root / "cpp" / "model"
    found: list[Path] = []
    flat = model_dir / "sidecars" / SIDECAR_NAME
    if flat.is_file():
        found.append(flat)
    for p in sorted(model_dir.glob(f"*/sidecars/{SIDECAR_NAME}")):
        if p.is_file() and p not in found:
            found.append(p)
    return found


def active_config(root: Path = ROOT) -> Path | None:
    """当前在用模型对应的那份 sidecar（读 cpp/server.json）；解析不出来返回 None。

    只用于在输出里标"（在用）"，不改变"改全部"的 CLI 语义。
    """
    try:
        cfg = json.loads((root / "cpp" / "server.json").read_text(encoding="utf-8"))
        entry = (cfg.get("models") or [{}])[0]
    except (OSError, ValueError, IndexError, AttributeError):
        return None
    rel = str(entry.get("path") or "").strip().replace("\\", "/")
    name = str((entry.get("session_options") or {}).get("yue2.model_gguf") or "").strip()
    if not name and rel:
        name = rel.rsplit("/", 1)[-1]
    if not name:
        return None
    parent = (root / "cpp" / rel).parent if "/" in rel else (root / "cpp" / "model")
    sidecar = parent / "sidecars" / SIDECAR_NAME
    return sidecar if sidecar.is_file() else None


def patch_file(path: Path, frames: int) -> tuple[int, int]:
    d = json.loads(path.read_text(encoding="utf-8"))
    old = int(d.get("decode_core_frames") or 0)
    if old == frames:
        return old, old
    bak = path.with_name(path.name + BACKUP_SUFFIX)
    if not bak.exists():
        shutil.copy2(path, bak)
    d["decode_core_frames"] = frames
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return old, frames


def restore_file(path: Path) -> bool:
    bak = path.with_name(path.name + BACKUP_SUFFIX)
    if not bak.is_file():
        return False
    shutil.copy2(bak, path)
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="调整 Yue2 VAE 解码窗口（低显存模式）")
    ap.add_argument("--frames", type=int, default=512,
                    help="decode_core_frames，16~4096 且为 8 的倍数（默认 512；官方 1024）")
    ap.add_argument("--restore", action="store_true", help="从备份恢复原始窗口")
    ap.add_argument("--root", type=Path, default=ROOT)
    a = ap.parse_args(argv)
    configs = find_configs(a.root)
    if not configs:
        print(f"[x] 没找到 VAE sidecar 配置：{a.root / 'cpp' / 'model'}/sidecars/{SIDECAR_NAME}"
              f" 或 {a.root / 'cpp' / 'model'}/*/sidecars/{SIDECAR_NAME}")
        return 2
    active = active_config(a.root)
    if active is not None:
        print(f"[i] 当前加载模型的配置：{active}")
    if a.restore:
        n = sum(restore_file(p) for p in configs)
        print(f"[OK] 已恢复 {n} 份 VAE sidecar；重启引擎后生效")
        return 0 if n else 1
    if a.frames < 16 or a.frames > 4096 or a.frames % 8:
        print("[x] --frames 必须满足 16 <= frames <= 4096 且是 8 的倍数")
        return 2
    for p in configs:
        old, new = patch_file(p, a.frames)
        mark = "（在用）" if p == active else ""
        print(f"[OK] {p}{mark}: decode_core_frames {old} -> {new}")
    print("[!] 已备份为 *.bak-vae-window；重启引擎/工作台后生效。窗口越小接缝风险越高，请完整试听。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
