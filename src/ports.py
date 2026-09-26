# -*- coding: utf-8 -*-
"""端口唯一真源。

历史上 7863 / 3081 / 8080 散落在 app.py、watchdog.py、dsh-plugin、bat 脚本、
static/index.html 等多处，改端口要改好几个地方且极易漏（漏一处就 502/白屏）。
现在统一读仓库根的 ports.json；文件缺失或写坏时回退内置默认值 —— 配置问题
绝不允许导致服务起不来。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORTS_FILE = ROOT / "ports.json"

DEFAULTS: dict[str, int] = {
    "gateway": 7863,    # FastAPI 网关（app.py）
    "dsh": 3081,        # dsh 工作台 UI（dsh-plugin）
    "audiocpp": 8080,   # YuE2 GGUF 推理引擎
}

# 环境变量临时覆盖（优先级最高）：不改动文件就能换端口，便于排障/并行实例
_ENV_KEYS: dict[str, str] = {
    "gateway": "YUE2_GATEWAY_PORT",
    "dsh": "YUE2_DSH_PORT",
    "audiocpp": "YUE2_AUDIOCPP_PORT",
}

_cache: dict[str, int] | None = None


def load(force: bool = False) -> dict[str, int]:
    """读取端口表（首次调用后缓存，force=True 强制重读）。"""
    global _cache
    if _cache is not None and not force:
        return _cache
    data = dict(DEFAULTS)
    try:
        raw = json.loads(PORTS_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            for k in DEFAULTS:
                v = raw.get(k)
                if isinstance(v, int) and 1 <= v <= 65535:
                    data[k] = v
    except Exception:
        pass  # 文件缺失/损坏 → 用默认值，不因配置阻断启动
    for k, name in _ENV_KEYS.items():
        v = (os.environ.get(name) or "").strip()
        if v.isdigit() and 1 <= int(v) <= 65535:
            data[k] = int(v)
    _cache = data
    return data


def get(name: str) -> int:
    """取单个端口，未知名字返回 -1（调用方自行兜底）。"""
    return int(load().get(name, -1))


def all_ports() -> dict[str, int]:
    return dict(load())
