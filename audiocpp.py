"""Thin async client for a running audiocpp_server."""

from __future__ import annotations

import base64
import io
import struct
from typing import Any

import httpx
from fastapi import HTTPException

from settings import settings


def resolve_model_id(model: str | None) -> str:
    name = (model or settings.audiocpp_model_id).strip()
    key = name.lower()
    aliases = {
        "yue2",
        "yue-2",
        "yue2-3b",
        "yue2-music",
        "yue2-music-gen",
        "music",
        "default",
        settings.audiocpp_model_id.lower(),
    }
    if key in aliases:
        return settings.audiocpp_model_id
    return name


def strip_b64(data: str) -> str:
    raw = data.strip()
    if "," in raw and raw.lower().startswith("data:"):
        raw = raw.split(",", 1)[1]
    return raw


def voice_ref_payload(b64: str) -> dict[str, str]:
    return {"type": "base64", "data": strip_b64(b64)}


def audio_payload(b64: str) -> dict[str, str]:
    return {"type": "base64", "data": strip_b64(b64)}


def silent_wav_b64(duration_ms: int = 200, sample_rate: int = 16000) -> str:
    n = max(1, int(sample_rate * duration_ms / 1000))
    pcm = b"\x00\x00" * n
    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + len(pcm)))
    buf.write(b"WAVEfmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", len(pcm)))
    buf.write(pcm)
    return base64.b64encode(buf.getvalue()).decode("ascii")


class AudioCppClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.audiocpp_base_url.rstrip("/"),
            timeout=httpx.Timeout(settings.audiocpp_timeout_sec, connect=10.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def health(self) -> dict[str, Any]:
        try:
            resp = await self._client.get("/health")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"audio.cpp unreachable: {exc}",
            ) from exc
        try:
            return resp.json()
        except ValueError:
            return {"ok": True, "raw": resp.text}

    async def models(self) -> Any:
        try:
            resp = await self._client.get("/v1/models")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"audio.cpp /v1/models failed: {exc}",
            ) from exc
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    async def speech(self, payload: dict[str, Any]) -> bytes:
        return await self._post_audio("/v1/audio/speech", payload)

    async def tasks_run(self, payload: dict[str, Any]) -> bytes:
        return await self._post_audio("/v1/tasks/run", payload)

    async def generate(self, payload: dict[str, Any]) -> bytes:
        """YuE2 / music generation via the generic task route."""
        return await self.tasks_run(payload)

    async def _post_audio(self, path: str, payload: dict[str, Any]) -> bytes:
        try:
            resp = await self._client.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"audio.cpp {path} request failed: {exc}",
            ) from exc

        ctype = (resp.headers.get("content-type") or "").lower()
        if resp.status_code >= 400:
            detail: Any
            if "application/json" in ctype:
                try:
                    detail = resp.json()
                except ValueError:
                    detail = resp.text
            else:
                detail = resp.text[:2000]
            raise HTTPException(status_code=resp.status_code, detail=detail)

        if "application/json" in ctype:
            try:
                body = resp.json()
            except ValueError as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"audio.cpp returned invalid JSON from {path}",
                ) from exc
            audio = _extract_audio_bytes(body)
            if audio is None:
                raise HTTPException(
                    status_code=502,
                    detail={"message": "no audio in audio.cpp JSON response", "body": body},
                )
            return audio

        return resp.content


def _extract_audio_bytes(body: Any) -> bytes | None:
    if not isinstance(body, dict):
        return None
    for key in ("audio", "wav", "data", "output"):
        value = body.get(key)
        if isinstance(value, str) and value:
            try:
                return base64.b64decode(strip_b64(value))
            except Exception:
                continue
        if isinstance(value, dict):
            data = value.get("data") or value.get("b64")
            if isinstance(data, str) and data:
                try:
                    return base64.b64decode(strip_b64(data))
                except Exception:
                    continue
    result = body.get("result")
    if isinstance(result, dict):
        return _extract_audio_bytes(result)
    return None
