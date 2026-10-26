#!/usr/bin/env python
"""构建源码发布包（zip + sha256），不碰 cpp/runtime/py312/真实密钥。

    py312\\python.exe scripts\\build_release.py --out dist
    py312\\python.exe scripts\\build_release.py --out dist --dry-run

文件来源 = git 已跟踪 + 未跟踪但未被 .gitignore 排除的文件，因此能带上还没提交的新文件，
又不会把 runtime/cpp/py312/secrets/local_env.bat 带进去。打包前先跑
check_release_hygiene --release，任何一项不过就中止。
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_release_hygiene as hygiene  # noqa: E402

SKIP_PREFIXES = (
    "cpp/", "runtime/", "py312/", "hf_download/", "voices/", "tmp/",
    ".shots/", ".workbuddy/", ".atomcode/", "cache/", "y/", "dist/", "scripts/bak/",
)
SKIP_EXACT = {
    "secrets/local_env.bat",
    "secrets/local_env.bat.bak",
    "secrets/deepseek_api_key.txt",
    "main.cp312-win_amd64.pyd",
}


def _git(root: Path, *args: str) -> list[str]:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(f"[release] git {' '.join(args)} 失败："
                         f"{proc.stderr.decode('utf-8', 'replace')[:200]}")
    return [p for p in proc.stdout.decode("utf-8", "replace").split("\0") if p]


def collect_files(root: Path) -> list[str]:
    tracked = _git(root, "ls-files", "-z")
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    picked = []
    for rel in sorted(set(tracked) | set(untracked)):
        if rel in SKIP_EXACT or rel.startswith(SKIP_PREFIXES):
            continue
        if (root / rel).is_file():
            picked.append(rel)
    return picked


def version(root: Path) -> str:
    try:
        text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        m = re.search(r"^## \[v([0-9]+\.[0-9]+\.[0-9]+)\]", text, re.M)
        if m:
            return m.group(1)
    except OSError:
        pass
    return "dev"


def build(out_dir: Path, *, dry_run: bool = False) -> Path:
    files = collect_files(ROOT)
    if not files:
        raise SystemExit("[release] 没收集到任何文件（git 工作树为空？）")
    total = sum((ROOT / f).stat().st_size for f in files)
    name = f"yue2-workbench-v{version(ROOT)}"
    zip_path = out_dir / f"{name}.zip"
    print(f"[release] {len(files)} 个文件，{total / 1024 / 1024:.1f}MB → {zip_path.name}")
    if dry_run:
        return zip_path

    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="yue2_release_") as tmp:
        stage = Path(tmp) / name
        for rel in files:
            dst = stage / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, dst)

        failures, warnings = hygiene.check(stage, tracked=files, release=True)
        if failures:
            raise SystemExit("[release] 卫生检查失败：" + " | ".join(failures))

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for rel in files:
                zf.write(stage / rel, f"{name}/{rel}")

    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (out_dir / f"{name}.sha256").write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
    print(f"[release] 完成 {zip_path}  sha256={digest[:16]}…")
    return zip_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="构建源码发布包")
    ap.add_argument("--out", default=str(ROOT / "dist"), help="输出目录（默认 dist/）")
    ap.add_argument("--dry-run", action="store_true", help="只统计文件与体积，不写包")
    args = ap.parse_args(argv)
    build(Path(args.out).resolve(), dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
