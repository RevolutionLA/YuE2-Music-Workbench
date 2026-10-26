"""乐谱域：转谱任务、乐谱库 CRUD、导出。

实现从 app.py 拆出；scores_dir 由调用方以 callable 注入，保证测试/运行时改
`app.SCORES_DIR` 仍然生效。SCORE_JOBS/两把锁是模块级单例，app 里保留同名别名
指向同一对象，旧用例对 `app._SCORE_JOBS` 的 clear/patch 不会失效。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

import audio_io
import json_store

SCORE_JOBS: dict[str, dict] = {}
SCORE_LOCK = threading.Lock()
# SheetSage2 转谱串行闸门：上游把中间产物与 score.abc 写死在共享目录
SCORE_ENGINE_LOCK = threading.Lock()

_ID_RE = re.compile(r"\d{8}_\d{6}_[0-9a-f]{8}|[A-Za-z0-9_\-]{1,64}")
SCORE_EXPORT_FORMATS = ("mid", "abc")
SCORE_MIDI_PREFERRED = ("transcription.mid", "melody.mid", "chords.mid",
                        "melody_vocal.mid", "melody_instrumental.mid")


def score_archive_dir(score_id: str, scores_dir: Path) -> Path:
    sid = os.path.basename(str(score_id or "").strip().replace("\\", "/"))
    if not _ID_RE.fullmatch(sid):
        raise HTTPException(status_code=400, detail=f"非法乐谱 ID：{score_id!r}")
    return scores_dir / sid


def score_save(job_id: str, abc: str, artifacts: list[str] | None = None,
               title: str | None = None, *, scores_dir: Path,
               analyze_abc: Callable[[str], dict]) -> dict:
    scores_dir.mkdir(parents=True, exist_ok=True)
    rec = {"id": job_id, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "abc": abc, "analysis": analyze_abc(abc),
           "artifacts": sorted(artifacts or [])}
    if title:
        rec["title"] = title.strip()[:80]
    json_store.atomic_write_json(scores_dir / f"{job_id}.json", rec, indent=None)
    return rec


def score_record(score_id: str, scores_dir: Path) -> dict:
    score_id = os.path.basename(str(score_id or "").replace("\\", "/"))
    p = (scores_dir / f"{score_id}.json").resolve()
    if p.parent != scores_dir.resolve() or not p.is_file():
        raise HTTPException(status_code=404, detail="乐谱不存在")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"乐谱记录读不出来：{e}")


def score_run(job_id: str, src: Path, melody_only: bool, *,
              scores_dir: Callable[[], Path], jobs: dict, lock,
              engine_lock, analyze_abc: Callable[[str], dict],
              toast: Callable[..., None]) -> None:
    started = time.time()
    try:
        import sheetsage_pt
        with engine_lock:
            archive = score_archive_dir(job_id, scores_dir())
            abc = sheetsage_pt.transcribe_abc(str(src), melody_only=melody_only,
                                              archive_dir=str(archive))
            archived = sorted(p.name for p in archive.iterdir() if p.is_file()) \
                if archive.is_dir() else []
        rec = score_save(job_id, abc, archived, scores_dir=scores_dir(),
                         analyze_abc=analyze_abc)
        with lock:
            jobs[job_id].update({"done": True, "abc": abc,
                                 "analysis": rec.get("analysis"), "score_id": rec["id"],
                                 "artifacts": archived})
        toast(f"{src.stem} 乐谱提取成功",
              f"耗时 {int(time.time()-started)//60} 分 {int(time.time()-started)%60} 秒，已入库可回填")
    except Exception as e:  # noqa: BLE001
        with lock:
            jobs[job_id].update({"done": True, "error": str(e)[:300]})
        toast(f"{src.stem} 乐谱提取失败", str(e)[:120])
    finally:
        try:
            src.unlink(missing_ok=True)
        except Exception:
            pass
        with lock:
            if len(jobs) > 20:
                for k in sorted(jobs.keys())[:-20]:
                    jobs.pop(k, None)


def build_router(*, root: Path, scores_dir: Callable[[], Path],
                 new_id: Callable[[], str], stream_upload: Callable[..., Any],
                 toast: Callable[..., None],
                 analyze_abc: Callable[[str], dict],
                 resolve_server_audio: Callable[[str], Path]) -> APIRouter:
    router = APIRouter(tags=["score"])

    def _run(job_id: str, src: Path, melody_only: bool) -> None:
        score_run(job_id, src, melody_only, scores_dir=scores_dir, jobs=SCORE_JOBS,
                  lock=SCORE_LOCK, engine_lock=SCORE_ENGINE_LOCK,
                  analyze_abc=analyze_abc, toast=toast)

    @router.get("/score/status")
    def score_status():
        import sheetsage_pt
        try:
            ready = bool(sheetsage_pt.mert_cached() or sheetsage_pt.resolve_checkpoint().is_dir())
        except FileNotFoundError:
            ready = False
        return {"ready": ready}

    @router.post("/score")
    async def score_submit(audio: UploadFile | None = File(None),
                           melody_only: str = Form("true"), rid: str = Form("")):
        source_rid = os.path.basename(str(rid or "").strip())
        if audio is None and not source_rid:
            raise HTTPException(status_code=400,
                                detail="请上传参考音频，或从服务器歌曲里选一首")
        tmp_dir = root / "tmp" / "score"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        job_id = new_id()
        if source_rid:
            try:
                server = resolve_server_audio(source_rid)
                ext = server.suffix.lower() or ".wav"
                src = tmp_dir / f"score_{job_id}{ext}"
                await run_in_threadpool(shutil.copyfile, server, src)
            except HTTPException:
                raise
        else:
            ext = audio_io.normalize_suffix(audio.filename, ".wav")
            src = tmp_dir / f"score_{job_id}{ext}"
            await stream_upload(audio, src, 200 * 1024 * 1024, "音频")
        with SCORE_LOCK:
            SCORE_JOBS[job_id] = {"done": False, "ts": time.time()}
        threading.Thread(target=_run, args=(job_id, src, melody_only != "false"),
                         daemon=True).start()
        return {"job_id": job_id}

    @router.get("/score/result")
    def score_result(job_id: str):
        job_id = os.path.basename(job_id.replace("\\", "/"))
        with SCORE_LOCK:
            job = SCORE_JOBS.get(job_id)
        if not job:
            p = scores_dir() / f"{job_id}.json"
            if p.is_file():
                try:
                    rec = json.loads(p.read_text(encoding="utf-8"))
                    return {"done": True, "abc": rec.get("abc"), "error": None,
                            "analysis": rec.get("analysis"),
                            "score_id": rec.get("id") or job_id}
                except Exception:
                    pass
            raise HTTPException(status_code=404, detail="任务不存在或已过期")
        return {"done": job["done"], "abc": job.get("abc"), "error": job.get("error"),
                "analysis": job.get("analysis"), "score_id": job.get("score_id")}

    @router.get("/scores")
    def scores_list():
        sd = scores_dir()
        if not sd.is_dir():
            return []
        out = []
        for p in sd.glob("*.json"):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
                an = rec.get("analysis") or {}
                arts = rec.get("artifacts") or []
                out.append({"id": rec.get("id"), "time": rec.get("time"),
                            "title": rec.get("title") or "",
                            "cot": an.get("cot_suggested"),
                            "abc_preview": (rec.get("abc") or "")[:120],
                            "artifacts": arts, "has_midi": any(
                                str(a).lower().endswith(".mid") for a in arts)})
            except Exception:
                continue
        out.sort(key=lambda r: r.get("id") or "", reverse=True)
        return out

    @router.get("/scores/{score_id}")
    def scores_get(score_id: str):
        score_id = os.path.basename(score_id.replace("\\", "/"))
        sd = scores_dir()
        p = (sd / f"{score_id}.json").resolve()
        if p.parent != sd.resolve() or not p.is_file():
            raise HTTPException(status_code=404, detail="乐谱不存在")
        return json.loads(p.read_text(encoding="utf-8"))

    @router.get("/scores/{score_id}/export")
    def scores_export(score_id: str, format: str = "mid"):
        if format not in SCORE_EXPORT_FORMATS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的导出格式：{format}（本期只做 {', '.join(SCORE_EXPORT_FORMATS)}；"
                       "MusicXML/PDF 谱面不在 v2.0 范围）")
        sd = scores_dir()
        rec = score_record(score_id, sd)
        sid = os.path.basename(str(rec.get("id") or ""))
        if format == "abc":
            archived = score_archive_dir(sid, sd) / "score.abc"
            if archived.is_file():
                return FileResponse(str(archived), media_type="text/plain; charset=utf-8",
                                    filename=f"{sid}.abc")
            abc = str(rec.get("abc") or "")
            if not abc.strip():
                raise HTTPException(status_code=404, detail="该乐谱没有 ABC 内容，无法导出")
            return Response(content=abc.encode("utf-8"),
                            media_type="text/plain; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{sid}.abc"'})
        arts = {str(a) for a in (rec.get("artifacts") or [])}
        archive = score_archive_dir(sid, sd)
        if not archive.is_dir() or not arts:
            raise HTTPException(
                status_code=404,
                detail="该乐谱没有归档 MIDI（转谱时未归档，或它早于归档功能上线）——"
                       "请对该歌曲重新转谱一次，新的转谱会把 MIDI 一起存进 data/scores/<id>/")
        for name in SCORE_MIDI_PREFERRED:
            if name not in arts:
                continue
            f = (archive / name).resolve()
            if f.parent != archive.resolve():
                raise HTTPException(status_code=400, detail="非法归档路径")
            if f.is_file() and f.stat().st_size > 0:
                return FileResponse(str(f), media_type="audio/midi", filename=f"{sid}_{name}")
        got = sorted(n for n in arts if str(n).lower().endswith(".mid"))
        raise HTTPException(
            status_code=404,
            detail=("归档里没有可用 MIDI（存在但为空的文件不算）；已归档的 MIDI："
                    + (", ".join(got) if got else "无") + "——重新转谱一次可补齐"))

    @router.delete("/scores/{score_id}")
    def scores_delete(score_id: str):
        score_id = os.path.basename(score_id.replace("\\", "/"))
        sd = scores_dir()
        p = (sd / f"{score_id}.json").resolve()
        removed: list[str] = []
        if p.parent == sd.resolve() and p.is_file():
            p.unlink()
            removed.append(f"{score_id}.json")
        if _ID_RE.fullmatch(score_id):
            d = score_archive_dir(score_id, sd)
            try:
                if d.is_dir() and d.resolve().parent == sd.resolve():
                    shutil.rmtree(str(d))
                    removed.append(d.name + "/")
            except OSError as e:
                print(f"[scores] {score_id} 归档目录清理失败（记录已删，目录留着）：{e}",
                      flush=True)
        return {"ok": True, "removed": removed}

    return router
