"""生成历史 CRUD：列表 / 上传入库 / 音频回放 / 清空 / 删除。

`history_active` 留在 app.py：它聚合生成、换声、训练三类实时状态，不属于纯 CRUD。
历史目录与读写函数由 app 注入（目录是 callable），测试重定向 HIST_DIR/HIST_JSON 仍生效。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse


def build_router(*, hist_dir: Callable[[], Path],
                 hist_read: Callable[[], list[dict]],
                 hist_write: Callable[[list[dict]], None],
                 stream_upload: Callable[..., Any], new_id: Callable[[], str],
                 limit_text: Callable[..., Any], max_style: int, max_lyrics: int,
                 max_abc: int, hist_keep: int,
                 output_purge: Callable[[str], None]) -> APIRouter:
    router = APIRouter(tags=["history"])

    @router.get("/history")
    def history_list():
        return {"items": hist_read()}

    @router.post("/history")
    async def history_add(audio: UploadFile = File(...), meta: str = Form("{}")):
        root = hist_dir()
        root.mkdir(parents=True, exist_ok=True)
        try:
            m = json.loads(meta or "{}")
        except Exception:
            m = {}
        if not isinstance(m, dict):
            m = {}
        now = datetime.now()
        style_s = limit_text("曲风描述", m.get("style"), max_style)
        lyrics_s = limit_text("歌词", m.get("lyrics"), max_lyrics)
        abc_s = limit_text("乐谱", m.get("abc"), max_abc)
        rid = new_id()
        fn = rid + ".wav"
        size = await stream_upload(audio, root / fn, 200 * 1024 * 1024, "音频")
        item = {
            "id": rid, "ts": now.isoformat(timespec="seconds"), "file": fn,
            "bytes": size,
            "style": style_s, "lyrics": lyrics_s,
            "cot": m.get("cot", "full"), "abc": abc_s,
            "seed": m.get("seed"), "cfg": m.get("cfg_scale"),
            "steps": m.get("num_inference_steps"), "sec": m.get("sec"),
            "seed_actual": m.get("seed_actual"), "model": m.get("model_used", ""),
        }
        items = [item] + hist_read()
        for gone in items[hist_keep:]:
            name = Path(str(gone.get("file") or "")).name
            if not name:
                continue
            victim = (root / name).resolve()
            try:
                victim.relative_to(root.resolve())
            except ValueError:
                continue
            victim.unlink(missing_ok=True)
        items = items[:hist_keep]
        hist_write(items)
        return {"ok": True, "item": item}

    @router.get("/history/{rid}/audio")
    def history_audio(rid: str):
        rid = os.path.basename(rid)
        fn = next((i["file"] for i in hist_read() if i["id"] == rid), None)
        if not fn:
            raise HTTPException(status_code=404, detail="not found")
        fp = hist_dir() / fn
        if not fp.is_file():
            raise HTTPException(status_code=404, detail="audio file missing")
        return FileResponse(str(fp), media_type="audio/wav")

    @router.delete("/history/clear")
    def history_clear():
        root = hist_dir()
        for i in hist_read():
            fn = os.path.basename(str(i.get("file", "")).replace("\\", "/"))
            fp = (root / fn).resolve()
            if fn and fp.parent == root.resolve() and fp.is_file():
                fp.unlink(missing_ok=True)
        hist_write([])
        return {"ok": True}

    @router.delete("/history/{rid}")
    def history_delete(rid: str):
        rid = os.path.basename(rid)
        root = hist_dir()
        items = hist_read()
        keep = [i for i in items if i["id"] != rid]
        if len(keep) == len(items):
            raise HTTPException(status_code=404, detail="not found")
        for gone in items:
            if gone["id"] == rid:
                fn = os.path.basename(str(gone.get("file", "")).replace("\\", "/"))
                fp = (root / fn).resolve()
                if fn and fp.parent == root.resolve() and fp.is_file():
                    fp.unlink(missing_ok=True)
        output_purge(rid)
        hist_write(keep)
        return {"ok": True}

    return router
