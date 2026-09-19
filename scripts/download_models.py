#!/usr/bin/env python3
"""YuE2 模型自动下载（大陆网络友好）。

首次运行缺少模型时，启动脚本会调用本脚本，从 hf-mirror.com（大陆直连可用）
下载 YuE2-3B GGUF 模型与 VAE 到 cpp/model/yue2-q4_k_m/。若镜像不可用自动
回退 huggingface.co。支持断点续传，中断后重跑即可继续。

用法：
    python scripts/download_models.py            # 检查并按需下载（默认 q4_k_m）
    python scripts/download_models.py --q8       # 下载 q8_0（约 4GB，质量更佳）
    python scripts/download_models.py --check    # 仅检查是否齐全，退出码 0=齐全
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "cpp" / "model"

# 文件清单：仓库ID / 仓库内路径 / 落地相对路径（相对 cpp/model/<quant>/）
SIDECARS = [
    "sidecars/yue2-model-config.json",
    "sidecars/yue2-generation-config.json",
    "sidecars/yue2-qwen.tiktoken",
    "sidecars/yue2-vae-config.json",
]


def sources(quant: str) -> list[tuple[str, str, str]]:
    """返回 (hf仓库, 仓库内文件, 目标相对路径) 列表。"""
    if quant == "q8":
        main_repo, main_file, dest_dir = "ngquocvinh/YuE2-3B-GGUF", "yue2-3b-q8_0.gguf", "yue2-q8"
    else:
        main_repo, main_file, dest_dir = "ngquocvinh/YuE2-3B-GGUF", "yue2-3b-q4_k_m.gguf", "yue2-q4_k_m"
    vae_repo = "audio-cpp/Yue2-3B-GGUF"
    items = [(main_repo, main_file, f"{dest_dir}/{main_file}"),
             (vae_repo, "yue2-vae-f16.gguf", f"{dest_dir}/yue2-vae-f16.gguf")]
    items += [(main_repo, s, f"{dest_dir}/{s}") for s in SIDECARS]
    return items


def mirrors() -> list[str]:
    """按环境变量与默认顺序构造镜像列表（大陆默认镜像优先）。"""
    env = os.environ.get("HF_ENDPOINT", "").rstrip("/")
    mf = [m for m in [env, "https://hf-mirror.com", "https://huggingface.co"] if m]
    seen, out = set(), []
    for m in mf:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def fetch(url: str, dest: Path) -> None:
    """单文件下载，断点续传。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    done = part.stat().st_size if part.is_file() else 0
    req = Request(url, headers={"User-Agent": "yue2-workbench/1.0",
                                **({"Range": f"bytes={done}-"} if done else {})})
    with urlopen(req, timeout=60) as resp, open(part, "ab" if done else "wb") as f:
        total_hdr = resp.headers.get("Content-Range") or resp.headers.get("Content-Length")
        total = int(total_hdr.rsplit("/", 1)[-1]) if total_hdr and "/" in total_hdr else int(resp.headers.get("Content-Length") or 0)
        start = time.time()
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                spd = done / max(time.time() - start, 0.1)
                print(f"\r    {human(done)} / {human(total)}  {human(spd)}/s", end="", flush=True)
    print()
    if part.stat().st_size == 0:
        raise URLError("下载为空")
    part.rename(dest)


def download_one(repo: str, rel: str, dest: Path) -> bool:
    if dest.is_file() and dest.stat().st_size > 0:
        print(f"  已存在，跳过: {dest.name}")
        return True
    for mirror in mirrors():
        url = f"{mirror}/{repo}/resolve/main/{rel}"
        print(f"  镜像 {mirror.split('//')[1].split('/')[0]} ...")
        try:
            fetch(url, dest)
            return True
        except Exception as e:
            print(f"    失败: {e}")
    return False


def ensure_models(quant: str) -> bool:
    # SheetSage2 乐谱提取权重（约223MB，翻唱/改谱功能用）
    items = [("m-a-p/SheetSage2", "model.safetensors", "../checkpoints/model.safetensors")]
    items += sources(quant)
    missing = [(r, s, (ROOT / d).resolve() if d.startswith("..") else MODEL_DIR / d)
               for r, s, d in items
               if not ((ROOT / d).resolve() if d.startswith("..") else MODEL_DIR / d).is_file()]
    if not missing:
        print("模型已齐全 ✅")
        return True
    print(f"缺少 {len(missing)} 个文件，开始下载（支持断点续传，中断重跑即可）:")
    ok = True
    for repo, rel, dest in missing:
        print(f"  {dest.relative_to(MODEL_DIR)}")
        ok = download_one(repo, rel, dest) and ok
    if ok:
        # 保证 server.json 指向所下量化目录
        print("模型下载完成 ✅")
    else:
        print("部分文件下载失败，请检查网络后重试 ❌", file=sys.stderr)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--q8", action="store_true", help="下载 q8_0（约4GB）而非 q4_k_m（约2.7GB）")
    ap.add_argument("--check", action="store_true", help="仅检查，不下载")
    a = ap.parse_args()
    quant = "q8" if a.q8 else "q4km"
    if a.check:
        missing = [d for _, _, d in sources(quant) if not (MODEL_DIR / d).is_file()]
        print("齐全" if not missing else f"缺少 {len(missing)} 个文件")
        return 0 if not missing else 1
    return 0 if ensure_models(quant) else 1


if __name__ == "__main__":
    sys.exit(main())
