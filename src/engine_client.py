"""audio.cpp 的唯一 HTTP 入口（同步版）。

网关里所有对引擎的读请求/直连任务都应经过这里，避免每个功能各写一套
timeout / trust_env / base_url。异步生成走 main.py 的 generate_response。
"""

from __future__ import annotations

from typing import Any

import httpx

from settings import settings


def base_url() -> str:
    return settings.audiocpp_base_url.rstrip("/")


def _timeout(seconds: float | None = None) -> httpx.Timeout:
    total = settings.audiocpp_timeout_sec if seconds is None else float(seconds)
    return httpx.Timeout(total, connect=10.0)


def _client(seconds: float | None = None) -> httpx.Client:
    return httpx.Client(base_url=base_url(), timeout=_timeout(seconds), trust_env=False)


def status_code(path: str, seconds: float = 1.0) -> int | None:
    """只取状态码；连不上返回 None（调用方按“掉线”处理）。"""
    try:
        with _client(seconds) as c:
            return int(c.get(path).status_code)
    except Exception:
        return None


def get_json(path: str, seconds: float = 5.0) -> Any | None:
    """GET 并解析 JSON；非 2xx 或解析失败返回 None。"""
    try:
        with _client(seconds) as c:
            r = c.get(path)
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


def post_json(path: str, payload: dict, seconds: float | None = None) -> httpx.Response:
    """POST JSON，原样返回响应（状态码/错误体交给调用方判断）。"""
    with _client(seconds) as c:
        return c.post(path, json=payload)


def run_task(payload: dict, seconds: float | None = None) -> httpx.Response:
    return post_json("/v1/tasks/run", payload, seconds)


def unload_all(seconds: float = 30.0) -> list[str]:
    """卸载引擎里所有常驻模型；失败返回空列表（只是尽力而为）。"""
    try:
        r = post_json("/v1/tasks/unload_all_models", {}, seconds)
        if r.status_code < 400:
            return list((r.json() or {}).get("unloaded") or [])
    except Exception:
        pass
    return []
