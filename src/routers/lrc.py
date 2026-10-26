"""独立歌词对齐（LRC/eLRC）路由。

worker 与 `_lrc_sa_*` 存储留在 app.py：现有用例通过 patch `app.lrc_align_mod`
验证对齐失败回退，搬走 worker 会把那条测试的钩子弄断。这里只负责 HTTP 层，
目录/读写函数全部由 app 注入（standalone_dir 用 callable，测试可直接重定向）。
"""

from __future__ import annotations

import os
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import aiofiles
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

import audio_io


def build_router(*, standalone_dir: Callable[[], Path],
                 new_id: Callable[[], str], sa_dir: Callable[[str], Path],
                 sa_read: Callable[[str], dict | None],
                 sa_write: Callable[[str, dict], None],
                 start_worker: Callable[..., None],
                 audio_exts, max_bytes: int, max_lyrics: int,
                 limit_text: Callable[..., Any],
                 engine_ready: Callable[[], bool],
                 resolve_server_audio: Callable[[str], Path],
                 server_name: Callable[[str], str]) -> APIRouter:
    router = APIRouter(tags=["lrc"])

    def _artifact(rid: str, fmt: str) -> Path | None:
        name = "lyrics.elrc" if fmt == "elrc" else "lyrics.lrc"
        fp = sa_dir(rid) / name
        return fp if fp.is_file() else None

    @router.post("/lrc/start")
    async def lrc_start(file: UploadFile | None = File(None), lyrics: str = Form(""),
                        task_name: str = Form(""), rid: str = Form("")):
        text = str(lyrics or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="歌词不能为空")
        limit_text("歌词", text, max_lyrics)
        if not engine_ready():
            raise HTTPException(status_code=400, detail="本机缺少歌词对齐引擎")
        source_rid = os.path.basename(str(rid or "").strip())
        if file is None and not source_rid:
            raise HTTPException(status_code=400,
                                detail="请选择要上传的音频，或从服务器歌曲里选一首")
        suffix = ""
        if file is not None:
            suffix = Path(file.filename or "").suffix.lower()
            if suffix not in audio_exts:
                raise HTTPException(status_code=400,
                                    detail="音频格式只支持 " + "、".join(sorted(audio_exts)))
        job_id = new_id()
        d = sa_dir(job_id)
        d.mkdir(parents=True, exist_ok=True)
        if source_rid:
            try:
                server = resolve_server_audio(source_rid)
                src = d / "src.wav"
                await run_in_threadpool(shutil.copyfile, server, src)
                size = int((await run_in_threadpool(src.stat)).st_size)
            except HTTPException:
                shutil.rmtree(d, ignore_errors=True)
                raise
            file_label = server.name
            try:
                default_name = server_name(source_rid)
            except Exception:
                default_name = ""
            name_ = (str(task_name or "").strip() or default_name or source_rid)[:100]
        else:
            src = d / ("src" + suffix)
            size = 0
            try:
                async with aiofiles.open(src, "wb") as fh:
                    while True:
                        chunk = await file.read(1 << 20)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > max_bytes:
                            raise HTTPException(status_code=413, detail="音频超过 300MB 上限")
                        await fh.write(chunk)
            except HTTPException:
                shutil.rmtree(d, ignore_errors=True)
                raise
            except Exception as e:  # noqa: BLE001
                shutil.rmtree(d, ignore_errors=True)
                raise HTTPException(status_code=400, detail=f"音频保存失败：{e}") from e
            # soundfile/FunASR 读不了 m4a/aac/opus/webm/wma：先统一成 16k 单声道 WAV
            try:
                wav_src = await run_in_threadpool(
                    lambda: audio_io.ensure_wav(src, sample_rate=16000, channels=1))
            except audio_io.AudioFormatError as e:
                shutil.rmtree(d, ignore_errors=True)
                raise HTTPException(status_code=400, detail=str(e)[:300]) from e
            if wav_src != src:
                src.unlink(missing_ok=True)
                src = wav_src
            file_label = file.filename or src.name
            name_ = (str(task_name or "").strip() or Path(file.filename or "歌词").stem)[:100]
        sa_write(job_id, {"id": job_id, "status": "running", "step": "排队对齐",
                          "name": name_, "file": file_label,
                          "bytes": size, "ts": datetime.now().isoformat(timespec="seconds"),
                          "_started": time.time()})
        threading.Thread(target=start_worker, args=(job_id, src, text, name_),
                         daemon=True).start()
        return {"ok": True, "id": job_id, "status": "running"}

    @router.get("/lrc/list")
    def lrc_list(limit: int = 20):
        limit = max(1, min(int(limit or 20), 100))
        items = []
        root = standalone_dir()
        if root.is_dir():
            for p in root.iterdir():
                if p.is_dir():
                    st = sa_read(p.name)
                    if st:
                        st.pop("_started", None)
                        items.append(st)
        items.sort(key=lambda x: str(x.get("ts") or ""), reverse=True)
        return {"items": items[:limit]}

    @router.get("/lrc/status/{rid}")
    def lrc_status(rid: str):
        rid = os.path.basename(rid)
        st = sa_read(rid)
        if not st:
            raise HTTPException(status_code=404, detail="任务不存在")
        st = dict(st)
        st.pop("_started", None)
        fp = _artifact(rid, "lrc")
        if fp and fp.is_file():
            st["lrc"] = fp.read_text(encoding="utf-8", errors="replace")
        ep = _artifact(rid, "elrc")
        st["has_elrc"] = bool(ep)
        if ep and ep.is_file():
            st["elrc"] = ep.read_text(encoding="utf-8", errors="replace")
        return st

    @router.get("/lrc/download/{rid}")
    def lrc_download(rid: str, fmt: str = "lrc"):
        rid = os.path.basename(rid)
        fmt = "elrc" if (fmt or "").lower() == "elrc" else "lrc"
        fp = _artifact(rid, fmt)
        if not fp:
            raise HTTPException(status_code=404, detail=f"{fmt} 文件不存在")
        st = sa_read(rid) or {}
        base = re.sub(r'[\\/:*?"<>|]+', "_", str(st.get("name") or rid))[:80]
        return FileResponse(str(fp), media_type="text/plain; charset=utf-8",
                            filename=f"{base}.{fmt}")

    @router.delete("/lrc/{rid}")
    def lrc_delete(rid: str):
        rid = os.path.basename(rid)
        st = sa_read(rid)
        if not st:
            raise HTTPException(status_code=404, detail="任务不存在")
        if st.get("status") == "running":
            raise HTTPException(status_code=409, detail="任务进行中，不能删除")
        shutil.rmtree(sa_dir(rid), ignore_errors=True)
        return {"ok": True}

    return router
