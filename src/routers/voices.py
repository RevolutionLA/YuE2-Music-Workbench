"""音色库路由：参考音频入库、删除、ASR 转写、降噪。

从 app.py 拆出；依赖由 build_router 注入，避免反向 import 造成循环。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

import audio_io


def build_router(*, stream_upload, root: Path, voices_mod: Any,
                 asr_mod: Any, denoise_mod: Any) -> APIRouter:
    router = APIRouter(tags=["voices"])

    @router.get("/voices")
    def list_voices():
        return {"voices": voices_mod.list_voices()}

    @router.post("/voices")
    async def save_voice(
        name: str = Form(...),
        reference_text: str = Form(""),
        audio: UploadFile = File(...),
    ):
        suffix = audio_io.normalize_suffix(audio.filename, ".wav")
        # 走与其它上传同一个流式闸门：大文件整读进内存会把网关顶到 OOM
        tmp = root / "tmp" / "voices" / (
            datetime.now().strftime("%H%M%S_") + os.urandom(2).hex() + suffix)
        wav_tmp = tmp
        try:
            await stream_upload(audio, tmp, 100 * 1024 * 1024, "音色参考音频")
            try:
                # 建档侧的解码器只保证 wav/flac/mp3/ogg；其余先转换，保留原采样率
                wav_tmp = await run_in_threadpool(lambda: audio_io.ensure_wav(tmp))
            except audio_io.AudioFormatError as exc:
                raise HTTPException(status_code=400, detail=f"音频无法解码：{exc}") from exc
            data = await run_in_threadpool(wav_tmp.read_bytes)
            suffix = wav_tmp.suffix
        finally:
            tmp.unlink(missing_ok=True)
            if wav_tmp != tmp:
                wav_tmp.unlink(missing_ok=True)
        # 建档要跑嵌入模型推理，必须丢线程池，不能冻住事件循环
        item = await run_in_threadpool(
            voices_mod.save_voice, name, reference_text, data, suffix)
        return {"ok": True, "voice": item}

    @router.delete("/voices/{voice_id}")
    def delete_voice(voice_id: str):
        voices_mod.delete_voice(voice_id)
        return {"ok": True}

    @router.post("/voices/transcribe")
    async def transcribe_voice(audio: UploadFile = File(...)):
        """上传参考音频 -> 自动识别歌词/文本（ASR），用于翻唱工作流。"""
        p = root / "tmp" / "asr"
        p.mkdir(parents=True, exist_ok=True)
        ext = audio_io.normalize_suffix(audio.filename, ".wav")
        f = p / (datetime.now().strftime("%H%M%S_") + os.urandom(2).hex() + ext)
        await stream_upload(audio, f, 200 * 1024 * 1024, "音频")
        wav_f = f
        try:
            try:
                wav_f = await run_in_threadpool(
                    lambda: audio_io.ensure_wav(f, sample_rate=16000, channels=1))
            except audio_io.AudioFormatError as exc:
                raise HTTPException(status_code=400, detail=f"音频无法解码：{exc}") from exc
            text = await run_in_threadpool(
                lambda: asr_mod.recognize_wav_bytes(wav_f.read_bytes(), wav_f.name))
        finally:
            f.unlink(missing_ok=True)
            if wav_f != f:
                wav_f.unlink(missing_ok=True)
        return {"ok": True, "text": text}

    @router.post("/voices/denoise")
    async def denoise_voice(audio: UploadFile = File(...)):
        """上传参考音频 -> 降噪后返回增强音频。"""
        p = root / "tmp" / "denoise"
        p.mkdir(parents=True, exist_ok=True)
        ext = audio_io.normalize_suffix(audio.filename, ".wav")
        f = p / (datetime.now().strftime("%H%M%S_") + os.urandom(2).hex() + ext)
        await stream_upload(audio, f, 200 * 1024 * 1024, "音频")
        wav_f = f
        try:
            try:
                wav_f = await run_in_threadpool(
                    lambda: audio_io.ensure_wav(f, sample_rate=16000, channels=1))
            except audio_io.AudioFormatError as exc:
                raise HTTPException(status_code=400, detail=f"音频无法解码：{exc}") from exc
            out = await run_in_threadpool(
                lambda: denoise_mod.denoise_wav_bytes(wav_f.read_bytes(), wav_f.name))
        finally:
            f.unlink(missing_ok=True)
            if wav_f != f:
                wav_f.unlink(missing_ok=True)
        return Response(
            content=out,
            media_type="audio/wav",
            headers={"Content-Disposition": 'attachment; filename="enh.wav"'},
        )

    return router
