"""内容工具路由：歌词结构自检、生成模板的持久化。

模板路径由调用方以 callable 注入（每次请求现取），这样运行时改
`app._TEMPLATES_FILE` 与测试重定向都能生效；实现函数同时供 app.py 的
兼容包装复用，不在两处复制存储逻辑。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException

import json_store

Limiter = Callable[[str, Any, int], Any]


def load_templates(templates_file: Path) -> list[dict]:
    if not templates_file.exists():
        return []
    try:
        data = json.loads(templates_file.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def save_template_impl(payload: dict, *, templates_file: Path, limit_text: Limiter,
                       caps: dict[str, int], voices_mod: Any) -> dict:
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    tpl = {
        "id": voices_mod._SAFE.sub("-", name) + "-" + str(int(time.time() * 1000)),
        "name": name[:60],
        "created_at": datetime.now().isoformat(),
    }
    for key in ("style", "lyrics", "cot", "abc", "seed", "cfg", "steps",
                "gender", "abc_temperature", "abc_top_p", "abc_top_k",
                "semantic_temperature", "semantic_top_p", "semantic_top_k",
                "asm", "taskName"):
        if key in payload and payload[key] is not None:
            val = payload[key]
            # 模板的三本文本用与生成入口相同的上限
            if isinstance(val, str):
                tpl[key] = limit_text(f"模板字段 {key}", val, caps.get(key, 4000))
            elif isinstance(val, dict):
                if len(json.dumps(val, ensure_ascii=False)) <= 8000:
                    tpl[key] = val
            else:
                tpl[key] = val
    items = load_templates(templates_file)
    items.insert(0, tpl)
    json_store.atomic_write_json(templates_file, items)
    return {"ok": True, "template": tpl}


def delete_template_impl(template_id: str, templates_file: Path) -> dict:
    items = load_templates(templates_file)
    kept = [t for t in items if t.get("id") != template_id]
    if len(kept) == len(items):
        raise HTTPException(status_code=404, detail="template not found")
    json_store.atomic_write_json(templates_file, kept)
    return {"ok": True}


def build_router(*, templates_file: Callable[[], Path], limit_text: Limiter,
                 max_style: int, max_lyrics: int, max_abc: int,
                 voices_mod: Any) -> APIRouter:
    router = APIRouter(tags=["content"])
    caps = {"style": max_style, "lyrics": max_lyrics, "abc": max_abc}

    @router.post("/lyrics/validate")
    def validate_lyrics_endpoint(payload: dict):
        from ai_tools import _validate_lyrics_impl
        lyrics = payload.get("lyrics")
        if not isinstance(lyrics, str):
            raise HTTPException(status_code=400, detail="lyrics (string) is required")
        # 只做长度保护，不截断：截断会让校验结果针对一份并不存在的歌词
        lyrics = limit_text("待校验歌词", lyrics, max_lyrics)
        return _validate_lyrics_impl(lyrics)

    @router.get("/templates")
    def list_templates():
        return {"templates": load_templates(templates_file())}

    @router.post("/templates")
    def save_template(payload: dict):
        return save_template_impl(
            payload, templates_file=templates_file(), limit_text=limit_text,
            caps=caps, voices_mod=voices_mod)

    @router.delete("/templates/{template_id}")
    def delete_template(template_id: str):
        return delete_template_impl(template_id, templates_file())

    return router
