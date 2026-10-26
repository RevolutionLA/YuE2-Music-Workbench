#!/usr/bin/env python
"""发布卫生检查：确保发布包/仓库没有不该带的东西。

    py312\\python.exe scripts\\check_release_hygiene.py             # 开发机（真实密钥在 untracked 的 local_env.bat 里，允许）
    py312\\python.exe scripts\\check_release_hygiene.py --release   # 发布目录：出现 local_env.bat / pyd 一律判失败
    py312\\python.exe scripts\\check_release_hygiene.py --allow-pyd # 明确回滚 pyd 时

检查项：编译内核、真实密钥、模型权重、运行件是否被跟踪、依赖是否全部钉版本、
端口表/CHANGELOG/前端产物是否完整。失败返回 1，输出只列结论，不回显密钥。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_MAX_TRACKED_BYTES = 20 * 1024 * 1024
MIN_UI_BYTES = 100_000
WEIGHT_SUFFIXES = (".gguf", ".safetensors", ".pth", ".index", ".ckpt", ".pt", ".onnx", ".bin")
RUNTIME_PREFIXES = ("cpp/", "runtime/", "py312/", "hf_download/", "tmp/", "voices/")
SENSITIVE_NAMES = ("secrets/local_env.bat", "secrets/local_env.bat.bak",
                   "secrets/deepseek_api_key.txt", ".env")
SECRET_RE = re.compile(r"sk-[A-Za-z0-9]{20,}")
TOKEN_RE = re.compile(r"YUE2_ACCESS_TOKEN=[A-Za-z0-9_\-]{16,}")
SKIP_SCAN_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".wav", ".mp3", ".zip", ".gz",
                      ".gguf", ".safetensors", ".pth", ".index", ".pyc")


def _tracked_files(root: Path) -> list[str] | None:
    if not (root / ".git").exists():
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True, timeout=30,
        )
        if out.returncode != 0:
            return None
        return [p for p in out.stdout.decode("utf-8", "replace").split("\0") if p]
    except Exception:
        return None


def check(root: Path, *, tracked: list[str] | None = None, release: bool = False,
          allow_pyd: bool = False,
          max_tracked_bytes: int = DEFAULT_MAX_TRACKED_BYTES) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    if tracked is None:
        tracked = _tracked_files(root)

    # 1) 编译内核
    pdbs = sorted(p.name for p in root.glob("main.*.pyd"))
    if pdbs and not allow_pyd:
        failures.append(f"仓库根有编译内核：{pdbs}（回滚请放 runtime/backup/ 并加 --allow-pyd）")
    elif pdbs:
        warnings.append(f"显式允许编译内核：{pdbs}")

    # 2) 被跟踪的敏感文件 / 运行件 / 权重
    if tracked is not None:
        for path in tracked:
            low = path.lower()
            if low in SENSITIVE_NAMES or (low.startswith("secrets/") and low.endswith(".bak")):
                failures.append(f"真实环境文件被 git 跟踪：{path}")
            if low.startswith(RUNTIME_PREFIXES):
                failures.append(f"运行件/模型目录被 git 跟踪：{path}")
            if low.endswith(".pyd"):
                failures.append(f"编译内核被 git 跟踪：{path}")
            if low.endswith(WEIGHT_SUFFIXES):
                failures.append(f"模型权重被 git 跟踪：{path}")
            if "__pycache__/" in low or low.endswith(".pyc"):
                failures.append(f"缓存文件被 git 跟踪：{path}")
            full = root / path
            try:
                if full.is_file() and full.stat().st_size > max_tracked_bytes:
                    failures.append(f"受控文件超过 {max_tracked_bytes // (1024 * 1024)}MB：{path}")
            except OSError:
                pass

    # 3) 受控文本里的真实密钥（只报路径，不回显内容）
    if tracked is not None:
        for path in tracked:
            if path.lower().endswith(SKIP_SCAN_SUFFIXES):
                continue
            full = root / path
            try:
                if not full.is_file() or full.stat().st_size > 1024 * 1024:
                    continue
                text = full.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if SECRET_RE.search(text) or TOKEN_RE.search(text):
                failures.append(f"受控文件里疑似真实密钥：{path}")

    # 4) 发布目录不允许出现本机环境文件 / 编译内核
    if release:
        for name in SENSITIVE_NAMES:
            if (root / name).exists():
                failures.append(f"发布目录含本机环境文件：{name}")

    # 5) 依赖必须全部钉版本，且 requirements 保持 UTF-8 BOM
    req = root / "requirements.txt"
    if not req.is_file():
        failures.append("缺 requirements.txt")
    else:
        raw = req.read_bytes()
        if not raw.startswith(b"\xef\xbb\xbf"):
            failures.append("requirements.txt 缺 UTF-8 BOM（启动脚本按 BOM 识别）")
        text = raw.decode("utf-8-sig", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("-") or "==" not in line:
                failures.append(f"requirements.txt 第 {lineno} 行未钉版本：{line[:60]}")
                break

    # 6) 端口表
    ports_file = root / "ports.json"
    try:
        ports = json.loads(ports_file.read_text(encoding="utf-8"))
        values = [ports[k] for k in ("gateway", "dsh", "audiocpp")]
        if not all(isinstance(v, int) and 1 <= v <= 65535 for v in values):
            failures.append("ports.json 的端口必须是 1..65535 的整数")
        elif len(set(values)) != 3:
            failures.append("ports.json 的三个端口不能重复")
    except Exception as exc:
        failures.append(f"ports.json 读不了：{type(exc).__name__}")

    # 7) 版本与前端产物
    changelog = root / "CHANGELOG.md"
    try:
        head = changelog.read_text(encoding="utf-8")[:4000]
        if not re.search(r"^## \[v\d+\.\d+\.\d+\] - \d{4}-\d{2}-\d{2}", head, re.M):
            failures.append("CHANGELOG.md 找不到日期化版本标题")
    except OSError:
        failures.append("缺 CHANGELOG.md")
    ui = root / "static" / "index.html"
    if not ui.is_file() or ui.stat().st_size < MIN_UI_BYTES:
        failures.append(f"static/index.html 缺失或过小（<{MIN_UI_BYTES} 字节）")
    for name in ("web/index.html", "web/app.css", "web/app.js"):
        if not (root / name).is_file():
            failures.append(f"缺前端源文件：{name}")
    for name in ("README.md", "LICENSE"):
        if not (root / name).is_file():
            failures.append(f"缺 {name}")

    return failures, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="发布卫生检查")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--release", action="store_true", help="按发布目录口径检查（含本机环境文件即失败）")
    parser.add_argument("--allow-pyd", action="store_true", help="显式允许仓库根存在编译内核")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    tracked = _tracked_files(root)
    failures, warnings = check(root, tracked=tracked, release=args.release,
                               allow_pyd=args.allow_pyd)

    print(f"[hygiene] root={root} tracked={'git' if tracked is not None else 'filesystem'}")
    for msg in failures:
        print(f"[FAIL] {msg}")
    for msg in warnings:
        print(f"[warn] {msg}")
    if failures:
        print(f"[hygiene] {len(failures)} 项未通过")
        return 1
    print("[hygiene] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
