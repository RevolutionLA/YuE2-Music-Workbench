"""只读系统探测：版本号、端口监听、SheetSage2/AI 工作台就绪、网关内核来源。

全部是不改状态的探测，供 /api/system/info 与启动自检复用。
"""

from __future__ import annotations

import re
import socket
from pathlib import Path
from typing import Any


def version_from_changelog(root: Path) -> str:
    """版本号取 CHANGELOG 顶部条目（真源是 git 附注标签，但网关不为版本号起子进程）。"""
    try:
        with (root / "CHANGELOG.md").open(encoding="utf-8", errors="replace") as fh:
            head = fh.read(8192)
        m = re.search(r"^## \[(v[^\]]+)\]", head, re.M)
        if m:
            return m.group(1)
    except Exception as e:
        print(f"[system/info] CHANGELOG 版本号读取失败：{e}", flush=True)
    return "unknown"


def tcp_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """端口是否有人监听（只做 connect 探测，不发消息、不拉起任何进程）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def sheetsage_ready() -> bool:
    """SheetSage2 权重是否就绪（与 /api/score/status 同一判据）。"""
    try:
        import sheetsage_pt
        return bool(sheetsage_pt.mert_cached() or sheetsage_pt.resolve_checkpoint().is_dir())
    except FileNotFoundError:
        return False          # 未下载权重是新装环境常态，不是错误
    except Exception as e:
        print(f"[system/info] SheetSage2 就绪探测异常：{e}", flush=True)
        return False


def ai_ready(dsh_port: int) -> dict:
    """AI 工作台就绪：只报布尔与模型名，密钥本身绝不出现在返回值里。"""
    out: dict[str, Any] = {"ready": False, "key_configured": False,
                           "runner_exists": False, "model": "",
                           "dsh_listening": False}
    try:
        import ai_lab
        import ai_router
        out["key_configured"] = bool(ai_lab.ai_key())
        out["ready"] = bool(ai_lab.ai_ready())
        out["model"] = ai_lab.MODEL
        out["runner_exists"] = ai_router.DSH_RUNNER.is_file()
    except Exception as e:
        out["error"] = f"AI 工作台状态探测失败：{type(e).__name__}: {str(e)[:120]}"
        print(out["error"], flush=True)
    out["dsh_listening"] = tcp_open("127.0.0.1", dsh_port)
    return out


def gateway_kernel(main_mod: Any) -> dict:
    """网关内核来自哪：编译的 `.pyd` 还是仓库里的 `main.py`（开源 shim）。

    CPython 扩展模块优先于同名 `.py`，而 main.cp312-win_amd64.pyd 在老安装机器上
    普遍存在 —— 同一版本两台机器可能走不同内核，界面与日志必须能查出来。
    """
    f = Path(str(getattr(main_mod, "__file__", "") or ""))
    kind = "pyd" if f.suffix.lower() == ".pyd" else "shim"
    note = ("编译版内核（*.pyd）：不启用开源网关的字段白名单/丢弃键日志，"
            "要换成开源网关请把同目录的 main.cp312-win_amd64.pyd 移走"
            if kind == "pyd" else
            "开源网关 shim（main.py）：按 spec 白名单筛字段，被丢弃的键写日志")
    return {"kind": kind, "path": str(f), "note": note}
