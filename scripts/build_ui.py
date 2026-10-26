#!/usr/bin/env python
"""把 web/ 源文件构建成 static/index.html，运行时仍保持单文件自包含。

    py312\\python.exe scripts\\build_ui.py          # 生成
    py312\\python.exe scripts\\build_ui.py --check  # 只检查是否同步（CI/发版）

改前端请改 web/index.html、web/app.css、web/app.js；直接改 static/index.html
会在下一次构建时被覆盖，--check 也会判红。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "web" / "index.html"
CSS = ROOT / "web" / "app.css"
JS = ROOT / "web" / "app.js"
OUT = ROOT / "static" / "index.html"

CSS_MARK = b"/*__APP_CSS__*/"
JS_MARK = b"/*__APP_JS__*/"


def render() -> bytes:
    if not TEMPLATE.is_file():
        raise SystemExit(f"[ui] 缺模板：{TEMPLATE}")
    template = TEMPLATE.read_bytes()
    for mark, path in ((CSS_MARK, CSS), (JS_MARK, JS)):
        if mark not in template:
            raise SystemExit(f"[ui] 模板缺标记 {mark!r}：{TEMPLATE}")
        if not path.is_file():
            raise SystemExit(f"[ui] 缺源文件：{path}")
        data = path.read_bytes()
        if CSS_MARK in data or JS_MARK in data:
            raise SystemExit(f"[ui] 源文件里出现了构建标记，拒绝构建：{path}")
        template = template.replace(mark, data)
    return template


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建 static/index.html（单文件自包含）")
    parser.add_argument("--check", action="store_true", help="只检查生成物是否与 web/ 源文件一致")
    args = parser.parse_args(argv)

    out = render()
    if args.check:
        if not OUT.is_file():
            print(f"[ui] 缺生成物：{OUT}", file=sys.stderr)
            return 1
        current = OUT.read_bytes()
        if current != out:
            limit = min(len(current), len(out))
            offset = next((i for i in range(limit) if current[i] != out[i]), limit)
            print(f"[ui] static/index.html 与 web/ 不一致"
                  f"（offset {offset}，现有 {len(current)} / 应为 {len(out)} 字节）", file=sys.stderr)
            print(r"[ui] 请改 web/ 后运行：py312\python.exe scripts\build_ui.py", file=sys.stderr)
            return 1
        print(f"[ui] OK {len(out)} 字节")
        return 0

    if OUT.is_file() and OUT.read_bytes() == out:
        print(f"[ui] 无变化 {len(out)} 字节")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(out)
    print(f"[ui] 已生成 {OUT.relative_to(ROOT)} {len(out)} 字节")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
