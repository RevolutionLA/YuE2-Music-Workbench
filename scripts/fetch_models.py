#!/usr/bin/env python3
"""YuE2 权重的多路分块下载器（可单独用，也被 scripts/setup_runtime.py 调用）。

为什么不直接用仓库里原有的 scripts/download_models.py：那个脚本是对的（仓库、文件名、
落地路径都实测可用），但它一条连接跑到底。本机 2026-10-21 实测 hf-mirror.com
**单连接只有 92KB/s**，2.88GB 的主模型要 9 小时；同一条 URL 开 4~6 条 Range 连接，
每路仍有 150~190KB/s，合计约 650KB/s -> 2.7GB 实测 18 分钟跑完。

断点续传：每个块落在 `<目标文件>.part.NNNN`，中断后重跑本脚本即接着下；全部块齐了
才顺序拼装、校验、删块。校验口径与 app.py 的 _MODEL_MANIFEST_MAIN 相同（存在 +
最小字节数 + GGUF 文件查头部 magic），避免"目录存在 ≠ 模型可用"那类假绿灯。

用法：
    py312\\python.exe scripts\\fetch_models.py       # 下载缺的部分
    py312\\python.exe scripts\\fetch_models.py --check   # 只校验，0=齐全
    py312\\python.exe scripts\\fetch_models.py --q8    # 换 q8_0（约 4GB，音质更好）
    py312\\python.exe scripts\\fetch_models.py --workers 8
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIRROR = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com").rstrip("/")
CHUNK = 32 * 1024 * 1024
GGUF_MAGIC = b"GGUF"
MAIN_REPO = "ngquocvinh/YuE2-3B-GGUF"      # 量化全（社区档，q4_k_m/q8_0 都在）
VAE_REPO = "audio-cpp/Yue2-3B-GGUF"        # 官方 VAE 与 sidecars
SIDECARS = ["sidecars/yue2-model-config.json", "sidecars/yue2-generation-config.json",
            "sidecars/yue2-qwen.tiktoken", "sidecars/yue2-vae-config.json"]

# ③④ 从音频提取乐谱（SheetSage2）：权重不在本仓库，约 223MB。
# 与 app.py 的 _MODEL_MANIFEST_MAIN 同一落点与最小字节数。
SHEETSAGE = ("m-a-p/SheetSage2", "model.safetensors",
             "checkpoints/model.safetensors", 100_000_000, False)

QUANTS = {
    # (主模型文件名, 落地目录名, 最小字节数)
    # 最小字节数按上游真实大小留 1% 余量（q4_k_m 2,877,8xx,xxx；q8 4,264,2xx,xxx）。
    # 以前写 2.0GB/3.5GB，一个被掐断 2.1GB 的文件也能过"最小字节"这道门（评审 B19）。
    "q4km": ("yue2-3b-q4_k_m.gguf", "yue2-q4_k_m", 2_850_000_000),
    "q8": ("yue2-3b-q8_0.gguf", "yue2-q8", 4_200_000_000),
}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def file_list(quant: str) -> list[tuple[str, str, str, int, bool]]:
    main_file, dest_dir, min_size = QUANTS[quant]
    items = [
        (MAIN_REPO, main_file, f"cpp/model/{dest_dir}/{main_file}", min_size, True),
        (VAE_REPO, "yue2-vae-f16.gguf", f"cpp/model/{dest_dir}/yue2-vae-f16.gguf",
         100_000_000, True),
    ]
    items += [(MAIN_REPO, s, f"cpp/model/{dest_dir}/{s}", 10 if "json" in s else 100_000,
               False) for s in SIDECARS]
    return items


def verify_one(dest: Path, min_size: int, is_gguf: bool) -> tuple[bool, str]:
    if not dest.is_file():
        return False, "缺失"
    size = dest.stat().st_size
    if size < min_size:
        return False, f"不完整（{size} < 期望 ≥{min_size}）"
    if is_gguf:
        try:
            with open(dest, "rb") as f:
                if f.read(4) != GGUF_MAGIC:
                    return False, "GGUF 头无效"
        except OSError as e:
            return False, f"读取失败：{e}"
    if dest.suffix.lower() == ".safetensors":
        # 只看大小会假绿：头部 JSON 里每个张量都带 data_offsets，最后一段的终点加上
        # 头部长度就是文件应有字节数——截断下载在这里必然露馅（本机那份 228MB 的
        # SheetSage2 权重就是这么验的，而不是"超过 100MB 就算齐"）。
        try:
            with open(dest, "rb") as f:
                head_len = struct.unpack("<Q", f.read(8))[0]
                head = json.loads(f.read(head_len))
            head.pop("__metadata__", None)
            if not head:
                return False, "safetensors 里没有张量"
            need = 8 + head_len + max(v["data_offsets"][1] for v in head.values())
            if size != need:
                return False, f"截断（头声明 {need} 字节，实际 {size}）"
        except Exception as e:  # noqa: BLE001
            return False, f"safetensors 结构异常：{e}"
    return True, "ok"


def head_size(url: str) -> int | None:
    """用 Range 探真实长度。HF 对不存在的路径可能 HEAD 回 200，所以不信 HEAD。"""
    req = urllib.request.Request(url, headers={"User-Agent": "yue2-setup/1.0",
                                               "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            cr = r.headers.get("Content-Range")
            if cr and "/" in cr:
                total = cr.rsplit("/", 1)[-1].strip()
                if total.isdigit():
                    return int(total)
    except Exception as e:  # noqa: BLE001
        log(f"  探测失败 {url}: {e}")
    return None


def _parse_content_range(value: str) -> tuple[int, int, int | None] | None:
    """解析 `bytes start-end/total`；total 可能是 `*`。"""
    import re as _re
    m = _re.match(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", (value or "").strip(), _re.I)
    if not m:
        return None
    total = None if m.group(3) == "*" else int(m.group(3))
    return int(m.group(1)), int(m.group(2)), total


def fetch_chunk(url: str, part: Path, start: int, length: int) -> int:
    """把 [start, start+length) 下到 part；**逐字节核对待下载窗口**（评审 B4）。

    以前只看"字节数够了没有"：镜像回 200 整份文件、或回一段错位的 206，都会把
    垃圾拼进块里，最后照样打 [OK] 并覆盖旧文件。现在核对 Content-Range 的起止。
    """
    have = part.stat().st_size if part.is_file() else 0
    if have == length:
        return have
    if have > length:           # 块文件被污染/错位：整块重下
        part.unlink(missing_ok=True)
        have = 0
    req = urllib.request.Request(url, headers={
        "User-Agent": "yue2-setup/1.0",
        "Range": f"bytes={start + have}-{start + length - 1}"})
    part.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=120) as r, open(part, "ab" if have else "wb") as f:
        if have and r.status == 200:
            # 服务器无视 Range 从头给：丢掉旧数据，下满这一整块
            f.close()
            part.unlink(missing_ok=True)
            raise urllib.error.URLError("服务器忽略了 Range（回 200），本块重下")
        if r.status not in (206, 200):
            raise urllib.error.URLError(f"意外状态码 {r.status}")
        if r.status == 206:
            got = _parse_content_range(r.headers.get("Content-Range") or "")
            want_start = start + have
            if got is None:
                raise urllib.error.URLError("206 缺 Content-Range，无法核对窗口")
            g_start, g_end, _total = got
            if g_start != want_start or g_end != start + length - 1:
                raise urllib.error.URLError(
                    f"窗口错位：请求 {want_start}-{start + length - 1}，镜像给 {g_start}-{g_end}")
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
            have += len(b)
    if have != length:
        raise urllib.error.URLError(f"块长度不符：{have}/{length}")
    return have


def download(repo: str, rel: str, dest: Path, min_size: int, is_gguf: bool,
             workers: int, attempts: int = 6) -> bool:
    if verify_one(dest, min_size, is_gguf)[0]:
        log(f"  已齐全，跳过 {dest.name}")
        return True
    url = f"{MIRROR}/{repo}/resolve/main/{rel}"
    total = head_size(url)
    if not total:
        log(f"  [x] 取不到 {rel} 的长度（镜像拒绝了这个路径，检查仓库名/文件名或换镜像）")
        return False
    n = max(1, -(-total // CHUNK))
    parts = [dest.with_name(f"{dest.name}.part.{i:04d}") for i in range(n)]
    bounds = [(i * CHUNK, min(CHUNK, total - i * CHUNK)) for i in range(n)]
    log(f"-> {rel}  共 {human(total)}，切 {n} 块 × {workers} 并发")
    t0 = time.time()
    done = sum(p.stat().st_size if p.is_file() else 0 for p in parts)

    def work(idx: int) -> tuple[int, str | None]:
        s, ln = bounds[idx]
        last = None
        for k in range(attempts):
            try:
                fetch_chunk(url, parts[idx], s, ln)
                return idx, None
            except Exception as e:  # noqa: BLE001
                last = str(e)
                time.sleep(min(30, 2 ** k))
        return idx, last

    failed: list[str] = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, i) for i in range(n)]
        for fut in cf.as_completed(futs):
            idx, err = fut.result()
            if err:
                failed.append(f"块{idx}: {err}")
                log(f"  [x] 第 {idx} 块连续 {attempts} 次失败：{err}")
            got = sum(p.stat().st_size if p.is_file() else 0 for p in parts)
            if got > done:
                spd = (got - done) / max(time.time() - t0, 0.1)
                left = (total - got) / max(spd, 1)
                log(f"  {human(got)}/{human(total)}  {human(spd)}/s  余约 {int(left // 60)} 分")
                done = got
    if failed:
        log(f"  [x] {dest.name}：{len(failed)} 块失败。重跑本脚本会从断点接着下")
        return False
    sizes = [p.stat().st_size for p in parts if p.is_file()]
    if len(sizes) != n or sum(sizes) != total:
        log(f"  [x] {dest.name}：块数/总长不符（{len(sizes)}/{n} 块，合计 "
            f"{sum(sizes)}/{total} 字节），拒绝拼装")
        return False
    tmp = dest.with_suffix(dest.suffix + ".assembling")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "wb") as out:
        for p in parts:
            with open(p, "rb") as f:
                while True:
                    b = f.read(1 << 22)
                    if not b:
                        break
                    out.write(b)
            p.unlink()
    os.replace(tmp, dest)
    ok, why = verify_one(dest, min_size, is_gguf)
    log(f"  {'[OK]' if ok else '[x]'} {dest.name} {human(dest.stat().st_size)} —— {why}")
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--q8", action="store_true", help="下 q8_0（约4GB）而非 q4_k_m")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--check", action="store_true", help="只校验不下载")
    ap.add_argument("--with-sheetsage", action="store_true",
                    help="顺带下 ③④ 转谱要的 SheetSage2 权重（约223MB，落 checkpoints/）")
    a = ap.parse_args(argv)
    a.workers = max(1, min(32, int(a.workers or 1)))   # 评审 B4：0/负并发会卡死自己
    quant = "q8" if a.q8 else "q4km"
    bad: list[str] = []
    items = file_list(quant) + ([SHEETSAGE] if a.with_sheetsage else [])
    for repo, rel, d, min_size, is_gguf in items:
        dest = ROOT / d
        if a.check:
            ok, why = verify_one(dest, min_size, is_gguf)
            print(f"  {'[OK]' if ok else '[x]'} {d}  {why}")
            if not ok:
                bad.append(d)
            continue
        if not download(repo, rel, dest, min_size, is_gguf, a.workers):
            bad.append(d)
    if a.check:
        print("齐全 [OK]" if not bad else f"缺 {len(bad)} 项 [FAIL]")
        return 0 if not bad else 1
    print(json.dumps({"ok": not bad, "failed": bad}, ensure_ascii=False))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
