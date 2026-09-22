"""Optional SenseVoice ASR for reference-audio transcripts."""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from pathlib import Path

from fastapi import HTTPException

from settings import settings

logger = logging.getLogger("fireredtts3_gateway")

_lock = threading.Lock()
_model = None
_ALLOWED_EXT = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".webm", ".aac", ".wma"}


def _load_model():
    global _model
    if _model is not None:
        return _model
    with _lock:
        if _model is not None:
            return _model
        model_dir = Path(settings.sensevoice_model_dir)
        if not model_dir.exists():
            raise HTTPException(
                status_code=503,
                detail=f"SenseVoice model not found: {model_dir}",
            )
        try:
            from funasr import AutoModel
        except ImportError as exc:
            raise HTTPException(
                status_code=503,
                detail="funasr is not installed",
            ) from exc
        logger.info("loading SenseVoice from %s device=%s", model_dir, settings.sensevoice_device)
        _model = AutoModel(
            model=str(model_dir),
            disable_update=True,
            log_level="ERROR",
            device=settings.sensevoice_device,
        )
        return _model


def recognize_wav_bytes(data: bytes, filename: str = "prompt.wav") -> str:
    if not data:
        raise HTTPException(status_code=400, detail="audio is empty")
    ext = Path(filename or "prompt.wav").suffix.lower()
    if ext not in _ALLOWED_EXT:
        ext = ".wav"
    model = _load_model()
    fd, path = tempfile.mkstemp(prefix="fr3_asr_", suffix=ext)
    os.close(fd)
    try:
        with open(path, "wb") as fh:
            fh.write(data)
        try:
            res = model.generate(
                input=path,
                language=settings.sensevoice_language,
                use_itn=True,
            )
        except Exception as exc:
            logger.exception("SenseVoice failed")
            raise HTTPException(status_code=500, detail=f"ASR failed: {exc}") from exc
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if not res:
        return ""
    text = res[0].get("text") or ""
    return text.split("|>")[-1].strip()
