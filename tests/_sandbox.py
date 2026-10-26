"""测试隔离：禁止引擎自启，并保护本机真实引擎配置。

`import app` 会连带加载网关内核；闭源 pyd 会无视环境变量直接拉起引擎。
另外有些用例虽然重定向了 app 常量，仍可能写到真实的 `cpp/` sidecar。
这里在测试进程最早阶段把环境钉死，并在退出时检查/还原几个会改变本机
运行状态的文件——测试可以红，但不能污染开发机。
"""

from __future__ import annotations

import atexit
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SAFE_ENV = {
    "YUE2_NO_ENGINE_AUTOSTART": "1",
    "YUE2_TESTING": "1",
    "YUE2_OPEN_BROWSER": "0",
}

_PROTECTED = (
    ROOT / "cpp" / "server.json",
    ROOT / "runtime" / "data" / "engine_state.json",
)
_PROTECTED_GLOBS = (
    "cpp/model/sidecars/*.json",
    "cpp/model/*/sidecars/*.json",
)

_snapshot: dict[Path, bytes] = {}
_installed = False


def _protected_files() -> list[Path]:
    files = [p for p in _PROTECTED if p.is_file()]
    for pattern in _PROTECTED_GLOBS:
        files.extend(sorted(ROOT.glob(pattern)))
    return files


def install() -> None:
    global _installed
    for key, value in SAFE_ENV.items():
        os.environ.setdefault(key, value)
    if _installed:
        return
    _installed = True
    _preload_real_kernel()
    for path in _protected_files():
        try:
            _snapshot[path] = path.read_bytes()
        except OSError:
            pass
    atexit.register(restore)


def _preload_real_kernel() -> None:
    """先绑定真 shim，别让某些用例注入的假 main 污染整轮测试。

    干净 clone（缺 cpp/model_specs）上 main.py 按设计拒绝加载，此时留给
    test_review_fixes 的替身路径；本机部署了引擎参数表则必须是真 shim。
    """
    if not (ROOT / "cpp" / "model_specs" / "yue2.json").is_file():
        return
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        import main  # noqa: F401
    except Exception as exc:  # 让用例自己红，别在这里吞掉
        print(f"[test-sandbox] 预加载 shim 失败：{type(exc).__name__}: {exc}", flush=True)


def restore() -> None:
    restored: list[Path] = []
    for path, data in _snapshot.items():
        try:
            if not path.is_file() or path.read_bytes() != data:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                restored.append(path)
        except OSError:
            pass
    if restored:
        names = ", ".join(str(p.relative_to(ROOT)) for p in restored)
        print(f"[test-sandbox] 已还原 {len(restored)} 个被测试改写的真实配置：{names}",
              flush=True)
