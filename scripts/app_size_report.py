"""app.py 体积报表（issue #10）：当前值 + 最近若干版本的增量。

门本身在 tests/test_app_budget.py；这里只负责把"它长得多快"打印成一张表。
一律按 LF 口径计字节：仓库存 LF、Windows 检出成 CRLF，直接 getsize 会比 Linux
上大近 1 万字节，同一条预算在两台机器上就不成立。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app.py"


def normalized_size(data: bytes) -> int:
    """按 LF 口径计字节（跨平台一致）。"""
    return len(data.replace(b"\r\n", b"\n"))


def over_budget(size: int, budget: int) -> int:
    """超出预算的字节数；没超返回 0。"""
    return max(0, size - budget)


def current_size(path: Path = APP) -> int:
    return normalized_size(path.read_bytes())


def ref_size(ref: str) -> int | None:
    proc = subprocess.run(["git", "show", f"{ref}:app.py"], cwd=ROOT,
                          capture_output=True, check=False)
    if proc.returncode != 0:
        return None
    return normalized_size(proc.stdout)


def _refs(limit: int) -> list[str]:
    proc = subprocess.run(["git", "log", f"-{limit}", "--format=%h", "--", "app.py"],
                          cwd=ROOT, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="app.py 体积与逐版增量")
    ap.add_argument("--refs", nargs="*", default=None,
                    help="对比的 ref 列表（默认取最近改动 app.py 的提交）")
    ap.add_argument("--limit", type=int, default=8, help="默认取样条数")
    args = ap.parse_args(argv)

    now = current_size()
    print(f"app.py 当前 {now:,} 字节（LF 口径）")
    refs = args.refs if args.refs is not None else _refs(args.limit)
    if not refs:
        return 0
    print(f"{'ref':<10}{'字节':>12}{'相对当前':>14}")
    for ref in refs:
        size = ref_size(ref)
        if size is None:
            print(f"{ref:<10}{'(读不到)':>12}")
            continue
        print(f"{ref:<10}{size:>12,}{now - size:>+14,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
